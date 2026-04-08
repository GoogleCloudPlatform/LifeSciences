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

"""Tool for retrieving AlphaFold2 prediction results."""

import logging
import os
from typing import Any, Dict

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job, get_task_details, list_artifacts

logger = logging.getLogger(__name__)


class AF2GetResultsTool(AF2Tool):
    """Tool for retrieving AlphaFold2 prediction results."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get prediction results.

        Args:
            arguments: {
                'job_id': Job ID,
                'output_dir': Local directory to save results (optional),
                'include_raw_predictions': Include raw prediction pickle files (default: False),
                'download_files': Whether to download files (default: True)
            }

        Returns:
            Prediction results
        """
        job_id = arguments.get("job_id")
        output_dir = arguments.get("output_dir")
        include_raw_predictions = arguments.get("include_raw_predictions", False)
        download_files = arguments.get("download_files", True)

        if not job_id:
            raise ValueError("job_id is required")

        # Get pipeline job
        job = get_pipeline_job(job_id, self.config.project_id, self.config.region)

        # Check if job is complete
        if job.state.name not in ["PIPELINE_STATE_SUCCEEDED", "PIPELINE_STATE_FAILED"]:
            return {
                "status": "incomplete",
                "message": f"Job is not complete yet. Current state: {job.state.name}",
                "job_id": job_id,
            }

        # Get task details
        task_details = get_task_details(job)
        predictions = task_details.get("predictions", [])

        if not predictions:
            return {
                "status": "no_predictions",
                "message": "No predictions found for this job",
                "job_id": job_id,
            }

        # Get pipeline root from job runtime_config (not pipeline_spec)
        pipeline_root = ""
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        # Prepare output directory
        if output_dir and download_files:
            os.makedirs(output_dir, exist_ok=True)

        # Process predictions
        all_models = []
        best_model = None

        for idx, pred in enumerate(predictions):
            model_info = {
                "model_name": pred["model_name"],
                "ranking_confidence": pred["ranking_confidence"],
                "rank": idx + 1,
            }

            # Download files if requested
            if download_files and output_dir:
                # Download unrelaxed protein
                unrelaxed_uri = pred.get("uri")
                if unrelaxed_uri:
                    unrelaxed_path = os.path.join(output_dir, f"unrelaxed_{pred['model_name']}.pdb")
                    try:
                        self._download_from_gcs(unrelaxed_uri, unrelaxed_path)
                        model_info["unrelaxed_pdb_path"] = unrelaxed_path
                    except Exception as e:
                        logger.warning(f"Failed to download unrelaxed protein: {e}")

                # Try to find and download relaxed protein
                # This would require finding the relaxed protein task output
                # For now, we'll include the GCS URIs

            model_info["unrelaxed_pdb_uri"] = pred.get("uri")

            all_models.append(model_info)

        best_model = all_models[0] if all_models else None

        # Get MSA info from artifacts
        msa_info = None
        if (
            hasattr(job, "job_detail")
            and job.job_detail
            and hasattr(job.job_detail, "pipeline_run_context")
        ):
            pipeline_ctx = job.job_detail.pipeline_run_context.name
            try:
                artifacts = list_artifacts(
                    project_id=self.config.project_id,
                    region=self.config.region,
                    pipeline_context=pipeline_ctx,
                    artifact_filter='display_name="features"',
                )

                if artifacts:
                    features_artifact = artifacts[0]
                    if hasattr(features_artifact, "metadata"):
                        msa_info = {
                            "final_dedup_msa_size": features_artifact.metadata.get(
                                "final_dedup_msa_size"
                            ),
                            "total_number_templates": features_artifact.metadata.get(
                                "total_number_templates"
                            ),
                        }
            except Exception as e:
                logger.warning(f"Failed to retrieve MSA info: {e}")

        return {
            "status": "success",
            "job_id": job_id,
            "best_model": best_model,
            "all_models": all_models,
            "total_models": len(all_models),
            "msa_info": msa_info,
            "pipeline_root": pipeline_root,
            "gcs_console_url": self.gcs_console_url(pipeline_root) if pipeline_root else None,
            "output_dir": output_dir if download_files else None,
        }
