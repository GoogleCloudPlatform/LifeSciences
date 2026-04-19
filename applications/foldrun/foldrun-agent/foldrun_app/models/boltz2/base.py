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

"""Base class for Boltz2 tools."""

import logging
import os
from typing import Any, Dict, Optional

from foldrun_app.core.base_tool import BaseTool

from .config import BOLTZ2Config

logger = logging.getLogger(__name__)


class BOLTZ2Tool(BaseTool):
    """Base class for Boltz2 tools following ToolUniverse pattern."""

    def __init__(self, tool_config: Dict[str, Any], config: Optional[BOLTZ2Config] = None):
        super().__init__(tool_config, config or BOLTZ2Config())

    def _setup_compile_env(
        self,
        hardware_config: Dict[str, Any],
        filestore_ip: str = None,
        filestore_network: str = None,
    ):
        """Set up environment variables for BOLTZ2 pipeline compilation."""
        nfs_server = filestore_ip or self.config.filestore_ip or ""
        network = filestore_network or self.config.filestore_network or ""

        env_vars = {
            "BOLTZ2_COMPONENTS_IMAGE": self.config.base_image,
            "NFS_SERVER": nfs_server,
            "NFS_PATH": self.config.nfs_share,
            "NFS_MOUNT_POINT": self.config.nfs_mount_point,
            "NETWORK": network,
            "BOLTZ2_CACHE_PATH": self.config.cache_path,
            "PREDICT_MACHINE_TYPE": hardware_config.get("predict_machine", "a2-highgpu-1g"),
            "PREDICT_ACCELERATOR_TYPE": hardware_config.get("predict_accel", "NVIDIA_TESLA_A100"),
            "PREDICT_ACCELERATOR_COUNT": str(hardware_config.get("predict_count", 1)),
            "DWS_MAX_WAIT_HOURS": str(hardware_config.get("dws_max_wait_hours", 168)),
        }

        os.environ.update(env_vars)

    @staticmethod
    def _recommend_gpu(num_tokens: int) -> str:
        """Recommend GPU type based on total input tokens.

        Boltz2 uses diffusion-based prediction — minimum A100 (40GB).

        Auto-selection tiers:
            <=2000 tokens  → A100     (40 GB VRAM)
            >2000 tokens   → A100_80GB (80 GB VRAM)

        No L4 tier — BOLTZ2 requires at least 40GB VRAM.

        Args:
            num_tokens: Total number of tokens (residues + nucleotides + ligand atoms)

        Returns:
            Recommended GPU type: 'A100' or 'A100_80GB'
        """
        if num_tokens > 2000:
            return "A100_80GB"
        return "A100"

    def _get_hardware_config(
        self,
        gpu_type: str,
        num_gpus: int = 1,
        num_tokens: int = 0,
    ) -> Dict[str, Any]:
        """Get hardware configuration for BOLTZ2 pipeline.

        No relax phase, no MMseqs2 GPU data pipeline — simpler than AF2.

        Args:
            gpu_type: GPU type for predict phase ('auto', 'A100', 'A100_80GB').
            num_gpus: Number of GPUs (1, 2, 4, 8).
            num_tokens: Total input tokens for auto GPU selection.

        Returns:
            Hardware configuration dictionary.
        """
        if gpu_type == "auto":
            gpu_type = self._recommend_gpu(num_tokens)
            logger.info(f"Auto-selected GPU: {gpu_type} (num_tokens={num_tokens})")

        configs = {
            "A100": {
                "predict_machine": "a2-highgpu-1g",
                "predict_accel": "NVIDIA_TESLA_A100",
                "predict_count": 1,
            },
            "A100_80GB": {
                "predict_machine": "a2-ultragpu-1g",
                "predict_accel": "NVIDIA_A100_80GB",
                "predict_count": 1,
            },
        }

        config = configs.get(gpu_type, configs["A100"]).copy()

        # Enforce supported GPUs logic
        supported = self.config.supported_gpus
        if gpu_type not in supported:
            original_type = gpu_type
            upgrade_path = ["A100", "A100_80GB"]

            try:
                start_idx = upgrade_path.index(original_type)
            except ValueError:
                start_idx = 0

            new_type = None
            for candidate in upgrade_path[start_idx:]:
                if candidate in supported:
                    new_type = candidate
                    break

            if new_type:
                logger.warning(
                    f"Requested GPU '{original_type}' not supported. Auto-upgrading to '{new_type}'."
                )
                gpu_type = new_type
                config = configs.get(gpu_type).copy()
            elif supported:
                fallback = supported[0]
                logger.warning(
                    f"Requested GPU '{original_type}' not supported. Falling back to '{fallback}'."
                )
                gpu_type = fallback
                config = configs.get(gpu_type, configs["A100"]).copy()
            else:
                raise ValueError(
                    f"No GPUs are supported in this environment! (Supported: {supported})"
                )

        # Multi-GPU machine types
        if num_gpus > 1 and gpu_type in ["A100", "A100_80GB"]:
            base = "a2-highgpu" if gpu_type == "A100" else "a2-ultragpu"
            config["predict_machine"] = f"{base}-{num_gpus}g"
            config["predict_count"] = num_gpus

        config["msa_machine"] = "c2-standard-16"
        config["dws_max_wait_hours"] = self.config.dws_max_wait_hours

        return config
