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

from foldrun_app.core.vertex_utils import get_pipeline_job


class TestGetPipelineJobSecurity:
    """Tests preventing IDOR and traversal via get_pipeline_job()."""

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
