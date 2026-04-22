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

"""Tests for OF3Config class."""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "OPENFOLD3_COMPONENTS_IMAGE": "test-of3-image:stable",
        "OF3_PARAMS_PATH": "of3/params",
        "OF3_CCD_PATH": "of3/ccd",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestOF3Config:
    """Tests for OF3Config initialization and properties."""

    def test_config_loads_from_env(self):
        from foldrun_app.models.of3.config import OF3Config

        config = OF3Config()
        assert config.project_id == "test-project"
        assert config.region == "us-central1"
        assert config.base_image == "test-of3-image:stable"

    def test_config_params_path(self):
        from foldrun_app.models.of3.config import OF3Config

        config = OF3Config()
        assert config.params_path == "of3/params"

    def test_config_ccd_path(self):
        from foldrun_app.models.of3.config import OF3Config

        config = OF3Config()
        assert config.ccd_path == "of3/ccd"

    def test_config_missing_image(self, monkeypatch):
        monkeypatch.delenv("OPENFOLD3_COMPONENTS_IMAGE", raising=False)
        monkeypatch.setattr("foldrun_app.core.config.load_dotenv", lambda *a, **kw: None)
        from foldrun_app.models.of3.config import OF3Config

        with pytest.raises(ValueError, match="Missing required environment variables"):
            OF3Config()

    def test_config_default_params_path(self, monkeypatch):
        monkeypatch.delenv("OF3_PARAMS_PATH", raising=False)
        from foldrun_app.models.of3.config import OF3Config

        config = OF3Config()
        assert config.params_path == "of3/params/of3_ft3_v1.pt"

    def test_config_to_dict(self):
        from foldrun_app.models.of3.config import OF3Config

        config = OF3Config()
        d = config.to_dict()
        expected_keys = {
            "project_id",
            "region",
            "zone",
            "bucket_name",
            "base_image",
            "params_path",
            "ccd_path",
            "supported_gpus",
        }
        assert set(d.keys()) == expected_keys
        assert d["base_image"] == "test-of3-image:stable"
