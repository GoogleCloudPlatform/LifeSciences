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

"""Tests for RFD4ProteinaConfig class."""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "RFD4_PROTEINA_COMPONENTS_IMAGE": "test-rfd4-image:stable",
        "RFD4_PROTEINA_CACHE_PATH": "rfd4_proteina/cache",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestRFD4ProteinaConfig:
    """Tests for RFD4ProteinaConfig initialization and properties."""

    def test_config_loads_from_env(self):
        from foldrun_app.models.rfd4_proteina.config import RFD4ProteinaConfig

        config = RFD4ProteinaConfig()
        assert config.project_id == "test-project"
        assert config.region == "us-central1"
        assert config.base_image == "test-rfd4-image:stable"

    def test_config_cache_path(self):
        from foldrun_app.models.rfd4_proteina.config import RFD4ProteinaConfig

        config = RFD4ProteinaConfig()
        assert config.cache_path == "rfd4_proteina/cache"

    def test_config_missing_image(self, monkeypatch):
        monkeypatch.delenv("RFD4_PROTEINA_COMPONENTS_IMAGE", raising=False)
        monkeypatch.setattr("foldrun_app.core.config.load_dotenv", lambda *a, **kw: None)
        from foldrun_app.models.rfd4_proteina.config import RFD4ProteinaConfig

        with pytest.raises(ValueError, match="Missing required environment variables"):
            RFD4ProteinaConfig()

    def test_config_default_cache_path(self, monkeypatch):
        monkeypatch.delenv("RFD4_PROTEINA_CACHE_PATH", raising=False)
        from foldrun_app.models.rfd4_proteina.config import RFD4ProteinaConfig

        config = RFD4ProteinaConfig()
        assert config.cache_path == "rfd4_proteina/cache"

    def test_config_to_dict(self):
        from foldrun_app.models.rfd4_proteina.config import RFD4ProteinaConfig

        config = RFD4ProteinaConfig()
        d = config.to_dict()
        assert d["base_image"] == "test-rfd4-image:stable"
        assert d["cache_path"] == "rfd4_proteina/cache"
