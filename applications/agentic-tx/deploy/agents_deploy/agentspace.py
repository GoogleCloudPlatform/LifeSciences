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

"""Agentspace agent registration management."""

import builtins

from .client import GoogleCloudRestApiClient
from .types import AgentspaceAgentResource


class AgentspaceManager:
    """Manage Agentspace agent registration."""

    # API version for agentspace resources
    API_BASE_URL = "https://discoveryengine.googleapis.com/v1alpha"

    def __init__(self, *, client: GoogleCloudRestApiClient):
        self.client = client

    def get(self, *, agent_resource_name: str) -> AgentspaceAgentResource | None:
        """Get agent by full resource name."""
        response = self.client.get(base_url=self.API_BASE_URL, path=f"/{agent_resource_name}")

        if response.status_code == 404:
            return None

        response.raise_for_status()
        data = response.json()
        return self._parse_agent(data)

    def list(self, *, agentspace_app_id: str) -> list[AgentspaceAgentResource]:
        """List all agents in Agentspace app."""
        path = (
            f"/projects/{self.client.project_number}/locations/global"
            f"/collections/default_collection/engines/{agentspace_app_id}"
            f"/assistants/default_assistant/agents"
        )

        response = self.client.get(base_url=self.API_BASE_URL, path=path)
        response.raise_for_status()

        data = response.json()
        agents = []
        for item in data.get("agents", []):
            agents.append(self._parse_agent(item))

        return agents

    def get_by_display_name(self, *, agentspace_app_id: str, display_name: str) -> AgentspaceAgentResource | None:
        """Find agent by reasoning engine resource name."""
        agents = self.list(agentspace_app_id=agentspace_app_id)

        for agent in agents:
            if agent.display_name == display_name:
                return agent

        return None

    def create(
        self,
        *,
        agentspace_app_id: str,
        display_name: str,
        description: str,
        tool_description: str,
        reasoning_engine_resource: str,
        authorization_resources: builtins.list[str],
        update_if_exists: bool = False,
    ) -> AgentspaceAgentResource:
        """Create/register agent with Agentspace."""
        # Check if already registered
        # TODO: This needs to be fixed. The current resource should be passed in or it should be with a filter
        existing = self.get_by_display_name(
            agentspace_app_id=agentspace_app_id,
            display_name=display_name,
        )

        if existing:
            if not update_if_exists:
                print(f"Agent already registered: {existing.name}")
                return existing

            # Update existing agent
            print(f"Updating existing agent: {existing.name}")
            return self.update(
                agent_resource_name=existing.name,
                display_name=display_name,
                description=description,
                tool_description=tool_description,
                authorization_resources=authorization_resources,
            )

        # Register new agent
        path = (
            f"/projects/{self.client.project_number}/locations/global"
            f"/collections/default_collection/engines/{agentspace_app_id}"
            f"/assistants/default_assistant/agents"
        )

        body = {
            "displayName": display_name,
            "description": description,
            "adk_agent_definition": {
                "tool_settings": {"tool_description": tool_description},
                "provisioned_reasoning_engine": {"reasoning_engine": reasoning_engine_resource},
                "authorizations": authorization_resources,
            },
        }

        response = self.client.post(base_url=self.API_BASE_URL, path=path, json=body)
        response.raise_for_status()

        data = response.json()
        return self._parse_agent(data)

    def update(
        self,
        *,
        agent_resource_name: str,
        display_name: str | None = None,
        description: str | None = None,
        tool_description: str | None = None,
        authorization_resources: builtins.list[str] | None = None,
    ) -> AgentspaceAgentResource:
        """Update existing Agentspace agent.
        TODO: This method doesn't work and causes an 400 client error bad request.
        """
        # Build patch body with only provided fields
        body = {}
        update_mask_fields = []

        if display_name is not None:
            body["displayName"] = display_name
            update_mask_fields.append("displayName")
        if description is not None:
            body["description"] = description
            update_mask_fields.append("description")

        if tool_description is not None or authorization_resources is not None:
            body["adk_agent_definition"] = {}
            if tool_description is not None:
                body["adk_agent_definition"]["tool_settings"] = {"tool_description": tool_description}
                update_mask_fields.append("adk_agent_definition.tool_settings")
            if authorization_resources is not None:
                body["adk_agent_definition"]["authorizations"] = authorization_resources
                update_mask_fields.append("adk_agent_definition.authorizations")

        params = {"updateMask": ",".join(update_mask_fields)}

        response = self.client.patch(
            base_url=self.API_BASE_URL,
            path=f"/{agent_resource_name}",
            json=body,
            params=params,
        )
        response.raise_for_status()

        data = response.json()
        return self._parse_agent(data)

    def delete(self, *, agent_resource_name: str) -> bool:
        """Delete/unregister agent from Agentspace."""
        response = self.client.delete(base_url=self.API_BASE_URL, path=f"/{agent_resource_name}")

        if response.status_code == 404:
            return False

        response.raise_for_status()
        return True

    def _parse_agent(self, data: dict) -> AgentspaceAgentResource:
        """Parse API response into AgentspaceAgentResource."""
        adk_def = data.get("adk_agent_definition", {})
        return AgentspaceAgentResource(
            name=data["name"],
            display_name=data["displayName"],
            description=data.get("description", ""),
            agent_engine_resource=adk_def.get("provisioned_reasoning_engine", {}).get("reasoning_engine", ""),
            authorization_resources=adk_def.get("authorizations", []),
            state=data.get("state", "UNKNOWN"),
        )
