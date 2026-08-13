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

"""Centralized retry options and Gemini model instances for BioCompass."""

from __future__ import annotations

import os

from google.adk.models.google_llm import Gemini
from google.genai import types

# Retry configuration for model calls to handle transient 429 RESOURCE_EXHAUSTED
# and 5xx server errors across both agent and genai client layers.
MODEL_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=int(os.getenv("MODEL_RETRY_ATTEMPTS", "5")),
    initial_delay=float(os.getenv("MODEL_RETRY_INITIAL_DELAY", "1.0")),
    max_delay=float(os.getenv("MODEL_RETRY_MAX_DELAY", "60.0")),
    exp_base=float(os.getenv("MODEL_RETRY_EXP_BASE", "2.0")),
    jitter=float(os.getenv("MODEL_RETRY_JITTER", "1.0")),
    http_status_codes=[408, 429, 500, 502, 503, 504],
)

MODEL_HTTP_OPTIONS = types.HttpOptions(retry_options=MODEL_RETRY_OPTIONS)


def get_gemini_model(
    model_name: str,
    retry_options: types.HttpRetryOptions | None = None,
) -> Gemini:
    """Returns an ADK Gemini model configured with HTTP retries."""
    return Gemini(
        model=model_name,
        retry_options=retry_options or MODEL_RETRY_OPTIONS,
    )
