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

import logging
import os
import re
import tempfile
from contextlib import nullcontext
from datetime import datetime
from typing import Any

import yaml
from google.cloud import aiplatform as vertex_ai

from foldrun_app.gateway._compat import GLOBAL_COMPILE_LOCK

from ..base import BOLTZ2Tool
from ..utils.input_converter import (
    count_tokens,
    fasta_to_boltz2_yaml,
    is_boltz2_yaml,
    validate_boltz2_yaml,
)

logger = logging.getLogger(__name__)

_COMPILE_LOCK = GLOBAL_COMPILE_LOCK
_COMPILE_ENV_LOCK = GLOBAL_COMPILE_LOCK


class BOLTZ2SubmitPredictionTool(BOLTZ2Tool):
    """Tool for submitting Boltz2 structure predictions."""

    _compile_env_lock = GLOBAL_COMPILE_LOCK

    @staticmethod
    def _validate_local_file_path(file_path: str) -> str:
        """Validate that a local file path resides within an explicitly authorized directory.

        Uses ``os.path.realpath`` to resolve symlinks and relative segments before
        verifying directory containment via ``os.path.commonpath``.
        """
        allowed_dir = os.environ.get("FOLDRUN_ALLOWED_INPUT_DIR", "").strip()
        if not allowed_dir:
            raise ValueError(
                "Local file input is not allowed unless FOLDRUN_ALLOWED_INPUT_DIR is explicitly configured. "
                "Provide inline FASTA/YAML content or a gs:// URI instead."
            )

        real_allowed = os.path.realpath(allowed_dir)
        real_path = os.path.realpath(file_path)

        if (
            os.path.commonpath([real_allowed, real_path]) != real_allowed
            or real_path == real_allowed
        ):
            raise ValueError(
                f"Local file path '{file_path}' is outside the authorized directory '{real_allowed}'."
            )

        return real_path

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Submit BOLTZ2 prediction job.

        Args:
            arguments: {
                'input': FASTA content, BOLTZ2 JSON content, or file path,
                'job_name': Optional job name,
                'num_model_seeds': Number of seeds (default: 1),
                'gpu_type': 'auto', 'L4', 'A100', or 'A100_80GB',
                'enable_flex_start': Enable DWS FLEX_START (default: true),
            }

        Returns:
            Job submission details.
        """
        input_data = arguments.get("input")
        raw_job_name = arguments.get(
            "job_name", f"boltz2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        job_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(raw_job_name)).strip("._-")
        if not job_name:
            job_name = f"boltz2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        num_model_seeds = arguments.get("num_model_seeds", 1)
        num_diffusion_samples = arguments.get("num_diffusion_samples", 5)
        gpu_type = arguments.get("gpu_type") or "auto"
        enable_flex_start = arguments.get("enable_flex_start", True)
        extra_cli_args = arguments.get("extra_cli_args") or []
        custom_job_id = arguments.get("custom_job_id")
        pipeline_root_override = arguments.get("pipeline_root")
        extra_labels = arguments.get("extra_labels") or {}
        query_name_override = arguments.get("query_name")
        allow_local_files = arguments.get("allow_local_files", True)
        dws_max_wait_hours = arguments.get("dws_max_wait_hours")

        import random

        base_seed = arguments.get("base_seed")
        if base_seed is None:
            base_seed = random.randint(0, 2**32 - 1)

        # Determine input type and load content
        is_gcs = isinstance(input_data, str) and input_data.startswith("gs://")
        looks_like_file = (
            isinstance(input_data, str)
            and not is_gcs
            and "\n" not in input_data
            and not input_data.lstrip().startswith(("version:", ">"))
            and (
                os.path.isfile(input_data)
                or (not allow_local_files and input_data.startswith(("/", "./", "../", "~")))
            )
        )

        if is_gcs:
            bucket_name, blob_path = self._validate_gcs_uri(input_data)
            if not allow_local_files and bucket_name != self.config.bucket_name:
                raise ValueError(
                    f"Unauthorized GCS bucket '{bucket_name}'; expected '{self.config.bucket_name}'."
                )
            bucket = self.storage_client.bucket(bucket_name)
            content = bucket.blob(blob_path).download_as_text()
        elif looks_like_file:
            if not allow_local_files:
                raise ValueError(
                    "Local file path inputs are not allowed on remote gateway requests. "
                    "Stage inputs to GCS first."
                )
            validated_path = self._validate_local_file_path(input_data)
            with open(validated_path, encoding="utf-8") as f:
                content = f.read()
        else:
            content = input_data

        if is_boltz2_yaml(content):
            query_yaml = content
            ok, errors, warnings = validate_boltz2_yaml(query_yaml)
            if not ok:
                return {
                    "status": "error",
                    "message": "Invalid Boltz-2 query YAML:\n"
                    + "\n".join(f"  - {e}" for e in errors),
                }
            for w in warnings:
                logger.warning(f"Boltz-2 YAML warning: {w}")
        else:
            # Treat as FASTA and convert (fasta_to_boltz2_yaml raises on invalid input)
            query_yaml = fasta_to_boltz2_yaml(content, job_name)

        # Count tokens for GPU auto-selection
        num_tokens = count_tokens(query_yaml)

        safe_job_name = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", (job_name or "").strip()).strip("._-")[:96] or "job"
        )
        # Write query YAML to temp file
        temp_yaml = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix=f"boltz2_{safe_job_name}_"
        )
        temp_yaml.write(query_yaml)
        temp_yaml.close()

        with tempfile.NamedTemporaryFile(
            mode="w", prefix=f"boltz2_pipeline_{safe_job_name}_", suffix=".yaml", delete=False
        ) as pipeline_tmp:
            pipeline_path = pipeline_tmp.name

        try:
            # Upload to GCS
            query_key = custom_job_id or safe_job_name
            gcs_query_path = f"gs://{self.config.bucket_name}/boltz2_queries/{query_key}.yaml"
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
            if dws_max_wait_hours is not None:
                hardware_config["dws_max_wait_hours"] = int(dws_max_wait_hours)

            resolved_gpu = hardware_config["predict_accel"]
            accel_to_label = {
                "NVIDIA_L4": "l4",
                "NVIDIA_TESLA_A100": "a100",
                "NVIDIA_A100_80GB": "a100-80gb",
            }

            with self._compile_env_lock:
                env_ctx = self._setup_compile_env(hardware_config, filestore_ip, filestore_network)
                with env_ctx if env_ctx is not None else nullcontext():
                    # Load and compile pipeline
                    from ..utils.pipeline_utils import load_vertex_pipeline

                    pipeline = load_vertex_pipeline(enable_flex_start=enable_flex_start)

                    from kfp import compiler

                    compiler.Compiler().compile(
                        pipeline_func=pipeline,
                        package_path=pipeline_path,
                    )

            # Prepare labels — parse chain count from YAML for job_type
            import yaml as _yaml

            _parsed = _yaml.safe_load(query_yaml) if isinstance(query_yaml, str) else {}
            _seqs = _parsed.get("sequences", []) if _parsed else []
            _num_chains = (
                sum(
                    len(s[next(iter(s.keys()))].get("id", ["?"]))
                    if isinstance(s[next(iter(s.keys()))].get("id"), list)
                    else 1
                    for s in _seqs
                    if s
                )
                if _seqs
                else 1
            )
            query_name = query_name_override or job_name
            labels = {
                "model_type": "boltz2",
                "job_type": "monomer" if _num_chains <= 1 else "complex",
                "seq_name": self._clean_label(query_name),
                "query_name": self._clean_label(query_name),
                "num_tokens": str(num_tokens),
                "num_chains": str(_num_chains),
                "num_seeds": str(num_model_seeds),
                "gpu_type": accel_to_label.get(resolved_gpu, gpu_type.lower().replace("_", "-")),
                "msa_method": "jackhmmer",
                "submitted_by": "foldrun-agent",
            }
            for k, v in extra_labels.items():
                labels[str(k)] = self._clean_label(str(v))

            # Submit pipeline job
            pipeline_root = (
                pipeline_root_override
                or f"gs://{self.config.bucket_name}/pipeline_runs/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

            parameter_values: dict[str, Any] = {
                "query_json_path": gcs_query_path,
                "project": self.config.project_id,
                "region": self.config.region,
                "nfs_cache_path": f"{self.config.nfs_mount_point}/{self.config.cache_path}",
                "num_model_seeds": num_model_seeds,
                "num_diffusion_samples": num_diffusion_samples,
                "base_seed": base_seed,
            }
            if extra_cli_args:
                parameter_values["extra_cli_args"] = [str(a) for a in extra_cli_args]

            job_kwargs = {
                "display_name": job_name,
                "template_path": pipeline_path,
                "pipeline_root": pipeline_root,
                "parameter_values": parameter_values,
                "enable_caching": True,
                "labels": labels,
            }
            if custom_job_id:
                job_kwargs["job_id"] = custom_job_id

            pipeline_job = vertex_ai.PipelineJob(**job_kwargs)
            pipeline_job.submit(
                network=filestore_network, service_account=self.config.pipelines_sa_email
            )
        finally:
            if os.path.exists(pipeline_path):
                os.remove(pipeline_path)
            if os.path.exists(temp_yaml.name):
                os.remove(temp_yaml.name)

        # Chain info from yaml dict (accounting for homo-oligomer id lists)
        mol_types = {}
        num_chains = 0
        parsed_yaml = yaml.safe_load(query_yaml)
        for seq in parsed_yaml.get("sequences", []):
            if not seq or not isinstance(seq, dict):
                continue
            entry_key = next(iter(seq.keys()))
            entry_val = seq.get(entry_key) or {}
            ids = entry_val.get("id", "A") if isinstance(entry_val, dict) else "A"
            chain_count = max(1, len(ids)) if isinstance(ids, list) else 1
            if "protein" in seq:
                mt = "protein"
            elif "rna" in seq:
                mt = "rna"
            elif "dna" in seq:
                mt = "dna"
            elif "smiles" in seq or "ligand" in seq or "ccd" in seq:
                mt = "ligand"
            else:
                mt = "other"
            mol_types[mt] = mol_types.get(mt, 0) + chain_count
            num_chains += chain_count

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
                "base_seed": base_seed,
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
