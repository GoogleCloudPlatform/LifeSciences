# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""KFP v2 4-stage pipeline orchestrating AlphaFold 3 Vertex AI Endpoint predictions.

Architecture (4 observable KFP stages):
1. `1. Provision & Queue AF3 Endpoint` (`provision_and_queue_af3_endpoint_task`):
   - Ensures the AlphaFold 3 Vertex AI Endpoint is deployed and READY (auto-initiating
     deployment if the endpoint was previously idle-drained to 0 replicas).
   - Coordinates FIFO access across active H100 replica(s) (`_slot_0 .. _slot_{R-1}`)
     using GCS conditional-generation locks (`if_generation_match=0`) and queue tickets.
2. `2. Run AF3 Inference (H100 Endpoint)` (`run_af3_inference_task`):
   - Executes synchronous `endpoint.predict()` against the warm H100 replica while
     heartbeating the replica lease every 45s.
   - Handles long-running Vertex AI HTTP front-end disconnects (~480s-600s on >1,000 aa
     targets) by polling `output_dir` in GCS until `_model.cif` and confidence JSONs land.
   - Immediately releases the GCS replica slot in `finally:` the instant inference completes
     so the next queued job can start H100 inference with zero inter-step delay.
3. `3. Report AF3 Endpoint Available` (`report_af3_endpoint_available_task`):
   - Verifies replica slot release, records endpoint availability and queue depth in
     `_activity.json`, and schedules the 20-minute `alphafold3-idle-drain-*` watchdog
     once the queue drains.
4. `4. Process AF3 Results & Expert Analysis` (`process_af3_results_task`):
   - Runs on CPU completely off the H100 GPU critical path.
   - Harvests AF3 container logs from Cloud Logging into `execution.log`, renders
     `plddt_plot_0.png` and `pae_plot_0.png`, invokes `gemini-3.1-pro-preview` multimodal
     Expert Analysis, and writes `analysis/summary.json` for FoldRun Viewer.
"""

from typing import NamedTuple

from kfp import dsl


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform>=1.50.0",
        "google-cloud-storage>=2.10.0",
        "requests-toolbelt>=1.0.0",
        "pyyaml>=6.0",
    ],
)
def provision_and_queue_af3_endpoint_task(
    project_id: str,
    region: str,
    endpoint_location: str,
    endpoint_id: str,
    bucket_name: str,
    job_name: str,
    pipeline_job_id: str,
    job_prefix: str,
    timeout_seconds: int = 1800,
) -> NamedTuple(
    "AF3QueueOutputs",
    [
        ("slot_blob_path", str),
        ("slot_index", int),
        ("active_replicas", int),
        ("queue_wait_seconds", float),
    ],
):
    """Stage 1: Ensure the AF3 Endpoint is provisioned/READY and acquire a FIFO replica slot."""
    import json
    import logging
    import time
    from collections import namedtuple

    import google.auth
    from google.api_core.exceptions import PreconditionFailed
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import aiplatform, storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("af3_stage1_queue")

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    _ = job_prefix

    aiplatform.init(project=project_id, location=endpoint_location)
    endpoint = aiplatform.Endpoint(
        endpoint_name=endpoint_id,
        project=project_id,
        location=endpoint_location,
    )
    endpoint_short_id = endpoint.resource_name.split("/")[-1]
    creds, _ = google.auth.default()
    authed = AuthorizedSession(creds)

    def _get_deployed_models(ep) -> list:
        try:
            return list(ep.list_models())
        except Exception:
            gca = getattr(ep, "gca_resource", None) or getattr(ep, "_gca_resource", None)
            return list(getattr(gca, "deployed_models", None) or [])

    def _has_active_deploy_op() -> bool:
        try:
            op_url = (
                f"https://{endpoint_location}-aiplatform.googleapis.com/v1/"
                f"{endpoint.resource_name}/operations"
            )
            r = authed.get(op_url, timeout=10)
            if not r.ok:
                return False
            for op in r.json().get("operations", []):
                if not op.get("done") and "DeployModel" in op.get("metadata", {}).get("@type", ""):
                    return True
        except Exception:
            pass
        return False

    def _get_active_replica_count(ep) -> int:
        models = _get_deployed_models(ep)
        total_replicas = 0
        for m in models:
            dr = getattr(m, "dedicated_resources", None)
            min_rep = int(getattr(dr, "min_replica_count", 0) or 0) if dr else 1
            total_replicas += max(1, min_rep)
        return max(1, total_replicas)

    wait_start = time.time()
    if not _get_deployed_models(endpoint):
        if not _has_active_deploy_op():
            log.info(
                f"[AF3 Stage 1] Endpoint '{endpoint.resource_name}' has 0 deployed models and no "
                "active DeployModel operation. Auto-discovering AlphaFold 3 model in Model Registry..."
            )
            try:
                models = aiplatform.Model.list(project=project_id, location=endpoint_location)
                af3_models = [
                    m
                    for m in models
                    if "alphafold" in (getattr(m, "display_name", "") or "").lower()
                    or "af3" in (getattr(m, "display_name", "") or "").lower()
                ]
                if af3_models:
                    allowed_tagless_uri = (
                        "us-docker.pkg.dev/vertex-ai-restricted/alphafold3/alphafold3-inference"
                    )

                    def _image_uri_rank(m) -> int:
                        spec = getattr(m, "container_spec", None) or getattr(
                            getattr(m, "_gca_resource", None), "container_spec", None
                        )
                        uri = str(getattr(spec, "image_uri", "") or "").strip()
                        if uri == allowed_tagless_uri:
                            return 0
                        if uri and ":" not in uri.split("/")[-1]:
                            return 1
                        return 2

                    af3_models.sort(key=_image_uri_rank)
                    target_model = af3_models[0]
                    log.info(
                        f"[AF3 Stage 1] Auto-initiating deployment of model '{target_model.resource_name}' "
                        f"to '{endpoint.resource_name}' (a3-highgpu-1g + NVIDIA_H100_80GB)..."
                    )
                    endpoint.deploy(
                        model=target_model,
                        machine_type="a3-highgpu-1g",
                        accelerator_type="NVIDIA_H100_80GB",
                        accelerator_count=1,
                        min_replica_count=1,
                        max_replica_count=1,
                        traffic_percentage=100,
                        sync=False,
                    )
            except Exception as auto_dep_exc:
                log.warning(f"[AF3 Stage 1] Auto-deploy attempt warning: {auto_dep_exc}")

        log.info(
            f"[AF3 Stage 1] Waiting up to 35 minutes for endpoint '{endpoint.resource_name}' "
            "to finish provisioning and reach READY..."
        )
        deploy_wait_deadline = time.time() + 2100.0
        while time.time() < deploy_wait_deadline:
            endpoint = aiplatform.Endpoint(
                endpoint_name=endpoint_id,
                project=project_id,
                location=endpoint_location,
            )
            if _get_deployed_models(endpoint):
                log.info(
                    f"[AF3 Stage 1] Endpoint '{endpoint.resource_name}' is READY with deployed model(s)."
                )
                break
            time.sleep(20.0)
        else:
            raise RuntimeError(
                f"Timed out waiting for AF3 endpoint '{endpoint.resource_name}' to reach READY."
            )

    active_replicas = _get_active_replica_count(endpoint)
    stale_heartbeat_sec = 210.0
    max_lease_sec = float(max(timeout_seconds + 300, 2400))

    # Register FIFO queue ticket so concurrent jobs acquire replica slots in submission order
    queue_ticket_path = f"af3_predictions/.locks/{endpoint_short_id}_queue_{pipeline_job_id}.json"
    queue_ticket_blob = bucket.blob(queue_ticket_path)

    def _refresh_queue_ticket():
        now_t = time.time()
        try:
            queue_ticket_blob.upload_from_string(
                json.dumps(
                    {
                        "job_name": job_name,
                        "pipeline_job_id": pipeline_job_id,
                        "queued_at": wait_start,
                        "heartbeat_at": now_t,
                        "heartbeat_epoch": now_t,
                    },
                    indent=2,
                ),
                content_type="application/json",
            )
        except Exception:
            pass

    def _inspect_slots_and_reap(num_slots: int) -> tuple[list[int], list[str]]:
        now_t = time.time()
        free_indices = []
        holders = []
        for slot_idx in range(num_slots):
            slot_path = f"af3_predictions/.locks/{endpoint_short_id}_slot_{slot_idx}.json"
            blob = bucket.blob(slot_path)
            if not blob.exists():
                free_indices.append(slot_idx)
                continue
            try:
                blob.reload()
                existing = json.loads(blob.download_as_text())
                hb = float(
                    existing.get("heartbeat_at")
                    or existing.get("heartbeat_epoch")
                    or existing.get("acquired_at")
                    or 0
                )
                acq = float(existing.get("acquired_at") or 0)
                holder = existing.get("job_name", "unknown")
                if (now_t - hb) > stale_heartbeat_sec or (now_t - acq) > max_lease_sec:
                    log.warning(
                        f"[AF3 Stage 1] Reaping stale lock on {slot_path} held by '{holder}' "
                        f"(last heartbeat {int(now_t - hb)}s ago)."
                    )
                    blob.delete(if_generation_match=blob.generation)
                    free_indices.append(slot_idx)
                else:
                    holders.append(f"slot-{slot_idx}:{holder}")
            except Exception:
                free_indices.append(slot_idx)
        return free_indices, holders

    def _get_fifo_queue_rank() -> int:
        now_t = time.time()
        tickets = []
        try:
            for b in bucket.list_blobs(prefix=f"af3_predictions/.locks/{endpoint_short_id}_queue_"):
                try:
                    td = json.loads(b.download_as_text())
                    hb = float(td.get("heartbeat_at") or td.get("heartbeat_epoch") or 0)
                    if (now_t - hb) <= 120.0:
                        tickets.append(
                            (
                                float(td.get("queued_at") or now_t),
                                str(td.get("pipeline_job_id") or b.name),
                            )
                        )
                    else:
                        b.delete()
                except Exception:
                    pass
        except Exception:
            return 0
        tickets.sort(key=lambda x: (x[0], x[1]))
        for idx, (_q_at, pjid) in enumerate(tickets):
            if pjid == pipeline_job_id:
                return idx
        return 0

    def _try_acquire_slot(free_indices: list[int]):
        now_t = time.time()
        for slot_idx in free_indices:
            slot_path = f"af3_predictions/.locks/{endpoint_short_id}_slot_{slot_idx}.json"
            blob = bucket.blob(slot_path)
            payload = {
                "job_name": job_name,
                "pipeline_job_id": pipeline_job_id,
                "slot_index": slot_idx,
                "stage": "provisioned_ready_for_inference",
                "acquired_at": now_t,
                "heartbeat_at": now_t,
                "heartbeat_epoch": now_t,
                "max_lease_sec": max_lease_sec,
            }
            try:
                blob.upload_from_string(
                    json.dumps(payload, indent=2),
                    content_type="application/json",
                    if_generation_match=0,
                )
                return slot_path, slot_idx
            except PreconditionFailed:
                continue
            except Exception as exc:
                log.warning(f"[AF3 Stage 1] Unexpected error acquiring {slot_path}: {exc}")
        return None, None

    log.info(
        f"[AF3 Stage 1] Enqueuing '{job_name}' ({pipeline_job_id}) for endpoint "
        f"'{endpoint.resource_name}' ({active_replicas} active H100 replica(s))..."
    )
    last_wait_log = 0.0
    try:
        while True:
            _refresh_queue_ticket()
            active_replicas = _get_active_replica_count(endpoint)
            free_indices, current_holders = _inspect_slots_and_reap(active_replicas)
            fifo_rank = _get_fifo_queue_rank()

            if free_indices and fifo_rank < len(free_indices):
                slot_path, slot_idx = _try_acquire_slot(free_indices)
                if slot_path is not None and slot_idx is not None:
                    queue_wait_sec = round(time.time() - wait_start, 2)
                    log.info(
                        f"[AF3 Stage 1] Acquired replica slot {slot_idx}/{active_replicas} "
                        f"('{slot_path}') after {queue_wait_sec:.1f}s wait (FIFO rank {fifo_rank})."
                    )
                    outputs = namedtuple(
                        "AF3QueueOutputs",
                        [
                            "slot_blob_path",
                            "slot_index",
                            "active_replicas",
                            "queue_wait_seconds",
                        ],
                    )
                    return outputs(
                        slot_path,
                        int(slot_idx),
                        int(active_replicas),
                        float(queue_wait_sec),
                    )

            if time.time() - last_wait_log >= 25.0:
                log.info(
                    f"[AF3 Stage 1] Waiting in FIFO queue (position #{fifo_rank + 1}) for "
                    f"available H100 replica slot ({active_replicas} active replica(s), "
                    f"currently held by {current_holders})..."
                )
                last_wait_log = time.time()
            time.sleep(5.0)
    finally:
        try:
            if queue_ticket_blob.exists():
                queue_ticket_blob.delete()
        except Exception:
            pass


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform>=1.50.0",
        "google-cloud-storage>=2.10.0",
        "requests-toolbelt>=1.0.0",
        "pyyaml>=6.0",
    ],
)
def run_af3_inference_task(
    project_id: str,
    region: str,
    endpoint_location: str,
    endpoint_id: str,
    bucket_name: str,
    job_name: str,
    pipeline_job_id: str,
    query_json_path: str,
    job_prefix: str,
    slot_blob_path: str,
    slot_index: int,
    active_replicas: int,
    queue_wait_seconds: float,
    msa_free: bool = False,
    num_diffusion_samples: int = 5,
    timeout_seconds: int = 1800,
    idle_shutdown_minutes: int = 20,
) -> NamedTuple(
    "AF3InferenceOutputs",
    [
        ("raw_output_gcs_uri", str),
        ("start_iso", str),
        ("end_iso", str),
        ("inference_seconds", float),
        ("slot_released", bool),
    ],
):
    """Stage 2: Execute AlphaFold 3 inference on the H100 Endpoint and immediately release the replica slot."""
    import json
    import logging
    import threading
    import time
    from collections import namedtuple
    from datetime import datetime, timezone

    from google.cloud import aiplatform, storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("af3_stage2_inference")

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    if not query_json_path.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI for query_json_path, got: {query_json_path}")
    q_bucket_name, q_blob_path = query_json_path[5:].split("/", 1)
    instance_payload = json.loads(
        storage_client.bucket(q_bucket_name).blob(q_blob_path).download_as_text()
    )

    aiplatform.init(project=project_id, location=endpoint_location)
    endpoint = aiplatform.Endpoint(
        endpoint_name=endpoint_id,
        project=project_id,
        location=endpoint_location,
    )
    endpoint_short_id = endpoint.resource_name.split("/")[-1]

    job_prefix_clean = job_prefix.strip("/")
    raw_output_prefix = f"{job_prefix_clean}/af3_raw"
    raw_output_gcs_uri = f"gs://{bucket_name}/{raw_output_prefix}"

    def _find_gcs_raw_outputs():
        cif_b = None
        conf_b = None
        sum_b = None
        for b in bucket.list_blobs(prefix=f"{raw_output_prefix}/"):
            bname = b.name
            if "/seed-" in bname:
                continue
            if bname.endswith("_model.cif"):
                cif_b = b
            elif bname.endswith("_summary_confidences.json"):
                sum_b = b
            elif bname.endswith("_confidences.json"):
                conf_b = b
        return cif_b, conf_b, sum_b

    acquired_slot_blob = bucket.blob(slot_blob_path) if slot_blob_path else None
    heartbeat_interval_sec = 45.0
    stop_heartbeat = threading.Event()
    slot_released = False

    # Immediately refresh lease heartbeat upon entering Stage 2
    if acquired_slot_blob is not None:
        try:
            now_t = time.time()
            lock_data = (
                json.loads(acquired_slot_blob.download_as_text())
                if acquired_slot_blob.exists()
                else {
                    "job_name": job_name,
                    "pipeline_job_id": pipeline_job_id,
                    "slot_index": slot_index,
                    "acquired_at": now_t,
                }
            )
            lock_data["job_name"] = job_name
            lock_data["pipeline_job_id"] = pipeline_job_id
            lock_data["stage"] = "running_inference"
            lock_data["heartbeat_at"] = now_t
            lock_data["heartbeat_epoch"] = now_t
            acquired_slot_blob.upload_from_string(
                json.dumps(lock_data, indent=2), content_type="application/json"
            )
        except Exception as init_hb_exc:
            log.debug(f"[AF3 Stage 2] Initial slot heartbeat refresh warning: {init_hb_exc}")

    def _heartbeat_loop():
        while not stop_heartbeat.wait(heartbeat_interval_sec):
            try:
                if acquired_slot_blob is None:
                    break
                acquired_slot_blob.reload()
                data = json.loads(acquired_slot_blob.download_as_text())
                if data.get("job_name") != job_name:
                    break
                now_t = time.time()
                data["heartbeat_at"] = now_t
                data["heartbeat_epoch"] = now_t
                acquired_slot_blob.upload_from_string(
                    json.dumps(data, indent=2),
                    content_type="application/json",
                )
            except Exception as hb_exc:
                log.debug(f"[AF3 Stage 2] Slot heartbeat update warning: {hb_exc}")

    if acquired_slot_blob is not None:
        hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        hb_thread.start()

    elapsed_sec = 0.0
    try:
        for stale_blob in list(bucket.list_blobs(prefix=f"{raw_output_prefix}/")):
            try:
                stale_blob.delete()
            except Exception:
                pass

        use_gcs_output_dir = True
        predict_parameters = {
            "run_data_pipeline": not msa_free,
            "num_diffusion_samples": int(num_diffusion_samples),
            "output_dir": raw_output_gcs_uri,
        }
        start_dt = datetime.now(timezone.utc)
        start_iso = start_dt.isoformat()
        t0 = time.time()
        log.info(
            f"[AF3 Stage 2] Calling endpoint.predict() for '{job_name}' on slot {slot_index}/{active_replicas} "
            f"(run_data_pipeline={not msa_free}, num_diffusion_samples={num_diffusion_samples}, "
            f"output_dir={raw_output_gcs_uri}, timeout={timeout_seconds}s)..."
        )

        prediction_response = None
        while True:
            cif_b, conf_b, sum_b = _find_gcs_raw_outputs()
            if cif_b is not None and conf_b is not None and sum_b is not None:
                log.info(
                    f"[AF3 Stage 2] Completed AF3 outputs already present in {raw_output_gcs_uri}."
                )
                time.sleep(2)
                break

            call_t0 = time.time()
            try:
                prediction_response = endpoint.predict(
                    instances=[instance_payload],
                    parameters=predict_parameters,
                    timeout=timeout_seconds,
                )
                break
            except Exception as pred_err:
                call_elapsed = time.time() - call_t0
                err_repr = repr(pred_err) + " " + str(pred_err)

                # Case A: Fast DNS or immediate connection establishment error (< 30s)
                if "NameResolutionError" in err_repr or (
                    "ConnectionError" in err_repr
                    and call_elapsed < 30.0
                    and "RemoteDisconnected" not in err_repr
                ):
                    log.warning(
                        f"[AF3 Stage 2] Fast DNS/ConnectionError after {call_elapsed:.1f}s ({pred_err}); "
                        "refreshing endpoint and retrying after 5s..."
                    )
                    time.sleep(5)
                    endpoint = aiplatform.Endpoint(
                        endpoint_name=endpoint_id,
                        project=project_id,
                        location=endpoint_location,
                    )
                    continue

                # Case B: Container still busy finishing a prior request (HTTP 429)
                if "Status code:429" in err_repr or (
                    "429" in err_repr and "already working on a prediction" in err_repr
                ):
                    if time.time() - t0 > max(timeout_seconds, 3600):
                        raise
                    log.warning(
                        "[AF3 Stage 2] Endpoint container reported HTTP 429 (still finishing prior prediction); "
                        "holding slot and retrying in 20s..."
                    )
                    time.sleep(20)
                    continue

                # Case C: Endpoint tenant SA missing GCS permission on output_dir -> fallback to inline response
                # (Terraform provisions roles/storage.objectAdmin for service-<PROJECT_NUMBER>@gcp-sa-aiplatform-cc.iam.gserviceaccount.com)
                if use_gcs_output_dir and (
                    "does not have storage.objects" in err_repr
                    or "does not have write access" in err_repr
                ):
                    log.warning(
                        f"[AF3 Stage 2] Vertex AI Endpoint service agent lacks write access to {raw_output_gcs_uri} "
                        "(ensure Terraform IAM binding for service-<PROJECT_NUMBER>@gcp-sa-aiplatform-cc.iam.gserviceaccount.com "
                        "is applied); falling back to inline response mode (without output_dir)."
                    )
                    use_gcs_output_dir = False
                    predict_parameters.pop("output_dir", None)
                    predict_parameters.pop("force_output_dir", None)
                    continue

                # Case D: Vertex AI HTTP proxy dropped long-running connection (~480s RemoteDisconnected
                # or ~600s HTTP 504 / DeadlineExceeded) while H100 container continues executing
                if use_gcs_output_dir and (
                    call_elapsed >= 30.0
                    or "RemoteDisconnected" in err_repr
                    or "Connection aborted" in err_repr
                    or "Status code:504" in err_repr
                    or "504" in err_repr
                    or "DeadlineExceeded" in err_repr
                    or "timed out" in err_repr.lower()
                ):
                    log.info(
                        f"[AF3 Stage 2] Vertex AI HTTP front-end closed long-running connection after "
                        f"{call_elapsed:.1f}s ({type(pred_err).__name__}) while H100 container continues "
                        f"running '{job_name}'. Holding replica slot and polling {raw_output_gcs_uri} for completion..."
                    )
                    poll_deadline = call_t0 + max(timeout_seconds, 3600)
                    last_poll_log = 0.0
                    while time.time() < poll_deadline:
                        cif_b, conf_b, sum_b = _find_gcs_raw_outputs()
                        if cif_b is not None and sum_b is not None and conf_b is not None:
                            log.info(
                                f"[AF3 Stage 2] Detected completed AF3 outputs in {raw_output_gcs_uri} "
                                f"after {time.time() - call_t0:.1f}s total runtime!"
                            )
                            time.sleep(3)
                            break
                        if time.time() - last_poll_log >= 60.0:
                            log.info(
                                f"[AF3 Stage 2] Still waiting for H100 container to finish '{job_name}' "
                                f"and upload to {raw_output_gcs_uri} ({(time.time() - call_t0) / 60:.1f}m elapsed)..."
                            )
                            last_poll_log = time.time()
                        time.sleep(15)
                    else:
                        raise RuntimeError(
                            f"Timed out waiting for AF3 outputs in {raw_output_gcs_uri} after {time.time() - call_t0:.1f}s."
                        ) from pred_err
                    break

                raise

        cif_b, conf_b, sum_b = _find_gcs_raw_outputs()
        elapsed_sec = round(time.time() - t0, 2)
        end_dt = datetime.now(timezone.utc)
        end_iso = end_dt.isoformat()

        # If inline fallback was used, persist raw files into raw_output_prefix so Stage 4 reads uniformly
        if (cif_b is None or sum_b is None) and prediction_response is not None:
            predictions = getattr(prediction_response, "predictions", []) or []
            if not predictions:
                raise RuntimeError(
                    f"Agent Platform Endpoint returned empty prediction response for '{job_name}'."
                )
            result_item = predictions[0] if isinstance(predictions, list) else predictions
            cif_content = result_item.get("structure_cif") or result_item.get("cif", "")
            if not cif_content or not cif_content.strip():
                raise RuntimeError(
                    f"Agent Platform Endpoint returned empty CIF coordinates for '{job_name}'."
                )
            summary_confidences = dict(
                result_item.get("summary") or result_item.get("summary_confidences") or {}
            )
            conf_payload = {
                "atom_plddts": result_item.get("plddt"),
                "pae": result_item.get("pae"),
            }
            bucket.blob(f"{raw_output_prefix}/{job_name}_model.cif").upload_from_string(
                cif_content, content_type="chemical/x-mmcif"
            )
            bucket.blob(
                f"{raw_output_prefix}/{job_name}_summary_confidences.json"
            ).upload_from_string(
                json.dumps(summary_confidences, indent=2), content_type="application/json"
            )
            bucket.blob(f"{raw_output_prefix}/{job_name}_confidences.json").upload_from_string(
                json.dumps(conf_payload), content_type="application/json"
            )

        log.info(
            f"[AF3 Stage 2] H100 inference for '{job_name}' completed in {elapsed_sec:.1f}s "
            f"(queue wait: {queue_wait_seconds:.1f}s)."
        )
        outputs = namedtuple(
            "AF3InferenceOutputs",
            [
                "raw_output_gcs_uri",
                "start_iso",
                "end_iso",
                "inference_seconds",
                "slot_released",
            ],
        )
        return outputs(raw_output_gcs_uri, start_iso, end_iso, float(elapsed_sec), True)

    finally:
        stop_heartbeat.set()
        if acquired_slot_blob is not None:
            try:
                if acquired_slot_blob.exists():
                    acquired_slot_blob.reload()
                    lock_data = json.loads(acquired_slot_blob.download_as_text())
                    if lock_data.get("job_name") == job_name:
                        acquired_slot_blob.delete()
                        slot_released = True
                        log.info(
                            f"[AF3 Endpoint Available] Immediately released replica slot '{acquired_slot_blob.name}' "
                            f"— H100 replica {slot_index}/{active_replicas} is now free for the next queued job."
                        )
            except Exception as rel_exc:
                log.warning(f"[AF3 Stage 2] Slot release warning: {rel_exc}")

        try:
            now_ts = time.time()
            now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            activity_blob = bucket.blob(f"af3_predictions/.locks/{endpoint_short_id}_activity.json")
            activity_blob.upload_from_string(
                json.dumps(
                    {
                        "endpoint_id": endpoint.resource_name,
                        "last_activity_epoch": now_ts,
                        "last_activity_iso": now_iso,
                        "last_event": f"inference_finished:{job_name}",
                        "last_pipeline_job_id": pipeline_job_id,
                        "slot_released": slot_released,
                        "idle_shutdown_minutes": int(idle_shutdown_minutes),
                    },
                    indent=2,
                ),
                content_type="application/json",
            )
        except Exception:
            pass


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform>=1.50.0",
        "google-cloud-storage>=2.10.0",
        "requests-toolbelt>=1.0.0",
        "pyyaml>=6.0",
    ],
)
def report_af3_endpoint_available_task(
    project_id: str,
    region: str,
    endpoint_location: str,
    endpoint_id: str,
    bucket_name: str,
    job_name: str,
    pipeline_job_id: str,
    slot_blob_path: str,
    slot_index: int,
    active_replicas: int,
    queue_wait_seconds: float,
    inference_seconds: float,
    slot_released: bool,
    idle_shutdown_minutes: int = 20,
) -> NamedTuple(
    "AF3EndpointAvailableOutputs",
    [
        ("endpoint_status", str),
        ("active_queued_jobs", int),
        ("idle_drain_status", str),
    ],
):
    """Stage 3: Verify replica slot release, report endpoint availability, and arm idle auto-drain."""
    import json
    import logging
    import time
    from collections import namedtuple
    from datetime import datetime, timezone

    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import aiplatform, storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("af3_stage3_available")

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    aiplatform.init(project=project_id, location=endpoint_location)
    endpoint = aiplatform.Endpoint(
        endpoint_name=endpoint_id,
        project=project_id,
        location=endpoint_location,
    )
    endpoint_short_id = endpoint.resource_name.split("/")[-1]

    # Double-check that the replica slot lock is released
    if slot_blob_path:
        try:
            slot_blob = bucket.blob(slot_blob_path)
            if slot_blob.exists():
                slot_blob.reload()
                ld = json.loads(slot_blob.download_as_text())
                if ld.get("job_name") == job_name:
                    slot_blob.delete()
                    log.info(
                        f"[AF3 Stage 3] Cleaned up replica slot '{slot_blob_path}' for '{job_name}'."
                    )
        except Exception as exc:
            log.debug(f"[AF3 Stage 3] Slot verification note: {exc}")

    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stale_heartbeat_sec = 210.0

    # Inspect other active AF3 pipeline jobs and active slot locks
    other_af3_jobs = []
    active_drain_jobs = []
    try:
        creds, _ = google.auth.default()
        authed = AuthorizedSession(creds)
        list_url = (
            f"https://{region}-aiplatform.googleapis.com/v1/"
            f"projects/{project_id}/locations/{region}/pipelineJobs?pageSize=50"
        )
        resp = authed.get(list_url, timeout=15)
        pjobs = resp.json().get("pipelineJobs", []) if resp.ok else []
        active_states = {
            "PIPELINE_STATE_PENDING",
            "PIPELINE_STATE_RUNNING",
            "PIPELINE_STATE_QUEUED",
            "PIPELINE_STATE_CANCELLING",
        }
        for pj in pjobs:
            pj_id = pj.get("name", "").split("/")[-1]
            st = pj.get("state", "")
            if st not in active_states or pj_id == pipeline_job_id:
                continue
            if pj_id.startswith("alphafold3-inference-pipeline-"):
                other_af3_jobs.append(pj_id)
            elif pj_id.startswith("alphafold3-idle-drain-"):
                active_drain_jobs.append(pj_id)
    except Exception as list_exc:
        log.warning(f"[AF3 Stage 3] Could not list active PipelineJobs: {list_exc}")

    active_slots = 0
    for b in bucket.list_blobs(prefix=f"af3_predictions/.locks/{endpoint_short_id}_slot_"):
        try:
            ld = json.loads(b.download_as_text())
            hb = float(
                ld.get("heartbeat_at") or ld.get("heartbeat_epoch") or ld.get("acquired_at") or 0
            )
            if now_ts - hb < stale_heartbeat_sec:
                active_slots += 1
        except Exception:
            pass

    activity_blob = bucket.blob(f"af3_predictions/.locks/{endpoint_short_id}_activity.json")
    activity_payload = {
        "endpoint_id": endpoint.resource_name,
        "last_activity_epoch": now_ts,
        "last_activity_iso": now_iso,
        "last_event": f"endpoint_available_after:{job_name}",
        "last_pipeline_job_id": pipeline_job_id,
        "released_slot_index": int(slot_index),
        "active_replicas": int(active_replicas),
        "occupied_slots": int(active_slots),
        "other_active_af3_jobs": other_af3_jobs,
        "queue_wait_seconds": round(float(queue_wait_seconds), 2),
        "inference_seconds": round(float(inference_seconds), 2),
        "idle_shutdown_minutes": int(idle_shutdown_minutes),
    }
    activity_blob.upload_from_string(
        json.dumps(activity_payload, indent=2), content_type="application/json"
    )

    endpoint_status = "available"
    idle_drain_status = "none"

    def _ensure_idle_watchdog() -> str:
        if active_drain_jobs:
            log.info(
                f"[AF3 Stage 3] Refreshed {idle_shutdown_minutes}m countdown on active watchdog '{active_drain_jobs[0]}'."
            )
            return active_drain_jobs[0]
        if not bucket.blob("af3_predictions/.locks/af3_idle_drain_pipeline.json").exists():
            return "none"
        # Guard against concurrent Stage 3 tasks spawning duplicate watchdogs within 90s
        spawn_lock_blob = bucket.blob(
            f"af3_predictions/.locks/{endpoint_short_id}_watchdog_spawn.json"
        )
        try:
            if spawn_lock_blob.exists():
                spawn_data = json.loads(spawn_lock_blob.download_as_text())
                if time.time() - float(spawn_data.get("spawned_at", 0)) < 90.0:
                    existing_id = spawn_data.get("drain_job_id", "spawning")
                    log.info(
                        f"[AF3 Stage 3] Watchdog '{existing_id}' was recently spawned; reusing."
                    )
                    return existing_id
        except Exception:
            pass

        drain_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        drain_job_id = f"alphafold3-idle-drain-{drain_ts}"
        try:
            spawn_lock_blob.upload_from_string(
                json.dumps({"drain_job_id": drain_job_id, "spawned_at": time.time()}),
                content_type="application/json",
            )
        except Exception:
            pass
        drain_template_uri = (
            f"gs://{bucket_name}/af3_predictions/.locks/af3_idle_drain_pipeline.json"
        )
        drain_job = aiplatform.PipelineJob(
            display_name="af3-endpoint-idle-drain",
            template_path=drain_template_uri,
            job_id=drain_job_id,
            pipeline_root=f"gs://{bucket_name}/pipeline_runs/idle_drain_{drain_ts}",
            parameter_values={
                "project_id": project_id,
                "region": region,
                "endpoint_location": endpoint_location,
                "endpoint_id": endpoint.resource_name,
                "bucket_name": bucket_name,
                "idle_shutdown_minutes": int(idle_shutdown_minutes),
            },
            enable_caching=False,
            project=project_id,
            location=region,
            labels={
                "model_type": "af3_system",
                "job_type": "af3_idle_drain",
                "submitted_by": "foldrun-agent",
            },
        )
        try:
            drain_job.submit(service_account=f"pipelines-sa@{project_id}.iam.gserviceaccount.com")
            log.info(
                f"[AF3 Stage 3] Launched {idle_shutdown_minutes}m idle-drain watchdog '{drain_job_id}'."
            )
            return drain_job_id
        except Exception as drain_exc:
            log.warning(
                f"[AF3 Stage 3] Could not submit idle-drain watchdog '{drain_job_id}': {drain_exc}"
            )
            return "watchdog_submit_skipped"

    if other_af3_jobs or active_slots > 0:
        endpoint_status = "available_serving_queue"
        log.info(
            f"[AF3 Stage 3] Endpoint '{endpoint.resource_name}' replica {slot_index}/{active_replicas} "
            f"reported AVAILABLE after '{job_name}' ({inference_seconds:.1f}s inference). "
            f"{len(other_af3_jobs)} other AF3 pipeline job(s) ({other_af3_jobs}) and "
            f"{active_slots} active slot(s) are currently active."
        )
        if idle_shutdown_minutes > 0:
            idle_drain_status = _ensure_idle_watchdog()
        else:
            idle_drain_status = f"deferred ({len(other_af3_jobs)} queued/running)"
    elif idle_shutdown_minutes == 0:
        endpoint_status = "auto_undeployed"
        idle_drain_status = "undeployed_immediately"
        log.info(
            "[AF3 Stage 3] Queue is empty and idle_shutdown_minutes=0 -> "
            "undeploying H100 endpoint immediately ($0.00/hr)."
        )
        endpoint.undeploy_all(sync=False)
        activity_payload["state"] = "auto_undeployed"
        activity_blob.upload_from_string(
            json.dumps(activity_payload, indent=2), content_type="application/json"
        )
    elif idle_shutdown_minutes > 0:
        idle_drain_status = _ensure_idle_watchdog()
        endpoint_status = (
            "available_idle_timer_reset" if active_drain_jobs else "available_idle_watchdog_started"
        )

    outputs = namedtuple(
        "AF3EndpointAvailableOutputs",
        ["endpoint_status", "active_queued_jobs", "idle_drain_status"],
    )
    return outputs(endpoint_status, len(other_af3_jobs), idle_drain_status)


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform>=1.50.0",
        "google-cloud-storage>=2.10.0",
        "google-cloud-logging>=3.5.0",
        "google-genai>=1.0.0",
        "matplotlib>=3.7.0",
        "numpy>=1.24.0",
    ],
)
def process_af3_results_task(
    project_id: str,
    region: str,
    endpoint_location: str,
    endpoint_id: str,
    bucket_name: str,
    job_name: str,
    pipeline_job_id: str,
    query_json_path: str,
    pipeline_root: str,
    job_prefix: str,
    raw_output_gcs_uri: str,
    start_iso: str,
    end_iso: str,
    queue_wait_seconds: float,
    inference_seconds: float,
    endpoint_status: str,
    msa_free: bool = False,
) -> NamedTuple(
    "AF3PredictOutputs",
    [
        ("cif_uri", str),
        ("summary_uri", str),
        ("ranking_score", float),
        ("mean_plddt", float),
    ],
):
    """Stage 4: Harvest container logs, render confidence plots, and run Gemini 3.1 Pro Expert Analysis."""
    import io
    import json
    import logging
    import time
    from collections import namedtuple
    from datetime import datetime, timezone

    import numpy as np
    from google.cloud import storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("af3_stage4_process")

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    q_bucket_name, q_blob_path = query_json_path[5:].split("/", 1)
    instance_payload = json.loads(
        storage_client.bucket(q_bucket_name).blob(q_blob_path).download_as_text()
    )

    endpoint_short_id = endpoint_id.rstrip("/").split("/")[-1]
    job_prefix_clean = job_prefix.strip("/")
    raw_output_prefix = f"{job_prefix_clean}/af3_raw"

    # 1. Harvest AF3 container execution logs from Cloud Logging for [start_iso, end_iso]
    execution_log_lines = [
        f"# AlphaFold 3 Endpoint Execution Log — {job_name}",
        f"# Pipeline Job ID: {pipeline_job_id}",
        f"# Endpoint: {endpoint_id} (Post-Inference Status: {endpoint_status})",
        f"# Mode: {'Zero-MSA (--msa-free)' if msa_free else 'Full 630 GB MSA + PDB Templates'}",
        f"# Queue Wait: {queue_wait_seconds:.1f}s | H100 Inference Window: {start_iso} -> {end_iso} ({inference_seconds:.1f}s)",
        "",
    ]
    try:
        from google.cloud import logging as cloud_logging

        time.sleep(2)
        log_client = cloud_logging.Client(project=project_id)
        log_filter = (
            f'resource.type="aiplatform.googleapis.com/Endpoint" '
            f'AND resource.labels.endpoint_id="{endpoint_short_id}" '
            f'AND timestamp>="{start_iso}" '
            f'AND timestamp<="{datetime.now(timezone.utc).isoformat()}"'
        )
        fetched_lines = []
        for entry in log_client.list_entries(filter_=log_filter, page_size=500):
            payload = entry.payload
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("message") or "")
            elif isinstance(payload, str):
                msg = payload
            if not msg or "/health" in msg:
                continue
            ts_str = entry.timestamp.isoformat() if entry.timestamp else ""
            fetched_lines.append(f"{ts_str}  {msg}")
        if fetched_lines:
            log.info(f"[AF3 Stage 4] Harvested {len(fetched_lines)} container log lines.")
            execution_log_lines.extend(fetched_lines)
        else:
            execution_log_lines.append("(No container log entries matched time window.)")
    except Exception as log_exc:
        log.warning(f"[AF3 Stage 4] Cloud Logging harvest warning: {log_exc}")
        execution_log_lines.append(f"(Cloud Logging query skipped: {log_exc})")

    execution_log_text = "\n".join(execution_log_lines) + "\n"

    # 2. Load raw prediction artifacts from GCS (all seed-*_sample-* subdirectories + top-level best)
    import re as _re

    top_cif_b = None
    top_conf_b = None
    top_sum_b = None
    sample_blobs: dict = {}

    for b in bucket.list_blobs(prefix=f"{raw_output_prefix}/"):
        bname = b.name
        m_seed = _re.search(r"/(seed-\d+_sample-\d+)/", bname)
        if m_seed:
            s_key = m_seed.group(1)
            s_entry = sample_blobs.setdefault(s_key, {})
            if bname.endswith("_model.cif") or bname.endswith("/model.cif"):
                s_entry["cif"] = b
            elif bname.endswith("_summary_confidences.json") or bname.endswith(
                "/summary_confidences.json"
            ):
                s_entry["summary"] = b
            elif bname.endswith("_confidences.json") or bname.endswith("/confidences.json"):
                s_entry["conf"] = b
            continue
        if bname.endswith("_model.cif"):
            top_cif_b = b
        elif bname.endswith("_summary_confidences.json"):
            top_sum_b = b
        elif bname.endswith("_confidences.json"):
            top_conf_b = b

    valid_samples = {
        k: v
        for k, v in sorted(sample_blobs.items())
        if v.get("cif") is not None and v.get("summary") is not None
    }
    if not valid_samples:
        if top_cif_b is None or top_sum_b is None:
            raise RuntimeError(
                f"Stage 4 could not find raw AF3 outputs (_model.cif / _summary_confidences.json) in {raw_output_gcs_uri}."
            )
        default_seed = (instance_payload.get("modelSeeds") or [1])[0]
        valid_samples = {
            f"seed-{default_seed}_sample-0": {
                "cif": top_cif_b,
                "summary": top_sum_b,
                "conf": top_conf_b,
            }
        }

    log.info(
        f"[AF3 Stage 4] Discovered {len(valid_samples)} AF3 sample(s) in {raw_output_gcs_uri}: "
        f"{list(valid_samples.keys())}"
    )

    mode_label = "Zero-MSA (--msa-free)" if msa_free else "Full MSA + Templates"
    pipeline_root_prefix = (
        pipeline_root[5:].split("/", 1)[1].strip("/")
        if pipeline_root.startswith("gs://") and "/" in pipeline_root[5:]
        else f"pipeline_runs/{pipeline_job_id}"
    )

    # 3. Parse CIF & summary_confidences for every sample and sort by ranking_score descending
    parsed_samples = []
    for s_key, s_files in valid_samples.items():
        s_cif_b = s_files["cif"]
        s_sum_b = s_files["summary"]
        s_conf_b = s_files.get("conf")

        s_cif_content = s_cif_b.download_as_text()
        s_summary_conf = dict(json.loads(s_sum_b.download_as_text()) or {})

        s_ranking_score = s_summary_conf.get("ranking_score")
        if s_ranking_score is None:
            ptm_val = s_summary_conf.get("ptm")
            iptm_val = s_summary_conf.get("iptm")
            frac_dis = float(s_summary_conf.get("fraction_disordered", 0.0) or 0.0)
            has_clash_val = float(s_summary_conf.get("has_clash", 0.0) or 0.0)
            if ptm_val is not None:
                eff_iptm = float(iptm_val) if iptm_val is not None else float(ptm_val)
                s_ranking_score = (
                    0.8 * eff_iptm + 0.2 * float(ptm_val) + 0.5 * frac_dis - 100.0 * has_clash_val
                )
            else:
                s_ranking_score = 0.0
        s_ranking_score = round(float(s_ranking_score), 4)

        s_atom_lines = []
        for line in s_cif_content.splitlines():
            if line.startswith(("ATOM ", "HETATM ")):
                parts = line.split()
                if len(parts) >= 15:
                    chain_id = parts[6]
                    res_seq = parts[8]
                    try:
                        bfac = float(parts[14])
                    except (ValueError, IndexError):
                        bfac = None
                    s_atom_lines.append((chain_id, res_seq, bfac))

        s_res_plddts = []
        if s_atom_lines:
            res_scores = {}
            res_order = []
            for chain_id, res_seq, bfac in s_atom_lines:
                if bfac is None:
                    continue
                key = (chain_id, res_seq)
                if key not in res_scores:
                    res_scores[key] = []
                    res_order.append(key)
                res_scores[key].append(bfac)
            s_res_plddts = [float(np.mean(res_scores[k])) for k in res_order]

        if s_res_plddts:
            plddt_arr = np.array(s_res_plddts, dtype=float)
            s_plddt_mean = round(float(np.mean(plddt_arr)), 2)
            s_plddt_median = round(float(np.median(plddt_arr)), 2)
            s_plddt_min = round(float(np.min(plddt_arr)), 2)
            s_plddt_max = round(float(np.max(plddt_arr)), 2)
            s_plddt_dist = {
                "very_low_confidence": int(np.sum(plddt_arr < 50)),
                "low_confidence": int(np.sum((plddt_arr >= 50) & (plddt_arr < 70))),
                "high_confidence": int(np.sum((plddt_arr >= 70) & (plddt_arr < 90))),
                "very_high_confidence": int(np.sum(plddt_arr >= 90)),
            }
        else:
            raw_mean = s_summary_conf.get("mean_plddt")
            s_plddt_mean = round(float(raw_mean), 2) if raw_mean is not None else 0.0
            s_plddt_median = s_plddt_mean
            s_plddt_min = s_plddt_mean
            s_plddt_max = s_plddt_mean
            s_plddt_dist = {
                "very_low_confidence": 0,
                "low_confidence": 0,
                "high_confidence": 0,
                "very_high_confidence": 0,
            }

        s_summary_conf["ranking_score"] = s_ranking_score
        s_summary_conf["mean_plddt"] = s_plddt_mean

        parsed_samples.append(
            {
                "s_key": s_key,
                "cif_b": s_cif_b,
                "sum_b": s_sum_b,
                "conf_b": s_conf_b,
                "cif_content": s_cif_content,
                "summary_confidences": s_summary_conf,
                "ranking_score": s_ranking_score,
                "res_plddts": s_res_plddts,
                "plddt_mean": s_plddt_mean,
                "plddt_median": s_plddt_median,
                "plddt_min": s_plddt_min,
                "plddt_max": s_plddt_max,
                "plddt_dist": s_plddt_dist,
            }
        )

    parsed_samples.sort(
        key=lambda item: (item["ranking_score"], item["plddt_mean"]),
        reverse=True,
    )

    # 4. Render per-sample pLDDT & PAE PNG plots and build ranked prediction entries
    cif_filename = f"{job_name}_model.cif"
    conf_filename = f"{job_name}_summary_confidences.json"
    cif_uri = f"gs://{bucket_name}/{job_prefix_clean}/{cif_filename}"

    ranked_predictions = []
    best_plddt_png = None
    best_pae_png = None
    best_pae_matrix = None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        plt = None

    for rank_idx, item in enumerate(parsed_samples, start=1):
        plot_idx = rank_idx - 1
        s_key = item["s_key"]
        s_Label = s_key.replace("-", "_")
        s_sum = item["summary_confidences"]
        s_res_plddts = item["res_plddts"]
        s_conf_b = item["conf_b"]

        s_pae_matrix = None
        if s_conf_b is not None and (
            len(parsed_samples) <= 10
            or rank_idx <= 5
            or (getattr(s_conf_b, "size", 0) or 0) <= 5_000_000
        ):
            try:
                s_conf_data = json.loads(s_conf_b.download_as_text())
                s_pae_matrix = s_conf_data.get("pae")
            except Exception as conf_exc:
                log.warning(f"[AF3 Stage 4] Could not load confidences for {s_key}: {conf_exc}")

        if s_pae_matrix and len(s_pae_matrix) > 0:
            pae_arr = np.array(s_pae_matrix, dtype=float)
            s_pae_mean = round(float(np.mean(pae_arr)), 2)
            s_pae_median = round(float(np.median(pae_arr)), 2)
            s_pae_min = round(float(np.min(pae_arr)), 2)
            s_pae_max = round(float(np.max(pae_arr)), 2)
            s_sum["mean_pae"] = s_pae_mean
        else:
            s_pae_mean = s_sum.get("mean_pae")
            s_pae_median = s_pae_mean
            s_pae_min = s_pae_mean
            s_pae_max = s_pae_mean

        s_plddt_png = None
        s_pae_png = None
        if plt is not None:
            try:
                title_suffix = f"{job_name} — {s_Label} ({mode_label})"
                if s_res_plddts:
                    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=140)
                    xs = np.arange(1, len(s_res_plddts) + 1)
                    ax.axhspan(90, 100, color="#0053D6", alpha=0.12, label="Very High (>90)")
                    ax.axhspan(70, 90, color="#65CBF3", alpha=0.14, label="Confident (70-90)")
                    ax.axhspan(50, 70, color="#FFDB13", alpha=0.14, label="Low (50-70)")
                    ax.axhspan(0, 50, color="#FF7D45", alpha=0.12, label="Very Low (<50)")
                    ax.plot(xs, s_res_plddts, color="#0053D6", linewidth=2.0)
                    ax.set_xlim(1, max(len(s_res_plddts), 2))
                    ax.set_ylim(0, 100)
                    ax.set_xlabel("Residue / Token Position")
                    ax.set_ylabel("pLDDT")
                    ax.set_title(
                        f"AlphaFold 3 Per-Residue Confidence (pLDDT) — {title_suffix}",
                        fontsize=10,
                        fontweight="bold",
                    )
                    ax.legend(loc="lower right", fontsize=8, framealpha=0.9)
                    ax.grid(True, linestyle="--", alpha=0.3)
                    fig.tight_layout()
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png")
                    plt.close(fig)
                    s_plddt_png = buf.getvalue()

                if s_pae_matrix and len(s_pae_matrix) > 0:
                    fig2, ax2 = plt.subplots(figsize=(5.2, 4.6), dpi=140)
                    pae_arr = np.array(s_pae_matrix, dtype=float)
                    im = ax2.imshow(pae_arr, cmap="Greens_r", vmin=0, vmax=31.75, origin="upper")
                    cbar = fig2.colorbar(im, ax=ax2, fraction=0.046, pad=0.04)
                    cbar.set_label("Expected Position Error (Å)")
                    ax2.set_xlabel("Scored Residue")
                    ax2.set_ylabel("Aligned Residue")
                    ax2.set_title(
                        f"Predicted Aligned Error (PAE) — {title_suffix}",
                        fontsize=10,
                        fontweight="bold",
                    )
                    fig2.tight_layout()
                    buf2 = io.BytesIO()
                    fig2.savefig(buf2, format="png")
                    plt.close(fig2)
                    s_pae_png = buf2.getvalue()
            except Exception as plot_exc:
                log.warning(f"[AF3 Stage 4] Plot generation warning for {s_key}: {plot_exc}")

        s_plots_dict = {}
        if s_plddt_png:
            plddt_rel = f"{job_prefix_clean}/analysis/plddt_plot_{plot_idx}.png"
            bucket.blob(plddt_rel).upload_from_string(s_plddt_png, content_type="image/png")
            bucket.blob(
                f"{pipeline_root_prefix}/analysis/plddt_plot_{plot_idx}.png"
            ).upload_from_string(s_plddt_png, content_type="image/png")
            s_plots_dict["plddt_plot"] = f"gs://{bucket_name}/{plddt_rel}"
        if s_pae_png:
            pae_rel = f"{job_prefix_clean}/analysis/pae_plot_{plot_idx}.png"
            bucket.blob(pae_rel).upload_from_string(s_pae_png, content_type="image/png")
            bucket.blob(
                f"{pipeline_root_prefix}/analysis/pae_plot_{plot_idx}.png"
            ).upload_from_string(s_pae_png, content_type="image/png")
            s_plots_dict["pae_plot"] = f"gs://{bucket_name}/{pae_rel}"

        if rank_idx == 1:
            best_plddt_png = s_plddt_png
            best_pae_png = s_pae_png
            best_pae_matrix = s_pae_matrix

        s_plddt_mean = item["plddt_mean"]
        if s_plddt_mean >= 90:
            s_qa = "very_high_confidence"
        elif s_plddt_mean >= 70:
            s_qa = "high_confidence"
        elif s_plddt_mean >= 50:
            s_qa = "low_confidence"
        else:
            s_qa = "very_low_confidence"

        s_cif_uri = (
            cif_uri if len(parsed_samples) == 1 else f"gs://{bucket_name}/{item['cif_b'].name}"
        )
        s_ptm = s_sum.get("ptm")
        s_iptm = s_sum.get("iptm")
        s_has_clash = float(s_sum.get("has_clash", 0.0) or 0.0)
        s_frac_dis = float(s_sum.get("fraction_disordered", 0.0) or 0.0)

        ranked_predictions.append(
            {
                "rank": rank_idx,
                "sample_name": f"{s_Label} ({mode_label})",
                "model_name": f"AlphaFold 3 ({mode_label})",
                "cif_uri": s_cif_uri,
                "uri": s_cif_uri,
                "ranking_score": item["ranking_score"],
                "ranking_confidence": item["ranking_score"],
                "ptm": s_ptm,
                "iptm": s_iptm,
                "plddt_mean": s_plddt_mean,
                "plddt_median": item["plddt_median"],
                "plddt_min": item["plddt_min"],
                "plddt_max": item["plddt_max"],
                "plddt_distribution": item["plddt_dist"],
                "plddt_scores": [round(x, 2) for x in s_res_plddts[:2000]],
                "pae_mean": s_pae_mean,
                "pae_median": s_pae_median,
                "pae_min": s_pae_min,
                "pae_max": s_pae_max,
                "has_pae": s_pae_mean is not None,
                "has_pde": False,
                "has_clash": s_has_clash,
                "fraction_disordered": s_frac_dis,
                "chain_ptm": s_sum.get("chain_ptm"),
                "chain_iptm": s_sum.get("chain_iptm"),
                "chain_pair_iptm": s_sum.get("chain_pair_iptm"),
                "chain_pair_pae_min": s_sum.get("chain_pair_pae_min"),
                "quality_assessment": s_qa,
                "plots": s_plots_dict,
            }
        )

    # 5. Upload canonical top-level artifacts to af3_predictions/<job_name>/ and pipeline_root
    best_item = parsed_samples[0]
    pred_entry = ranked_predictions[0]
    cif_content = best_item["cif_content"]
    summary_confidences = best_item["summary_confidences"]
    ranking_score = pred_entry["ranking_score"]
    plddt_mean = pred_entry["plddt_mean"]
    plddt_median = pred_entry["plddt_median"]
    plddt_min = pred_entry["plddt_min"]
    plddt_max = pred_entry["plddt_max"]
    plddt_dist = pred_entry["plddt_distribution"]
    pae_mean = pred_entry["pae_mean"]
    pae_min = pred_entry["pae_min"]
    pae_max = pred_entry["pae_max"]
    ptm = pred_entry["ptm"]
    iptm = pred_entry["iptm"]
    has_clash = pred_entry["has_clash"]
    fraction_disordered = pred_entry["fraction_disordered"]
    chain_ptm = pred_entry["chain_ptm"]
    chain_iptm = pred_entry["chain_iptm"]
    chain_pair_iptm = pred_entry["chain_pair_iptm"]
    chain_pair_pae_min = pred_entry["chain_pair_pae_min"]
    quality_assessment = pred_entry["quality_assessment"]
    plddt_png = best_plddt_png
    pae_png = best_pae_png
    pae_matrix = best_pae_matrix

    bucket.blob(f"{job_prefix_clean}/input.json").upload_from_string(
        json.dumps(instance_payload, indent=2), content_type="application/json"
    )
    bucket.blob(f"{pipeline_root_prefix}/input.json").upload_from_string(
        json.dumps(instance_payload, indent=2), content_type="application/json"
    )
    bucket.blob(f"{job_prefix_clean}/execution.log").upload_from_string(
        execution_log_text, content_type="text/plain; charset=utf-8"
    )
    bucket.blob(f"{pipeline_root_prefix}/execution.log").upload_from_string(
        execution_log_text, content_type="text/plain; charset=utf-8"
    )
    bucket.blob(f"{job_prefix_clean}/{cif_filename}").upload_from_string(
        cif_content, content_type="chemical/x-mmcif"
    )
    bucket.blob(f"{job_prefix_clean}/{conf_filename}").upload_from_string(
        json.dumps(summary_confidences, indent=2), content_type="application/json"
    )
    if pae_matrix:
        bucket.blob(f"{job_prefix_clean}/pae.json").upload_from_string(
            json.dumps(pae_matrix, indent=2), content_type="application/json"
        )

    chain_composition = []
    total_residues = 0
    for entity in instance_payload.get("sequences", []):
        for mol_type in ("protein", "rna", "dna"):
            if mol_type in entity:
                c_data = entity[mol_type]
                seq_str = c_data.get("sequence", "")
                total_residues += len(seq_str)
                chain_composition.append(
                    {
                        "chain_id": c_data.get("id", "A"),
                        "molecule_type": mol_type,
                        "residue_count": len(seq_str),
                    }
                )
        for lig_type in ("ligand", "ion"):
            if lig_type in entity:
                c_data = entity[lig_type]
                chain_composition.append(
                    {
                        "chain_id": c_data.get("id", "L"),
                        "molecule_type": lig_type,
                        "ccd_codes": c_data.get("ccdCodes"),
                        "smiles": c_data.get("smiles"),
                    }
                )

    all_plddt_means = [p["plddt_mean"] for p in ranked_predictions]
    all_ranking_scores = [p["ranking_score"] for p in ranked_predictions]
    all_pae_means = [p["pae_mean"] for p in ranked_predictions if p.get("pae_mean") is not None]

    duration_formatted = (
        f"{inference_seconds:.1f}s" if inference_seconds < 60 else f"{inference_seconds / 60:.1f}m"
    )
    expert_md_lines = [
        f"### AlphaFold 3 Structure Prediction Summary (`{job_name}`)",
        "",
        f"- **Execution Mode**: **{mode_label}** (`run_data_pipeline={not msa_free}`, **{len(ranked_predictions)} sample(s)**) in **{duration_formatted}** on Vertex AI Dedicated Endpoint (`NVIDIA_H100_80GB`, queue wait `{queue_wait_seconds:.1f}s`, KFP PipelineJob `{pipeline_job_id}`).",
        f"- **Overall Quality**: **{quality_assessment.replace('_', ' ').title()}** "
        f"(Mean pLDDT: **{plddt_mean:.2f}**, Ranking Score: **{ranking_score:.4f}**, "
        f"pTM: **{ptm if ptm is not None else 'N/A'}**"
        + (f", ipTM: **{iptm}**" if iptm is not None else "")
        + (f", Mean PAE: **{pae_mean:.2f} Å**" if pae_mean is not None else "")
        + ").",
        f"- **Clash & Disorder Check**: `has_clash = {has_clash}`, "
        f"`fraction_disordered = {fraction_disordered:.2f}`.",
    ]

    base_summary = {
        "job_id": job_name,
        "pipeline_job_id": pipeline_job_id,
        "query_name": job_name,
        "af3_job_dir": f"gs://{bucket_name}/{job_prefix_clean}",
        "execution_log_uri": f"gs://{bucket_name}/{job_prefix_clean}/execution.log",
        "model_type": "alphafold3",
        "analyzed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_predictions": len(ranked_predictions),
        "successful_analyses": len(ranked_predictions),
        "failed_analyses": 0,
        "summary": {
            "protein_info": {
                "total_length": total_residues,
                "total_predictions": len(ranked_predictions),
                "num_chains": len(chain_composition) or 1,
                "chain_composition": chain_composition,
                "input_query_json": instance_payload,
                "job_metadata": {
                    "display_name": job_name,
                    "pipeline_job_id": pipeline_job_id,
                    "state": "PIPELINE_STATE_SUCCEEDED",
                    "started": start_iso,
                    "completed": end_iso,
                    "duration_seconds": round(float(inference_seconds), 2),
                    "queue_wait_seconds": round(float(queue_wait_seconds), 2),
                    "duration_formatted": duration_formatted,
                    "endpoint_post_status": endpoint_status,
                    "machine_type": "a3-highgpu-1g",
                    "accelerator_type": "NVIDIA_H100_80GB",
                    "accelerator_count": 1,
                    "gpu_type": "H100_80GB",
                    "job_type": "alphafold3",
                    "seq_len": total_residues,
                    "seq_name": job_name,
                },
            },
            "quality_metrics": {
                "best_model": pred_entry["sample_name"],
                "best_model_plddt": plddt_mean,
                "best_model_pae": pae_mean,
                "best_ranking_score": ranking_score,
                "best_ptm": ptm,
                "best_iptm": iptm,
                "has_clash": has_clash > 0.0,
                "fraction_disordered": fraction_disordered,
                "quality_assessment": quality_assessment,
            },
            "ranking_score_stats": {
                "mean": round(float(np.mean(all_ranking_scores)), 4),
                "min": round(float(np.min(all_ranking_scores)), 4),
                "max": round(float(np.max(all_ranking_scores)), 4),
                "range": round(float(np.max(all_ranking_scores) - np.min(all_ranking_scores)), 4),
            },
            "plddt_stats": {
                "mean_across_models": round(float(np.mean(all_plddt_means)), 2),
                "std_across_models": round(float(np.std(all_plddt_means)), 2),
                "min_model_mean": round(float(np.min(all_plddt_means)), 2),
                "max_model_mean": round(float(np.max(all_plddt_means)), 2),
                "range": round(float(np.max(all_plddt_means) - np.min(all_plddt_means)), 2),
            },
            "pae_stats": {
                "mean_across_models": round(float(np.mean(all_pae_means)), 2),
                "min_model_mean": round(float(np.min(all_pae_means)), 2),
                "max_model_mean": round(float(np.max(all_pae_means)), 2),
            }
            if all_pae_means
            else {},
            "recommendations": [
                f"Best structure: {pred_entry['sample_name']} (pLDDT: {plddt_mean:.2f}, pTM: {ptm}, Ranking Score: {ranking_score:.4f})",
            ],
        },
        "best_prediction": pred_entry,
        "top_predictions": ranked_predictions[:10],
        "all_predictions_summary": ranked_predictions,
    }

    now_ea_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    disclaimer_text = (
        "\n\n---\n"
        "*This analysis was generated by AI (Google Gemini) and has not been "
        "peer-reviewed. It is intended as a starting point for interpretation, "
        "not as expert biological advice. Predictions should be validated "
        "experimentally by qualified scientists before use in research or "
        "clinical decisions.*"
    )
    expert_analysis_obj = {
        "status": "success",
        "model": "gemini-3.1-pro-preview",
        "generated_at": now_ea_iso,
        "ai_generated": True,
        "analysis": "\n".join(expert_md_lines) + disclaimer_text,
    }
    try:
        from google import genai
        from google.genai import types

        seq_lines = []
        for ent in instance_payload.get("sequences", []):
            for mol_t in ("protein", "rna", "dna"):
                if mol_t in ent:
                    c = ent[mol_t]
                    seq_lines.append(
                        f">{job_name}|Chain_{c.get('id', 'A')}|{mol_t}\n{c.get('sequence', '')}"
                    )
            for lig_t in ("ligand", "ion"):
                if lig_t in ent:
                    c = ent[lig_t]
                    ccd = c.get("ccdCodes") or c.get("ion") or c.get("smiles")
                    seq_lines.append(f"# Chain {c.get('id', 'L')} ({lig_t}): {ccd}")

        chain_desc_lines = []
        for ci in chain_composition:
            cid = ci.get("chain_id", "A")
            mtype = ci.get("molecule_type", "protein")
            if mtype in ("ligand", "ion"):
                comps = ci.get("ccd_codes") or ci.get("smiles") or mtype
                chain_desc_lines.append(f"- Chain {cid}: {mtype} ({comps})")
            else:
                chain_desc_lines.append(
                    f"- Chain {cid}: {mtype}, {ci.get('residue_count', 'N/A')} residues/nt"
                )
        chain_desc = "\n".join(chain_desc_lines) or f"- {total_residues} total tokens/residues"

        ea_prompt = f"""You are an expert structural biologist and computational chemist reviewing AlphaFold 3 structure prediction results generated on Google Cloud Vertex AI.

**AlphaFold 3 Reference (use to interpret results):**
- Pairformer + Diffusion-based biomolecular structure prediction model supporting proteins, RNA, DNA, covalent modifications, small-molecule ligands (CCD/SMILES), and metal ions.
- **Metric interpretation thresholds:**
  - `ranking_score` (-100 to 1.5, typically 0–1.5): Weighted global confidence (`0.8 * ipTM + 0.2 * pTM + 0.5 * fraction_disordered - 100 * has_clash`; for monomers `ptm + 0.5 * fraction_disordered - 100 * has_clash`). Scores slightly above `1.0` (e.g. `1.00–1.10`) occur naturally when a high-confidence complex contains flexible/disordered terminal residues (`+0.5 * fraction_disordered`). `>0.8` = high confidence; `>0.9` = very high confidence.
  - `pTM` (0–1): Global Template Modeling score. `>0.8` = high confidence global fold; `0.5–0.8` = moderate fold confidence; `<0.5` = uncertain global topology. Note: For short nucleic acid or peptide chains (`L < 50` nt/aa), the TM-score length-scaling factor $d_0(L)$ is conservative and often yields low `pTM` (`0.30–0.45`) even when `mean_plddt > 80` and `mean_pae < 5 Å` confirm a well-defined local 3D fold.
  - `ipTM` (0–1): Interface predicted TM-score across interacting chains/ligands. `>0.75` = reliable complex interface; `0.6–0.75` = plausible interface requiring validation; `<0.6` = uncertain binding pose/orientation.
  - `pLDDT` (0–100): Per-atom/per-residue local confidence (`>90` very high accuracy including sidechains; `70–90` confident backbone; `50–70` low confidence/flexible loop; `<50` disordered or unconstrained).
  - `PAE` (Predicted Aligned Error, Å): Expected positional error at residue `y` when aligned on residue `x`. Low off-diagonal blocks (`<5 Å`) indicate rigid inter-domain or inter-chain packing; high off-diagonal PAE (`>15 Å`) indicates independent domain motion or uncertain docking orientation.

**Prediction Summary:**
- Target / Query Name: `{job_name}` (Pipeline Job ID: `{pipeline_job_id}`)
- Execution Mode: {mode_label} (`run_data_pipeline={not msa_free}`)
- H100 Inference Runtime: `{duration_formatted}` (Queue Wait: `{queue_wait_seconds:.1f}s`)
- Total Polymer Residues / Tokens: {total_residues} across {len(chain_composition) or 1} chain(s)
- Best Sample: `{pred_entry["sample_name"]}`

**Chain & Entity Composition:**
{chain_desc}

**Quality Metrics (Best Sample):**
- Ranking Score: `{ranking_score:.4f}`
- pTM: `{ptm if ptm is not None else "N/A"}`
- ipTM: `{iptm if iptm is not None else "N/A (monomer)"}`
- Mean pLDDT: `{plddt_mean:.2f}` (median: `{plddt_median}`, range: `[{plddt_min}, {plddt_max}]`)
- Mean PAE: `{f"{pae_mean:.2f} Å" if pae_mean is not None else "N/A"}` (range: `[{pae_min}, {pae_max}] Å`)
- Has Clash: `{has_clash}`
- Fraction Disordered: `{fraction_disordered}`
- Per-Chain pTM: `{json.dumps(chain_ptm)}`
- Per-Chain ipTM: `{json.dumps(chain_iptm)}`
- Chain-Pair ipTM: `{json.dumps(chain_pair_iptm)}`
- Chain-Pair Min PAE (Å): `{json.dumps(chain_pair_pae_min)}`

**Per-Residue Confidence Distribution (pLDDT):**
- Very High (`>=90`): {plddt_dist["very_high_confidence"]} residues
- Confident (`70–90`): {plddt_dist["high_confidence"]} residues
- Low (`50–70`): {plddt_dist["low_confidence"]} residues
- Very Low (`<50`): {plddt_dist["very_low_confidence"]} residues

**Input Biomolecular Entities & Sequences:**
```
{chr(10).join(seq_lines)}
```

**Expert Analysis Request:**

Provide a comprehensive structural biology assessment covering:
1. **Overall Prediction Quality**: Evaluate the `ranking_score`, `pTM`, `ipTM`, `mean_plddt`, and `mean_pae`. How reliable is the predicted 3D structure?
2. **Per-Chain & Domain Confidence Analysis**: Analyze the per-residue pLDDT distribution and the attached pLDDT profile plot. Identify well-ordered core secondary structure elements versus flexible loops or N/C-terminal tails.
3. **Ligand / Cofactor / Nucleic Acid Coordination** (if ligands, ions, RNA, or DNA are present; otherwise evaluate active-site / binding-pocket readiness): Assess cofactor/ligand/ion placement (`ATP`, `MG`, `ZN`, inhibitor, DNA/RNA groove, etc.) using `ipTM`, `chain_pair_pae_min`, and local pLDDT.
4. **Domain Packing & Interface Analysis**: Interpret the PAE heatmap (`pae_plot`) and `chain_pair_iptm` / `chain_pair_pae_min` matrices. Which domains or inter-chain interfaces form rigid structural units, and where is conformational flexibility observed?
5. **MSA & Template Alignment Impact**: Evaluate how the execution mode (Full 630 GB MSA + PDB Templates vs. Zero-MSA `--msa-free`) influenced evolutionary co-evolutionary constraints and fold accuracy for this target.
6. **Stereochemical & Clash Assessment**: Evaluate `has_clash` and `fraction_disordered` and note any steric or topological considerations.
7. **Biological & Functional Insights**: Connect the structural features of this specific target (`{job_name}`) to its biological mechanism, catalytic state, or therapeutic relevance.
8. **Limitations & Recommended Next Steps**: Concrete follow-up recommendations (e.g., multi-seed ensemble sampling, holo/apo comparison, MD relaxation, FEP, or experimental cryo-EM/X-ray/SPR validation).

IMPORTANT: Begin your response directly with the markdown headings/analysis. Do NOT include conversational preamble."""

        content_parts = [ea_prompt]
        if plddt_png:
            content_parts.append("\n\n**Per-Residue pLDDT Plot:**")
            content_parts.append(types.Part.from_bytes(data=plddt_png, mime_type="image/png"))
        if pae_png:
            content_parts.append("\n\n**Predicted Aligned Error (PAE) Matrix:**")
            content_parts.append(types.Part.from_bytes(data=pae_png, mime_type="image/png"))

        genai_client = genai.Client(vertexai=True, project=project_id, location="global")
        for candidate_model in (
            "gemini-3.1-pro-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        ):
            try:
                resp = genai_client.models.generate_content(
                    model=candidate_model,
                    contents=content_parts,
                    config=types.GenerateContentConfig(temperature=0.7),
                )
                if resp and getattr(resp, "text", None):
                    expert_analysis_obj = {
                        "status": "success",
                        "model": candidate_model,
                        "generated_at": now_ea_iso,
                        "ai_generated": True,
                        "analysis": resp.text.strip() + disclaimer_text,
                    }
                    log.info(
                        f"[AF3 Stage 4] Generated Gemini Expert Analysis via {candidate_model} "
                        f"({len(expert_analysis_obj['analysis'])} chars)."
                    )
                    break
            except Exception as model_exc:
                log.warning(f"[AF3 Stage 4] Gemini model {candidate_model} warning: {model_exc}")
    except Exception as ea_exc:
        log.warning(f"[AF3 Stage 4] Gemini expert analysis fallback used: {ea_exc}")

    base_summary["expert_analysis"] = expert_analysis_obj

    af3_summary_blob_path = f"{job_prefix_clean}/analysis/summary.json"
    bucket.blob(af3_summary_blob_path).upload_from_string(
        json.dumps(base_summary, indent=2), content_type="application/json"
    )
    try:
        stale_zip = bucket.blob(f"{job_prefix_clean}/analysis/artifacts_bundle.zip")
        if stale_zip.exists():
            stale_zip.delete()
    except Exception:
        pass

    kfp_summary = dict(base_summary)
    kfp_summary["job_id"] = pipeline_job_id
    kfp_summary_blob_path = f"{pipeline_root_prefix}/analysis/summary.json"
    bucket.blob(kfp_summary_blob_path).upload_from_string(
        json.dumps(kfp_summary, indent=2), content_type="application/json"
    )
    try:
        stale_kfp_zip = bucket.blob(f"{pipeline_root_prefix}/analysis/artifacts_bundle.zip")
        if stale_kfp_zip.exists():
            stale_kfp_zip.delete()
    except Exception:
        pass

    summary_uri = f"gs://{bucket_name}/{kfp_summary_blob_path}"
    outputs = namedtuple(
        "AF3PredictOutputs", ["cif_uri", "summary_uri", "ranking_score", "mean_plddt"]
    )
    return outputs(cif_uri, summary_uri, float(ranking_score), float(plddt_mean))


# Alias for backwards compatibility
predict_af3_endpoint_task = process_af3_results_task


def create_af3_inference_pipeline():
    """Create the 4-stage KFP v2 pipeline for AlphaFold 3 Endpoint predictions."""

    @dsl.pipeline(
        name="alphafold3-inference-pipeline",
        description=(
            "AlphaFold 3 4-stage structure prediction pipeline on Vertex AI Dedicated H100 Endpoint: "
            "(1) Provision & Queue Endpoint, (2) Run AF3 Inference, (3) Report Endpoint Available, "
            "(4) Process Results & Gemini 3.1 Pro Multimodal Expert Analysis."
        ),
    )
    def af3_inference_pipeline(
        project_id: str,
        region: str,
        endpoint_location: str,
        endpoint_id: str,
        bucket_name: str,
        job_name: str,
        pipeline_job_id: str,
        query_json_path: str,
        pipeline_root: str,
        job_prefix: str,
        msa_free: bool = False,
        num_diffusion_samples: int = 5,
        timeout_seconds: int = 1800,
        idle_shutdown_minutes: int = 20,
    ):
        queue_task = provision_and_queue_af3_endpoint_task(
            project_id=project_id,
            region=region,
            endpoint_location=endpoint_location,
            endpoint_id=endpoint_id,
            bucket_name=bucket_name,
            job_name=job_name,
            pipeline_job_id=pipeline_job_id,
            job_prefix=job_prefix,
            timeout_seconds=timeout_seconds,
        )
        queue_task.set_display_name("1. Provision & Queue AF3 Endpoint")
        queue_task.set_caching_options(False)

        infer_task = run_af3_inference_task(
            project_id=project_id,
            region=region,
            endpoint_location=endpoint_location,
            endpoint_id=endpoint_id,
            bucket_name=bucket_name,
            job_name=job_name,
            pipeline_job_id=pipeline_job_id,
            query_json_path=query_json_path,
            job_prefix=job_prefix,
            slot_blob_path=queue_task.outputs["slot_blob_path"],
            slot_index=queue_task.outputs["slot_index"],
            active_replicas=queue_task.outputs["active_replicas"],
            queue_wait_seconds=queue_task.outputs["queue_wait_seconds"],
            msa_free=msa_free,
            num_diffusion_samples=num_diffusion_samples,
            timeout_seconds=timeout_seconds,
            idle_shutdown_minutes=idle_shutdown_minutes,
        )
        infer_task.set_display_name("2. Run AF3 Inference (H100 Endpoint)")
        infer_task.set_caching_options(False)

        release_task = report_af3_endpoint_available_task(
            project_id=project_id,
            region=region,
            endpoint_location=endpoint_location,
            endpoint_id=endpoint_id,
            bucket_name=bucket_name,
            job_name=job_name,
            pipeline_job_id=pipeline_job_id,
            slot_blob_path=queue_task.outputs["slot_blob_path"],
            slot_index=queue_task.outputs["slot_index"],
            active_replicas=queue_task.outputs["active_replicas"],
            queue_wait_seconds=queue_task.outputs["queue_wait_seconds"],
            inference_seconds=infer_task.outputs["inference_seconds"],
            slot_released=infer_task.outputs["slot_released"],
            idle_shutdown_minutes=idle_shutdown_minutes,
        )
        release_task.set_display_name("3. Report AF3 Endpoint Available")
        release_task.set_caching_options(False)

        process_task = process_af3_results_task(
            project_id=project_id,
            region=region,
            endpoint_location=endpoint_location,
            endpoint_id=endpoint_id,
            bucket_name=bucket_name,
            job_name=job_name,
            pipeline_job_id=pipeline_job_id,
            query_json_path=query_json_path,
            pipeline_root=pipeline_root,
            job_prefix=job_prefix,
            raw_output_gcs_uri=infer_task.outputs["raw_output_gcs_uri"],
            start_iso=infer_task.outputs["start_iso"],
            end_iso=infer_task.outputs["end_iso"],
            queue_wait_seconds=queue_task.outputs["queue_wait_seconds"],
            inference_seconds=infer_task.outputs["inference_seconds"],
            endpoint_status=release_task.outputs["endpoint_status"],
            msa_free=msa_free,
        )
        process_task.set_display_name("4. Process AF3 Results & Expert Analysis")
        process_task.set_caching_options(False)

    return af3_inference_pipeline


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform>=1.50.0",
        "google-cloud-storage>=2.10.0",
        "requests-toolbelt>=1.0.0",
        "pyyaml>=6.0",
    ],
)
def af3_idle_drain_task(
    project_id: str,
    region: str,
    endpoint_location: str,
    endpoint_id: str,
    bucket_name: str,
    idle_shutdown_minutes: int = 20,
) -> str:
    """Monitor the AF3 H100 Endpoint after a job/batch completes and auto-undeploy after idle_shutdown_minutes of inactivity."""
    import json
    import logging
    import time
    from datetime import datetime, timezone

    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import aiplatform, storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("af3_idle_drain")

    if idle_shutdown_minutes < 0:
        log.info("[AF3 Auto-Drain] idle_shutdown_minutes < 0; auto-undeploy disabled.")
        return "disabled"

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    aiplatform.init(project=project_id, location=endpoint_location)
    endpoint = aiplatform.Endpoint(
        endpoint_name=endpoint_id,
        project=project_id,
        location=endpoint_location,
    )
    endpoint_short_id = endpoint.resource_name.split("/")[-1]
    activity_blob_path = f"af3_predictions/.locks/{endpoint_short_id}_activity.json"

    creds, _ = google.auth.default()
    authed = AuthorizedSession(creds)

    def _get_deployed_models(ep):
        try:
            return list(ep.list_models())
        except Exception:
            gca = getattr(ep, "_gca_resource", None) or getattr(ep, "gca_resource", None)
            return list(getattr(gca, "deployed_models", None) or [])

    def _has_active_deploy_op() -> bool:
        try:
            op_url = (
                f"https://{endpoint_location}-aiplatform.googleapis.com/v1/"
                f"{endpoint.resource_name}/operations"
            )
            r = authed.get(op_url, timeout=10)
            if not r.ok:
                return False
            for op in r.json().get("operations", []):
                if not op.get("done") and "DeployModel" in op.get("metadata", {}).get("@type", ""):
                    return True
        except Exception:
            pass
        return False

    def _get_active_af3_jobs() -> list[str]:
        active = []
        try:
            list_url = (
                f"https://{region}-aiplatform.googleapis.com/v1/"
                f"projects/{project_id}/locations/{region}/pipelineJobs?pageSize=50"
            )
            r = authed.get(list_url, timeout=15)
            if r.ok:
                active_states = {
                    "PIPELINE_STATE_PENDING",
                    "PIPELINE_STATE_RUNNING",
                    "PIPELINE_STATE_QUEUED",
                    "PIPELINE_STATE_CANCELLING",
                }
                for pj in r.json().get("pipelineJobs", []):
                    pj_id = pj.get("name", "").split("/")[-1]
                    if pj.get("state") in active_states and pj_id.startswith(
                        "alphafold3-inference-pipeline-"
                    ):
                        active.append(pj_id)
        except Exception:
            pass
        return active

    def _get_active_slot_locks() -> int:
        count = 0
        now_t = time.time()
        try:
            for b in bucket.list_blobs(prefix=f"af3_predictions/.locks/{endpoint_short_id}_slot_"):
                ld = json.loads(b.download_as_text())
                hb = float(
                    ld.get("heartbeat_at")
                    or ld.get("heartbeat_epoch")
                    or ld.get("acquired_at")
                    or 0
                )
                if now_t - hb < 210.0:
                    count += 1
        except Exception:
            pass
        return count

    last_activity_epoch = time.time()
    effective_idle_minutes = int(idle_shutdown_minutes)
    last_log_time = 0.0

    log.info(
        f"[AF3 Auto-Drain] Started idle watchdog for endpoint '{endpoint.resource_name}' "
        f"(grace window: {effective_idle_minutes} minutes)."
    )

    while True:
        now_ts = time.time()
        deployed = _get_deployed_models(endpoint)
        deploying = _has_active_deploy_op()

        if not deployed and not deploying:
            log.info(
                "[AF3 Auto-Drain] Endpoint already has 0 deployed models and no active DeployModel operation. Exiting ($0.00/hr)."
            )
            return "already_undeployed"

        if deploying and not deployed:
            last_activity_epoch = now_ts

        # Check activity file in GCS
        try:
            act_blob = bucket.blob(activity_blob_path)
            if act_blob.exists():
                act_data = json.loads(act_blob.download_as_text())
                file_epoch = float(act_data.get("last_activity_epoch", 0))
                if file_epoch > last_activity_epoch:
                    last_activity_epoch = file_epoch
                if "idle_shutdown_minutes" in act_data:
                    effective_idle_minutes = int(act_data["idle_shutdown_minutes"])
                    if effective_idle_minutes < 0:
                        log.info(
                            "[AF3 Auto-Drain] idle_shutdown_minutes set to < 0 in activity config; exiting."
                        )
                        return "disabled"
        except Exception:
            pass

        # Check if any AF3 prediction jobs or slot locks are active right now
        active_jobs = _get_active_af3_jobs()
        active_slots = _get_active_slot_locks()
        if active_jobs or active_slots > 0:
            last_activity_epoch = now_ts
            if now_ts - last_log_time >= 120.0:
                log.info(
                    f"[AF3 Auto-Drain] {len(active_jobs)} AF3 job(s) ({active_jobs}) / "
                    f"{active_slots} slot(s) currently active; resetting {effective_idle_minutes}m idle timer."
                )
                last_log_time = now_ts
            time.sleep(30)
            continue

        idle_sec = now_ts - last_activity_epoch
        target_sec = max(0, effective_idle_minutes * 60)

        if idle_sec >= target_sec:
            if not _get_active_af3_jobs() and _get_active_slot_locks() == 0:
                log.info(
                    f"[AF3 Auto-Drain] Endpoint '{endpoint.resource_name}' has been idle for "
                    f"{idle_sec / 60:.1f} minutes (threshold: {effective_idle_minutes}m) with 0 active jobs. "
                    "Undeploying all models to revert GPU cost to $0.00/hr..."
                )
                endpoint.undeploy_all(sync=False)
                try:
                    act_blob = bucket.blob(activity_blob_path)
                    act_blob.upload_from_string(
                        json.dumps(
                            {
                                "endpoint_id": endpoint.resource_name,
                                "state": "auto_undeployed",
                                "undeployed_at_iso": datetime.now(timezone.utc)
                                .isoformat()
                                .replace("+00:00", "Z"),
                                "idle_minutes_before_undeploy": round(idle_sec / 60.0, 2),
                                "idle_shutdown_minutes": effective_idle_minutes,
                            },
                            indent=2,
                        ),
                        content_type="application/json",
                    )
                except Exception:
                    pass
                return "auto_undeployed"

        if now_ts - last_log_time >= 120.0:
            log.info(
                f"[AF3 Auto-Drain] Endpoint idle for {idle_sec / 60:.1f} / {effective_idle_minutes} min "
                "(0 active AF3 jobs). Waiting before auto-undeploy..."
            )
            last_log_time = now_ts

        time.sleep(30)


def create_af3_idle_drain_pipeline():
    """Create the KFP v2 pipeline for auto-undeploying the AF3 H100 Endpoint after an idle grace window."""

    @dsl.pipeline(
        name="alphafold3-idle-drain-pipeline",
        description="Auto-undeploys the AlphaFold 3 Dedicated H100 Endpoint after idle_shutdown_minutes of inactivity ($0.00/hr).",
    )
    def af3_idle_drain_pipeline(
        project_id: str,
        region: str,
        endpoint_location: str,
        endpoint_id: str,
        bucket_name: str,
        idle_shutdown_minutes: int = 20,
    ):
        task = af3_idle_drain_task(
            project_id=project_id,
            region=region,
            endpoint_location=endpoint_location,
            endpoint_id=endpoint_id,
            bucket_name=bucket_name,
            idle_shutdown_minutes=idle_shutdown_minutes,
        )
        task.set_display_name("AF3 Endpoint Idle Auto-Undeploy Watchdog")
        task.set_caching_options(False)

    return af3_idle_drain_pipeline
