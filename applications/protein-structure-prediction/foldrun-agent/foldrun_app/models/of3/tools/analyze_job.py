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

from foldrun_app.core.kfp_tools import KFPAnalyzeJobTool
from foldrun_common import build_analysis_task_config, discover_of3_predictions

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
        self._validate_gcs_uri(pipeline_root)
        storage_client = self._storage_module().Client(project=self.config.project_id)
        return discover_of3_predictions(pipeline_root, storage_client)

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

        task_config = build_analysis_task_config(
            model_type=self.model_type,
            job_id=job_id,
            analysis_path=analysis_path,
            raw_predictions=samples,
        ).model_dump(exclude_none=True)
        return task_config, len(samples), {}
