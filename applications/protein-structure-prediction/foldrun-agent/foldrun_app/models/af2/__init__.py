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

"""AlphaFold2 model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.af2.startup import get_config

MODEL_ID = "alphafold2"
DISPLAY_NAME = "AlphaFold2"
CAPABILITIES = ("protein",)
INPUT_FORMAT = "fasta"
OUTPUT_FORMAT = "pdb"


def _get_af2_tool_classes() -> dict[str, type[BaseTool]]:
    from foldrun_app.models.af2.tools import (
        AF2AnalysisTool,
        AF2BatchSubmitTool,
        AF2GetAnalysisResultsTool,
        AF2GetResultsTool,
        AF2JobAnalysisTool,
        AF2OpenViewerTool,
        AF2SubmitMonomerTool,
        AF2SubmitMultimerTool,
    )

    return {
        "af2_submit_monomer": AF2SubmitMonomerTool,
        "af2_submit_multimer": AF2SubmitMultimerTool,
        "af2_submit_batch": AF2BatchSubmitTool,
        "af2_get_results": AF2GetResultsTool,
        "af2_analyze_quality": AF2AnalysisTool,
        "af2_analyze_parallel": AF2JobAnalysisTool,
        "af2_get_analysis_results": AF2GetAnalysisResultsTool,
        "af2_open_viewer": AF2OpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("alphafold2", "af2"),
        job_id_prefixes=("alphafold2-", "alphafold2_", "alphafold-", "af2-", "af2_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("L4", "A100", "A100_80GB"),
        is_enabled=lambda: bool(os.environ.get("ALPHAFOLD_COMPONENTS_IMAGE")),
        get_config=get_config,
        get_tool_classes=_get_af2_tool_classes,
        analyze_tool_key="af2_analyze_parallel",
        results_tool_key="af2_get_analysis_results",
        viewer_tool_key="af2_open_viewer",
    )
)
