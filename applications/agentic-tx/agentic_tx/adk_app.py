# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from vertexai.preview.reasoning_engines import AdkApp

from .agent import create_agentic_tx_agent


# IMPORTANT! the build and deployment process rely on this file and this function
# to exist with these exact names. Modify the terraform pickle process to support
# other approaches.
def create_app() -> AdkApp:
    return AdkApp(
        agent=create_agentic_tx_agent(),
    )
