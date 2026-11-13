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

"""Tool for submitting ESMFold2 / ESMFold2-Fast structure predictions."""

import json
import logging
import os
import random
import tempfile
from datetime import datetime
from typing import Any

from google.cloud import aiplatform as vertex_ai  # noqa: F401

from ..base import ESMFOLD2Tool
from ..utils.input_converter import (
    count_tokens,
    fasta_to_esmfold2_json,
    is_esmfold2_json,
    validate_esmfold2_json,
)

logger = logging.getLogger(__name__)


class ESMFOLD2SubmitPredictionTool(ESMFOLD2Tool):
    """Tool for submitting ESMFold2 / ESMFold2-Fast structure predictions."""

    def _validate_local_file_path(self, file_path: str) -> str:
        """Validate that a local file path resides within an explicitly authorized directory."""
        return self.resolve_local_input_path(file_path, allow_temp_dir=False)

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Submit ESMFold2 / ESMFold2-Fast prediction job.

        Args:
            arguments: {
                'input': FASTA content, ESMFold2 JSON content, or file/GCS path,
                'job_name': Optional job name,
                'model_variant': 'fast' (default, biohub/ESMFold2-Fast) or 'full' (biohub/ESMFold2),
                'use_msa': Optional bool (forced False for 'fast'; defaults True for 'full'),
                'num_model_seeds': Number of seeds (default: 1),
                'num_diffusion_samples': Diffusion samples per seed (default: 1),
                'num_loops': Recycling loops (default: 4),
                'num_sampling_steps': Diffusion sampling steps (default: 200),
                'gpu_type': 'auto', 'L4', 'A100', 'A100_80GB', or 'RTX_PRO_6000',
                'enable_flex_start': Enable DWS FLEX_START (default: true),
            }

        Returns:
            Job submission details.
        """
        input_data = arguments.get("input")
        default_job_name = f"esmfold2_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        job_name = self.sanitize_job_name(arguments.get("job_name"), default_job_name)

        raw_variant = str(arguments.get("model_variant") or "fast").strip().lower()
        if raw_variant in ("fast", "esmfold2_fast"):
            model_variant = "fast"
        elif raw_variant in ("esmfold2", "full"):
            model_variant = "full"
        else:
            return {
                "status": "error",
                "message": f"Invalid model_variant '{raw_variant}'. Supported: 'fast' or 'full'.",
            }

        raw_use_msa = arguments.get("use_msa")
        if model_variant == "fast":
            effective_use_msa = False
        else:
            effective_use_msa = True if raw_use_msa is None else bool(raw_use_msa)

        num_model_seeds = int(arguments.get("num_model_seeds", 1))
        num_diffusion_samples = int(arguments.get("num_diffusion_samples", 1))
        num_loops = int(arguments.get("num_loops", 4))
        num_sampling_steps = int(arguments.get("num_sampling_steps", 200))
        gpu_type = arguments.get("gpu_type") or "auto"
        enable_flex_start = arguments.get("enable_flex_start", True)

        base_seed = arguments.get("base_seed")
        if base_seed is None:
            base_seed = random.randint(0, 2**32 - 1)
        else:
            base_seed = int(base_seed)

        is_gcs = isinstance(input_data, str) and input_data.startswith("gs://")
        is_file = (
            os.path.isfile(input_data) if isinstance(input_data, str) and not is_gcs else False
        )

        if is_gcs:
            bucket_name, blob_path = self._validate_gcs_uri(input_data)
            bucket = self.storage_client.bucket(bucket_name)
            content = bucket.blob(blob_path).download_as_text()
        elif is_file and isinstance(input_data, str):
            validated_path = self._validate_local_file_path(input_data)
            with open(validated_path, encoding="utf-8") as f:
                content = f.read()
        else:
            content = input_data

        if isinstance(content, dict):
            content = json.dumps(content, indent=2)

        if not isinstance(content, str) or not content.strip():
            raise ValueError("No valid input content provided for ESMFold2 submission")

        if is_esmfold2_json(content):
            query_json_str = content
            ok, errors, warnings = validate_esmfold2_json(query_json_str)
            if not ok:
                return {
                    "status": "error",
                    "message": "Invalid ESMFold2 query JSON:\n"
                    + "\n".join(f"  - {e}" for e in errors),
                }
            for w in warnings:
                logger.warning(f"ESMFold2 JSON warning: {w}")
        else:
            query_json_str = fasta_to_esmfold2_json(content, job_name)

        num_tokens = count_tokens(query_json_str)

        temp_json = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix=f"esmfold2_{job_name}_"
        )
        temp_json.write(query_json_str)
        temp_json.close()

        pipeline_path = os.path.join(tempfile.gettempdir(), f"esmfold2_pipeline_{job_name}.yaml")
        try:
            gcs_query_path = f"gs://{self.config.bucket_name}/esmfold2_queries/{job_name}.json"
            self._upload_to_gcs(temp_json.name, gcs_query_path)

            filestore_ip, filestore_network = self._ensure_filestore_config()

            hardware_config = self._get_hardware_config(
                gpu_type,
                num_tokens=num_tokens,
                model_variant=model_variant,
            )

            resolved_gpu = hardware_config["predict_accel"]

            parsed_json = json.loads(query_json_str)
            seqs = parsed_json.get("sequences", [])
            num_chains = (
                sum(
                    len(s[next(iter(s.keys()))].get("id", ["?"]))
                    if isinstance(s[next(iter(s.keys()))].get("id"), list)
                    else 1
                    for s in seqs
                    if isinstance(s, dict) and s
                )
                or 1
            )

            query_name = job_name
            labels = {
                "model_type": "esmfold2",
                "model_variant": model_variant,
                "job_type": "monomer" if num_chains <= 1 else "complex",
                "query_name": self._clean_label(query_name),
                "num_tokens": str(num_tokens),
                "num_chains": str(num_chains),
                "num_seeds": str(num_model_seeds),
                "gpu_type": self.ACCEL_TO_LABEL.get(
                    resolved_gpu, gpu_type.lower().replace("_", "-")
                ),
                "msa_method": "jackhmmer" if effective_use_msa else "none",
                "submitted_by": "foldrun-agent",
            }

            pipeline_root = f"gs://{self.config.bucket_name}/pipeline_runs/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            rel_cache_path = self.config.get_cache_path(model_variant)

            job_kwargs = {
                "display_name": job_name,
                "template_path": pipeline_path,
                "pipeline_root": pipeline_root,
                "parameter_values": {
                    "query_json_path": gcs_query_path,
                    "project": self.config.project_id,
                    "region": self.config.region,
                    "nfs_cache_path": f"{self.config.nfs_mount_point}/{rel_cache_path}",
                    "num_model_seeds": num_model_seeds,
                    "num_diffusion_samples": num_diffusion_samples,
                    "num_loops": num_loops,
                    "num_sampling_steps": num_sampling_steps,
                    "base_seed": base_seed,
                },
                "enable_caching": True,
                "labels": labels,
            }

            from ..utils.pipeline_utils import load_vertex_pipeline

            pipeline_job = self._compile_and_submit_kfp_pipeline(
                pipeline_loader=lambda: load_vertex_pipeline(
                    enable_flex_start=enable_flex_start,
                    use_msa=effective_use_msa,
                ),
                package_path=pipeline_path,
                hardware_config=hardware_config,
                filestore_ip=filestore_ip,
                filestore_network=filestore_network,
                job_kwargs=job_kwargs,
                service_account=self.config.pipelines_sa_email,
                extra_temp_paths=[temp_json.name],
            )
        except Exception:
            for path in (pipeline_path, temp_json.name):
                if path and os.path.exists(path):
                    os.remove(path)
            raise

        mol_types: dict[str, int] = {}
        for seq in parsed_json.get("sequences", []):
            if not isinstance(seq, dict) or not seq:
                continue
            mt = next(iter(seq.keys()))
            cid = seq[mt].get("id") if isinstance(seq.get(mt), dict) else None
            count = len(cid) if isinstance(cid, list) and cid else 1
            mol_types[mt] = mol_types.get(mt, 0) + count

        msa_hw_desc = (
            f"{hardware_config['msa_machine']} (CPU-only, Jackhmmer protein MSA)"
            if effective_use_msa
            else "none (single-sequence ESMC backbone)"
        )

        return {
            "job_id": pipeline_job.resource_name,
            "job_name": job_name,
            "model_variant": model_variant,
            "use_msa": effective_use_msa,
            "status": "submitted",
            "console_url": f"https://console.cloud.google.com/vertex-ai/locations/{self.config.region}/pipelines/runs/{pipeline_job.resource_name.split('/')[-1]}?project={self.config.project_id}",
            "input_info": {
                "name": query_name,
                "model_variant": model_variant,
                "use_msa": effective_use_msa,
                "num_tokens": num_tokens,
                "num_chains": num_chains,
                "molecule_types": mol_types,
                "num_model_seeds": num_model_seeds,
                "num_diffusion_samples": num_diffusion_samples,
                "num_loops": num_loops,
                "num_sampling_steps": num_sampling_steps,
                "base_seed": base_seed,
            },
            "hardware": {
                "msa_pipeline": msa_hw_desc,
                "prediction": f"{hardware_config['predict_machine']} ({hardware_config['predict_accel']} x{hardware_config['predict_count']})",
                "scheduling": "FLEX_START (DWS)" if enable_flex_start else "ON_DEMAND",
            },
            "submitted_at": datetime.now().isoformat(),
            "pipeline_root": pipeline_root,
            "gcs_console_url": self.gcs_console_url(pipeline_root),
        }
