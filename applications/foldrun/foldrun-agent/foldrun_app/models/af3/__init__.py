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

"""AlphaFold 3 Vertex AI Endpoint model plugin registration."""

import os

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import ModelDescriptor, register_model
from foldrun_app.models.af3.startup import get_config

from .config import AF3Config
from .tools import (
    AF3BatchSubmitTool,
    AF3CheckEndpointTool,
    AF3GetResultsTool,
    AF3OpenViewerTool,
    AF3SubmitPredictionTool,
)

MODEL_ID = "alphafold3"
DISPLAY_NAME = "AlphaFold 3"
CAPABILITIES = ("protein", "rna", "dna", "ligand", "ion")
INPUT_FORMAT = "json"
OUTPUT_FORMAT = "cif"

_AF3_ENV_VARS = (
    "AF3_ENDPOINT",
    "AF3_AGENT_PLATFORM_ENDPOINT",
    "AF3_ENDPOINT_ID",
    "AF3_VERTEX_ENDPOINT",
)


def _get_af3_tool_classes() -> dict[str, type[BaseTool]]:
    return {
        "af3_submit_prediction": AF3SubmitPredictionTool,
        "af3_submit_batch": AF3BatchSubmitTool,
        "af3_check_endpoint": AF3CheckEndpointTool,
        "af3_get_results": AF3GetResultsTool,
        "af3_open_viewer": AF3OpenViewerTool,
    }


DESCRIPTOR = register_model(
    ModelDescriptor(
        name=MODEL_ID,
        display_name=DISPLAY_NAME,
        aliases=("alphafold3", "af3"),
        job_id_prefixes=("alphafold3-", "alphafold3_", "af3-", "af3_"),
        capabilities=CAPABILITIES,
        input_format=INPUT_FORMAT,
        output_format=OUTPUT_FORMAT,
        default_gpus=("H100_80GB",),
        is_enabled=lambda: any(bool(os.environ.get(k)) for k in _AF3_ENV_VARS),
        get_config=get_config,
        get_tool_classes=_get_af3_tool_classes,
        analyze_tool_key="af3_get_results",
        results_tool_key="af3_get_results",
        viewer_tool_key="af3_open_viewer",
    )
)

__all__ = [
    "DESCRIPTOR",
    "AF3BatchSubmitTool",
    "AF3CheckEndpointTool",
    "AF3Config",
    "AF3GetResultsTool",
    "AF3OpenViewerTool",
    "AF3SubmitPredictionTool",
]
