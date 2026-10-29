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

"""Tool for triggering parallel analysis of BOLTZGEN predictions via Cloud Run Jobs."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from google.cloud import run_v2, storage

from ..base import BOLTZGENTool

logger = logging.getLogger(__name__)


class BOLTZGENJobAnalysisTool(BOLTZGENTool):
    """Triggers parallel analysis of BoltzGen designs via Cloud Run Jobs."""

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

    def _discover_boltzgen_samples(self, job: Any) -> list[dict[str, Any]]:
        """Discover BOLTZGEN design output samples from GCS.

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

        logger.info(f"Discovered {len(samples)} BOLTZGEN design samples")
        return samples

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Trigger parallel analysis via Cloud Run Job.

        Args:
            arguments: {
                'job_id': Job ID (required),
                'overwrite': Overwrite existing analysis (default: False)
            }
        """
        job_id = arguments.get("job_id") or arguments.get("job_name")
        overwrite = arguments.get("overwrite") or arguments.get("force_reanalysis", False)

        if not job_id:
            raise ValueError("job_id is required")

        logger.info(f"Starting BOLTZGEN parallel analysis for job {job_id}")

        from foldrun_app.core.vertex_utils import get_pipeline_job

        try:
            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {e!s}"}

        analysis_path = self._get_analysis_path(job)

        if not overwrite:
            exists, existing_summary = self._check_existing_analysis(analysis_path)
            if exists:
                logger.info(f"Analysis already exists for job {job_id}")
                return {
                    "status": "already_exists",
                    "job_id": job_id,
                    "analysis_path": analysis_path,
                    "message": "Analysis already exists. Use overwrite=True to re-run.",
                    "summary": existing_summary,
                }

        samples = self._discover_boltzgen_samples(job)
        if not samples:
            return {
                "status": "error",
                "message": "No BOLTZGEN design output files found. Pipeline may still be running or failed.",
            }

        # Query YAML URI from labels or parameters
        query_yaml_uri = None
        if hasattr(job, "runtime_config") and hasattr(job.runtime_config, "parameter_values"):
            query_yaml_uri = job.runtime_config.parameter_values.get("query_yaml_path")

        predictions = [
            {
                "index": i,
                "sample_name": s["sample_name"],
                "cif_uri": s["cif_uri"],
                "aggregated_uri": s["aggregated_uri"],
                "output_uri": f"{analysis_path}{s['sample_name']}_analysis.json",
            }
            for i, s in enumerate(samples)
        ]

        short_id = (
            job.name.split("/")[-1]
            if hasattr(job, "name") and job.name
            else str(job_id).split("/")[-1]
        )
        task_config = {
            "model_type": "boltzgen",
            "job_id": short_id,
            "predictions": predictions,
            "analysis_path": analysis_path,
            "query_yaml_uri": query_yaml_uri,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        task_config_uri = f"{analysis_path}task_config.json"
        try:
            self._write_to_gcs(task_config_uri, task_config)
            logger.info(f"Wrote task config to {task_config_uri}")
        except Exception as e:
            return {"status": "error", "message": f"Failed to write task config: {e!s}"}

        # Trigger Cloud Run Job
        try:
            run_client = run_v2.JobsClient()
            analysis_region = os.getenv("PIPELINE_JOB_LOCATION", self.config.region)
            job_name_full = f"projects/{self.config.project_id}/locations/{analysis_region}/jobs/{self.job_name}"
            bucket_name = analysis_path[5:].split("/", 1)[0]
            analysis_tasks = int(arguments.get("analysis_tasks", 10))
            task_count = max(1, min(len(samples), analysis_tasks, 25))

            env_vars = [
                run_v2.EnvVar(name="JOB_ID", value=short_id),
                run_v2.EnvVar(name="GCS_BUCKET", value=bucket_name),
                run_v2.EnvVar(name="ANALYSIS_PATH", value=analysis_path),
                run_v2.EnvVar(name="MODEL_TYPE", value="boltzgen"),
                run_v2.EnvVar(name="TASK_CONFIG_URI", value=task_config_uri),
                run_v2.EnvVar(
                    name="GEMINI_MODEL",
                    value=os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-3.1-pro-preview"),
                ),
            ]

            request = run_v2.RunJobRequest(
                name=job_name_full,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(env=env_vars)
                    ],
                    task_count=task_count,
                    timeout={"seconds": 600},
                ),
            )

            execution = run_client.run_job(request=request)
            execution_name = (
                execution.operation.name
                if hasattr(execution, "operation")
                else getattr(getattr(execution, "metadata", None), "name", "")
            )
            logger.info(f"Triggered Cloud Run Job execution: {execution_name}")
            self._write_to_gcs(
                f"{analysis_path}analysis_metadata.json",
                {
                    "job_id": short_id,
                    "total_predictions": len(samples),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                    "execution_method": "cloud_run_job",
                    "model_type": "boltzgen",
                    "cloud_run_execution": execution_name,
                },
            )

            return {
                "status": "submitted",
                "model_type": "boltzgen",
                "job_id": short_id,
                "execution_name": execution_name,
                "analysis_path": analysis_path,
                "samples_count": len(samples),
                "task_count": task_count,
                "message": f"Parallel analysis triggered with {len(samples)} design samples.",
            }

        except Exception as e:
            logger.error(f"Failed to trigger Cloud Run Job: {e}")
            return {"status": "error", "message": f"Failed to trigger Cloud Run Job: {e!s}"}
