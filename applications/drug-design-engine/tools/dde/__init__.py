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

"""dde — the execution surface for dde agent tooling.

The CLI owns execution, rate limits and leases, retry, provenance
stamping, artifact naming, and threshold values. Routing lives in skill
descriptions, not here: never assume an agent will read `dde --help`
to find its way (docs/tool-design-guidance.md §1).
"""

from .core.env import CLI_VERSION

__version__ = CLI_VERSION
