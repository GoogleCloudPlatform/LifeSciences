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

"""Tool for triggering parallel analysis of OpenFold3 predictions via Cloud Run Jobs."""

import logging
from typing import Any

from google.cloud import run_v2, storage  # noqa: F401

from foldrun_app.core.kfp_tools import KFPAnalyzeJobTool
from foldrun_app.core.vertex_utils import get_pipeline_job  # noqa: F401

from ..base import OF3Tool

logger = logging.getLogger(__name__)


class OF3JobAnalysisTool(KFPAnalyzeJobTool, OF3Tool):
    """Triggers parallel analysis of OpenFold3 predictions via Cloud Run Jobs."""

    model_type = "openfold3"
    display_label = "OF3"
    check_existing_before_discovery = True

    def _discover_of3_samples(self, job: Any) -> list[dict[str, Any]]:
        """Discover OF3 output samples by walking seed_*/sample directories in GCS."""
        pipeline_root = job.runtime_config.gcs_output_directory
        bucket_name, prefix = self._validate_gcs_uri(pipeline_root)
        if not prefix.endswith("/"):
            prefix += "/"

        storage_client = self._storage_module().Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        all_blobs = list(bucket.list_blobs(prefix=prefix))

        samples = []
        seen = set()

        for blob in all_blobs:
            name = blob.name
            if name.endswith("_confidences_aggregated.json"):
                if name in seen:
                    continue
                seen.add(name)

                base = name.replace("_confidences_aggregated.json", "")
                cif_name = f"{base}_model.cif"
                confidences_name = f"{base}_confidences.json"
                filename = name.rsplit("/", 1)[-1]
                sample_name = filename.replace("_confidences_aggregated.json", "")

                samples.append(
                    {
                        "sample_name": sample_name,
                        "cif_uri": f"gs://{bucket_name}/{cif_name}",
                        "confidences_uri": f"gs://{bucket_name}/{confidences_name}",
                        "aggregated_uri": f"gs://{bucket_name}/{name}",
                    }
                )

        logger.info(f"Discovered {len(samples)} OF3 samples")
        return samples

    def _discover_and_build_task_config(
        self,
        job: Any,
        job_id: str,
        analysis_path: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], int, dict[str, Any]] | dict[str, Any]:
        samples = self._discover_of3_samples(job)
        if not samples:
            return {"status": "no_predictions", "message": "No OF3 samples found for this job"}

        task_config = {
            "job_id": job_id,
            "analysis_path": analysis_path,
            "predictions": [
                {
                    "index": i,
                    "sample_name": s["sample_name"],
                    "cif_uri": s["cif_uri"],
                    "confidences_uri": s["confidences_uri"],
                    "aggregated_uri": s["aggregated_uri"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, s in enumerate(samples)
            ],
        }
        return task_config, len(samples), {}
