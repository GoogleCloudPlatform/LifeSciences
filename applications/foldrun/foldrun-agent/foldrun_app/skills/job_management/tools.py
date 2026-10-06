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

"""Job management tool wrappers for ADK FunctionTool."""

from foldrun_app.skills._tool_registry import get_tool


def check_job_status(job_id: str) -> dict:
    """Check status and progress of an AlphaFold2 prediction job."""
    return get_tool("af2_check_job_status").run({"job_id": job_id})


def list_jobs(
    filter: str | None = None,
    label_filter: dict | None = None,
    state: str | None = None,
    job_type: str | None = None,
    gpu_type: str | None = None,
    seq_name: str | None = None,
    min_seq_length: int | None = None,
    max_seq_length: int | None = None,
    limit: int = 20,
    check_analysis: bool = True,
) -> dict:
    """
    List AlphaFold2 prediction jobs with detailed metadata similar to Agent Platform console.

    IMPORTANT: Use simple lowercase values for the state parameter:
    - state: Use "running", "failed", "succeeded", "pending", or "cancelled" (lowercase, simple words)
      DO NOT use PIPELINE_STATE_* format or snake_case. Just use the simple word.

    Other filters:
    - job_type: Use "monomer" or "multimer"
    - gpu_type: Use "l4", "a100", or "a100-80gb"
    - seq_name: Partial match on sequence name (case-insensitive)
    - min_seq_length: Minimum sequence length in residues (e.g., 100)
    - max_seq_length: Maximum sequence length in residues (e.g., 500)
    - limit: Maximum number of jobs to return (default: 20)
    - check_analysis: Check if analysis exists for succeeded jobs (default: True)
    - label_filter: Advanced custom label filters (dict format)

    Examples:
    - list_jobs(state="succeeded") - List all succeeded jobs
    - list_jobs(state="failed", limit=10) - List 10 most recent failed jobs
    - list_jobs(job_type="multimer", gpu_type="a100") - List multimer jobs on A100 GPUs

    Returns comprehensive job information including duration, timestamps, labels, and console URLs.
    """
    return get_tool("af2_list_jobs").run(
        {
            "filter": filter,
            "label_filter": label_filter,
            "state": state,
            "job_type": job_type,
            "gpu_type": gpu_type,
            "seq_name": seq_name,
            "min_seq_length": min_seq_length,
            "max_seq_length": max_seq_length,
            "limit": limit,
            "check_analysis": check_analysis,
        }
    )


def get_job_details(job_id: str) -> dict:
    """
    Get detailed job information including original FASTA sequence and all submission parameters.

    Use this to retrieve job metadata for failed jobs that you want to resubmit with different
    settings (e.g., different GPU type). Returns complete job configuration, timing info,
    and resubmission-ready parameters.
    """
    return get_tool("af2_get_job_details").run({"job_id": job_id})


def delete_job(job_id: str, confirm: bool = False) -> dict:
    """
    Delete a pipeline job from Agent Platform.

    This removes the job metadata and history from Agent Platform Pipelines.
    WARNING: This action cannot be undone.

    Note: This does NOT delete the output files in GCS - those must be deleted separately
    using the GCS console or gcloud storage commands.

    Args:
        job_id: Job ID to delete
        confirm: Must be True to proceed with deletion (safety check)
    """
    return get_tool("af2_delete_job").run({"job_id": job_id, "confirm": confirm})


def check_gpu_quota(region: str | None = None) -> dict:
    """
    Check GPU quota limits and current usage in the configured region.

    Shows available capacity for L4, A100 (40GB), and A100 (80GB) GPUs,
    both on-demand and preemptible/spot instances. Use this before submitting
    jobs to understand GPU availability and whether FLEX_START is needed.

    Args:
        region: Region to check quotas for (default: configured region)

    Returns quota limits, current usage, available capacity, and recommendations.
    """
    return get_tool("af2_check_gpu_quota").run({"region": region} if region else {})


def check_af3_endpoint(endpoint_id: str | None = None) -> dict:
    """Check health, deployment status, and hardware configuration of AlphaFold 3 Agent Platform Endpoint.

    Args:
        endpoint_id: Optional Agent Platform Endpoint ID override.
    """
    args = {}
    if endpoint_id is not None:
        args["endpoint_id"] = endpoint_id
    return get_tool("af3_check_endpoint").run(args)


def deploy_af3_endpoint(
    model_id: str | None = None,
    endpoint_id: str | None = None,
    machine_type: str = "a3-highgpu-1g",
    accelerator_type: str = "NVIDIA_H100_80GB",
    accelerator_count: int = 1,
    min_replica_count: int | None = None,
    max_replica_count: int | None = None,
    sync: bool = False,
) -> dict:
    """Deploy or scale the AlphaFold 3 model on the Agent Platform Endpoint.

    Allocates dedicated GPU hardware (default: a3-highgpu-1g with 1x NVIDIA H100 80GB GPU
    and 3 TB local NVMe SSD for the 630 GB MSA bundle) for interactive prediction campaigns.
    If a model is already deployed, passing min_replica_count / max_replica_count scales
    the active endpoint's replica count up or down in-place (without creating a new endpoint).

    Args:
        model_id: Optional Agent Platform Model resource name or ID.
        endpoint_id: Optional Agent Platform Endpoint override.
        machine_type: Machine type for inference (default: 'a3-highgpu-1g').
        accelerator_type: GPU accelerator type (default: 'NVIDIA_H100_80GB').
        accelerator_count: Number of GPU accelerators (default: 1).
        min_replica_count: Minimum active GPU replicas (default: 1; pass >1 to scale out for large backlogs).
        max_replica_count: Maximum GPU replicas for autoscaling (defaults to min_replica_count).
        sync: Whether to wait synchronously (~10-12 mins; default: False).
    """
    args = {
        "machine_type": machine_type,
        "accelerator_type": accelerator_type,
        "accelerator_count": accelerator_count,
        "sync": sync,
    }
    if model_id is not None:
        args["model_id"] = model_id
    if endpoint_id is not None:
        args["endpoint_id"] = endpoint_id
    if min_replica_count is not None:
        args["min_replica_count"] = min_replica_count
    if max_replica_count is not None:
        args["max_replica_count"] = max_replica_count
    return get_tool("af3_deploy_endpoint").run(args)


def undeploy_af3_endpoint(
    deployed_model_id: str | None = None,
    endpoint_id: str | None = None,
    sync: bool = True,
) -> dict:
    """Undeploy models from the AlphaFold 3 Agent Platform Endpoint (teardown GPU to $0/hr).

    Releases dedicated GPU hardware from the endpoint, reverting ongoing idle costs
    to $0.00/hr immediately while preserving the endpoint and model registry entries.

    Args:
        deployed_model_id: Optional specific deployed model ID to undeploy. If omitted, undeploys all models.
        endpoint_id: Optional Agent Platform Endpoint override.
        sync: Whether to wait synchronously (default: True).
    """
    args = {"sync": sync}
    if deployed_model_id is not None:
        args["deployed_model_id"] = deployed_model_id
    if endpoint_id is not None:
        args["endpoint_id"] = endpoint_id
    return get_tool("af3_undeploy_endpoint").run(args)
