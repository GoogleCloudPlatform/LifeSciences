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

from typing import Optional

from foldrun_app.skills._tool_registry import get_tool


def check_job_status(job_id: str) -> dict:
    """Check status and progress of an AlphaFold2 prediction job."""
    return get_tool("af2_check_job_status").run({"job_id": job_id})


def list_jobs(
    filter: Optional[str] = None,
    label_filter: Optional[dict] = None,
    state: Optional[str] = None,
    job_type: Optional[str] = None,
    gpu_type: Optional[str] = None,
    seq_name: Optional[str] = None,
    min_seq_length: Optional[int] = None,
    max_seq_length: Optional[int] = None,
    limit: int = 20,
    check_analysis: bool = True,
) -> dict:
    """
    List AlphaFold2 prediction jobs with detailed metadata similar to Vertex AI console.

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
    Delete a pipeline job from Vertex AI.

    This removes the job metadata and history from Vertex AI Pipelines.
    WARNING: This action cannot be undone.

    Note: This does NOT delete the output files in GCS - those must be deleted separately
    using the GCS console or gcloud storage commands.

    Args:
        job_id: Job ID to delete
        confirm: Must be True to proceed with deletion (safety check)
    """
    return get_tool("af2_delete_job").run({"job_id": job_id, "confirm": confirm})


def check_gpu_quota(region: Optional[str] = None) -> dict:
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
