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

"""Tool for submitting monomer AlphaFold2 predictions."""

import logging
import os
import tempfile
from datetime import datetime
from typing import Any, Dict

from google.cloud import aiplatform_v1

logger = logging.getLogger(__name__)

from ..base import AF2Tool
from ..utils.fasta_utils import (
    get_sequence_length,
    parse_fasta_content,
    validate_fasta_file,
    write_fasta,
)


class AF2SubmitMonomerTool(AF2Tool):
    """Tool for submitting monomer AlphaFold2 predictions."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit monomer prediction job.

        Args:
            arguments: {
                'sequence': FASTA content or file path,
                'job_name': Optional job name,
                'max_template_date': Template search date,
                'use_small_bfd': Use small BFD database,
                'run_relaxation': Run AMBER relaxation,
                'gpu_type': 'L4', 'A100', or 'A100_80GB',
                'vertex_repo_path': (Deprecated) Pipeline is now vendored internally
            }

        Returns:
            Job submission details
        """
        # Extract parameters
        sequence = arguments.get("sequence")
        now = datetime.now()
        job_id = f"alphafold2-inference-pipeline-{now.strftime('%Y%m%d%H%M%S')}"
        job_name = arguments.get("job_name", f"monomer_{now.strftime('%Y%m%d_%H%M%S')}")
        max_template_date = arguments.get(
            "max_template_date", "2025-04-01"
        )  # Matches DB download date (April 2025)
        use_small_bfd = arguments.get("use_small_bfd", True)
        run_relaxation = arguments.get("run_relaxation", True)
        gpu_type = arguments.get(
            "gpu_type", "auto"
        )  # Default to auto-select based on sequence length
        relax_gpu_type = arguments.get("relax_gpu_type")  # Optional override for relax phase
        enable_flex_start = arguments.get("enable_flex_start", True)
        msa_method = arguments.get("msa_method", "auto")  # 'auto', 'jackhmmer', or 'mmseqs2'
        vertex_repo_path = arguments.get(
            "vertex_repo_path"
        )  # No longer needed, kept for backwards compatibility

        # Validate: explicit mmseqs2 requires use_small_bfd=True and pre-built indexes
        if msa_method == "mmseqs2":
            if not use_small_bfd:
                return {
                    "status": "error",
                    "message": "msa_method=mmseqs2 requires use_small_bfd=True. "
                    "MMseqs2 GPU acceleration only works with FASTA sequence databases "
                    "(uniref90, mgnify, small_bfd). Set use_small_bfd=True or use msa_method=jackhmmer.",
                }
            logger.info(
                "MMseqs2 requested — requires pre-built MMseqs2 indexes on NFS. "
                "If indexes are missing, run ConvertMMseqs2Tool first."
            )

        # Validate and prepare FASTA
        is_gcs = isinstance(sequence, str) and sequence.startswith("gs://")
        is_fasta_file = os.path.isfile(sequence) if isinstance(sequence, str) and not is_gcs else False

        if is_gcs:
            bucket_name = sequence[5:].split("/", 1)[0]
            blob_path = sequence[5:].split("/", 1)[1]
            bucket = self.storage_client.bucket(bucket_name)
            sequence = bucket.blob(blob_path).download_as_text()
            is_fasta_file = False
        if is_fasta_file:
            fasta_path = sequence
            is_monomer, sequences = validate_fasta_file(fasta_path)
        else:
            # Parse FASTA content
            sequences = parse_fasta_content(sequence)
            is_monomer = len(sequences) == 1

            # Write to temporary file
            temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".fasta", delete=False)
            write_fasta(sequences, temp_file.name)
            fasta_path = temp_file.name

        if not is_monomer:
            raise ValueError(
                "Sequence appears to be multimer. Use submit_af2_multimer_prediction instead."
            )

        seq_name = sequences[0]["description"].split()[0] if sequences else job_name
        seq_length = get_sequence_length(sequences)

        # Upload to GCS
        gcs_sequence_path = f"gs://{self.config.bucket_name}/fasta/{job_name}.fasta"
        self._upload_to_gcs(fasta_path, gcs_sequence_path)

        filestore_ip = self.config.filestore_ip
        if not filestore_ip:
            return {
                "status": "error",
                "message": "FILESTORE_IP environment variable must be set.",
            }

        # Setup hardware configuration and environment (auto-selects GPU and MSA method)
        hardware_config = self._get_hardware_config(
            gpu_type,
            relax_gpu_type=relax_gpu_type,
            msa_method=msa_method,
            use_small_bfd=use_small_bfd,
            seq_length=seq_length,
            is_multimer=False,
        )

        # Resolve actual values for labels and response (in case 'auto' was used)
        resolved_gpu = hardware_config["predict_accel"]
        resolved_msa = hardware_config["msa_method"]
        accel_to_label = {
            "NVIDIA_L4": "l4",
            "NVIDIA_TESLA_A100": "a100",
            "NVIDIA_A100_80GB": "a100-80gb",
        }

        # Pass filestore info to environment setup
        self._setup_compile_env(hardware_config, filestore_ip=filestore_ip)

        # Import the vendored pipeline
        from ..utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(
            enable_flex_start=enable_flex_start, msa_method=resolved_msa
        )

        # Compile pipeline
        pipeline_path = os.path.join(tempfile.gettempdir(), f"af2_pipeline_{job_name}.json")
        from kfp import compiler

        compiler.Compiler().compile(pipeline_func=pipeline, package_path=pipeline_path)

        # Prepare labels (use resolved values, not 'auto')
        labels = {
            "model_type": "alphafold2",
            "job_type": "monomer",
            "query_name": self._clean_label(seq_name),
            "num_tokens": str(seq_length),
            "num_chains": "1",
            "gpu_type": accel_to_label.get(resolved_gpu, gpu_type.lower().replace("_", "-")),
            "msa_method": resolved_msa,
            "submitted_by": "foldrun-agent",
        }

        # Submit pipeline job
        pipeline_root = f"gs://{self.config.bucket_name}/pipeline_runs/{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Prepare scheduling strategy
        job_kwargs = {
            "display_name": job_name,
            "template_path": pipeline_path,
            "pipeline_root": pipeline_root,
            "parameter_values": {
                "sequence_path": gcs_sequence_path,
                "max_template_date": max_template_date,
                "model_preset": "monomer",
                "model_params_gcs_location": os.environ.get("MODEL_PARAMS_GCS_LOCATION"),
                "project": self.config.project_id,
                "region": self.config.region,
                "use_small_bfd": use_small_bfd,
                "num_multimer_predictions_per_model": 1,
                "is_run_relax": "relax" if run_relaxation else "",
            },
            "enable_caching": True,  # Enable caching to reuse previous run results
            "labels": labels,
        }

        # Submit pipeline job (DWS scheduling already baked into compiled pipeline if enabled)
        import json

        with open(pipeline_path, "r") as f:
            pipeline_spec = json.load(f)

        client = aiplatform_v1.PipelineServiceClient(
            client_options={"api_endpoint": f"{self.config.region}-aiplatform.googleapis.com"}
        )

        network_attachment = os.environ.get("VERTEX_AI_NETWORK_ATTACHMENT")

        psc_interface_config = aiplatform_v1.PscInterfaceConfig(
            network_attachment=network_attachment
        )

        request = aiplatform_v1.CreatePipelineJobRequest(
            parent=f"projects/{self.config.project_id}/locations/{self.config.region}",
            pipeline_job=aiplatform_v1.PipelineJob(
                display_name=job_name,
                pipeline_spec=pipeline_spec,
                runtime_config=aiplatform_v1.PipelineJob.RuntimeConfig(
                    gcs_output_directory=pipeline_root,
                    parameter_values=job_kwargs["parameter_values"],
                ),
                psc_interface_config=psc_interface_config,
                labels=labels,
                service_account=os.environ.get("PIPELINES_SA_EMAIL"),
            ),
            pipeline_job_id=job_id,
        )

        response = client.create_pipeline_job(request=request)

        # Clean up
        os.remove(pipeline_path)
        if not is_fasta_file:
            os.remove(fasta_path)

        # Return result
        return {
            "job_id": job_id,
            "job_name": job_name,
            "status": "submitted",
            "console_url": f"https://console.cloud.google.com/vertex-ai/locations/{self.config.region}/pipelines/runs/{job_id}?project={self.config.project_id}",
            "sequence_info": {"name": seq_name, "length": seq_length, "type": "monomer"},
            "hardware": {
                "data_pipeline": f"{hardware_config.get('dp_machine', hardware_config['data_pipeline'])} (MMseqs2-GPU)"
                if resolved_msa == "mmseqs2"
                else hardware_config["data_pipeline"],
                "msa_method": resolved_msa,
                "prediction": f"{hardware_config['predict_machine']} ({hardware_config['predict_accel']} x{hardware_config['predict_count']})",
                "relaxation": f"{hardware_config['relax_machine']} ({hardware_config['relax_accel']} x{hardware_config['relax_count']})"
                if run_relaxation
                else "skipped",
                "scheduling": "FLEX_START (DWS)" if enable_flex_start else "ON_DEMAND",
            },
            "submitted_at": datetime.now().isoformat(),
            "pipeline_root": pipeline_root,
            "gcs_console_url": self.gcs_console_url(pipeline_root),
        }
