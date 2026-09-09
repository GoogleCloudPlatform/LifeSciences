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

"""Tests for AF3Config class."""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "AF3_ENDPOINT": "projects/test-project/locations/us-central1/endpoints/123456789",
        "AF3_ENDPOINT_LOCATION": "us-central1",
        "AF3_DEFAULT_MSA_FREE": "true",
        "AF3_TIMEOUT_SECONDS": "1200",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestAF3Config:
    """Tests for AF3Config initialization and properties."""

    def test_config_loads_from_env(self):
        from foldrun_app.models.af3.config import AF3Config

        config = AF3Config()
        assert config.project_id == "test-project"
        assert config.region == "us-central1"
        assert config.bucket_name == "test-bucket"
        assert (
            config.endpoint_id == "projects/test-project/locations/us-central1/endpoints/123456789"
        )
        assert config.endpoint_location == "us-central1"
        assert config.default_msa_free is True
        assert config.timeout_seconds == 1200

    def test_config_missing_endpoint(self, monkeypatch):
        monkeypatch.delenv("AF3_ENDPOINT", raising=False)
        monkeypatch.delenv("AF3_AGENT_PLATFORM_ENDPOINT", raising=False)
        monkeypatch.delenv("AF3_ENDPOINT_ID", raising=False)
        monkeypatch.delenv("AF3_VERTEX_ENDPOINT", raising=False)
        monkeypatch.setattr("foldrun_app.core.config.load_dotenv", lambda *a, **kw: None)
        from foldrun_app.models.af3.config import AF3Config

        with pytest.raises(ValueError, match="Missing required environment variables"):
            AF3Config()

    def test_config_endpoint_fallbacks(self, monkeypatch):
        monkeypatch.delenv("AF3_ENDPOINT", raising=False)
        monkeypatch.delenv("AF3_AGENT_PLATFORM_ENDPOINT", raising=False)
        monkeypatch.delenv("AF3_ENDPOINT_ID", raising=False)
        monkeypatch.delenv("AF3_VERTEX_ENDPOINT", raising=False)
        from foldrun_app.models.af3.config import AF3Config

        # Test AF3_AGENT_PLATFORM_ENDPOINT fallback
        monkeypatch.setenv("AF3_AGENT_PLATFORM_ENDPOINT", "endpoint-agent-platform")
        config = AF3Config()
        assert config.endpoint_id == "endpoint-agent-platform"

        # Test AF3_ENDPOINT priority over AF3_AGENT_PLATFORM_ENDPOINT
        monkeypatch.setenv("AF3_ENDPOINT", "endpoint-canonical")
        config = AF3Config()
        assert config.endpoint_id == "endpoint-canonical"

        # Test legacy AF3_ENDPOINT_ID fallback
        monkeypatch.delenv("AF3_ENDPOINT", raising=False)
        monkeypatch.delenv("AF3_AGENT_PLATFORM_ENDPOINT", raising=False)
        monkeypatch.setenv("AF3_ENDPOINT_ID", "endpoint-legacy-id")
        config = AF3Config()
        assert config.endpoint_id == "endpoint-legacy-id"

    def test_config_to_dict(self):
        from foldrun_app.models.af3.config import AF3Config

        config = AF3Config()
        d = config.to_dict()
        assert "endpoint_id" in d
        assert "endpoint_location" in d
        assert "default_msa_free" in d
        assert "timeout_seconds" in d
        assert d["endpoint_id"] == "projects/test-project/locations/us-central1/endpoints/123456789"
