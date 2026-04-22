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

"""Tests for BOLTZ2Config class."""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "BOLTZ2_COMPONENTS_IMAGE": "test-boltz2-image:stable",
        "BOLTZ2_CACHE_PATH": "boltz2/cache",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestBOLTZ2Config:
    """Tests for BOLTZ2Config initialization and properties."""

    def test_config_loads_from_env(self):
        from foldrun_app.models.boltz2.config import BOLTZ2Config

        config = BOLTZ2Config()
        assert config.project_id == "test-project"
        assert config.region == "us-central1"
        assert config.base_image == "test-boltz2-image:stable"

    def test_config_cache_path(self):
        from foldrun_app.models.boltz2.config import BOLTZ2Config

        config = BOLTZ2Config()
        assert config.cache_path == "boltz2/cache"

    def test_config_missing_image(self, monkeypatch):
        monkeypatch.delenv("BOLTZ2_COMPONENTS_IMAGE", raising=False)
        monkeypatch.setattr("foldrun_app.core.config.load_dotenv", lambda *a, **kw: None)
        from foldrun_app.models.boltz2.config import BOLTZ2Config

        with pytest.raises(ValueError, match="Missing required environment variables"):
            BOLTZ2Config()

    def test_config_default_cache_path(self, monkeypatch):
        monkeypatch.delenv("BOLTZ2_CACHE_PATH", raising=False)
        from foldrun_app.models.boltz2.config import BOLTZ2Config

        config = BOLTZ2Config()
        assert config.cache_path == "boltz2/cache"

    def test_config_to_dict(self):
        from foldrun_app.models.boltz2.config import BOLTZ2Config

        config = BOLTZ2Config()
        d = config.to_dict()
        expected_keys = {
            "project_id",
            "region",
            "zone",
            "bucket_name",
            "base_image",
            "cache_path",
            "supported_gpus",
        }
        assert set(d.keys()) == expected_keys
        assert d["base_image"] == "test-boltz2-image:stable"
