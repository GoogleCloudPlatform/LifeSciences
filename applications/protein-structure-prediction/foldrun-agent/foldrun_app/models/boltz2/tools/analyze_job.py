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

from foldrun_app.core.kfp_tools import KFPAnalyzeJobTool
from foldrun_common import (
    build_analysis_task_config,
    discover_boltz2_predictions,
    resolve_boltz2_query_yaml_uri,
)

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
        self._validate_gcs_uri(pipeline_root)
        storage_client = self._storage_module().Client(project=self.config.project_id)
        return discover_boltz2_predictions(pipeline_root, storage_client)

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
            if labels.get("query_name"):
                pipeline_root = job.runtime_config.gcs_output_directory
                bucket, _ = self._validate_gcs_uri(pipeline_root)
                candidate_query_uri = resolve_boltz2_query_yaml_uri(
                    bucket_name=bucket,
                    labels=labels,
                    strict_label_validation=True,
                )
                if candidate_query_uri:
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

        task_config = build_analysis_task_config(
            model_type=self.model_type,
            job_id=job_id,
            analysis_path=analysis_path,
            raw_predictions=samples,
            affinity_uri=affinity_uri,
            query_yaml_uri=query_yaml_uri,
        ).model_dump()
        return task_config, len(samples), {}
