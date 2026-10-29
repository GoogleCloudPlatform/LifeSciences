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

"""RFD4-Proteina model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.rfd4_proteina.startup import get_config

MODEL_ID = "rfd4_proteina"
DISPLAY_NAME = "RFD4-Proteina"
CAPABILITIES = ("binder_design", "motif_scaffolding", "unconditional_monomer")
INPUT_FORMAT = "pdb"
OUTPUT_FORMAT = "cif"


def _get_rfd4_proteina_tool_classes() -> dict[str, type[BaseTool]]:
    from foldrun_app.models.rfd4_proteina.tools import (
        RFD4ProteinaGetAnalysisResultsTool,
        RFD4ProteinaJobAnalysisTool,
        RFD4ProteinaOpenViewerTool,
        RFD4ProteinaSubmitDesignTool,
    )

    return {
        "rfd4_proteina_submit_design": RFD4ProteinaSubmitDesignTool,
        "rfd4_proteina_analyze_job": RFD4ProteinaJobAnalysisTool,
        "rfd4_proteina_get_analysis_results": RFD4ProteinaGetAnalysisResultsTool,
        "rfd4_proteina_open_viewer": RFD4ProteinaOpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("rfd4_proteina", "rfd4-proteina", "rfd4", "proteina"),
        job_id_prefixes=("rfd4_proteina-", "rfd4_proteina_", "rfd4-proteina-", "rfd4-", "rfd4_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("A100", "A100_80GB", "L4"),
        is_enabled=lambda: bool(os.environ.get("RFD4_PROTEINA_COMPONENTS_IMAGE")),
        get_config=get_config,
        get_tool_classes=_get_rfd4_proteina_tool_classes,
        analyze_tool_key="rfd4_proteina_analyze_job",
        results_tool_key="rfd4_proteina_get_analysis_results",
        viewer_tool_key="rfd4_proteina_open_viewer",
    )
)
