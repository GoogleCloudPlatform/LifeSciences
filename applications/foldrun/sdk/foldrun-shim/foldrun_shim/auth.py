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

"""IAM OIDC identity token resolution, JWT expiry inspection, and caching for FoldRun Gateway."""

import base64
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_BUFFER_SEC = 300.0  # Refresh tokens 5 minutes before JWT exp
_DEFAULT_OPAQUE_TTL_SEC = 3300.0  # 55-minute fallback TTL for opaque/mock tokens without JWT exp
_EXPORT_PREFIX_RE = re.compile(r"^export(?=\s)\s*")

_TOKEN_CACHE_LOCK = threading.Lock()
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}


class FoldRunAuthError(RuntimeError):
    """Raised when FoldRun Gateway OIDC authentication or service account impersonation fails."""


def _is_placeholder_value(val: str) -> bool:
    """Return True if an environment or config value is empty or an unconfigured placeholder."""
    cleaned = val.strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    return (
        "example" in lowered
        or "your-project" in lowered
        or "your_project" in lowered
        or "<" in lowered
    )


def _decode_jwt_payload(jwt_token: str) -> dict[str, Any] | None:
    """Decode an unverified JWT payload segment if well-formed, otherwise return None."""
    try:
        parts = jwt_token.strip().split(".")
        if len(parts) < 2 or not parts[1]:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
        payload = json.loads(decoded)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _extract_jwt_exp(jwt_token: str) -> float | None:
    """Extract numeric UNIX expiration timestamp (`exp`) from a JWT if present."""
    payload = _decode_jwt_payload(jwt_token)
    if not payload or "exp" not in payload:
        return None
    try:
        return float(payload["exp"])
    except (TypeError, ValueError):
        return None


def _is_token_expired(jwt_token: str, buffer_sec: float = _TOKEN_REFRESH_BUFFER_SEC) -> bool:
    """Return True if the JWT carries an `exp` claim that is expired or within `buffer_sec`."""
    exp_ts = _extract_jwt_exp(jwt_token)
    if exp_ts is None:
        return False
    return exp_ts <= (time.time() + buffer_sec)


def invalidate_token_cache(gateway_url: str | None = None) -> None:
    """Invalidate cached OIDC tokens (all entries when gateway_url is None, or matching URL)."""
    with _TOKEN_CACHE_LOCK:
        if gateway_url is None:
            _TOKEN_CACHE.clear()
            return
        norm_url = gateway_url.strip().rstrip("/")
        stale_keys = [k for k in _TOKEN_CACHE if k[0] == norm_url]
        for k in stale_keys:
            _TOKEN_CACHE.pop(k, None)


def _get_cached_token(cache_key: tuple[str, str]) -> str | None:
    with _TOKEN_CACHE_LOCK:
        entry = _TOKEN_CACHE.get(cache_key)
        if entry is None:
            return None
        token, exp_ts = entry
        if exp_ts > (time.time() + _TOKEN_REFRESH_BUFFER_SEC):
            return token
        _TOKEN_CACHE.pop(cache_key, None)
        return None


def _cache_token(cache_key: tuple[str, str], token: str) -> None:
    exp_ts = _extract_jwt_exp(token)
    if exp_ts is None:
        exp_ts = time.time() + _DEFAULT_OPAQUE_TTL_SEC
    if exp_ts > (time.time() + _TOKEN_REFRESH_BUFFER_SEC):
        with _TOKEN_CACHE_LOCK:
            _TOKEN_CACHE[cache_key] = (token, exp_ts)


def _load_dotenv_fallback() -> None:
    """Load key=value pairs from .env candidates until FOLDRUN_GATEWAY_URL is configured."""
    candidates: list[Path] = []
    explicit_env = os.environ.get("FOLDRUN_ENV_FILE", "").strip()
    if explicit_env:
        candidates.append(Path(explicit_env))
    candidates.extend([Path(".env"), Path("../.env"), Path("../../.env"), Path("/content/.env")])

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = _EXPORT_PREFIX_RE.sub("", k.strip()).strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                if (
                    k
                    and v
                    and (k not in os.environ or _is_placeholder_value(os.environ.get(k, "")))
                ):
                    os.environ[k] = v
            gw_val = os.environ.get("FOLDRUN_GATEWAY_URL", "")
            if gw_val and not _is_placeholder_value(gw_val):
                return
        except Exception:
            continue


def resolve_gateway_url(explicit_url: str | None = None) -> str:
    """Resolve the FoldRun Gateway URL from argument, FOLDRUN_GATEWAY_URL, or .env."""
    url = (explicit_url or os.environ.get("FOLDRUN_GATEWAY_URL") or "").strip().rstrip("/")
    if _is_placeholder_value(url):
        _load_dotenv_fallback()
        url = (explicit_url or os.environ.get("FOLDRUN_GATEWAY_URL") or "").strip().rstrip("/")
    if _is_placeholder_value(url):
        raise RuntimeError(
            "FOLDRUN_GATEWAY_URL environment variable is not set. "
            "Export FOLDRUN_GATEWAY_URL='https://foldrun-gateway-<hash>-uc.a.run.app' "
            "or place a .env file in the notebook directory before running foldrun-shim commands."
        )
    return url


def _impersonate_service_account_token(impersonate_sa: str, gateway_url: str) -> str:
    """Mint an audience-bound OIDC ID token for `impersonate_sa`, raising FoldRunAuthError on failure."""
    gcloud_err: Exception | None = None
    cmd = [
        "gcloud",
        "auth",
        "print-identity-token",
        f"--impersonate-service-account={impersonate_sa}",
        f"--audiences={gateway_url}",
        "--include-email",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        token = proc.stdout.strip()
        if token and not _is_token_expired(token, buffer_sec=0.0):
            return token
    except Exception as exc:
        gcloud_err = exc

    try:
        import google.auth
        from google.auth import impersonated_credentials
        from google.auth.transport.requests import Request

        source_creds, _ = google.auth.default()
        target_creds = impersonated_credentials.Credentials(
            source_credentials=source_creds,
            target_principal=impersonate_sa,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
            lifetime=3600,
        )
        id_creds = impersonated_credentials.IDTokenCredentials(
            target_creds,
            target_audience=gateway_url,
            include_email=True,
        )
        id_creds.refresh(Request())
        if id_creds.token:
            return str(id_creds.token).strip()
    except Exception as iam_exc:
        if gcloud_err is None:
            gcloud_err = iam_exc

    stderr_detail = ""
    if isinstance(gcloud_err, subprocess.CalledProcessError) and gcloud_err.stderr:
        stderr_detail = f" ({gcloud_err.stderr.strip()})"
    elif gcloud_err is not None:
        stderr_detail = f" ({gcloud_err})"

    raise FoldRunAuthError(
        f"Failed to obtain OIDC identity token via service account impersonation for "
        f"'{impersonate_sa}' targeting '{gateway_url}'{stderr_detail}. "
        f"Ensure your active identity has roles/iam.serviceAccountTokenCreator on {impersonate_sa}."
    ) from gcloud_err


def _gcloud_print_identity_token_for_account(
    gateway_url: str,
    account: str | None = None,
) -> str | None:
    """Try `gcloud auth print-identity-token` with `--audiences` first (for SAs), then bare (for users)."""
    base_cmd = ["gcloud", "auth", "print-identity-token"]
    if account:
        base_cmd.append(f"--account={account}")

    # 1. Try with --audiences (succeeds for *.gserviceaccount.com identities)
    #    For obvious human user emails (`@` without `.gserviceaccount.com`), try bare first to avoid an extra spawn.
    try_audiences_first = account is None or account.endswith(".gserviceaccount.com")
    variants = (
        [[*base_cmd, f"--audiences={gateway_url}"], base_cmd]
        if try_audiences_first
        else [base_cmd, [*base_cmd, f"--audiences={gateway_url}"]]
    )

    for cmd in variants:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            tok = proc.stdout.strip()
            if proc.returncode == 0 and tok and not _is_token_expired(tok):
                return tok
        except Exception:
            continue
    return None


def fetch_gateway_headers(gateway_url: str) -> dict[str, str]:
    """Obtain an Authorization: Bearer <OIDC_ID_TOKEN> header for Cloud Run IAM.

    Resolution order:
    1. Explicit `FOLDRUN_ID_TOKEN` environment variable (verified unexpired via JWT `exp`;
       logs a warning and falls through to auto-refresh if expired or within 5 minutes of expiry).
    2. Local test server (`http://testserver`, `http://localhost`, `http://127.0.0.1`).
    3. In-memory cached OIDC token (reused until `exp - 300s`).
    4. Service account impersonation (`FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT` / `FOLDRUN_IMPERSONATE_SA`).
    5. Google Cloud Metadata Server / ADC (`google.oauth2.id_token.fetch_id_token`) — fast HTTP
       path on Vertex AI Workbench, Colab Enterprise, GCE, and Cloud Run service accounts.
    6. Active `gcloud auth print-identity-token` session (trying `--audiences` for service accounts
       and falling back to bare invocation for human user accounts).
    """
    norm_url = gateway_url.strip().rstrip("/")

    explicit_token = os.environ.get("FOLDRUN_ID_TOKEN", "").strip()
    if explicit_token:
        if not _is_token_expired(explicit_token):
            return {"Authorization": f"Bearer {explicit_token}"}
        logger.warning(
            "FOLDRUN_ID_TOKEN is expired or within 5 minutes of expiration; "
            "falling back to automatic OIDC credential refresh."
        )

    if norm_url.startswith(("http://testserver", "http://localhost", "http://127.0.0.1")):
        return {"Authorization": "Bearer local-dev-token"}

    _load_dotenv_fallback()
    impersonate_sa = (
        os.environ.get("FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT")
        or os.environ.get("FOLDRUN_IMPERSONATE_SA")
        or ""
    ).strip()

    cache_key = (norm_url, impersonate_sa)
    cached_token = _get_cached_token(cache_key)
    if cached_token:
        return {"Authorization": f"Bearer {cached_token}"}

    if impersonate_sa:
        token = _impersonate_service_account_token(impersonate_sa, norm_url)
        _cache_token(cache_key, token)
        return {"Authorization": f"Bearer {token}"}

    # Prefer Google Cloud Metadata Server / service account OIDC token first on GCE / Workbench / Colab Enterprise
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        token = id_token.fetch_id_token(Request(), norm_url)
        if token and not _is_token_expired(token):
            _cache_token(cache_key, token)
            return {"Authorization": f"Bearer {token}"}
    except Exception:
        pass

    # Fallback to gcloud CLI identity token (works for user accounts in Colab/workstations and gcloud SAs)
    token = _gcloud_print_identity_token_for_account(norm_url, account=None)
    if token:
        _cache_token(cache_key, token)
        return {"Authorization": f"Bearer {token}"}

    raise FoldRunAuthError(
        f"Could not obtain a valid Google Cloud IAM OIDC identity token for {norm_url}. "
        "Run `gcloud auth login` with an authorized account or set `FOLDRUN_ID_TOKEN`."
    )
