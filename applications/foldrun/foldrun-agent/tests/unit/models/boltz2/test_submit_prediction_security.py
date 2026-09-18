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

"""Security tests for BOLTZ2SubmitPredictionTool local file read protection (CWE-73)."""

import os
from unittest.mock import MagicMock, patch

import pytest

from foldrun_app.models.boltz2.tools.submit_prediction import BOLTZ2SubmitPredictionTool


def _make_tool() -> BOLTZ2SubmitPredictionTool:
    """Create a BOLTZ2SubmitPredictionTool instance with mocked GCP clients."""
    config = MagicMock()
    config.project_id = "test-project"
    config.region = "us-central1"
    config.bucket_name = "test-bucket"
    config.filestore_ip = "10.0.0.2"
    config.filestore_network = "projects/123/global/networks/default"
    config.nfs_share = "/datasets"
    config.nfs_mount_point = "/mnt/nfs/foldrun"
    config.cache_path = "boltz2/cache"
    config.pipelines_sa_email = "sa@test-project.iam.gserviceaccount.com"
    config.supported_gpus = ["A100", "A100_80GB"]
    config.dws_max_wait_hours = 168

    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = BOLTZ2SubmitPredictionTool({"name": "boltz2_submit"}, config=config)
    tool.storage_client = MagicMock()
    return tool


class TestBoltz2SubmitPredictionLocalFileSecurity:
    """Verify CWE-73 protection against arbitrary local file reads."""

    def test_rejects_arbitrary_local_file_when_allowed_dir_unset(self, tmp_path, monkeypatch):
        """Arbitrary local file read must be rejected when FOLDRUN_ALLOWED_INPUT_DIR is not set."""
        monkeypatch.delenv("FOLDRUN_ALLOWED_INPUT_DIR", raising=False)
        secret_file = tmp_path / "sensitive_secret.fasta"
        secret_file.write_text(">secret\nACDEFGHIKLMNPQRSTVWY\n")

        tool = _make_tool()
        with pytest.raises(ValueError, match="Local file input is not allowed"):
            tool.run({"input": str(secret_file)})

    def test_rejects_local_file_outside_authorized_directory(self, tmp_path, monkeypatch):
        """Local files outside FOLDRUN_ALLOWED_INPUT_DIR must be rejected."""
        allowed_dir = tmp_path / "authorized_inputs"
        allowed_dir.mkdir()
        outside_dir = tmp_path / "outside_secrets"
        outside_dir.mkdir()

        secret_file = outside_dir / "secret.fasta"
        secret_file.write_text(">secret\nACDEFGHIKLMNPQRSTVWY\n")

        monkeypatch.setenv("FOLDRUN_ALLOWED_INPUT_DIR", str(allowed_dir))
        tool = _make_tool()

        with pytest.raises(ValueError, match="outside the authorized directory"):
            tool.run({"input": str(secret_file)})

    def test_rejects_path_traversal_and_symlink_escape(self, tmp_path, monkeypatch):
        """Path traversal (../) and symlinks escaping FOLDRUN_ALLOWED_INPUT_DIR must be rejected."""
        allowed_dir = tmp_path / "authorized_inputs"
        allowed_dir.mkdir()
        outside_file = tmp_path / "secret.fasta"
        outside_file.write_text(">secret\nACDEFGHIKLMNPQRSTVWY\n")

        symlink_path = allowed_dir / "escape_link.fasta"
        os.symlink(outside_file, symlink_path)

        traversal_path = os.path.join(str(allowed_dir), "..", "secret.fasta")

        monkeypatch.setenv("FOLDRUN_ALLOWED_INPUT_DIR", str(allowed_dir))
        tool = _make_tool()

        with pytest.raises(ValueError, match="outside the authorized directory"):
            tool.run({"input": str(symlink_path)})

        with pytest.raises(ValueError, match="outside the authorized directory"):
            tool.run({"input": traversal_path})

    def test_allows_local_file_inside_authorized_directory(self, tmp_path, monkeypatch):
        """Local file inside FOLDRUN_ALLOWED_INPUT_DIR verified via os.path.realpath is permitted."""
        allowed_dir = tmp_path / "authorized_inputs"
        allowed_dir.mkdir()
        valid_file = allowed_dir / "valid_query.yaml"
        valid_file.write_text(
            "version: 1\nsequences:\n  - protein:\n      id: A\n      sequence: 123\n"
        )

        monkeypatch.setenv("FOLDRUN_ALLOWED_INPUT_DIR", str(allowed_dir))
        tool = _make_tool()

        result = tool.run({"input": str(valid_file)})
        assert result["status"] == "error"
        assert "Invalid Boltz-2 query YAML" in result["message"]
