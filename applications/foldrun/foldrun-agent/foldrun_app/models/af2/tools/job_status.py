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

"""Tool for checking AlphaFold2 job status."""

import logging
from typing import Any, Dict

from ..base import AF2Tool
from ..utils.vertex_utils import get_job_status, get_pipeline_job, get_task_details

logger = logging.getLogger(__name__)


class AF2JobStatusTool(AF2Tool):
    """Tool for checking status of AlphaFold2 prediction jobs."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check job status with concise summary appropriate for the job state.

        Args:
            arguments: {
                'job_id': Full job ID or job name
            }

        Returns:
            Job status summary (NOT full task details)
        """
        job_id = arguments.get("job_id")

        if not job_id:
            raise ValueError("job_id is required")

        # Get pipeline job
        job = get_pipeline_job(job_id, self.config.project_id, self.config.region)

        # Extract status
        status = get_job_status(job)
        state = status["state"]

        # Get task details for progress calculation
        task_details = get_task_details(job)
        tasks = task_details.get("tasks", {})

        # Build concise result based on job state
        result = {
            "job_id": status["job_id"],
            "display_name": status["display_name"],
            "state": state,
            "create_time": status["create_time"],
            "start_time": status["start_time"],
        }

        if state == "PIPELINE_STATE_SUCCEEDED":
            # For completed jobs, show summary info
            result["end_time"] = status["end_time"]
            result["duration_seconds"] = status.get("duration_seconds")
            result["message"] = "Job completed successfully"

            # Show only best prediction summary
            predictions = task_details.get("predictions", [])
            if predictions:
                best = predictions[0]
                result["best_prediction"] = {
                    "model_name": best.get("model_name"),
                    "ranking_confidence": best.get("ranking_confidence"),
                }

        elif state == "PIPELINE_STATE_FAILED":
            # For failed jobs, show error details
            result["end_time"] = status["end_time"]
            result["message"] = "Job failed"
            if "error_message" in status:
                result["error_message"] = status["error_message"]

            # Find which task failed
            failed_tasks = [name for name, info in tasks.items() if info.get("state") == "FAILED"]
            if failed_tasks:
                result["failed_task"] = failed_tasks[0]
                # Include error from failed task
                failed_task_info = tasks[failed_tasks[0]]
                if "error" in failed_task_info:
                    result["task_error"] = failed_task_info["error"]

        elif state == "PIPELINE_STATE_RUNNING":
            # For running jobs, show progress summary
            result["message"] = "Job is currently running"

            # Count task states
            total_tasks = len(tasks)
            completed_tasks = sum(1 for t in tasks.values() if t.get("state") == "SUCCEEDED")
            running_tasks = [name for name, t in tasks.items() if t.get("state") == "RUNNING"]
            pending_tasks = sum(
                1 for t in tasks.values() if t.get("state") in ["PENDING", "QUEUED"]
            )

            result["progress"] = {
                "total_tasks": total_tasks,
                "completed": completed_tasks,
                "running": len(running_tasks),
                "pending": pending_tasks,
            }

            # Show current running task(s)
            if running_tasks:
                result["current_step"] = running_tasks[0]
                # Extract task type from name (e.g., "data-pipeline-123" -> "data-pipeline")
                task_type = (
                    "-".join(running_tasks[0].split("-")[:-1])
                    if "-" in running_tasks[0]
                    else running_tasks[0]
                )
                result["message"] = (
                    f"Job is running {task_type} step ({completed_tasks}/{total_tasks} tasks completed)"
                )

        elif state == "PIPELINE_STATE_PENDING":
            # For pending jobs
            result["message"] = "Job is pending"
            total_tasks = len(tasks)
            if total_tasks > 0:
                result["total_tasks"] = total_tasks
        else:
            result["message"] = f"Job state: {state}"

        logger.info(f"Retrieved status for job {job_id}: {state}")

        return result
