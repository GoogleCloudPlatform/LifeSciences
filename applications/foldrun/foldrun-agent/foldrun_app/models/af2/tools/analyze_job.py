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

"""Tool for triggering parallel analysis via Cloud Run Jobs."""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

from google.cloud import run_v2, storage

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job, get_task_details

logger = logging.getLogger(__name__)


class AF2JobAnalysisTool(AF2Tool):
    """Tool for triggering parallel analysis via Cloud Run Jobs."""

    def __init__(self, tool_config: Dict[str, Any], config: Any):
        super().__init__(tool_config, config)
        self.job_name = os.getenv("CLOUD_RUN_JOB_NAME", "af2-analysis-job")

    def _get_analysis_path(self, job: Any) -> str:
        """
        Get GCS analysis path for a job.

        Stores analysis results at the pipeline root level (gcs_output_directory).

        Args:
            job: Vertex AI PipelineJob object (not just job_id)

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

    def _write_to_gcs(self, gcs_uri: str, data: Dict[str, Any]) -> None:
        """Write JSON data to GCS."""
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        parts = gcs_uri[5:].split("/", 1)
        bucket_name = parts[0]
        blob_name = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")

        logger.info(f"Wrote data to {gcs_uri}")

    def _check_existing_analysis(self, analysis_path: str) -> tuple[bool, dict | None]:
        """Check if analysis already exists.

        Returns:
            Tuple of (exists: bool, summary_data: dict | None)
        """
        try:
            storage_client = storage.Client(project=self.config.project_id)
            parts = analysis_path[5:].split("/", 1)
            bucket = storage_client.bucket(parts[0])
            prefix = parts[1] if len(parts) > 1 else ""

            # Check for existing summary.json (contains expert analysis)
            summary_blob = bucket.blob(f"{prefix}summary.json")
            if summary_blob.exists():
                logger.info(f"Found existing summary.json at {analysis_path}summary.json")
                # Download and return the summary data
                content = summary_blob.download_as_string()
                summary_data = json.loads(content)
                return True, summary_data

            return False, None
        except Exception as e:
            logger.warning(f"Could not check for existing analysis: {e}")
            return False, None

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Trigger parallel analysis via Cloud Run Job.

        Args:
            arguments: {
                'job_id': Job ID (required),
                'top_n': Number of top predictions to return (default: 10),
                'overwrite': Overwrite existing analysis (default: False)
            }

        Returns:
            Status with execution details
        """
        job_id = arguments.get("job_id")
        top_n = arguments.get("top_n", 10)
        overwrite = arguments.get("overwrite", False)

        if not job_id:
            raise ValueError("job_id is required")

        logger.info(f"Starting parallel analysis for job {job_id}")

        # Get job and predictions
        try:
            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {str(e)}"}
        task_details = get_task_details(job)
        predictions = task_details.get("predictions", [])

        if not predictions:
            return {"status": "no_predictions", "message": "No predictions found for this job"}

        logger.info(f"Found {len(predictions)} predictions to analyze")

        # Determine analysis path (using the job object to get the actual pipeline root)
        analysis_path = self._get_analysis_path(job)

        # Check if analysis already exists (check for summary.json with expert analysis)
        exists, existing_summary = self._check_existing_analysis(analysis_path)
        if not overwrite and exists:
            logger.info(f"Analysis already exists for job {job_id} (found summary.json)")
            return {
                "status": "already_exists",
                "message": "Analysis already exists with expert analysis from Gemini. Use overwrite=true to re-analyze, or use get_analysis_results to retrieve the existing analysis.",
                "analysis_path": analysis_path,
                "summary_uri": f"{analysis_path}summary.json",
                # Include the existing summary data
                **existing_summary,
            }

        # Create metadata file
        metadata = {
            "job_id": job_id,
            "total_predictions": len(predictions),
            "started_at": datetime.utcnow().isoformat() + "Z",
            "status": "running",
            "top_n": top_n,
            "execution_method": "cloud_run_job",
        }

        self._write_to_gcs(f"{analysis_path}analysis_metadata.json", metadata)

        # Write task configuration file for Cloud Run Job to read
        # Each task will read this file and use CLOUD_RUN_TASK_INDEX to find its work
        task_config = {
            "job_id": job_id,
            "analysis_path": analysis_path,
            "task_config_uri": f"{analysis_path}task_config.json",
            "predictions": [
                {
                    "index": i,
                    "uri": pred["uri"],
                    "model_name": pred["model_name"],
                    "ranking_confidence": pred["ranking_confidence"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, pred in enumerate(predictions)
            ],
        }
        task_config_path = f"{analysis_path}task_config.json"
        self._write_to_gcs(task_config_path, task_config)

        # Also update metadata to include task config URI
        metadata["task_config_uri"] = task_config_path
        self._write_to_gcs(f"{analysis_path}analysis_metadata.json", metadata)

        # Create Cloud Run Job execution
        client = run_v2.JobsClient()
        job_path = (
            f"projects/{self.config.project_id}/locations/{self.config.region}/jobs/{self.job_name}"
        )
        task_count = len(predictions)

        # Execute the job
        try:
            logger.info(f"Executing Cloud Run Job: {job_path}")
            logger.info(f"Task count: {task_count}")
            logger.info(f"Task config URI: {analysis_path}task_config.json")

            # The task_config.json contains all necessary information:
            # - job_id
            # - analysis_path
            # - predictions with URIs
            # The Cloud Run job will read this file and determine its parameters from it.
            # We only need to pass ANALYSIS_PATH as an override to tell it where to find task_config.json

            # Execute the job with task count and environment variable overrides
            request = run_v2.RunJobRequest(
                name=job_path,
                overrides=run_v2.RunJobRequest.Overrides(
                    task_count=task_count,
                    timeout={"seconds": 600},  # 10 minute timeout per task
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            env=[
                                run_v2.EnvVar(name="ANALYSIS_PATH", value=analysis_path),
                                run_v2.EnvVar(
                                    name="GEMINI_MODEL",
                                    value=os.getenv(
                                        "GEMINI_ANALYSIS_MODEL", "gemini-3-pro-preview"
                                    ),
                                ),
                            ]
                        )
                    ],
                ),
            )

            logger.info(f"Executing with ANALYSIS_PATH={analysis_path}")

            # Run job returns a long-running operation
            operation = client.run_job(request=request)

            # The operation represents the execution
            # We can get the execution from the operation metadata
            logger.info("Cloud Run Job execution started")
            logger.info(
                f"Operation: {operation.operation.name if hasattr(operation, 'operation') else 'N/A'}"
            )

            # Extract execution name from operation metadata
            # The execution name is in the metadata field of the operation
            execution_name = None
            execution_uid = None
            if hasattr(operation, "metadata") and operation.metadata:
                # The metadata contains the execution resource name
                # Format: projects/{project}/locations/{location}/jobs/{job}/executions/{execution}
                if hasattr(operation.metadata, "name"):
                    execution_name = operation.metadata.name
                    execution_uid = execution_name.split("/")[-1] if execution_name else None
                    logger.info(f"Execution name: {execution_name}")
                    logger.info(f"Execution UID: {execution_uid}")

            # Update metadata with Cloud Run execution details
            metadata["execution_name"] = execution_name
            metadata["execution_uid"] = execution_uid  # Short form for display
            metadata["cloud_run_job"] = job_path
            self._write_to_gcs(f"{analysis_path}analysis_metadata.json", metadata)

            return {
                "status": "started",
                "job_id": job_id,
                "analysis_path": analysis_path,
                "gcs_console_url": self.gcs_console_url(analysis_path),
                "total_predictions": len(predictions),
                "execution_name": execution_name,
                "execution_uid": execution_uid,
                "cloud_run_job": job_path,
                "message": f"Analysis started with {task_count} parallel tasks. Use get_analysis_results to check status.",
            }

        except Exception as e:
            logger.error(f"Failed to execute Cloud Run Job: {e}", exc_info=True)
            return {
                "status": "failed",
                "job_id": job_id,
                "error": str(e),
                "message": "Failed to start Cloud Run Job execution",
            }
