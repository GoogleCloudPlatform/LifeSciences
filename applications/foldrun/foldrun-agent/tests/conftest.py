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

"""Shared test fixtures for AF2 agent tests."""

import os
from unittest.mock import MagicMock, patch

import pytest
from dotenv import dotenv_values

# Load test config from .env.test
_TEST_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env.test")
_TEST_ENV = dotenv_values(_TEST_ENV_PATH)

for key, value in _TEST_ENV.items():
    os.environ[key] = value


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set all required environment variables from tests/.env.test."""
    for key, value in _TEST_ENV.items():
        monkeypatch.setenv(key, value)
    return dict(_TEST_ENV)


@pytest.fixture
def mock_config(mock_env_vars):
    """Return a Config instance with mocked environment."""
    from foldrun_app.models.af2.config import Config

    return Config()


@pytest.fixture
def mock_vertex_ai():
    """Patch google.cloud.aiplatform module."""
    with patch("google.cloud.aiplatform") as mock_ai:
        mock_ai.init = MagicMock()
        yield mock_ai


@pytest.fixture
def mock_storage_client():
    """Patch google.cloud.storage.Client."""
    with patch("google.cloud.storage.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        yield mock_client
