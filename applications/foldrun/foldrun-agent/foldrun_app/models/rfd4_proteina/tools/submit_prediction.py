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

"""Tool for submitting RFD4-Proteina generative protein and binder design jobs."""

import json
import logging
import os
import random
import tempfile
from datetime import datetime
from typing import Any

from google.cloud import aiplatform as vertex_ai

from foldrun_app.core.pipeline_utils import compile_pipeline
from foldrun_app.core.target_resolver import (
    cleanup_staged_target,
    resolve_target_structure,
)

try:
    from foldrun_app.core.compile_lock import GLOBAL_COMPILE_LOCK
except ImportError:
    from ..utils.pipeline_utils import GLOBAL_COMPILE_LOCK

from ..base import RFD4ProteinaTool
from ..utils.input_converter import (
    build_rfd4_proteina_spec,
    count_tokens,
    validate_rfd4_proteina_spec,
)

logger = logging.getLogger(__name__)

_COMPILE_LOCK = GLOBAL_COMPILE_LOCK


class RFD4ProteinaSubmitDesignTool(RFD4ProteinaTool):
    """Tool for submitting RFD4-Proteina generative design jobs."""

    @staticmethod
    def _validate_local_file_path(file_path: str) -> str:
        """Validate that a local file path resides within an authorized directory."""
        allowed_dir = os.environ.get("FOLDRUN_ALLOWED_INPUT_DIR", "").strip()
        if not allowed_dir:
            raise ValueError(
                "Local file input is not allowed unless FOLDRUN_ALLOWED_INPUT_DIR is explicitly configured. "
                "Provide inline specification, RCSB PDB ID, or a gs:// URI instead."
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
        """Submit RFD4-Proteina design job.

        Args:
            arguments: {
                'input': Optional raw specification content or file path,
                'design_mode': 'binder_design' (default), 'motif_scaffolding', or 'unconditional_monomer',
                'target_structure': Optional RCSB PDB ID, GCS URI, or local file,
                'target_chains': Target chains to bind against (e.g. ['A']),
                'binding_residues': Hotspot residues (e.g. ['23', '24']),
                'scaffold_motif': Optional motif for motif_scaffolding,
                'binder_length_range': Length range (default '80..140'),
                'job_name': Optional job name,
                'num_model_seeds': Number of seeds (default: 5),
                'num_designs_per_seed': Designs per seed (default: 2),
                'gpu_type': 'auto', 'A100', 'A100_80GB', or 'L4',
                'enable_flex_start': Enable DWS FLEX_START (default: true),
            }

        Returns:
            Job submission details.
        """
        input_data = arguments.get("input")
        design_mode = arguments.get("design_mode", "binder_design")
        target_structure_input = arguments.get("target_structure")
        target_chains = arguments.get("target_chains") or ["A"]
        binding_residues = arguments.get("binding_residues")
        scaffold_motif = arguments.get("scaffold_motif")
        binder_length_range = arguments.get("binder_length_range", "80..140")

        job_name = arguments.get("job_name")
        num_model_seeds = int(arguments.get("num_model_seeds", 5))
        num_designs_per_seed = int(arguments.get("num_designs_per_seed", 2))
        gpu_type = arguments.get("gpu_type", "auto")
        num_gpus = int(arguments.get("num_gpus", 1))
        enable_flex_start = arguments.get("enable_flex_start", True)
        extra_cli_args = list(arguments.get("extra_cli_args") or [])
        custom_job_id = arguments.get("custom_job_id")
        pipeline_root_override = arguments.get("pipeline_root")
        extra_labels = arguments.get("extra_labels") or {}
        allow_local_files = bool(arguments.get("allow_local_files", True))
        compute_metrics = bool(arguments.get("compute_metrics", False))
        base_seed = arguments.get("base_seed")
        if base_seed is None:
            base_seed = random.randint(0, 2**31 - 1)

        if num_model_seeds < 1 or num_model_seeds > 50:
            raise ValueError(f"num_model_seeds must be between 1 and 50, got {num_model_seeds}")
        if num_designs_per_seed < 1 or num_designs_per_seed > 20:
            raise ValueError(
                f"num_designs_per_seed must be between 1 and 20, got {num_designs_per_seed}"
            )

        if not input_data and not target_structure_input and design_mode != "unconditional_monomer":
            return {
                "status": "error",
                "message": "Either 'target_structure' or 'input' must be provided.",
            }

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if not job_name:
            job_name = f"rfd4-proteina-{timestamp}"
        pipeline_job_name = f"{job_name}-{random.randint(100, 999)}"

        # Resolve target structure if provided
        target_gcs_uri = None
        target_local_cif = None
        staged_local_paths: list[str] = []
        pipeline_yaml_path: str | None = None

        try:
            if target_structure_input:
                if isinstance(
                    target_structure_input, str
                ) and not target_structure_input.startswith("gs://"):
                    import re as _re_ts

                    ts_stem = _re_ts.sub(
                        r"\.(cif|pdb|bcif)$",
                        "",
                        target_structure_input.strip(),
                        flags=_re_ts.IGNORECASE,
                    )
                    is_pdb_code = bool(_re_ts.match(r"^[0-9][A-Za-z0-9]{3}$", ts_stem))
                    if not is_pdb_code:
                        if not allow_local_files:
                            raise ValueError(
                                "Local file paths are disabled for remote submissions; provide a PDB ID or staged gs:// URI."
                            )
                        self._validate_local_file_path(target_structure_input)

                target_gcs_uri, target_local_cif = resolve_target_structure(
                    target_structure_input,
                    gcs_bucket=self.config.bucket_name,
                    job_name=pipeline_job_name,
                    prefer_format="cif",
                    storage_client=self.storage_client,
                    allow_local_files=allow_local_files,
                    restrict_to_bucket=(not allow_local_files),
                )
                staged_local_paths.append(target_local_cif)
                logger.info(f"Target structure resolved to GCS: {target_gcs_uri}")

            # Build specification dictionary
            spec_dict = None
            looks_like_local_path = (
                isinstance(input_data, str)
                and not input_data.startswith("gs://")
                and not input_data.lstrip().startswith(("data_", "{"))
                and "\n" not in input_data
                and (
                    os.path.exists(input_data)
                    or input_data.startswith(("/", "./", "../"))
                    or input_data.lower().endswith((".json", ".yaml", ".yml", ".cif", ".pdb"))
                )
            )
            if isinstance(input_data, str) and input_data.lstrip().startswith("data_"):
                from google.cloud import storage

                storage_client = self.storage_client or storage.Client(
                    project=self.config.project_id
                )
                bucket = storage_client.bucket(self.config.bucket_name)
                cif_blob_name = f"inputs/rfd4_proteina/{pipeline_job_name}_input.cif"
                blob = bucket.blob(cif_blob_name)
                blob.upload_from_string(input_data, content_type="text/plain")
                target_gcs_uri = f"gs://{self.config.bucket_name}/{cif_blob_name}"
                spec_dict = {"raw_input_cif": True, "input": f"{pipeline_job_name}_input.cif"}
            elif looks_like_local_path:
                if not allow_local_files:
                    raise ValueError("Local file input is disabled for remote submissions.")
                validated_path = self._validate_local_file_path(input_data)
                with open(validated_path) as f:
                    content = f.read()
                    try:
                        spec_dict = json.loads(content)
                    except Exception:
                        import yaml

                        spec_dict = yaml.safe_load(content)
            elif input_data and isinstance(input_data, str):
                try:
                    spec_dict = json.loads(input_data)
                except Exception:
                    import yaml

                    try:
                        loaded = yaml.safe_load(input_data)
                        if isinstance(loaded, dict):
                            spec_dict = loaded
                    except Exception:
                        spec_dict = None
            if spec_dict is None:
                target_fname = os.path.basename(target_gcs_uri) if target_gcs_uri else None
                spec_dict = build_rfd4_proteina_spec(
                    design_mode=design_mode,
                    target_structure_filename=target_fname,
                    target_chains=target_chains,
                    binding_residues=binding_residues,
                    scaffold_motif=scaffold_motif,
                    binder_length_range=binder_length_range,
                )

            # Resolve PDB IDs or gs:// URIs in target_structure or multi-task task.input
            if isinstance(spec_dict, dict) and not target_gcs_uri:
                import re

                raw_ts = str(spec_dict.get("target_structure", "")).strip()
                if raw_ts:
                    stem = re.sub(r"\.(cif|pdb|bcif)$", "", raw_ts, flags=re.IGNORECASE)
                    if raw_ts.startswith("gs://") or re.match(r"^[0-9][A-Za-z0-9]{3}$", stem):
                        resolve_arg = raw_ts if raw_ts.startswith("gs://") else stem
                        target_gcs_uri, target_local_cif = resolve_target_structure(
                            resolve_arg,
                            gcs_bucket=self.config.bucket_name,
                            job_name=pipeline_job_name,
                            prefer_format="cif",
                            storage_client=self.storage_client,
                            allow_local_files=allow_local_files,
                            restrict_to_bucket=(not allow_local_files),
                        )
                        staged_local_paths.append(target_local_cif)
                        spec_dict["target_structure"] = os.path.basename(target_local_cif)
                    elif not allow_local_files and (
                        raw_ts.startswith(("/", "./", "../")) or ".." in raw_ts.split("/")
                    ):
                        raise ValueError(f"Disallowed target_structure path '{raw_ts}'.")
                else:
                    resolved_cache: dict[str, tuple[str, str]] = {}
                    task_resolved_entries: list[tuple[dict[str, Any], str, str]] = []
                    for idx_task, task_cfg in enumerate(spec_dict.values()):
                        if isinstance(task_cfg, dict) and task_cfg.get("input"):
                            raw_in = str(task_cfg["input"]).strip()
                            stem = re.sub(r"\.(cif|pdb|bcif)$", "", raw_in, flags=re.IGNORECASE)
                            if raw_in.startswith("gs://") or re.match(
                                r"^[0-9][A-Za-z0-9]{3}$", stem
                            ):
                                resolve_arg = raw_in if raw_in.startswith("gs://") else stem
                                if resolve_arg not in resolved_cache:
                                    r_uri, r_local = resolve_target_structure(
                                        resolve_arg,
                                        gcs_bucket=self.config.bucket_name,
                                        job_name=f"{pipeline_job_name}-t{idx_task}",
                                        prefer_format="cif",
                                        storage_client=self.storage_client,
                                        allow_local_files=allow_local_files,
                                        restrict_to_bucket=(not allow_local_files),
                                    )
                                    staged_local_paths.append(r_local)
                                    resolved_cache[resolve_arg] = (r_uri, r_local)
                                r_uri, r_local = resolved_cache[resolve_arg]
                                task_resolved_entries.append((task_cfg, r_uri, r_local))
                            elif not allow_local_files and (
                                raw_in.startswith(("/", "./", "../")) or ".." in raw_in.split("/")
                            ):
                                raise ValueError(f"Disallowed task input path '{raw_in}'.")
                    if task_resolved_entries:
                        target_gcs_uri = task_resolved_entries[0][1]
                        target_local_cif = task_resolved_entries[0][2]
                        multi_distinct = len(resolved_cache) > 1
                        for task_cfg, r_uri, r_local in task_resolved_entries:
                            task_cfg["input"] = (
                                r_uri if multi_distinct else os.path.basename(r_local)
                            )

            is_native_rfd4 = isinstance(spec_dict, dict) and (
                bool(spec_dict.get("benchmark_input"))
                or bool(spec_dict.get("raw_input_cif"))
                or (
                    "design_mode" not in spec_dict
                    and any(
                        isinstance(v, dict) and ("contig" in v or "input" in v or "length" in v)
                        for v in spec_dict.values()
                    )
                )
            )
            if not is_native_rfd4:
                is_valid, errors = validate_rfd4_proteina_spec(spec_dict)
                if not is_valid:
                    raise ValueError(f"Invalid RFD4-Proteina specification: {'; '.join(errors)}")

            # Explicit fast-mode (~metrics) vs self-consistency metrics control
            for a in extra_cli_args:
                low_a = str(a).strip().lower()
                if low_a in ("--metrics", "compute_metrics=true", "metrics=true"):
                    compute_metrics = True
                elif low_a in (
                    "--fast-no-metrics",
                    "compute_metrics=false",
                    "metrics=false",
                    "~metrics",
                ):
                    compute_metrics = False
            if isinstance(spec_dict, dict):
                if "compute_metrics" in spec_dict:
                    compute_metrics = bool(spec_dict["compute_metrics"])
                else:
                    spec_dict["compute_metrics"] = compute_metrics
            metrics_mode = (
                "self_consistency_enabled" if compute_metrics else "fast_no_self_consistency"
            )

            spec_json_str = json.dumps(spec_dict, indent=2)

            # Get Filestore info
            if not self.config.filestore_ip:
                filestore_ip, filestore_network = self._get_filestore_info()
            else:
                filestore_ip = self.config.filestore_ip
                filestore_network = self.config.filestore_network

            # Hardware recommendation & selection
            num_tokens = count_tokens(spec_dict)
            hardware_config = self._get_hardware_config(
                gpu_type=gpu_type, num_gpus=num_gpus, num_tokens=num_tokens
            )
            logger.info(
                f"RFD4-Proteina hardware config for {num_tokens} tokens: {hardware_config['gpu_type']} "
                f"({hardware_config['predict_machine']})"
            )

            # Set up compile environment and compile pipeline
            from contextlib import nullcontext

            from ..utils.pipeline_utils import load_vertex_pipeline

            with _COMPILE_LOCK:
                env_ctx = self._setup_compile_env(hardware_config, filestore_ip, filestore_network)
                with env_ctx if env_ctx is not None else nullcontext():
                    pipeline_func = load_vertex_pipeline(enable_flex_start=enable_flex_start)
                    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
                        pipeline_yaml_path = tmp_file.name

                    try:
                        compile_pipeline(pipeline_func, pipeline_yaml_path)
                    except Exception as e:
                        if pipeline_yaml_path and os.path.exists(pipeline_yaml_path):
                            os.remove(pipeline_yaml_path)
                        raise RuntimeError(f"RFD4-Proteina pipeline compilation failed: {e}") from e

            # Upload spec JSON to GCS
            spec_gcs_path = (
                f"gs://{self.config.bucket_name}/inputs/rfd4_proteina/{pipeline_job_name}_spec.json"
            )
            from google.cloud import storage

            storage_client = self.storage_client or storage.Client(project=self.config.project_id)
            bucket = storage_client.bucket(self.config.bucket_name)
            blob = bucket.blob(f"inputs/rfd4_proteina/{pipeline_job_name}_spec.json")
            blob.upload_from_string(spec_json_str, content_type="application/json")
            logger.info(f"Uploaded spec to {spec_gcs_path}")

            # Pipeline parameters
            nfs_mount = self.config.nfs_mount_point.rstrip("/")
            nfs_cache_path = f"{nfs_mount}/{self.config.cache_path.lstrip('/')}"

            pipeline_parameters: dict[str, Any] = {
                "spec_json_path": spec_gcs_path,
                "project": self.config.project_id,
                "region": self.config.region,
                "nfs_cache_path": nfs_cache_path,
                "design_mode": design_mode,
                "target_structure_path": target_gcs_uri or spec_gcs_path,
                "num_model_seeds": num_model_seeds,
                "num_designs_per_seed": num_designs_per_seed,
                "base_seed": base_seed,
            }
            if extra_cli_args:
                pipeline_parameters["extra_cli_args"] = [str(a) for a in extra_cli_args]

            pipeline_root = (
                pipeline_root_override
                or f"gs://{self.config.bucket_name}/pipeline_runs/{pipeline_job_name}"
            )

            resolved_gpu = hardware_config["predict_accel"]
            accel_to_label = {
                "NVIDIA_TESLA_A100": "a100",
                "NVIDIA_A100_80GB": "a100-80gb",
                "NVIDIA_L4": "l4",
            }
            labels = {
                "model_type": "rfd4-proteina",
                "job_type": self._clean_label(design_mode),
                "seq_name": self._clean_label(job_name),
                "query_name": self._clean_label(job_name),
                "num_tokens": str(num_tokens),
                "num_seeds": str(num_model_seeds),
                "num_designs": str(num_model_seeds * num_designs_per_seed),
                "gpu_type": accel_to_label.get(resolved_gpu, gpu_type.lower().replace("_", "-")),
                "metrics_mode": "self-consistency" if compute_metrics else "fast-no-metrics",
                "submitted_by": "foldrun-agent",
            }
            for k, v in extra_labels.items():
                labels[str(k)] = self._clean_label(str(v))

            # Initialize Vertex AI
            vertex_ai.init(
                project=self.config.project_id,
                location=self.config.region,
                staging_bucket=f"gs://{self.config.bucket_name}/staging",
            )

            job_kwargs: dict[str, Any] = {
                "display_name": pipeline_job_name,
                "template_path": pipeline_yaml_path,
                "pipeline_root": pipeline_root,
                "parameter_values": pipeline_parameters,
                "enable_caching": False,
                "labels": labels,
            }
            if custom_job_id:
                job_kwargs["job_id"] = custom_job_id

            job = vertex_ai.PipelineJob(**job_kwargs)

            job.submit(
                network=filestore_network,
                service_account=self.config.pipelines_sa_email,
            )

            res_name = getattr(job, "resource_name", None) or getattr(
                job, "name", pipeline_job_name
            )
            dashboard_url = (
                f"https://console.cloud.google.com/vertex-ai/locations/{self.config.region}/"
                f"pipelines/runs/{res_name.split('/')[-1]}?project={self.config.project_id}"
            )

            total_candidates = num_model_seeds * num_designs_per_seed

            return {
                "status": "SUBMITTED",
                "job_name": pipeline_job_name,
                "job_id": res_name,
                "resource_name": res_name,
                "dashboard_url": dashboard_url,
                "pipeline_root": pipeline_root,
                "hardware_config": {
                    "gpu_type": hardware_config["gpu_type"],
                    "predict_machine": hardware_config["predict_machine"],
                    "predict_accel": hardware_config["predict_accel"],
                    "num_gpus": hardware_config["predict_count"],
                    "estimated_tokens": num_tokens,
                },
                "design_config": {
                    "design_mode": design_mode,
                    "num_model_seeds": num_model_seeds,
                    "num_designs_per_seed": num_designs_per_seed,
                    "total_candidate_designs": total_candidates,
                    "binder_length_range": binder_length_range,
                    "target_structure": target_structure_input or "N/A",
                    "enable_flex_start": enable_flex_start,
                    "compute_metrics": compute_metrics,
                    "metrics_mode": metrics_mode,
                },
                "spec_gcs_path": spec_gcs_path,
            }
        finally:
            for p in staged_local_paths:
                cleanup_staged_target(p)
            if pipeline_yaml_path and os.path.exists(pipeline_yaml_path):
                try:
                    os.remove(pipeline_yaml_path)
                except OSError:
                    pass
