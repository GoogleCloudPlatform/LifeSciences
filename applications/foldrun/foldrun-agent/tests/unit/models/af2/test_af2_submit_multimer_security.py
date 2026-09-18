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

"""Security tests for AF2SubmitMultimerTool local file path validation (CWE-22)."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from foldrun_app.models.af2.tools.submit_multimer import AF2SubmitMultimerTool
from foldrun_app.models.af2.utils.fasta_utils import FastaValidationError


class _UploadReached(Exception):
    """Sentinel raised when _upload_to_gcs is reached."""


def _make_tool() -> AF2SubmitMultimerTool:
    mock_config = MagicMock()
    mock_config.project_id = "test-project"
    mock_config.region = "us-central1"
    mock_config.bucket_name = "test-bucket"
    mock_config.filestore_ip = "10.0.0.1"
    mock_config.filestore_network = "projects/test-project/global/networks/default"
    mock_config.pipelines_sa_email = "sa@test-project.iam.gserviceaccount.com"
    tool_config = {"name": "submit_af2_multimer_prediction", "description": "test"}
    with patch("google.cloud.storage.Client"):
        tool = AF2SubmitMultimerTool(tool_config=tool_config, config=mock_config)
    tool._upload_to_gcs = MagicMock(side_effect=_UploadReached("uploaded to GCS"))
    return tool


class TestAF2SubmitMultimerSecurity:
    """Tests preventing arbitrary local file read/exfiltration in AF2SubmitMultimerTool."""

    def test_rejects_local_file_outside_temp_directory(self):
        """Passing a local file path outside the designated temp directory must raise ValueError."""
        tool = _make_tool()
        outside_dir = os.path.dirname(os.path.abspath(__file__))
        secret_file = os.path.join(outside_dir, "_test_secret_outside_tmp.fasta")
        try:
            with open(secret_file, "w") as f:
                f.write(">chainA\nACDEFGHIKLMNPQRSTVWY\n>chainB\nACDEFGHIKLMNPQRSTVWY\n")

            with pytest.raises(ValueError, match="Local file path must be within"):
                tool.run({"sequence": secret_file, "job_name": "test_job"})

            tool._upload_to_gcs.assert_not_called()
        finally:
            if os.path.exists(secret_file):
                os.remove(secret_file)

    def test_rejects_path_traversal_from_temp_directory(self):
        """Path traversal escaping the temp directory via '..' must be rejected."""
        tool = _make_tool()
        outside_dir = os.path.dirname(os.path.abspath(__file__))
        secret_file = os.path.join(outside_dir, "_test_traversal_secret.fasta")
        try:
            with open(secret_file, "w") as f:
                f.write(">chainA\nACDEFGHIKLMNPQRSTVWY\n>chainB\nACDEFGHIKLMNPQRSTVWY\n")

            traversal_path = os.path.join(tempfile.gettempdir(), "..", secret_file.lstrip("/"))
            with pytest.raises(ValueError, match="Local file path must be within"):
                tool.run({"sequence": traversal_path, "job_name": "test_job"})

            tool._upload_to_gcs.assert_not_called()
        finally:
            if os.path.exists(secret_file):
                os.remove(secret_file)

    def test_enforces_amino_acid_validation_on_local_file(self):
        """A file inside temp dir with non-amino-acid content must fail sequence validation."""
        tool = _make_tool()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".fasta", delete=False) as tf:
            tf.write(">chain1\nSECRET_API_KEY_1234567890\n>chain2\nANOTHER_SECRET_VALUE_98765\n")
            temp_path = tf.name

        try:
            with pytest.raises(FastaValidationError, match="Invalid amino acid characters"):
                tool.run({"sequence": temp_path, "job_name": "test_job"})

            tool._upload_to_gcs.assert_not_called()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_allows_valid_multimer_fasta_in_temp_directory(self):
        """A valid multimer FASTA file inside the temp directory passes validation."""
        tool = _make_tool()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".fasta", delete=False) as tf:
            tf.write(">chainA\nACDEFGHIKLMNPQRSTVWY\n>chainB\nACDEFGHIKLMNPQRSTVWY\n")
            temp_path = tf.name

        try:
            with pytest.raises(_UploadReached):
                tool.run({"sequence": temp_path, "job_name": "test_job"})

            tool._upload_to_gcs.assert_called_once_with(
                os.path.realpath(temp_path), "gs://test-bucket/fasta/test_job.fasta"
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
