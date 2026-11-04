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

"""Tool for aggregating async analysis results from GCS."""

import json
import logging
import time
from typing import Any
from urllib.parse import quote_plus

from google.cloud import run_v2, storage

from foldrun_app.core.download_utils import build_agent_downloads_dict

from ..base import AF2Tool

logger = logging.getLogger(__name__)


class AF2GetAnalysisResultsTool(AF2Tool):
    """Tool for retrieving and aggregating Cloud Run analysis results."""

    def _get_analysis_path(self, job: Any) -> str:
        """
        Get GCS analysis path for a job.

        Stores analysis results alongside AlphaFold results:
        gs://bucket/pipeline_runs/YYYYMMDD_HHMMSS/analysis/

        Args:
            job: Agent Platform PipelineJob object (not just job_id)

        Returns:
            GCS path for analysis files (e.g., gs://bucket/pipeline_runs/.../analysis/)
        """
        # Get the pipeline root from the job's runtime_config
        pipeline_root = None
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        # Fallback if no runtime_config found
        if not pipeline_root:
            raise ValueError(
                "Job does not have gcs_output_directory in runtime_config. "
                "Cannot determine analysis path."
            )

        self._validate_gcs_uri(pipeline_root)

        # Ensure trailing slash
        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        analysis_path = f"{pipeline_root}analysis/"
        self._validate_gcs_uri(analysis_path)
        return analysis_path

    def _check_cloudrun_execution_status(self, execution_name: str) -> dict[str, Any]:
        """Check the status of a Cloud Run job execution.

        Args:
            execution_name: Full execution name from Cloud Run

        Returns:
            Dict with status info including: state, failed_count, succeeded_count, running_count
        """
        try:
            client = run_v2.ExecutionsClient()
            execution = client.get_execution(
                name=execution_name,
                timeout=10,  # 10s timeout to avoid stalling
            )

            # Get task counts
            failed_count = execution.failed_count if hasattr(execution, "failed_count") else 0
            succeeded_count = (
                execution.succeeded_count if hasattr(execution, "succeeded_count") else 0
            )
            running_count = execution.running_count if hasattr(execution, "running_count") else 0
            task_count = execution.task_count if hasattr(execution, "task_count") else 0

            # Get completion status
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

    def _read_from_gcs(self, gcs_uri: str) -> dict[str, Any]:
        """Read JSON data from GCS."""
        bucket_name, blob_name = self._validate_gcs_uri(gcs_uri)

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        content = blob.download_as_text()
        return json.loads(content)

    def _list_completed_analyses(self, analysis_path: str) -> list[str]:
        """List all completed analysis files in GCS."""
        bucket_name, prefix = self._validate_gcs_uri(analysis_path)

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)

        # List all prediction analysis files
        blobs = bucket.list_blobs(prefix=prefix)
        completed = []

        for blob in blobs:
            if "prediction_" in blob.name and blob.name.endswith("_analysis.json"):
                completed.append(f"gs://{bucket_name}/{blob.name}")

        return completed

    def _build_viewer_url(
        self, job_id: str, summary_uri: str, summary: dict[str, Any]
    ) -> str | None:
        """Build the viewer URL from analysis results.

        Extracts the PDB URI from the best prediction and constructs
        the full viewer URL so the agent can present it directly.
        """
        try:
            viewer_base = self.config.viewer_url
            if not viewer_base:
                return None

            # Get PDB/CIF URI from best prediction
            best = summary.get("best_prediction", {})
            pkl_uri = best.get("uri")
            if not pkl_uri:
                return None

            if str(pkl_uri).endswith(".cif") or summary.get("job_type") in (
                "alphafold3",
                "openfold3",
                "boltz2",
            ):
                return f"{viewer_base.rstrip('/')}/job/{quote_plus(job_id)}"

            # Convert pickle URI to PDB: .../raw_prediction.pkl -> .../unrelaxed_protein.pdb
            pdb_uri = pkl_uri.rsplit("/", 1)[0] + "/unrelaxed_protein.pdb"
            model_name = best.get("model_name", "Best Model")

            params = [
                f"pdb_uri={quote_plus(pdb_uri)}",
                f"job_id={quote_plus(job_id)}",
                f"model={quote_plus(model_name)}",
            ]
            if summary_uri:
                params.append(f"summary_uri={quote_plus(summary_uri)}")

            return f"{viewer_base}/combined?{'&'.join(params)}"
        except Exception as e:
            logger.warning(f"Could not build viewer URL: {e}")
            return None

    def _trim_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Remove bulky per-residue score arrays from top_predictions to reduce response size.

        Keeps plddt_scores only in best_prediction (for confidence breakdown).
        """
        if "top_predictions" in summary:
            for pred in summary["top_predictions"]:
                pred.pop("plddt_scores", None)
        if "all_predictions_summary" in summary:
            for pred in summary["all_predictions_summary"]:
                pred.pop("plddt_scores", None)
        return summary

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Get analysis results from Cloud Run.

        Args:
            arguments: {
                'job_id': Job ID (required),
                'wait': Poll until complete (default: False - NEVER blocks by default),
                'timeout': Max wait time in seconds (default: 10 - very short to avoid blocking),
                'poll_interval': Seconds between polls (default: 2)
            }

        Returns:
            Aggregated analysis results
        """
        job_id = arguments.get("job_id")
        # NEVER wait by default - return immediately with current status
        wait = arguments.get("wait", False)
        # Very short timeout to prevent blocking the agent (max 10s)
        timeout = arguments.get("timeout", 10)
        poll_interval = arguments.get("poll_interval", 2)

        if not job_id:
            raise ValueError("job_id is required")

        logger.info(f"Getting analysis results for job {job_id}")

        # Get the job object to extract pipeline root
        from ..utils.vertex_utils import get_pipeline_job

        try:
            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {e!s}"}

        job_res_name = getattr(job, "name", None)
        job_id = (
            job_res_name.rsplit("/", 1)[-1]
            if isinstance(job_res_name, str) and job_res_name
            else str(job_id).rsplit("/", 1)[-1]
        )

        # Get analysis path
        analysis_path = self._get_analysis_path(job)

        # Fast path: check for summary.json first — if it exists, analysis is done
        # and we can skip all intermediate checks (Cloud Run status, error.json, etc.)
        summary_uri = f"{analysis_path}summary.json"
        try:
            cloud_run_summary = self._read_from_gcs(summary_uri)
            logger.info("Found summary.json — returning completed analysis")

            # Build viewer URL so the agent can present it directly
            viewer_url = self._build_viewer_url(job_id, summary_uri, cloud_run_summary)
            downloads = build_agent_downloads_dict(
                job_id=job_id,
                analysis_path=analysis_path,
                summary_data=cloud_run_summary,
                project_id=self.config.project_id,
                ensure_bundle=False,
            )

            result = {
                "status": "complete",
                "job_id": job_id,
                "analysis_path": analysis_path,
                "gcs_console_url": self.gcs_console_url(analysis_path),
                "downloads": downloads,
                **cloud_run_summary,
            }
            if viewer_url:
                result["viewer_url"] = viewer_url
            return self._trim_summary(result)
        except Exception:
            # No summary yet — fall through to detailed status checks
            logger.info("No summary.json found, checking analysis status...")

        # Read metadata
        try:
            metadata = self._read_from_gcs(f"{analysis_path}analysis_metadata.json")
        except Exception as e:
            return {
                "status": "not_found",
                "message": f"No analysis found for job {job_id}. Run analyze_job_parallel first.",
                "error": str(e),
            }

        total_predictions = metadata["total_predictions"]

        # Check Cloud Run execution status if available
        execution_name = metadata.get("execution_name")
        cloudrun_status = None
        if execution_name:
            cloudrun_status = self._check_cloudrun_execution_status(execution_name)
            if cloudrun_status:
                logger.info(f"Cloud Run execution status: {cloudrun_status}")

                # If Cloud Run job has failures, report them immediately
                if cloudrun_status["has_failures"]:
                    failed = cloudrun_status["failed_count"]
                    total = cloudrun_status["task_count"]
                    return {
                        "status": "failed",
                        "message": f"Cloud Run analysis job failed. {failed}/{total} tasks failed.",
                        "failed_tasks": failed,
                        "succeeded_tasks": cloudrun_status["succeeded_count"],
                        "total_tasks": total,
                        "analysis_path": analysis_path,
                        "error_hint": "Cloud Run tasks encountered errors. Common causes: prediction files not found (404), permission issues, or invalid file formats.",
                        "suggestion": "Check Cloud Run logs for detailed error messages. The prediction files may not exist at the expected GCS paths.",
                    }

        # Check for errors (Cloud Run writes error.json if analysis fails)
        try:
            error_data = self._read_from_gcs(f"{analysis_path}error.json")
            logger.error(f"Analysis failed with error: {error_data}")
            return {
                "status": "failed",
                "message": "Analysis failed during execution.",
                "error": error_data.get("error", "Unknown error"),
                "error_details": error_data.get("details", "No additional details available"),
                "analysis_path": analysis_path,
                "suggestion": "Check the error details above. The Cloud Run job may have encountered issues accessing prediction files or processing them.",
            }
        except Exception:
            pass

        # Count completed analyses
        completed_files = self._list_completed_analyses(analysis_path)
        completed_count = len(completed_files)

        logger.info(f"Found {completed_count}/{total_predictions} completed analyses")

        # Wait if requested
        if wait and completed_count < total_predictions:
            start_time = time.time()
            logger.info(f"Waiting for analysis to complete (timeout: {timeout}s)...")

            while completed_count < total_predictions and (time.time() - start_time) < timeout:
                time.sleep(poll_interval)
                completed_files = self._list_completed_analyses(analysis_path)
                new_count = len(completed_files)

                if new_count > completed_count:
                    logger.info(f"Progress: {new_count}/{total_predictions} completed")
                    completed_count = new_count

            if completed_count < total_predictions:
                logger.warning(
                    f"Timeout reached. Only {completed_count}/{total_predictions} completed."
                )

            # Re-check for summary after waiting
            try:
                cloud_run_summary = self._read_from_gcs(summary_uri)
                viewer_url = self._build_viewer_url(job_id, summary_uri, cloud_run_summary)
                downloads = build_agent_downloads_dict(
                    job_id=job_id,
                    analysis_path=analysis_path,
                    summary_data=cloud_run_summary,
                    project_id=self.config.project_id,
                    ensure_bundle=False,
                )
                result = {
                    "status": "complete",
                    "job_id": job_id,
                    "analysis_path": analysis_path,
                    "downloads": downloads,
                    **cloud_run_summary,
                }
                if viewer_url:
                    result["viewer_url"] = viewer_url
                return self._trim_summary(result)
            except Exception:
                pass

        # Summary not ready
        if completed_count < total_predictions:
            if completed_count == 0:
                created_at = metadata.get("started_at")
                if created_at:
                    try:
                        from datetime import datetime

                        created_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        elapsed = (datetime.now(created_time.tzinfo) - created_time).total_seconds()
                        if elapsed > 300:  # 5 minutes
                            return {
                                "status": "likely_failed",
                                "message": f"Analysis appears to have failed. 0/{total_predictions} predictions analyzed after {elapsed / 60:.1f} minutes.",
                                "completed": 0,
                                "total": total_predictions,
                                "analysis_path": analysis_path,
                                "error_hint": "The Cloud Run job may have failed to find prediction files.",
                                "suggestion": "Check Cloud Run logs for detailed errors, or try running analyze_job_parallel again.",
                            }
                    except Exception:
                        pass

            return {
                "status": "running",
                "message": f"Analysis still running. {completed_count}/{total_predictions} predictions analyzed.",
                "completed": completed_count,
                "total": total_predictions,
                "analysis_path": analysis_path,
                "suggestion": "Wait for analysis to complete, or use get_analysis_results with wait=true to poll until done.",
            }
        else:
            return {
                "status": "incomplete",
                "message": "Analysis jobs completed but summary.json not found. The Cloud Run consolidation step may have failed.",
                "completed": completed_count,
                "total": total_predictions,
                "analysis_path": analysis_path,
                "suggestion": "Run analyze_job_parallel again with overwrite=true to regenerate the summary with expert analysis.",
            }
