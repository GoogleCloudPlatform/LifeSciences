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
    """Check status and progress of a FoldRun structure prediction job (AF2, OF3, Boltz-2, or AF3)."""
    return get_tool("check_job_status").run({"job_id": job_id})


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
    """List FoldRun prediction jobs (AF2, OF3, Boltz-2, AF3) with metadata and console URLs.

    Args:
        state: Simple lowercase word: "running", "failed", "succeeded", "pending", or "cancelled" (never PIPELINE_STATE_*).
        job_type: "monomer" or "multimer".
        gpu_type: "l4", "a100", or "a100-80gb".
        seq_name: Case-insensitive partial sequence name filter.
        min_seq_length: Minimum sequence length in residues.
        max_seq_length: Maximum sequence length in residues.
        limit: Max jobs to return (default 20).
        check_analysis: Check if analysis exists for succeeded jobs (default True).
        label_filter: Custom label filter dict.
    """
    return get_tool("list_jobs").run(
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
    """Get original FASTA sequence, submission parameters, and timing metadata for a job."""
    return get_tool("get_job_details").run({"job_id": job_id})


def delete_job(job_id: str, confirm: bool = False) -> dict:
    """Permanently delete a pipeline job from Agent Platform (does not delete GCS files).

    Args:
        job_id: Job ID to delete.
        confirm: Must be True after explicit user approval.
    """
    return get_tool("delete_job").run({"job_id": job_id, "confirm": confirm})


def check_gpu_quota(region: str | None = None) -> dict:
    """Check GPU quota limits and availability for L4, A100 (40GB/80GB), and H100 GPUs in the region."""
    return get_tool("check_gpu_quota").run({"region": region} if region else {})


def check_af3_endpoint(endpoint_id: str | None = None) -> dict:
    """Check health, deployment status, and hardware configuration of AlphaFold 3 Agent Platform Endpoint.

    Args:
        endpoint_id: Optional Agent Platform Endpoint ID override.
    """
    args = {}
    if endpoint_id is not None:
        args["endpoint_id"] = endpoint_id
    return get_tool("af3_check_endpoint").run(args)
