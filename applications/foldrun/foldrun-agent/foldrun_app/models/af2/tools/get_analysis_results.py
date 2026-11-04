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

"""Tool for retrieving and aggregating AlphaFold2 analysis results from GCS."""

from google.cloud import run_v2, storage  # noqa: F401

from foldrun_app.core.download_utils import build_agent_downloads_dict  # noqa: F401
from foldrun_app.core.kfp_tools import KFPGetAnalysisResultsTool

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job  # noqa: F401


class AF2GetAnalysisResultsTool(KFPGetAnalysisResultsTool, AF2Tool):
    """Tool for retrieving and aggregating Cloud Run analysis results for AlphaFold2."""

    model_type = "alphafold2"
    item_noun = "predictions"
