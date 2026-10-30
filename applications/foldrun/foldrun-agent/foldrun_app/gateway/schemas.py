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

"""Pydantic request and response schemas for foldrun-gateway."""

from typing import Any, Literal

from pydantic import BaseModel, Field

SupportedModel = Literal[
    "boltz",
    "openfold3",
    "alphafold2",
    "boltzgen",
    "rfd4_proteina",
    "bindcraft",
    "alphafold3",
    "af3",
]


class JobSubmissionParams(BaseModel):
    """JSON parameters sent alongside multipart file uploads to POST /v1/{model}/jobs."""

    cli_args: list[str] = Field(default_factory=list)
    input_sha256: str | None = None
    deduplicate: bool = True
    gpu_type: str = "auto"
    enable_flex_start: bool = True
    fallback_to_ondemand: bool = True
    dws_max_wait_hours: float | None = None
    override: bool = False
    runner_yaml_overrides: dict[str, Any] = Field(default_factory=dict)


class JobSubmitResponse(BaseModel):
    """Response returned by POST /v1/{model}/jobs (HTTP 202 Accepted)."""

    job_id: str
    pipeline_job_resource_name: str
    model: SupportedModel
    state: str
    input_sha256: str
    viewer_url: str | None = None
    deduplicated: bool = False


class TaskProgressInfo(BaseModel):
    """Status summary for an individual KFP task."""

    task_name: str
    state: str
    start_time: str | None = None
    end_time: str | None = None
    error_message: str | None = None
    custom_job_id: str | None = None
    custom_job_state: str | None = None
    accelerator_type: str | None = None
    scheduling_strategy: str | None = None
    queue_wait_sec: float | None = None


class JobStatusResponse(BaseModel):
    """Response returned by GET /v1/jobs/{job_id}."""

    job_id: str
    pipeline_job_resource_name: str
    model: str
    state: str
    current_step: str
    execution_phase: str = "UNKNOWN"
    progress_percent: float = 0.0
    queued_gpu_tasks: int = 0
    running_gpu_tasks: int = 0
    completed_gpu_tasks: int = 0
    total_gpu_tasks: int = 0
    scheduling_strategy: str | None = None
    accelerator_type: str | None = None
    max_queue_wait_sec: float | None = None
    queue_notice: str | None = None
    tasks: list[TaskProgressInfo] = Field(default_factory=list)
    error_message: str | None = None
    viewer_url: str | None = None
    create_time: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class JobOutputsResponse(BaseModel):
    """Response returned by GET /v1/jobs/{job_id}/outputs."""

    job_id: str
    model: str
    state: str
    archive_gcs_uri: str
    download_url: str | None = None
    expires_in_seconds: int = 900
    viewer_url: str | None = None


class JobCancelResponse(BaseModel):
    """Response returned by POST /v1/jobs/{job_id}/cancel."""

    status: str
    job_id: str
    pipeline_job_resource_name: str
    cancelled_resources: list[str] = Field(default_factory=list)
    failed_resources: list[str] = Field(default_factory=list)
    message: str


class GPUTierStatus(BaseModel):
    """Status and live workload metrics for a specific GPU accelerator tier."""

    gpu_type: str
    accelerator_type: str
    machine_type: str
    vram_gb: int
    on_demand_quota: int | None = None
    flex_start_quota: int | None = None
    running_on_demand: int = 0
    queued_on_demand: int = 0
    running_flex_start: int = 0
    queued_flex_start: int = 0
    status: str = "AVAILABLE"
    typical_use: str = ""


class GatewayGPUStatusResponse(BaseModel):
    """Response returned by GET /v1/gpus."""

    project_id: str
    region: str
    tiers: list[GPUTierStatus]
    active_pipeline_jobs: int = 0
    recommended_gpu_type: str = "L4"
    recommended_enable_flex_start: bool = False
    recommendation_reason: str = ""
