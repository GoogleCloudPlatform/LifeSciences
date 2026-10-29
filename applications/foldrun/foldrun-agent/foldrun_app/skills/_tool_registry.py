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

"""Singleton registry for all FoldRun tool instances (platform + registered model plugins)."""

import logging

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.model_registry import get_registered_models
from foldrun_app.core.startup_utils import get_core_config
from foldrun_app.core.tools import PLATFORM_TOOL_CLASSES

logger = logging.getLogger(__name__)

_agents: dict[str, BaseTool] = {}
_initialized = False


def ensure_initialized() -> None:
    """Eagerly initialize all platform and enabled model tools."""
    global _initialized
    if not _initialized:
        _initialize_all_tools()
        _initialized = True
    logger.info(f"Tool registry ready — {len(_agents)} tools loaded")


def get_tool(tool_name: str) -> BaseTool:
    """Get an initialized tool instance by registry key."""
    global _initialized
    if not _initialized:
        _initialize_all_tools()
        _initialized = True
    if tool_name not in _agents:
        raise KeyError(
            f"Tool '{tool_name}' is not initialized. If this is an AF3, OF3, Boltz-2, BoltzGen, "
            f"RFD4-Proteina, or BindCraft tool, verify that the corresponding environment "
            f"variables (e.g. AF3_ENDPOINT, BOLTZGEN_COMPONENTS_IMAGE) are set."
        )
    return _agents[tool_name]


def _initialize_all_tools() -> None:
    """Initialize cross-model platform tools and all enabled model plugin tools."""
    _initialize_platform_tools()
    for descriptor in get_registered_models(enabled_only=True).values():
        _initialize_model_tools(descriptor.name)


def _initialize_platform_tools() -> None:
    """Initialize cross-model platform tools backed by CoreConfig."""
    config = get_core_config()
    for tool_name, tool_cls in PLATFORM_TOOL_CLASSES.items():
        _agents[tool_name] = tool_cls(tool_config={"name": tool_name}, config=config)
        logger.info(f"Initialized platform tool: {tool_name}")


def _initialize_model_tools(model_id: str) -> None:
    """Initialize backend tools for a registered model plugin."""
    from foldrun_app.core.model_registry import get_model

    descriptor = get_model(model_id)
    if not descriptor.is_enabled():
        return

    try:
        config = descriptor.get_config()
        for tool_name, tool_cls in descriptor.get_tool_classes().items():
            _agents[tool_name] = tool_cls(tool_config={"name": tool_name}, config=config)
            logger.info(f"Initialized {descriptor.display_name} tool: {tool_name}")
    except Exception as e:
        logger.warning(f"{descriptor.display_name} tools not initialized: {e}")
