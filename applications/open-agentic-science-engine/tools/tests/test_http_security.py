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

"""Regression tests for http.py security fixes (#213, #214).

#213 — Streaming transport exceptions must be caught by the retry loop
        and wrapped into EndpointUnavailable, not allowed to escape raw.
#214 — Credential redaction must cover URL authority credentials,
        expanded token parameter names, and error response bodies.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dde.core import http
from dde.core.errors import EndpointError, EndpointUnavailable
from dde.core.http import _sanitize_text, _sanitize_url

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    *,
    status_code: int = 200,
    headers: dict | None = None,
    content: bytes = b"",
    chunks: list[bytes] | None = None,
    iter_content_side_effect=None,
):
    """Build a mock ``requests.Response``-like object.

    *iter_content_side_effect* can be set to make ``iter_content`` raise
    after yielding zero or more chunks.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}

    if iter_content_side_effect is not None:
        resp.iter_content = MagicMock(side_effect=iter_content_side_effect)
    elif chunks is not None:
        resp.iter_content = MagicMock(return_value=iter(chunks))
    else:
        resp.iter_content = MagicMock(
            return_value=iter([content] if content else [b""])
        )

    resp.close = MagicMock()
    resp._content = content
    resp.text = content.decode("utf-8", errors="replace") if content else ""
    return resp


# ===================================================================
# Issue #213 — Streaming exceptions bypass retry logic
# ===================================================================


class TestStreamingRetry(unittest.TestCase):
    """Transport errors during response body streaming must enter the
    retry loop and, when retries are exhausted, raise
    ``EndpointUnavailable`` rather than the raw exception."""

    def setUp(self):
        http._network_forbidden = None

    # ------------------------------------------------------------------
    # 1. ChunkedEncodingError during iter_content retries and wraps
    # ------------------------------------------------------------------
    def test_chunked_encoding_error_retries_and_wraps(self):
        """A ChunkedEncodingError on the first chunk must be retried
        ``max_attempts`` times and then raise EndpointUnavailable."""

        def _exploding_iter(*_a, **_kw):
            yield b"partial"
            raise ConnectionError("connection reset during chunked read")

        call_count = 0

        def _make_exploding_response(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            return _make_response(
                content=b"",
                iter_content_side_effect=_exploding_iter,
            )

        with patch.object(http, "requests") as mock_lib, patch("time.sleep"):
            mock_lib.request.side_effect = _make_exploding_response
            with self.assertRaises(EndpointUnavailable) as ctx:
                http.request(
                    "GET",
                    "https://api.example.com/stream",
                    qps=0,
                    max_attempts=3,
                )

        # All 3 attempts must have been made.
        self.assertEqual(call_count, 3)
        self.assertIn("transport failure", str(ctx.exception))

    # ------------------------------------------------------------------
    # 2. Streaming error on first attempt retries (not immediate abort)
    # ------------------------------------------------------------------
    def test_streaming_error_does_not_abort_immediately(self):
        """If iter_content fails on attempt 1 but succeeds on attempt 2,
        the call must succeed — proving retry logic is engaged."""

        attempt = 0

        def _request_side_effect(*_a, **_kw):
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                # First attempt: response OK, but streaming fails.
                def _exploding(*_a2, **_kw2):
                    raise ConnectionError("stream reset")

                return _make_response(
                    iter_content_side_effect=_exploding,
                )
            # Second attempt: normal response.
            return _make_response(content=b"hello")

        with patch.object(http, "requests") as mock_lib, patch("time.sleep"):
            mock_lib.request.side_effect = _request_side_effect
            result = http.request(
                "GET", "https://api.example.com/data", qps=0, max_attempts=3
            )

        self.assertEqual(result._content, b"hello")
        self.assertEqual(attempt, 2)

    # ------------------------------------------------------------------
    # 3. Response is closed on streaming failure
    # ------------------------------------------------------------------
    def test_response_closed_on_streaming_error(self):
        """When iter_content raises, the response handle must be closed
        to prevent socket leaks."""
        responses = []

        def _request_side_effect(*_a, **_kw):
            def _exploding(*_a2, **_kw2):
                raise OSError("read timeout")

            resp = _make_response(iter_content_side_effect=_exploding)
            responses.append(resp)
            return resp

        with patch.object(http, "requests") as mock_lib, patch("time.sleep"):
            mock_lib.request.side_effect = _request_side_effect
            with self.assertRaises(EndpointUnavailable):
                http.request("GET", "https://api.example.com/r", qps=0, max_attempts=2)

        # Every failed response must have had .close() called.
        for resp in responses:
            resp.close.assert_called()

    # ------------------------------------------------------------------
    # 4. Streaming error during error-body drain retries
    # ------------------------------------------------------------------
    def test_drain_error_retries(self):
        """If _drain_limited (called for non-200 responses) raises during
        iter_content, the retry loop must catch and retry."""
        call_count = 0

        def _request_side_effect(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:

                def _exploding(*_a2, **_kw2):
                    raise ConnectionError("drain failed")

                return _make_response(
                    status_code=500,
                    iter_content_side_effect=_exploding,
                )
            # Third attempt: clean 200
            return _make_response(content=b"ok")

        with patch.object(http, "requests") as mock_lib, patch("time.sleep"):
            mock_lib.request.side_effect = _request_side_effect
            result = http.request(
                "GET", "https://api.example.com/r", qps=0, max_attempts=3
            )

        self.assertEqual(result._content, b"ok")
        self.assertEqual(call_count, 3)


# ===================================================================
# Issue #214 — Incomplete credential redaction
# ===================================================================


class TestSanitizeUrl(unittest.TestCase):
    """_sanitize_url must redact URL authority credentials and all
    sensitive query-string parameter names."""

    # ------------------------------------------------------------------
    # 5. URL authority credentials (user:pass@host)
    # ------------------------------------------------------------------
    def test_authority_credentials_redacted(self):
        url = "https://user:password123@api.example.com/data?x=1"
        result = _sanitize_url(url)
        self.assertNotIn("user", result)
        self.assertNotIn("password123", result)
        self.assertIn("api.example.com", result)
        self.assertIn("/data", result)

    # ------------------------------------------------------------------
    # 6. URL authority with only username (no password)
    # ------------------------------------------------------------------
    def test_authority_username_only_redacted(self):
        url = "https://apiuser@api.example.com/v2"
        result = _sanitize_url(url)
        self.assertNotIn("apiuser", result)
        self.assertIn("api.example.com", result)

    # ------------------------------------------------------------------
    # 7. URL authority with port preserved
    # ------------------------------------------------------------------
    def test_authority_port_preserved(self):
        url = "https://user:pass@host.example.com:8443/path"
        result = _sanitize_url(url)
        self.assertNotIn("user", result)
        self.assertNotIn("pass@", result)
        self.assertIn("host.example.com:8443", result)
        self.assertIn("/path", result)

    # ------------------------------------------------------------------
    # 8. access_token parameter redacted
    # ------------------------------------------------------------------
    def test_access_token_redacted(self):
        url = "https://api.example.com/data?access_token=secret_val"
        result = _sanitize_url(url)
        self.assertNotIn("secret_val", result)
        self.assertIn("access_token=<REDACTED>", result)

    # ------------------------------------------------------------------
    # 9. password parameter redacted
    # ------------------------------------------------------------------
    def test_password_param_redacted(self):
        url = "https://example.com/login?password=hunter2&user=bob"
        result = _sanitize_url(url)
        self.assertNotIn("hunter2", result)
        self.assertIn("password=<REDACTED>", result)
        # Non-sensitive param preserved.
        self.assertIn("user=bob", result)

    # ------------------------------------------------------------------
    # 10. api-key (hyphenated) redacted
    # ------------------------------------------------------------------
    def test_api_key_hyphenated_redacted(self):
        url = "https://example.com/api?api-key=k3y_value"
        result = _sanitize_url(url)
        self.assertNotIn("k3y_value", result)
        self.assertIn("api-key=<REDACTED>", result)

    # ------------------------------------------------------------------
    # 11. Multiple sensitive params all redacted
    # ------------------------------------------------------------------
    def test_multiple_sensitive_params(self):
        url = (
            "https://example.com/q"
            "?client_secret=cs1&refresh_token=rt1&session_token=st1"
        )
        result = _sanitize_url(url)
        self.assertNotIn("cs1", result)
        self.assertNotIn("rt1", result)
        self.assertNotIn("st1", result)

    # ------------------------------------------------------------------
    # 12. Original params still redacted (backward compat)
    # ------------------------------------------------------------------
    def test_original_params_still_redacted(self):
        url = "https://example.com/?api_key=ak&apikey=ak2&key=k&token=t&secret=s"
        result = _sanitize_url(url)
        for val in ("ak", "ak2", "k", "t", "s"):
            self.assertNotIn(f"={val}", result)

    # ------------------------------------------------------------------
    # 13. URL without credentials unchanged
    # ------------------------------------------------------------------
    def test_clean_url_unchanged(self):
        url = "https://api.example.com/data?format=json&limit=10"
        self.assertEqual(_sanitize_url(url), url)


class TestSanitizeText(unittest.TestCase):
    """_sanitize_text must redact credentials in free-form error bodies."""

    # ------------------------------------------------------------------
    # 14. Embedded URL with authority creds in error body
    # ------------------------------------------------------------------
    def test_embedded_url_authority_redacted(self):
        body = "Error connecting to https://admin:s3cret@db.internal:5432/main"
        result = _sanitize_text(body)
        self.assertNotIn("admin", result)
        self.assertNotIn("s3cret", result)
        self.assertIn("db.internal:5432", result)

    # ------------------------------------------------------------------
    # 15. Server-echoed token in error body
    # ------------------------------------------------------------------
    def test_server_echoed_token_redacted(self):
        body = '{"error": "invalid request", "url": "https://api.ex.com/x?access_token=LEAKED"}'
        result = _sanitize_text(body)
        self.assertNotIn("LEAKED", result)


class TestErrorBodySanitization(unittest.TestCase):
    """Error response bodies attached to EndpointError/EndpointUnavailable
    must be sanitized before inclusion in exception detail."""

    def setUp(self):
        http._network_forbidden = None

    # ------------------------------------------------------------------
    # 16. Non-retryable error body with credentials is sanitized
    # ------------------------------------------------------------------
    def test_non_retryable_error_body_sanitized(self):
        """When a 403 response body echoes credentials, the detail
        attached to EndpointError must have them redacted."""
        error_body = (
            b'{"error":"bad token","url":"https://x.com/a?access_token=LEAKED"}'
        )
        resp = _make_response(
            status_code=403,
            content=error_body,
        )
        resp.iter_content = MagicMock(return_value=iter([error_body]))
        resp.text = error_body.decode()

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = resp
            with self.assertRaises(EndpointError) as ctx:
                http.request("GET", "https://example.com/api", qps=0)

        self.assertNotIn("LEAKED", ctx.exception.detail or "")
        self.assertNotIn("LEAKED", str(ctx.exception))

    # ------------------------------------------------------------------
    # 17. Retryable error body with credentials is sanitized
    # ------------------------------------------------------------------
    def test_retryable_error_body_sanitized(self):
        """When a 503 response body echoes credentials, the detail
        on the final EndpointUnavailable must have them redacted."""
        error_body = b"retry: see https://user:pwd@internal/status"
        resp = _make_response(
            status_code=503,
            content=error_body,
        )
        resp.iter_content = MagicMock(return_value=iter([error_body]))
        resp.text = error_body.decode()

        with patch.object(http, "requests") as mock_lib, patch("time.sleep"):
            mock_lib.request.return_value = resp
            with self.assertRaises(EndpointUnavailable) as ctx:
                http.request("GET", "https://example.com/api", qps=0, max_attempts=1)

        self.assertNotIn("pwd", ctx.exception.detail or "")
        self.assertNotIn("user:", ctx.exception.detail or "")


if __name__ == "__main__":
    unittest.main()
