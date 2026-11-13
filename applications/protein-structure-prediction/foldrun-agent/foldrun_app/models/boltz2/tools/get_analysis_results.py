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

"""Tool for retrieving Boltz-2 analysis results from GCS."""

from foldrun_app.core.kfp_tools import KFPGetAnalysisResultsTool

from ..base import BOLTZ2Tool


class BOLTZ2GetAnalysisResultsTool(KFPGetAnalysisResultsTool, BOLTZ2Tool):
    """Retrieves Boltz-2 analysis results from Cloud Run Jobs."""

    model_type = "boltz2"
    item_noun = "samples"
