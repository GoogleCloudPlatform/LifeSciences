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

"""Tool for finding GCS files that don't have corresponding Vertex AI jobs."""

import logging
from typing import Any, Dict

from google.cloud import storage

from foldrun_app.app_utils.gcs_retry import GCS_RETRY

from ..base import AF2Tool
from ..utils.vertex_utils import list_pipeline_jobs

logger = logging.getLogger(__name__)


class AF2FindOrphanedGCSFilesTool(AF2Tool):
    """Tool for finding GCS files without corresponding Vertex AI jobs."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Find GCS files that don't have corresponding Vertex AI pipeline jobs.

        This helps identify orphaned files from deleted jobs that are still
        consuming storage space.

        Args:
            arguments: {
                'check_fasta': Also check for orphaned FASTA files (default: True),
                'max_jobs_to_check': Maximum number of jobs to retrieve from Vertex AI (default: 1000)
            }

        Returns:
            List of orphaned directories and files with sizes
        """
        # Extract parameters
        check_fasta = arguments.get("check_fasta", True)
        max_jobs_to_check = arguments.get("max_jobs_to_check", 1000)

        # Step 1: Get all job names and pipeline roots from Vertex AI
        logger.info("Fetching all pipeline jobs from Vertex AI...")
        # Vertex AI API has a max page_size of 100, but list_pipeline_jobs handles pagination
        # So we can still retrieve max_jobs_to_check jobs, just in smaller pages
        page_size = min(max_jobs_to_check, 100)  # Cap at 100 per API limit
        jobs = list_pipeline_jobs(
            project_id=self.config.project_id, region=self.config.region, page_size=page_size
        )

        # Limit to max_jobs_to_check if we got more than requested
        if len(jobs) > max_jobs_to_check:
            jobs = jobs[:max_jobs_to_check]

        vertex_job_names = set()
        vertex_pipeline_roots = set()
        for job in jobs:
            vertex_job_names.add(job.display_name)

            # Extract pipeline root from runtime_config.gcs_output_directory
            # This is where Vertex AI stores the actual output directory for the pipeline
            if hasattr(job, "runtime_config") and job.runtime_config:
                if (
                    hasattr(job.runtime_config, "gcs_output_directory")
                    and job.runtime_config.gcs_output_directory
                ):
                    pipeline_root = job.runtime_config.gcs_output_directory
                    logger.debug(f"Found pipeline root for {job.display_name}: {pipeline_root}")

                    # Extract the path after the bucket name
                    # Example: gs://bucket-name/pipeline_runs/20251112_172826 -> pipeline_runs/20251112_172826
                    if pipeline_root.startswith("gs://"):
                        root_path = "/".join(pipeline_root.split("/")[3:])  # Skip gs://bucket/
                        vertex_pipeline_roots.add(root_path)
                        logger.debug(f"Added pipeline root: {root_path}")

        logger.info(
            f"Found {len(vertex_job_names)} jobs and {len(vertex_pipeline_roots)} pipeline roots in Vertex AI"
        )

        # Step 2: Scan GCS for pipeline run directories
        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(self.config.bucket_name)

        orphaned_dirs = []
        total_orphaned_size = 0

        # Check timestamped directories
        logger.info("Scanning timestamped directories...")
        logger.info(f"Active pipeline roots to match against: {vertex_pipeline_roots}")
        timestamped_prefix = "pipeline_runs/"

        # Get all timestamped directories
        # Note: We need to consume the iterator before accessing .prefixes
        blobs_iterator = bucket.list_blobs(prefix=timestamped_prefix, delimiter="/", retry=GCS_RETRY)

        # Consume the iterator to populate the prefixes
        _ = list(blobs_iterator)  # This forces the iterator to fetch all pages

        # Now we can access the prefixes
        prefixes_found = list(blobs_iterator.prefixes)
        logger.info(f"Found {len(prefixes_found)} top-level directories in {timestamped_prefix}")

        for prefix in prefixes_found:
            logger.info(f"Checking directory: {prefix}")

            # Check if this directory path matches any active job's pipeline_root
            # Remove trailing slash for comparison
            dir_path = prefix.rstrip("/")

            # For matching, we need the full path starting from pipeline_runs/
            # Example: pipeline_runs/20251110_073955

            # Check if this exact path is in the set of active pipeline roots
            is_active = False
            for root in vertex_pipeline_roots:
                # Match both:
                # 1. Exact match of the directory path
                # 2. If root starts with this directory (for nested structures)
                if dir_path in root or root.startswith(dir_path):
                    is_active = True
                    logger.info(f"Directory {dir_path} matched active pipeline root: {root}")
                    break

            if not is_active:
                # This directory is orphaned
                logger.info(f"Directory {dir_path} is ORPHANED (no matching pipeline root)")
                dir_blobs = list(bucket.list_blobs(prefix=prefix, retry=GCS_RETRY))
                dir_size = sum(blob.size for blob in dir_blobs)

                orphaned_dirs.append(
                    {
                        "path": f"gs://{self.config.bucket_name}/{prefix}",
                        "job_name": "unknown (timestamped directory)",
                        "file_count": len(dir_blobs),
                        "size_bytes": dir_size,
                        "size_mb": round(dir_size / (1024 * 1024), 2),
                        "size_gb": round(dir_size / (1024 * 1024 * 1024), 2),
                        "note": f"Timestamped directory: {dir_path}",
                    }
                )
                total_orphaned_size += dir_size

        # Step 3: Check for orphaned FASTA files
        orphaned_fasta = []
        if check_fasta:
            logger.info("Scanning FASTA files...")
            fasta_blobs = bucket.list_blobs(prefix="fasta/", retry=GCS_RETRY)

            for blob in fasta_blobs:
                # Extract job name from fasta/job-name.fasta
                if blob.name.endswith(".fasta"):
                    fasta_job_name = blob.name.replace("fasta/", "").replace(".fasta", "")

                    if fasta_job_name not in vertex_job_names:
                        orphaned_fasta.append(
                            {
                                "path": f"gs://{self.config.bucket_name}/{blob.name}",
                                "job_name": fasta_job_name,
                                "size_bytes": blob.size,
                                "size_kb": round(blob.size / 1024, 2),
                                "updated": blob.updated.isoformat() if blob.updated else None,
                            }
                        )
                        total_orphaned_size += blob.size

        # Calculate totals
        total_mb = round(total_orphaned_size / (1024 * 1024), 2)
        total_gb = round(total_orphaned_size / (1024 * 1024 * 1024), 2)

        return {
            "vertex_jobs_checked": len(vertex_job_names),
            "orphaned_pipeline_dirs": orphaned_dirs,
            "orphaned_fasta_files": orphaned_fasta,
            "summary": {
                "orphaned_pipeline_dirs_count": len(orphaned_dirs),
                "orphaned_fasta_count": len(orphaned_fasta),
                "total_orphaned_size_mb": total_mb,
                "total_orphaned_size_gb": total_gb,
                "potential_savings": f"${round(total_gb * 0.02, 2)}/month"
                if total_gb > 0
                else "$0/month",
            },
            "cleanup_instructions": {
                "method_1": "Use cleanup_gcs_files tool for each orphaned job name",
                "method_2": "Use gcloud storage to delete directories: gcloud storage rm --recursive gs://path/to/directory/",
                "warning": "Always verify paths before deletion - this action cannot be undone!",
            },
        }
