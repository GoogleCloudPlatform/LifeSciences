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

"""Centralized retry options and Gemini model instances for AlphaGenome variant annotation agent."""

from __future__ import annotations

import os

from google.adk.models.google_llm import Gemini
from google.adk.planners import BuiltInPlanner
from google.genai import types

PRO_MODEL = os.getenv("COORDINATOR_MODEL_NAME", "gemini-4-argon")
FLASH_MODEL = os.getenv("WORKER_MODEL_NAME", "gemini-3.8-flash")

# Surface model reasoning as thought summaries in Gemini Enterprise and Agent Engine traces.
# Toggle off via SHOW_THOUGHTS=0 if needed.
_SHOW_THOUGHTS = os.getenv("SHOW_THOUGHTS", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "",
)


def get_thinking_planner() -> BuiltInPlanner | None:
    """Returns a BuiltInPlanner with include_thoughts=True when enabled."""
    if not _SHOW_THOUGHTS:
        return None
    return BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True))


THINKING_PLANNER = get_thinking_planner()

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
    model_name: str | None = None,
    retry_options: types.HttpRetryOptions | None = None,
) -> Gemini:
    """Returns an ADK Gemini model configured with HTTP retries."""
    effective_model = model_name or PRO_MODEL
    return Gemini(
        model=effective_model,
        retry_options=retry_options or MODEL_RETRY_OPTIONS,
    )
