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

import logging
import os

from collections.abc import AsyncGenerator
from functools import cached_property
from typing import TYPE_CHECKING

from google.adk.models import Gemini
from google.genai import Client, types

if TYPE_CHECKING:
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class Gemini3(Gemini):
    """Override for Gemini3 models because they are only available on the Global endpoint as of 1/2026.
    The ADK gemini client overrides the region even if you use the full model path. So you need to
    override it manually.
    """

    @cached_property
    def api_client(self) -> Client:
        """Provides the api client with explicit global region"""
        # Ensure project ID is retrieved, falling back to a placeholder or raising an error if needed.
        project = os.getenv("GOOGLE_CLOUD_PROJECT")

        # Explicitly setting location to 'global' to avoid regional endpoint resolution issues
        location = "global"

        return Client(
            project=project,
            location=location,
            http_options=types.HttpOptions(
                headers=self._tracking_headers(),
                retry_options=self.retry_options,
            ),
        )
