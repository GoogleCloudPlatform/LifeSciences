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

"""Configuration for the Research Agent."""

import os
from dataclasses import dataclass

import google.auth

# Configure Vertex AI by default
# To use AI Studio credentials instead:
# 1. Create a .env file with:
#    GOOGLE_GENAI_USE_VERTEXAI=FALSE
#    GOOGLE_API_KEY=PASTE_YOUR_ACTUAL_API_KEY_HERE
# 2. This will override the default Vertex AI configuration
_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")  # Vertex AI region for Gemini models
os.environ.setdefault("BIGQUERY_LOCATION", "US")  # BigQuery location for public datasets
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


@dataclass
class Config:
    """Configuration for the research agent."""

    # Model configurations
    # Use Flash for simple tasks (search, retrieval, orchestration)
    worker_model: str = os.getenv("WORKER_MODEL", "gemini-2.5-flash")
    root_model: str = os.getenv("ROOT_MODEL", "gemini-2.5-flash")
    bigquery_model: str = os.getenv("BIGQUERY_MODEL", "gemini-2.5-flash")

    # Use Pro ONLY for heavy synthesis and critical evaluation
    critic_model: str = os.getenv("CRITIC_MODEL", "gemini-2.5-pro")
    synthesis_model: str = os.getenv("SYNTHESIS_MODEL", "gemini-2.5-pro")

    # Search configurations
    max_search_iterations: int = int(os.getenv("MAX_SEARCH_ITERATIONS", "2"))

    # Retry configuration for improved reliability
    max_retry_count: int = int(os.getenv("MAX_RETRY_COUNT", "3"))
    initial_retry_delay: float = float(os.getenv("INITIAL_RETRY_DELAY", "1.0"))
    delay_multiplier: float = float(os.getenv("DELAY_MULTIPLIER", "2.0"))


config = Config()
