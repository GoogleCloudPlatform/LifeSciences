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

"""BindCraft model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.bindcraft.startup import get_config

MODEL_ID = "bindcraft"
DISPLAY_NAME = "BindCraft"
CAPABILITIES = ("binder_design", "protein_binder_design")
INPUT_FORMAT = "pdb"
OUTPUT_FORMAT = "pdb"


def _get_bindcraft_tool_classes() -> dict[str, type[BaseTool]]:
    from foldrun_app.models.bindcraft.tools import (
        BINDCRAFTGetAnalysisResultsTool,
        BINDCRAFTJobAnalysisTool,
        BINDCRAFTOpenViewerTool,
        BINDCRAFTSubmitDesignTool,
    )

    return {
        "bindcraft_submit_design": BINDCRAFTSubmitDesignTool,
        "bindcraft_analyze_job": BINDCRAFTJobAnalysisTool,
        "bindcraft_get_analysis_results": BINDCRAFTGetAnalysisResultsTool,
        "bindcraft_open_viewer": BINDCRAFTOpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("bindcraft", "freebindcraft"),
        job_id_prefixes=("bindcraft-", "bindcraft_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("A100", "A100_80GB", "L4"),
        is_enabled=lambda: bool(os.environ.get("BINDCRAFT_COMPONENTS_IMAGE")),
        get_config=get_config,
        get_tool_classes=_get_bindcraft_tool_classes,
        analyze_tool_key="bindcraft_analyze_job",
        results_tool_key="bindcraft_get_analysis_results",
        viewer_tool_key="bindcraft_open_viewer",
    )
)
