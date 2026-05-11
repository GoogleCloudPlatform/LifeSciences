"""
ClinicalTrials.gov API Client

Simple Python client for the ClinicalTrials.gov API v2.
Based on the logic from clinicaltrialsgov-mcp-server.

API Documentation: https://clinicaltrials.gov/data-api/api
"""

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

BASE_URL = "https://clinicaltrials.gov/api/v2"


class ClinicalTrialsAPIError(Exception):
    """Exception raised for ClinicalTrials.gov API errors."""

    pass


class ClinicalTrialsClient:
    """Client for interacting with the ClinicalTrials.gov API."""

    def __init__(self, timeout: int = 30):
        """
        Initialize the client.

        Args:
            timeout: Request timeout in seconds (default: 30)
        """
        self.timeout = timeout

    async def search_studies(
        self,
        query: Optional[str] = None,
        filter: Optional[str] = None,
        page_size: int = 10,
        page_token: Optional[str] = None,
        sort: Optional[str] = None,
        fields: Optional[List[str]] = None,
        country: Optional[str] = None,
        state: Optional[str] = None,
        city: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search for clinical trials.

        Args:
            query: General search query for conditions, interventions, sponsors
            filter: Advanced filter expression using ClinicalTrials.gov filter syntax
            page_size: Number of studies to return per page (1-200, default: 10)
            page_token: Token for retrieving the next page of results
            sort: Sort order specification (e.g., "LastUpdateDate:desc")
            fields: Specific fields to return (reduces payload size)
            country: Filter by country (e.g., "United States", "Canada")
            state: Filter by state/province (e.g., "California", "Ontario")
            city: Filter by city (e.g., "New York", "Toronto")

        Returns:
            Paged studies response with studies list and pagination metadata

        Raises:
            ClinicalTrialsAPIError: If the API request fails
        """
        params = {}

        if query:
            params["query.term"] = query

        if filter:
            params["filter.advanced"] = filter

        if page_size:
            params["pageSize"] = str(page_size)

        if page_token:
            params["pageToken"] = page_token

        if sort:
            params["sort"] = sort

        if fields:
            params["fields"] = ",".join(fields)

        # Always count total for pagination
        params["countTotal"] = "true"

        # Build geographic filters
        geo_filters = []
        if country:
            geo_filters.append(f'AREA[LocationCountry]{country}')
        if state:
            geo_filters.append(f'AREA[LocationState]{state}')
        if city:
            geo_filters.append(f'AREA[LocationCity]{city}')

        if geo_filters:
            combined_filter = " AND ".join(geo_filters)
            if filter:
                params["filter.advanced"] = f'({filter}) AND ({combined_filter})'
            else:
                params["filter.advanced"] = combined_filter

        url = f"{BASE_URL}/studies"
        return await self._fetch(url, params)

    async def get_study(self, nct_id: str, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Get detailed information about a specific study by NCT ID.

        Args:
            nct_id: The NCT identifier (e.g., "NCT03372603")
            fields: Specific fields to return (reduces payload size)

        Returns:
            Study data

        Raises:
            ClinicalTrialsAPIError: If the study is not found or API request fails
        """
        url = f"{BASE_URL}/studies/{nct_id}"
        params = {}

        if fields:
            params["fields"] = ",".join(fields)

        return await self._fetch(url, params)

    async def get_study_metadata(self, nct_id: str) -> Dict[str, Any]:
        """
        Get lightweight metadata for a specific study.

        Args:
            nct_id: The NCT identifier

        Returns:
            Study metadata (title, status, dates)

        Raises:
            ClinicalTrialsAPIError: If the study is not found
        """
        fields = [
            "NCTId",
            "BriefTitle",
            "OfficialTitle",
            "OverallStatus",
            "StartDateStruct",
            "CompletionDateStruct",
            "LastUpdatePostDateStruct",
        ]

        study = await self.get_study(nct_id, fields)

        # Extract metadata from the response
        protocol = study.get("protocolSection", {})
        identification = protocol.get("identificationModule", {})
        status = protocol.get("statusModule", {})

        return {
            "nctId": identification.get("nctId", nct_id),
            "title": identification.get("briefTitle") or identification.get("officialTitle"),
            "status": status.get("overallStatus"),
            "startDate": status.get("startDateStruct", {}).get("date"),
            "completionDate": status.get("completionDateStruct", {}).get("date"),
            "lastUpdateDate": status.get("lastUpdatePostDateStruct", {}).get("date"),
        }

    async def get_api_stats(self) -> Dict[str, Any]:
        """
        Get API statistics.

        Returns:
            API stats including total study count and version

        Raises:
            ClinicalTrialsAPIError: If the API request fails
        """
        url = f"{BASE_URL}/stats/size"
        stats = await self._fetch(url)

        return {
            "totalStudies": stats.get("totalStudies", 0),
            "lastUpdated": stats.get("lastUpdated", ""),
            "version": "v2",
        }

    async def _fetch(self, url: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Internal method to fetch data from the API.

        Args:
            url: The API endpoint URL
            params: Query parameters

        Returns:
            Parsed JSON response

        Raises:
            ClinicalTrialsAPIError: If the request fails
        """
        if params:
            query_string = urlencode(params)
            full_url = f"{url}?{query_string}"
        else:
            full_url = url

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    full_url,
                    headers={"Accept": "application/json"},
                ) as response:
                    if response.status == 404:
                        text = await response.text()
                        raise ClinicalTrialsAPIError(f"Resource not found: {text}")

                    if response.status != 200:
                        text = await response.text()
                        raise ClinicalTrialsAPIError(
                            f"API request failed with status {response.status}: {text}"
                        )

                    return await response.json()

        except aiohttp.ClientError as e:
            raise ClinicalTrialsAPIError(f"HTTP request failed: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ClinicalTrialsAPIError(f"Request timed out after {self.timeout}s") from e
