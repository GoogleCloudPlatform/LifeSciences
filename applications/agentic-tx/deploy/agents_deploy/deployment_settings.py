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

"""Deployment configuration settings."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from google.cloud import resourcemanager_v3, secretmanager

from .types import OAuth2Config


def get_project_number(project_id: str) -> str:
    """Look up project number from project ID using Resource Manager API."""
    client = resourcemanager_v3.ProjectsClient()
    project = client.get_project(name=f"projects/{project_id}")
    # Project name format: projects/{project_number}
    return project.name.split("/")[1]


class DeploymentSettings(BaseSettings):
    """Deployment-specific settings - loaded from .env.deploy as values only."""

    model_config = SettingsConfigDict(
        env_file=".env.deploy",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OAuth config for authorization creation
    oauth2_authorization_url: str | None = Field(default=None, alias="OAUTH2_AUTHORIZATION_URL")
    oauth2_token_url: str | None = Field(default=None, alias="OAUTH2_TOKEN_URL")
    oauth2_client_id: str | None = Field(default=None, alias="OAUTH2_CLIENT_ID")
    oauth2_scopes: str | None = Field(default=None, alias="OAUTH2_SCOPES")
    oauth2_audience: str | None = Field(default=None, alias="OAUTH2_AUDIENCE")
    client_secret_secret_name: str | None = Field(default=None, alias="CLIENT_SECRET_SECRET_NAME")

    # Authorization config (ID comes from agent env, not here)
    authorization_location: str = Field(default="global", alias="AUTHORIZATION_LOCATION")

    # Infrastructure
    google_cloud_project: str = Field(alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(alias="GOOGLE_CLOUD_LOCATION")
    staging_bucket: str = Field(alias="STAGING_BUCKET")

    # Agentspace registration (optional)
    agentspace_app_id: str | None = Field(default=None, alias="AGENTSPACE_APP_ID")

    # Agent metadata
    display_name: str = Field(alias="DISPLAY_NAME")
    description: str = Field(alias="DESCRIPTION")
    tool_description: str = Field(alias="TOOL_DESCRIPTION")

    # Agent runtime configuration
    agent_engine_service_account: str | None = Field(default=None, alias="AGENT_ENGINE_SERVICE_ACCOUNT")

    # Computed field
    _project_number: str | None = None

    @field_validator("agent_engine_service_account", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        """Convert empty strings to None."""
        if v == "":
            return None
        return v

    @property
    def project_number(self) -> str:
        """Lazy-load project number from project ID."""
        if self._project_number is None:
            self._project_number = get_project_number(self.google_cloud_project)
        return self._project_number

    def get_client_secret(self) -> str:
        """Retrieve OAuth client secret from Secret Manager."""
        if not self.client_secret_secret_name:
            raise ValueError("CLIENT_SECRET_SECRET_NAME not configured")
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=self.client_secret_secret_name)
        return response.payload.data.decode("UTF-8")

    @property
    def oauth2_scopes_list(self) -> list[str]:
        """Parse OAuth2 scopes from comma-separated string."""
        if not self.oauth2_scopes:
            return []
        return [scope.strip() for scope in self.oauth2_scopes.split(",")]

    @property
    def oauth2_config(self) -> OAuth2Config:
        """Get OAuth2 config for authorization creation."""
        if not all(
            [
                self.oauth2_client_id,
                self.oauth2_authorization_url,
                self.oauth2_token_url,
            ]
        ):
            raise ValueError("OAuth2 configuration is incomplete")
        return OAuth2Config(
            client_id=self.oauth2_client_id,
            client_secret=self.get_client_secret(),
            authorization_uri=self.oauth2_authorization_url,
            token_uri=self.oauth2_token_url,
            scopes=self.oauth2_scopes_list,
            audience=self.oauth2_audience if self.oauth2_audience else None,
        )
