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

"""Startup initialization for AlphaFold 3 Vertex AI Endpoint tools."""

import json
import logging
import os

logger = logging.getLogger(__name__)

_config = None
_tool_configs = None

_TOOL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "alphafold3_tools.json")


def get_tool_configs():
    """Load and cache alphafold3_tools.json."""
    global _tool_configs
    if _tool_configs is None:
        with open(_TOOL_CONFIG_PATH) as f:
            _tool_configs = json.load(f)
    return _tool_configs


def get_config():
    """Get or create the singleton AF3Config instance.

    Returns:
        Initialized AF3Config instance.
    """
    global _config
    if _config is not None:
        return _config

    from .config import AF3Config

    config = AF3Config()
    _config = config
    return _config
