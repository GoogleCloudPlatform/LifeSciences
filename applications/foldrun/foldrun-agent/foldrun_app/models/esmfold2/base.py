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

"""Base class for ESMFold2 / ESMFold2-Fast tools."""

import logging
import os
from typing import Any

from foldrun_app.core.kfp_tools import KFPModelTool

from .config import ESMFOLD2Config

logger = logging.getLogger(__name__)


class ESMFOLD2Tool(KFPModelTool):
    """Base class for ESMFold2 tools following ToolUniverse pattern."""

    def __init__(
        self,
        tool_config: dict[str, Any] | None = None,
        config: ESMFOLD2Config | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ):
        super().__init__(
            tool_config=tool_config,
            config=config or ESMFOLD2Config(),
            name=name,
            description=description,
        )

    def _setup_compile_env(
        self,
        hardware_config: dict[str, Any],
        filestore_ip: str | None = None,
        filestore_network: str | None = None,
    ):
        """Set up environment variables for ESMFold2 pipeline compilation."""
        env_vars = self._build_base_compile_env(
            hardware_config,
            filestore_ip=filestore_ip,
            filestore_network=filestore_network,
        )
        env_vars.update(
            {
                "ESMFOLD2_COMPONENTS_IMAGE": self.config.base_image,
                "ESMFOLD2_FAST_CACHE_PATH": self.config.fast_cache_path,
                "ESMFOLD2_CACHE_PATH": self.config.full_cache_path,
            }
        )
        return self._scoped_compile_env(env_vars)

    @staticmethod
    def _recommend_gpu(num_tokens: int, model_variant: str = "fast") -> str:
        """Recommend GPU type based on total input tokens and model variant.

        ESMFold2-Fast (24-block trunk, single-sequence):
            <=1000 tokens     -> L4       (24 GB VRAM)
            1001..2500 tokens -> A100     (40 GB VRAM)
            >2500 tokens      -> A100_80GB (80 GB VRAM)

        ESMFold2 (48-block trunk, optional MSA):
            <=700 tokens      -> L4       (24 GB VRAM)
            701..2000 tokens  -> A100     (40 GB VRAM)
            >2000 tokens      -> A100_80GB (80 GB VRAM)
        """
        variant = str(model_variant).strip().lower()
        if variant == "full":
            if num_tokens <= 700:
                return "L4"
            if num_tokens <= 2000:
                return "A100"
            return "A100_80GB"

        if num_tokens <= 1000:
            return "L4"
        if num_tokens <= 2500:
            return "A100"
        return "A100_80GB"

    def _get_hardware_config(
        self,
        gpu_type: str,
        num_gpus: int = 1,
        num_tokens: int = 0,
        model_variant: str = "fast",
    ) -> dict[str, Any]:
        """Get hardware configuration for ESMFold2 pipeline."""
        gpu_type = self._normalize_gpu_type(gpu_type)

        if gpu_type == "auto":
            gpu_type = self._recommend_gpu(num_tokens, model_variant=model_variant)
            logger.info(
                f"Auto-selected GPU: {gpu_type} (num_tokens={num_tokens}, variant={model_variant})"
            )

        supported_types = list(self.GPU_SPECS.keys())
        if gpu_type not in supported_types:
            raise ValueError(
                f"Unsupported GPU type '{gpu_type}' for ESMFold2. Supported types: {supported_types}"
            )

        allowed_pool = ["L4", "A100", "A100_80GB"]
        gpu_type = self._resolve_supported_gpu(
            gpu_type,
            allowed_pool=allowed_pool,
            empty_pool_error=(
                f"No ESMFold2 compatible GPUs are supported in this environment! "
                f"(Supported: {self.config.supported_gpus})"
            ),
        )

        if gpu_type == "L4" and num_tokens > 1000:
            logger.warning(
                f"ESMFold2 input has {num_tokens} tokens (>1000) on 24GB L4 VRAM; "
                f"consider gpu_type='A100' or 'A100_80GB' to avoid GPU OOM."
            )

        config = self.predict_spec_for_gpu(gpu_type)
        self._scale_multi_gpu_machine(config, gpu_type, num_gpus)
        config["msa_machine"] = os.getenv("MSA_MACHINE_TYPE", self.DEFAULT_MSA_MACHINE)
        config["dws_max_wait_hours"] = self.config.dws_max_wait_hours
        return config
