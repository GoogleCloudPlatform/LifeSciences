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

"""OF3 Open Viewer Tool - Opens the 3D structure viewer for OpenFold3 prediction results."""

from foldrun_app.core.kfp_tools import KFPOpenViewerTool

from ..base import OF3Tool


class OF3OpenViewerTool(KFPOpenViewerTool, OF3Tool):
    """Opens the FoldRun structure viewer for completed OpenFold3 predictions."""
