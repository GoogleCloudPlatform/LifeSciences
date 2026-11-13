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

"""Base classes for FoldRun modular skills architecture."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from google.adk.tools import FunctionTool


@dataclass
class Skill:
    """Encapsulates a modular domain skill with instructions, tools, and metadata."""

    name: str
    description: str
    instruction: str
    tool_functions: list[Callable[..., Any]] = field(default_factory=list)

    @property
    def tools(self) -> list[Any]:
        """Convert underlying tool functions or tool instances to ADK Tools."""
        result = []
        for item in self.tool_functions:
            if callable(item) and not hasattr(item, "func") and not hasattr(item, "name"):
                result.append(FunctionTool(item))
            else:
                result.append(item)
        return result

    @tools.setter
    def tools(self, value: list[Any]):
        self.tool_functions = value
