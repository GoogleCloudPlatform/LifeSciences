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

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from google.api_core.exceptions import DeadlineExceeded, GoogleAPICallError

from ..base import MODEL_CIF_FILENAME, SUMMARY_CONFIDENCES_FILENAME, AF3Tool
from ..utils.input_converter import (
    count_af3_tokens,
    fasta_to_af3_json,
    is_af3_json,
    validate_af3_json,
)
from ..utils.metrics import compute_ranking_score

logger = logging.getLogger(__name__)


class AF3SubmitPredictionTool(AF3Tool):
    """Tool for submitting all-atom complex predictions via AlphaFold 3 Agent Platform Endpoint."""

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Submit an AlphaFold 3 prediction job.

        Args:
            arguments: {
                'input': FASTA content, AF3 JSON content, or GCS/file path,
                'job_name': Optional job name (defaults to 'af3_YYYYMMDD_HHMMSS'),
                'msa_free': Zero-MSA screening mode (default: True),
                'model_seeds': Random seeds for diffusion sampling (default: [1]),
                'endpoint_id': Optional Agent Platform Endpoint ID override,
                'output_gcs_uri': Optional custom GCS destination URI,
            }

        Returns:
            Dictionary containing prediction status, metrics, warnings, and GCS artifact URIs.
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
        msa_free = arguments.get("msa_free", self.config.default_msa_free)
        model_seeds = arguments.get("model_seeds") or [1]
        if isinstance(model_seeds, int):
            model_seeds = [model_seeds]

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
                af3_query["msaFree"] = msa_free
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
            logger.info(
                f"Submitting AF3 job {job_name} ({total_tokens} tokens, msa_free={msa_free}) to Agent Platform Endpoint"
            )

            # 3. Resolve Endpoint and dispatch prediction
            # Send the complete af3_query to preserve all dialect fields and msaFree
            endpoint = self.get_endpoint(endpoint_id)
            instance_payload = dict(af3_query)
            instance_payload["name"] = job_name
            instance_payload["modelSeeds"] = model_seeds
            instance_payload["msaFree"] = msa_free

            try:
                prediction_response = endpoint.predict(
                    instances=[instance_payload],
                    timeout=self.config.timeout_seconds,
                )
            except DeadlineExceeded:
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": (
                        f"AlphaFold 3 prediction exceeded timeout budget of {self.config.timeout_seconds}s. "
                        "Consider reducing complex size or checking endpoint resources."
                    ),
                }
            except GoogleAPICallError as e:
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": f"Agent Platform Prediction API call failed: {e.message or str(e)}",
                }

            predictions = getattr(prediction_response, "predictions", [])
            if not predictions:
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": f"Agent Platform Endpoint returned empty prediction response for job '{job_name}'.",
                }

            result_item = predictions[0] if isinstance(predictions, list) else predictions
            cif_content = result_item.get("cif") or result_item.get("structure_cif", "")
            if not cif_content or not cif_content.strip():
                return {
                    "status": "error",
                    "job_id": job_name,
                    "message": f"Agent Platform Endpoint returned no structure coordinates for job '{job_name}'.",
                }

            summary_confidences = result_item.get("summary_confidences") or {}
            pae = result_item.get("pae")

            # 4. Upload results to GCS using canonical paths
            output_gcs_uri = arguments.get("output_gcs_uri")
            if output_gcs_uri:
                parsed_out = urlparse(output_gcs_uri)
                if parsed_out.scheme != "gs" or not parsed_out.netloc:
                    raise ValueError(
                        f"Invalid output GCS URI: '{output_gcs_uri}'. Must follow format 'gs://bucket-name/[prefix]'."
                    )
                bucket_name = parsed_out.netloc
                out_path = parsed_out.path.strip("/")
                job_prefix = (
                    f"{out_path}/{job_name}" if out_path else self.get_job_blob_prefix(job_name)
                )
            else:
                bucket_name = self.config.bucket_name
                job_prefix = self.get_job_blob_prefix(job_name)

            bucket = self.storage_client.bucket(bucket_name)

            # Write input JSON
            input_blob = bucket.blob(f"{job_prefix}/input.json")
            input_blob.upload_from_string(
                json.dumps(af3_query, indent=2), content_type="application/json"
            )

            # Write summary confidences
            conf_filename = SUMMARY_CONFIDENCES_FILENAME.format(job_id=job_name)
            conf_blob = bucket.blob(f"{job_prefix}/{conf_filename}")
            conf_blob.upload_from_string(
                json.dumps(summary_confidences, indent=2), content_type="application/json"
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

            gcs_output_dir = f"gs://{bucket_name}/{job_prefix}"
            cif_uri = f"{gcs_output_dir}/{cif_filename}"
            viewer_url = (
                f"{self.config.viewer_url}/job/{job_name}?model=af3"
                if self.config.viewer_url
                else self.gcs_console_url(cif_uri)
            )

            ranking_score = compute_ranking_score(summary_confidences)

            return {
                "status": "succeeded",
                "job_id": job_name,
                "model": "AlphaFold 3",
                "mode": "msa-free" if msa_free else "standard",
                "total_tokens": total_tokens,
                "endpoint": endpoint.resource_name,
                "gcs_output_dir": gcs_output_dir,
                "cif_uri": cif_uri,
                "metrics": {
                    "ranking_score": round(float(ranking_score), 4)
                    if ranking_score is not None
                    else None,
                    "ptm": summary_confidences.get("ptm"),
                    "iptm": summary_confidences.get("iptm"),
                    "mean_plddt": summary_confidences.get("mean_plddt"),
                    "has_clash": summary_confidences.get("has_clash", 0.0),
                },
                "warnings": collected_warnings,
                "viewer_url": viewer_url,
                "message": (
                    f"AlphaFold 3 all-atom prediction for '{job_name}' completed on Agent Platform Endpoint. "
                    f"Predicted coordinates saved to {cif_uri}."
                ),
            }

        except Exception as e:
            logger.exception(f"Error executing AF3 prediction for {job_name}: {e}")
            return {
                "status": "error",
                "job_id": job_name,
                "message": f"AlphaFold 3 prediction failed: {e!s}",
            }
