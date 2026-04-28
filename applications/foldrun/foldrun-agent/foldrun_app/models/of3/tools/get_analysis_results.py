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

"""Tool for retrieving OF3 analysis results from GCS."""

import json
import logging
import time
from typing import Any, Dict, List
from urllib.parse import quote_plus

from google.cloud import run_v2, storage

from ..base import OF3Tool

logger = logging.getLogger(__name__)


class OF3GetAnalysisResultsTool(OF3Tool):
    """Retrieves OF3 analysis results from Cloud Run Jobs."""

    def _get_analysis_path(self, job: Any) -> str:
        """Get GCS analysis path for a job."""
        pipeline_root = None
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        if not pipeline_root:
            raise ValueError("Job does not have gcs_output_directory in runtime_config")

        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        return f"{pipeline_root}analysis/"

    def _read_from_gcs(self, gcs_uri: str) -> Dict[str, Any]:
        """Read JSON data from GCS."""
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        parts = gcs_uri[5:].split("/", 1)
        bucket_name = parts[0]
        blob_name = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        content = blob.download_as_text()
        return json.loads(content)

    def _check_cloudrun_execution_status(self, execution_name: str) -> Dict[str, Any] | None:
        """Check Cloud Run job execution status."""
        try:
            client = run_v2.ExecutionsClient()
            execution = client.get_execution(name=execution_name, timeout=10)

            failed_count = execution.failed_count if hasattr(execution, "failed_count") else 0
            succeeded_count = (
                execution.succeeded_count if hasattr(execution, "succeeded_count") else 0
            )
            running_count = execution.running_count if hasattr(execution, "running_count") else 0
            task_count = execution.task_count if hasattr(execution, "task_count") else 0
            completed = (
                execution.completion_time is not None
                if hasattr(execution, "completion_time")
                else False
            )

            return {
                "state": "completed" if completed else "running",
                "failed_count": failed_count,
                "succeeded_count": succeeded_count,
                "running_count": running_count,
                "task_count": task_count,
                "has_failures": failed_count > 0,
            }
        except Exception as e:
            logger.warning(f"Could not check Cloud Run execution status: {e}")
            return None

    def _list_completed_analyses(self, analysis_path: str) -> List[str]:
        """List all completed analysis files in GCS."""
        if not analysis_path.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {analysis_path}")

        parts = analysis_path[5:].split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)

        blobs = bucket.list_blobs(prefix=prefix)
        return [
            f"gs://{bucket_name}/{blob.name}"
            for blob in blobs
            if "prediction_" in blob.name and blob.name.endswith("_analysis.json")
        ]

    def _build_viewer_url(self, job_id: str, summary_uri: str) -> str | None:
        """Build the viewer URL."""
        try:
            viewer_base = self.config.viewer_url
            if not viewer_base:
                return None

            return f"{viewer_base}/job/{quote_plus(job_id)}"
        except Exception as e:
            logger.warning(f"Could not build viewer URL: {e}")
            return None

    def _trim_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Remove bulky per-residue score arrays to reduce response size."""
        if "top_predictions" in summary:
            for pred in summary["top_predictions"]:
                pred.pop("plddt_scores", None)
        if "all_predictions_summary" in summary:
            for pred in summary["all_predictions_summary"]:
                pred.pop("plddt_scores", None)
        return summary

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Get OF3 analysis results.

        Args:
            arguments: {
                'job_id': Job ID (required),
                'wait': Poll until complete (default: False),
                'timeout': Max wait time in seconds (default: 10),
                'poll_interval': Seconds between polls (default: 2)
            }
        """
        job_id = arguments.get("job_id")
        wait = arguments.get("wait", False)
        timeout = arguments.get("timeout", 10)
        poll_interval = arguments.get("poll_interval", 2)

        if not job_id:
            raise ValueError("job_id is required")

        # Get the job object
        from foldrun_app.models.af2.utils.vertex_utils import get_pipeline_job

        try:
            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {str(e)}"}

        analysis_path = self._get_analysis_path(job)

        # Fast path: check for summary.json
        summary_uri = f"{analysis_path}summary.json"
        try:
            cloud_run_summary = self._read_from_gcs(summary_uri)
            viewer_url = self._build_viewer_url(job_id, summary_uri)

            result = {
                "status": "complete",
                "job_id": job_id,
                "analysis_path": analysis_path,
                "gcs_console_url": self.gcs_console_url(analysis_path),
                **cloud_run_summary,
            }
            if viewer_url:
                result["viewer_url"] = viewer_url
            return self._trim_summary(result)
        except Exception:
            logger.info("No summary.json found, checking analysis status...")

        # Read metadata
        try:
            metadata = self._read_from_gcs(f"{analysis_path}analysis_metadata.json")
        except Exception as e:
            return {
                "status": "not_found",
                "message": f"No analysis found for job {job_id}. Run of3_analyze_job_parallel first.",
                "error": str(e),
            }

        total_predictions = metadata["total_predictions"]

        # Check Cloud Run execution status
        execution_name = metadata.get("execution_name")
        if execution_name:
            cloudrun_status = self._check_cloudrun_execution_status(execution_name)
            if cloudrun_status and cloudrun_status["has_failures"]:
                failed = cloudrun_status["failed_count"]
                total = cloudrun_status["task_count"]
                return {
                    "status": "failed",
                    "message": f"Cloud Run analysis job failed. {failed}/{total} tasks failed.",
                    "failed_tasks": failed,
                    "succeeded_tasks": cloudrun_status["succeeded_count"],
                    "total_tasks": total,
                    "analysis_path": analysis_path,
                }

        # Count completed analyses
        completed_files = self._list_completed_analyses(analysis_path)
        completed_count = len(completed_files)

        # Wait if requested
        if wait and completed_count < total_predictions:
            start_time = time.time()
            while completed_count < total_predictions and (time.time() - start_time) < timeout:
                time.sleep(poll_interval)
                completed_files = self._list_completed_analyses(analysis_path)
                completed_count = len(completed_files)

            # Re-check for summary after waiting
            try:
                cloud_run_summary = self._read_from_gcs(summary_uri)
                viewer_url = self._build_viewer_url(job_id, summary_uri)
                result = {
                    "status": "complete",
                    "job_id": job_id,
                    "analysis_path": analysis_path,
                    **cloud_run_summary,
                }
                if viewer_url:
                    result["viewer_url"] = viewer_url
                return self._trim_summary(result)
            except Exception:
                pass

        if completed_count < total_predictions:
            return {
                "status": "running",
                "message": f"Analysis still running. {completed_count}/{total_predictions} samples analyzed.",
                "completed": completed_count,
                "total": total_predictions,
                "analysis_path": analysis_path,
            }
        else:
            return {
                "status": "incomplete",
                "message": "Analysis jobs completed but summary.json not found. Consolidation may still be running.",
                "completed": completed_count,
                "total": total_predictions,
                "analysis_path": analysis_path,
            }
