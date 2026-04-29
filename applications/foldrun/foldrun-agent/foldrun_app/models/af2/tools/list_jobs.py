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

"""Tool for listing AlphaFold2 jobs."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from ..base import AF2Tool
from ..utils.vertex_utils import get_job_status, list_pipeline_jobs

logger = logging.getLogger(__name__)


class AF2ListJobsTool(AF2Tool):
    """Tool for listing AlphaFold2 prediction jobs."""

    def _format_duration(self, seconds: Optional[float]) -> str:
        """Format duration in human-readable format."""
        if seconds is None:
            return "N/A"

        if seconds < 60:
            return f"{int(seconds)} sec"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes} min {secs} sec"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours} hr {minutes} min"

    def _format_state(self, state: str) -> str:
        """Format pipeline state for readability."""
        state_map = {
            "PIPELINE_STATE_SUCCEEDED": "Succeeded",
            "PIPELINE_STATE_FAILED": "Failed",
            "PIPELINE_STATE_RUNNING": "Running",
            "PIPELINE_STATE_PENDING": "Pending",
            "PIPELINE_STATE_CANCELLED": "Cancelled",
            "PIPELINE_STATE_CANCELLING": "Cancelling",
            "PIPELINE_STATE_PAUSED": "Paused",
        }
        return state_map.get(state, state.replace("PIPELINE_STATE_", "").title())

    def _get_analysis_path(self, job: Any) -> str:
        """Get GCS analysis path for a job.

        Stores analysis results at the pipeline root level (gcs_output_directory).

        Args:
            job: Agent Platform PipelineJob object

        Returns:
            GCS path for analysis files (e.g., gs://bucket/pipeline_runs/YYYYMMDD_HHMMSS/analysis/)
        """
        # Get the pipeline root from the job's runtime_config
        pipeline_root = None
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        if not pipeline_root:
            raise ValueError("Job does not have gcs_output_directory in runtime_config")

        # Ensure trailing slash
        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        return f"{pipeline_root}analysis/"

    def _check_analysis_exists_for_job(self, job) -> bool:
        """Check if analysis summary.json exists for a specific job.

        Uses a direct blob existence check (single API call per job)
        instead of scanning all blobs in the bucket.

        Args:
            job: Agent Platform PipelineJob object

        Returns:
            True if analysis summary.json exists for this job
        """
        try:
            analysis_path = self._get_analysis_path(job)

            if not analysis_path.startswith("gs://"):
                logger.warning(f"Invalid analysis_path format: {analysis_path}")
                return False

            parts = analysis_path[5:].split("/", 1)
            bucket_name = parts[0]
            blob_path = f"{parts[1]}summary.json" if len(parts) > 1 else "summary.json"

            bucket = self.storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            found = blob.exists()

            return found

        except Exception as e:
            job_id = job.name.split("/")[-1] if hasattr(job, "name") else "unknown"
            logger.debug(f"Error checking analysis for {job_id}: {e}")
            return False

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        List jobs with optional filters.

        Args:
            arguments: {
                'filter': Optional filter string (e.g., 'state="PIPELINE_STATE_RUNNING"'),
                'label_filter': Optional dict of label filters (e.g., {'job_type': 'monomer'}),
                'limit': Maximum number of jobs to return (default: 20),
                'state': Optional state filter ('running', 'failed', 'succeeded'),
                'job_type': Optional job type filter ('monomer', 'multimer'),
                'gpu_type': Optional GPU type filter ('l4', 'a100', 'a100-80gb'),
                'seq_name': Optional sequence name filter (partial match),
                'min_seq_length': Optional minimum sequence length,
                'max_seq_length': Optional maximum sequence length,
                'check_analysis': Optional boolean to check for analysis files (default: False, can be slow)
            }

        Returns:
            List of jobs with detailed metadata
        """
        filter_str = arguments.get("filter")
        label_filter = arguments.get("label_filter") or {}
        limit = arguments.get("limit", 20)
        check_analysis = arguments.get(
            "check_analysis", True
        )  # Default True - batch check is efficient

        # Client-side filters (applied after fetching)
        # NOTE: Agent Platform Pipelines API doesn't support filtering by state, so we apply it client-side
        seq_name_filter = arguments.get("seq_name")
        min_seq_length = arguments.get("min_seq_length")
        max_seq_length = arguments.get("max_seq_length")
        state_filter = arguments.get("state")

        # Map state filter to full state name for client-side filtering
        state_value = None
        if state_filter:
            state_map = {
                "running": "PIPELINE_STATE_RUNNING",
                "failed": "PIPELINE_STATE_FAILED",
                "succeeded": "PIPELINE_STATE_SUCCEEDED",
                "pending": "PIPELINE_STATE_PENDING",
                "cancelled": "PIPELINE_STATE_CANCELLED",
            }
            state_value = state_map.get(state_filter.lower(), state_filter.upper())

        # Add label-based convenience filters
        if arguments.get("job_type"):
            label_filter["job_type"] = arguments["job_type"]
        if arguments.get("gpu_type"):
            label_filter["gpu_type"] = arguments["gpu_type"].lower().replace("_", "-")

        # Build filter string from labels
        if label_filter:
            label_filters = [f'labels.{k}="{v}"' for k, v in label_filter.items()]
            if filter_str:
                filter_str = f"({filter_str}) AND ({' AND '.join(label_filters)})"
            else:
                filter_str = " AND ".join(label_filters)

        # List jobs - fetch more if using client-side filters
        needs_client_filter = seq_name_filter or min_seq_length or max_seq_length or state_value
        page_size = min(limit * 3 if needs_client_filter else limit * 2, 100)

        jobs = list_pipeline_jobs(
            project_id=self.config.project_id,
            region=self.config.region,
            filter_str=filter_str,
            page_size=page_size,
        )

        # Analysis checking is done per-job using direct blob existence checks

        # Extract job information with console-like formatting
        job_list = []
        for job in jobs:
            status = get_job_status(job)

            # Apply client-side filters
            labels = status["labels"]

            # Filter by state
            if state_value and status["state"] != state_value:
                continue

            # Filter by sequence name (partial match, case-insensitive)
            if seq_name_filter:
                seq_name_label = labels.get("seq_name", "")
                if seq_name_filter.lower() not in seq_name_label.lower():
                    continue

            # Filter by sequence length range
            if min_seq_length or max_seq_length:
                try:
                    seq_len = int(labels.get("seq_len", 0))
                    if min_seq_length and seq_len < min_seq_length:
                        continue
                    if max_seq_length and seq_len > max_seq_length:
                        continue
                except (ValueError, TypeError):
                    # Skip jobs without valid seq_len label
                    if min_seq_length or max_seq_length:
                        continue

            # Stop if we've collected enough jobs
            if len(job_list) >= limit:
                break

            # Calculate duration for running jobs
            duration_seconds = status.get("duration_seconds")
            if not duration_seconds and status.get("start_time"):
                start_time = datetime.fromisoformat(status["start_time"].replace("Z", "+00:00"))
                duration_seconds = (datetime.now(start_time.tzinfo) - start_time).total_seconds()

            # Extract labels
            labels = status["labels"]

            # Build console URL
            job_id = job.name.split("/")[-1]
            console_url = f"https://console.cloud.google.com/vertex-ai/locations/{self.config.region}/pipelines/runs/{job_id}?project={self.config.project_id}"

            # Check if analysis exists (only if requested and only for succeeded jobs)
            has_analysis = False
            if check_analysis and status["state"] == "PIPELINE_STATE_SUCCEEDED":
                has_analysis = self._check_analysis_exists_for_job(job)

            job_info = {
                "job_id": job_id,
                "full_job_id": job.name,
                "display_name": job.display_name,
                "status": self._format_state(status["state"]),
                "state_raw": status["state"],
                "pipeline": status["pipeline_spec"],
                "duration": self._format_duration(duration_seconds),
                "duration_seconds": duration_seconds,
                "created": status["create_time"],
                "started": status.get("start_time"),
                "ended": status.get("end_time"),
                "labels": labels,
                "console_url": console_url,
                "has_analysis": has_analysis,
            }

            if "error_message" in status:
                job_info["error_message"] = status["error_message"]

            # Add experiment-like grouping info
            if "job_type" in labels:
                job_info["experiment"] = f"AlphaFold {labels['job_type'].title()}"
            if "seq_name" in labels:
                job_info["sequence_name"] = labels["seq_name"]
            if "gpu_type" in labels:
                job_info["gpu_type"] = labels["gpu_type"]

            job_list.append(job_info)

        logger.info(f"Listed {len(job_list)} jobs (filter: {filter_str})")

        # Summary statistics
        summary = {
            "total_jobs": len(job_list),
            "running": sum(1 for j in job_list if "Running" in j["status"]),
            "failed": sum(1 for j in job_list if "Failed" in j["status"]),
            "succeeded": sum(1 for j in job_list if "Succeeded" in j["status"]),
            "pending": sum(1 for j in job_list if "Pending" in j["status"]),
        }

        return {
            "summary": summary,
            "jobs": job_list,
            "filter_applied": filter_str or "None",
            "showing": len(job_list),
            "limit": limit,
        }
