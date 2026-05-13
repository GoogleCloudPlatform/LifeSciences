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

"""Shared httpx client + retry decorator for the biomedical-source tools.

All public-API HTTP tools route through `client()` so connections + TLS
sessions are pooled across calls, and `retryable_get()` applies exponential
backoff on transient failures (5xx + network) without retrying on 4xx
(those are usually a bad query or auth problem and won't fix themselves).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# Module-level pooled client. Default 30s timeout matches what the per-call
# clients used; individual callers can pass `timeout=` to override (e.g.
# Europe PMC fulltext is 60s).
_client: httpx.AsyncClient | None = None


_USER_AGENT = (
    'BioCompass/1.0 (Google ADK biomedical research agent; '
    'https://github.com/cloud-sean/LifeSciences) httpx'
)


def client() -> httpx.AsyncClient:
  """Lazy-init the shared async client (one per process).

  Sets an identifying User-Agent — ClinicalTrials.gov returns 403 to the
  default httpx UA and NCBI politely asks callers to identify themselves
  in the E-utilities terms of use.
  """
  global _client
  if _client is None:
    _client = httpx.AsyncClient(
        timeout=30.0,
        headers={'User-Agent': _USER_AGENT},
    )
  return _client


def _is_retryable(exc: BaseException) -> bool:
  # Retry on connection / timeout / read errors (transient), and on 5xx
  # responses. Do NOT retry on 4xx — those are caller errors that won't be
  # fixed by waiting (bad query, missing param, auth failure, 404).
  if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
    return True
  if isinstance(exc, httpx.HTTPStatusError):
    return 500 <= exc.response.status_code < 600
  return False


# Decorator applied to every tool's HTTP call. 3 attempts, exponential
# backoff capped at 8 s — keeps the worst case at ~9 s before failing.
retryable = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)


@retryable
async def get_json(url: str, params: dict[str, Any] | None = None,
                   timeout: float | None = None) -> Any:
  """GET + raise_for_status + .json(), with retry on transient errors."""
  resp = await client().get(url, params=params, timeout=timeout)
  resp.raise_for_status()
  return resp.json()


@retryable
async def get_text(url: str, params: dict[str, Any] | None = None,
                   timeout: float | None = None) -> str:
  """GET + raise_for_status + .text, with retry on transient errors."""
  resp = await client().get(url, params=params, timeout=timeout)
  resp.raise_for_status()
  return resp.text


@retryable
async def get_response(url: str, params: dict[str, Any] | None = None,
                       timeout: float | None = None) -> httpx.Response:
  """GET + retry. Caller is responsible for status handling — useful when
  some non-2xx codes (e.g. 404) carry meaningful semantics that should not
  trigger a retry but should be inspected rather than raise.
  """
  resp = await client().get(url, params=params, timeout=timeout)
  if 500 <= resp.status_code < 600:
    resp.raise_for_status()  # let the decorator retry it
  return resp


# ---------------------------------------------------------------------------
# Stdlib fallback for hosts that fingerprint-block httpx.
#
# ClinicalTrials.gov's v2 API rejects httpx (any User-Agent, any HTTP
# version) — likely a TLS-fingerprint (JA3) block at the CDN. urllib's
# stdlib client uses Python's default OpenSSL stack, which gets through.
# Wrapped in `asyncio.to_thread` so calls don't block the event loop.
# ---------------------------------------------------------------------------

class UrllibStatusError(Exception):
  """Raised on non-2xx responses from `get_json_urllib`."""

  def __init__(self, status: int, body: str, url: str):
    super().__init__(f'{status} from {url}')
    self.status = status
    self.body = body
    self.url = url


def _is_retryable_urllib(exc: BaseException) -> bool:
  if isinstance(exc, urllib.error.URLError):
    return True
  if isinstance(exc, UrllibStatusError):
    return 500 <= exc.status < 600
  return False


_retryable_urllib = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
    retry=retry_if_exception(_is_retryable_urllib),
    reraise=True,
)


def _build_query(params: dict[str, Any] | None) -> str:
  if not params:
    return ''
  from urllib.parse import urlencode
  return '?' + urlencode(params)


def _urllib_get(url: str, params: dict[str, Any] | None,
                timeout: float) -> tuple[int, str]:
  full_url = url + _build_query(params)
  req = urllib.request.Request(
      full_url, headers={'User-Agent': _USER_AGENT, 'Accept': 'application/json'},
  )
  try:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
      return resp.status, resp.read().decode('utf-8')
  except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8', errors='replace') if e.fp else ''
    return e.code, body


@_retryable_urllib
async def get_json_urllib(url: str, params: dict[str, Any] | None = None,
                          timeout: float = 30.0) -> Any:
  """GET via stdlib urllib (in a worker thread). Use for hosts that block
  httpx by TLS fingerprint. Returns parsed JSON or raises UrllibStatusError.
  """
  status, body = await asyncio.to_thread(_urllib_get, url, params, timeout)
  if status >= 400:
    raise UrllibStatusError(status, body, url)
  return json.loads(body)
