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

"""Shared async httpx client and retry wrapper for genomic API tools."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None

_USER_AGENT = (
    "AnnotationAgentForAlphaGenome/1.0 (Google ADK Annotation Agent for AlphaGenome; "
    "https://cloud.google.com/products/gemini) httpx"
)


def client() -> httpx.AsyncClient:
    """Lazy-init the shared async client with connection pooling and loop lifecycle check."""
    global _client, _client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if (
        _client is None
        or _client.is_closed
        or (current_loop is not None and _client_loop is not current_loop)
    ):
        _client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            },
            follow_redirects=True,
            limits=httpx.Limits(max_connections=25, max_keepalive_connections=15),
        )
        _client_loop = current_loop
    return _client


def _is_retryable_error(exc: BaseException) -> bool:
    """Retry on network errors and transient 429/5xx HTTP codes."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (408, 429, 500, 502, 503, 504)
    return False


retry_api_call = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1.0, min=1.0, max=15.0),
    retry=retry_if_exception(_is_retryable_error),
)


@retry_api_call
async def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Fetch JSON with pooled client and automatic retries."""
    c = client()
    resp = await c.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


@retry_api_call
async def post_json(
    url: str,
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Post JSON with pooled client and automatic retries."""
    c = client()
    resp = await c.post(url, json=json_data, headers=headers)
    resp.raise_for_status()
    return resp.json()
