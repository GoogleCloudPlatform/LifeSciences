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

"""Startup initialization for AF2 tools.

Provides singleton Config and GPU auto-detection logic.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_config = None
_tool_configs = None

_TOOL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "alphafold_tools.json")


def get_tool_configs():
    """Load and cache alphafold_tools.json (read once, shared everywhere)."""
    global _tool_configs
    if _tool_configs is None:
        with open(_TOOL_CONFIG_PATH, "r") as f:
            _tool_configs = json.load(f)
    return _tool_configs


def get_config():
    """Get or create the singleton Config instance with GPU auto-detection.

    Returns:
        Initialized Config instance with supported GPUs auto-configured.
    """
    global _config
    if _config is not None:
        return _config

    from .config import Config

    config = Config()

    # Run GPU auto-detection
    _auto_detect_gpus(config)

    _config = config
    return _config


def _auto_detect_gpus(config):
    """Run startup GPU quota auto-detection.

    Args:
        config: Config instance to update with detected GPUs.
    """
    from foldrun_app.core.hardware import detect_supported_gpus

    try:
        logger.info("Running startup GPU quota auto-detection...")
        ordered_support = detect_supported_gpus(config.project_id, config.region)

        if ordered_support:
            config.set_supported_gpus(ordered_support)
            logger.info(f"Auto-configured supported GPUs: {ordered_support}")
        else:
            logger.warning("No GPUs found with limit > 0! keeping defaults.")

    except Exception as e:
        logger.warning(f"Auto-detection failed (using defaults): {e}")
