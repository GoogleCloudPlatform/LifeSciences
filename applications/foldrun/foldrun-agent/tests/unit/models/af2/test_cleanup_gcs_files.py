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

"""Unit tests for AF2CleanupGCSFilesTool security validations."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_cleanup_tool_class():
    """Load AF2CleanupGCSFilesTool with lightweight stubs if run outside full venv."""
    try:
        from foldrun_app.models.af2.tools.cleanup_gcs_files import AF2CleanupGCSFilesTool

        return AF2CleanupGCSFilesTool
    except ImportError:
        # Provide minimal stubs for google.cloud.storage, base tool, and vertex_utils
        for mod_name in [
            "google",
            "google.cloud",
            "google.cloud.storage",
            "foldrun_app",
            "foldrun_app.models",
            "foldrun_app.models.af2",
            "foldrun_app.models.af2.base",
            "foldrun_app.models.af2.utils",
            "foldrun_app.models.af2.utils.vertex_utils",
            "foldrun_app.models.af2.tools",
        ]:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = types.ModuleType(mod_name)

        class _StubAF2Tool:
            def __init__(self, tool_config=None, config=None):
                self.config = config

        sys.modules["google.cloud"].storage = sys.modules["google.cloud.storage"]
        sys.modules["google.cloud.storage"].Client = MagicMock()
        sys.modules["foldrun_app.models.af2.base"].AF2Tool = _StubAF2Tool
        sys.modules["foldrun_app.models.af2.utils.vertex_utils"].get_pipeline_job = MagicMock()

        src_path = (
            Path(__file__).resolve().parents[4]
            / "foldrun_app"
            / "models"
            / "af2"
            / "tools"
            / "cleanup_gcs_files.py"
        )
        spec = importlib.util.spec_from_file_location(
            "foldrun_app.models.af2.tools.cleanup_gcs_files", src_path
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["foldrun_app.models.af2.tools.cleanup_gcs_files"] = mod
        spec.loader.exec_module(mod)
        return mod.AF2CleanupGCSFilesTool


AF2CleanupGCSFilesTool = _load_cleanup_tool_class()


class TestAF2CleanupGCSFilesSecurity(unittest.TestCase):
    """Tests for CWE-284 (_delete_paths) and CWE-20 (_cleanup_job) remediations."""

    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.project_id = "test-project"
        self.mock_config.region = "us-central1"
        self.mock_config.bucket_name = "test-af2-bucket"
        with patch("foldrun_app.core.base_tool._ensure_clients"):
            self.tool = AF2CleanupGCSFilesTool(
                tool_config={"name": "cleanup_gcs_files"}, config=self.mock_config
            )

    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.storage.Client")
    def test_delete_paths_rejects_mismatched_bucket(self, mock_storage_client):
        """Finding 3.2/4.16: Reject GCS paths targeting a different bucket."""
        with self.assertRaises(ValueError):
            self.tool._delete_paths(
                ["gs://wrong-bucket/pipeline_runs/20260101_000000/"],
                search_only=True,
                confirm_delete=False,
            )

    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.storage.Client")
    def test_delete_paths_rejects_empty_prefix_or_root_bucket(self, mock_storage_client):
        """Finding 3.2/4.16: Reject root bucket paths that expand to empty prefix."""
        for root_path in [
            "gs://test-af2-bucket/",
            "gs://test-af2-bucket",
            "gs://test-af2-bucket///",
        ]:
            with self.assertRaises(ValueError, msg=f"Should reject {root_path}"):
                self.tool._delete_paths(
                    [root_path],
                    search_only=True,
                    confirm_delete=False,
                )

    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.storage.Client")
    def test_delete_paths_enforces_base_prefix_allowlist(self, mock_storage_client):
        """Finding 3.2/4.16: Reject paths outside allowlisted prefixes or root base dirs."""
        for disallowed_path in [
            "gs://test-af2-bucket/databases/uniref90.fasta",
            "gs://test-af2-bucket/params/params_model_1.npz",
            "gs://test-af2-bucket/pipeline_runs/",
            "gs://test-af2-bucket/fasta/",
            "gs://test-af2-bucket/pipeline_runs/../databases/uniref90.fasta",
        ]:
            with self.assertRaises(ValueError, msg=f"Should reject {disallowed_path}"):
                self.tool._delete_paths(
                    [disallowed_path],
                    search_only=True,
                    confirm_delete=False,
                )

    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.storage.Client")
    def test_delete_paths_allows_valid_pipeline_runs_and_fasta_paths(self, mock_storage_client):
        """Valid paths under pipeline_runs/ and fasta/ succeed."""
        mock_bucket = MagicMock()
        mock_storage_client.return_value.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_blob.name = "fasta/job_1234.fasta"
        mock_blob.size = 1024
        mock_blob.exists.return_value = True
        mock_bucket.blob.return_value = mock_blob

        result = self.tool._delete_paths(
            ["gs://test-af2-bucket/fasta/job_1234.fasta"],
            search_only=True,
            confirm_delete=False,
        )
        self.assertEqual(result["files_found"], 1)

    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.get_pipeline_job")
    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.storage.Client")
    def test_cleanup_job_rejects_generic_and_short_job_ids(self, mock_storage_client, mock_get_job):
        """Finding 4.17: Reject generic/short job_ids like 'pipeline', 'runs', '/', '_'."""
        mock_get_job.side_effect = RuntimeError("Not found")
        for invalid_job_id in ["pipeline", "runs", "/", "_", "a", "pipeline_runs", "fasta"]:
            with self.assertRaises(ValueError, msg=f"Should reject job_id={invalid_job_id!r}"):
                self.tool._cleanup_job(
                    job_id=invalid_job_id,
                    search_only=True,
                    confirm_delete=False,
                    include_fasta=False,
                )

    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.get_pipeline_job")
    @patch("foldrun_app.models.af2.tools.cleanup_gcs_files.storage.Client")
    def test_cleanup_job_matches_path_segments_not_unanchored_substring(
        self, mock_storage_client, mock_get_job
    ):
        """Finding 4.17: Match job_name against specific path segments, not arbitrary substrings."""
        mock_get_job.side_effect = RuntimeError("Not found")
        mock_bucket = MagicMock()
        mock_storage_client.return_value.bucket.return_value = mock_bucket

        prefix_iter = MagicMock()
        prefix_iter.prefixes = ["pipeline_runs/20260101_120000/"]

        unrelated_blob = MagicMock()
        unrelated_blob.name = "pipeline_runs/20260101_120000/prefix_job_alpha_suffix/output.pdb"
        unrelated_blob.size = 2048
        unrelated_blob.updated = None

        exact_segment_blob = MagicMock()
        exact_segment_blob.name = "pipeline_runs/20260101_120000/job_alpha/output.pdb"
        exact_segment_blob.size = 1024
        exact_segment_blob.updated = None

        def list_blobs_side_effect(prefix=None, delimiter=None):
            if delimiter == "/":
                return prefix_iter
            return [unrelated_blob, exact_segment_blob]

        mock_bucket.list_blobs.side_effect = list_blobs_side_effect
        mock_fasta_blob = MagicMock()
        mock_fasta_blob.exists.return_value = False
        mock_bucket.blob.return_value = mock_fasta_blob

        result = self.tool._cleanup_job(
            job_id="job_alpha",
            search_only=True,
            confirm_delete=False,
            include_fasta=False,
        )
        matched_paths = [f["path"] for f in result["files_found"]["pipeline_runs"]]
        self.assertEqual(
            matched_paths,
            ["gs://test-af2-bucket/pipeline_runs/20260101_120000/job_alpha/output.pdb"],
        )


if __name__ == "__main__":
    unittest.main()
