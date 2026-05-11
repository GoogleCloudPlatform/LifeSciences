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

"""Resilient, idempotent deployment library for Agentspace agents."""

from .agent_deployment import AgentDeployment
from .agent_engine import AgentEngineManager
from .agentspace import AgentspaceManager
from .authorization import AuthorizationManager
from .client import GoogleCloudRestApiClient
from .deployment_settings import DeploymentSettings
from .deployment_state import DeploymentState
from .types import (
    AgentEngineResource,
    AgentspaceAgentResource,
    AuthorizationResource,
    OAuth2Config,
)

# CLI is available but not exported by default
# Users should run: uv run agents-deploy <command>

__all__ = [
    "AgentDeployment",
    "AgentEngineManager",
    "AgentEngineResource",
    "AgentspaceAgentResource",
    "AgentspaceManager",
    "AuthorizationManager",
    "AuthorizationResource",
    "DeploymentSettings",
    "DeploymentState",
    "GoogleCloudRestApiClient",
    "OAuth2Config",
]
