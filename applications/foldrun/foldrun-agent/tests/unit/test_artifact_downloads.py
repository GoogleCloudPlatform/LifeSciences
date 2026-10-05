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

"""Unit tests for isolated artifact downloads (b/568818894) in foldrun-agent."""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from foldrun_app.core import download_utils


class TestDownloadUtils:
    """Tests for V4 Signed URL generation, archive bundling, and download_job_artifacts."""

    def test_default_expiration_is_60_minutes(self):
        """Default V4 Signed URL expiration must be 60 minutes (3600 seconds)."""
        assert download_utils.SIGNED_URL_EXPIRATION_SECONDS == 3600

    @patch("foldrun_app.core.download_utils.storage.Client")
    @patch("foldrun_app.core.download_utils.google.auth.default")
    def test_generate_signed_download_url_with_iam_token(self, mock_auth_default, mock_storage_cls):
        """Keyless IAM signBlob path passes service_account_email and access_token to generate_signed_url."""
        mock_creds = MagicMock(spec=["valid", "service_account_email", "token", "refresh"])
        mock_creds.valid = True
        mock_creds.service_account_email = "foldrun-agent-sa@test-project.iam.gserviceaccount.com"
        mock_creds.token = "mock_iam_access_token"
        mock_auth_default.return_value = (mock_creds, "test-project")

        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = (
            "https://storage.googleapis.com/test-bucket/pipeline_runs/run1/analysis/artifacts_bundle.zip"
            "?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Expires=3600"
        )
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_storage_cls.return_value = mock_client

        url = download_utils.generate_signed_download_url(
            gcs_uri="gs://test-bucket/pipeline_runs/run1/analysis/artifacts_bundle.zip",
            download_filename="job1_artifacts.zip",
            content_type="application/zip",
        )

        assert url is not None
        assert url.startswith("https://storage.googleapis.com/test-bucket/")
        mock_blob.generate_signed_url.assert_called_once_with(
            version="v4",
            expiration=timedelta(seconds=3600),
            method="GET",
            response_disposition='attachment; filename="job1_artifacts.zip"',
            response_type="application/zip",
            service_account_email="foldrun-agent-sa@test-project.iam.gserviceaccount.com",
            access_token="mock_iam_access_token",
        )

    @patch("foldrun_app.core.download_utils.prepare_signing_context")
    @patch("foldrun_app.core.download_utils.generate_signed_download_url")
    @patch("foldrun_app.core.download_utils.ensure_run_archive_in_gcs")
    def test_build_agent_downloads_dict_for_analyzed_job(
        self, mock_ensure_zip, mock_sign_url, mock_prep_ctx
    ):
        """build_agent_downloads_dict returns signed URLs for artifacts_bundle_signed_url, best_structure_signed_url, summary_json_signed_url, and best_plots_signed_urls."""
        mock_prep_ctx.return_value = (MagicMock(), MagicMock())
        mock_ensure_zip.return_value = (
            "gs://test-bucket/pipeline_runs/20261002_120000/analysis/artifacts_bundle.zip"
        )
        mock_sign_url.side_effect = lambda gcs_uri, download_filename=None, **kwargs: (
            f"https://storage.googleapis.com/signed/{download_filename or gcs_uri.split('/')[-1]}"
        )

        summary_data = {
            "job_id": "alphafold-inference-pipeline-20261002120000",
            "model_type": "alphafold2",
            "best_prediction": {
                "rank": 1,
                "model_name": "model_1_pred_0",
                "pdb_uri": "gs://test-bucket/pipeline_runs/20261002_120000/predict/model_1_pred_0/unrelaxed_protein.pdb",
                "plots": {
                    "plddt_plot": "gs://test-bucket/pipeline_runs/20261002_120000/analysis/plddt_plot_0.png",
                    "pae_plot": "gs://test-bucket/pipeline_runs/20261002_120000/analysis/pae_plot_0.png",
                },
            },
        }

        downloads = download_utils.build_agent_downloads_dict(
            summary_data=summary_data,
            analysis_path="gs://test-bucket/pipeline_runs/20261002_120000/analysis/",
            job_id="alphafold-inference-pipeline-20261002120000",
        )

        mock_prep_ctx.assert_called_once()
        assert downloads["expires_in_seconds"] == 3600
        assert downloads["expires_in_minutes"] == 60
        assert (
            downloads["artifacts_bundle_signed_url"]
            == "https://storage.googleapis.com/signed/alphafold-inference-pipeline-20261002120000_artifacts.zip"
        )
        assert (
            downloads["best_structure_signed_url"]
            == "https://storage.googleapis.com/signed/alphafold-inference-pipeline-20261002120000_rank_01_model_1_pred_0.pdb"
        )
        assert "plddt_plot" in downloads["best_plots_signed_urls"]
        assert "pae_plot" in downloads["best_plots_signed_urls"]

    def test_download_job_artifacts_rejects_invalid_job_id(self):
        """download_job_artifacts_for_job rejects path traversal or malformed job_ids."""
        res = download_utils.download_job_artifacts_for_job(
            job_id="../malicious/job",
            project_id="test-project",
            region="us-central1",
        )
        assert res["status"] == "error"
        assert "Invalid job_id" in res["message"]

    @patch("foldrun_app.models.af2.startup.get_config")
    @patch("foldrun_app.core.vertex_utils.get_pipeline_job")
    @patch("foldrun_app.core.download_utils.prepare_signing_context")
    @patch("foldrun_app.core.download_utils.generate_signed_download_url")
    @patch("foldrun_app.core.download_utils.ensure_run_archive_in_gcs")
    @patch("foldrun_app.core.download_utils.storage.Client")
    def test_download_job_artifacts_skill_wrapper(
        self,
        mock_storage_cls,
        mock_ensure_zip,
        mock_sign_url,
        mock_prep_ctx,
        mock_get_job,
        mock_get_config,
    ):
        """download_job_artifacts skill tool returns compact zip and individual signed URLs."""
        from foldrun_app.skills.results_analysis import download_job_artifacts

        mock_config = MagicMock()
        mock_config.project_id = "test-project"
        mock_config.region = "us-central1"
        mock_config.bucket_name = "test-bucket"
        mock_get_config.return_value = mock_config
        mock_prep_ctx.return_value = (MagicMock(), MagicMock())

        mock_job = MagicMock()
        mock_job.runtime_config.gcs_output_directory = (
            "gs://test-bucket/pipeline_runs/20261002_120000"
        )
        mock_get_job.return_value = mock_job

        summary_payload = {
            "job_id": "openfold3-inference-pipeline-20261002120000",
            "model_type": "openfold3",
            "best_prediction": {
                "rank": 1,
                "sample_name": "seed_1_sample_1",
                "cif_uri": "gs://test-bucket/pipeline_runs/20261002_120000/pred/seed_1_sample_1_model.cif",
                "plots": {
                    "plddt_plot": "gs://test-bucket/pipeline_runs/20261002_120000/analysis/seed_1_sample_1_plddt.png",
                },
            },
            "all_predictions_summary": [
                {
                    "rank": 1,
                    "sample_name": "seed_1_sample_1",
                    "cif_uri": "gs://test-bucket/pipeline_runs/20261002_120000/pred/seed_1_sample_1_model.cif",
                    "confidences_uri": "gs://test-bucket/pipeline_runs/20261002_120000/pred/seed_1_sample_1_confidences.json",
                    "plots": {
                        "plddt_plot": "gs://test-bucket/pipeline_runs/20261002_120000/analysis/seed_1_sample_1_plddt.png",
                    },
                }
            ],
        }

        mock_summary_blob = MagicMock()
        mock_summary_blob.exists.return_value = True
        mock_summary_blob.download_as_text.return_value = json.dumps(summary_payload)

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_summary_blob

        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_storage_cls.return_value = mock_client

        mock_ensure_zip.return_value = (
            "gs://test-bucket/pipeline_runs/20261002_120000/analysis/artifacts_bundle.zip"
        )
        mock_sign_url.side_effect = lambda gcs_uri, download_filename=None, **kwargs: (
            f"https://storage.googleapis.com/signed/{download_filename}"
        )

        # 1. Default compact mode (include_raw=False)
        res_compact = download_job_artifacts(
            job_id="openfold3-inference-pipeline-20261002120000",
            include_raw=False,
        )
        assert res_compact["status"] == "success"
        downloads_compact = res_compact["downloads"]
        assert downloads_compact["artifacts_bundle_signed_url"].endswith("_artifacts.zip")
        assert "best_structure_signed_url" in downloads_compact
        assert "plddt_plot" in downloads_compact["best_plots_signed_urls"]
        assert "raw_artifact_signed_url" not in downloads_compact["top_predictions"][0]

        # 2. With include_raw=True
        res_raw = download_job_artifacts(
            job_id="openfold3-inference-pipeline-20261002120000",
            include_raw=True,
        )
        downloads_raw = res_raw["downloads"]
        assert "raw_artifact_signed_url" in downloads_raw["top_predictions"][0]
