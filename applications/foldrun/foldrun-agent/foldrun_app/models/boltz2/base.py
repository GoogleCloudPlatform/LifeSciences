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
from typing import Any

from foldrun_app.core.kfp_tools import KFPModelTool

from .config import BOLTZ2Config

logger = logging.getLogger(__name__)


class BOLTZ2Tool(KFPModelTool):
    """Base class for Boltz2 tools following ToolUniverse pattern."""

    def __init__(
        self,
        tool_config: dict[str, Any] | None = None,
        config: BOLTZ2Config | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ):
        super().__init__(
            tool_config=tool_config,
            config=config or BOLTZ2Config(),
            name=name,
            description=description,
        )

    def _setup_compile_env(
        self,
        hardware_config: dict[str, Any],
        filestore_ip: str | None = None,
        filestore_network: str | None = None,
    ):
        """Set up environment variables for BOLTZ2 pipeline compilation."""
        env_vars = self._build_base_compile_env(
            hardware_config,
            filestore_ip=filestore_ip,
            filestore_network=filestore_network,
        )
        env_vars.update(
            {
                "BOLTZ2_COMPONENTS_IMAGE": self.config.base_image,
                "BOLTZ2_CACHE_PATH": self.config.cache_path,
            }
        )
        return self._scoped_compile_env(env_vars)

    @staticmethod
    def _recommend_gpu(num_tokens: int) -> str:
        """Recommend GPU type based on total input tokens.

        Auto-selection defaults to >=40GB A100 tiers:
            <=2000 tokens -> A100     (40 GB VRAM)
            >2000 tokens  -> A100_80GB (80 GB VRAM)

        L4 (g2-standard-12, 24 GB VRAM) is never auto-selected, though callers
        can request it explicitly via gpu_type='L4' for smaller targets.
        """
        if num_tokens > 2000:
            return "A100_80GB"
        return "A100"

    def _get_hardware_config(
        self,
        gpu_type: str,
        num_gpus: int = 1,
        num_tokens: int = 0,
    ) -> dict[str, Any]:
        """Get hardware configuration for BOLTZ2 pipeline."""
        gpu_type = self._normalize_gpu_type(gpu_type)

        if gpu_type == "auto":
            gpu_type = self._recommend_gpu(num_tokens)
            logger.info(f"Auto-selected GPU: {gpu_type} (num_tokens={num_tokens})")

        supported_types = list(self.GPU_SPECS.keys())
        if gpu_type not in supported_types:
            raise ValueError(
                f"Unsupported GPU type '{gpu_type}' for Boltz-2. Supported types: {supported_types}"
            )

        explicit_l4 = gpu_type == "L4"
        allowed_pool = (
            ["L4", "A100", "A100_80GB", "RTX_PRO_6000"]
            if explicit_l4
            else ["A100", "A100_80GB", "RTX_PRO_6000"]
        )
        gpu_type = self._resolve_supported_gpu(
            gpu_type,
            allowed_pool=allowed_pool,
            empty_pool_error=(
                f"No Boltz-2 compatible GPUs are supported in this environment! "
                f"(Supported: {self.config.supported_gpus}; >=40GB A100/A100_80GB/RTX_PRO_6000 required unless gpu_type='L4')"
            ),
        )

        if gpu_type == "L4" and num_tokens > 1000:
            logger.warning(
                f"Boltz-2 input has {num_tokens} tokens (>1000) on 24GB L4 VRAM "
                f"(--no_kernels --write_full_pde); high risk of GPU OOM during "
                f"diffusion after CPU MSA completes. Consider gpu_type='A100' or 'A100_80GB'."
            )

        config = self.predict_spec_for_gpu(gpu_type)
        self._scale_multi_gpu_machine(config, gpu_type, num_gpus)
        config["msa_machine"] = self._resolve_msa_machine()
        config["dws_max_wait_hours"] = self.config.dws_max_wait_hours
        return config
