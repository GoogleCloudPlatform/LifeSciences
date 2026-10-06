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

"""Tool for submitting AlphaFold 3 predictions to Agent Platform Prediction Endpoint."""

import copy
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from google.api_core.exceptions import DeadlineExceeded, GoogleAPICallError
from google.cloud import aiplatform as vertex_ai

from ..base import MODEL_CIF_FILENAME, SUMMARY_CONFIDENCES_FILENAME, AF3Tool
from ..utils.input_converter import (
    count_af3_tokens,
    fasta_to_af3_json,
    is_af3_json,
    validate_af3_json,
)
from ..utils.metrics import compute_ranking_score
from ..utils.viewer_artifacts import build_and_upload_af3_viewer_artifacts

logger = logging.getLogger(__name__)

_COMPILE_LOCK = threading.Lock()
_LAST_PIPELINE_TS = 0


def _allocate_unique_pipeline_timestamps() -> tuple[str, str]:
    """Allocate a strictly unique (YYYYMMDD_HHMMSS, YYYYMMDDHHMMSS) timestamp pair for KFP runs.

    Prevents collisions in `pipeline_runs/YYYYMMDD_HHMMSS` and `job_id` when multiple AF3 jobs
    are submitted within the same second (e.g., via `submit_af3_batch_predictions`).
    """
    global _LAST_PIPELINE_TS
    with _COMPILE_LOCK:
        now_epoch = int(time.time())
        if now_epoch <= _LAST_PIPELINE_TS:
            now_epoch = _LAST_PIPELINE_TS + 1
        _LAST_PIPELINE_TS = now_epoch
        dt = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
        return dt.strftime("%Y%m%d_%H%M%S"), dt.strftime("%Y%m%d%H%M%S")


def _prepare_af3_instance_for_endpoint(
    af3_query: dict[str, Any],
    job_name: str,
    model_seeds: list[int],
    msa_free: bool,
) -> dict[str, Any]:
    """Prepare an AF3 query dictionary for the Vertex AI Model Garden AlphaFold 3 container.

    The Model Garden `google/alphafold3` serving container parses each instance via
    `alphafold3.common.folding_input.Input.from_json()`, which rejects top-level keys
    outside the official DeepMind AF3 schema (such as `msaFree`).
    - When `msa_free=True` (`run_data_pipeline=False`), protein chains must include
      `unpairedMsa: ""`, `pairedMsa: ""`, and `templates: []` so AF3 skips Jackhmmer.
    - When `msa_free=False` (`run_data_pipeline=True`), empty default MSA strings must
      be omitted so AF3 runs Jackhmmer + Hmmsearch + template search against the
      local 630 GB MSA bundle.
    """
    payload = copy.deepcopy(af3_query)
    payload.pop("msaFree", None)
    payload["name"] = job_name
    payload["modelSeeds"] = model_seeds
    payload.setdefault("dialect", "alphafold3")
    payload.setdefault("version", 1)

    for seq_entry in payload.get("sequences", []):
        if "protein" in seq_entry and isinstance(seq_entry["protein"], dict):
            prot = seq_entry["protein"]
            if msa_free:
                prot.setdefault("unpairedMsa", "")
                prot.setdefault("pairedMsa", "")
                prot.setdefault("templates", [])
            else:
                if prot.get("unpairedMsa") == "":
                    prot.pop("unpairedMsa", None)
                if prot.get("pairedMsa") == "":
                    prot.pop("pairedMsa", None)
                if prot.get("templates") == []:
                    prot.pop("templates", None)
        elif "rna" in seq_entry and isinstance(seq_entry["rna"], dict):
            rna = seq_entry["rna"]
            if msa_free:
                rna.setdefault("unpairedMsa", "")
            elif rna.get("unpairedMsa") == "":
                rna.pop("unpairedMsa", None)

    return payload


def _count_af3_chains(af3_query: dict[str, Any]) -> int:
    """Count total chains/entities across all sequences in an AF3 query."""
    total = 0
    for entry in af3_query.get("sequences", []):
        for key in ("protein", "rna", "dna", "ligand", "ion"):
            if key in entry and isinstance(entry[key], dict):
                chain_id = entry[key].get("id", "A")
                if isinstance(chain_id, list):
                    total += len(chain_id)
                else:
                    total += 1
    return max(1, total)


class AF3SubmitPredictionTool(AF3Tool):
    """Tool for submitting all-atom complex predictions via AlphaFold 3 Agent Platform Endpoint."""

    def _validate_gcs_bucket(self, bucket_name: str) -> None:
        """Ensure GCS bucket matches configured bucket or explicit AF3_ALLOWED_BUCKETS."""
        allowed_buckets = {self.config.bucket_name}
        extra = os.environ.get("AF3_ALLOWED_BUCKETS", "")
        if extra:
            allowed_buckets.update(b.strip() for b in extra.split(",") if b.strip())
        if bucket_name not in allowed_buckets:
            raise PermissionError(
                f"Access denied: GCS bucket '{bucket_name}' is not in allowed buckets "
                f"{sorted(allowed_buckets)}."
            )

    def _submit_kfp_pipeline(
        self,
        *,
        endpoint: Any,
        instance_payload: dict[str, Any],
        job_name: str,
        bucket_name: str,
        job_prefix: str,
        msa_free: bool,
        model_seeds: list[int],
        num_diffusion_samples: int,
        total_tokens: int,
        num_chains: int,
        collected_warnings: list[str],
    ) -> dict[str, Any]:
        """Submit an asynchronous KFP PipelineJob that wraps the synchronous AF3 endpoint call."""
        deployed_models = self.get_deployed_models(endpoint)
        active_ops = self.get_active_deploy_operations(endpoint)
        if not deployed_models and not active_ops:
            return {
                "status": "error",
                "job_id": job_name,
                "message": (
                    "AlphaFold 3 Endpoint currently has 0 deployed models (paused at $0.00/hr). "
                    "Please call `deploy_af3_endpoint` first and wait for it to reach READY before submitting predictions."
                ),
            }

        active_replicas = 0
        for dm in deployed_models:
            dr = getattr(dm, "dedicated_resources", None)
            min_rep = int(getattr(dr, "min_replica_count", 0) or 0) if dr else 1
            active_replicas += max(1, min_rep)
        active_replicas = max(1, active_replicas)

        bucket = self.storage_client.bucket(bucket_name)
        input_blob_path = f"{job_prefix}/input.json"
        bucket.blob(input_blob_path).upload_from_string(
            json.dumps(instance_payload, indent=2), content_type="application/json"
        )
        gcs_query_path = f"gs://{bucket_name}/{input_blob_path}"

        ts_dir, ts_id = _allocate_unique_pipeline_timestamps()
        pipeline_job_id = f"alphafold3-inference-pipeline-{ts_id}"
        pipeline_root = f"gs://{bucket_name}/pipeline_runs/{ts_dir}"

        with _COMPILE_LOCK:
            from kfp import compiler

            from ..pipeline import create_af3_inference_pipeline

            pipeline_func = create_af3_inference_pipeline()
            fd, pipeline_path = tempfile.mkstemp(
                suffix=".json", prefix=f"af3_pipeline_{job_name}_"
            )
            os.close(fd)
            compiler.Compiler().compile(
                pipeline_func=pipeline_func,
                package_path=pipeline_path,
            )

        labels = {
            "model_type": "alphafold3",
            "job_type": "monomer" if num_chains <= 1 else "complex",
            "query_name": self._clean_label(job_name),
            "num_tokens": str(total_tokens),
            "num_chains": str(num_chains),
            "num_seeds": str(len(model_seeds)),
            "gpu_type": "h100-80gb",
            "msa_method": "none" if msa_free else "jackhmmer",
            "submitted_by": "foldrun-agent",
        }

        try:
            pipeline_job = vertex_ai.PipelineJob(
                display_name=job_name,
                job_id=pipeline_job_id,
                template_path=pipeline_path,
                pipeline_root=pipeline_root,
                parameter_values={
                    "project_id": self.config.project_id,
                    "region": self.config.region,
                    "endpoint_location": self.config.endpoint_location,
                    "endpoint_id": endpoint.resource_name,
                    "bucket_name": bucket_name,
                    "job_name": job_name,
                    "pipeline_job_id": pipeline_job_id,
                    "query_json_path": gcs_query_path,
                    "pipeline_root": pipeline_root,
                    "job_prefix": job_prefix,
                    "msa_free": msa_free,
                    "num_diffusion_samples": num_diffusion_samples,
                    "timeout_seconds": self.config.timeout_seconds,
                },
                enable_caching=False,
                labels=labels,
                project=self.config.project_id,
                location=self.config.region,
            )
            pipeline_job.submit(service_account=self.config.pipelines_sa_email)
        finally:
            if os.path.exists(pipeline_path):
                try:
                    os.remove(pipeline_path)
                except OSError:
                    pass

        resource_name = getattr(pipeline_job, "resource_name", "") or pipeline_job_id
        run_id = resource_name.split("/")[-1] if "/" in resource_name else pipeline_job_id
        console_url = (
            f"https://console.cloud.google.com/vertex-ai/pipelines/locations/{self.config.region}"
            f"/runs/{run_id}?project={self.config.project_id}"
        )
        viewer_url = (
            f"{self.config.viewer_url}/job/{run_id}"
            if self.config.viewer_url
            else self.gcs_console_url(f"gs://{bucket_name}/{job_prefix}")
        )

        return {
            "status": "submitted",
            "job_id": run_id,
            "pipeline_job_resource": resource_name,
            "job_name": job_name,
            "query_name": job_name,
            "model": "AlphaFold 3",
            "mode": "msa-free" if msa_free else "standard",
            "execution_mode": "kfp_pipeline",
            "total_tokens": total_tokens,
            "num_chains": num_chains,
            "endpoint": endpoint.resource_name,
            "active_replicas": active_replicas,
            "gcs_output_dir": f"gs://{bucket_name}/{job_prefix}",
            "pipeline_root": pipeline_root,
            "console_url": console_url,
            "endpoint_console_url": self.get_endpoint_console_url(endpoint),
            "endpoint_logs_url": self.get_endpoint_logs_url(endpoint, job_name=job_name),
            "viewer_url": viewer_url,
            "warnings": collected_warnings,
            "message": (
                f"Submitted AlphaFold 3 KFP pipeline job '{job_name}' (run ID: '{run_id}', "
                f"mode={'Zero-MSA' if msa_free else 'Full 630 GB MSA + Templates'}). "
                f"The KFP worker coordinates replica slots ({active_replicas} active H100 replica(s)), "
                f"executes the synchronous endpoint prediction, streams container logs into KFP + GCS, "
                f"and generates FoldRun Viewer artifacts. Monitor progress via `check_job_status(job_id='{run_id}')`."
            ),
        }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Submit an AlphaFold 3 prediction job.

        Args:
            arguments: {
                'input': FASTA content, AF3 JSON content, or GCS/file path,
                'job_name': Optional job name (defaults to 'af3_YYYYMMDD_HHMMSS'),
                'msa_free': Zero-MSA screening mode (default: False),
                'model_seeds': Random seeds for diffusion sampling (default: [1]),
                'num_diffusion_samples': Diffusion samples per seed (default: 5),
                'endpoint_id': Optional Agent Platform Endpoint ID override,
                'output_gcs_uri': Optional custom GCS destination URI,
                'sync': If False, submits a KFP PipelineJob and returns immediately;
                    if True, calls endpoint.predict() inline.
            }

        Returns:
            Dictionary containing prediction status, metrics/URLs, warnings, and GCS artifact URIs.
        """
        input_data = arguments.get("input")
        if not input_data:
            return {
                "status": "error",
                "message": "Missing required argument 'input' (must provide FASTA or AF3 JSON).",
            }

        job_name = (
            arguments.get("job_name")
            or f"af3_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )
        try:
            job_name = self.validate_job_name(str(job_name))
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

        msa_free = bool(arguments.get("msa_free", self.config.default_msa_free))
        model_seeds = arguments.get("model_seeds") or [1]
        if isinstance(model_seeds, int):
            model_seeds = [model_seeds]
        num_diffusion_samples = int(arguments.get("num_diffusion_samples", 5))
        sync = bool(arguments.get("sync", True))

        endpoint_id = arguments.get("endpoint_id")
        collected_warnings: list[str] = []

        # 1. Resolve content from GCS, local file, or direct string
        try:
            if isinstance(input_data, str) and input_data.startswith("gs://"):
                parsed_gcs = urlparse(input_data)
                if (
                    parsed_gcs.scheme != "gs"
                    or not parsed_gcs.netloc
                    or not parsed_gcs.path.lstrip("/")
                ):
                    raise ValueError(
                        f"Invalid GCS URI: '{input_data}'. Expected format 'gs://<bucket>/<object>'."
                    )
                bucket_name = parsed_gcs.netloc
                blob_path = parsed_gcs.path.lstrip("/")
                if ".." in blob_path.split("/"):
                    raise ValueError(
                        f"Invalid GCS path in '{input_data}': path traversal segments ('..') are not allowed."
                    )
                self._validate_gcs_bucket(bucket_name)
                bucket = self.storage_client.bucket(bucket_name)
                content = bucket.blob(blob_path).download_as_text()
            elif isinstance(input_data, str) and (
                input_data.strip().startswith("{") or input_data.strip().startswith(">")
            ):
                content = input_data
            elif isinstance(input_data, str) and (
                input_data.startswith("/")
                or input_data.startswith("./")
                or input_data.startswith("../")
                or os.path.exists(input_data)
            ):
                allowed_dirs = [
                    Path(os.environ.get("FOLDRUN_WORKSPACE_DIR", os.getcwd())).resolve(),
                    Path("/tmp").resolve(),
                    Path("/private/tmp").resolve(),
                ]
                target_path = Path(input_data).resolve()
                is_allowed = any(
                    target_path == allowed_dir or allowed_dir in target_path.parents
                    for allowed_dir in allowed_dirs
                )
                if not is_allowed:
                    raise PermissionError(
                        f"Access denied: Path '{input_data}' escapes allowed workspace directory."
                    )
                if not target_path.is_file():
                    raise FileNotFoundError(f"Input file not found: '{input_data}'")
                with open(target_path, encoding="utf-8") as f:
                    content = f.read()
            else:
                content = input_data

            # 2. Parse or convert to AF3 JSON
            if is_af3_json(content):
                is_valid, errors, warns = validate_af3_json(content)
                collected_warnings.extend(warns)
                if not is_valid:
                    return {
                        "status": "error",
                        "message": f"Invalid AF3 JSON input: {'; '.join(errors)}",
                    }
                af3_query = json.loads(content) if isinstance(content, str) else dict(content)
                af3_query["name"] = job_name
                af3_query["modelSeeds"] = model_seeds
            else:
                af3_query = fasta_to_af3_json(
                    fasta_content=content,
                    job_name=job_name,
                    model_seeds=model_seeds,
                    msa_free=msa_free,
                    warnings_out=collected_warnings,
                )
                is_valid, errors, warns = validate_af3_json(af3_query)
                collected_warnings.extend(warns)
                if not is_valid:
                    return {
                        "status": "error",
                        "message": f"Failed to construct valid AF3 input: {'; '.join(errors)}",
                    }

            total_tokens = count_af3_tokens(af3_query)
            num_chains = _count_af3_chains(af3_query)

            # Resolve destination GCS bucket and prefix
            output_gcs_uri = arguments.get("output_gcs_uri")
            if output_gcs_uri:
                parsed_out = urlparse(output_gcs_uri)
                if parsed_out.scheme != "gs" or not parsed_out.netloc:
                    raise ValueError(
                        f"Invalid output GCS URI: '{output_gcs_uri}'. Must follow format 'gs://bucket-name/[prefix]'."
                    )
                out_bucket_name = parsed_out.netloc
                self._validate_gcs_bucket(out_bucket_name)
                out_path = parsed_out.path.strip("/")
                if ".." in out_path.split("/"):
                    raise ValueError(
                        f"Invalid output GCS path in '{output_gcs_uri}': path traversal segments ('..') are not allowed."
                    )
                job_prefix = (
                    f"{out_path}/{job_name}" if out_path else self.get_job_blob_prefix(job_name)
                )
            else:
                out_bucket_name = self.config.bucket_name
                job_prefix = self.get_job_blob_prefix(job_name)

            # 3. Resolve Endpoint and prepare payload
            endpoint = self.get_endpoint(endpoint_id)
            instance_payload = _prepare_af3_instance_for_endpoint(
                af3_query=af3_query,
                job_name=job_name,
                model_seeds=model_seeds,
                msa_free=msa_free,
            )

            # If async KFP mode requested (default for agent tool calls), submit KFP PipelineJob
            if not sync:
                logger.info(
                    f"Submitting AF3 KFP PipelineJob '{job_name}' ({total_tokens} tokens, msa_free={msa_free})"
                )
                return self._submit_kfp_pipeline(
                    endpoint=endpoint,
                    instance_payload=instance_payload,
                    job_name=job_name,
                    bucket_name=out_bucket_name,
                    job_prefix=job_prefix,
                    msa_free=msa_free,
                    model_seeds=model_seeds,
                    num_diffusion_samples=num_diffusion_samples,
                    total_tokens=total_tokens,
                    num_chains=num_chains,
                    collected_warnings=collected_warnings,
                )

            logger.info(
                f"Submitting synchronous AF3 job {job_name} ({total_tokens} tokens, msa_free={msa_free}) to Agent Platform Endpoint"
            )
            predict_parameters = {
                "run_data_pipeline": not msa_free,
                "num_diffusion_samples": num_diffusion_samples,
            }

            t0 = time.time()
            try:
                try:
                    prediction_response = endpoint.predict(
                        instances=[instance_payload],
                        parameters=predict_parameters,
                        timeout=self.config.timeout_seconds,
                    )
                except Exception as first_err:
                    if "NameResolutionError" in repr(first_err) or "ConnectionError" in repr(
                        first_err
                    ):
                        logger.warning(
                            f"Transient DNS/ConnectionError on AF3 endpoint ({first_err}); "
                            "refreshing endpoint metadata and retrying after 5s..."
                        )
                        if not type(endpoint).__module__.startswith("unittest.mock"):
                            time.sleep(5)
                        endpoint = self.get_endpoint(endpoint_id)
                        prediction_response = endpoint.predict(
                            instances=[instance_payload],
                            parameters=predict_parameters,
                            timeout=self.config.timeout_seconds,
                        )
                    else:
                        raise
            except DeadlineExceeded:
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": (
                        f"AlphaFold 3 prediction exceeded timeout budget of {self.config.timeout_seconds}s. "
                        "Consider reducing complex size or checking endpoint resources. "
                        "Do NOT undeploy the endpoint automatically without asking the user."
                    ),
                }
            except GoogleAPICallError as e:
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": (
                        f"Agent Platform Prediction API call failed: {e.message or str(e)}. "
                        "Do NOT undeploy the endpoint automatically without asking the user."
                    ),
                }
            elapsed_sec = time.time() - t0

            predictions = getattr(prediction_response, "predictions", [])
            if not predictions:
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": f"Agent Platform Endpoint returned empty prediction response for job '{job_name}'.",
                }

            result_item = predictions[0] if isinstance(predictions, list) else predictions
            cif_content = result_item.get("structure_cif") or result_item.get("cif", "")
            if not cif_content or not cif_content.strip():
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": f"Agent Platform Endpoint returned no structure coordinates for job '{job_name}'.",
                }

            summary_confidences = dict(
                result_item.get("summary") or result_item.get("summary_confidences") or {}
            )
            atom_plddts = result_item.get("plddt")
            pae = result_item.get("pae")

            # 4. Upload results to GCS using canonical paths
            bucket = self.storage_client.bucket(out_bucket_name)

            # Write input JSON
            input_blob = bucket.blob(f"{job_prefix}/input.json")
            input_blob.upload_from_string(
                json.dumps(instance_payload, indent=2), content_type="application/json"
            )

            # Write CIF structure
            cif_filename = MODEL_CIF_FILENAME.format(job_id=job_name)
            cif_blob = bucket.blob(f"{job_prefix}/{cif_filename}")
            cif_blob.upload_from_string(cif_content, content_type="chemical/x-mmcif")

            if pae:
                pae_blob = bucket.blob(f"{job_prefix}/pae.json")
                pae_blob.upload_from_string(
                    json.dumps(pae, indent=2), content_type="application/json"
                )

            gcs_output_dir = f"gs://{out_bucket_name}/{job_prefix}"
            cif_uri = f"{gcs_output_dir}/{cif_filename}"
            ranking_score = compute_ranking_score(summary_confidences)

            # Generate viewer summary.json + plots and enrich mean_plddt / mean_pae
            viewer_meta = build_and_upload_af3_viewer_artifacts(
                bucket=bucket,
                bucket_name=out_bucket_name,
                job_prefix=job_prefix,
                job_name=job_name,
                af3_query=instance_payload,
                cif_text=cif_content,
                cif_uri=cif_uri,
                summary_confidences=summary_confidences,
                atom_plddts=atom_plddts,
                pae_matrix=pae,
                msa_free=msa_free,
                ranking_score=ranking_score,
                elapsed_sec=elapsed_sec,
            )
            if summary_confidences.get("mean_plddt") is None and viewer_meta.get("mean_plddt"):
                summary_confidences["mean_plddt"] = viewer_meta["mean_plddt"]
            if (
                summary_confidences.get("mean_pae") is None
                and viewer_meta.get("mean_pae") is not None
            ):
                summary_confidences["mean_pae"] = viewer_meta["mean_pae"]

            # Write summary confidences (enriched with mean_plddt / mean_pae)
            conf_filename = SUMMARY_CONFIDENCES_FILENAME.format(job_id=job_name)
            conf_blob = bucket.blob(f"{job_prefix}/{conf_filename}")
            conf_blob.upload_from_string(
                json.dumps(summary_confidences, indent=2), content_type="application/json"
            )

            viewer_url = (
                f"{self.config.viewer_url}/job/{job_name}?model=af3"
                if self.config.viewer_url
                else self.gcs_console_url(cif_uri)
            )

            return {
                "status": "succeeded",
                "job_id": job_name,
                "model": "AlphaFold 3",
                "mode": "msa-free" if msa_free else "standard",
                "total_tokens": total_tokens,
                "endpoint": endpoint.resource_name,
                "gcs_output_dir": gcs_output_dir,
                "cif_uri": cif_uri,
                "summary_uri": viewer_meta.get("summary_uri"),
                "metrics": {
                    "ranking_score": round(float(ranking_score), 4)
                    if ranking_score is not None
                    else None,
                    "ptm": summary_confidences.get("ptm"),
                    "iptm": summary_confidences.get("iptm"),
                    "mean_plddt": summary_confidences.get("mean_plddt"),
                    "mean_pae": summary_confidences.get("mean_pae"),
                    "has_clash": summary_confidences.get("has_clash", 0.0),
                },
                "warnings": collected_warnings,
                "viewer_url": viewer_url,
                "message": (
                    f"AlphaFold 3 all-atom prediction for '{job_name}' completed on Agent Platform Endpoint. "
                    f"Predicted coordinates and viewer analysis saved to {gcs_output_dir}."
                ),
            }

        except Exception as e:
            logger.exception(f"Error executing AF3 prediction for {job_name}: {e}")
            return {
                "status": "error",
                "job_id": job_name,
                "message": (
                    f"AlphaFold 3 prediction failed: {e!s}. "
                    "Do NOT undeploy the endpoint automatically without asking the user."
                ),
            }
