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

"""Tool for retrieving ESMFold2 analysis results from GCS."""

from foldrun_app.core.kfp_tools import KFPGetAnalysisResultsTool

from ..base import ESMFOLD2Tool


class ESMFOLD2GetAnalysisResultsTool(KFPGetAnalysisResultsTool, ESMFOLD2Tool):
    """Retrieves ESMFold2 / ESMFold2-Fast analysis results from Cloud Run Jobs."""

    model_type = "esmfold2"
    item_noun = "samples"
