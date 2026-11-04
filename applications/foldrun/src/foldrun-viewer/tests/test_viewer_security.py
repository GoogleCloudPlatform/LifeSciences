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

"""CI pytest entrypoint discovering FoldRun Viewer security and API unit tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from test_security import (  # noqa: F401
    TestArtifactDownloadEndpoints,
    TestCustomSlugResolutionAndAnalyzeRouting,
    TestFinding444GcsUriValidation,
    TestFinding445JobIdValidation,
    TestGenerateSvgsDefusedXml,
    TestScientificIntegrityAndBfactors,
)
