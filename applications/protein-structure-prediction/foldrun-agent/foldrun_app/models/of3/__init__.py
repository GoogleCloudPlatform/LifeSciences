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

"""OpenFold3 model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.of3.startup import get_config

MODEL_ID = "openfold3"
DISPLAY_NAME = "OpenFold3"
CAPABILITIES = ("protein", "rna", "dna", "ligand")
INPUT_FORMAT = "json"
OUTPUT_FORMAT = "cif"


def _get_of3_tool_classes() -> dict[str, type[BaseTool]]:
    from foldrun_app.models.of3.tools import (
        OF3GetAnalysisResultsTool,
        OF3JobAnalysisTool,
        OF3OpenViewerTool,
        OF3SubmitPredictionTool,
    )

    return {
        "of3_submit_prediction": OF3SubmitPredictionTool,
        "of3_analyze_parallel": OF3JobAnalysisTool,
        "of3_get_analysis_results": OF3GetAnalysisResultsTool,
        "of3_open_viewer": OF3OpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("openfold3", "of3"),
        job_id_prefixes=("openfold3-", "openfold3_", "of3-", "of3_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("A100", "A100_80GB", "RTX_PRO_6000"),
        is_enabled=lambda: bool(os.environ.get("OPENFOLD3_COMPONENTS_IMAGE")),
        get_config=get_config,
        get_tool_classes=_get_of3_tool_classes,
        analyze_tool_key="of3_analyze_parallel",
        results_tool_key="of3_get_analysis_results",
        viewer_tool_key="of3_open_viewer",
    )
)
