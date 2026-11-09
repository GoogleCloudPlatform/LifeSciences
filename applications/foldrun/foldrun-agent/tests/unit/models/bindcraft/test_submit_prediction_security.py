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

"""Security tests for BINDCRAFTSubmitDesignTool local file read protection (CWE-73)."""

from unittest.mock import MagicMock, patch

import pytest

from foldrun_app.models.bindcraft.tools.submit_prediction import (
    BINDCRAFTSubmitDesignTool,
)


def _make_tool() -> BINDCRAFTSubmitDesignTool:
    """Create a BINDCRAFTSubmitDesignTool instance with mocked GCP clients."""
    config = MagicMock()
    config.project_id = "test-project"
    config.region = "us-central1"
    config.bucket_name = "test-bucket"
    config.filestore_ip = "10.0.0.2"
    config.filestore_network = "projects/123/global/networks/default"
    config.nfs_share = "/datasets"
    config.nfs_mount_point = "/mnt/nfs/foldrun"
    config.af2_params_path = "alphafold2/params"
    config.mpnn_weights_path = "bindcraft/mpnn_weights"
    config.pipelines_sa_email = "sa@test-project.iam.gserviceaccount.com"
    config.supported_gpus = ["A100", "A100_80GB"]
    config.dws_max_wait_hours = 168

    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = BINDCRAFTSubmitDesignTool({"name": "bindcraft_submit"}, config=config)
    tool.storage_client = MagicMock()
    return tool


class TestBindCraftSubmitDesignLocalFileSecurity:
    """Verify CWE-73 protection against arbitrary local file reads."""

    def test_rejects_arbitrary_local_file_when_allowed_dir_unset(self, tmp_path, monkeypatch):
        """Arbitrary local file read must be rejected when FOLDRUN_ALLOWED_INPUT_DIR is not set."""
        monkeypatch.delenv("FOLDRUN_ALLOWED_INPUT_DIR", raising=False)
        secret_file = tmp_path / "sensitive_secret.json"
        secret_file.write_text('{"target_pdb": "target.pdb"}')

        tool = _make_tool()
        with pytest.raises(ValueError, match="Local file input is not allowed"):
            tool.run({"input": str(secret_file)})

    def test_rejects_local_file_outside_authorized_directory(self, tmp_path, monkeypatch):
        """Local files outside FOLDRUN_ALLOWED_INPUT_DIR must be rejected."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        monkeypatch.setenv("FOLDRUN_ALLOWED_INPUT_DIR", str(allowed_dir))

        outside_file = tmp_path / "outside.json"
        outside_file.write_text('{"target_pdb": "target.pdb"}')

        tool = _make_tool()
        with pytest.raises(ValueError, match="outside the authorized directory"):
            tool.run({"input": str(outside_file)})

    def test_rejects_empty_arguments(self):
        """Must return error if neither input nor target_structure is provided."""
        tool = _make_tool()
        result = tool.run({})
        assert result["status"] == "error"
        assert "Either 'target_structure' or 'input' must be provided" in result["message"]
