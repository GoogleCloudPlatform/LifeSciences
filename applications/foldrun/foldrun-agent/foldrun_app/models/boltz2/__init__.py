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

"""Boltz-2 model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.boltz2.startup import get_config

MODEL_ID = "boltz2"
DISPLAY_NAME = "Boltz-2"
CAPABILITIES = ("protein", "rna", "dna", "ligand")
INPUT_FORMAT = "yaml"
OUTPUT_FORMAT = "cif"


def _get_boltz2_tool_classes() -> dict[str, type[BaseTool]]:
    from foldrun_app.models.boltz2.tools import (
        BOLTZ2GetAnalysisResultsTool,
        BOLTZ2JobAnalysisTool,
        BOLTZ2OpenViewerTool,
        BOLTZ2SubmitPredictionTool,
    )

    return {
        "boltz2_submit_prediction": BOLTZ2SubmitPredictionTool,
        "boltz2_analyze_parallel": BOLTZ2JobAnalysisTool,
        "boltz2_get_analysis_results": BOLTZ2GetAnalysisResultsTool,
        "boltz2_open_viewer": BOLTZ2OpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("boltz2", "boltz-2", "boltz"),
        job_id_prefixes=("boltz2-", "boltz2_", "boltz-", "boltz_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("A100", "A100_80GB", "RTX_PRO_6000", "L4"),
        is_enabled=lambda: bool(os.environ.get("BOLTZ2_COMPONENTS_IMAGE")),
        get_config=get_config,
        get_tool_classes=_get_boltz2_tool_classes,
        analyze_tool_key="boltz2_analyze_parallel",
        results_tool_key="boltz2_get_analysis_results",
        viewer_tool_key="boltz2_open_viewer",
    )
)
