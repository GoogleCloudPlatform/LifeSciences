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

"""Tool for triggering parallel analysis of AlphaFold2 predictions via Cloud Run Jobs."""

import logging
from typing import Any

from google.cloud import run_v2, storage  # noqa: F401

from foldrun_app.core.kfp_tools import KFPAnalyzeJobTool

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job, get_task_details  # noqa: F401

logger = logging.getLogger(__name__)


class AF2JobAnalysisTool(KFPAnalyzeJobTool, AF2Tool):
    """Tool for triggering parallel analysis of AlphaFold2 predictions via Cloud Run Jobs."""

    model_type = "alphafold2"
    display_label = ""
    check_existing_before_discovery = False

    def _discover_and_build_task_config(
        self,
        job: Any,
        job_id: str,
        analysis_path: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], int, dict[str, Any]] | dict[str, Any]:
        task_details_fn = self._resolve_module_attr("get_task_details", get_task_details)
        task_details = task_details_fn(job)
        predictions = task_details.get("predictions", [])

        if not predictions:
            return {"status": "no_predictions", "message": "No predictions found for this job"}

        logger.info(f"Found {len(predictions)} predictions to analyze")
        top_n = arguments.get("top_n", 10)
        task_config = {
            "job_id": job_id,
            "analysis_path": analysis_path,
            "task_config_uri": f"{analysis_path}task_config.json",
            "predictions": [
                {
                    "index": i,
                    "uri": pred["uri"],
                    "model_name": pred["model_name"],
                    "ranking_confidence": pred["ranking_confidence"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, pred in enumerate(predictions)
            ],
        }
        return task_config, len(predictions), {"top_n": top_n}
