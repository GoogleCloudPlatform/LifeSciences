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

"""Shared HTTP client: rate limiting and retry in one place.

Not re-implemented per subcommand, and not requested of the agent in
prose (docs/tool-design-guidance.md §8).

Error bodies are returned, not just codes — an agent can act on
"429, retry after 300s"; it cannot act on "request failed".

Pacing state is persisted to disk with ``flock(2)`` so that the
minimum interval between requests to a given host is honoured across
CLI invocations (not only within a single process).  When a shared
filesystem volume is available (``/scion-volumes/scratchpad/pace`` or
an explicit ``DDE_PACE_DIR``), cross-container coordination is active
and multiple agents on the same host observe the per-host interval.
When falling back to container-local pacing (``~/.cache/dde/pace``),
cross-container coordination is NOT covered — each container enforces
the interval independently.
"""

from __future__ import annotations

import fcntl
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit

from .errors import (
    DependencyError,
    EndpointError,
    EndpointUnavailable,
    PhaseContractError,
    Refusal,
)
from .paths import is_safe_to_open

try:  # requests is the one hard HTTP dependency
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_QPS = 1.0
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_MAX_RESPONSE_BYTES = 100 * 1024 * 1024  # 100 MB
# Note: max_response_bytes=0 rejects all responses (including empty ones)
# because Layer 2 checks ``total > 0`` after the first chunk.
DEFAULT_CHUNK_SIZE = 8192

USER_AGENT = "dde-cli/1.0 (+https://github.com/GoogleCloudPlatform/LifeSciences)"

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _resolve_pace_dir() -> tuple[Path, str]:
    """Resolve the pacing directory with a three-tier fallback.

    Returns (path, tier) where tier is one of:
    - "shared" — cross-container coordination via env var or shared volume
    - "local" — container-local (~/.cache/dde/pace)
    - "memory" — in-process only (no disk pacing)

    Always completes without raising — even when
    ``DDE_PACE_REQUIRE_SHARED=1`` is set and no shared tier is found.
    The strict-mode check is deferred to :func:`_pace` so that the CLI
    (including ``dde doctor``) can import this module without crashing.
    """
    # Tier 1: explicit env var override
    env_path = os.environ.get("DDE_PACE_DIR", "")
    if env_path:
        p = Path(env_path)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        else:
            if p.is_dir():
                return p, "shared"
            # DDE_PACE_DIR exists as a file — misconfiguration; fall through
            # and let ``dde doctor`` show the resolved tier.

    # Tier 2: shared project volume (scion default)
    shared = Path("/scion-volumes/scratchpad/pace")
    try:
        shared.mkdir(parents=True, exist_ok=True)
        return shared, "shared"
    except OSError:
        pass

    # Tier 3: container-local (today's behavior)
    local = Path.home() / ".cache" / "dde" / "pace"
    try:
        local.mkdir(parents=True, exist_ok=True)
        return local, "local"
    except OSError:
        pass

    # Tier 4: no disk pacing possible
    return local, "memory"


_PACE_DIR, _PACE_TIER = _resolve_pace_dir()

# Strict-mode flag: deferred from _resolve_pace_dir() so the import
# succeeds and ``dde doctor`` can report the resolved tier.
_PACE_SHARED_REQUIRED = os.environ.get("DDE_PACE_REQUIRE_SHARED", "") == "1"

# In-process fallback when disk pacing is unavailable.
_last_call: dict[str, float] = {}

# Query-string parameter names that must never appear in error messages,
# log lines, or exception detail.  Checked case-insensitively.
_SENSITIVE_PARAMS = re.compile(
    r"([?&])(client_secret|access_token|auth_token|session_token"
    r"|refresh_token|api_key|api-key|apikey|password|passwd"
    r"|key|token|secret)=[^&]*",
    re.IGNORECASE,
)


def _sanitize_url(url: str) -> str:
    """Strip credential-bearing query parameters and authority credentials.

    Handles two leak vectors:
    1. Query-string parameters matching ``_SENSITIVE_PARAMS``.
    2. URL authority credentials (``https://user:pass@host/...``).

    Used in every error message that includes a URL so that credentials
    cannot leak through exception text, stderr, or sidecar records.
    """
    # Redact authority credentials (user:password@host).
    try:
        parts = urlsplit(url)
        if parts.username or parts.password:
            # Rebuild netloc without credentials.
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            url = urlunsplit(
                (parts.scheme, host, parts.path, parts.query, parts.fragment)
            )
    except ValueError:
        pass  # Malformed URL — fall through to param redaction.
    # Redact sensitive query-string parameters.
    return _SENSITIVE_PARAMS.sub(r"\1\2=<REDACTED>", url)


def _sanitize_text(text: str) -> str:
    """Redact credentials that may appear in error response bodies.

    Applies ``_sanitize_url`` to any URL-like substrings and scrubs
    ``_SENSITIVE_PARAMS`` patterns even when they appear outside a URL
    context (e.g. a server echoing ``access_token=...`` in a JSON error).
    """
    # Redact any embedded URLs with authority credentials.
    text = re.sub(
        r"https?://[^\s\"'<>]+",
        lambda m: _sanitize_url(m.group(0)),
        text,
    )
    # Redact bare sensitive-param patterns (server echo).
    return _SENSITIVE_PARAMS.sub(r"\1\2=<REDACTED>", text)


def _require_requests():
    if requests is None:
        raise DependencyError(
            "the 'requests' package is not installed",
            remedy="install it into the tools environment (see tools/requirements.txt)",
        )
    return requests


#: Set by the CLI before a phase-2 command runs. Non-None means every
#: call through this module raises. Deliberately a module-level latch and
#: not a parameter: a parameter would have to be passed correctly by the
#: very call site the guard exists to catch.
_network_forbidden: str | None = None


def forbid_network(reason: str) -> None:
    """Latch this process offline for the rest of the invocation."""
    global _network_forbidden
    _network_forbidden = reason


def network_forbidden() -> str | None:
    return _network_forbidden


def _pace(url: str, qps: float) -> None:
    """Enforce per-host request pacing.

    Persists pacing state to disk with ``flock(2)``, fixing
    cross-invocation pacing within one filesystem.  When the resolved
    pacing directory lives on a shared volume, cross-container
    coordination is active.  When falling back to container-local
    pacing, each container enforces the interval independently.

    Falls back to in-process-only pacing if the pace directory cannot
    be created or the lock file cannot be acquired (e.g. read-only
    filesystem), or if the resolved tier is ``"memory"`` (no disk
    pacing was possible at startup).
    """
    if qps <= 0:
        return
    if _PACE_SHARED_REQUIRED and _PACE_TIER != "shared":
        raise Refusal(
            "DDE_PACE_REQUIRE_SHARED is set but pacing is not using a shared path",
            detail=f"Resolved tier: {_PACE_TIER}, path: {_PACE_DIR}",
            remedy="set DDE_PACE_DIR to a shared path or ensure "
            "/scion-volumes/scratchpad is mounted",
        )
    host = urlparse(url).hostname or ""
    interval = 1.0 / qps
    if _PACE_TIER == "memory":
        _pace_memory(host, interval)
        return
    try:
        _pace_disk(host, interval)
    except OSError:
        _pace_memory(host, interval)


def _pace_disk(host: str, interval: float) -> None:
    """Disk-based pacing with flock for cross-invocation coordination."""
    pace_file = _PACE_DIR / host.replace(":", "_")

    # Refuse to open symlinks — fall back to memory pacing.
    if not is_safe_to_open(pace_file):
        _pace_memory(host, interval)
        return

    # Open atomically with O_NOFOLLOW to prevent TOCTOU symlink attacks.
    # This collapses the symlink check and open into one kernel operation,
    # complementing the is_safe_to_open belt-and-suspenders pre-check above.
    fd = os.open(str(pace_file), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            content = f.read().strip()
            try:
                previous = float(content) if content else 0.0
            except ValueError:
                previous = 0.0
            now = time.time()
            wait = interval - (now - previous)
            if wait > 0:
                time.sleep(min(wait, interval))
            f.seek(0)
            f.truncate()
            f.write(str(time.time()))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _pace_memory(host: str, interval: float) -> None:
    """In-process-only pacing fallback when disk is unavailable."""
    previous = _last_call.get(host)
    now = time.monotonic()
    if previous is not None:
        wait = interval - (now - previous)
        if wait > 0:
            time.sleep(min(wait, interval))
    _last_call[host] = time.monotonic()


def _drain_limited(response, max_bytes=1024):
    """Read up to *max_bytes* of a streamed response into ``response._content``."""
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=max_bytes):
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    response._content = b"".join(chunks)
    response.close()


def request(
    method: str,
    url: str,
    *,
    qps: float = DEFAULT_QPS,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: float = 2.0,
    expect_status: int = 200,
    tolerate_status: frozenset[int] | set[int] | tuple[int, ...] = (),
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    **kwargs: Any,
):
    """Perform an HTTP request with pacing and bounded retry.

    Raises EndpointUnavailable when the retry budget is exhausted on a
    retryable status, EndpointError on a non-retryable one.

    `tolerate_status` names statuses that are an *answer* rather than a
    failure and should be returned to the caller. A 404 from a
    single-record endpoint is the model case: "this identifier does not
    exist" is exactly what the caller asked, and raising would turn a
    definitive negative into an apparent outage. Tolerated statuses are
    opt-in per call site so that no ordinary request can swallow one by
    accident.
    """
    if _network_forbidden:
        raise PhaseContractError(
            f"a phase-2 command attempted {method} {_sanitize_url(url)}",
            detail=_network_forbidden,
            remedy=(
                "phase 2 reads what phase 1 wrote and applies thresholds to it. "
                "Whatever this call was fetching belongs in the fetch phase, "
                "written to Layer 0 with a sidecar, so that re-analysis stays "
                "offline and the reviewer's re-run does not depend on an "
                "endpoint being reachable"
            ),
        )
    lib = _require_requests()
    last_detail = ""
    last_status: int | None = None
    kwargs["stream"] = True

    # Inject default User-Agent unless the caller supplied one.
    headers = kwargs.pop("headers", None) or {}
    headers.setdefault("User-Agent", USER_AGENT)
    kwargs["headers"] = headers

    for attempt in range(1, max_attempts + 1):
        _pace(url, qps)
        response = None
        try:
            response = lib.request(method, url, timeout=timeout, **kwargs)

            if (
                response.status_code == expect_status
                or response.status_code in tolerate_status
            ):
                # Layer 1: Content-Length pre-check
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        cl = int(content_length)
                    except ValueError:
                        cl = -1
                    if cl > max_response_bytes:
                        response.close()
                        raise EndpointError(
                            f"response from {_sanitize_url(url)} exceeds size limit "
                            f"({max_response_bytes} bytes)",
                            detail=f"Content-Length: {content_length}",
                            remedy="if this endpoint legitimately returns large "
                            "responses, pass a higher max_response_bytes",
                        )

                # Layer 2: Streaming read with byte cap
                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
                    total += len(chunk)
                    if total > max_response_bytes:
                        response.close()
                        raise EndpointError(
                            f"response from {_sanitize_url(url)} exceeds size limit "
                            f"({max_response_bytes} bytes)",
                            detail="size exceeded during streaming read",
                            remedy="if this endpoint legitimately returns large "
                            "responses, pass a higher max_response_bytes",
                        )
                    chunks.append(chunk)
                response._content = b"".join(chunks)
                return response

            # Read limited error body from streamed response.
            _drain_limited(response)
            body = _sanitize_text((response.text or "")[:500])
            last_status = response.status_code
            last_detail = _sanitize_text(f"HTTP {response.status_code}: {body}")

        except (EndpointError, EndpointUnavailable):
            raise
        except Exception as exc:  # transport-level (connect or stream)
            if response is not None:
                response.close()
            last_detail = _sanitize_url(f"{type(exc).__name__}: {exc}")
            last_status = None
            if attempt == max_attempts:
                raise EndpointUnavailable(
                    f"transport failure calling {_sanitize_url(url)}",
                    detail=last_detail,
                    remedy="check network access and endpoint health, then retry",
                ) from exc
            time.sleep(backoff**attempt)
            continue

        if response.status_code in _RETRY_STATUS:
            retry_after = response.headers.get("Retry-After")
            if attempt == max_attempts:
                raise EndpointUnavailable(
                    f"{_sanitize_url(url)} still returning {response.status_code} after "
                    f"{max_attempts} attempts",
                    detail=last_detail,
                    remedy=(
                        f"endpoint is rate-limited or unhealthy; retry after "
                        f"{retry_after}s"
                        if retry_after
                        else "endpoint is rate-limited or unhealthy; retry later or "
                        "run `dde doctor`"
                    ),
                )
            delay = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else backoff**attempt
            )
            time.sleep(delay)
            continue

        raise EndpointError(
            f"{_sanitize_url(url)} returned HTTP {response.status_code}",
            detail=body or None,
        )

    raise EndpointUnavailable(
        f"exhausted attempts calling {_sanitize_url(url)}",
        detail=last_detail or f"last status {last_status}",
    )


def get_json(url: str, **kwargs: Any) -> Any:
    response = request("GET", url, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise EndpointError(
            f"{_sanitize_url(url)} did not return valid JSON", detail=str(exc)
        ) from exc


def get_bytes(url: str, **kwargs: Any) -> bytes:
    return request("GET", url, **kwargs).content
