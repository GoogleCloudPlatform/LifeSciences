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

"""Unit tests for AF2FindOrphanedGCSFilesTool."""

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

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


def _make_mock_blob(name: str, size: int = 1024):
    blob = MagicMock()
    blob.name = name
    blob.size = size
    blob.updated = datetime(2026, 1, 1, tzinfo=UTC)
    return blob


def _make_mock_job(display_name: str, gcs_output_directory: str | None = None):
    job = MagicMock()
    job.display_name = display_name
    if gcs_output_directory:
        job.runtime_config.gcs_output_directory = gcs_output_directory
    else:
        job.runtime_config = None
    return job


class TestFindOrphanedGCSFilesTool:
    """Tests for FindOrphanedGCSFilesTool FASTA filename parsing."""

    def test_fasta_filename_with_inner_fasta_or_prefix_not_flagged_as_orphaned(self):
        from foldrun_app.core.config import CoreConfig
        from foldrun_app.core.tools.find_orphaned_gcs_files import (
            FindOrphanedGCSFilesTool,
        )

        config = CoreConfig()
        tool = FindOrphanedGCSFilesTool(
            tool_config={"name": "find_orphaned_gcs_files"}, config=config
        )

        active_jobs = [
            _make_mock_job("protein.fasta-v2"),
            _make_mock_job("fasta/custom-job"),
            _make_mock_job("standard-job"),
        ]

        fasta_blobs = [
            _make_mock_blob("fasta/protein.fasta-v2.fasta"),
            _make_mock_blob("fasta/fasta/custom-job.fasta"),
            _make_mock_blob("fasta/standard-job.fasta"),
            _make_mock_blob("fasta/truly-orphaned.fasta"),
        ]

        mock_pipeline_runs_iter = MagicMock()
        mock_pipeline_runs_iter.__iter__.return_value = iter([])
        mock_pipeline_runs_iter.prefixes = set()

        def list_blobs_side_effect(prefix=None, delimiter=None):
            if prefix == "pipeline_runs/":
                return mock_pipeline_runs_iter
            if prefix == "fasta/":
                return iter(fasta_blobs)
            return iter([])

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.side_effect = list_blobs_side_effect

        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with (
            patch(
                "foldrun_app.core.tools.find_orphaned_gcs_files.list_pipeline_jobs",
                return_value=active_jobs,
            ),
            patch(
                "foldrun_app.core.tools.find_orphaned_gcs_files.storage.Client",
                return_value=mock_storage_client,
            ),
        ):
            result = tool.run({"check_fasta": True})

        orphaned_job_names = [f["job_name"] for f in result["orphaned_fasta_files"]]
        assert orphaned_job_names == ["truly-orphaned"]

    def test_detects_orphaned_of3_and_boltz2_query_files(self):
        from foldrun_app.core.config import CoreConfig
        from foldrun_app.core.tools.find_orphaned_gcs_files import (
            FindOrphanedGCSFilesTool,
        )

        config = CoreConfig()
        tool = FindOrphanedGCSFilesTool(
            tool_config={"name": "find_orphaned_gcs_files"}, config=config
        )

        active_of3 = _make_mock_job("active-of3-job")
        active_boltz = _make_mock_job("active-boltz-job")

        mock_pipeline_runs_iter = MagicMock()
        mock_pipeline_runs_iter.__iter__.return_value = iter([])
        mock_pipeline_runs_iter.prefixes = set()

        def list_blobs_side_effect(prefix=None, delimiter=None):
            if prefix == "pipeline_runs/":
                return mock_pipeline_runs_iter
            if prefix == "fasta/":
                return iter([])
            if prefix == "of3_queries/":
                return iter(
                    [
                        _make_mock_blob("of3_queries/active-of3-job.json", size=500),
                        _make_mock_blob("of3_queries/orphaned-of3-job.json", size=600),
                    ]
                )
            if prefix == "boltz2_queries/":
                return iter(
                    [
                        _make_mock_blob("boltz2_queries/active-boltz-job.yaml", size=700),
                        _make_mock_blob("boltz2_queries/orphaned-boltz-job.yaml", size=800),
                    ]
                )
            return iter([])

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.side_effect = list_blobs_side_effect
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with (
            patch(
                "foldrun_app.core.tools.find_orphaned_gcs_files.list_pipeline_jobs",
                return_value=[active_of3, active_boltz],
            ),
            patch(
                "foldrun_app.core.tools.find_orphaned_gcs_files.storage.Client",
                return_value=mock_storage_client,
            ),
        ):
            result = tool.run({"check_fasta": True})

        assert [f["job_name"] for f in result["orphaned_of3_queries"]] == ["orphaned-of3-job"]
        assert [f["job_name"] for f in result["orphaned_boltz2_queries"]] == ["orphaned-boltz-job"]
        assert result["summary"]["orphaned_of3_queries_count"] == 1
        assert result["summary"]["orphaned_boltz2_queries_count"] == 1
