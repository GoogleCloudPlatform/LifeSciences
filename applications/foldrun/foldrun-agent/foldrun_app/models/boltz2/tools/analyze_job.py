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

"""Tool for triggering parallel analysis of Boltz-2 predictions via Cloud Run Jobs."""

import logging
from typing import Any

from google.cloud import run_v2, storage  # noqa: F401

from foldrun_app.core.kfp_tools import KFPAnalyzeJobTool
from foldrun_app.core.vertex_utils import get_pipeline_job  # noqa: F401

from ..base import BOLTZ2Tool

logger = logging.getLogger(__name__)


class BOLTZ2JobAnalysisTool(KFPAnalyzeJobTool, BOLTZ2Tool):
    """Triggers parallel analysis of Boltz-2 predictions via Cloud Run Jobs."""

    model_type = "boltz2"
    display_label = "BOLTZ2"
    check_existing_before_discovery = True

    def _discover_boltz2_samples(self, job: Any) -> tuple[list[dict[str, Any]], str | None]:
        """Discover BOLTZ2 output samples and optional affinity file from GCS."""
        pipeline_root = job.runtime_config.gcs_output_directory
        bucket_name, prefix = self._validate_gcs_uri(pipeline_root)
        if not prefix.endswith("/"):
            prefix += "/"

        storage_client = self._storage_module().Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        all_blobs = list(bucket.list_blobs(prefix=prefix))

        samples = []
        seen = set()
        affinity_uri = None

        for blob in all_blobs:
            name = blob.name
            if name.endswith(".json") and "/predictions/" in name:
                parts_list = name.split("/")
                filename = parts_list[-1]

                if "/confidence_" in name:
                    if name in seen:
                        continue
                    seen.add(name)

                    cif_filename = filename.replace("confidence_", "", 1).replace(".json", ".cif")
                    cif_name = "/".join([*parts_list[:-1], cif_filename])
                    pde_filename = filename.replace("confidence_", "pde_", 1).replace(
                        ".json", ".npz"
                    )
                    pde_name = "/".join([*parts_list[:-1], pde_filename])
                    sample_name = filename.replace("confidence_", "", 1).replace(".json", "")

                    samples.append(
                        {
                            "sample_name": sample_name,
                            "cif_uri": f"gs://{bucket_name}/{cif_name}",
                            "aggregated_uri": f"gs://{bucket_name}/{name}",
                            "pde_uri": f"gs://{bucket_name}/{pde_name}",
                        }
                    )

                elif filename.startswith("affinity_") and affinity_uri is None:
                    affinity_uri = f"gs://{bucket_name}/{name}"
                    logger.info(f"Found affinity results: {affinity_uri}")

        logger.info(f"Discovered {len(samples)} BOLTZ2 samples")
        return samples, affinity_uri

    def _discover_and_build_task_config(
        self,
        job: Any,
        job_id: str,
        analysis_path: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], int, dict[str, Any]] | dict[str, Any]:
        samples, affinity_uri = self._discover_boltz2_samples(job)
        if not samples:
            return {"status": "no_predictions", "message": "No BOLTZ2 samples found for this job"}

        query_yaml_uri = None
        try:
            labels = dict(job.labels) if hasattr(job, "labels") and job.labels else {}
            query_name_label = labels.get("query_name", "")
            if query_name_label:
                pipeline_root = job.runtime_config.gcs_output_directory
                bucket, _ = self._validate_gcs_uri(pipeline_root)
                candidate_query_uri = f"gs://{bucket}/boltz2_queries/{query_name_label}.yaml"
                self._validate_gcs_uri(candidate_query_uri)
                query_yaml_uri = candidate_query_uri
                logger.info(f"Resolved query YAML URI from label: {query_yaml_uri}")
            else:
                logger.warning(
                    "No 'query_name' label found on pipeline job — query YAML will not be embedded in analysis"
                )
        except ValueError:
            raise
        except Exception as e:
            logger.warning(f"Could not resolve query YAML URI: {e}")

        task_config = {
            "job_id": job_id,
            "analysis_path": analysis_path,
            "affinity_uri": affinity_uri,
            "query_yaml_uri": query_yaml_uri,
            "predictions": [
                {
                    "index": i,
                    "sample_name": s["sample_name"],
                    "cif_uri": s["cif_uri"],
                    "confidences_uri": "",
                    "aggregated_uri": s["aggregated_uri"],
                    "pde_uri": s.get("pde_uri", ""),
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, s in enumerate(samples)
            ],
        }
        return task_config, len(samples), {}
