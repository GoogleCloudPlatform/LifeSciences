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

"""ESMFold2 / ESMFold2-Fast model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.esmfold2.startup import get_config

MODEL_ID = "esmfold2"
DISPLAY_NAME = "ESMFold2"
CAPABILITIES = ("protein", "rna", "dna", "ligand")
INPUT_FORMAT = "json"
OUTPUT_FORMAT = "cif"


def _get_esmfold2_tool_classes() -> dict[str, type[BaseTool]]:
    from foldrun_app.models.esmfold2.tools import (
        ESMFOLD2GetAnalysisResultsTool,
        ESMFOLD2JobAnalysisTool,
        ESMFOLD2OpenViewerTool,
        ESMFOLD2SubmitPredictionTool,
    )

    return {
        "esmfold2_submit_prediction": ESMFOLD2SubmitPredictionTool,
        "esmfold2_analyze_parallel": ESMFOLD2JobAnalysisTool,
        "esmfold2_get_analysis_results": ESMFOLD2GetAnalysisResultsTool,
        "esmfold2_open_viewer": ESMFOLD2OpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("esmfold2", "esmfold2_fast"),
        job_id_prefixes=("esmfold2-", "esmfold2_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("A100", "A100_80GB", "RTX_PRO_6000", "L4"),
        is_enabled=lambda: bool(os.environ.get("ESMFOLD2_COMPONENTS_IMAGE")),
        get_config=get_config,
        get_tool_classes=_get_esmfold2_tool_classes,
        analyze_tool_key="esmfold2_analyze_parallel",
        results_tool_key="esmfold2_get_analysis_results",
        viewer_tool_key="esmfold2_open_viewer",
    )
)
