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

"""Tool for submitting BoltzGen binder design and redesign jobs."""

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
    map_auth_to_label_seq_id,
    resolve_target_structure,
)

try:
    from foldrun_app.core.compile_lock import GLOBAL_COMPILE_LOCK
except ImportError:
    from ..utils.pipeline_utils import GLOBAL_COMPILE_LOCK

from ..base import BOLTZGENTool
from ..utils.input_converter import (
    build_boltzgen_yaml,
    count_tokens,
    is_boltzgen_yaml,
    validate_boltzgen_yaml,
)

logger = logging.getLogger(__name__)

_COMPILE_LOCK = GLOBAL_COMPILE_LOCK


class BOLTZGENSubmitDesignTool(BOLTZGENTool):
    """Tool for submitting BoltzGen binder design and redesign jobs."""

    @staticmethod
    def _validate_local_file_path(file_path: str) -> str:
        """Validate that a local file path resides within an authorized directory."""
        allowed_dir = os.environ.get("FOLDRUN_ALLOWED_INPUT_DIR", "").strip()
        if not allowed_dir:
            raise ValueError(
                "Local file input is not allowed unless FOLDRUN_ALLOWED_INPUT_DIR is explicitly configured. "
                "Provide inline YAML content, RCSB PDB ID, or a gs:// URI instead."
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
        """Submit BOLTZGEN design job.

        Args:
            arguments: {
                'input': Optional raw YAML content or file path,
                'target_structure': Optional RCSB PDB ID, GCS URI, or local file,
                'target_chains': Target chains to bind against (e.g. ['A']),
                'binding_residues': Hotspot residues (e.g. ['23', '24']),
                'binder_length_range': Length range (default '80..140'),
                'protocol': BoltzGen protocol (default 'protein-anything'),
                'ligand_smiles': Optional SMILES,
                'ligand_ccd': Optional CCD code,
                'job_name': Optional job name,
                'num_model_seeds': Number of seeds (default: 5),
                'num_designs_per_seed': Designs per seed (default: 2),
                'budget_per_seed': Budget per seed (default: 2),
                'gpu_type': 'auto', 'A100', or 'A100_80GB',
                'enable_flex_start': Enable DWS FLEX_START (default: true),
            }

        Returns:
            Job submission details.
        """
        input_data = arguments.get("input")
        target_structure_input = arguments.get("target_structure")
        target_chains = arguments.get("target_chains") or ["A"]
        binding_residues = arguments.get("binding_residues")
        binder_length_range = arguments.get("binder_length_range", "80..140")
        protocol = arguments.get("protocol", "protein-anything")
        ligand_smiles = arguments.get("ligand_smiles")
        ligand_ccd = arguments.get("ligand_ccd")

        job_name = arguments.get("job_name", f"boltzgen_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        num_model_seeds = arguments.get("num_model_seeds", 5)
        num_designs_per_seed = arguments.get("num_designs_per_seed", 2)
        budget_per_seed = arguments.get("budget_per_seed", 2)
        gpu_type = arguments.get("gpu_type", "auto")
        enable_flex_start = arguments.get("enable_flex_start", True)
        extra_cli_args = arguments.get("extra_cli_args") or []
        custom_job_id = arguments.get("custom_job_id")
        pipeline_root_override = arguments.get("pipeline_root")
        extra_labels = arguments.get("extra_labels") or {}
        allow_local_files = bool(arguments.get("allow_local_files", True))

        base_seed = arguments.get("base_seed")
        if base_seed is None:
            base_seed = random.randint(0, 2**32 - 1)

        target_gcs_uri = None
        target_filename = "target.cif"
        staged_local_paths: list[str] = []
        temp_yaml_path: str | None = None
        pipeline_path: str | None = None

        try:
            # Check if structured target structure input is supplied
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

                target_gcs_uri, local_target_path = resolve_target_structure(
                    target_structure_input,
                    gcs_bucket=self.config.bucket_name,
                    job_name=job_name,
                    prefer_format="cif",
                    storage_client=self.storage_client,
                    allow_local_files=allow_local_files,
                    restrict_to_bucket=(not allow_local_files),
                )
                staged_local_paths.append(local_target_path)
                target_filename = os.path.basename(local_target_path)

                # Map hotspot residues to label_seq_id if target_chains and binding_residues given
                if binding_residues and os.path.exists(local_target_path):
                    primary_chain = target_chains[0] if target_chains else "A"
                    mapped_residues = map_auth_to_label_seq_id(
                        local_target_path, primary_chain, binding_residues
                    )
                    if mapped_residues:
                        binding_residues = mapped_residues

                query_yaml = build_boltzgen_yaml(
                    target_structure_filename=target_filename,
                    target_chains=target_chains,
                    binding_residues=binding_residues,
                    binder_length_range=binder_length_range,
                    protocol=protocol,
                    ligand_smiles=ligand_smiles,
                    ligand_ccd=ligand_ccd,
                )
            elif input_data:
                # Determine input type and load content
                is_gcs = isinstance(input_data, str) and input_data.startswith("gs://")
                looks_like_path = (
                    isinstance(input_data, str)
                    and not is_gcs
                    and "\n" not in input_data
                    and (
                        os.path.isfile(input_data)
                        or input_data.startswith(("/", "./", "../"))
                        or input_data.lower().endswith((".yaml", ".yml", ".json"))
                    )
                )

                if is_gcs:
                    parts = input_data[5:].split("/", 1)
                    bucket_name = parts[0]
                    blob_path = parts[1] if len(parts) > 1 else ""
                    if not allow_local_files and bucket_name != self.config.bucket_name:
                        raise ValueError(
                            f"Cross-bucket GCS URI '{input_data}' is not permitted; must use gs://{self.config.bucket_name}/..."
                        )
                    bucket = self.storage_client.bucket(bucket_name)
                    content = bucket.blob(blob_path).download_as_text()
                elif looks_like_path:
                    if not allow_local_files:
                        raise ValueError("Local file input is disabled for remote submissions.")
                    validated_path = self._validate_local_file_path(input_data)
                    with open(validated_path) as f:
                        content = f.read()
                else:
                    content = input_data

                if is_boltzgen_yaml(content):
                    query_yaml = content
                    ok, errors = validate_boltzgen_yaml(query_yaml)
                    if not ok:
                        return {
                            "status": "error",
                            "message": "Invalid BoltzGen query YAML:\n"
                            + "\n".join(f"  - {e}" for e in errors),
                        }

                    # Check if YAML entities reference a PDB ID or gs:// URI in file.path
                    import re

                    import yaml

                    parsed_yaml = yaml.safe_load(query_yaml)
                    if isinstance(parsed_yaml, dict) and isinstance(
                        parsed_yaml.get("entities"), list
                    ):
                        yaml_modified = False
                        for ent in parsed_yaml["entities"]:
                            if isinstance(ent, dict) and isinstance(ent.get("file"), dict):
                                fpath = str(ent["file"].get("path", "")).strip()
                                if not fpath:
                                    continue
                                stem = re.sub(r"\.(cif|pdb|bcif)$", "", fpath, flags=re.IGNORECASE)
                                if fpath.startswith("gs://") or re.match(
                                    r"^[0-9][A-Za-z0-9]{3}$", stem
                                ):
                                    resolve_arg = fpath if fpath.startswith("gs://") else stem
                                    target_gcs_uri, local_target_path = resolve_target_structure(
                                        resolve_arg,
                                        gcs_bucket=self.config.bucket_name,
                                        job_name=job_name,
                                        prefer_format="cif",
                                        storage_client=self.storage_client,
                                        allow_local_files=allow_local_files,
                                        restrict_to_bucket=(not allow_local_files),
                                    )
                                    staged_local_paths.append(local_target_path)
                                    ent["file"]["path"] = os.path.basename(local_target_path)
                                    yaml_modified = True
                                    # Map auth_seq_id to label_seq_id in binding_types if present
                                    btypes = ent["file"].get("binding_types")
                                    if isinstance(btypes, list) and os.path.exists(
                                        local_target_path
                                    ):
                                        for bt in btypes:
                                            if isinstance(bt, dict) and isinstance(
                                                bt.get("chain"), dict
                                            ):
                                                ch_id = str(bt["chain"].get("id", "A"))
                                                bval = bt["chain"].get("binding")
                                                if bval is not None:
                                                    raw_tokens = [
                                                        t.strip()
                                                        for t in str(bval).split(",")
                                                        if t.strip()
                                                    ]
                                                    mapped_tokens = map_auth_to_label_seq_id(
                                                        local_target_path, ch_id, raw_tokens
                                                    )
                                                    if mapped_tokens:
                                                        bt["chain"]["binding"] = ",".join(
                                                            str(t) for t in mapped_tokens
                                                        )
                                elif not allow_local_files and (
                                    fpath.startswith(("/", "./", "../")) or ".." in fpath.split("/")
                                ):
                                    raise ValueError(
                                        f"Disallowed file.path '{fpath}' in BoltzGen YAML entity."
                                    )
                        if yaml_modified:
                            query_yaml = yaml.safe_dump(parsed_yaml, sort_keys=False)
                else:
                    return {
                        "status": "error",
                        "message": "Input is neither a valid BoltzGen YAML nor was a target_structure provided.",
                    }
            else:
                return {
                    "status": "error",
                    "message": "Either 'target_structure' or 'input' must be provided for BoltzGen design.",
                }

            # Count tokens for GPU recommendation
            num_tokens = count_tokens(query_yaml)

            from foldrun_app.core.target_resolver import _sanitize_job_segment

            safe_job_name = _sanitize_job_segment(job_name)
            # Write query YAML to temp file and upload to GCS
            temp_yaml = tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, prefix=f"boltzgen_{safe_job_name}_"
            )
            temp_yaml_path = temp_yaml.name
            temp_yaml.write(query_yaml)
            temp_yaml.close()

            gcs_query_path = f"gs://{self.config.bucket_name}/boltzgen_queries/{safe_job_name}.yaml"
            self._upload_to_gcs(temp_yaml_path, gcs_query_path)

            # Get Filestore info
            if not self.config.filestore_ip:
                filestore_ip, filestore_network = self._get_filestore_info()
            else:
                filestore_ip = self.config.filestore_ip
                filestore_network = self.config.filestore_network

            # Hardware configuration
            hardware_config = self._get_hardware_config(
                gpu_type,
                num_tokens=num_tokens,
            )

            resolved_gpu = hardware_config["predict_accel"]
            accel_to_label = {
                "NVIDIA_L4": "l4",
                "NVIDIA_TESLA_A100": "a100",
                "NVIDIA_A100_80GB": "a100-80gb",
            }

            with tempfile.NamedTemporaryFile(
                suffix=".yaml", delete=False, prefix=f"boltzgen_pipeline_{safe_job_name}_"
            ) as tmp_pipe:
                pipeline_path = tmp_pipe.name

            from contextlib import nullcontext

            with _COMPILE_LOCK:
                env_ctx = self._setup_compile_env(hardware_config, filestore_ip, filestore_network)
                with env_ctx if env_ctx is not None else nullcontext():
                    from ..utils.pipeline_utils import load_vertex_pipeline

                    pipeline = load_vertex_pipeline(enable_flex_start=enable_flex_start)
                    compile_pipeline(pipeline, pipeline_path)

            labels = {
                "model_type": "boltzgen",
                "job_type": "binder_design",
                "protocol": self._clean_label(protocol),
                "seq_name": self._clean_label(job_name),
                "query_name": self._clean_label(job_name),
                "num_tokens": str(num_tokens),
                "num_seeds": str(num_model_seeds),
                "num_designs": str(num_model_seeds * num_designs_per_seed),
                "gpu_type": accel_to_label.get(resolved_gpu, gpu_type.lower().replace("_", "-")),
                "submitted_by": "foldrun-agent",
            }
            for k, v in extra_labels.items():
                labels[str(k)] = self._clean_label(str(v))

            pipeline_root = (
                pipeline_root_override
                or f"gs://{self.config.bucket_name}/pipeline_runs/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

            parameter_values: dict[str, Any] = {
                "query_yaml_path": gcs_query_path,
                "project": self.config.project_id,
                "region": self.config.region,
                "nfs_cache_path": f"{self.config.nfs_mount_point}/{self.config.cache_path}",
                "protocol": protocol,
                "target_structure_path": target_gcs_uri or gcs_query_path,
                "num_model_seeds": num_model_seeds,
                "num_designs_per_seed": num_designs_per_seed,
                "budget_per_seed": budget_per_seed,
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
                network=filestore_network,
                service_account=self.config.pipelines_sa_email,
            )

            return {
                "status": "submitted",
                "model_type": "boltzgen",
                "job_name": job_name,
                "job_id": pipeline_job.name,
                "pipeline_state": str(pipeline_job.state),
                "gcs_output_directory": pipeline_root,
                "query_yaml_uri": gcs_query_path,
                "target_gcs_uri": target_gcs_uri,
                "protocol": protocol,
                "num_model_seeds": num_model_seeds,
                "num_designs_per_seed": num_designs_per_seed,
                "budget_per_seed": budget_per_seed,
                "total_candidate_designs": num_model_seeds * num_designs_per_seed,
                "hardware_config": hardware_config,
            }
        finally:
            for p in staged_local_paths:
                cleanup_staged_target(p)
            for tmp_f in (temp_yaml_path, pipeline_path):
                if tmp_f and os.path.exists(tmp_f):
                    try:
                        os.remove(tmp_f)
                    except OSError:
                        pass
