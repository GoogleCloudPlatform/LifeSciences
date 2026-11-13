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
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "ALPHAFOLD_COMPONENTS_IMAGE": "test-af2-image:latest",
        "FOLDRUN_VIEWER_URL": "https://viewer.example.com",
        "AF2_PARALLELISM": "5",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestAF2Config:
    """Tests for AF2 Config initialization and properties."""

    def test_config_loads_from_env(self):
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.project_id == "test-project"
        assert config.region == "us-central1"
        assert config.base_image == "test-af2-image:latest"

    def test_config_bucket_name(self):
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.bucket_name == "test-bucket"

    def test_config_viewer_url(self):
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.viewer_url == "https://viewer.example.com"

    def test_config_viewer_url_defaults_to_empty(self, monkeypatch):
        monkeypatch.delenv("FOLDRUN_VIEWER_URL", raising=False)
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.viewer_url == ""

    def test_config_parallelism(self):
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.parallelism == 5

    def test_config_parallelism_default(self, monkeypatch):
        monkeypatch.delenv("AF2_PARALLELISM", raising=False)
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.parallelism == 5

    def test_config_missing_image_raises(self, monkeypatch):
        monkeypatch.delenv("ALPHAFOLD_COMPONENTS_IMAGE", raising=False)
        monkeypatch.setattr("foldrun_app.core.config.load_dotenv", lambda *a, **kw: None)
        from foldrun_app.models.af2.config import Config

        with pytest.raises(ValueError, match="Missing required environment variables"):
            Config()

    def test_config_to_dict(self):
        from foldrun_app.models.af2.config import Config

        config = Config()
        d = config.to_dict()
        assert "base_image" in d
        assert "viewer_url" in d
        assert d["base_image"] == "test-af2-image:latest"
        assert d["project_id"] == "test-project"

    def test_config_supported_gpus_default(self):
        from foldrun_app.models.af2.config import Config

        config = Config()
        # Should include at least A100 by default
        assert "A100" in config.supported_gpus

    def test_dockerfile_uses_miniforge_and_pins_mmseqs2_with_sha256(self):
        """Ensure alphafold-components Dockerfile avoids Anaconda TOS and verifies MMseqs2/Miniforge SHA-256."""
        dockerfile_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "..",
            "..",
            "src",
            "alphafold-components",
            "Dockerfile",
        )
        with open(dockerfile_path, encoding="utf-8") as f:
            content = f.read()

        assert "repo.anaconda.com" not in content
        assert "CONDA_PLUGINS_AUTO_ACCEPT_TOS" not in content
        assert "mmseqs.com/latest" not in content
        assert "conda-forge/miniforge/releases/download/" in content
        assert "--override-channels --channel conda-forge" in content
        assert "ARG MMSEQS2_VERSION=18-8cc5c" in content
        assert (
            "soedinglab/MMseqs2/releases/download/${MMSEQS2_VERSION}/mmseqs-linux-gpu.tar.gz"
            in content
        )
        assert content.count("sha256sum -c -") >= 2
