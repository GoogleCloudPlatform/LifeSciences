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

"""Tool for checking status of genetic database download Cloud Batch jobs."""

import logging
from typing import Any, Dict

from google.cloud import batch_v1

from ..base import AF2Tool

logger = logging.getLogger(__name__)


class AF2CheckDatabaseDownloadTool(AF2Tool):
    """Check the status of genetic database download Cloud Batch jobs."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments.get("job_id")
        list_all = arguments.get("list_all", False)

        if job_id:
            return self._check_single_job(job_id)
        elif list_all:
            return self._list_download_jobs()
        else:
            return {
                "status": "error",
                "message": "Provide either 'job_id' to check a specific job or 'list_all=true' to list all download jobs.",
            }

    def _check_single_job(self, job_id: str) -> Dict[str, Any]:
        """Check status of a single Cloud Batch download job."""
        try:
            client = batch_v1.BatchServiceClient()

            # Build the full resource name if just an ID was provided
            if "/" not in job_id:
                resource_name = (
                    f"projects/{self.config.project_id}/"
                    f"locations/{self.config.region}/"
                    f"jobs/{job_id}"
                )
            else:
                resource_name = job_id

            job = client.get_job(request=batch_v1.GetJobRequest(name=resource_name))

            labels = dict(job.labels) if job.labels else {}
            database_name = labels.get("database", "unknown")

            result = {
                "status": "success",
                "job_id": job_id,
                "job_name": job.name,
                "state": str(job.status.state.name) if job.status else "UNKNOWN",
                "database_name": database_name,
                "create_time": str(job.create_time) if job.create_time else None,
                "update_time": str(job.update_time) if job.update_time else None,
                "labels": labels,
            }

            # Add task-level status if available
            if job.status and job.status.status_events:
                result["events"] = [
                    {
                        "type": e.type_,
                        "description": e.description,
                        "event_time": str(e.event_time) if e.event_time else None,
                    }
                    for e in job.status.status_events[-5:]  # Last 5 events
                ]

            # Add task status counts
            if job.status and job.status.task_groups:
                for tg_name, tg_status in job.status.task_groups.items():
                    counts = {}
                    for count_entry in tg_status.counts:
                        counts[count_entry.state.name] = count_entry.count
                    result["task_counts"] = counts

            # Check GCS output if job succeeded
            state_name = result.get("state", "")
            if state_name == "SUCCEEDED":
                gcs_path = f"gs://{self.config.databases_bucket_name}/{database_name}/"
                gcs_info = self._check_gcs_output(gcs_path)
                result["gcs_output"] = gcs_info

            return result

        except Exception as e:
            logger.error(f"Failed to check job {job_id}: {e}", exc_info=True)
            return {
                "status": "error",
                "job_id": job_id,
                "message": f"Failed to check job status: {str(e)}",
            }

    def _list_download_jobs(self) -> Dict[str, Any]:
        """List all database download Cloud Batch jobs."""
        try:
            client = batch_v1.BatchServiceClient()

            parent = f"projects/{self.config.project_id}/locations/{self.config.region}"
            request = batch_v1.ListJobsRequest(
                parent=parent,
                filter="labels.task=database-download",
            )

            job_list = []
            for job in client.list_jobs(request=request):
                labels = dict(job.labels) if job.labels else {}
                state = str(job.status.state.name) if job.status else "UNKNOWN"

                job_info = {
                    "job_id": job.name.split("/")[-1],
                    "display_name": job.name.split("/")[-1],
                    "database_name": labels.get("database", "unknown"),
                    "state": state,
                    "create_time": str(job.create_time) if job.create_time else None,
                }
                job_list.append(job_info)

            # Sort by create time descending
            job_list.sort(key=lambda j: j.get("create_time", ""), reverse=True)

            return {
                "status": "success",
                "total_jobs": len(job_list),
                "jobs": job_list,
            }

        except Exception as e:
            logger.error(f"Failed to list download jobs: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Failed to list download jobs: {str(e)}",
            }

    def _check_gcs_output(self, gcs_path: str) -> Dict[str, Any]:
        """Check GCS for downloaded database files."""
        try:
            path = gcs_path.replace("gs://", "")
            bucket_name = path.split("/")[0]
            prefix = "/".join(path.split("/")[1:])

            bucket = self.storage_client.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=prefix, max_results=100))

            total_size_bytes = sum(b.size for b in blobs if b.size)
            total_size_gb = round(total_size_bytes / (1024**3), 2)

            file_list = [
                {
                    "name": b.name.split("/")[-1],
                    "size_mb": round(b.size / (1024**2), 2) if b.size else 0,
                }
                for b in blobs[:20]
                if b.name and not b.name.endswith("/")
            ]

            return {
                "gcs_path": gcs_path,
                "file_count": len(blobs),
                "total_size_gb": total_size_gb,
                "files": file_list,
                "truncated": len(blobs) > 20,
            }

        except Exception as e:
            logger.warning(f"Failed to check GCS output at {gcs_path}: {e}")
            return {
                "gcs_path": gcs_path,
                "error": str(e),
            }
