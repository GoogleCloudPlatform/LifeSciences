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
from typing import Any

from foldrun_app.core.compile_lock import CompileEnvContext
from foldrun_app.core.kfp_tools import KFPModelTool

from .config import Config

logger = logging.getLogger(__name__)


_MMSEQS2_ENV_KEYS = (
    "MMSEQS2_DATA_PIPELINE_MACHINE_TYPE",
    "MMSEQS2_ACCELERATOR_TYPE",
    "MMSEQS2_ACCELERATOR_COUNT",
)

_CompileEnvContext = CompileEnvContext


class AF2Tool(KFPModelTool):
    """Base class for AlphaFold2 tools following ToolUniverse pattern."""

    def __init__(self, tool_config: dict[str, Any], config: Config | None = None):
        """Initialize AF2 tool with configuration."""
        super().__init__(tool_config, config or Config())

    def _setup_compile_env(
        self,
        hardware_config: dict[str, Any],
        filestore_ip: str | None = None,
        filestore_network: str | None = None,
    ) -> _CompileEnvContext:
        """Set up environment variables for AF2 pipeline compilation.

        Explicitly cleans stale MMSEQS2_* overrides when msa_method != "mmseqs2"
        and returns a re-entrant context manager holding _compile_env_lock so
        concurrent requests cannot race on os.environ.
        """
        env_vars = self._build_base_compile_env(
            hardware_config,
            filestore_ip=filestore_ip,
            filestore_network=filestore_network,
            default_machine="g2-standard-12",
            default_accel="NVIDIA_L4",
        )
        env_vars.update(
            {
                "ALPHAFOLD_COMPONENTS_IMAGE": self.config.base_image,
                "MODEL_PARAMS_GCS_LOCATION": f"gs://{self.config.databases_bucket_name}/alphafold2",
                "DATA_PIPELINE_MACHINE_TYPE": hardware_config.get(
                    "data_pipeline", self.DEFAULT_MSA_MACHINE
                ),
                "RELAX_MACHINE_TYPE": hardware_config.get("relax_machine", "g2-standard-12"),
                "RELAX_ACCELERATOR_TYPE": hardware_config.get("relax_accel", "NVIDIA_L4"),
                "RELAX_ACCELERATOR_COUNT": str(hardware_config.get("relax_count", 1)),
                "PARALLELISM": str(hardware_config.get("parallelism", 5)),
            }
        )

        keys_to_clear: tuple[str, ...] = ()
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
        else:
            keys_to_clear = _MMSEQS2_ENV_KEYS

        return self._scoped_compile_env(env_vars, keys_to_clear=keys_to_clear)

    @staticmethod
    def _recommend_gpu(seq_length: int, is_multimer: bool = False) -> str:
        """Recommend GPU type based on sequence length and job type.

        Auto-selection tiers (gpu_type='auto'):
            Monomer:
                <=1500 residues -> A100     (40 GB VRAM)
                >1500           -> A100_80GB (80 GB VRAM)
            Multimer (total residues across all chains):
                <1000           -> A100     (40 GB VRAM)
                >=1000          -> A100_80GB (80 GB VRAM)

        L4 is no longer auto-selected — it provisions slowly under DWS FLEX_START
        and A100 capacity is typically deeper. Users can still request L4 explicitly
        with gpu_type='L4'.
        """
        if is_multimer:
            if seq_length >= 1000:
                return "A100_80GB"
            return "A100"
        if seq_length > 1500:
            return "A100_80GB"
        return "A100"

    def _get_hardware_config(
        self,
        gpu_type: str,
        num_gpus: int = 1,
        relax_gpu_type: str | None = None,
        msa_method: str = "auto",
        use_small_bfd: bool = True,
        seq_length: int = 0,
        is_multimer: bool = False,
    ) -> dict[str, Any]:
        """Get hardware configuration based on GPU type and MSA method."""
        if gpu_type == "auto":
            gpu_type = self._recommend_gpu(seq_length, is_multimer)
            logger.info(
                f"Auto-selected GPU: {gpu_type} (seq_length={seq_length}, multimer={is_multimer})"
            )

        if msa_method == "auto":
            msa_method = "jackhmmer"
            logger.info(
                "Auto-selected MSA method: jackhmmer (default; use msa_method='mmseqs2' for GPU-accelerated search)"
            )

        resolved_gpu = self._resolve_supported_gpu(
            gpu_type,
            allowed_pool=["L4", "A100", "A100_80GB"],
            empty_pool_error=(
                f"No GPUs are supported in this environment! (Supported: {self.config.supported_gpus})"
            ),
        )

        default_relax_gpu = self.RELAX_GPU_MAP.get(resolved_gpu, "A100")
        config = {
            **self.predict_spec_for_gpu(resolved_gpu),
            **self.relax_spec_for_gpu(default_relax_gpu),
        }

        self._scale_multi_gpu_machine(config, resolved_gpu, num_gpus)

        if relax_gpu_type:
            if relax_gpu_type in self.GPU_SPECS:
                config.update(self.relax_spec_for_gpu(relax_gpu_type))
                logger.info(f"Relax GPU overridden to {relax_gpu_type}")
            else:
                logger.warning(f"Unknown relax_gpu_type '{relax_gpu_type}', using default")

        config["data_pipeline"] = self.DEFAULT_MSA_MACHINE
        config["parallelism"] = self.config.parallelism
        config["dws_max_wait_hours"] = self.config.dws_max_wait_hours
        config["msa_method"] = msa_method

        if msa_method == "mmseqs2":
            dp_machine, dp_accel, dp_count = self.GPU_SPECS["L4"]
            config["dp_machine"] = dp_machine
            config["dp_accel"] = dp_accel
            config["dp_accel_count"] = dp_count

        return config
