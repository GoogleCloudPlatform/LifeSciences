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

"""Tests for NCBI API key transmission, QPS coupling, and credential safety.

Asserts:
- When NCBI_API_KEY is set, QPS is 10 and requests carry the key.
- When NCBI_API_KEY is unset, QPS is 3 and no api_key parameter appears.
- The QPS constant and key transmission derive from a single variable
  (they cannot diverge).
- Error messages never contain the API key value.
"""

from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from dde.core import http
from dde.core.errors import EndpointError, EndpointUnavailable


def _reload_ncbi(env: dict[str, str]):
    """Reload ``dde.core.ncbi`` with a controlled environment.

    Returns the reloaded module so callers can inspect its exports.
    """
    with patch.dict(os.environ, env, clear=False):
        # Remove NCBI_API_KEY from env when not in `env` dict.
        if "NCBI_API_KEY" not in env:
            os.environ.pop("NCBI_API_KEY", None)
        import dde.core.ncbi as ncbi_mod

        importlib.reload(ncbi_mod)
        return ncbi_mod


def _make_response(
    *,
    status_code: int = 200,
    headers: dict | None = None,
    content: bytes = b"",
    chunks: list[bytes] | None = None,
):
    """Build a mock ``requests.Response``-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}

    if chunks is None:
        chunks = [content] if content else [b""]

    resp.iter_content = MagicMock(return_value=iter(chunks))
    resp.close = MagicMock()
    resp._content = content
    resp.text = content.decode("utf-8", errors="replace") if content else ""
    resp.json = MagicMock(return_value={})
    return resp


class TestNCBIQPSCoupling(unittest.TestCase):
    """The QPS constant and key transmission derive from a single variable."""

    def test_qps_10_when_key_set(self):
        ncbi = _reload_ncbi({"NCBI_API_KEY": "test-key-abc123"})
        self.assertEqual(ncbi.EUTILS_QPS, 10.0)

    def test_qps_3_when_key_unset(self):
        ncbi = _reload_ncbi({})
        self.assertEqual(ncbi.EUTILS_QPS, 3.0)

    def test_qps_3_when_key_empty(self):
        ncbi = _reload_ncbi({"NCBI_API_KEY": ""})
        self.assertEqual(ncbi.EUTILS_QPS, 3.0)

    def test_suffix_present_when_key_set(self):
        ncbi = _reload_ncbi({"NCBI_API_KEY": "test-key-abc123"})
        suffix = ncbi.api_key_suffix()
        self.assertIn("api_key=test-key-abc123", suffix)

    def test_suffix_empty_when_key_unset(self):
        ncbi = _reload_ncbi({})
        self.assertEqual(ncbi.api_key_suffix(), "")

    def test_params_dict_present_when_key_set(self):
        ncbi = _reload_ncbi({"NCBI_API_KEY": "test-key-abc123"})
        params = ncbi.api_key_params()
        self.assertEqual(params, {"api_key": "test-key-abc123"})

    def test_params_dict_empty_when_key_unset(self):
        ncbi = _reload_ncbi({})
        self.assertEqual(ncbi.api_key_params(), {})

    def test_coupling_cannot_diverge(self):
        """QPS and key suffix must agree: both on or both off."""
        ncbi = _reload_ncbi({"NCBI_API_KEY": "k"})
        self.assertEqual(ncbi.EUTILS_QPS, 10.0)
        self.assertTrue(ncbi.api_key_suffix())  # non-empty
        self.assertTrue(ncbi.api_key_params())  # non-empty

        ncbi = _reload_ncbi({})
        self.assertEqual(ncbi.EUTILS_QPS, 3.0)
        self.assertFalse(ncbi.api_key_suffix())  # empty string
        self.assertFalse(ncbi.api_key_params())  # empty dict


class TestURLKeyTransmission(unittest.TestCase):
    """E-utilities URLs include api_key when set, omit it when unset."""

    def test_pubmed_esearch_url_includes_key(self):
        ncbi = _reload_ncbi({"NCBI_API_KEY": "test-key-abc123"})
        # Simulate what pubmed.py does: string concatenation + suffix.
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        url = (
            f"{base}/esearch.fcgi?db=pubmed&term=test&retmax=20"
            f"&sort=relevance&retmode=json" + ncbi.api_key_suffix()
        )
        parsed = parse_qs(urlparse(url).query)
        self.assertIn("api_key", parsed)
        self.assertEqual(parsed["api_key"], ["test-key-abc123"])

    def test_pubmed_esearch_url_omits_key_when_unset(self):
        ncbi = _reload_ncbi({})
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        url = (
            f"{base}/esearch.fcgi?db=pubmed&term=test&retmax=20"
            f"&sort=relevance&retmode=json" + ncbi.api_key_suffix()
        )
        parsed = parse_qs(urlparse(url).query)
        self.assertNotIn("api_key", parsed)

    def test_geo_params_include_key(self):
        ncbi = _reload_ncbi({"NCBI_API_KEY": "test-key-abc123"})
        params = {
            "db": "gds",
            "retmode": "json",
            "retmax": 20,
            "term": "test",
            **ncbi.api_key_params(),
        }
        self.assertIn("api_key", params)
        self.assertEqual(params["api_key"], "test-key-abc123")

    def test_geo_params_omit_key_when_unset(self):
        ncbi = _reload_ncbi({})
        params = {
            "db": "gds",
            "retmode": "json",
            "retmax": 20,
            "term": "test",
            **ncbi.api_key_params(),
        }
        self.assertNotIn("api_key", params)


class TestCredentialScrubbing(unittest.TestCase):
    """API key must never appear in error messages or exception text."""

    def setUp(self):
        http._network_forbidden = None

    def test_sanitize_url_strips_api_key(self):
        url = "https://example.com/api?db=pubmed&api_key=SECRET123&retmax=20"
        sanitized = http._sanitize_url(url)
        self.assertNotIn("SECRET123", sanitized)
        self.assertIn("api_key=<REDACTED>", sanitized)
        # Other params preserved.
        self.assertIn("db=pubmed", sanitized)
        self.assertIn("retmax=20", sanitized)

    def test_sanitize_url_handles_no_key(self):
        url = "https://example.com/api?db=pubmed&retmax=20"
        sanitized = http._sanitize_url(url)
        self.assertEqual(sanitized, url)

    def test_sanitize_url_strips_key_at_end(self):
        url = "https://example.com/api?db=pubmed&api_key=SECRET123"
        sanitized = http._sanitize_url(url)
        self.assertNotIn("SECRET123", sanitized)

    def test_error_message_excludes_key_on_non_retryable(self):
        """EndpointError from a 403 must not contain the key value."""
        secret = "my-secret-ncbi-key-12345"
        url = f"https://eutils.ncbi.nlm.nih.gov/api?db=pubmed&api_key={secret}"

        error_body = b"forbidden"
        mock_resp = _make_response(status_code=403, content=error_body)
        mock_resp.iter_content = MagicMock(return_value=iter([error_body]))
        mock_resp.text = error_body.decode()

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            with self.assertRaises(EndpointError) as ctx:
                http.request("GET", url, qps=0)

        exc_text = str(ctx.exception)
        exc_msg = ctx.exception.message
        exc_detail = ctx.exception.detail or ""
        combined = f"{exc_msg} {exc_detail} {exc_text}"
        self.assertNotIn(secret, combined, "API key leaked in EndpointError")

    def test_error_message_excludes_key_on_transport_failure(self):
        """EndpointUnavailable from a transport error must not contain the key."""
        secret = "SECRETKEY123"
        url = f"https://eutils.ncbi.nlm.nih.gov/api?api_key={secret}&db=pubmed"

        # A real requests.ConnectionError embeds the full URL (including
        # query parameters) in its string representation.  The mock must
        # reproduce that so this test catches unsanitised detail fields.
        realistic_exc = ConnectionError(
            "HTTPSConnectionPool(host='eutils.ncbi.nlm.nih.gov', port=443): "
            "Max retries exceeded with url: "
            f"/entrez/eutils/esearch.fcgi?db=pubmed&term=test&api_key={secret}"
        )

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.side_effect = realistic_exc
            with self.assertRaises(EndpointUnavailable) as ctx:
                http.request("GET", url, qps=0, max_attempts=1)

        exc_text = str(ctx.exception)
        exc_msg = ctx.exception.message
        exc_detail = ctx.exception.detail or ""
        combined = f"{exc_msg} {exc_detail} {exc_text}"
        self.assertNotIn(secret, combined, "API key leaked in EndpointUnavailable")
        self.assertIn(
            "<REDACTED>", exc_detail, "detail field should contain redacted marker"
        )

    def test_error_message_excludes_key_on_retry_exhaustion(self):
        """Retry-exhaustion error must not contain the key."""
        secret = "retry-secret-key-777"
        url = f"https://eutils.ncbi.nlm.nih.gov/api?db=gds&api_key={secret}"

        error_body = b"rate limited"
        mock_resp = _make_response(status_code=429, content=error_body)
        mock_resp.iter_content = MagicMock(return_value=iter([error_body]))
        mock_resp.text = error_body.decode()

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            with self.assertRaises(EndpointUnavailable) as ctx:
                http.request("GET", url, qps=0, max_attempts=1)

        exc_text = str(ctx.exception)
        exc_msg = ctx.exception.message
        combined = f"{exc_msg} {exc_text}"
        self.assertNotIn(secret, combined, "API key leaked in retry-exhaustion error")

    def test_error_message_excludes_key_on_invalid_json(self):
        """get_json error must not contain the key."""
        secret = "json-secret-key-555"
        url = f"https://eutils.ncbi.nlm.nih.gov/api?api_key={secret}"

        mock_resp = _make_response(content=b"not json")
        mock_resp.json.side_effect = ValueError("Expecting value")

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            with self.assertRaises(EndpointError) as ctx:
                http.get_json(url, qps=0)

        exc_text = str(ctx.exception)
        exc_msg = ctx.exception.message
        combined = f"{exc_msg} {exc_text}"
        self.assertNotIn(secret, combined, "API key leaked in get_json error")


if __name__ == "__main__":
    unittest.main()
