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

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_gemini_client, get_storage_client
from api.main import app


@pytest.fixture
def mock_storage_client():
    return MagicMock()


@pytest.fixture
def mock_gemini_client():
    mock = MagicMock()
    mock.close = AsyncMock()
    mock.analyze_video = AsyncMock()
    mock.analyze_image = AsyncMock()
    mock.analyze_image_without_location = AsyncMock()
    mock.find_issue_location = AsyncMock()
    return mock


@pytest.fixture
def client(mock_gemini_client, mock_storage_client):
    # Override dependencies
    app.dependency_overrides[get_gemini_client] = lambda: mock_gemini_client
    app.dependency_overrides[get_storage_client] = lambda: mock_storage_client

    with TestClient(app) as c:
        yield c

    # Clear overrides after test
    app.dependency_overrides.clear()


@pytest.fixture
def mock_app_state(mock_gemini_client, mock_storage_client):
    """Fixture to mock app.state for unit tests that don't use TestClient."""
    state = MagicMock()
    state.gemini_client = mock_gemini_client
    state.storage_client = mock_storage_client
    return state
