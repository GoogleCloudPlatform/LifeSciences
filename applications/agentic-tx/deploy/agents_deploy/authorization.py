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

"""Authorization resource management."""

import urllib.parse
import uuid

from .client import GoogleCloudRestApiClient
from .types import AGENTSPACE_REDIRECT_URI, AuthorizationResource, OAuth2Config


class AuthorizationManager:
    """Manage Agentspace authorization resources."""

    # API version for authorization resources
    API_BASE_URL = "https://discoveryengine.googleapis.com/v1alpha"

    def __init__(self, *, client: GoogleCloudRestApiClient):
        self.client = client

    def _build_authorization_url(
        self,
        *,
        base_url: str,
        client_id: str,
        scopes: list[str],
        redirect_uri: str = AGENTSPACE_REDIRECT_URI,
        audience: str | None = None,
    ) -> str:
        """Build full OAuth2 authorization URL with required parameters.

        Args:
            base_url: Base authorization endpoint URL
            client_id: OAuth2 client ID
            scopes: List of OAuth2 scopes
            redirect_uri: OAuth2 redirect URI (defaults to Agentspace redirect)
            audience: Optional OAuth2 audience parameter

        Returns:
            Fully constructed authorization URL with query parameters

        Note:
            Automatically generates a random UUID for the state parameter to provide
            CSRF protection, matching the security pattern from the original notebook.
            The audience parameter is only included in the URL if provided and non-empty.
        """
        # Generate a random state value for security/CSRF protection
        state_value = str(uuid.uuid4())

        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state_value,
            "prompt": "consent",
            "access_type": "offline",
        }

        # Add audience if provided and non-empty
        if audience:
            params["audience"] = audience

        # For EntraID, if the OAUTH client has approved admin scopes, including `prompt=consent` doesn't work.
        # I believe what's happening is that Microsoft has decided that a user can't consent to admin scopes without
        # admin approval even if they are already approved for the client. It's just a behavior of that SSO system.
        # So in this case we remove the `prompt=consent` from the auth url. The user will just see a popup that immediately
        # dissappears. They don't seen which permissions / scopes are used by this agent.
        if "https://login.microsoftonline.com" in base_url:
            del params["prompt"]

        return f"{base_url}?{urllib.parse.urlencode(params)}"

    def get(self, *, authorization_id: str, location: str = "global") -> AuthorizationResource | None:
        """Get existing authorization or None if not found."""
        path = f"/projects/{self.client.project_number}/locations/{location}/authorizations/{authorization_id}"

        response = self.client.get(base_url=self.API_BASE_URL, path=path)

        if response.status_code == 404:
            return None

        response.raise_for_status()
        data = response.json()

        return self._parse_authorization(data)

    def list(self, *, location: str = "global") -> list[AuthorizationResource]:
        """List all authorizations in the specified location."""
        path = f"/projects/{self.client.project_number}/locations/{location}/authorizations"

        response = self.client.get(base_url=self.API_BASE_URL, path=path)
        response.raise_for_status()

        data = response.json()
        authorizations = []
        for item in data.get("authorizations", []):
            authorizations.append(self._parse_authorization(item))

        return authorizations

    def create_or_update(
        self,
        *,
        authorization_id: str,
        oauth2_config: OAuth2Config,
        location: str = "global",
        redirect_uri: str = AGENTSPACE_REDIRECT_URI,
    ) -> AuthorizationResource:
        """Create authorization if it doesn't exist, otherwise verify it matches."""
        # Check if exists
        existing = self.get(authorization_id=authorization_id, location=location)

        if existing:
            print(f"Authorization {authorization_id} already exists")
            return existing

        # Build full authorization URL with parameters
        authorization_url = self._build_authorization_url(
            base_url=oauth2_config["authorization_uri"],
            client_id=oauth2_config["client_id"],
            scopes=oauth2_config["scopes"],
            redirect_uri=redirect_uri,
            audience=oauth2_config.get("audience"),
        )

        # Create new
        path = f"/projects/{self.client.project_number}/locations/{location}/authorizations"
        body = {
            "name": f"projects/{self.client.project_number}/locations/{location}/authorizations/{authorization_id}",
            "serverSideOauth2": {
                "clientId": oauth2_config["client_id"],
                "clientSecret": oauth2_config["client_secret"],
                "authorizationUri": authorization_url,
                "tokenUri": oauth2_config["token_uri"],
            },
        }

        response = self.client.post(
            base_url=self.API_BASE_URL,
            path=path,
            json=body,
            params={"authorizationId": authorization_id},
        )

        response.raise_for_status()
        data = response.json()

        return self._parse_authorization(data)

    def delete(self, *, authorization_id: str, location: str = "global") -> bool:
        """Delete authorization."""
        path = f"/projects/{self.client.project_number}/locations/{location}/authorizations/{authorization_id}"

        response = self.client.delete(base_url=self.API_BASE_URL, path=path)

        if response.status_code == 404:
            return False

        response.raise_for_status()
        return True

    def _parse_authorization(self, data: dict) -> AuthorizationResource:
        """Parse API response into AuthorizationResource."""
        # Extract from resource name: projects/{num}/locations/{loc}/authorizations/{id}
        parts = data["name"].split("/")
        return AuthorizationResource(
            name=data["name"],
            project_number=parts[1],
            location=parts[3],
            authorization_id=parts[5],
            oauth2_config=data.get("serverSideOauth2", {}),
        )
