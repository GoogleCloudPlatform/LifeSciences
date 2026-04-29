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

"""Storage management tool wrappers for ADK FunctionTool."""

from typing import Optional

from foldrun_app.skills._tool_registry import get_tool


def cleanup_gcs_files(
    job_id: Optional[str] = None,
    gcs_paths: Optional[list[str]] = None,
    search_only: bool = True,
    confirm_delete: bool = False,
    include_fasta: bool = False,
) -> dict:
    """
    Find and optionally delete GCS output files for a job.

    Works even if the job has been deleted from Agent Platform - just pass the job name directly.
    Searches for pipeline outputs in GCS and optionally deletes them. This is useful
    for cleaning up storage after failed or unnecessary jobs.

    Supports two modes:
    - Mode 1 (job-based): Provide job_id to find/delete files for a specific job
    - Mode 2 (bulk deletion): Provide gcs_paths list to delete specific directories/files

    Workflow:
    1. First call with search_only=True (default) to see what files exist
    2. Review the file list and sizes
    3. Call again with search_only=False and confirm_delete=True to delete

    Args:
        job_id: Job ID or job name to search for (works even if job deleted from Agent Platform)
        gcs_paths: List of GCS paths to delete (for bulk mode)
        search_only: If True, only list files without deleting (default: True)
        confirm_delete: Must be True to actually delete files (safety check)
        include_fasta: Also delete the original FASTA file (default: False)

    Returns:
        File list with sizes and deletion status if applicable
    """
    args = {
        "search_only": search_only,
        "confirm_delete": confirm_delete,
        "include_fasta": include_fasta,
    }
    if job_id is not None:
        args["job_id"] = job_id
    if gcs_paths is not None:
        args["gcs_paths"] = gcs_paths
    return get_tool("af2_cleanup_gcs_files").run(args)


def find_orphaned_gcs_files(
    check_fasta: bool = True,
    max_jobs_to_check: int = 1000,
) -> dict:
    """
    Find all GCS files that don't have corresponding Agent Platform jobs.

    This tool identifies "orphaned" files from jobs that have been deleted from
    Agent Platform but still have files consuming storage in GCS. Useful for bulk cleanup
    and identifying storage cost savings opportunities.

    The tool:
    1. Retrieves all pipeline jobs from Agent Platform
    2. Scans pipeline_runs/ and fasta/ directories in GCS
    3. Identifies files/directories without corresponding jobs
    4. Reports sizes and potential cost savings

    Args:
        check_fasta: Also check for orphaned FASTA files (default: True)
        max_jobs_to_check: Maximum number of jobs to retrieve from Agent Platform (default: 1000)

    Returns:
        List of orphaned directories and files with sizes and cleanup instructions
    """
    return get_tool("af2_find_orphaned_files").run(
        {
            "check_fasta": check_fasta,
            "max_jobs_to_check": max_jobs_to_check,
        }
    )
