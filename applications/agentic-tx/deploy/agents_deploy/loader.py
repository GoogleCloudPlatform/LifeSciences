# Copyright 2025 Google LLC
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

"""Dynamic agent application loading."""

import importlib

from typing import Any

from dotenv import load_dotenv


def load_agent_app(*, package_name: str, agent_env_file: str, app_factory: str = "create_app") -> Any:
    """Dynamically load agent application from package.

    This function:
    1. Loads agent runtime environment variables from .env.agent
    2. Dynamically imports the agent package's adk_app module
    3. Calls the create_app() factory function to build the agent

    Args:
        package_name: Agent package name (e.g., "onedrive-agent")
        agent_env_file: Path to .env.agent file
        app_factory: Name of factory function (default: "create_app")

    Returns:
        Agent application instance

    Example:
        agent_app = load_agent_app(
            package_name="onedrive-agent",
            agent_env_file="./packages/onedrive-agent/.env.agent"
        )
    """
    # Load agent runtime environment
    # This environment gets pickled with the agent
    load_dotenv(agent_env_file)

    # Dynamically import the adk_app module
    module_name = f"{package_name.replace('-', '_')}.adk_app"

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(
            f"Failed to import {module_name}. Ensure package '{package_name}' is installed and has an adk_app module."
        ) from e

    # Get the create_app factory function
    if not hasattr(module, app_factory):
        raise AttributeError(
            f"Module {module_name} does not have a '{app_factory}' function. "
            f"Expected to find: def {app_factory}() -> AdkApp"
        )

    create_fn = getattr(module, app_factory)

    # Create and return the agent app
    # The agent is created with the environment loaded above
    return create_fn()
