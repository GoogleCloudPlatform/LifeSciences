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

"""Generic Google Cloud REST API client with authentication."""

import requests

from google.auth import default
from google.auth.transport.requests import Request


class GoogleCloudRestApiClient:
    """Generic client for Google Cloud REST API calls with authentication.

    Supports versioned APIs (v1alpha, v1beta1, v1) by accepting base_url per request.
    This allows different resources to use different API versions as they get promoted.
    """

    def __init__(self, *, project_number: str):
        self.project_number = project_number
        self.credentials, _ = default()
        self.session = requests.Session()

    def _get_token(self) -> str:
        """Get fresh access token."""
        if not self.credentials.valid:
            self.credentials.refresh(Request())
        return self.credentials.token

    def _request(
        self,
        *,
        method: str,
        base_url: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
    ) -> requests.Response:
        """Make authenticated API request.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            base_url: Base API URL (e.g., https://discoveryengine.googleapis.com/v1alpha)
            path: API path (e.g., /projects/{num}/locations/{loc}/authorizations)
            json: Request body
            params: Query parameters
        """
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": self.project_number,
        }

        url = f"{base_url}{path}"
        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            json=json,
            params=params,
        )

        return response

    def get(self, *, base_url: str, path: str, **kwargs) -> requests.Response:
        return self._request(method="GET", base_url=base_url, path=path, **kwargs)

    def post(self, *, base_url: str, path: str, **kwargs) -> requests.Response:
        return self._request(method="POST", base_url=base_url, path=path, **kwargs)

    def patch(self, *, base_url: str, path: str, **kwargs) -> requests.Response:
        return self._request(method="PATCH", base_url=base_url, path=path, **kwargs)

    def delete(self, *, base_url: str, path: str, **kwargs) -> requests.Response:
        return self._request(method="DELETE", base_url=base_url, path=path, **kwargs)
