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

"""Tool for deep analysis of AlphaFold jobs (any state)."""

import logging
import re
from typing import Any, Dict, List, Optional

from google.cloud import aiplatform
from google.cloud import logging as cloud_logging

from ..base import AF2Tool

logger = logging.getLogger(__name__)


class AF2AnalyzeJobDeepTool(AF2Tool):
    """Tool for comprehensive deep analysis of AlphaFold jobs in any state."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Perform analysis of a specific job with configurable detail level.

        Args:
            arguments: Dictionary containing:
                - job_id: The AlphaFold pipeline job ID to analyze
                - detail_level: Level of detail - 'summary' (quick overview), 'detailed' (full analysis with logs)
                  Default: 'detailed'

        Returns:
            Dictionary containing job information based on state and detail level
        """
        job_id = arguments.get("job_id")
        if not job_id:
            return {"error": "job_id is required"}

        detail_level = arguments.get("detail_level", "detailed")
        if detail_level not in ["summary", "detailed"]:
            return {"error": "detail_level must be 'summary' or 'detailed'"}

        try:
            logger.info(f"Performing {detail_level} analysis for job: {job_id}")

            # Initialize Agent Platform
            aiplatform.init(project=self.config.project_id, location=self.config.region)

            # Get the pipeline job
            job = aiplatform.PipelineJob.get(
                resource_name=f"projects/{self.config.project_id}/locations/{self.config.region}/pipelineJobs/{job_id}"
            )

            # Get job state
            state = job.state.name if job.state else "UNKNOWN"

            # Base analysis info
            analysis = {
                "job_id": job_id,
                "display_name": job.display_name,
                "state": state,
                "create_time": str(job.create_time) if hasattr(job, "create_time") else None,
                "console_url": (
                    f"https://console.cloud.google.com/vertex-ai/locations/{self.config.region}/"
                    f"pipelines/runs/{job_id}?project={self.config.project_id}"
                ),
                "analysis_type": None,
            }

            # State-specific analysis
            if state == "PIPELINE_STATE_FAILED":
                analysis["analysis_type"] = "failure_analysis"
                analysis.update(self._analyze_failure(job, detail_level))

            elif state == "PIPELINE_STATE_SUCCEEDED":
                analysis["analysis_type"] = "success_summary"
                analysis.update(self._analyze_success(job, detail_level))

            elif state in ["PIPELINE_STATE_RUNNING", "PIPELINE_STATE_PENDING"]:
                analysis["analysis_type"] = "progress_tracking"
                analysis.update(self._analyze_progress(job, detail_level))

            else:
                analysis["analysis_type"] = "general_info"
                analysis["message"] = f"Job in state: {state}"

            # Add task details for all states - check gca_resource.job_detail
            task_details = []
            if hasattr(job, "gca_resource") and job.gca_resource:
                if hasattr(job.gca_resource, "job_detail") and job.gca_resource.job_detail:
                    job_detail = job.gca_resource.job_detail
                    if hasattr(job_detail, "task_details"):
                        task_details = job_detail.task_details

            if task_details:
                analysis["task_summary"] = {
                    "total_tasks": len(task_details),
                    "tasks": [
                        {
                            "name": task.task_name if hasattr(task, "task_name") else "Unknown",
                            "state": str(task.state) if hasattr(task, "state") else "UNKNOWN",
                        }
                        for task in task_details
                    ],
                }

            logger.info(f"Completed deep analysis for {job_id} (type: {analysis['analysis_type']})")
            return analysis

        except Exception as e:
            logger.error(f"Error analyzing job {job_id}: {e}", exc_info=True)
            return {"error": f"Failed to analyze job: {str(e)}", "job_id": job_id}

    def _analyze_failure(self, job, detail_level: str = "detailed") -> Dict[str, Any]:
        """Analyze a failed job.

        Args:
            job: The pipeline job object
            detail_level: 'summary' for quick overview, 'detailed' for logs
        """
        failure_info = {
            "is_failed": True,
            "error_message": None,
            "error_code": None,
            "failed_tasks": [],
            "task_errors": [],
        }

        # Get job-level error
        if hasattr(job, "error") and job.error:
            failure_info["error_message"] = (
                str(job.error.message) if hasattr(job.error, "message") else str(job.error)
            )
            failure_info["error_code"] = job.error.code if hasattr(job.error, "code") else None

        # Get task-level errors - check gca_resource.job_detail
        task_details = []
        if hasattr(job, "gca_resource") and job.gca_resource:
            if hasattr(job.gca_resource, "job_detail") and job.gca_resource.job_detail:
                job_detail = job.gca_resource.job_detail
                if hasattr(job_detail, "task_details"):
                    task_details = job_detail.task_details

        for task in task_details:
            # Check if task failed - state value 7 = FAILED or has a meaningful error
            is_failed = False
            if hasattr(task, "state") and task.state:
                # State enum value 7 = FAILED
                is_failed = task.state == 7 or "FAILED" in str(task.state)

            # Also check for error presence with non-zero code or non-empty message
            if not is_failed and hasattr(task, "error") and task.error:
                has_error_code = (
                    hasattr(task.error, "code") and task.error.code and task.error.code != 0
                )
                has_error_msg = (
                    hasattr(task.error, "message")
                    and task.error.message
                    and task.error.message.strip()
                )
                is_failed = has_error_code or has_error_msg

            if is_failed:
                task_name = task.task_name if hasattr(task, "task_name") else "Unknown"
                failure_info["failed_tasks"].append(task_name)

                if hasattr(task, "error") and task.error:
                    error_msg = (
                        str(task.error.message)
                        if hasattr(task.error, "message")
                        else str(task.error)
                    )
                    error_code = task.error.code if hasattr(task.error, "code") else None

                    # Only add to task_errors if there's meaningful error info
                    if error_msg.strip() or (error_code and error_code != 0):
                        task_error = {"task": task_name, "message": error_msg, "code": error_code}

                        # Try to fetch detailed logs only if detail_level is 'detailed'
                        if detail_level == "detailed":
                            custom_job_id = self._extract_job_id_from_error(error_msg)
                            if custom_job_id:
                                logger.info(f"Fetching logs for Custom Job {custom_job_id}")
                                # Fetch only top 5 ERROR logs to avoid response size issues
                                logs = self._fetch_task_logs(custom_job_id, max_entries=5)
                                if logs:
                                    task_error["detailed_logs"] = logs
                                    task_error["logs_count"] = len(logs)

                        failure_info["task_errors"].append(task_error)

        # Add diagnostic guidance based on failed tasks
        failure_info["diagnostic_guidance"] = self._get_diagnostic_guidance(
            failure_info["failed_tasks"]
        )

        return failure_info

    def _analyze_success(self, job, detail_level: str = "detailed") -> Dict[str, Any]:
        """Analyze a successful job.

        Args:
            job: The pipeline job object
            detail_level: 'summary' for quick overview, 'detailed' for full info
        """
        success_info = {
            "is_successful": True,
            "end_time": str(job.end_time) if hasattr(job, "end_time") else None,
            "duration": None,
        }

        # Calculate duration
        if (
            hasattr(job, "create_time")
            and hasattr(job, "end_time")
            and job.create_time
            and job.end_time
        ):
            duration = job.end_time - job.create_time
            success_info["duration"] = str(duration)

        # Get output artifacts/GCS paths
        if hasattr(job, "gca_resource") and hasattr(job.gca_resource, "job_detail"):
            job_detail = job.gca_resource.job_detail
            if hasattr(job_detail, "pipeline_context"):
                success_info["outputs_note"] = (
                    "Use get_prediction_results or analyze_prediction_quality to retrieve outputs"
                )

        return success_info

    def _analyze_progress(self, job, detail_level: str = "detailed") -> Dict[str, Any]:
        """Analyze a running/pending job.

        Args:
            job: The pipeline job object
            detail_level: 'summary' for quick overview, 'detailed' for full info
        """
        progress_info = {
            "is_running": True,
            "start_time": str(job.start_time) if hasattr(job, "start_time") else None,
        }

        # Get current progress from tasks - check gca_resource.job_detail
        task_details = []
        if hasattr(job, "gca_resource") and job.gca_resource:
            if hasattr(job.gca_resource, "job_detail") and job.gca_resource.job_detail:
                job_detail = job.gca_resource.job_detail
                if hasattr(job_detail, "task_details"):
                    task_details = job_detail.task_details

        if task_details:
            completed = sum(
                1 for t in task_details if hasattr(t, "state") and "SUCCEEDED" in str(t.state)
            )
            running = sum(
                1 for t in task_details if hasattr(t, "state") and "RUNNING" in str(t.state)
            )
            total = len(task_details)

            progress_info["progress"] = {
                "completed_tasks": completed,
                "running_tasks": running,
                "total_tasks": total,
                "percentage": round((completed / total * 100) if total > 0 else 0, 1),
            }

            # Find current running task
            for task in task_details:
                if hasattr(task, "state") and "RUNNING" in str(task.state):
                    progress_info["current_task"] = (
                        task.task_name if hasattr(task, "task_name") else "Unknown"
                    )
                    break

        return progress_info

    def _extract_job_id_from_error(self, error_message: str) -> Optional[str]:
        """Extract Custom Job ID from error message containing log URL."""
        if not error_message:
            return None

        # Look for job_id in the log URL
        # Format: resource=ml_job%2Fjob_id%2F8575487060750630912
        match = re.search(r"job_id%2F(\d+)", error_message)
        if match:
            return match.group(1)

        # Also try direct job_id pattern
        match = re.search(r"job_id[/=](\d+)", error_message)
        if match:
            return match.group(1)

        return None

    def _fetch_task_logs(self, custom_job_id: str, max_entries: int = 5) -> List[Dict[str, Any]]:
        """Fetch logs for a failed Custom Job from Cloud Logging.

        Args:
            custom_job_id: The Custom Job ID (e.g., '8575487060750630912')
            max_entries: Maximum number of log entries to fetch (default: 5 to avoid response size issues)

        Returns:
            List of log entries with timestamp, severity, and message (filtered for actionable errors only)
        """
        try:
            logging_client = cloud_logging.Client(project=self.config.project_id)

            # Build filter for this specific job - ERROR severity only
            # Logs are stored with resource.type="ml_job" and resource.labels.job_id
            filter_str = f'''
                resource.type="ml_job"
                resource.labels.job_id="{custom_job_id}"
                severity>=ERROR
            '''

            entries = []
            for entry in logging_client.list_entries(
                filter_=filter_str, order_by=cloud_logging.DESCENDING, max_results=max_entries
            ):
                # Extract message
                message = entry.payload if isinstance(entry.payload, str) else str(entry.payload)

                # Filter out empty or non-actionable messages
                if message and message.strip() and message != "''":
                    entries.append(
                        {
                            "timestamp": str(entry.timestamp),
                            "severity": entry.severity,
                            "message": message,
                        }
                    )

            return entries

        except Exception as e:
            logger.warning(f"Failed to fetch logs for job {custom_job_id}: {e}")
            return []

    def _get_diagnostic_guidance(self, failed_tasks: List[str]) -> Dict[str, Any]:
        """Get diagnostic guidance based on which tasks failed."""
        guidance = {"likely_causes": [], "recommended_actions": []}

        # Check for common failure patterns
        if any("data-pipeline" in task for task in failed_tasks):
            guidance["likely_causes"].extend(
                [
                    "Invalid sequence format (non-standard amino acids, lowercase letters)",
                    "Sequence too short (<10 residues) or empty",
                    "MSA generation failure (database connectivity issues)",
                    "Template search failure (PDB/mmCIF download issues)",
                ]
            )
            guidance["recommended_actions"].extend(
                [
                    "Verify sequence contains only standard amino acids (ACDEFGHIKLMNPQRSTVWY)",
                    "Check sequence is properly formatted in FASTA format",
                    "Ensure sequence length is appropriate (typically >10 residues)",
                    "Review logs in Console URL for specific error messages",
                ]
            )

        if any("alphafold" in task or "predict" in task for task in failed_tasks):
            guidance["likely_causes"].extend(
                [
                    "GPU out of memory (sequence too long for selected GPU type)",
                    "Invalid MSA input from upstream data-pipeline",
                    "Model checkpoint loading failure",
                    "Insufficient disk space for prediction outputs",
                ]
            )
            guidance["recommended_actions"].extend(
                [
                    "Try using a larger GPU type (L4 → A100 → A100-80GB)",
                    "Check if data-pipeline task completed successfully",
                    "Verify GCS bucket permissions and quota",
                ]
            )

        if not guidance["likely_causes"]:
            guidance["likely_causes"] = ["Unknown failure - check Console URL for detailed logs"]
            guidance["recommended_actions"] = ["Review task logs in Agent Platform Console"]

        return guidance

    def get_parameters_schema(self) -> Dict[str, Any]:
        """Return the JSON schema for the tool's parameters.

        Returns:
            JSON schema dict for tool parameters
        """
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": (
                        "The AlphaFold pipeline job ID to analyze (e.g., "
                        "'alphafold2-inference-pipeline-20251110082144')"
                    ),
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "default": "detailed",
                    "description": (
                        "Level of detail: 'summary' for quick overview (no log fetching), "
                        "'detailed' for comprehensive analysis with Cloud Logging error logs. "
                        "Default: 'detailed'"
                    ),
                },
            },
            "required": ["job_id"],
        }
