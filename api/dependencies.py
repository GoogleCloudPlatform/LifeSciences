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

import logging

from fastapi import Depends, Request
from google.cloud import storage

from api.services.analyzer_service import AnalyzerService
from api.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


def get_gemini_client(request: Request) -> GeminiClient:
    """Dependency for getting the shared Gemini client."""
    client = getattr(request.app.state, "gemini_client", None)
    if client is None:
        raise RuntimeError("Gemini Client not initialized")
    return client


def get_storage_client(request: Request) -> storage.Client:
    """Dependency for getting the shared GCS client."""
    client = getattr(request.app.state, "storage_client", None)
    if client is None:
        raise RuntimeError(
            "GCS Client not initialized. This feature requires GOOGLE_GENAI_USE_VERTEXAI=True."
        )
    return client


def get_analyzer_service(
    client: GeminiClient = Depends(get_gemini_client),
) -> AnalyzerService:
    """Dependency for getting an AnalyzerService instance with the shared client."""
    return AnalyzerService(gemini_client=client)
