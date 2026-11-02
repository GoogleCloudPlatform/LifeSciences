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
                "model": "gemini-4-argon",
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
                "model": "gemini-4-argon",
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

    @patch.object(shared_utils.storage, "Client")
    def test_download_json_from_gcs_uses_download_as_text(self, mock_storage_cls):
        """Verify download_json_from_gcs calls download_as_text() instead of removed download_as_string()."""
        mock_blob = MagicMock()
        mock_blob.download_as_string.side_effect = AttributeError(
            "'Blob' object has no attribute 'download_as_string'"
        )
        mock_blob.download_as_text.return_value = (
            '{"job_id": "job-123", "status": "ok"}'
        )
        mock_storage_cls.return_value.bucket.return_value.blob.return_value = mock_blob

        data = shared_utils.download_json_from_gcs(
            "gs://test-bucket/analysis/summary.json"
        )
        assert data == {"job_id": "job-123", "status": "ok"}
        mock_blob.download_as_text.assert_called_once_with()
        mock_blob.download_as_string.assert_not_called()


class TestSharedUtilsFixes:
    """Regression tests for NaN sanitization, GCS URI validation, zstd cap, and mmCIF parsing."""

    def test_sanitize_data_replaces_nan_inf_with_none(self):
        import numpy as np

        raw = {
            "a": float("nan"),
            "b": float("inf"),
            "c": float("-inf"),
            "d": np.float32("nan"),
            "e": [1.0, float("nan"), 92.5],
        }
        cleaned = shared_utils._sanitize_json_floats(raw)
        assert cleaned == {
            "a": None,
            "b": None,
            "c": None,
            "d": None,
            "e": [1.0, None, 92.5],
        }

    def test_calculate_plddt_stats_empty_and_all_nan_never_fabricates(self):
        empty_stats = shared_utils.calculate_plddt_stats([])
        assert empty_stats["mean"] is None
        assert empty_stats["min"] is None
        assert empty_stats["max"] is None
        assert empty_stats["confidence_category"] == "uncomputed"

        nan_stats = shared_utils.calculate_plddt_stats([float("nan"), None])
        assert nan_stats["mean"] is None
        assert nan_stats["confidence_category"] == "uncomputed"
        assert shared_utils.get_quality_assessment(None) == "uncomputed"

    def test_validate_gcs_uri_in_bucket_preserves_s_and_g_bucket_chars(self):
        import pytest

        bucket, obj = shared_utils.validate_gcs_uri_in_bucket(
            "gs://storage-bucket/analysis/summary.json",
            expected_bucket="storage-bucket",
        )
        assert bucket == "storage-bucket"
        assert obj == "analysis/summary.json"

        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            shared_utils.validate_gcs_uri_in_bucket(
                "gs://other-bucket/analysis/summary.json",
                expected_bucket="storage-bucket",
            )

        with pytest.raises(ValueError, match="Path traversal detected"):
            shared_utils.validate_gcs_uri_in_bucket(
                "gs://storage-bucket/analysis/../secret.json",
                expected_bucket="storage-bucket",
            )

    def test_parse_cif_chains_headerless_18col_mmcif_and_fixed_pdb_and_nan_bfactor(
        self,
    ):
        import pytest

        mmcif_18 = (
            "ATOM 1 N N . MET A 1 1 ? 10.123 20.456 30.789 1.00 88.50 ? 1 MET A N 1\n"
            "ATOM 2 CA C . MET A 1 1 ? 11.123 21.456 31.789 1.00 nan ? 1 MET A CA 1\n"
            "ATOM 3 N N . GLY B 2 1 ? 12.123 22.456 32.789 1.00 76.25 ? 1 GLY B N 1\n"
        )
        chains, plddts = shared_utils.parse_cif_chains(mmcif_18, extract_plddt=True)
        assert [c["chain_id"] for c in chains] == ["A", "B"]
        assert chains[0]["atom_count"] == 2
        assert chains[0]["residue_count"] == 1
        assert chains[1]["atom_count"] == 1
        assert plddts == [pytest.approx(88.50), None, pytest.approx(76.25)]

        pdb_text = (
            "ATOM      1  N   MET A   1      10.123  20.456  30.789  1.00 91.20           N\n"
            "ATOM      2  CA  MET A   1      11.123  21.456  31.789  1.00 93.40           C\n"
        )
        pdb_chains, pdb_plddts = shared_utils.parse_cif_chains(
            pdb_text, extract_plddt=True
        )
        assert len(pdb_chains) == 1
        assert pdb_chains[0]["chain_id"] == "A"
        assert pdb_chains[0]["residue_count"] == 1
        assert pdb_plddts == [pytest.approx(91.20), pytest.approx(93.40)]

    def test_decompress_zst_bytes_roundtrip_and_size_cap(self):
        import pytest
        import zstandard as zstd

        payload = b'{"iptm": null, "ptm": 0.89, "ranking_score": 0.89}'
        compressed = zstd.ZstdCompressor().compress(payload)
        assert shared_utils.decompress_zst_bytes(compressed) == payload

        big_compressed = zstd.ZstdCompressor().compress(b"A" * 4096)
        with pytest.raises(ValueError, match="exceeds safety limit"):
            shared_utils.decompress_zst_bytes(big_compressed, max_bytes=1024)
