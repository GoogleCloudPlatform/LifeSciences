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

"""KFP v2 pipeline wrapping synchronous AlphaFold 3 Vertex AI Endpoint predictions.

Why a KFP PipelineJob wrapper for an online Vertex AI Endpoint?
- Full-MSA (`msa_free=False`) AlphaFold 3 predictions run Jackhmmer + Hmmsearch against
  the 630 GB NVMe database bundle plus diffusion sampling on an H100 GPU, taking 4-12 minutes
  per target (and 30-60+ minutes for a multi-target batch).
- Running those synchronous HTTP calls directly inside an agent tool turn blocks the agent
  and risks hitting the Vertex AI Agent Engine (~15-minute) streaming turn timeout.
- By wrapping each AF3 target in a lightweight CPU KFP `PipelineJob` (`python:3.12-slim`),
  `submit_af3_endpoint_prediction` and `submit_af3_batch_predictions` return in ~2 seconds
  with a live Vertex AI Pipelines URL, stream the container execution logs into KFP + GCS,
  and coordinate concurrency across active H100 endpoint replicas via a GCS slot lock.
"""

from typing import NamedTuple

from kfp import dsl


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform>=1.50.0",
        "google-cloud-storage>=2.10.0",
        "google-cloud-logging>=3.5.0",
        "requests-toolbelt>=1.0.0",
        "matplotlib>=3.7.0",
        "numpy>=1.24.0",
    ],
)
def predict_af3_endpoint_task(
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
) -> NamedTuple(
    "AF3PredictOutputs",
    [
        ("cif_uri", str),
        ("summary_uri", str),
        ("ranking_score", float),
        ("mean_plddt", float),
    ],
):
    """Execute a replica-queued synchronous AlphaFold 3 prediction on a Vertex AI Endpoint."""
    import io
    import json
    import logging
    import threading
    import time
    from collections import namedtuple
    from datetime import datetime, timezone

    import numpy as np
    from google.api_core.exceptions import PreconditionFailed
    from google.cloud import aiplatform, storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("af3_kfp_worker")

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    # 1. Load prepared AF3 instance payload from GCS
    if not query_json_path.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI for query_json_path, got: {query_json_path}")
    q_bucket_name, q_blob_path = query_json_path[5:].split("/", 1)
    instance_payload = json.loads(
        storage_client.bucket(q_bucket_name).blob(q_blob_path).download_as_text()
    )

    # 2. Initialize Vertex AI Endpoint and inspect active replica capacity
    aiplatform.init(project=project_id, location=endpoint_location)
    endpoint = aiplatform.Endpoint(
        endpoint_name=endpoint_id,
        project=project_id,
        location=endpoint_location,
    )
    endpoint_short_id = endpoint.resource_name.split("/")[-1]

    def _get_active_replica_count(ep) -> int:
        try:
            models = list(ep.list_models())
        except Exception:
            gca = getattr(ep, "gca_resource", None)
            models = list(getattr(gca, "deployed_models", None) or [])
        total_replicas = 0
        for m in models:
            dr = getattr(m, "dedicated_resources", None)
            min_rep = int(getattr(dr, "min_replica_count", 0) or 0) if dr else 1
            total_replicas += max(1, min_rep)
        return max(1, total_replicas)

    active_replicas = _get_active_replica_count(endpoint)
    log.info(
        f"[AF3 KFP] Target endpoint '{endpoint.resource_name}' has {active_replicas} "
        f"active GPU replica(s). Coordinating replica slot for job '{job_name}'..."
    )

    # 3. Acquire a replica-aware GCS lease slot so batch KFP jobs never stampede a 1-replica H100
    heartbeat_interval_sec = 45.0
    stale_heartbeat_sec = 210.0  # Reap locks if a worker dies / is cancelled (no heartbeat for 3.5m)
    max_lease_sec = float(max(timeout_seconds + 300, 2400))
    acquired_slot_blob = None
    stop_heartbeat = threading.Event()

    def _try_acquire_slot(num_slots: int):
        now = time.time()
        holders = []
        for slot_idx in range(num_slots):
            slot_path = f"af3_predictions/.locks/{endpoint_short_id}_slot_{slot_idx}.json"
            blob = bucket.blob(slot_path)
            payload = {
                "job_name": job_name,
                "pipeline_job_id": pipeline_job_id,
                "slot_index": slot_idx,
                "acquired_at": now,
                "heartbeat_at": now,
                "max_lease_sec": max_lease_sec,
            }
            try:
                blob.upload_from_string(
                    json.dumps(payload, indent=2),
                    content_type="application/json",
                    if_generation_match=0,
                )
                blob.reload()
                return blob, blob.generation, []
            except PreconditionFailed:
                try:
                    blob.reload()
                    existing = json.loads(blob.download_as_text())
                    hb = float(existing.get("heartbeat_at") or existing.get("acquired_at") or 0)
                    acq = float(existing.get("acquired_at") or 0)
                    holder = existing.get("job_name", "unknown")
                    holders.append(f"slot-{slot_idx}:{holder}")
                    if (now - hb) > stale_heartbeat_sec or (now - acq) > max_lease_sec:
                        log.warning(
                            f"[AF3 KFP] Reaping stale lock on {slot_path} held by '{holder}' "
                            f"(last heartbeat {int(now - hb)}s ago)."
                        )
                        blob.delete(if_generation_match=blob.generation)
                except Exception:
                    pass
            except Exception as exc:
                log.warning(f"[AF3 KFP] Unexpected error checking lock {slot_path}: {exc}")
        return None, None, holders

    wait_start = time.time()
    last_wait_log = 0.0
    while True:
        active_replicas = _get_active_replica_count(endpoint)
        acquired_slot_blob, _acquired_generation, current_holders = _try_acquire_slot(
            active_replicas
        )
        if acquired_slot_blob is not None:
            log.info(
                f"[AF3 KFP] Acquired replica slot '{acquired_slot_blob.name}' "
                f"after {time.time() - wait_start:.1f}s queue wait."
            )
            break
        if time.time() - last_wait_log >= 30.0:
            log.info(
                f"[AF3 KFP] Waiting for available H100 replica slot "
                f"({active_replicas} active replica(s), in use by {current_holders})..."
            )
            last_wait_log = time.time()
        time.sleep(5.0)

    def _heartbeat_loop():
        while not stop_heartbeat.wait(heartbeat_interval_sec):
            try:
                acquired_slot_blob.reload()
                data = json.loads(acquired_slot_blob.download_as_text())
                if data.get("job_name") != job_name:
                    break
                data["heartbeat_at"] = time.time()
                acquired_slot_blob.upload_from_string(
                    json.dumps(data, indent=2),
                    content_type="application/json",
                )
            except Exception as hb_exc:
                log.debug(f"[AF3 KFP] Slot heartbeat update warning: {hb_exc}")

    hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb_thread.start()

    try:
        # 4. Dispatch prediction to Vertex AI Endpoint with GCS output_dir
        # Using parameters.output_dir causes the AF3 container to upload all outputs
        # (*_model.cif, *_confidences.json, *_summary_confidences.json) directly to GCS.
        # Critically, for large proteins (>1,000 aa) where Full MSA + inference exceeds
        # Vertex AI Online Prediction's 600s (10-minute) HTTP proxy timeout (HTTP 504),
        # the container subprocess continues running (up to 3,590s) and uploads the
        # completed files to output_dir. Holding the slot lock and polling output_dir on
        # 504 prevents premature slot release and 429 collisions.
        job_prefix_clean = job_prefix.strip("/")
        raw_output_prefix = f"{job_prefix_clean}/af3_raw"
        raw_output_gcs_uri = f"gs://{bucket_name}/{raw_output_prefix}"

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
            "force_output_dir": True,
        }
        start_dt = datetime.now(timezone.utc)
        start_iso = start_dt.isoformat()
        t0 = time.time()
        log.info(
            f"[AF3 KFP] Calling endpoint.predict() for '{job_name}' "
            f"(run_data_pipeline={not msa_free}, num_diffusion_samples={num_diffusion_samples}, "
            f"output_dir={raw_output_gcs_uri}, timeout={timeout_seconds}s)..."
        )

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

        prediction_response = None
        while True:
            try:
                prediction_response = endpoint.predict(
                    instances=[instance_payload],
                    parameters=predict_parameters,
                    timeout=timeout_seconds,
                )
                break
            except Exception as pred_err:
                err_repr = repr(pred_err) + " " + str(pred_err)
                # Case A: Transient DNS or connection reset
                if "NameResolutionError" in err_repr or "ConnectionError" in err_repr:
                    log.warning(
                        f"[AF3 KFP] Transient DNS/ConnectionError ({pred_err}); "
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
                if "Status code:429" in err_repr or ("429" in err_repr and "already working on a prediction" in err_repr):
                    if time.time() - t0 > timeout_seconds:
                        raise
                    log.warning(
                        "[AF3 KFP] Endpoint container reported HTTP 429 (still finishing prior prediction); "
                        "holding slot and retrying in 20s..."
                    )
                    time.sleep(20)
                    continue

                # Case C: Endpoint tenant SA missing GCS permission on output_dir -> auto-grant or fallback
                if use_gcs_output_dir and (
                    "does not have storage.objects" in err_repr
                    or "does not have write access" in err_repr
                ):
                    import re as _re

                    sa_match = _re.search(
                        r"([a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+\.iam\.gserviceaccount\.com)",
                        err_repr,
                    )
                    granted = False
                    if sa_match:
                        tenant_sa = sa_match.group(1)
                        try:
                            policy = bucket.get_iam_policy(requested_policy_version=3)
                            policy.bindings.append(
                                {
                                    "role": "roles/storage.objectAdmin",
                                    "members": {f"serviceAccount:{tenant_sa}"},
                                }
                            )
                            bucket.set_iam_policy(policy)
                            log.info(
                                f"[AF3 KFP] Granted roles/storage.objectAdmin on gs://{bucket_name} to {tenant_sa}; retrying..."
                            )
                            granted = True
                            time.sleep(5)
                        except Exception as iam_exc:
                            log.warning(f"[AF3 KFP] Could not auto-grant GCS IAM to {tenant_sa}: {iam_exc}")
                    if not granted:
                        log.warning(
                            "[AF3 KFP] Falling back to inline response mode (without output_dir)."
                        )
                        use_gcs_output_dir = False
                        predict_parameters.pop("output_dir", None)
                        predict_parameters.pop("force_output_dir", None)
                    continue

                # Case D: Vertex AI HTTP proxy 600s gateway timeout (HTTP 504 / DeadlineExceeded)
                # while container continues executing /run_alphafold and uploading to output_dir
                if use_gcs_output_dir and (
                    "Status code:504" in err_repr
                    or "504" in err_repr
                    or "DeadlineExceeded" in err_repr
                    or "timed out" in err_repr.lower()
                ):
                    log.info(
                        f"[AF3 KFP] Vertex AI HTTP front-end reached 600s gateway timeout after "
                        f"{time.time() - t0:.1f}s while H100 container continues running '{job_name}'. "
                        f"Holding replica slot and polling {raw_output_gcs_uri} for completion..."
                    )
                    poll_deadline = t0 + max(timeout_seconds, 3600)
                    last_poll_log = 0.0
                    while time.time() < poll_deadline:
                        cif_b, conf_b, sum_b = _find_gcs_raw_outputs()
                        if cif_b is not None and sum_b is not None and conf_b is not None:
                            log.info(
                                f"[AF3 KFP] Detected completed AF3 outputs in {raw_output_gcs_uri} "
                                f"after {time.time() - t0:.1f}s total runtime!"
                            )
                            break
                        if time.time() - last_poll_log >= 60.0:
                            log.info(
                                f"[AF3 KFP] Still waiting for H100 container to finish '{job_name}' "
                                f"and upload to {raw_output_gcs_uri} ({ (time.time() - t0) / 60:.1f}m elapsed)..."
                            )
                            last_poll_log = time.time()
                        time.sleep(15)
                    else:
                        raise RuntimeError(
                            f"Timed out waiting for AF3 outputs in {raw_output_gcs_uri} after {time.time() - t0:.1f}s."
                        ) from pred_err
                    break

                raise

        elapsed_sec = time.time() - t0
        end_dt = datetime.now(timezone.utc)
        end_iso = end_dt.isoformat()
        log.info(f"[AF3 KFP] Endpoint prediction for '{job_name}' completed in {elapsed_sec:.1f}s.")

        # 5. Harvest AF3 container execution logs from Cloud Logging for this job window
        execution_log_lines = [
            f"# AlphaFold 3 Endpoint Execution Log — {job_name}",
            f"# Pipeline Job ID: {pipeline_job_id}",
            f"# Endpoint: {endpoint.resource_name}",
            f"# Mode: {'Zero-MSA (--msa-free)' if msa_free else 'Full 630 GB MSA + PDB Templates'}",
            f"# Window: {start_iso} -> {end_iso} ({elapsed_sec:.1f}s)",
            "",
        ]
        try:
            from google.cloud import logging as cloud_logging

            # Allow 3s for trailing container logs to flush into Cloud Logging
            time.sleep(3)
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
                log.info("=== AlphaFold 3 Container Execution Logs ===")
                for line in fetched_lines:
                    log.info(f"  {line}")
                execution_log_lines.extend(fetched_lines)
            else:
                execution_log_lines.append("(No container log entries matched time window.)")
        except Exception as log_exc:
            log.warning(f"[AF3 KFP] Could not fetch container logs from Cloud Logging: {log_exc}")
            execution_log_lines.append(f"(Cloud Logging query skipped: {log_exc})")

        execution_log_text = "\n".join(execution_log_lines) + "\n"

        # 6. Extract predictions from GCS output_dir (or inline prediction_response fallback)
        cif_b, conf_b, sum_b = _find_gcs_raw_outputs() if use_gcs_output_dir else (None, None, None)
        if cif_b is not None and sum_b is not None:
            cif_content = cif_b.download_as_text()
            summary_confidences = dict(json.loads(sum_b.download_as_text()) or {})
            conf_data = json.loads(conf_b.download_as_text()) if conf_b is not None else {}
            atom_plddts = conf_data.get("atom_plddts") or conf_data.get("plddt")
            pae_matrix = conf_data.get("pae")
        else:
            predictions = getattr(prediction_response, "predictions", []) if prediction_response else []
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
            atom_plddts = result_item.get("plddt")
            pae_matrix = result_item.get("pae")

        # Compute ranking score
        ranking_score = summary_confidences.get("ranking_score")
        if ranking_score is None:
            ptm_val = summary_confidences.get("ptm")
            iptm_val = summary_confidences.get("iptm")
            frac_dis = float(summary_confidences.get("fraction_disordered", 0.0) or 0.0)
            has_clash_val = float(summary_confidences.get("has_clash", 0.0) or 0.0)
            if ptm_val is not None:
                eff_iptm = float(iptm_val) if iptm_val is not None else float(ptm_val)
                ranking_score = (
                    0.8 * eff_iptm + 0.2 * float(ptm_val) + 0.5 * frac_dis - 100.0 * has_clash_val
                )
            else:
                ranking_score = 0.0
        ranking_score = round(float(ranking_score), 4)

        # Compute per-residue pLDDT from CIF + atom_plddts
        atom_lines = []
        for line in cif_content.splitlines():
            if line.startswith(("ATOM ", "HETATM ")):
                parts = line.split()
                if len(parts) >= 15:
                    chain_id = parts[6]
                    res_seq = parts[8]
                    try:
                        bfac = float(parts[14])
                    except (ValueError, IndexError):
                        bfac = None
                    atom_lines.append((chain_id, res_seq, bfac))

        res_plddts = []
        if atom_lines:
            res_scores = {}
            res_order = []
            use_atom_list = bool(atom_plddts) and len(atom_plddts) == len(atom_lines)
            for idx, (chain_id, res_seq, bfac) in enumerate(atom_lines):
                score = float(atom_plddts[idx]) if use_atom_list else bfac
                if score is None:
                    continue
                key = (chain_id, res_seq)
                if key not in res_scores:
                    res_scores[key] = []
                    res_order.append(key)
                res_scores[key].append(score)
            res_plddts = [float(np.mean(res_scores[k])) for k in res_order]
        elif atom_plddts:
            res_plddts = [float(x) for x in atom_plddts]

        if res_plddts:
            plddt_arr = np.array(res_plddts, dtype=float)
            plddt_mean = round(float(np.mean(plddt_arr)), 2)
            plddt_median = round(float(np.median(plddt_arr)), 2)
            plddt_min = round(float(np.min(plddt_arr)), 2)
            plddt_max = round(float(np.max(plddt_arr)), 2)
            plddt_dist = {
                "very_low_confidence": int(np.sum(plddt_arr < 50)),
                "low_confidence": int(np.sum((plddt_arr >= 50) & (plddt_arr < 70))),
                "high_confidence": int(np.sum((plddt_arr >= 70) & (plddt_arr < 90))),
                "very_high_confidence": int(np.sum(plddt_arr >= 90)),
            }
        else:
            raw_mean = summary_confidences.get("mean_plddt")
            plddt_mean = round(float(raw_mean), 2) if raw_mean is not None else 0.0
            plddt_median = plddt_mean
            plddt_min = plddt_mean
            plddt_max = plddt_mean
            plddt_dist = {
                "very_low_confidence": 0,
                "low_confidence": 0,
                "high_confidence": 0,
                "very_high_confidence": 0,
            }

        if pae_matrix and len(pae_matrix) > 0:
            pae_arr = np.array(pae_matrix, dtype=float)
            pae_mean = round(float(np.mean(pae_arr)), 2)
            pae_median = round(float(np.median(pae_arr)), 2)
            pae_min = round(float(np.min(pae_arr)), 2)
            pae_max = round(float(np.max(pae_arr)), 2)
        else:
            pae_mean = summary_confidences.get("mean_pae")
            pae_median = pae_mean
            pae_min = pae_mean
            pae_max = pae_mean

        summary_confidences["ranking_score"] = ranking_score
        summary_confidences["mean_plddt"] = plddt_mean
        if pae_mean is not None:
            summary_confidences["mean_pae"] = pae_mean

        # Render pLDDT and PAE PNG plots
        mode_label = "Zero-MSA (--msa-free)" if msa_free else "Full MSA + Templates"
        title_suffix = f"{job_name} ({mode_label})"
        plddt_png = None
        pae_png = None
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            if res_plddts:
                fig, ax = plt.subplots(figsize=(8, 3.6), dpi=140)
                xs = np.arange(1, len(res_plddts) + 1)
                ax.axhspan(90, 100, color="#0053D6", alpha=0.12, label="Very High (>90)")
                ax.axhspan(70, 90, color="#65CBF3", alpha=0.14, label="Confident (70-90)")
                ax.axhspan(50, 70, color="#FFDB13", alpha=0.14, label="Low (50-70)")
                ax.axhspan(0, 50, color="#FF7D45", alpha=0.12, label="Very Low (<50)")
                ax.plot(xs, res_plddts, color="#0053D6", linewidth=2.0)
                ax.set_xlim(1, max(len(res_plddts), 2))
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
                plddt_png = buf.getvalue()

            if pae_matrix and len(pae_matrix) > 0:
                fig2, ax2 = plt.subplots(figsize=(5.2, 4.6), dpi=140)
                pae_arr = np.array(pae_matrix, dtype=float)
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
                pae_png = buf2.getvalue()
        except Exception as plot_exc:
            log.warning(f"[AF3 KFP] Plot generation warning: {plot_exc}")

        # 7. Upload artifacts to both canonical af3_predictions/<job_name>/ and KFP pipeline_root
        job_prefix_clean = job_prefix.strip("/")
        pipeline_root_prefix = (
            pipeline_root[5:].split("/", 1)[1].strip("/")
            if pipeline_root.startswith("gs://") and "/" in pipeline_root[5:]
            else f"pipeline_runs/{pipeline_job_id}"
        )

        cif_filename = f"{job_name}_model.cif"
        conf_filename = f"{job_name}_summary_confidences.json"
        cif_uri = f"gs://{bucket_name}/{job_prefix_clean}/{cif_filename}"

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

        plots_dict = {}
        if plddt_png:
            plddt_rel = f"{job_prefix_clean}/analysis/plddt_plot_0.png"
            bucket.blob(plddt_rel).upload_from_string(plddt_png, content_type="image/png")
            bucket.blob(f"{pipeline_root_prefix}/analysis/plddt_plot_0.png").upload_from_string(
                plddt_png, content_type="image/png"
            )
            plots_dict["plddt_plot"] = f"gs://{bucket_name}/{plddt_rel}"
        if pae_png:
            pae_rel = f"{job_prefix_clean}/analysis/pae_plot_0.png"
            bucket.blob(pae_rel).upload_from_string(pae_png, content_type="image/png")
            bucket.blob(f"{pipeline_root_prefix}/analysis/pae_plot_0.png").upload_from_string(
                pae_png, content_type="image/png"
            )
            plots_dict["pae_plot"] = f"gs://{bucket_name}/{pae_rel}"

        if plddt_mean >= 90:
            quality_assessment = "very_high_confidence"
        elif plddt_mean >= 70:
            quality_assessment = "high_confidence"
        elif plddt_mean >= 50:
            quality_assessment = "low_confidence"
        else:
            quality_assessment = "very_low_confidence"

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

        ptm = summary_confidences.get("ptm")
        iptm = summary_confidences.get("iptm")
        has_clash = float(summary_confidences.get("has_clash", 0.0) or 0.0)
        fraction_disordered = float(summary_confidences.get("fraction_disordered", 0.0) or 0.0)
        chain_ptm = summary_confidences.get("chain_ptm")
        chain_iptm = summary_confidences.get("chain_iptm")
        chain_pair_iptm = summary_confidences.get("chain_pair_iptm")
        chain_pair_pae_min = summary_confidences.get("chain_pair_pae_min")

        pred_entry = {
            "rank": 1,
            "sample_name": f"seed_{instance_payload.get('modelSeeds', [1])[0]}_sample_0 ({mode_label})",
            "model_name": f"AlphaFold 3 ({mode_label})",
            "cif_uri": cif_uri,
            "uri": cif_uri,
            "ranking_score": ranking_score,
            "ranking_confidence": ranking_score,
            "ptm": ptm,
            "iptm": iptm,
            "plddt_mean": plddt_mean,
            "plddt_median": plddt_median,
            "plddt_min": plddt_min,
            "plddt_max": plddt_max,
            "plddt_distribution": plddt_dist,
            "plddt_scores": [round(x, 2) for x in res_plddts[:2000]],
            "pae_mean": pae_mean,
            "pae_median": pae_median,
            "pae_min": pae_min,
            "pae_max": pae_max,
            "has_pae": pae_mean is not None,
            "has_pde": False,
            "has_clash": has_clash,
            "fraction_disordered": fraction_disordered,
            "chain_ptm": chain_ptm,
            "chain_iptm": chain_iptm,
            "chain_pair_iptm": chain_pair_iptm,
            "chain_pair_pae_min": chain_pair_pae_min,
            "quality_assessment": quality_assessment,
            "plots": plots_dict,
        }

        elapsed_note = f" in **{elapsed_sec:.1f}s**" if elapsed_sec else ""
        expert_md_lines = [
            f"### AlphaFold 3 Structure Prediction Summary (`{job_name}`)",
            "",
            f"- **Execution Mode**: **{mode_label}** (`run_data_pipeline={not msa_free}`){elapsed_note} on Vertex AI Dedicated Endpoint (`NVIDIA_H100_80GB` via KFP PipelineJob `{pipeline_job_id}`).",
            f"- **Overall Quality**: **{quality_assessment.replace('_', ' ').title()}** "
            f"(Mean pLDDT: **{plddt_mean:.2f}**, Ranking Score: **{ranking_score:.4f}**, "
            f"pTM: **{ptm if ptm is not None else 'N/A'}**"
            + (f", ipTM: **{iptm}**" if iptm is not None else "")
            + (f", Mean PAE: **{pae_mean:.2f} Å**" if pae_mean is not None else "")
            + ").",
            f"- **Clash & Disorder Check**: `has_clash = {has_clash}`, "
            f"`fraction_disordered = {fraction_disordered:.2f}`.",
            "",
        ]
        if msa_free:
            expert_md_lines.extend(
                [
                    "#### Mode Comparison Note",
                    "This prediction ran in **Zero-MSA (`--msa-free`)** mode (`unpairedMsa: \"\"`), which skips Jackhmmer/Hmmsearch genetic database alignments for rapid turnaround (~50s). "
                    "Without evolutionary co-evolutionary constraints, confidence is concentrated in local secondary structure elements (`mean pLDDT = "
                    f"{plddt_mean:.1f}`). Running with **Full 630 GB MSA + PDB Templates (`msa_free=False`)** incorporates deep evolutionary alignments from local NVMe SSD to substantially increase global fold (`pTM`) and per-residue (`pLDDT`) accuracy.",
                ]
            )
        else:
            expert_md_lines.extend(
                [
                    "#### Full MSA + Template Alignment Analysis",
                    "This prediction ran with the **full 630 GB AlphaFold 3 genetic database bundle** (UniRef90, MGnify, BFD-small, UniProt, PDB seqres + mmCIF templates, RNACentral, Rfam) mounted on local NVMe SSD (`a3-highgpu-1g`). "
                    f"Deep evolutionary alignments yielded **{plddt_dist['very_high_confidence']}** very-high-confidence residues (`pLDDT > 90`) and **{plddt_dist['high_confidence']}** confident residues (`70–90`).",
                ]
            )

        duration_formatted = (
            f"{elapsed_sec:.1f}s"
            if elapsed_sec < 60
            else f"{elapsed_sec / 60:.1f}m"
        )
        base_summary = {
            "job_id": job_name,
            "pipeline_job_id": pipeline_job_id,
            "query_name": job_name,
            "af3_job_dir": f"gs://{bucket_name}/{job_prefix_clean}",
            "execution_log_uri": f"gs://{bucket_name}/{job_prefix_clean}/execution.log",
            "model_type": "alphafold3",
            "analyzed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "total_predictions": 1,
            "successful_analyses": 1,
            "failed_analyses": 0,
            "summary": {
                "protein_info": {
                    "total_length": total_residues,
                    "num_chains": len(chain_composition) or 1,
                    "chain_composition": chain_composition,
                    "input_query_json": instance_payload,
                    "job_metadata": {
                        "display_name": job_name,
                        "pipeline_job_id": pipeline_job_id,
                        "state": "PIPELINE_STATE_SUCCEEDED",
                        "started": start_iso,
                        "completed": end_iso,
                        "duration_seconds": round(elapsed_sec, 2),
                        "duration_formatted": duration_formatted,
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
                "plddt_stats": {
                    "mean_across_models": plddt_mean,
                    "std_across_models": 0.0,
                    "min_model_mean": plddt_mean,
                    "max_model_mean": plddt_mean,
                    "range": 0.0,
                },
                "pae_stats": {
                    "mean_across_models": pae_mean,
                    "min_model_mean": pae_mean,
                    "max_model_mean": pae_mean,
                }
                if pae_mean is not None
                else {},
                "recommendations": [
                    f"Best structure: {pred_entry['sample_name']} (pLDDT: {plddt_mean:.2f}, pTM: {ptm}, Ranking Score: {ranking_score:.4f})",
                ],
            },
            "best_prediction": pred_entry,
            "top_predictions": [pred_entry],
            "all_predictions_summary": [pred_entry],
            "expert_analysis": {
                "status": "complete",
                "model": "alphafold3-metrics-synthesizer",
                "analysis": "\n".join(expert_md_lines),
            },
        }

        # Upload summary.json to canonical af3_predictions/<job_name>/analysis/summary.json
        af3_summary_blob_path = f"{job_prefix_clean}/analysis/summary.json"
        bucket.blob(af3_summary_blob_path).upload_from_string(
            json.dumps(base_summary, indent=2), content_type="application/json"
        )

        # Also upload summary.json to KFP pipeline_root/analysis/summary.json
        kfp_summary = dict(base_summary)
        kfp_summary["job_id"] = pipeline_job_id
        kfp_summary_blob_path = f"{pipeline_root_prefix}/analysis/summary.json"
        bucket.blob(kfp_summary_blob_path).upload_from_string(
            json.dumps(kfp_summary, indent=2), content_type="application/json"
        )

        summary_uri = f"gs://{bucket_name}/{kfp_summary_blob_path}"
        outputs = namedtuple(
            "AF3PredictOutputs", ["cif_uri", "summary_uri", "ranking_score", "mean_plddt"]
        )
        return outputs(cif_uri, summary_uri, float(ranking_score), float(plddt_mean))

    finally:
        stop_heartbeat.set()
        if acquired_slot_blob is not None:
            try:
                acquired_slot_blob.reload()
                lock_data = json.loads(acquired_slot_blob.download_as_text())
                if lock_data.get("job_name") == job_name:
                    acquired_slot_blob.delete()
                    log.info(f"[AF3 KFP] Released replica slot '{acquired_slot_blob.name}'.")
            except Exception as rel_exc:
                log.warning(f"[AF3 KFP] Slot release warning: {rel_exc}")

        # Stamp endpoint activity record and check if we should trigger / refresh idle auto-drain
        try:
            now_ts = time.time()
            now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            activity_blob = bucket.blob(
                f"af3_predictions/.locks/{endpoint_short_id}_activity.json"
            )
            activity_payload = {
                "endpoint_id": endpoint.resource_name,
                "last_activity_epoch": now_ts,
                "last_activity_iso": now_iso,
                "last_event": f"completed:{job_name}",
                "last_pipeline_job_id": pipeline_job_id,
                "idle_shutdown_minutes": int(idle_shutdown_minutes),
            }
            activity_blob.upload_from_string(
                json.dumps(activity_payload, indent=2), content_type="application/json"
            )

            if idle_shutdown_minutes >= 0:
                import google.auth
                from google.auth.transport.requests import AuthorizedSession

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
                other_af3_jobs = []
                active_drain_jobs = []
                for pj in pjobs:
                    pj_id = pj.get("name", "").split("/")[-1]
                    st = pj.get("state", "")
                    if st not in active_states or pj_id == pipeline_job_id:
                        continue
                    if pj_id.startswith("alphafold3-inference-pipeline-"):
                        other_af3_jobs.append(pj_id)
                    elif pj_id.startswith("alphafold3-idle-drain-"):
                        active_drain_jobs.append(pj_id)

                # Also check if any slot lock is still held
                active_slots = 0
                for b in bucket.list_blobs(
                    prefix=f"af3_predictions/.locks/{endpoint_short_id}_slot_"
                ):
                    try:
                        ld = json.loads(b.download_as_text())
                        if now_ts - float(ld.get("heartbeat_epoch", 0)) < stale_heartbeat_sec:
                            active_slots += 1
                    except Exception:
                        pass

                if other_af3_jobs or active_slots > 0:
                    log.info(
                        f"[AF3 Auto-Drain] {len(other_af3_jobs)} other AF3 pipeline job(s) "
                        f"and {active_slots} slot lock(s) still active; deferring endpoint "
                        "idle-drain to the last job in the queue."
                    )
                elif idle_shutdown_minutes == 0:
                    log.info(
                        "[AF3 Auto-Drain] Queue is empty and idle_shutdown_minutes=0 -> "
                        "undeploying H100 endpoint immediately ($0.00/hr)."
                    )
                    endpoint.undeploy_all(sync=False)
                    activity_payload["state"] = "auto_undeployed"
                    activity_blob.upload_from_string(
                        json.dumps(activity_payload, indent=2), content_type="application/json"
                    )
                elif active_drain_jobs:
                    log.info(
                        f"[AF3 Auto-Drain] Existing idle-drain watchdog '{active_drain_jobs[0]}' "
                        f"is active; refreshed activity timestamp to reset its {idle_shutdown_minutes}m countdown."
                    )
                else:
                    drain_template_uri = (
                        f"gs://{bucket_name}/af3_predictions/.locks/af3_idle_drain_pipeline.json"
                    )
                    if bucket.blob(
                        "af3_predictions/.locks/af3_idle_drain_pipeline.json"
                    ).exists():
                        drain_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                        drain_job_id = f"alphafold3-idle-drain-{drain_ts}"
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
                        drain_job.submit(
                            service_account=f"pipelines-sa@{project_id}.iam.gserviceaccount.com"
                        )
                        log.info(
                            f"[AF3 Auto-Drain] Queue empty -> launched {idle_shutdown_minutes}m "
                            f"idle-drain watchdog '{drain_job_id}'."
                        )
        except Exception as drain_exc:
            log.warning(f"[AF3 Auto-Drain] Post-job idle drain check warning: {drain_exc}")



def create_af3_inference_pipeline():
    """Create the KFP v2 pipeline for AlphaFold 3 Endpoint predictions."""

    @dsl.pipeline(
        name="alphafold3-inference-pipeline",
        description="AlphaFold 3 structure prediction on Vertex AI Dedicated H100 Endpoint with replica-aware queueing and idle auto-drain.",
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
        task = predict_af3_endpoint_task(
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
            msa_free=msa_free,
            num_diffusion_samples=num_diffusion_samples,
            timeout_seconds=timeout_seconds,
            idle_shutdown_minutes=idle_shutdown_minutes,
        )
        task.set_display_name("AF3 Endpoint Predict (H100)")
        task.set_caching_options(False)

    return af3_inference_pipeline


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform>=1.50.0",
        "google-cloud-storage>=2.10.0",
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
            gca = getattr(ep, "_gca_resource", None)
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
                    if (
                        pj.get("state") in active_states
                        and pj_id.startswith("alphafold3-inference-pipeline-")
                    ):
                        active.append(pj_id)
        except Exception:
            pass
        return active

    def _get_active_slot_locks() -> int:
        count = 0
        now_t = time.time()
        try:
            for b in bucket.list_blobs(
                prefix=f"af3_predictions/.locks/{endpoint_short_id}_slot_"
            ):
                ld = json.loads(b.download_as_text())
                if now_t - float(ld.get("heartbeat_epoch", 0)) < 210.0:
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
            # Reset idle timer while initial H100 + 630 GB NVMe unpack is still provisioning
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
            # Final double-check before undeploying
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

