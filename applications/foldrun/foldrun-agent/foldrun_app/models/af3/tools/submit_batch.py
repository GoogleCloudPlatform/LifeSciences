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

"""Tool for submitting multiple AlphaFold 3 predictions in batch via KFP PipelineJobs."""

import logging
from typing import Any

from ..base import AF3Tool
from .submit_prediction import AF3SubmitPredictionTool

logger = logging.getLogger(__name__)


class AF3BatchSubmitTool(AF3Tool):
    """Tool for submitting multiple AlphaFold 3 predictions as asynchronous KFP PipelineJobs."""

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Submit a batch of AlphaFold 3 prediction jobs.

        Each item in `batch_config` is submitted as its own KFP `PipelineJob` (`sync=False`),
        which coordinates replica slot access against the active H100 endpoint so jobs
        queue cleanly in Vertex AI Pipelines without blocking the agent turn.

        Args:
            arguments: {
                'batch_config': List of job configuration dicts, each containing:
                    {
                        'input': FASTA string, AF3 JSON string, or file/GCS path (required),
                        'job_name': Optional human-readable job name,
                        'msa_free': Whether to run in zero-MSA mode (default: False),
                        'model_seeds': Optional list of random seeds (default: [1]),
                        'num_diffusion_samples': Diffusion samples per seed (default: 5),
                        'endpoint_id': Optional endpoint override,
                    }
            }

        Returns:
            Dictionary summarizing submitted and failed KFP pipeline jobs.
        """
        batch_config = arguments.get("batch_config", [])
        if not batch_config or not isinstance(batch_config, list):
            return {
                "status": "error",
                "message": "batch_config must be a non-empty list of AF3 job configuration dicts.",
            }

        submit_tool = AF3SubmitPredictionTool(
            tool_config={
                "name": "af3_submit_prediction_batch_item",
                "description": "Single AF3 KFP submission within batch",
            },
            config=self.config,
        )
        submit_tool.storage_client = self.storage_client

        default_idle_shutdown = int(arguments.get("idle_shutdown_minutes", 20))
        submitted_jobs = []
        failed_jobs = []

        for idx, raw_item in enumerate(batch_config):
            item = dict(raw_item or {})
            # Allow 'sequence' as an alias for 'input' for consistency with AF2 batch_config
            if "input" not in item and "sequence" in item:
                item["input"] = item.pop("sequence")
            # Always submit batch items asynchronously as KFP PipelineJobs
            item["sync"] = False
            item.setdefault("idle_shutdown_minutes", default_idle_shutdown)

            job_label = item.get("job_name") or f"af3_batch_{idx + 1}"
            try:
                res = submit_tool.run(item)
                if res.get("status") in ("submitted", "succeeded"):
                    submitted_jobs.append(
                        {
                            "index": idx,
                            "job_id": res.get("job_id"),
                            "job_name": res.get("job_name", job_label),
                            "status": res.get("status", "submitted"),
                            "mode": res.get("mode"),
                            "total_tokens": res.get("total_tokens"),
                            "console_url": res.get("console_url"),
                            "viewer_url": res.get("viewer_url"),
                            "gcs_output_dir": res.get("gcs_output_dir"),
                        }
                    )
                else:
                    failed_jobs.append(
                        {
                            "index": idx,
                            "job_name": job_label,
                            "status": "failed",
                            "error": res.get("message", "Unknown submission error"),
                        }
                    )
            except Exception as exc:
                logger.error(f"Failed to submit AF3 batch item {idx} ({job_label}): {exc}")
                failed_jobs.append(
                    {
                        "index": idx,
                        "job_name": job_label,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        return {
            "status": "submitted" if submitted_jobs else "error",
            "total": len(batch_config),
            "succeeded": len(submitted_jobs),
            "failed": len(failed_jobs),
            "idle_shutdown_minutes": default_idle_shutdown,
            "submitted_jobs": submitted_jobs,
            "failed_jobs": failed_jobs if failed_jobs else None,
            "message": (
                f"Submitted {len(submitted_jobs)}/{len(batch_config)} AlphaFold 3 KFP pipeline job(s). "
                f"Each job is tracked in Vertex AI Pipelines, coordinates H100 replica slots automatically, "
                f"and auto-undeploys the H100 endpoint after {default_idle_shutdown} minutes of inactivity once the batch drains."
            ),
        }
