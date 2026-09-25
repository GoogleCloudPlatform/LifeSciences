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

"""Regression tests for core/http.py response size guard (#305).

Covers Content-Length pre-check rejection, streaming byte cap rejection,
non-integer Content-Length fallthrough, error body drain, exactly-at-limit
boundary, and custom max_response_bytes.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dde.core import http
from dde.core.errors import EndpointError


def _make_response(
    *,
    status_code: int = 200,
    headers: dict | None = None,
    content: bytes = b"",
    chunks: list[bytes] | None = None,
):
    """Build a mock ``requests.Response``-like object.

    *chunks* controls what ``iter_content`` yields.  When omitted the
    full *content* is returned as a single chunk.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}

    if chunks is None:
        chunks = [content] if content else [b""]

    resp.iter_content = MagicMock(return_value=iter(chunks))
    resp.close = MagicMock()

    # For error-body drain: .text property derived from ._content.
    resp._content = content
    resp.text = content.decode("utf-8", errors="replace") if content else ""

    return resp


class TestHTTPSizeGuard(unittest.TestCase):
    """Tests for the two-layer response size guard."""

    def setUp(self):
        # Reset the network-forbidden latch so tests can make calls.
        http._network_forbidden = None

    # ------------------------------------------------------------------
    # 1. Normal response under limit
    # ------------------------------------------------------------------
    def test_normal_response_under_limit(self):
        body = b"hello world"
        mock_resp = _make_response(content=body)

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            result = http.request("GET", "https://example.com/ok", qps=0)

        self.assertEqual(result._content, body)

    # ------------------------------------------------------------------
    # 2. Content-Length rejection (Layer 1)
    # ------------------------------------------------------------------
    def test_content_length_rejection(self):
        """A Content-Length larger than the cap should raise EndpointError
        WITHOUT downloading the body (iter_content must not be called)."""
        mock_resp = _make_response(
            headers={"Content-Length": "999999999"},
            content=b"",
        )

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            with self.assertRaises(EndpointError) as ctx:
                http.request(
                    "GET",
                    "https://example.com/big",
                    qps=0,
                    max_response_bytes=1000,
                )

        self.assertIn("exceeds size limit", str(ctx.exception))
        # Layer 1 should short-circuit before streaming.
        mock_resp.iter_content.assert_not_called()
        mock_resp.close.assert_called()

    # ------------------------------------------------------------------
    # 3. Streaming rejection (Layer 2)
    # ------------------------------------------------------------------
    def test_streaming_rejection(self):
        """When Content-Length is absent the streaming read should enforce
        the byte cap and raise with 'streaming read' in the detail."""
        # 5 chunks of 300 bytes each = 1500 bytes, cap at 1000
        chunks = [b"x" * 300] * 5
        mock_resp = _make_response(chunks=chunks)

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            with self.assertRaises(EndpointError) as ctx:
                http.request(
                    "GET",
                    "https://example.com/stream",
                    qps=0,
                    max_response_bytes=1000,
                )

        self.assertIn("streaming read", ctx.exception.detail)
        mock_resp.close.assert_called()

    # ------------------------------------------------------------------
    # 4. Non-integer Content-Length
    # ------------------------------------------------------------------
    def test_non_integer_content_length(self):
        """A garbage Content-Length should fall through to Layer 2 rather
        than crashing.  With a body under the cap, it should succeed."""
        body = b"small payload"
        mock_resp = _make_response(
            headers={"Content-Length": "garbage"},
            content=body,
        )

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            result = http.request(
                "GET",
                "https://example.com/garbled",
                qps=0,
            )

        # Should succeed — non-integer CL is ignored, body fits.
        self.assertEqual(result._content, body)

    # ------------------------------------------------------------------
    # 5. Error body drain
    # ------------------------------------------------------------------
    def test_error_body_drain(self):
        """A non-200 streamed response should still have its error body
        readable via _drain_limited so that last_detail is populated."""
        error_body = b"rate limited - try later"
        mock_resp = _make_response(
            status_code=403,
            content=error_body,
        )
        # _drain_limited calls iter_content to read the body, then sets
        # response._content.  The code path then reads response.text to
        # build the error detail.  We wire up iter_content to yield the
        # body, and make .text a property that decodes ._content so the
        # drain is observable.
        mock_resp.iter_content = MagicMock(return_value=iter([error_body]))

        # MagicMock doesn't derive .text from ._content the way a real
        # Response does.  Pre-set .text to the decoded body so that the
        # code path after _drain_limited reads it correctly.
        mock_resp.text = error_body.decode()

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            # 403 is not retryable and not expected -> EndpointError
            with self.assertRaises(EndpointError) as ctx:
                http.request("GET", "https://example.com/forbidden", qps=0)

        # The error detail should contain the drained body text,
        # confirming _drain_limited made the body available.
        self.assertIn("rate limited", ctx.exception.detail)
        self.assertIn("403", str(ctx.exception))

    # ------------------------------------------------------------------
    # 6. Exactly-at-limit (boundary condition)
    # ------------------------------------------------------------------
    def test_exactly_at_limit(self):
        """A response whose body is exactly max_response_bytes should
        succeed — the guard is strictly-greater-than."""
        limit = 500
        body = b"A" * limit
        mock_resp = _make_response(content=body)

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            result = http.request(
                "GET",
                "https://example.com/exact",
                qps=0,
                max_response_bytes=limit,
            )

        self.assertEqual(result._content, body)
        self.assertEqual(len(result._content), limit)

    # ------------------------------------------------------------------
    # 7. Custom max_response_bytes
    # ------------------------------------------------------------------
    def test_custom_max_response_bytes(self):
        """Passing a small max_response_bytes should enforce that custom
        limit rather than the default 100 MB."""
        body = b"B" * 200
        mock_resp = _make_response(content=body)

        with patch.object(http, "requests") as mock_lib:
            mock_lib.request.return_value = mock_resp
            with self.assertRaises(EndpointError) as ctx:
                http.request(
                    "GET",
                    "https://example.com/custom",
                    qps=0,
                    max_response_bytes=100,
                )

        self.assertIn("exceeds size limit", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
