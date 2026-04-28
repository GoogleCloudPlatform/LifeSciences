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

"""Tool for retrieving detailed job metadata including FASTA sequences."""

from typing import Any, Dict

from google.cloud import storage

from foldrun_app.app_utils.gcs_retry import GCS_RETRY

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job


class AF2GetJobDetailsTool(AF2Tool):
    """Tool for retrieving detailed job information including original FASTA sequence."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get detailed job information including the original FASTA sequence.

        This allows you to review job parameters and resubmit if needed with
        modified settings (e.g., different GPU type).

        Args:
            arguments: {
                'job_id': Job ID to retrieve details for
            }

        Returns:
            Detailed job information including:
            - Job metadata (name, state, timestamps)
            - Original parameters (GPU type, template date, relaxation settings)
            - FASTA sequence and header
            - Resubmission-ready parameters
        """
        # Extract parameters
        job_id = arguments.get("job_id")
        if not job_id:
            raise ValueError("job_id is required")

        # Step 1: Get job metadata from Vertex AI
        job = get_pipeline_job(job_id, self.config.project_id, self.config.region)

        # Extract metadata
        labels = dict(job.labels) if job.labels else {}
        params = dict(job.runtime_config.parameter_values) if job.runtime_config else {}

        # Get the original input path from parameters
        # AF2 jobs use 'sequence_path' (FASTA), OF3 jobs use 'query_json_path' (JSON)
        sequence_path = params.get("sequence_path")
        query_json_path = params.get("query_json_path")
        input_path = sequence_path or query_json_path

        if not input_path:
            raise ValueError(f"Could not find sequence_path or query_json_path in job {job_id}")

        is_of3 = labels.get("model_type") == "openfold3" or query_json_path is not None

        # Step 2: Download the input content from GCS
        fasta_header = None
        fasta_sequence = None

        if input_path.startswith("gs://"):
            # Parse GCS path: gs://bucket_name/path/to/file
            path_parts = input_path[5:].split("/", 1)  # Remove 'gs://' and split
            bucket_name = path_parts[0]
            blob_path = path_parts[1]

            try:
                # Download input from GCS
                storage_client = storage.Client(project=self.config.project_id)
                bucket = storage_client.bucket(bucket_name)
                blob = bucket.blob(blob_path)
                fasta_content = blob.download_as_text(retry=GCS_RETRY)

                if is_of3:
                    # OF3 input is JSON
                    fasta_header = "OF3 query JSON"
                    fasta_sequence = fasta_content  # Store raw JSON as the "sequence"
                else:
                    # AF2 input is FASTA
                    lines = fasta_content.strip().split("\n")
                    if lines and lines[0].startswith(">"):
                        fasta_header = lines[0][1:].strip()  # Remove '>'
                        fasta_sequence = "".join(line.strip() for line in lines[1:])
            except Exception as e:
                fasta_header = f"Error retrieving input: {str(e)}"
                fasta_sequence = None

        # Extract job timing info
        create_time = job.create_time.isoformat() if job.create_time else None
        start_time = job.start_time.isoformat() if job.start_time else None
        end_time = job.end_time.isoformat() if job.end_time else None

        duration = None
        if job.end_time and job.start_time:
            duration = (job.end_time - job.start_time).total_seconds()

        # Determine relaxation setting
        is_run_relax = params.get("is_run_relax", "")
        run_relaxation = is_run_relax == "relax"

        # Extract scheduling configuration from job parameters
        strategy = params.get("strategy", "unknown")
        enable_flex_start = strategy == "FLEX_START"

        # Extract per-task worker pool configurations from task execution metadata
        task_configs = {}
        if hasattr(job, "job_detail") and job.job_detail:
            # Track unique task names to avoid duplicates
            seen_tasks = set()

            for task in job.job_detail.task_details:
                task_name = task.task_name

                # Skip if we've already processed this task type
                if task_name in seen_tasks:
                    continue
                seen_tasks.add(task_name)

                # Extract execution metadata
                if hasattr(task, "execution") and task.execution:
                    if hasattr(task.execution, "metadata") and task.execution.metadata:
                        metadata = task.execution.metadata

                        # Extract worker pool specs
                        if "input:worker_pool_specs" in metadata:
                            worker_pools = metadata["input:worker_pool_specs"]

                            if len(worker_pools) > 0:
                                pool = worker_pools[0]

                                # Extract machine spec (GPU configuration)
                                machine_spec = pool.get("machine_spec", {})

                                # Extract scheduling parameters from task metadata
                                task_strategy = metadata.get("input:strategy", "STANDARD")
                                task_max_wait = metadata.get("input:max_wait_duration", "none")

                                task_configs[task_name] = {
                                    "machine_type": machine_spec.get("machine_type", "unknown"),
                                    "accelerator_type": machine_spec.get(
                                        "accelerator_type", "none"
                                    ),
                                    "accelerator_count": int(
                                        machine_spec.get("accelerator_count", 0)
                                    ),
                                    "strategy": task_strategy,
                                    "enable_flex_start": (task_strategy == "FLEX_START"),
                                    "max_wait_duration": task_max_wait,
                                }

        # Build comprehensive response
        result = {
            "job_id": job.name,
            "job_name": job.display_name,
            "state": job.state.name,
            "job_type": labels.get("job_type", "unknown"),
            "timing": {
                "created": create_time,
                "started": start_time,
                "ended": end_time,
                "duration_seconds": duration,
            },
            "input": {
                "header": fasta_header,
                "content": fasta_sequence,
                "length": labels.get("seq_len") or labels.get("num_tokens", "unknown"),
                "name": labels.get("seq_name") or labels.get("query_name", "unknown"),
                "path": input_path,
                "format": "json" if is_of3 else "fasta",
            },
            "parameters": {
                "model_type": labels.get("model_type", "alphafold2"),
                "gpu_type": labels.get("gpu_type", "unknown"),
                "max_template_date": params.get("max_template_date", "unknown"),
                "use_small_bfd": params.get("use_small_bfd", True),
                "run_relaxation": run_relaxation,
                "model_preset": params.get("model_preset", "unknown"),
                "num_multimer_predictions_per_model": params.get(
                    "num_multimer_predictions_per_model", 1
                ),
                "num_model_seeds": params.get("num_model_seeds"),
                "num_diffusion_samples": params.get("num_diffusion_samples"),
                "strategy": strategy,
                "enable_flex_start": enable_flex_start,
                "max_wait_duration": params.get("max_wait_duration", "unknown"),
            },
            "task_configurations": task_configs,  # Per-task worker pool specs
            "labels": labels,
            "error": {
                "has_error": hasattr(job, "error") and job.error is not None,
                "message": job.error.message if hasattr(job, "error") and job.error else None,
            },
            "resubmission_ready": {
                "input_available": fasta_sequence is not None,
                "suggested_params": {
                    "input": fasta_sequence
                    if is_of3
                    else (f">{fasta_header}\n{fasta_sequence}" if fasta_sequence else None),
                    "job_name": f"{job.display_name}_retry",
                    "model_type": labels.get("model_type", "alphafold2"),
                    "gpu_type": labels.get("gpu_type", "").upper().replace("-", "_")
                    if labels.get("gpu_type")
                    else None,
                    "max_template_date": params.get("max_template_date"),
                    "use_small_bfd": params.get("use_small_bfd"),
                    "run_relaxation": run_relaxation,
                    "enable_flex_start": True,
                },
            },
        }

        # Add pipeline root if available (from runtime_config, not pipeline_spec)
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                result["pipeline_root"] = job.runtime_config.gcs_output_directory

        return result
