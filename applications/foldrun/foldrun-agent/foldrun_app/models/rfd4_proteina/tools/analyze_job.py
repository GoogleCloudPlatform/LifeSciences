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

"""Tool for triggering parallel analysis of RFD4-Proteina predictions via Cloud Run Jobs."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from google.cloud import run_v2, storage

from ..base import RFD4ProteinaTool

logger = logging.getLogger(__name__)


class RFD4ProteinaJobAnalysisTool(RFD4ProteinaTool):
    """Triggers parallel analysis of RFD4-Proteina designs via Cloud Run Jobs."""

    def __init__(self, tool_config: dict[str, Any], config: Any):
        super().__init__(tool_config, config)
        self.job_name = os.getenv("ANALYSIS_JOB_NAME", "foldrun-analysis-job")

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

    def _write_to_gcs(self, gcs_uri: str, data: dict[str, Any]) -> None:
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

    def _discover_rfd4_proteina_samples(self, job: Any) -> list[dict[str, Any]]:
        """Discover RFD4-Proteina design output samples from GCS.

        Returns:
            samples: list of dicts with cif_uri, aggregated_uri, sample_name.
        """
        pipeline_root = job.runtime_config.gcs_output_directory
        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        parts = pipeline_root[5:].split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)

        all_blobs = list(bucket.list_blobs(prefix=prefix))

        samples = []
        seen = set()

        for blob in all_blobs:
            name = blob.name
            if name.endswith(".json") and ("/predictions/" in name or "confidence_design" in name):
                parts_list = name.split("/")
                filename = parts_list[-1]

                if "confidence_design_" in filename:
                    cif_filename = filename.replace("confidence_", "", 1).replace(".json", ".cif")
                    cif_name = "/".join([*parts_list[:-1], cif_filename])

                    if name in seen:
                        continue
                    seen.add(name)

                    sample_name = filename.replace("confidence_", "", 1).replace(".json", "")
                    samples.append(
                        {
                            "sample_name": sample_name,
                            "cif_uri": f"gs://{bucket_name}/{cif_name}",
                            "aggregated_uri": f"gs://{bucket_name}/{name}",
                        }
                    )

        logger.info(f"Discovered {len(samples)} RFD4-Proteina design samples")
        return samples

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Trigger parallel analysis via Cloud Run Job.

        Args:
            arguments: {
                'job_name': Pipeline job name or ID,
                'analysis_tasks': Number of Cloud Run tasks (default: 10),
                'force_reanalysis': Force re-running analysis (default: False),
            }
        """
        job_name = arguments.get("job_id") or arguments.get("job_name")
        if not job_name:
            raise ValueError("job_name is required")

        analysis_tasks = arguments.get("analysis_tasks", 10)
        force_reanalysis = arguments.get("force_reanalysis") or arguments.get("overwrite", False)

        from foldrun_app.core.vertex_utils import get_pipeline_job

        try:
            job = get_pipeline_job(job_name, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_name}: {e!s}"}
        analysis_path = self._get_analysis_path(job)

        if not force_reanalysis:
            exists, existing_summary = self._check_existing_analysis(analysis_path)
            if exists and existing_summary:
                return {
                    "status": "CACHED",
                    "message": "Analysis already completed for this job. Returning cached results.",
                    "analysis_path": analysis_path,
                    "summary": existing_summary,
                }

        samples = self._discover_rfd4_proteina_samples(job)
        if not samples:
            return {
                "status": "error",
                "message": f"No RFD4-Proteina design output samples found for job: {job_name}. Pipeline may still be running or failed.",
            }

        predictions = []
        for i, s in enumerate(samples):
            predictions.append(
                {
                    "index": i,
                    "sample_name": s["sample_name"],
                    "cif_uri": s["cif_uri"],
                    "aggregated_uri": s["aggregated_uri"],
                    "output_uri": f"{analysis_path}{s['sample_name']}_analysis.json",
                }
            )

        short_id = job.name.split("/")[-1]
        task_config = {
            "model_type": "rfd4_proteina",
            "job_id": short_id,
            "predictions": predictions,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        task_config_uri = f"{analysis_path}task_config.json"
        self._write_to_gcs(task_config_uri, task_config)

        task_count = max(1, min(len(samples), int(analysis_tasks), 25))

        run_client = run_v2.JobsClient()
        analysis_region = os.getenv("PIPELINE_JOB_LOCATION", self.config.region)
        job_path = (
            f"projects/{self.config.project_id}/locations/{analysis_region}/jobs/{self.job_name}"
        )

        parts = analysis_path[5:].split("/", 1)
        bucket_name = parts[0]

        env_overrides = [
            run_v2.EnvVar(name="JOB_ID", value=short_id),
            run_v2.EnvVar(name="GCS_BUCKET", value=bucket_name),
            run_v2.EnvVar(name="ANALYSIS_PATH", value=analysis_path),
            run_v2.EnvVar(name="MODEL_TYPE", value="rfd4_proteina"),
            run_v2.EnvVar(name="TASK_CONFIG_URI", value=task_config_uri),
            run_v2.EnvVar(
                name="GEMINI_MODEL",
                value=os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-3.1-pro-preview"),
            ),
        ]

        request = run_v2.RunJobRequest(
            name=job_path,
            overrides=run_v2.RunJobRequest.Overrides(
                task_count=task_count,
                timeout={"seconds": 600},
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(env=env_overrides)
                ],
            ),
        )

        operation = run_client.run_job(request=request)
        execution_name = (
            operation.operation.name
            if hasattr(operation, "operation")
            else (operation.metadata.name if hasattr(operation, "metadata") else "unknown")
        )
        self._write_to_gcs(
            f"{analysis_path}analysis_metadata.json",
            {
                "job_id": short_id,
                "total_predictions": len(samples),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
                "execution_method": "cloud_run_job",
                "model_type": "rfd4_proteina",
                "cloud_run_execution": execution_name,
            },
        )

        return {
            "status": "RUNNING",
            "message": f"Parallel analysis job triggered with {task_count} tasks",
            "execution_name": execution_name,
            "analysis_path": analysis_path,
            "sample_count": len(samples),
            "task_count": task_count,
        }
