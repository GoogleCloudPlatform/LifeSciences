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

"""Tool for deleting AlphaFold2 pipeline jobs."""

import logging
import time
from typing import Any, Dict

from google.cloud import aiplatform as vertex_ai

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job

logger = logging.getLogger(__name__)

# States where a job is still active and must be cancelled before deletion
_ACTIVE_STATES = {
    "PIPELINE_STATE_QUEUED",
    "PIPELINE_STATE_PENDING",
    "PIPELINE_STATE_RUNNING",
    "PIPELINE_STATE_CANCELLING",
    "PIPELINE_STATE_PAUSED",
}

# Max time to wait for cancellation before giving up
_CANCEL_TIMEOUT_SECONDS = 120
_CANCEL_POLL_INTERVAL = 5


class AF2DeleteJobTool(AF2Tool):
    """Tool for deleting AlphaFold2 pipeline jobs."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Delete a pipeline job from Agent Platform.

        If the job is still running, it will be cancelled first and then
        deleted once cancellation completes.

        This removes the job metadata and history from Agent Platform Pipelines.
        Note: This does NOT delete the output files in GCS - those must be
        deleted separately.

        Args:
            arguments: {
                'job_id': Job ID to delete,
                'confirm': Must be True to proceed with deletion (safety check)
            }

        Returns:
            Deletion confirmation details
        """
        job_id = arguments.get("job_id")
        confirm = arguments.get("confirm", False)

        if not job_id:
            raise ValueError("job_id is required")

        if not confirm:
            raise ValueError(
                "Deletion requires confirmation. Set confirm=True to proceed. "
                "WARNING: This action cannot be undone."
            )

        # Get job details before deletion
        job = get_pipeline_job(job_id, self.config.project_id, self.config.region)

        job_info = {
            "job_id": job.name,
            "job_name": job.display_name,
            "state": job.state.name,
            "created": job.create_time.isoformat() if job.create_time else None,
        }

        # Initialize Agent Platform
        vertex_ai.init(project=self.config.project_id, location=self.config.region)

        # Get the pipeline job using the resource name
        pipeline_job = vertex_ai.PipelineJob.get(resource_name=job.name)

        was_cancelled = False

        # If job is active, cancel it first
        if job.state.name in _ACTIVE_STATES:
            if job.state.name != "PIPELINE_STATE_CANCELLING":
                logger.info(
                    f"Job {job.display_name} is {job.state.name}, cancelling before delete..."
                )
                pipeline_job.cancel()
                was_cancelled = True

            # Wait for cancellation to complete
            elapsed = 0
            while elapsed < _CANCEL_TIMEOUT_SECONDS:
                pipeline_job = vertex_ai.PipelineJob.get(resource_name=job.name)
                current_state = pipeline_job.state.name
                if current_state not in _ACTIVE_STATES:
                    logger.info(f"Job reached state {current_state}, proceeding with delete.")
                    break
                time.sleep(_CANCEL_POLL_INTERVAL)
                elapsed += _CANCEL_POLL_INTERVAL
            else:
                return {
                    "status": "cancel_timeout",
                    "job": job_info,
                    "message": (
                        f"Job {job.display_name} was cancelled but did not reach a "
                        f"terminal state within {_CANCEL_TIMEOUT_SECONDS}s. "
                        f"Current state: {pipeline_job.state.name}. "
                        f"Try deleting again later."
                    ),
                }

        # Delete the job
        pipeline_job.delete()

        status = "cancelled_and_deleted" if was_cancelled else "deleted"
        action = "Cancelled and deleted" if was_cancelled else "Deleted"

        return {
            "status": status,
            "deleted_job": job_info,
            "message": f"Successfully {action.lower()} pipeline job: {job.display_name}",
            "note": (
                "Job metadata has been removed from Agent Platform. "
                "Output files in GCS were NOT deleted and must be removed manually if needed."
            ),
            "gcs_cleanup_hint": (
                f"To delete GCS outputs, use: "
                f"gcloud storage rm --recursive gs://{self.config.bucket_name}/pipeline_runs/*{job.display_name}*"
            ),
        }
