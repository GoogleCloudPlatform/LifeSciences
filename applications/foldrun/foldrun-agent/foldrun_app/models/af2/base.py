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

"""Base class for AlphaFold 2 tools."""

import logging
import os
from typing import Any, Dict, Optional

from foldrun_app.core.base_tool import BaseTool

from .config import Config

logger = logging.getLogger(__name__)


class AF2Tool(BaseTool):
    """Base class for AlphaFold2 tools following ToolUniverse pattern."""

    def __init__(self, tool_config: Dict[str, Any], config: Optional[Config] = None):
        """
        Initialize AF2 tool with configuration.

        Args:
            tool_config: Tool configuration from alphafold_tools.json
            config: Global configuration object (optional)
        """
        super().__init__(tool_config, config or Config())

    def _setup_compile_env(
        self,
        hardware_config: Dict[str, Any],
        filestore_ip: str = None,
    ):
        """
        Set up environment variables for pipeline compilation.

        Args:
            hardware_config: Hardware configuration dictionary
            filestore_ip: Filestore IP address (optional, uses config if not provided)
        """
        # Use provided values or fall back to config
        nfs_server = filestore_ip or self.config.filestore_ip or ""
        env_vars = {
            "ALPHAFOLD_COMPONENTS_IMAGE": self.config.base_image,
            "NFS_SERVER": nfs_server,
            "NFS_PATH": self.config.nfs_share,
            "NFS_MOUNT_POINT": self.config.nfs_mount_point,
            "MODEL_PARAMS_GCS_LOCATION": f"gs://{self.config.databases_bucket_name}/alphafold2",
            "DATA_PIPELINE_MACHINE_TYPE": hardware_config.get("data_pipeline", "c2-standard-16"),
            "PREDICT_MACHINE_TYPE": hardware_config.get("predict_machine", "g2-standard-12"),
            "PREDICT_ACCELERATOR_TYPE": hardware_config.get("predict_accel", "NVIDIA_L4"),
            "PREDICT_ACCELERATOR_COUNT": str(hardware_config.get("predict_count", 1)),
            "RELAX_MACHINE_TYPE": hardware_config.get("relax_machine", "g2-standard-12"),
            "RELAX_ACCELERATOR_TYPE": hardware_config.get("relax_accel", "NVIDIA_L4"),
            "RELAX_ACCELERATOR_COUNT": str(hardware_config.get("relax_count", 1)),
            "PARALLELISM": str(hardware_config.get("parallelism", 5)),
            "DWS_MAX_WAIT_HOURS": str(hardware_config.get("dws_max_wait_hours", 168)),
        }

        # MMseqs2 GPU data pipeline overrides
        if hardware_config.get("msa_method") == "mmseqs2":
            env_vars.update(
                {
                    "MMSEQS2_DATA_PIPELINE_MACHINE_TYPE": hardware_config.get(
                        "dp_machine", "g2-standard-12"
                    ),
                    "MMSEQS2_ACCELERATOR_TYPE": hardware_config.get("dp_accel", "NVIDIA_L4"),
                    "MMSEQS2_ACCELERATOR_COUNT": str(hardware_config.get("dp_accel_count", 1)),
                }
            )

        os.environ.update(env_vars)

    @staticmethod
    def _recommend_gpu(seq_length: int, is_multimer: bool = False) -> str:
        """Recommend GPU type based on sequence length and job type.

        GPU memory is the primary constraint for AlphaFold2 — longer sequences
        produce larger attention matrices that must fit in VRAM. Multimers need
        more memory than monomers at the same total residue count because of
        cross-chain attention.

        Auto-selection tiers (gpu_type='auto'):

            Monomer:
                <=1500 residues → A100     (40 GB VRAM)   ~$2-5/pred
                >1500           → A100_80GB (80 GB VRAM)  ~$5-15/pred

            Multimer (total residues across all chains):
                <1000           → A100     (40 GB VRAM)
                >=1000          → A100_80GB (80 GB VRAM)

        L4 is no longer auto-selected — it provisions slowly under DWS FLEX_START
        and A100 capacity is typically deeper. Users can still request L4 explicitly
        with gpu_type='L4' but should expect longer queue times.

        Relax GPU matches the predict tier (AMBER runs on the same machine):
            A100 predict      → A100 relax
            A100_80GB predict → A100 relax

        Users can always override by passing an explicit gpu_type value
        ('L4', 'A100', 'A100_80GB') instead of 'auto'.

        Args:
            seq_length: Total sequence length (sum of all chains for multimers)
            is_multimer: Whether this is a multimer prediction

        Returns:
            Recommended GPU type: 'L4', 'A100', or 'A100_80GB'
        """
        if is_multimer:
            if seq_length >= 1000:
                return "A100_80GB"
            return "A100"
        else:
            if seq_length > 1500:
                return "A100_80GB"
            return "A100"  # L4 no longer auto-selected; A100 provisions faster via DWS

    def _get_hardware_config(
        self,
        gpu_type: str,
        num_gpus: int = 1,
        relax_gpu_type: Optional[str] = None,
        msa_method: str = "auto",
        use_small_bfd: bool = True,
        seq_length: int = 0,
        is_multimer: bool = False,
    ) -> Dict[str, Any]:
        """
        Get hardware configuration based on GPU type.

        Three defaults are auto-selected when callers use 'auto':

        1. **gpu_type='auto'** — Predict/relax GPU selected by sequence length
           and multimer status. See ``_recommend_gpu()`` for tier thresholds.
           Explicit values ('L4', 'A100', 'A100_80GB') bypass auto-selection.

        2. **msa_method='auto'** — MSA search method selected by database config:
             use_small_bfd=True  → mmseqs2  (GPU-accelerated, L4, ~177x faster)
             use_small_bfd=False → jackhmmer (CPU-only, required for full BFD /
                                              UniRef30 HHsuite profile DBs)
           Explicit 'mmseqs2' with use_small_bfd=False is rejected at the
           submit_* layer (validation error before reaching this method).

        3. **DWS FLEX_START** — Enabled by default at the pipeline layer
           (``enable_flex_start=True`` in submit_monomer / submit_multimer).
           Jobs queue via Dynamic Workload Scheduler when GPUs are unavailable,
           using spot/preemptible pricing. Users can disable with
           ``enable_flex_start=False`` for immediate-or-fail provisioning.

           Configurable via .env:
             DWS_MAX_WAIT_HOURS  — per-task GPU wait timeout (default: 168h / 7 days)
             AF2_PARALLELISM     — max concurrent predict/relax tasks (default: 5)

           Note: timeout applies per GPU task, not per pipeline. A monomer
           pipeline has ~10 GPU tasks (5 predict + 5 relax), a multimer up
           to ~50. See pipeline comments for details.

        After auto-selection, the existing supported-GPU upgrade path still
        applies (e.g., if L4 quota is 0, auto-upgrades to A100).

        Args:
            gpu_type: GPU type for predict phase ('auto', 'L4', 'A100', 'A100_80GB').
                      'auto' selects based on seq_length and is_multimer.
            num_gpus: Number of GPUs (1, 2, 4, 8)
            relax_gpu_type: Optional GPU type for relax phase. If None, uses
                           cost-optimized default (downgraded from predict GPU).
            msa_method: MSA generation method ('auto', 'mmseqs2', 'jackhmmer').
                        'auto' selects based on use_small_bfd.
            use_small_bfd: Whether small BFD is used (determines auto msa_method).
            seq_length: Total sequence length for auto GPU selection.
            is_multimer: Whether this is a multimer job (affects auto GPU tiers).

        Returns:
            Hardware configuration dictionary
        """
        # Auto-select GPU based on sequence complexity
        if gpu_type == "auto":
            gpu_type = self._recommend_gpu(seq_length, is_multimer)
            logger.info(
                f"Auto-selected GPU: {gpu_type} (seq_length={seq_length}, multimer={is_multimer})"
            )

        # Auto-select MSA method — default to jackhmmer (CPU, works with raw FASTA DBs).
        # MMseqs2 requires a separate GPU index conversion step and must be explicitly requested.
        if msa_method == "auto":
            msa_method = "jackhmmer"
            logger.info(
                "Auto-selected MSA method: jackhmmer (default; use msa_method='mmseqs2' for GPU-accelerated search)"
            )

        # Base configurations - optimized for cost/performance
        # Relaxation uses smaller GPUs since AMBER is less demanding than prediction
        configs = {
            "L4": {
                "predict_machine": "g2-standard-12",
                "predict_accel": "NVIDIA_L4",
                "predict_count": 1,
                "relax_machine": "g2-standard-12",
                "relax_accel": "NVIDIA_L4",
                "relax_count": 1,
            },
            "A100": {
                "predict_machine": "a2-highgpu-1g",
                "predict_accel": "NVIDIA_TESLA_A100",
                "predict_count": 1,
                "relax_machine": "a2-highgpu-1g",  # Keep A100 — L4 is slow to provision
                "relax_accel": "NVIDIA_TESLA_A100",
                "relax_count": 1,
            },
            "A100_80GB": {
                "predict_machine": "a2-ultragpu-1g",
                "predict_accel": "NVIDIA_A100_80GB",
                "predict_count": 1,
                "relax_machine": "a2-highgpu-1g",  # Downgrade to A100 40GB for cost savings
                "relax_accel": "NVIDIA_TESLA_A100",
                "relax_count": 1,
            },
        }

        config = configs.get(gpu_type, configs["L4"]).copy()

        # Enforce Supported GPUs Logic
        supported = self.config.supported_gpus

        # If requested GPU is not supported, try to auto-upgrade
        if gpu_type not in supported:
            original_type = gpu_type

            # Defining the upgrade path: L4 -> A100 -> A100_80GB
            upgrade_path = ["L4", "A100", "A100_80GB"]

            try:
                start_idx = upgrade_path.index(original_type)
            except ValueError:
                # If unknown type, start from beginning
                start_idx = 0

            new_type = None
            for candidate in upgrade_path[start_idx:]:
                if candidate in supported:
                    new_type = candidate
                    break

            if new_type:
                logger.warning(
                    f"Requested GPU '{original_type}' is not supported in this environment. Auto-upgrading to '{new_type}'."
                )
                gpu_type = new_type
                config = configs.get(gpu_type).copy()
            else:
                # No valid upgrade path found
                # Fallback: Just pick the first supported one if available, or raise error
                if supported:
                    fallback = supported[0]
                    logger.warning(
                        f"Requested GPU '{original_type}' not supported and no logical upgrade path. Falling back to '{fallback}'."
                    )
                    gpu_type = fallback
                    config = configs.get(gpu_type).copy()
                else:
                    raise ValueError(
                        f"No GPUs are supported in this environment! (Supported: {supported})"
                    )

        # Adjust machine type for multiple GPUs
        if num_gpus > 1 and gpu_type in ["A100", "A100_80GB"]:
            base = "a2-highgpu" if gpu_type == "A100" else "a2-ultragpu"
            config["predict_machine"] = f"{base}-{num_gpus}g"
            config["predict_count"] = num_gpus

        # Override relax GPU if explicitly requested
        if relax_gpu_type:
            relax_configs = {
                "L4": {
                    "relax_machine": "g2-standard-12",
                    "relax_accel": "NVIDIA_L4",
                    "relax_count": 1,
                },
                "A100": {
                    "relax_machine": "a2-highgpu-1g",
                    "relax_accel": "NVIDIA_TESLA_A100",
                    "relax_count": 1,
                },
                "A100_80GB": {
                    "relax_machine": "a2-ultragpu-1g",
                    "relax_accel": "NVIDIA_A100_80GB",
                    "relax_count": 1,
                },
            }
            if relax_gpu_type in relax_configs:
                config.update(relax_configs[relax_gpu_type])
                logger.info(f"Relax GPU overridden to {relax_gpu_type}")
            else:
                logger.warning(f"Unknown relax_gpu_type '{relax_gpu_type}', using default")

        config["data_pipeline"] = "c2-standard-16"
        config["parallelism"] = self.config.parallelism
        config["dws_max_wait_hours"] = self.config.dws_max_wait_hours
        config["msa_method"] = msa_method

        # MMseqs2 GPU data pipeline hardware
        if msa_method == "mmseqs2":
            config["dp_machine"] = "g2-standard-12"
            config["dp_accel"] = "NVIDIA_L4"
            config["dp_accel_count"] = 1

        return config
