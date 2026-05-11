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

"""Type definitions for agent deployment resources."""

from dataclasses import dataclass
from typing import TypedDict

# Default Agentspace redirect URI for OAuth2 authorization code flow
AGENTSPACE_REDIRECT_URI = "https://vertexaisearch.cloud.google.com/oauth-redirect"


class OAuth2Config(TypedDict):
    """OAuth2 configuration for authorization."""

    client_id: str | None
    client_secret: str | None
    authorization_uri: str | None
    token_uri: str | None
    scopes: list[str]
    audience: str | None  # Optional OAuth2 audience parameter


@dataclass
class AuthorizationResource:
    """Authorization resource representation."""

    name: str
    project_number: str
    location: str
    authorization_id: str
    oauth2_config: OAuth2Config

    @property
    def resource_name(self) -> str:
        return f"projects/{self.project_number}/locations/{self.location}/authorizations/{self.authorization_id}"

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "resource_name": self.resource_name,
            "authorization_id": self.authorization_id,
            "location": self.location,
            "project_number": self.project_number,
        }


@dataclass
class AgentEngineResource:
    """Agent Engine resource representation."""

    resource_name: str
    display_name: str
    env_vars: dict[str, str] | None = None
    state: str | None = None

    @property
    def project_number(self) -> str:
        """Extract project number from resource name."""
        return self.resource_name.split("/")[1]

    @property
    def location(self) -> str:
        """Extract location from resource name."""
        return self.resource_name.split("/")[3]

    @property
    def agent_id(self) -> str:
        """Extract agent ID from resource name."""
        return self.resource_name.split("/")[-1]

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "resource_name": self.resource_name,
            "display_name": self.display_name,
            "project_number": self.project_number,
            "location": self.location,
            "agent_id": self.agent_id,
            "state": self.state,
        }


@dataclass
class AgentspaceAgentResource:
    """Agentspace agent resource representation."""

    name: str
    display_name: str
    description: str
    agent_engine_resource: str
    authorization_resources: list[str]
    state: str

    @property
    def agent_id(self) -> str:
        """Extract agent ID from resource name."""
        return self.name.split("/")[-1]

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "description": self.description,
            "agent_engine_resource": self.agent_engine_resource,
            "authorization_resources": self.authorization_resources,
            "state": self.state,
        }
