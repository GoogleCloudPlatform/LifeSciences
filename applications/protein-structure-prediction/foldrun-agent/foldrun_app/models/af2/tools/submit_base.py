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

"""Shared base tool for AlphaFold2 monomer and multimer KFP submissions."""

import logging
import os
import tempfile
from datetime import datetime
from typing import Any, ClassVar

from ..base import AF2Tool
from ..utils.fasta_utils import (
    _validate_sequences,
    get_sequence_length,
    parse_fasta_content,
    validate_fasta_file,
    write_fasta,
)

logger = logging.getLogger(__name__)


class AF2SubmitBaseTool(AF2Tool):
    """Shared submission implementation for AF2 monomer and multimer tools."""

    is_multimer: ClassVar[bool] = False
    job_type: ClassVar[str] = "monomer"

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate FASTA input, compile the AF2 KFP pipeline, and submit the Vertex PipelineJob."""
        sequence = arguments.get("sequence")
        default_job_name = f"{self.job_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        job_name = self.sanitize_job_name(arguments.get("job_name"), default_job_name)
        max_template_date = arguments.get("max_template_date", "2025-04-01")
        use_small_bfd = arguments.get("use_small_bfd", True)
        run_relaxation = arguments.get("run_relaxation", True)
        gpu_type = arguments.get("gpu_type", "auto")
        relax_gpu_type = arguments.get("relax_gpu_type")
        num_predictions_per_model = (
            arguments.get("num_predictions_per_model", 5) if self.is_multimer else 1
        )
        enable_flex_start = arguments.get("enable_flex_start", True)
        msa_method = arguments.get("msa_method", "auto")

        if msa_method == "mmseqs2":
            if not use_small_bfd:
                return {
                    "status": "error",
                    "message": (
                        "msa_method=mmseqs2 requires use_small_bfd=True. "
                        "MMseqs2 GPU acceleration only works with FASTA sequence databases "
                        "(uniref90, mgnify, small_bfd). Set use_small_bfd=True or use msa_method=jackhmmer."
                    ),
                }
            logger.info(
                "MMseqs2 requested — requires pre-built MMseqs2 indexes on NFS. "
                "If indexes are missing, run './deploy-all.sh <PROJECT_ID> <REGION> --steps convert' first."
            )

        is_gcs = isinstance(sequence, str) and sequence.startswith("gs://")
        looks_like_local_path = (
            isinstance(sequence, str)
            and not is_gcs
            and (os.path.exists(sequence) or sequence.startswith(("/", "./", "../", "~")))
        )

        is_fasta_file = False
        fasta_path = ""
        if is_gcs:
            bucket_name, blob_path = self._validate_gcs_uri(sequence)
            bucket = self.storage_client.bucket(bucket_name)
            sequence = bucket.blob(blob_path).download_as_text()

        if looks_like_local_path and isinstance(sequence, str):
            fasta_path = self.resolve_local_input_path(sequence, allow_temp_dir=True)
            is_fasta_file = True
            is_monomer, sequences = validate_fasta_file(fasta_path)
            _validate_sequences(sequences)
        else:
            sequences = parse_fasta_content(sequence)
            is_monomer = len(sequences) == 1

        if self.is_multimer and is_monomer:
            raise ValueError(
                "Sequence appears to be monomer. Use submit_af2_monomer_prediction instead."
            )
        if not self.is_multimer and not is_monomer:
            raise ValueError(
                "Sequence appears to be multimer. Use submit_af2_multimer_prediction instead."
            )

        extra_temp_paths: list[str] = []
        if not is_fasta_file:
            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".fasta", delete=False)
            write_fasta(sequences, temp_file.name)
            fasta_path = temp_file.name
            extra_temp_paths.append(fasta_path)

        seq_name = sequences[0]["description"].split()[0] if sequences else job_name
        seq_length = get_sequence_length(sequences)
        num_chains = len(sequences)

        with tempfile.NamedTemporaryFile(
            mode="w", prefix=f"af2_pipeline_{job_name}_", suffix=".yaml", delete=False
        ) as pipeline_tmp:
            pipeline_path = pipeline_tmp.name

        try:
            gcs_sequence_path = f"gs://{self.config.bucket_name}/fasta/{job_name}.fasta"
            self._upload_to_gcs(fasta_path, gcs_sequence_path)

            filestore_ip, filestore_network = self._ensure_filestore_config()

            hardware_config = self._get_hardware_config(
                gpu_type,
                relax_gpu_type=relax_gpu_type,
                msa_method=msa_method,
                use_small_bfd=use_small_bfd,
                seq_length=seq_length,
                is_multimer=self.is_multimer,
            )

            resolved_gpu = hardware_config["predict_accel"]
            resolved_msa = hardware_config["msa_method"]

            labels = {
                "model_type": "alphafold2",
                "job_type": self.job_type,
                "query_name": self._clean_label(seq_name),
                "num_tokens": str(seq_length),
                "num_chains": str(num_chains if self.is_multimer else 1),
                "gpu_type": self.ACCEL_TO_LABEL.get(
                    resolved_gpu, gpu_type.lower().replace("_", "-")
                ),
                "msa_method": resolved_msa,
                "submitted_by": "foldrun-agent",
            }

            pipeline_root = f"gs://{self.config.bucket_name}/pipeline_runs/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            model_params_gcs_location = f"gs://{self.config.databases_bucket_name}/alphafold2"

            job_kwargs = {
                "display_name": job_name,
                "template_path": pipeline_path,
                "pipeline_root": pipeline_root,
                "parameter_values": {
                    "sequence_path": gcs_sequence_path,
                    "max_template_date": max_template_date,
                    "model_preset": self.job_type,
                    "model_params_gcs_location": model_params_gcs_location,
                    "project": self.config.project_id,
                    "region": self.config.region,
                    "use_small_bfd": use_small_bfd,
                    "num_multimer_predictions_per_model": num_predictions_per_model,
                    "is_run_relax": "relax" if run_relaxation else "",
                },
                "enable_caching": True,
                "labels": labels,
            }

            from ..utils.pipeline_utils import load_vertex_pipeline as default_loader

            pipeline_loader_fn = self._resolve_module_attr("load_vertex_pipeline", default_loader)
            pipeline_job = self._compile_and_submit_kfp_pipeline(
                pipeline_loader=lambda: pipeline_loader_fn(
                    enable_flex_start=enable_flex_start, msa_method=resolved_msa
                ),
                package_path=pipeline_path,
                hardware_config=hardware_config,
                filestore_ip=filestore_ip,
                filestore_network=filestore_network,
                job_kwargs=job_kwargs,
                service_account=self.config.pipelines_sa_email,
                extra_temp_paths=extra_temp_paths,
            )
        except Exception:
            for path in [pipeline_path, *extra_temp_paths]:
                if path and os.path.exists(path):
                    os.remove(path)
            raise

        seq_info: dict[str, Any] = {
            "name": seq_name,
            "length": seq_length,
            "type": self.job_type,
        }
        if self.is_multimer:
            seq_info["num_chains"] = num_chains

        response: dict[str, Any] = {
            "job_id": pipeline_job.resource_name,
            "job_name": job_name,
            "status": "submitted",
            "console_url": (
                f"https://console.cloud.google.com/vertex-ai/locations/{self.config.region}"
                f"/pipelines/runs/{pipeline_job.resource_name.split('/')[-1]}?project={self.config.project_id}"
            ),
            "sequence_info": seq_info,
            "hardware": {
                "data_pipeline": (
                    f"{hardware_config.get('dp_machine', hardware_config['data_pipeline'])} (MMseqs2-GPU)"
                    if resolved_msa == "mmseqs2"
                    else hardware_config["data_pipeline"]
                ),
                "msa_method": resolved_msa,
                "prediction": (
                    f"{hardware_config['predict_machine']} "
                    f"({hardware_config['predict_accel']} x{hardware_config['predict_count']})"
                ),
                "relaxation": (
                    f"{hardware_config['relax_machine']} "
                    f"({hardware_config['relax_accel']} x{hardware_config['relax_count']})"
                    if run_relaxation
                    else "skipped"
                ),
                "scheduling": "FLEX_START (DWS)" if enable_flex_start else "ON_DEMAND",
            },
        }
        if self.is_multimer:
            response["num_predictions_per_model"] = num_predictions_per_model
            response["total_predictions"] = num_predictions_per_model * 5
        response["submitted_at"] = datetime.now().isoformat()
        response["pipeline_root"] = pipeline_root
        response["gcs_console_url"] = self.gcs_console_url(pipeline_root)
        return response
