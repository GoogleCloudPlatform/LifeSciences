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

"""Startup initialization for OF3 tools.

Provides singleton Config and GPU auto-detection logic.
"""

import logging
import os

from foldrun_app.core.startup_utils import auto_detect_model_gpus, load_json_tool_configs

logger = logging.getLogger(__name__)

_config = None
_tool_configs = None

_TOOL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "openfold3_tools.json")


def get_tool_configs():
    """Load and cache openfold3_tools.json (read once, shared everywhere)."""
    global _tool_configs
    if _tool_configs is None:
        _tool_configs = load_json_tool_configs(_TOOL_CONFIG_PATH)
    return _tool_configs


def get_config():
    """Get or create the singleton OF3Config instance with GPU auto-detection.

    Returns:
        Initialized OF3Config instance with supported GPUs auto-configured.
    """
    global _config
    if _config is not None:
        return _config

    from .config import OF3Config

    config = OF3Config()

    # Run GPU auto-detection
    _auto_detect_gpus(config)

    _config = config
    return _config


def _auto_detect_gpus(config):
    """Run startup GPU quota auto-detection.

    Retains L4 alongside A100 tiers so L4-only projects overwrite CoreConfig's
    default GPU list and fail fast in _get_hardware_config.
    """
    auto_detect_model_gpus(
        config,
        model_label="OF3",
        allowed_gpus=("A100", "A100_80GB", "L4"),
    )
