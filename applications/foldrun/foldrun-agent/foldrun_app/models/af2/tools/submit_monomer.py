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

"""Tool for submitting monomer AlphaFold2 predictions."""

from typing import ClassVar

from google.cloud import aiplatform as vertex_ai

from .submit_base import AF2SubmitBaseTool

__all__ = ["AF2SubmitMonomerTool", "vertex_ai"]


class AF2SubmitMonomerTool(AF2SubmitBaseTool):
    """Tool for submitting monomer AlphaFold2 predictions."""

    is_multimer: ClassVar[bool] = False
    job_type: ClassVar[str] = "monomer"
