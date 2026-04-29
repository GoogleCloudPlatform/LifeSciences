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

"""Tool for triggering parallel analysis of OF3 predictions via Cloud Run Jobs."""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

from google.cloud import run_v2, storage

from ..base import OF3Tool

logger = logging.getLogger(__name__)


class OF3JobAnalysisTool(OF3Tool):
    """Triggers parallel analysis of OpenFold3 predictions via Cloud Run Jobs."""

    def __init__(self, tool_config: Dict[str, Any], config: Any):
        super().__init__(tool_config, config)
        self.job_name = os.getenv("OF3_ANALYSIS_JOB_NAME", "of3-analysis-job")

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

    def _check_existing_analysis(self, analysis_path: str) -> tuple[bool, dict | None]:
        """Check if analysis already exists."""
        try:
            storage_client = storage.Client(project=self.config.project_id)
            parts = analysis_path[5:].split("/", 1)
            bucket = storage_client.bucket(parts[0])
            prefix = parts[1] if len(parts) > 1 else ""

            summary_blob = bucket.blob(f"{prefix}summary.json")
            if summary_blob.exists():
                content = summary_blob.download_as_string()
                return True, json.loads(content)

            return False, None
        except Exception as e:
            logger.warning(f"Could not check for existing analysis: {e}")
            return False, None

    def _discover_of3_samples(self, job: Any) -> list[Dict[str, Any]]:
        """Discover OF3 output samples by walking seed_*/sample directories in GCS.

        Returns:
            List of sample dicts with cif_uri, confidences_uri, aggregated_uri, sample_name
        """
        pipeline_root = job.runtime_config.gcs_output_directory
        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        parts = pipeline_root[5:].split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)

        # List all blobs under pipeline root to find OF3 output files
        # Pattern: predict-of3_*/query_name/seed_N/*_confidences_aggregated.json
        all_blobs = list(bucket.list_blobs(prefix=prefix))

        samples = []
        seen = set()

        for blob in all_blobs:
            name = blob.name
            if name.endswith("_confidences_aggregated.json"):
                base = name.replace("_confidences_aggregated.json", "")

                cif_name = f"{base}_model.cif"
                confidences_name = f"{base}_confidences.json"

                # Use full blob path as unique key (different predict-of3_*
                # task directories may have same-named files)
                if name in seen:
                    continue
                seen.add(name)

                # Sample name from filename (with patched seed values,
                # each seed produces unique filenames like
                # query_seed_1181241943_sample_1)
                parts_list = name.split("/")
                filename = parts_list[-1]
                sample_name = filename.replace("_confidences_aggregated.json", "")

                samples.append(
                    {
                        "sample_name": sample_name,
                        "cif_uri": f"gs://{bucket_name}/{cif_name}",
                        "confidences_uri": f"gs://{bucket_name}/{confidences_name}",
                        "aggregated_uri": f"gs://{bucket_name}/{name}",
                    }
                )

        logger.info(f"Discovered {len(samples)} OF3 samples")
        return samples

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Trigger parallel analysis via Cloud Run Job.

        Args:
            arguments: {
                'job_id': Job ID (required),
                'overwrite': Overwrite existing analysis (default: False)
            }
        """
        job_id = arguments.get("job_id")
        overwrite = arguments.get("overwrite", False)

        if not job_id:
            raise ValueError("job_id is required")

        logger.info(f"Starting OF3 parallel analysis for job {job_id}")

        # Get job from Agent Platform
        from foldrun_app.core.vertex_utils import get_pipeline_job

        try:
            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {str(e)}"}

        # Determine analysis path
        analysis_path = self._get_analysis_path(job)

        # Check if analysis already exists
        exists, existing_summary = self._check_existing_analysis(analysis_path)
        if not overwrite and exists:
            return {
                "status": "already_exists",
                "message": "Analysis already exists. Use overwrite=true to re-analyze, or use of3_get_analysis_results to retrieve.",
                "analysis_path": analysis_path,
                "summary_uri": f"{analysis_path}summary.json",
                **existing_summary,
            }

        # Discover OF3 samples from GCS
        samples = self._discover_of3_samples(job)

        if not samples:
            return {"status": "no_predictions", "message": "No OF3 samples found for this job"}

        # Build task configuration
        task_config = {
            "job_id": job_id,
            "analysis_path": analysis_path,
            "predictions": [
                {
                    "index": i,
                    "sample_name": s["sample_name"],
                    "cif_uri": s["cif_uri"],
                    "confidences_uri": s["confidences_uri"],
                    "aggregated_uri": s["aggregated_uri"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, s in enumerate(samples)
            ],
        }

        task_config_path = f"{analysis_path}task_config.json"
        self._write_to_gcs(task_config_path, task_config)

        # Write metadata
        metadata = {
            "job_id": job_id,
            "total_predictions": len(samples),
            "started_at": datetime.utcnow().isoformat() + "Z",
            "status": "running",
            "execution_method": "cloud_run_job",
            "model_type": "openfold3",
        }
        self._write_to_gcs(f"{analysis_path}analysis_metadata.json", metadata)

        # Execute Cloud Run Job
        client = run_v2.JobsClient()
        job_path = (
            f"projects/{self.config.project_id}/locations/{self.config.region}/jobs/{self.job_name}"
        )
        task_count = len(samples)

        try:
            request = run_v2.RunJobRequest(
                name=job_path,
                overrides=run_v2.RunJobRequest.Overrides(
                    task_count=task_count,
                    timeout={"seconds": 600},
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            env=[
                                run_v2.EnvVar(name="ANALYSIS_PATH", value=analysis_path),
                                run_v2.EnvVar(
                                    name="GEMINI_MODEL",
                                    value=os.getenv(
                                        "GEMINI_ANALYSIS_MODEL", "gemini-3.1-pro-preview"
                                    ),
                                ),
                            ]
                        )
                    ],
                ),
            )

            operation = client.run_job(request=request)

            execution_name = None
            execution_uid = None
            if hasattr(operation, "metadata") and operation.metadata:
                if hasattr(operation.metadata, "name"):
                    execution_name = operation.metadata.name
                    execution_uid = execution_name.split("/")[-1] if execution_name else None

            metadata["execution_name"] = execution_name
            metadata["execution_uid"] = execution_uid
            self._write_to_gcs(f"{analysis_path}analysis_metadata.json", metadata)

            return {
                "status": "started",
                "job_id": job_id,
                "analysis_path": analysis_path,
                "gcs_console_url": self.gcs_console_url(analysis_path),
                "total_predictions": len(samples),
                "execution_name": execution_name,
                "execution_uid": execution_uid,
                "message": f"OF3 analysis started with {task_count} parallel tasks. Use of3_get_analysis_results to check status.",
            }

        except Exception as e:
            logger.error(f"Failed to execute Cloud Run Job: {e}", exc_info=True)
            return {
                "status": "failed",
                "job_id": job_id,
                "error": str(e),
                "message": "Failed to start OF3 analysis Cloud Run Job execution",
            }
