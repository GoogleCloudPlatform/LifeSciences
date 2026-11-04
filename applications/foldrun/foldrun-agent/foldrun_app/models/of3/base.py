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

"""Base class for OpenFold3 tools."""

import logging
from typing import Any

from foldrun_app.core.kfp_tools import KFPModelTool

from .config import OF3Config

logger = logging.getLogger(__name__)


class OF3Tool(KFPModelTool):
    """Base class for OpenFold3 tools following ToolUniverse pattern."""

    def __init__(self, tool_config: dict[str, Any], config: OF3Config | None = None):
        super().__init__(tool_config, config or OF3Config())

    def _setup_compile_env(
        self,
        hardware_config: dict[str, Any],
        filestore_ip: str | None = None,
        filestore_network: str | None = None,
    ):
        """Set up environment variables for OF3 pipeline compilation."""
        env_vars = self._build_base_compile_env(
            hardware_config,
            filestore_ip=filestore_ip,
            filestore_network=filestore_network,
        )
        env_vars.update(
            {
                "OPENFOLD3_COMPONENTS_IMAGE": self.config.base_image,
                "OF3_PARAMS_PATH": self.config.params_path,
                "OF3_CCD_PATH": self.config.ccd_path,
                "OF3_RFAM_PATH": self.config.rfam_path,
                "OF3_RNACENTRAL_PATH": self.config.rnacentral_path,
                "RFAM_PATH": self.config.rfam_path,
                "RNACENTRAL_PATH": self.config.rnacentral_path,
            }
        )
        return self._scoped_compile_env(env_vars)

    @staticmethod
    def _recommend_gpu(num_tokens: int) -> str:
        """Recommend GPU type based on total input tokens.

        OpenFold3 uses diffusion-based prediction — minimum A100 (40GB).
            <=2200 tokens -> A100     (40 GB VRAM)
            >2200 tokens  -> A100_80GB (80 GB VRAM)
        """
        if num_tokens > 2200:
            return "A100_80GB"
        return "A100"

    def _get_hardware_config(
        self,
        gpu_type: str,
        num_gpus: int = 1,
        num_tokens: int = 0,
    ) -> dict[str, Any]:
        """Get hardware configuration for OF3 pipeline."""
        gpu_type = self._normalize_gpu_type(gpu_type)

        if gpu_type == "auto":
            gpu_type = self._recommend_gpu(num_tokens)
            logger.info(f"Auto-selected GPU: {gpu_type} (num_tokens={num_tokens})")
        elif gpu_type == "L4":
            gpu_type = self._recommend_gpu(num_tokens)
            logger.warning(
                f"OpenFold3 requires at least 40GB VRAM; upgrading requested L4 to {gpu_type} "
                f"(num_tokens={num_tokens})"
            )

        supported_types = ["A100", "A100_80GB"]
        if gpu_type not in supported_types:
            raise ValueError(
                f"Unsupported GPU type '{gpu_type}' for OpenFold3. Supported types: {supported_types}"
            )

        gpu_type = self._resolve_supported_gpu(
            gpu_type,
            allowed_pool=supported_types,
            empty_pool_error=(
                f"OpenFold3 requires at least 40GB VRAM (A100 or A100_80GB), "
                f"but none are supported in this environment! (Supported: {self.config.supported_gpus})"
            ),
        )

        config = self.predict_spec_for_gpu(gpu_type)
        self._scale_multi_gpu_machine(config, gpu_type, num_gpus)
        config["msa_machine"] = self.DEFAULT_MSA_MACHINE
        config["dws_max_wait_hours"] = self.config.dws_max_wait_hours
        return config
