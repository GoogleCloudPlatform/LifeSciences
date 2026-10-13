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

"""Security tests for GCS URI validation in AF2 submit and analysis tools."""

from unittest.mock import MagicMock, patch

import pytest

from foldrun_app.models.af2.tools.analyze_job import AF2JobAnalysisTool
from foldrun_app.models.af2.tools.get_analysis_results import AF2GetAnalysisResultsTool
from foldrun_app.models.af2.tools.submit_monomer import AF2SubmitMonomerTool
from foldrun_app.models.af2.tools.submit_multimer import AF2SubmitMultimerTool


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.project_id = "test-project"
    config.region = "us-central1"
    config.bucket_name = "authorized-pipeline-bucket"
    config.databases_bucket_name = "authorized-databases-bucket"
    config.filestore_ip = "10.0.0.1"
    config.filestore_network = "projects/test-project/global/networks/default"
    config.pipelines_sa_email = "sa@test-project.iam.gserviceaccount.com"
    config.base_image = "gcr.io/test/af2:latest"
    config.nfs_share = "/datasets"
    config.nfs_mount_point = "/mnt/nfs/foldrun"
    config.parallelism = 5
    config.dws_max_wait_hours = 168
    config.supported_gpus = ["L4", "A100", "A100_80GB"]
    return config


@pytest.fixture
def submit_monomer_tool(mock_config):
    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = AF2SubmitMonomerTool(
            tool_config={"name": "submit_monomer", "description": "test"},
            config=mock_config,
        )
        tool.storage_client = MagicMock()
        tool._upload_to_gcs = MagicMock()
        return tool


@pytest.fixture
def submit_multimer_tool(mock_config):
    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = AF2SubmitMultimerTool(
            tool_config={"name": "submit_multimer", "description": "test"},
            config=mock_config,
        )
        tool.storage_client = MagicMock()
        tool._upload_to_gcs = MagicMock()
        return tool


@pytest.fixture
def get_analysis_results_tool(mock_config):
    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = AF2GetAnalysisResultsTool(
            tool_config={"name": "get_analysis_results", "description": "test"},
            config=mock_config,
        )
        tool.storage_client = MagicMock()
        return tool


@pytest.fixture
def analyze_job_tool(mock_config):
    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = AF2JobAnalysisTool(
            tool_config={"name": "analyze_job", "description": "test"},
            config=mock_config,
        )
        tool.storage_client = MagicMock()
        return tool


class TestAF2SubmitGcsValidation:
    """Tests for GCS bucket validation and traversal prevention in AF2 sequence retrieval."""

    def test_monomer_rejects_unauthorized_bucket(self, submit_monomer_tool):
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            submit_monomer_tool.run(
                {"sequence": "gs://unauthorized-bucket/test.fasta", "job_name": "test_job"}
            )

    @pytest.mark.parametrize(
        "traversal_uri",
        [
            "gs://authorized-pipeline-bucket/../secret.fasta",
            "gs://authorized-pipeline-bucket/./secret.fasta",
            "gs://authorized-pipeline-bucket/folder/../../secret.fasta",
            "gs://authorized-pipeline-bucket/\x00secret.fasta",
        ],
    )
    def test_monomer_rejects_path_traversal(self, submit_monomer_tool, traversal_uri):
        with pytest.raises(ValueError):
            submit_monomer_tool.run({"sequence": traversal_uri, "job_name": "test_job"})

    @patch("foldrun_app.models.af2.tools.submit_monomer.vertex_ai.PipelineJob")
    def test_monomer_accepts_authorized_bucket(self, mock_pipeline_job, submit_monomer_tool):
        mock_blob = MagicMock()
        mock_blob.download_as_text.return_value = ">monomer\nACDEFGHIKLMNPQRSTVWY\n"
        submit_monomer_tool.storage_client.bucket.return_value.blob.return_value = mock_blob

        result = submit_monomer_tool.run(
            {"sequence": "gs://authorized-pipeline-bucket/valid.fasta", "job_name": "valid_job"}
        )
        assert result["status"] == "submitted"
        submit_monomer_tool.storage_client.bucket.assert_called_with("authorized-pipeline-bucket")

    def test_multimer_rejects_unauthorized_bucket(self, submit_multimer_tool):
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            submit_multimer_tool.run(
                {"sequence": "gs://unauthorized-bucket/test.fasta", "job_name": "test_job"}
            )

    @pytest.mark.parametrize(
        "traversal_uri",
        [
            "gs://authorized-pipeline-bucket/../secret.fasta",
            "gs://authorized-pipeline-bucket/./secret.fasta",
            "gs://authorized-pipeline-bucket/folder/../../secret.fasta",
            "gs://authorized-pipeline-bucket/\x00secret.fasta",
        ],
    )
    def test_multimer_rejects_path_traversal(self, submit_multimer_tool, traversal_uri):
        with pytest.raises(ValueError):
            submit_multimer_tool.run({"sequence": traversal_uri, "job_name": "test_job"})


class TestAF2AnalysisGcsValidation:
    """Tests for GCS scoping and traversal prevention in analysis tools."""

    def test_get_analysis_path_rejects_unauthorized_bucket(self, get_analysis_results_tool):
        mock_job = MagicMock()
        mock_job.runtime_config.gcs_output_directory = "gs://attacker-bucket/run1/"
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            get_analysis_results_tool._get_analysis_path(mock_job)

    def test_get_analysis_path_rejects_traversal(self, get_analysis_results_tool):
        mock_job = MagicMock()
        mock_job.runtime_config.gcs_output_directory = "gs://authorized-pipeline-bucket/run1/../"
        with pytest.raises(ValueError, match="Path traversal detected"):
            get_analysis_results_tool._get_analysis_path(mock_job)

    def test_get_analysis_path_valid(self, get_analysis_results_tool):
        mock_job = MagicMock()
        mock_job.runtime_config.gcs_output_directory = "gs://authorized-pipeline-bucket/run1"
        path = get_analysis_results_tool._get_analysis_path(mock_job)
        assert path == "gs://authorized-pipeline-bucket/run1/analysis/"

    def test_read_from_gcs_rejects_unauthorized_and_traversal(self, get_analysis_results_tool):
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            get_analysis_results_tool._read_from_gcs("gs://attacker-bucket/data.json")

        with pytest.raises(ValueError, match="Path traversal detected"):
            get_analysis_results_tool._read_from_gcs(
                "gs://authorized-pipeline-bucket/../secret.json"
            )

    @patch("google.cloud.storage.Client")
    def test_read_from_gcs_valid(self, mock_storage_cls, get_analysis_results_tool):
        mock_client = MagicMock()
        mock_storage_cls.return_value = mock_client
        mock_blob = MagicMock()
        mock_blob.download_as_text.return_value = '{"status": "ok"}'
        mock_client.bucket.return_value.blob.return_value = mock_blob

        data = get_analysis_results_tool._read_from_gcs(
            "gs://authorized-pipeline-bucket/run1/analysis/data.json"
        )
        assert data == {"status": "ok"}

    def test_write_to_gcs_rejects_unauthorized_and_traversal(self, get_analysis_results_tool):
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            get_analysis_results_tool._write_to_gcs(
                "gs://attacker-bucket/data.json", {"test": "val"}
            )

        with pytest.raises(ValueError, match="Path traversal detected"):
            get_analysis_results_tool._write_to_gcs(
                "gs://authorized-pipeline-bucket/../secret.json", {"test": "val"}
            )

    def test_list_completed_analyses_rejects_unauthorized_and_traversal(
        self, get_analysis_results_tool
    ):
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            get_analysis_results_tool._list_completed_analyses("gs://attacker-bucket/run1/")

        with pytest.raises(ValueError, match="Path traversal detected"):
            get_analysis_results_tool._list_completed_analyses(
                "gs://authorized-pipeline-bucket/run1/../"
            )

    def test_analyze_job_tool_rejects_unauthorized_and_traversal(self, analyze_job_tool):
        mock_job = MagicMock()
        mock_job.runtime_config.gcs_output_directory = "gs://attacker-bucket/run1/"
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            analyze_job_tool._get_analysis_path(mock_job)

        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            analyze_job_tool._write_to_gcs("gs://attacker-bucket/data.json", {"test": "val"})

        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            analyze_job_tool._check_existing_analysis("gs://attacker-bucket/run1/")

    def test_af2_analysis_tool_download_from_gcs_unpacks_validate_gcs_uri(self, mock_config):
        from foldrun_app.models.af2.tools.analyze import AF2AnalysisTool

        with patch("foldrun_app.core.base_tool._ensure_clients"):
            tool = AF2AnalysisTool(
                tool_config={"name": "af2_analyze", "description": "test"},
                config=mock_config,
            )
            tool.storage_client = MagicMock()

        result = tool._download_from_gcs(
            "gs://authorized-pipeline-bucket/job1/raw_prediction.pkl",
            "/tmp/raw_prediction.pkl",
        )
        assert result == "/tmp/raw_prediction.pkl"
        tool.storage_client.bucket.assert_called_once_with("authorized-pipeline-bucket")
        tool.storage_client.bucket.return_value.blob.assert_called_once_with(
            "job1/raw_prediction.pkl"
        )

    @patch("foldrun_app.core.vertex_utils.get_pipeline_job")
    def test_boltz2_analyze_job_rejects_query_name_traversal(
        self, mock_get_pipeline_job, mock_config
    ):
        from foldrun_app.models.boltz2.tools.analyze_job import BOLTZ2JobAnalysisTool

        with patch("foldrun_app.core.base_tool._ensure_clients"):
            tool = BOLTZ2JobAnalysisTool(
                tool_config={"name": "boltz2_analyze_job", "description": "test"},
                config=mock_config,
            )
            tool.storage_client = MagicMock()

        mock_job = MagicMock()
        mock_job.runtime_config.gcs_output_directory = "gs://authorized-pipeline-bucket/run1/"
        mock_job.labels = {"query_name": "../secret_query"}
        mock_get_pipeline_job.return_value = mock_job
        tool._check_existing_analysis = MagicMock(return_value=(False, None))
        tool._discover_boltz2_samples = MagicMock(
            return_value=(
                [
                    {
                        "sample_name": "s1",
                        "cif_uri": "gs://authorized-pipeline-bucket/run1/s1.cif",
                        "aggregated_uri": "gs://authorized-pipeline-bucket/run1/s1.json",
                    }
                ],
                None,
            )
        )

        with pytest.raises(ValueError, match="Path traversal detected"):
            tool.run({"job_id": "job-123", "overwrite": True})
