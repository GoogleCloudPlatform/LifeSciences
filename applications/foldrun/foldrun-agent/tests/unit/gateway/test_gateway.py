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

"""Unit tests for GET /v1/gpus gateway endpoint."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from foldrun_app.gateway.app import create_gateway_app


def test_get_gpus_endpoint_success(monkeypatch):
    """Verify GET /v1/gpus returns tier statuses, quotas, and recommendations."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    app = create_gateway_app()
    client = TestClient(app)

    with (
        patch("foldrun_app.gateway.router.check_gpu_quota") as mock_quota,
        patch("foldrun_app.gateway.router.aiplatform_v1.JobServiceClient") as mock_job_client,
        patch("foldrun_app.gateway.router.list_pipeline_jobs") as mock_pjobs,
    ):
        mock_quota.return_value = {
            "on_demand_gpus": {
                "NVIDIA_L4_GPUS": {"limit": 8.0},
                "NVIDIA_A100_GPUS": {"limit": 1.0},
                "NVIDIA_A100_80GB_GPUS": {"limit": 0.0},
                "NVIDIA_H100_GPUS": {"limit": 1.0},
            },
            "preemptible_gpus": {
                "PREEMPTIBLE_NVIDIA_L4_GPUS": {"limit": 8.0},
                "PREEMPTIBLE_NVIDIA_A100_GPUS": {"limit": 16.0},
                "PREEMPTIBLE_NVIDIA_A100_80GB_GPUS": {"limit": 0.0},
                "PREEMPTIBLE_NVIDIA_H100_GPUS": {"limit": 1.0},
            },
        }
        mock_pjobs.return_value = []
        mock_client_instance = MagicMock()
        mock_job_client.return_value = mock_client_instance
        mock_client_instance.list_custom_jobs.return_value = []

        resp = client.get("/v1/gpus")
        assert resp.status_code == 200
        data = resp.json()
        assert "tiers" in data
        assert len(data["tiers"]) == 4

        tier_names = [t["gpu_type"] for t in data["tiers"]]
        assert tier_names == ["L4", "A100", "A100_80GB", "H100"]

        l4_tier = next(t for t in data["tiers"] if t["gpu_type"] == "L4")
        assert l4_tier["on_demand_quota"] == 8
        assert l4_tier["flex_start_quota"] == 8
        assert l4_tier["status"] == "AVAILABLE"
        assert l4_tier["running_on_demand"] == 0
        assert l4_tier["queued_flex_start"] == 0

        a100_80gb_tier = next(t for t in data["tiers"] if t["gpu_type"] == "A100_80GB")
        assert a100_80gb_tier["status"] == "NO_QUOTA"

        h100_tier = next(t for t in data["tiers"] if t["gpu_type"] == "H100")
        assert h100_tier["vram_gb"] == 80
        assert h100_tier["machine_type"] == "a3-highgpu-1g"
        assert h100_tier["status"] == "AVAILABLE"

        assert data["recommended_gpu_type"] == "L4"
        assert data["recommended_enable_flex_start"] is True
        assert "Zero queued tasks" in data["recommendation_reason"]

        # Verify per-model minimum VRAM floor filtering
        resp_of3 = client.get("/v1/gpus?model=openfold3")
        assert resp_of3.status_code == 200
        assert resp_of3.json()["recommended_gpu_type"] == "A100"

        resp_af3 = client.get("/v1/gpus?model=alphafold3")
        assert resp_af3.status_code == 200
        assert resp_af3.json()["recommended_gpu_type"] == "H100"


def test_get_gpus_queued_recommendation(monkeypatch):
    """Verify that when DWS FLEX_START has queued tasks, on-demand L4/A100 is recommended."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    app = create_gateway_app()
    client = TestClient(app)

    with (
        patch("foldrun_app.gateway.router.check_gpu_quota") as mock_quota,
        patch("foldrun_app.gateway.router.aiplatform_v1.JobServiceClient") as mock_job_client,
        patch("foldrun_app.gateway.router.list_pipeline_jobs") as mock_pjobs,
    ):
        mock_quota.return_value = {
            "on_demand_gpus": {
                "NVIDIA_L4_GPUS": {"limit": 8.0},
                "NVIDIA_A100_GPUS": {"limit": 4.0},
            },
            "preemptible_gpus": {
                "PREEMPTIBLE_NVIDIA_L4_GPUS": {"limit": 8.0},
                "PREEMPTIBLE_NVIDIA_A100_GPUS": {"limit": 16.0},
            },
        }
        mock_pjobs.return_value = []
        mock_client_instance = MagicMock()
        mock_job_client.return_value = mock_client_instance

        # Simulate a queued FLEX_START custom job on A100
        queued_job = MagicMock()
        queued_job.state.name = "JOB_STATE_PENDING"
        queued_job.job_spec.scheduling.strategy.name = "FLEX_START"
        worker_spec = MagicMock()
        worker_spec.machine_spec.accelerator_type.name = "NVIDIA_TESLA_A100"
        worker_spec.machine_spec.accelerator_count = 1
        queued_job.job_spec.worker_pool_specs = [worker_spec]

        mock_client_instance.list_custom_jobs.return_value = [queued_job]

        resp = client.get("/v1/gpus")
        assert resp.status_code == 200
        data = resp.json()

        a100_tier = next(t for t in data["tiers"] if t["gpu_type"] == "A100")
        assert a100_tier["queued_flex_start"] == 1
        assert a100_tier["status"] == "QUEUED"

        # When FLEX_START has queued tasks, recommend on-demand L4 or A100
        assert data["recommended_enable_flex_start"] is False
        assert "--no-flex-start" in data["recommendation_reason"]


def test_get_gpus_missing_project_returns_503(monkeypatch):
    """Verify GET /v1/gpus fails fast with HTTP 503 when no GCP project is configured."""
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    app = create_gateway_app()
    client = TestClient(app)

    with patch("google.auth.default", return_value=(MagicMock(), None)):
        resp = client.get("/v1/gpus")
        assert resp.status_code == 503
