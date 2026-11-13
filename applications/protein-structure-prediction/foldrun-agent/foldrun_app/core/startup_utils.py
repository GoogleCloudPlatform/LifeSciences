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

"""Shared model plugin startup helpers (singleton CoreConfig and cached GPU detection)."""

import logging

from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)

_core_config: CoreConfig | None = None


def get_core_config() -> CoreConfig:
    """Get or create the shared model-agnostic CoreConfig singleton."""
    global _core_config
    if _core_config is None:
        _core_config = CoreConfig()
    return _core_config


def reset_core_config() -> None:
    """Reset the shared CoreConfig singleton (for testing)."""
    global _core_config
    _core_config = None
    CoreConfig.clear_gpu_detection_cache()


def auto_detect_model_gpus(
    config: CoreConfig,
    *,
    model_label: str = "",
    allowed_gpus: tuple[str, ...] | None = None,
    include_h100: bool = False,
) -> None:
    """Populate ``config.supported_gpus`` using the session-cached GCE quota check.

    Args:
        config: Model ``CoreConfig`` instance to update.
        model_label: Optional label for log messages (e.g. ``"AF2"``, ``"OF3"``, ``"BOLTZ2"``).
        allowed_gpus: Optional tuple of GPU tier identifiers accepted by this model.
            When set, filters detected GPUs to that subset before updating ``config``.
        include_h100: Whether to include H100 quota checks.
    """
    suffix = f" for {model_label}" if model_label else ""
    try:
        logger.info(f"Running startup GPU quota auto-detection{suffix}...")
        ordered_support = config.detect_supported_gpus_cached(include_h100=include_h100)

        if ordered_support:
            filtered = (
                [g for g in ordered_support if g in allowed_gpus]
                if allowed_gpus is not None
                else list(ordered_support)
            )
            if filtered:
                config.set_supported_gpus(filtered)
                label_prefix = f"{model_label} " if model_label else ""
                logger.info(f"Auto-configured {label_prefix}supported GPUs: {filtered}")
            else:
                logger.warning(
                    f"No compatible GPUs ({allowed_gpus}) found{suffix}! Keeping defaults."
                )
        else:
            logger.warning(f"No GPUs found with limit > 0{suffix}! Keeping defaults.")

        from foldrun_app.core import hardware as hw_mod

        region_key = (config.project_id or "", config.region or "")
        cpu_mocked = type(hw_mod.check_cpu_quota).__module__.startswith("unittest.mock")
        if region_key in hw_mod._last_cpu_quotas_by_region or cpu_mocked:
            cpu_quotas = config.detect_cpu_quotas_cached()
            if cpu_quotas:
                config.set_cpu_quotas(cpu_quotas)

    except Exception as e:
        logger.warning(f"Auto-detection failed{suffix} (using defaults): {e}")
