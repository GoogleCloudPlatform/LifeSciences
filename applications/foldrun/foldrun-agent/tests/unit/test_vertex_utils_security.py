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

"""Security tests for get_pipeline_job (cross-project IDOR prevention)."""

from unittest.mock import MagicMock, patch

import pytest

from foldrun_app.core.vertex_utils import (
    _PROJECT_NUMBERS,
    extract_short_job_id,
    get_pipeline_job,
    resolve_pipeline_job_name,
)


class TestGetPipelineJobSecurity:
    """Tests preventing IDOR and traversal via get_pipeline_job()."""

    def setup_method(self):
        _PROJECT_NUMBERS.clear()

    @patch("google.cloud.aiplatform_v1.PipelineServiceClient")
    def test_valid_short_job_id(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_job = MagicMock()
        mock_client.get_pipeline_job.return_value = mock_job

        result = get_pipeline_job("job-abc_123.test", "my-project", "us-central1")
        assert result == mock_job

        call_args = mock_client.get_pipeline_job.call_args
        req = call_args.kwargs.get("request") or call_args.args[0]
        assert req.name == "projects/my-project/locations/us-central1/pipelineJobs/job-abc_123.test"

    @patch("google.cloud.aiplatform_v1.PipelineServiceClient")
    def test_valid_full_resource_name(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_job = MagicMock()
        mock_client.get_pipeline_job.return_value = mock_job

        full_name = "projects/my-project/locations/us-central1/pipelineJobs/job-abc_123"
        result = get_pipeline_job(full_name, "my-project", "us-central1")
        assert result == mock_job

        call_args = mock_client.get_pipeline_job.call_args
        req = call_args.kwargs.get("request") or call_args.args[0]
        assert req.name == full_name

    def test_cross_project_resource_name_rejected(self):
        with pytest.raises(ValueError, match="Cross-project or cross-region"):
            get_pipeline_job(
                "projects/evil-project/locations/us-central1/pipelineJobs/target-job",
                "my-project",
                "us-central1",
            )

    def test_cross_region_resource_name_rejected(self):
        with pytest.raises(ValueError, match="Cross-project or cross-region"):
            get_pipeline_job(
                "projects/my-project/locations/europe-west4/pipelineJobs/target-job",
                "my-project",
                "us-central1",
            )

    @pytest.mark.parametrize(
        "bad_short_id",
        [
            "../secret-job",
            "job/traversal",
            "job;whoami",
            "job$name",
            "-leading-dash",
            ".leading-dot",
            "job name",
            "job\x00null",
            "",
            "   ",
        ],
    )
    def test_malformed_short_id_rejected(self, bad_short_id):
        with pytest.raises(ValueError):
            get_pipeline_job(bad_short_id, "my-project", "us-central1")

    @pytest.mark.parametrize(
        "bad_full_name",
        [
            "projects/my-project/locations/us-central1/pipelineJobs/../secret-job",
            "projects/my-project/locations/us-central1/pipelineJobs/job/traversal",
            "projects/my-project/locations/us-central1/pipelineJobs/",
            "projects/my-project/locations/us-central1/pipelineJobs/job;ls",
        ],
    )
    def test_malformed_full_resource_name_rejected(self, bad_full_name):
        with pytest.raises(ValueError):
            get_pipeline_job(bad_full_name, "my-project", "us-central1")

    @patch("google.cloud.aiplatform_v1.PipelineServiceClient")
    def test_display_name_fallback_when_get_fails(self, mock_client_cls):
        from google.api_core import exceptions as google_exceptions

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_pipeline_job.side_effect = google_exceptions.NotFound("404 Not Found")
        matched_job = MagicMock()
        mock_client.list_pipeline_jobs.return_value = [matched_job]

        result = get_pipeline_job("af2-ubiquitin-display", "my-project", "us-central1")
        assert result == matched_job
        mock_client.list_pipeline_jobs.assert_called_once()

    @patch("google.cloud.aiplatform_v1.PipelineServiceClient")
    def test_valid_numeric_project_number_resource_name(self, mock_client_cls):
        """Vertex AI submit_* returns projects/<PROJECT_NUMBER>/.../pipelineJobs/<id>."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_job = MagicMock()
        mock_job.name = "projects/123456789012/locations/us-central1/pipelineJobs/openfold3-inference-pipeline-1"
        mock_client.get_pipeline_job.return_value = mock_job

        numeric_name = "projects/123456789012/locations/us-central1/pipelineJobs/openfold3-inference-pipeline-1"
        result = get_pipeline_job(numeric_name, "my-project", "us-central1")
        assert result == mock_job

        # Outgoing RPC must always target configured alphanumeric project_id
        call_args = mock_client.get_pipeline_job.call_args
        req = call_args.kwargs.get("request") or call_args.args[0]
        assert (
            req.name
            == "projects/my-project/locations/us-central1/pipelineJobs/openfold3-inference-pipeline-1"
        )
        assert (
            extract_short_job_id(numeric_name, "my-project", "us-central1")
            == "openfold3-inference-pipeline-1"
        )

    @patch("google.cloud.aiplatform_v1.PipelineServiceClient")
    def test_foreign_numeric_project_number_rejected(self, mock_client_cls):
        """Numeric project prefix that mismatches actual project number is rejected."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_job = MagicMock()
        mock_job.name = "projects/123456789012/locations/us-central1/pipelineJobs/openfold3-inference-pipeline-1"
        mock_client.get_pipeline_job.return_value = mock_job

        with pytest.raises(ValueError, match="Cross-project or cross-region"):
            get_pipeline_job(
                "projects/999999999999/locations/us-central1/pipelineJobs/openfold3-inference-pipeline-1",
                "my-project",
                "us-central1",
            )

        # Subsequent call with cached project number fails before making any RPC
        mock_client.get_pipeline_job.reset_mock()
        with pytest.raises(ValueError, match="Cross-project or cross-region"):
            resolve_pipeline_job_name(
                "projects/999999999999/locations/us-central1/pipelineJobs/other-job",
                "my-project",
                "us-central1",
            )
        mock_client.get_pipeline_job.assert_not_called()

    def test_open_viewer_and_downloads_accept_numeric_resource_name(self):
        """Viewer and artifact download tools normalize full numeric resource names to short IDs."""
        from types import SimpleNamespace

        from foldrun_app.core import download_utils
        from foldrun_app.models.af2.tools.open_viewer import AF2OpenViewerTool
        from foldrun_app.models.boltz2.tools.open_viewer import BOLTZ2OpenViewerTool
        from foldrun_app.models.of3.tools.open_viewer import OF3OpenViewerTool

        cfg = SimpleNamespace(
            viewer_url="https://viewer.example.com",
            project_id="my-project",
            region="us-central1",
            bucket_name="my-bucket",
            databases_bucket_name="my-dbs",
            filestore_id="nfs",
            filestore_ip="10.0.0.2",
            filestore_network="default",
        )
        full_num_id = "projects/123456789012/locations/us-central1/pipelineJobs/boltz2-inference-pipeline-2026"
        with patch("foldrun_app.core.base_tool._ensure_clients"):
            for cls in (AF2OpenViewerTool, OF3OpenViewerTool, BOLTZ2OpenViewerTool):
                tool = cls(tool_config={"name": "open_viewer"}, config=cfg)
                res = tool.run({"job_id": full_num_id, "open_browser": False})
                assert (
                    res["viewer_url"]
                    == "https://viewer.example.com/job/boltz2-inference-pipeline-2026"
                )
                assert res["job_id"] == "boltz2-inference-pipeline-2026"

            # Foreign project rejected in viewer too
            tool = OF3OpenViewerTool(tool_config={"name": "open_viewer"}, config=cfg)
            with pytest.raises(ValueError, match="Invalid job_id"):
                tool.run(
                    {
                        "job_id": "projects/evil-project/locations/us-central1/pipelineJobs/boltz2-1",
                        "open_browser": False,
                    }
                )

        # Foreign project rejected in download_job_artifacts_for_job
        dl_err = download_utils.download_job_artifacts_for_job(
            job_id="projects/evil-project/locations/us-central1/pipelineJobs/boltz2-1",
            project_id="my-project",
            region="us-central1",
        )
        assert dl_err["status"] == "error"
        assert "Invalid job_id" in dl_err["message"]

        # Cold-start mismatched numeric project rejected in download_job_artifacts_for_job
        from foldrun_app.core.vertex_utils import _PROJECT_NUMBERS

        _PROJECT_NUMBERS.pop("my-project", None)
        with patch("google.cloud.aiplatform_v1.PipelineServiceClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_job = MagicMock()
            mock_job.name = "projects/123456789/locations/us-central1/pipelineJobs/boltz2-1"
            mock_client.get_pipeline_job.return_value = mock_job
            dl_err_num = download_utils.download_job_artifacts_for_job(
                job_id="projects/999999999/locations/us-central1/pipelineJobs/boltz2-1",
                project_id="my-project",
                region="us-central1",
            )
            assert dl_err_num["status"] == "error"
            assert "Invalid job_id" in dl_err_num["message"]
            assert "does not match project" in dl_err_num["message"]


class TestVertexTaskDetailsAndEpochGuard:
    """Tests for get_task_details custom_job extraction and unset epoch start_time guard."""

    def test_unset_epoch_start_time_ignored(self):
        from datetime import UTC, datetime

        from foldrun_app.models.af2.utils.vertex_utils import get_job_status, get_task_details

        job = MagicMock()
        job.name = "projects/my-project/locations/us-central1/pipelineJobs/job-1"
        job.display_name = "my-pipeline-run"
        job.state.name = "PIPELINE_STATE_PENDING"
        job.create_time = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        job.start_time = datetime(1970, 1, 1, 0, 0, tzinfo=UTC)
        job.end_time = datetime(2026, 4, 15, 12, 5, tzinfo=UTC)
        job.labels = {}
        job.pipeline_spec = {}
        job.error = None

        status = get_job_status(job)
        assert status["start_time"] is None
        assert "duration_seconds" not in status

        task = MagicMock()
        task.task_id = 101
        task.task_name = "predict"
        task.state.name = "RUNNING"
        task.create_time = datetime(2026, 4, 15, 12, 1, tzinfo=UTC)
        task.start_time = datetime(1970, 1, 1, 0, 0, tzinfo=UTC)
        task.end_time = None
        task.executor_detail = None
        task.execution.metadata = {
            "output:gcp_resources": '{"resources":[{"resourceType":"CustomJob","resourceUri":"projects/123/locations/us-central1/customJobs/98765"}]}'
        }
        task.error = None
        task.outputs = {}

        wrapper_task = MagicMock()
        wrapper_task.task_id = 100
        wrapper_task.task_name = "for-loop-2"
        wrapper_task.state.name = "RUNNING"
        wrapper_task.create_time = None
        wrapper_task.start_time = None
        wrapper_task.end_time = None
        wrapper_task.executor_detail = None
        wrapper_task.execution = None
        wrapper_task.error = None
        wrapper_task.outputs = {}

        job.job_detail.task_details = [wrapper_task, task]
        details = get_task_details(job)
        assert len(details["task_list"]) == 1
        assert details["task_list"][0]["task_name"] == "predict"
        assert details["task_list"][0]["start_time"] is None
        assert details["task_list"][0]["custom_job_id"] == "98765"
