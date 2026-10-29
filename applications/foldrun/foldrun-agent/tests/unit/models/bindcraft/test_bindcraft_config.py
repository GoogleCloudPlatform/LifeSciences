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

"""Tests for BINDCRAFTConfig class."""

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
        "BINDCRAFT_COMPONENTS_IMAGE": "test-bindcraft-image:stable",
        "AF2_PARAMS_PATH": "alphafold2/params",
        "BINDCRAFT_MPNN_WEIGHTS_PATH": "bindcraft/mpnn_weights",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestBINDCRAFTConfig:
    """Tests for BINDCRAFTConfig initialization and properties."""

    def test_config_loads_from_env(self):
        from foldrun_app.models.bindcraft.config import BINDCRAFTConfig

        config = BINDCRAFTConfig()
        assert config.project_id == "test-project"
        assert config.region == "us-central1"
        assert config.base_image == "test-bindcraft-image:stable"

    def test_config_paths(self):
        from foldrun_app.models.bindcraft.config import BINDCRAFTConfig

        config = BINDCRAFTConfig()
        assert config.af2_params_path == "alphafold2/params"
        assert config.mpnn_weights_path == "bindcraft/mpnn_weights"

    def test_config_missing_image(self, monkeypatch):
        monkeypatch.delenv("BINDCRAFT_COMPONENTS_IMAGE", raising=False)
        monkeypatch.setattr("foldrun_app.core.config.load_dotenv", lambda *a, **kw: None)
        from foldrun_app.models.bindcraft.config import BINDCRAFTConfig

        with pytest.raises(ValueError, match="Missing required environment variables"):
            BINDCRAFTConfig()

    def test_config_to_dict(self):
        from foldrun_app.models.bindcraft.config import BINDCRAFTConfig

        config = BINDCRAFTConfig()
        d = config.to_dict()
        assert d["base_image"] == "test-bindcraft-image:stable"
        assert d["af2_params_path"] == "alphafold2/params"
        assert d["mpnn_weights_path"] == "bindcraft/mpnn_weights"
