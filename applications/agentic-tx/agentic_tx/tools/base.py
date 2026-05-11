# Copyright 2025 Google LLC
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

"""Base class for all tools in Agentic-Tx."""

import re

from abc import ABC, abstractmethod


class BaseTool(ABC):
    """Base class for all tools."""

    def __init__(self):
        self.tool_name = self.__class__.__name__

    @abstractmethod
    def use_tool(self, *args, **kwargs):
        """Execute the tool functionality."""

    @abstractmethod
    def tool_is_used(self, query: str) -> bool:
        """Check if this tool was invoked in the query."""

    @abstractmethod
    def process_query(self, query: str) -> str:
        """Process the query to extract the tool input."""

    @abstractmethod
    def instructions(self) -> str:
        """Return instructions on how to use this tool."""


def extract_prompt(text: str, word: str) -> str:
    """Extract content from inside backticks with a specific word."""
    code_block_pattern = rf"```{word}(.*?)```"
    code_blocks = re.findall(code_block_pattern, text, re.DOTALL)
    extracted_code = "\n".join(code_blocks).strip()
    return extracted_code
