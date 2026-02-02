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

import os
from unittest import mock

import pytest
from pydantic import ValidationError

from api.config import Settings


def test_config_vertex_ai_false_valid():
    """Test configuration when using AI Studio (Vertex AI False) with API key."""
    with mock.patch.dict(
        os.environ,
        {"GOOGLE_GENAI_USE_VERTEXAI": "false", "GEMINI_API_KEY": "test-key"},
        clear=True,
    ):
        settings = Settings()
        assert settings.google_genai_use_vertexai is False
        assert settings.gemini_api_key == "test-key"


def test_config_vertex_ai_false_missing_api_key():
    """Test failure when using AI Studio without API key."""
    with mock.patch.dict(
        os.environ, {"GOOGLE_GENAI_USE_VERTEXAI": "false"}, clear=True
    ):
        os.environ["GEMINI_API_KEY"] = ""

        with pytest.raises(ValidationError) as exc:
            Settings()
        assert "GEMINI_API_KEY is required" in str(exc.value)


def test_config_vertex_ai_true_valid():
    """Test configuration when using Vertex AI with required fields."""
    with mock.patch.dict(
        os.environ,
        {
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        },
        clear=True,
    ):
        settings = Settings()
        assert settings.google_genai_use_vertexai is True
        assert settings.google_cloud_project == "test-project"
        assert settings.google_cloud_location == "us-central1"


def test_config_vertex_ai_true_missing_project():
    """Test failure when using Vertex AI without project."""
    with mock.patch.dict(
        os.environ,
        {"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_CLOUD_LOCATION": "us-central1"},
        clear=True,
    ):
        os.environ["GOOGLE_CLOUD_PROJECT"] = ""

        with pytest.raises(ValidationError) as exc:
            Settings()
        assert "GOOGLE_CLOUD_PROJECT is required" in str(exc.value)


def test_config_vertex_ai_true_missing_location():
    """Test failure when using Vertex AI without location."""
    with mock.patch.dict(
        os.environ,
        {
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "",
        },
        clear=True,
    ):
        with pytest.raises(ValidationError) as exc:
            Settings()
        assert "GOOGLE_CLOUD_LOCATION is required" in str(exc.value)
