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

"""Security tests for cost estimation filter expression injection (Finding 4.36)."""

from unittest.mock import MagicMock, patch

import pytest

from foldrun_app.skills.cost_estimation.pricing import get_actual_costs
from foldrun_app.skills.cost_estimation.tools import get_actual_job_costs


@pytest.mark.parametrize(
    "malicious_id",
    [
        'foo" OR labels.submitted_by!="foldrun-agent',
        '123" OR "*"="*',
        "job id with spaces",
        "job;drop",
        "",
    ],
)
def test_filter_injection_rejected_in_get_actual_costs(malicious_id):
    """Confirm that unvalidated pipeline_job_id payloads are rejected before API call."""
    captured_requests = []
    mock_client = MagicMock()

    def fake_list_custom_jobs(request):
        captured_requests.append(request)
        return []

    mock_client.list_custom_jobs.side_effect = fake_list_custom_jobs

    with patch("google.cloud.aiplatform_v1.JobServiceClient", return_value=mock_client):
        result = get_actual_costs(
            project_id="test-project",
            region="us-central1",
            pipeline_job_id=malicious_id,
        )

    assert "error" in result
    assert "Invalid pipeline_job_id" in result["error"]
    assert len(captured_requests) == 0


@pytest.mark.parametrize(
    "malicious_id",
    [
        'foo" OR labels.submitted_by!="foldrun-agent',
        "invalid/id",
        "",
    ],
)
def test_filter_injection_rejected_in_get_actual_job_costs(malicious_id, monkeypatch):
    """Confirm get_actual_job_costs rejects invalid pipeline_job_id inputs."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    result = get_actual_job_costs(pipeline_job_id=malicious_id)
    assert "error" in result
    assert "Invalid pipeline_job_id" in result["error"]


@pytest.mark.parametrize(
    "valid_id",
    [
        "1234567890",
        "pipeline_run-abc_123",
        "RUN-2026-09-18_01",
    ],
)
def test_valid_pipeline_job_id_accepted(valid_id):
    """Confirm valid alphanumeric/hyphen/underscore pipeline_job_id is accepted."""
    captured_requests = []
    mock_client = MagicMock()

    def fake_list_custom_jobs(request):
        captured_requests.append(request)
        return []

    mock_client.list_custom_jobs.side_effect = fake_list_custom_jobs

    with patch("google.cloud.aiplatform_v1.JobServiceClient", return_value=mock_client):
        result = get_actual_costs(
            project_id="test-project",
            region="us-central1",
            pipeline_job_id=valid_id,
        )

    assert "error" not in result
    assert len(captured_requests) == 1
    assert (
        captured_requests[0].filter
        == f'labels.submitted_by="foldrun-agent" AND labels.vertex-ai-pipelines-run-billing-id="{valid_id}"'
    )


def test_list_custom_jobs_error_handling():
    """Confirm API errors during list_custom_jobs are handled gracefully."""
    mock_client = MagicMock()
    mock_client.list_custom_jobs.side_effect = RuntimeError("API unavailable")

    with patch("google.cloud.aiplatform_v1.JobServiceClient", return_value=mock_client):
        result = get_actual_costs(
            project_id="test-project",
            region="us-central1",
            pipeline_job_id="valid_job_123",
        )

    assert "error" in result
    assert "Failed to retrieve custom jobs" in result["error"]
