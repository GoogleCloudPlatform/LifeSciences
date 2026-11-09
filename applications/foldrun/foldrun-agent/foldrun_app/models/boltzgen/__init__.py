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

"""BoltzGen model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.boltzgen.startup import get_config

MODEL_ID = "boltzgen"
DISPLAY_NAME = "BoltzGen"
CAPABILITIES = (
    "protein_binder_design",
    "peptide_design",
    "nanobody_design",
    "antibody_cdr_design",
    "small_molecule_binder_design",
    "protein_redesign",
)
INPUT_FORMAT = "yaml"
OUTPUT_FORMAT = "cif"


def _get_boltzgen_tool_classes() -> dict[str, type[BaseTool]]:
    from foldrun_app.models.boltzgen.tools import (
        BOLTZGENGetAnalysisResultsTool,
        BOLTZGENJobAnalysisTool,
        BOLTZGENOpenViewerTool,
        BOLTZGENSubmitDesignTool,
    )

    return {
        "boltzgen_submit_design": BOLTZGENSubmitDesignTool,
        "boltzgen_analyze_parallel": BOLTZGENJobAnalysisTool,
        "boltzgen_get_analysis_results": BOLTZGENGetAnalysisResultsTool,
        "boltzgen_open_viewer": BOLTZGENOpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("boltzgen",),
        job_id_prefixes=("boltzgen-", "boltzgen_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("A100", "A100_80GB", "L4"),
        is_enabled=lambda: bool(os.environ.get("BOLTZGEN_COMPONENTS_IMAGE")),
        get_config=get_config,
        get_tool_classes=_get_boltzgen_tool_classes,
        analyze_tool_key="boltzgen_analyze_parallel",
        results_tool_key="boltzgen_get_analysis_results",
        viewer_tool_key="boltzgen_open_viewer",
    )
)
