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

"""AlphaFold Database REST API client.

This module provides utilities for querying the public AlphaFold Database
hosted by EMBL-EBI. Based on the ToolUniverse AlphaFoldRESTTool implementation.
"""

import re
from typing import Any, Dict, Optional

import requests

ALPHAFOLD_BASE_URL = "https://alphafold.ebi.ac.uk/api"


class AlphaFoldDBClient:
    """Client for AlphaFold Database REST API."""

    def __init__(self, base_url: str = ALPHAFOLD_BASE_URL):
        self.base_url = base_url

    def _build_url(self, endpoint_template: str, arguments: Dict[str, Any]) -> str:
        """
        Build URL from endpoint template and arguments.

        Args:
            endpoint_template: URL path with placeholders like "/prediction/{qualifier}"
            arguments: Dict containing placeholder values and query parameters

        Returns:
            Full URL string
        """
        url_path = endpoint_template

        # Find placeholders like {qualifier} in the path
        placeholders = re.findall(r"\{([^{}]+)\}", url_path)
        used = set()

        # Replace placeholders with provided arguments
        for ph in placeholders:
            if ph not in arguments or arguments[ph] is None:
                raise ValueError(f"Missing required parameter '{ph}'")
            url_path = url_path.replace(f"{{{ph}}}", str(arguments[ph]))
            used.add(ph)

        # Remaining args become query parameters
        query_args = {k: v for k, v in arguments.items() if k not in used and v is not None}
        if query_args:
            from urllib.parse import urlencode

            url_path += "?" + urlencode(query_args)

        return self.base_url + url_path

    def _make_request(self, url: str, timeout: int = 30) -> Dict[str, Any]:
        """
        Perform GET request to AlphaFold API.

        Args:
            url: Full URL to request
            timeout: Request timeout in seconds

        Returns:
            Dict containing either 'data' or 'error'
        """
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "FoldRun-Agent/1.0",
                },
            )
        except requests.exceptions.Timeout:
            return {
                "error": "Request timeout",
                "detail": f"AlphaFold API did not respond within {timeout}s",
                "endpoint": url,
            }
        except Exception as e:
            return {"error": "Request to AlphaFold API failed", "detail": str(e), "endpoint": url}

        if resp.status_code == 404:
            return {
                "error": "Not found",
                "detail": "The requested protein or resource was not found in AlphaFold DB",
                "endpoint": url,
            }

        if resp.status_code != 200:
            return {
                "error": f"AlphaFold API returned {resp.status_code}",
                "detail": resp.text,
                "endpoint": url,
            }

        # Parse JSON response
        try:
            data = resp.json()
            if not data:
                return {
                    "error": "Empty response",
                    "detail": "AlphaFold returned an empty response",
                    "endpoint": url,
                }

            return {
                "data": data,
                "metadata": {
                    "count": len(data) if isinstance(data, list) else 1,
                    "source": "AlphaFold Protein Structure DB (EMBL-EBI)",
                    "endpoint": url,
                    "status_code": resp.status_code,
                },
            }
        except Exception as e:
            return {
                "error": "Failed to parse JSON response",
                "raw": resp.text[:500],  # Truncate for safety
                "detail": str(e),
                "endpoint": url,
            }

    def get_prediction(
        self, qualifier: str, sequence_checksum: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieve full AlphaFold 3D structure prediction for a protein.

        Args:
            qualifier: UniProt accession (e.g., 'P69905'), entry name, or CRC64 checksum
            sequence_checksum: Optional CRC64 checksum of the UniProt sequence

        Returns:
            Dict containing prediction data or error
        """
        endpoint = "/prediction/{qualifier}"
        arguments = {"qualifier": qualifier}
        if sequence_checksum:
            arguments["sequence_checksum"] = sequence_checksum

        url = self._build_url(endpoint, arguments)
        return self._make_request(url)

    def get_summary(self, qualifier: str) -> Dict[str, Any]:
        """
        Retrieve summary details of AlphaFold 3D models for a protein.

        Args:
            qualifier: UniProt accession, entry name, or CRC64 checksum

        Returns:
            Dict containing summary data or error
        """
        endpoint = "/uniprot/summary/{qualifier}.json"
        arguments = {"qualifier": qualifier}

        url = self._build_url(endpoint, arguments)
        return self._make_request(url)

    def get_annotations(self, qualifier: str, annotation_type: str = "MUTAGEN") -> Dict[str, Any]:
        """
        Retrieve AlphaFold variant annotations for a protein.

        Args:
            qualifier: UniProt accession, entry name, or CRC64 checksum
            annotation_type: Annotation type (currently only 'MUTAGEN' supported)

        Returns:
            Dict containing annotation data or error
        """
        endpoint = "/annotations/{qualifier}.json"
        arguments = {"qualifier": qualifier, "type": annotation_type}

        url = self._build_url(endpoint, arguments)
        return self._make_request(url)
