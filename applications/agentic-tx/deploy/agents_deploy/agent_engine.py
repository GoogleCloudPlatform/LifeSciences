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

"""Agent Engine deployment management."""

from collections.abc import Sequence
from typing import Any

from google.cloud.aiplatform_v1beta1 import types as aip_types
from vertexai import agent_engines

from .types import AgentEngineResource


class AgentEngineManager:
    """Manage Agent Engine deployments."""

    def __init__(self, *, project: str, location: str):
        self.project = project
        self.location = location

    def get(self, *, resource_name: str) -> AgentEngineResource | None:
        """Get agent by resource name."""
        try:
            agent = agent_engines.get(resource_name=resource_name)
            return self._to_resource(agent)
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return None
            raise

    def get_by_display_name(self, *, display_name: str) -> AgentEngineResource | None:
        """Get agent by display name."""
        agents = list(agent_engines.list(filter=f'display_name="{display_name}"'))

        if len(agents) == 0:
            return None
        if len(agents) > 1:
            raise ValueError(f"Multiple agents found with display_name={display_name}")

        return self._to_resource(agents[0])

    def list(self, *, filter: str | None = None) -> list[AgentEngineResource]:
        """List all agents with optional filter."""
        agents = agent_engines.list(filter=filter)
        return [self._to_resource(agent) for agent in agents]

    def deploy(
        self,
        *,
        agent_app: Any,
        display_name: str,
        description: str,
        env_vars: dict[str, str],
        package_name: str,
        # All optional parameters from agent_engines.create() for extensibility
        requirements: str | Sequence[str] | None = None,
        gcs_dir_name: str | None = None,
        extra_packages: Sequence[str] | None = None,
        build_options: dict[str, Sequence[str]] | None = None,
        service_account: str | None = None,
        psc_interface_config: aip_types.PscInterfaceConfig | None = None,
        min_instances: int | None = None,
        max_instances: int | None = None,
        resource_limits: dict[str, str] | None = None,
        container_concurrency: int | None = None,
        encryption_spec: aip_types.EncryptionSpec | None = None,
        update_if_exists: bool = True,
    ) -> AgentEngineResource:
        """Deploy agent to Agent Engine (create or update).

        Exposes all parameters from agent_engines.create() for full extensibility.
        """
        # Build create/update config matching agent_engines.create() signature
        config = {
            "agent_engine": agent_app,
            "display_name": display_name,
            "description": description,
            "env_vars": env_vars,
        }

        # Override with explicitly provided parameters
        if requirements is not None:
            config["requirements"] = requirements
        if gcs_dir_name is not None:
            config["gcs_dir_name"] = gcs_dir_name
        if extra_packages is not None:
            config["extra_packages"] = extra_packages
        if build_options is not None:
            config["build_options"] = build_options
        if service_account is not None:
            config["service_account"] = service_account
        if psc_interface_config is not None:
            config["psc_interface_config"] = psc_interface_config
        if min_instances is not None:
            config["min_instances"] = min_instances
        if max_instances is not None:
            config["max_instances"] = max_instances
        if resource_limits is not None:
            config["resource_limits"] = resource_limits
        if container_concurrency is not None:
            config["container_concurrency"] = container_concurrency
        if encryption_spec is not None:
            config["encryption_spec"] = encryption_spec

        # Check if exists
        existing = self.get_by_display_name(display_name=display_name)

        if existing:
            if not update_if_exists:
                print(f"Agent '{display_name}' already exists")
                return existing

            print(f"Updating existing agent: {existing.resource_name}")
            agent = agent_engines.update(resource_name=existing.resource_name, **config)
            return self._to_resource(agent)

        print(f"Creating new agent: {display_name}")
        agent = agent_engines.create(**config)
        return self._to_resource(agent)

    def delete(self, *, resource_name: str) -> bool:
        """Delete agent by resource name."""
        try:
            agent_engines.delete(resource_name=resource_name, force=True)
            return True
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return False
            raise

    def _to_resource(self, agent: Any) -> AgentEngineResource:
        """Convert Vertex AI agent object to AgentEngineResource."""
        return AgentEngineResource(
            resource_name=agent.resource_name,
            display_name=agent.display_name,
            env_vars=getattr(agent, "env_vars", None),
            state=getattr(agent, "state", None),
        )
