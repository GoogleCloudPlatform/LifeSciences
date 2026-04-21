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

"""Tool for submitting Boltz2 structure predictions."""

import yaml
import logging
import os
import tempfile
from datetime import datetime
from typing import Any, Dict

from google.cloud import aiplatform as vertex_ai

from ....utils.pipeline_dedup import check_duplicate_job
from ..base import BOLTZ2Tool
from ..utils.input_converter import count_tokens, fasta_to_boltz2_yaml, is_boltz2_yaml, validate_boltz2_yaml

logger = logging.getLogger(__name__)


class BOLTZ2SubmitPredictionTool(BOLTZ2Tool):
    """Tool for submitting Boltz2 structure predictions."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Submit BOLTZ2 prediction job.

        Args:
            arguments: {
                'input': FASTA content, BOLTZ2 JSON content, or file path,
                'job_name': Optional job name,
                'num_model_seeds': Number of seeds (default: 1),
                'gpu_type': 'auto', 'A100', or 'A100_80GB',
                'enable_flex_start': Enable DWS FLEX_START (default: true),
            }

        Returns:
            Job submission details.
        """
        input_data = arguments.get("input")
        job_name = arguments.get("job_name", f"boltz2_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

        # Dedup: block submission if a job with the same name is already running
        duplicate = check_duplicate_job(job_name, self.config.project, self.config.location)
        if duplicate:
            return duplicate

        num_model_seeds = arguments.get("num_model_seeds", 1)
        num_diffusion_samples = arguments.get("num_diffusion_samples", 5)
        gpu_type = arguments.get("gpu_type", "auto")
        enable_flex_start = arguments.get("enable_flex_start", True)

        # Determine input type and load content
        is_gcs = isinstance(input_data, str) and input_data.startswith("gs://")
        is_file = os.path.isfile(input_data) if isinstance(input_data, str) and not is_gcs else False

        if is_gcs:
            bucket_name = input_data[5:].split("/", 1)[0]
            blob_path = input_data[5:].split("/", 1)[1]
            bucket = self.storage_client.bucket(bucket_name)
            content = bucket.blob(blob_path).download_as_text()
        elif is_file:
            with open(input_data) as f:
                content = f.read()
        else:
            content = input_data

        if is_boltz2_yaml(content):
            query_yaml = content
            ok, errors, warnings = validate_boltz2_yaml(query_yaml)
            if not ok:
                return {
                    "status": "error",
                    "message": "Invalid Boltz-2 query YAML:\n" + "\n".join(f"  - {e}" for e in errors),
                }
            for w in warnings:
                logger.warning(f"Boltz-2 YAML warning: {w}")
        else:
            # Treat as FASTA and convert (fasta_to_boltz2_yaml raises on invalid input)
            query_yaml = fasta_to_boltz2_yaml(content, job_name)

        # Count tokens for GPU auto-selection
        num_tokens = count_tokens(query_yaml)

        # Write query YAML to temp file
        temp_yaml = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix=f"boltz2_{job_name}_"
        )
        temp_yaml.write(query_yaml)
        temp_yaml.close()

        # Upload to GCS
        gcs_query_path = f"gs://{self.config.bucket_name}/boltz2_queries/{job_name}.yaml"
        self._upload_to_gcs(temp_yaml.name, gcs_query_path)

        # Get Filestore info
        if not self.config.filestore_ip:
            filestore_ip, filestore_network = self._get_filestore_info()
        else:
            filestore_ip = self.config.filestore_ip
            filestore_network = self.config.filestore_network

        # Setup hardware configuration
        hardware_config = self._get_hardware_config(
            gpu_type,
            num_tokens=num_tokens,
        )

        resolved_gpu = hardware_config["predict_accel"]
        accel_to_label = {
            "NVIDIA_TESLA_A100": "a100",
            "NVIDIA_A100_80GB": "a100-80gb",
        }

        # Setup environment for pipeline compilation
        self._setup_compile_env(hardware_config, filestore_ip, filestore_network)

        # Load and compile pipeline
        from ..utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(enable_flex_start=enable_flex_start)

        pipeline_path = os.path.join(tempfile.gettempdir(), f"boltz2_pipeline_{job_name}.yaml")
        from kfp import compiler

        compiler.Compiler().compile(
            pipeline_func=pipeline,
            package_path=pipeline_path,
        )
        # Prepare labels — parse chain count from YAML for job_type
        import yaml as _yaml
        _parsed = _yaml.safe_load(query_yaml) if isinstance(query_yaml, str) else {}
        _seqs = _parsed.get("sequences", []) if _parsed else []
        _num_chains = sum(
            len(s[list(s.keys())[0]].get("id", ["?"])) if isinstance(s[list(s.keys())[0]].get("id"), list)
            else 1
            for s in _seqs if s
        ) if _seqs else 1
        query_name = job_name
        labels = {
            "model_type": "boltz2",
            "job_type": "monomer" if _num_chains <= 1 else "complex",
            "query_name": self._clean_label(query_name),
            "num_tokens": str(num_tokens),
            "num_chains": str(_num_chains),
            "num_seeds": str(num_model_seeds),
            "gpu_type": accel_to_label.get(resolved_gpu, gpu_type.lower().replace("_", "-")),
            "msa_method": "jackhmmer",
            "submitted_by": "foldrun-agent",
        }

        # Submit pipeline job
        pipeline_root = f"gs://{self.config.bucket_name}/pipeline_runs/{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        job_kwargs = {
            "display_name": job_name,
            "template_path": pipeline_path,
            "pipeline_root": pipeline_root,
            "parameter_values": {
                "query_json_path": gcs_query_path,
                "project": self.config.project_id,
                "region": self.config.region,
                "nfs_cache_path": f"{self.config.nfs_mount_point}/{self.config.cache_path}",
                "num_model_seeds": num_model_seeds,
                "num_diffusion_samples": num_diffusion_samples,
            },
            "enable_caching": True,
            "labels": labels,
        }

        pipeline_job = vertex_ai.PipelineJob(**job_kwargs)
        pipeline_job.submit(
            network=filestore_network, service_account=os.environ.get("PIPELINES_SA_EMAIL")
        )

        # Clean up temp files
        os.remove(pipeline_path)
        os.remove(temp_yaml.name)

        # Chain info from yaml dict
        mol_types = {}
        num_chains = 0
        parsed_yaml = yaml.safe_load(query_yaml)
        for seq in parsed_yaml.get("sequences", []):
            if "protein" in seq:
                mt = "protein"
            elif "rna" in seq:
                mt = "rna"
            elif "dna" in seq:
                mt = "dna"
            elif "smiles" in seq:
                mt = "ligand"
            else:
                mt = "other"
            mol_types[mt] = mol_types.get(mt, 0) + 1
            num_chains += 1

        return {
            "job_id": pipeline_job.resource_name,
            "job_name": job_name,
            "status": "submitted",
            "console_url": f"https://console.cloud.google.com/vertex-ai/locations/{self.config.region}/pipelines/runs/{pipeline_job.resource_name.split('/')[-1]}?project={self.config.project_id}",
            "input_info": {
                "name": query_name,
                "num_tokens": num_tokens,
                "num_chains": num_chains,
                "molecule_types": mol_types,
                "num_model_seeds": num_model_seeds,
                "num_diffusion_samples": num_diffusion_samples,
            },
            "hardware": {
                "msa_pipeline": f"{hardware_config['msa_machine']} (CPU-only, Jackhmmer protein MSA)",
                "prediction": f"{hardware_config['predict_machine']} ({hardware_config['predict_accel']} x{hardware_config['predict_count']})",
                "scheduling": "FLEX_START (DWS)" if enable_flex_start else "ON_DEMAND",
            },
            "submitted_at": datetime.now().isoformat(),
            "pipeline_root": pipeline_root,
            "gcs_console_url": self.gcs_console_url(pipeline_root),
        }
