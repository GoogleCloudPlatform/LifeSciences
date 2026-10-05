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

"""Tests for shared utility functions in shared_utils.py."""

import os
import sys
from datetime import UTC
from unittest.mock import MagicMock, patch

# Stub heavy imports BEFORE loading the module
_stubs = {
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
}
for name, stub in _stubs.items():
    sys.modules.setdefault(name, stub)

from foldrun_analysis import shared_utils  # noqa: E402


class TestGetJobMetadata:
    """Unit tests for get_job_metadata function."""

    @patch.dict(
        os.environ,
        {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "PIPELINE_JOB_LOCATION": "us-central1",
        },
    )
    @patch("google.cloud.aiplatform_v1.PipelineServiceClient")
    def test_get_job_metadata_success(self, mock_client_cls):
        """Verify that get_job_metadata successfully retrieves and formats pipeline job metadata."""
        from datetime import datetime

        # Create mock PipelineServiceClient instance
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Create mock PipelineJob
        mock_job = MagicMock()
        mock_job.display_name = "test-pipeline-job"
        mock_job.state.name = "PIPELINE_STATE_SUCCEEDED"
        mock_job.labels = {"key1": "val1", "query_name": "test_query"}

        # Create mock parameter value protobufs
        mock_param_str = MagicMock()
        mock_param_str.string_value = "gs://test-bucket/sequence.fasta"
        mock_param_str.number_value = None
        mock_param_str.bool_value = None

        mock_param_num = MagicMock()
        mock_param_num.string_value = None
        mock_param_num.number_value = 42
        mock_param_num.bool_value = None

        mock_job.runtime_config.parameter_values = {
            "sequence_path": mock_param_str,
            "num_predictions": mock_param_num,
        }

        # Mock time fields
        create_time = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        start_time = datetime(2026, 6, 9, 12, 5, 0, tzinfo=UTC)
        end_time = datetime(
            2026, 6, 9, 13, 17, 0, tzinfo=UTC
        )  # 1 hour 12 minutes (4320 seconds)

        mock_job.create_time = create_time
        mock_job.start_time = start_time
        mock_job.end_time = end_time

        mock_client.get_pipeline_job.return_value = mock_job

        # Call get_job_metadata
        metadata = shared_utils.get_job_metadata("test-job-id")

        # Verify PipelineServiceClient was instantiated and called correctly
        mock_client_cls.assert_called_once_with(
            client_options={"api_endpoint": "us-central1-aiplatform.googleapis.com"}
        )
        mock_client.get_pipeline_job.assert_called_once()

        # Assert correct metadata extraction
        assert metadata["display_name"] == "test-pipeline-job"
        assert metadata["state"] == "PIPELINE_STATE_SUCCEEDED"
        assert metadata["labels"] == {"key1": "val1", "query_name": "test_query"}
        assert metadata["parameters"] == {
            "sequence_path": "gs://test-bucket/sequence.fasta",
            "num_predictions": 42,
        }
        assert metadata["created"] == create_time.isoformat()
        assert metadata["started"] == start_time.isoformat()
        assert metadata["completed"] == end_time.isoformat()
        assert metadata["duration_seconds"] == 4320.0
        assert metadata["duration_formatted"] == "1h 12m"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_job_metadata_missing_project_id(self):
        """Verify that get_job_metadata returns empty metadata if GOOGLE_CLOUD_PROJECT is not set."""
        metadata = shared_utils.get_job_metadata("test-job-id")
        assert metadata == {"labels": {}, "parameters": {}}

    @patch.dict(
        os.environ,
        {
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "PIPELINE_JOB_LOCATION": "us-central1",
        },
    )
    @patch("google.cloud.aiplatform_v1.PipelineServiceClient")
    def test_get_job_metadata_exception(self, mock_client_cls):
        """Verify that get_job_metadata catches exceptions, logs them, and returns empty structures."""
        mock_client = MagicMock()
        mock_client.get_pipeline_job.side_effect = Exception(
            "Vertex AI connection error"
        )
        mock_client_cls.return_value = mock_client

        metadata = shared_utils.get_job_metadata("test-job-id")
        assert metadata == {"labels": {}, "parameters": {}}

    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True)
    def test_get_job_metadata_missing_location(self):
        """Verify that get_job_metadata returns empty metadata if PIPELINE_JOB_LOCATION is not set."""
        metadata = shared_utils.get_job_metadata("test-job-id")
        assert metadata == {"labels": {}, "parameters": {}}


class TestBuildAndUploadRunArchive:
    """Unit tests for compact ZIP archive creation in shared_utils.py."""

    def test_build_expert_analysis_markdown(self):
        """Verify standalone Markdown report includes job metadata, metrics, and Gemini analysis."""
        summary = {
            "job_id": "alphafold-inference-pipeline-20261002120000",
            "model_type": "alphafold2",
            "analyzed_at": "2026-10-02T12:00:00Z",
            "best_prediction": {
                "quality_assessment": "very_high_confidence",
                "model_name": "model_1_pred_0",
                "plddt_mean": 94.2,
            },
            "expert_analysis": {
                "model": "gemini-3.1-pro-preview",
                "analysis": "### Fold Quality\nHigh confidence alpha-helical bundle.",
            },
        }
        md = shared_utils.build_expert_analysis_markdown(summary)
        assert "# FoldRun Analysis Report:" in md
        assert "alphafold-inference-pipeline-20261002120000" in md
        assert "very_high_confidence" in md
        assert "High confidence alpha-helical bundle." in md

    @patch.object(shared_utils, "upload_to_gcs")
    @patch.object(shared_utils.storage, "Client")
    def test_build_and_upload_run_archive_creates_compact_zip(
        self, mock_storage_cls, mock_upload_to_gcs
    ):
        """Verify build_and_upload_run_archive packages structures, plots, metrics, and report into a compact zip."""
        import io
        import zipfile

        uploaded_files = {}

        def fake_upload_to_gcs(local_path, gcs_uri):
            with open(local_path, "rb") as f:
                uploaded_files[gcs_uri] = f.read()

        mock_upload_to_gcs.side_effect = fake_upload_to_gcs

        def fake_blob(blob_path):
            b = MagicMock()
            b.name = blob_path
            if blob_path.endswith("unrelaxed_protein.pdb"):
                b.exists.return_value = True
                b.download_as_bytes.return_value = b"HEADER MOCK PDB\nEND\n"
            elif blob_path.endswith(".png"):
                b.exists.return_value = True
                b.download_as_bytes.return_value = b"\x89PNG\r\n\x1a\n"
            else:
                b.exists.return_value = False
            return b

        mock_bucket = MagicMock()
        mock_bucket.blob.side_effect = fake_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_storage_cls.return_value = mock_client

        summary = {
            "job_id": "alphafold-inference-pipeline-20261002120000",
            "model_type": "alphafold2",
            "analyzed_at": "2026-10-02T12:00:00Z",
            "summary": {
                "protein_info": {
                    "fasta_header": "1FLD_A",
                    "fasta_sequence": "AELKVRDIFSYQ",
                },
                "quality_metrics": {
                    "quality_assessment": "very_high_confidence",
                    "best_model": "model_1_pred_0",
                    "best_model_plddt": 93.5,
                },
            },
            "all_predictions_summary": [
                {
                    "rank": 1,
                    "model_name": "model_1_pred_0",
                    "uri": "gs://test-bucket/pipeline_runs/20261002_120000/predict/model_1_pred_0/raw_prediction.pkl",
                    "plots": {
                        "plddt_plot": "gs://test-bucket/pipeline_runs/20261002_120000/analysis/plddt_plot_0.png",
                        "pae_plot": "gs://test-bucket/pipeline_runs/20261002_120000/analysis/pae_plot_0.png",
                    },
                }
            ],
            "expert_analysis": {
                "status": "success",
                "model": "gemini-3.1-pro-preview",
                "analysis": "Well-folded monomer.",
            },
        }

        bundle_uri = "gs://test-bucket/pipeline_runs/20261002_120000/analysis/artifacts_bundle.zip"
        meta = shared_utils.build_and_upload_run_archive(
            analysis_path="gs://test-bucket/pipeline_runs/20261002_120000/analysis/",
            bucket_name="test-bucket",
            summary_data=summary,
        )

        assert meta["artifacts_bundle_uri"] == bundle_uri
        assert meta["artifacts_bundle_size_bytes"] > 0
        assert bundle_uri in uploaded_files

        zip_bytes = uploaded_files[bundle_uri]
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())
            assert "README.md" in names
            assert "report/expert_analysis.md" in names
            assert "metrics/summary.json" in names
            assert "input/sequence.fasta" in names
            assert "structures/rank_01_model_1_pred_0_unrelaxed.pdb" in names
            assert "plots/rank_01_model_1_pred_0_plddt.png" in names
            assert "plots/rank_01_model_1_pred_0_pae.png" in names
            # Ensure raw matrices (.pkl/.npz) are excluded from the compact bundle
            assert not any(n.endswith((".pkl", ".npz")) for n in names)
