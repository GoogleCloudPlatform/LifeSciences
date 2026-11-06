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

from foldrun_app.skills._tool_registry import get_tool


def cleanup_gcs_files(
    job_id: str | None = None,
    gcs_paths: list[str] | None = None,
    search_only: bool = True,
    confirm_delete: bool = False,
    include_fasta: bool = False,
) -> dict:
    """Find and optionally delete GCS output files for a job or list of GCS paths.

    Always call with search_only=True first to preview files and sizes, then call with
    search_only=False and confirm_delete=True only after explicit user confirmation.

    Args:
        job_id: Job ID or job name to search for.
        gcs_paths: Explicit list of GCS paths for bulk cleanup.
        search_only: List files without deleting (default True).
        confirm_delete: Must be True to delete files.
        include_fasta: Also delete input FASTA file (default False).
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
    return get_tool("cleanup_gcs_files").run(args)


def find_orphaned_gcs_files(
    check_fasta: bool = True,
    max_jobs_to_check: int = 1000,
) -> dict:
    """Scan GCS for orphaned pipeline_runs/ and fasta/ files without corresponding Agent Platform jobs.

    Args:
        check_fasta: Also check for orphaned FASTA files (default True).
        max_jobs_to_check: Max pipeline jobs to cross-reference (default 1000).
    """
    return get_tool("find_orphaned_files").run(
        {
            "check_fasta": check_fasta,
            "max_jobs_to_check": max_jobs_to_check,
        }
    )
