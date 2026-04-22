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

"""Tests for AF2 Config class."""

import os

import pytest
from dotenv import dotenv_values

from foldrun_app.models.af2.config import Config

# Load expected values from the same .env.test used by fixtures
_TEST_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env.test")
_EXPECTED = dotenv_values(_TEST_ENV_PATH)


class TestConfig:
    """Tests for Config initialization and properties."""

    def test_config_loads_from_env(self, mock_env_vars):
        """Valid env vars create Config successfully."""
        config = Config()
        assert config.project_id == _EXPECTED["GCP_PROJECT_ID"]
        assert config.region == _EXPECTED["GCP_REGION"]
        assert config.bucket_name == _EXPECTED["GCS_BUCKET_NAME"]

    def test_config_missing_required_vars(self, monkeypatch):
        """Missing required vars raises ValueError."""
        # Clear all required vars
        for var in [
            "GCP_PROJECT_ID",
            "GCP_REGION",
            "GCS_BUCKET_NAME",
            "FILESTORE_IP",
            "VERTEX_AI_NETWORK_ATTACHMENT",
            "ALPHAFOLD_COMPONENTS_IMAGE",
        ]:
            monkeypatch.delenv(var, raising=False)

        # Prevent load_dotenv() from restoring vars from a .env file
        monkeypatch.setattr("foldrun_app.core.config.load_dotenv", lambda *a, **kw: None)

        with pytest.raises(ValueError, match="Missing required environment variables"):
            Config()

    def test_config_default_values(self, mock_env_vars, monkeypatch):
        """Config uses correct defaults for optional vars."""
        monkeypatch.delenv("GCP_ZONE", raising=False)
        monkeypatch.delenv("NFS_SHARE", raising=False)
        monkeypatch.delenv("NFS_MOUNT_POINT", raising=False)

        config = Config()
        assert config.zone == "us-central1-a"
        assert config.nfs_share == "/datasets"
        assert config.nfs_mount_point == "/mnt/nfs/foldrun"

    def test_config_supported_gpus_default(self, mock_env_vars, monkeypatch):
        """Default supported GPUs list."""
        monkeypatch.delenv("AF2_SUPPORTED_GPUS", raising=False)
        config = Config()
        assert config.supported_gpus == ["L4", "A100", "A100_80GB"]

    def test_config_supported_gpus_custom(self, mock_env_vars, monkeypatch):
        """Custom AF2_SUPPORTED_GPUS env var."""
        monkeypatch.setenv("AF2_SUPPORTED_GPUS", "L4,A100")
        config = Config()
        assert config.supported_gpus == ["L4", "A100"]

    def test_config_set_supported_gpus(self, mock_env_vars):
        """set_supported_gpus() updates the GPU list."""
        config = Config()
        config.set_supported_gpus(["A100"])
        assert config.supported_gpus == ["A100"]

    def test_config_to_dict(self, mock_env_vars):
        """to_dict() returns expected keys."""
        config = Config()
        d = config.to_dict()
        expected_keys = {
            "project_id",
            "region",
            "zone",
            "bucket_name",
            "base_image",
            "viewer_url",
            "supported_gpus",
        }
        assert set(d.keys()) == expected_keys
        assert d["project_id"] == _EXPECTED["GCP_PROJECT_ID"]
        assert d["bucket_name"] == _EXPECTED["GCS_BUCKET_NAME"]
