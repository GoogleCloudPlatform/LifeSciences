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

"""Base class for BindCraft tools."""

import logging
from typing import Any

from foldrun_app.core.base_tool import BaseTool

try:
    from foldrun_app.core.compile_lock import GLOBAL_COMPILE_LOCK, setup_compile_env_vars
except ImportError:
    from .utils.pipeline_utils import GLOBAL_COMPILE_LOCK, setup_compile_env_vars

from .config import BINDCRAFTConfig

logger = logging.getLogger(__name__)


class BINDCRAFTTool(BaseTool):
    """Base class for BindCraft tools following ToolUniverse pattern."""

    _compile_env_lock = GLOBAL_COMPILE_LOCK

    def __init__(self, tool_config: dict[str, Any], config: BINDCRAFTConfig | None = None):
        super().__init__(tool_config, config or BINDCRAFTConfig())

    def _setup_compile_env(
        self,
        hardware_config: dict[str, Any],
        filestore_ip: str | None = None,
        filestore_network: str | None = None,
    ):
        """Set up environment variables for BindCraft pipeline compilation."""
        nfs_server = filestore_ip or self.config.filestore_ip or ""
        network = filestore_network or self.config.filestore_network or ""

        env_vars = {
            "BINDCRAFT_COMPONENTS_IMAGE": self.config.base_image,
            "NFS_SERVER": nfs_server,
            "NFS_PATH": self.config.nfs_share,
            "NFS_MOUNT_POINT": self.config.nfs_mount_point,
            "NETWORK": network,
            "AF2_PARAMS_PATH": self.config.af2_params_path,
            "BINDCRAFT_MPNN_WEIGHTS_PATH": self.config.mpnn_weights_path,
            "PREDICT_MACHINE_TYPE": hardware_config.get("predict_machine", "a2-highgpu-1g"),
            "PREDICT_ACCELERATOR_TYPE": hardware_config.get("predict_accel", "NVIDIA_TESLA_A100"),
            "PREDICT_ACCELERATOR_COUNT": str(hardware_config.get("predict_count", 1)),
            "DWS_MAX_WAIT_HOURS": str(hardware_config.get("dws_max_wait_hours", 168)),
        }

        return setup_compile_env_vars(env_vars, lock=self._compile_env_lock)

    @staticmethod
    def _recommend_gpu(num_residues: int) -> str:
        """Recommend GPU type based on total input residues.

        BindCraft executes ColabDesign (AF2 backpropagation through structure module).
        Requires substantial VRAM: minimum A100 (40 GB). No L4 support.

        Auto-selection tiers:
            <=450 residues  → A100     (40 GB VRAM)
            >450 residues   → A100_80GB (80 GB VRAM)

        Args:
            num_residues: Total residues (target residues + binder length)

        Returns:
            Recommended GPU type: 'A100' or 'A100_80GB'
        """
        if num_residues > 450:
            return "A100_80GB"
        return "A100"

    def _get_hardware_config(
        self,
        gpu_type: str,
        num_gpus: int = 1,
        num_residues: int = 0,
    ) -> dict[str, Any]:
        """Get hardware configuration for BindCraft pipeline.

        Args:
            gpu_type: GPU type for predict phase ('auto', 'A100', 'A100_80GB', 'L4').
            num_gpus: Number of GPUs (1, 2, 4, 8).
            num_residues: Total input residues for auto GPU selection.

        Returns:
            Hardware configuration dictionary.
        """
        if gpu_type == "auto":
            gpu_type = self._recommend_gpu(num_residues)

        # Machine and accelerator mappings
        gpu_configs = {
            "L4": {
                "predict_machine": "g2-standard-12"
                if num_gpus == 1
                else f"g2-standard-{num_gpus * 12}",
                "predict_accel": "NVIDIA_L4",
                "predict_count": num_gpus,
            },
            "A100": {
                "predict_machine": "a2-highgpu-1g" if num_gpus == 1 else f"a2-highgpu-{num_gpus}g",
                "predict_accel": "NVIDIA_TESLA_A100",
                "predict_count": num_gpus,
            },
            "A100_80GB": {
                "predict_machine": "a2-ultragpu-1g"
                if num_gpus == 1
                else f"a2-ultragpu-{num_gpus}g",
                "predict_accel": "NVIDIA_A100_80GB",
                "predict_count": num_gpus,
            },
        }

        if gpu_type not in gpu_configs:
            raise ValueError(
                f"Unsupported GPU type for BindCraft: '{gpu_type}'. Must be 'auto', 'A100', 'A100_80GB', or 'L4'."
            )

        explicit_l4 = gpu_type == "L4"
        allowed_pool = ["L4", "A100", "A100_80GB"] if explicit_l4 else ["A100", "A100_80GB"]
        supported_list = getattr(self.config, "supported_gpus", None) or allowed_pool
        supported = [g for g in supported_list if g in allowed_pool]
        if supported and gpu_type not in supported:
            fallback = "A100_80GB" if "A100_80GB" in supported else supported[-1]
            logger.warning(
                f"Requested GPU '{gpu_type}' not in supported_gpus {supported_list}; selecting '{fallback}'."
            )
            gpu_type = fallback

        config = gpu_configs[gpu_type].copy()
        config["gpu_type"] = gpu_type
        config["dws_max_wait_hours"] = getattr(self.config, "dws_max_wait_hours", 168)
        return config
