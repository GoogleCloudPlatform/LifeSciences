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

"""Tool for finding and cleaning up GCS files for AlphaFold2 jobs."""

from typing import Any, Dict, List

from google.cloud import storage

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job


class AF2CleanupGCSFilesTool(AF2Tool):
    """Tool for finding and optionally deleting GCS files associated with a job."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Find and optionally delete GCS files associated with a job OR delete specific GCS paths.

        Two modes of operation:
        1. Job-based cleanup: Provide job_id to search and delete files for that job
        2. Path-based cleanup: Provide gcs_paths list to delete specific files (e.g., from find_orphaned_gcs_files)

        Args:
            arguments: {
                'job_id': Job ID or job name to search for (mode 1),
                'gcs_paths': List of GCS paths to delete directly (mode 2, e.g., ['gs://bucket/fasta/file.fasta']),
                'search_only': If True, only list files without deleting (default: True),
                'confirm_delete': Must be True to actually delete files (safety check),
                'include_fasta': Also delete the original FASTA file (default: False, only for mode 1)
            }

        Returns:
            List of files found and deletion status if applicable
        """
        # Extract parameters
        job_id = arguments.get("job_id")
        gcs_paths = arguments.get("gcs_paths")
        search_only = arguments.get("search_only", True)
        confirm_delete = arguments.get("confirm_delete", False)
        include_fasta = arguments.get("include_fasta", False)

        # Determine mode
        if gcs_paths:
            # Mode 2: Bulk path deletion
            return self._delete_paths(gcs_paths, search_only, confirm_delete)
        elif job_id:
            # Mode 1: Job-based cleanup
            return self._cleanup_job(job_id, search_only, confirm_delete, include_fasta)
        else:
            raise ValueError("Either job_id or gcs_paths is required")

    def _delete_paths(
        self, gcs_paths: List[str], search_only: bool, confirm_delete: bool
    ) -> Dict[str, Any]:
        """Delete specific GCS paths (bulk deletion mode).

        Supports both individual file paths and directory paths.
        Directory paths (ending with /) will be expanded to include all files within.
        """
        if not isinstance(gcs_paths, list):
            raise ValueError("gcs_paths must be a list of GCS paths")

        if not gcs_paths:
            raise ValueError("gcs_paths list is empty")

        # Initialize GCS client
        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(self.config.bucket_name)

        # Gather file info
        files_to_process = []
        total_size = 0

        for gcs_path in gcs_paths:
            if not gcs_path.startswith("gs://"):
                raise ValueError(f"Invalid GCS path (must start with gs://): {gcs_path}")

            # Extract blob name: gs://bucket/path/to/file -> path/to/file
            blob_name = "/".join(gcs_path.split("/")[3:])

            # Check if this is a directory path (ends with /)
            if gcs_path.endswith("/"):
                # List all files in this directory
                try:
                    dir_blobs = bucket.list_blobs(prefix=blob_name)
                    for blob in dir_blobs:
                        files_to_process.append(
                            {
                                "path": f"gs://{self.config.bucket_name}/{blob.name}",
                                "size_bytes": blob.size,
                                "size_kb": round(blob.size / 1024, 2),
                                "size_mb": round(blob.size / (1024 * 1024), 2),
                                "blob_name": blob.name,
                            }
                        )
                        total_size += blob.size
                except Exception:
                    # Error listing directory - skip
                    pass
            else:
                # Individual file
                try:
                    blob = bucket.blob(blob_name)
                    if blob.exists():
                        blob.reload()  # Get metadata
                        files_to_process.append(
                            {
                                "path": gcs_path,
                                "size_bytes": blob.size,
                                "size_kb": round(blob.size / 1024, 2),
                                "size_mb": round(blob.size / (1024 * 1024), 2),
                                "blob_name": blob_name,
                            }
                        )
                        total_size += blob.size
                except Exception:
                    # File doesn't exist or error accessing it
                    pass

        total_mb = round(total_size / (1024 * 1024), 2)

        if search_only:
            return {
                "mode": "bulk_path_deletion",
                "search_only": True,
                "files_found": len(files_to_process),
                "total_size_mb": total_mb,
                "files": files_to_process,
                "note": f"Found {len(files_to_process)} files totaling {total_mb} MB. Set search_only=False and confirm_delete=True to delete.",
            }

        # Delete mode
        if not confirm_delete:
            raise ValueError(
                f"Deletion requires confirmation. Set confirm_delete=True to proceed. "
                f"This will delete {len(files_to_process)} files ({total_mb} MB) and cannot be undone."
            )

        deleted_files = []
        failed_files = []
        total_deleted_size = 0

        for file_info in files_to_process:
            try:
                blob = bucket.blob(file_info["blob_name"])
                blob.delete()
                deleted_files.append(file_info["path"])
                total_deleted_size += file_info["size_bytes"]
            except Exception as e:
                failed_files.append({"path": file_info["path"], "error": str(e)})

        return {
            "mode": "bulk_path_deletion",
            "status": "completed" if not failed_files else "completed_with_errors",
            "deleted_count": len(deleted_files),
            "failed_count": len(failed_files),
            "total_size_deleted_mb": round(total_deleted_size / (1024 * 1024), 2),
            "deleted_files": deleted_files,
            "failed_files": failed_files if failed_files else None,
        }

    def _cleanup_job(
        self, job_id: str, search_only: bool, confirm_delete: bool, include_fasta: bool
    ) -> Dict[str, Any]:
        """Original job-based cleanup logic."""

        # Try to get job details to find the exact job name
        # If job has been deleted from Agent Platform, use the job_id as the job name
        job_name = None
        job_exists = True
        pipeline_root = None
        try:
            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
            job_name = job.display_name

            # Get the actual pipeline root where files are stored
            if hasattr(job, "runtime_config") and job.runtime_config:
                if (
                    hasattr(job.runtime_config, "gcs_output_directory")
                    and job.runtime_config.gcs_output_directory
                ):
                    pipeline_root = job.runtime_config.gcs_output_directory
                    # Extract the path after gs://bucket/
                    if pipeline_root.startswith("gs://"):
                        pipeline_root = "/".join(pipeline_root.split("/")[3:])
        except Exception:
            # Job doesn't exist in Agent Platform anymore
            # Use job_id as job_name (user can pass the job name directly)
            job_exists = False
            job_name = job_id
            # Strip common prefixes if present
            if job_name.startswith("projects/"):
                # Extract just the job name from full resource path
                job_name = job_name.split("/")[-1]

        # Extract timestamp from job ID for timestamped directory search
        # Job ID format: alphafold2-inference-pipeline-20251112172826 (new)
        #                alphafold-inference-pipeline-20251112172826  (legacy)
        # Timestamped dir: pipeline_runs/20251112_172826/
        timestamp_search = None
        if "alphafold2-inference-pipeline-" in job_id.lower() or \
                "alphafold-inference-pipeline-" in job_id.lower():
            timestamp = job_id.split("-")[-1]  # Get the timestamp part
            if timestamp.isdigit() and len(timestamp) >= 14:
                # Format as YYYYMMDD_HHMMSS
                timestamp_search = f"{timestamp[:8]}_{timestamp[8:]}"

        # Initialize GCS client
        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(self.config.bucket_name)

        # Search for files in different locations
        found_files = {"pipeline_runs": [], "fasta": [], "total_size_bytes": 0}

        # Strategy 1: If we have the pipeline_root from Agent Platform, use it directly
        if pipeline_root:
            try:
                dir_blobs = bucket.list_blobs(prefix=pipeline_root)
                for blob in dir_blobs:
                    found_files["pipeline_runs"].append(
                        {
                            "path": f"gs://{self.config.bucket_name}/{blob.name}",
                            "size_bytes": blob.size,
                            "size_mb": round(blob.size / (1024 * 1024), 2),
                            "updated": blob.updated.isoformat() if blob.updated else None,
                        }
                    )
                    found_files["total_size_bytes"] += blob.size
            except Exception as e:
                found_files["pipeline_root_search_error"] = str(e)

        # Strategy 2: Search in timestamped directories using extracted timestamp
        if timestamp_search:
            timestamped_dir = f"pipeline_runs/{timestamp_search}/"
            try:
                dir_blobs = bucket.list_blobs(prefix=timestamped_dir)
                for blob in dir_blobs:
                    blob_path = f"gs://{self.config.bucket_name}/{blob.name}"
                    if not any(f["path"] == blob_path for f in found_files["pipeline_runs"]):
                        found_files["pipeline_runs"].append(
                            {
                                "path": blob_path,
                                "size_bytes": blob.size,
                                "size_mb": round(blob.size / (1024 * 1024), 2),
                                "updated": blob.updated.isoformat() if blob.updated else None,
                            }
                        )
                        found_files["total_size_bytes"] += blob.size
            except Exception as e:
                found_files["timestamped_search_error"] = str(e)

        # Strategy 3: Fallback - search all timestamped directories for job name match
        if not found_files["pipeline_runs"]:  # Only if we haven't found anything yet
            timestamped_prefix = "pipeline_runs/"
            try:
                # List all timestamped directories
                blobs = bucket.list_blobs(prefix=timestamped_prefix, delimiter="/")
                for prefix in blobs.prefixes:
                    # Search within each timestamped directory for job name
                    dir_blobs = bucket.list_blobs(prefix=prefix)
                    for blob in dir_blobs:
                        if job_name.lower() in blob.name.lower():
                            blob_path = f"gs://{self.config.bucket_name}/{blob.name}"
                            if not any(
                                f["path"] == blob_path for f in found_files["pipeline_runs"]
                            ):
                                found_files["pipeline_runs"].append(
                                    {
                                        "path": blob_path,
                                        "size_bytes": blob.size,
                                        "size_mb": round(blob.size / (1024 * 1024), 2),
                                        "updated": blob.updated.isoformat()
                                        if blob.updated
                                        else None,
                                    }
                                )
                                found_files["total_size_bytes"] += blob.size
            except Exception as e:
                found_files["timestamped_fallback_error"] = str(e)

        # 3. Search for FASTA file
        fasta_path = f"fasta/{job_name}.fasta"
        try:
            fasta_blob = bucket.blob(fasta_path)
            if fasta_blob.exists():
                found_files["fasta"].append(
                    {
                        "path": f"gs://{self.config.bucket_name}/{fasta_path}",
                        "size_bytes": fasta_blob.size,
                        "size_mb": round(fasta_blob.size / (1024 * 1024), 2),
                        "updated": fasta_blob.updated.isoformat() if fasta_blob.updated else None,
                    }
                )
                if include_fasta:
                    found_files["total_size_bytes"] += fasta_blob.size
        except Exception as e:
            found_files["fasta_search_error"] = str(e)

        # Calculate total size
        total_mb = round(found_files["total_size_bytes"] / (1024 * 1024), 2)
        total_gb = round(found_files["total_size_bytes"] / (1024 * 1024 * 1024), 2)

        result = {
            "job_id": job.name if job_exists else job_id,
            "job_name": job_name,
            "job_exists_in_vertex": job_exists,
            "search_only": search_only,
            "files_found": found_files,
            "summary": {
                "pipeline_files": len(found_files["pipeline_runs"]),
                "fasta_files": len(found_files["fasta"]),
                "total_size_mb": total_mb,
                "total_size_gb": total_gb,
            },
        }

        if not job_exists:
            result["note_vertex_deleted"] = (
                f"Job '{job_name}' no longer exists in Agent Platform. "
                f"Searching GCS using the provided job name/ID. "
                f"If no files are found, verify the exact job name."
            )

        # Handle deletion if requested
        if not search_only:
            if not confirm_delete:
                raise ValueError(
                    f"Deletion requires confirmation. Set confirm_delete=True to proceed. "
                    f"This will delete {len(found_files['pipeline_runs'])} pipeline files "
                    f"({total_mb} MB) and cannot be undone."
                )

            deleted_files = []
            deletion_errors = []

            # Delete pipeline run files
            for file_info in found_files["pipeline_runs"]:
                try:
                    blob_name = file_info["path"].replace(f"gs://{self.config.bucket_name}/", "")
                    blob = bucket.blob(blob_name)
                    blob.delete()
                    deleted_files.append(file_info["path"])
                except Exception as e:
                    deletion_errors.append({"file": file_info["path"], "error": str(e)})

            # Delete FASTA file if requested
            if include_fasta and found_files["fasta"]:
                for file_info in found_files["fasta"]:
                    try:
                        blob_name = file_info["path"].replace(
                            f"gs://{self.config.bucket_name}/", ""
                        )
                        blob = bucket.blob(blob_name)
                        blob.delete()
                        deleted_files.append(file_info["path"])
                    except Exception as e:
                        deletion_errors.append({"file": file_info["path"], "error": str(e)})

            result["deletion"] = {
                "status": "completed" if not deletion_errors else "completed_with_errors",
                "deleted_count": len(deleted_files),
                "deleted_files": deleted_files,
                "errors": deletion_errors if deletion_errors else None,
            }

        else:
            result["note"] = (
                f"Search only mode. Found {len(found_files['pipeline_runs'])} pipeline files "
                f"and {len(found_files['fasta'])} FASTA file(s) totaling {total_mb} MB. "
                f"Set search_only=False and confirm_delete=True to delete these files."
            )

        return result
