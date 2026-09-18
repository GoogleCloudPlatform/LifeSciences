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

"""Unit tests for PDF whitepaper rendering and security controls."""

from unittest.mock import patch

import pytest

from app.tools import pdf_renderer
from app.tools.assets import save_asset

# Minimal valid 1x1 transparent PNG
_ONE_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
    b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_render_whitepaper_pdf_with_valid_asset():
    """Legitimate markdown and registered asset:// images render into PDF bytes."""
    token = save_asset("unit_test_chart", _ONE_PIXEL_PNG)
    pdf_bytes = pdf_renderer.render_whitepaper_pdf(
        markdown_text=f"## Executive Summary\n\n![Runway Chart]({token})\n",
        title="Target Assessment",
        subtitle="Phase 2 Oncology Pipeline",
    )
    assert pdf_bytes.startswith(b"%PDF-")


def test_blocks_remote_http_resource_fetch():
    """Remote HTTP/HTTPS URLs in markdown/HTML must be rejected."""
    with pytest.raises(ValueError, match="Disallowed resource URI scheme"):
        pdf_renderer.render_whitepaper_pdf(
            markdown_text="![ssrf](http://169.254.169.254/latest/meta-data/)",
            title="SSRF Test",
        )


def test_blocks_local_file_inclusion():
    """Local paths outside _ASSET_DIR and path traversal must be rejected."""
    with pytest.raises(ValueError, match="Unauthorized resource path"):
        pdf_renderer.render_whitepaper_pdf(
            markdown_text="![lfi](/etc/passwd)",
            title="LFI Test",
        )


def test_escapes_title_and_subtitle():
    """HTML/CSS in title and subtitle must be HTML-escaped before rendering."""
    captured_html = []

    def fake_create_pdf(src, dest, encoding="utf-8", link_callback=None):
        captured_html.append(src.read() if hasattr(src, "read") else str(src))
        dest.write(b"%PDF-1.4\n")

        class _Result:
            err = 0

        return _Result()

    malicious_title = "Report</h1><style>.confidential{display:none}</style><h1>"
    malicious_subtitle = '<script>alert("xss")</script>'

    with patch.object(pdf_renderer.pisa, "CreatePDF", side_effect=fake_create_pdf):
        pdf_renderer.render_whitepaper_pdf(
            markdown_text="Normal content",
            title=malicious_title,
            subtitle=malicious_subtitle,
        )

    assert len(captured_html) == 1
    rendered = captured_html[0]
    assert "<style>.confidential{display:none}</style>" not in rendered
    assert '<script>alert("xss")</script>' not in rendered
    assert "&lt;style&gt;" in rendered
    assert "&lt;script&gt;" in rendered


def test_strips_dangerous_html_tags_from_body():
    """Dangerous raw HTML tags in markdown body must be stripped."""
    captured_html = []

    def fake_create_pdf(src, dest, encoding="utf-8", link_callback=None):
        captured_html.append(src.read() if hasattr(src, "read") else str(src))
        dest.write(b"%PDF-1.4\n")

        class _Result:
            err = 0

        return _Result()

    malicious_body = (
        "# Heading\n\n"
        "<style>.confidential { display: none; }</style>\n"
        "<script>window.location='http://evil.example'</script>\n"
        '<link rel="stylesheet" href="http://evil.example/override.css" />\n'
        '<iframe src="http://169.254.169.254/"></iframe>\n'
        "Legitimate **bold** text.\n"
    )

    with patch.object(pdf_renderer.pisa, "CreatePDF", side_effect=fake_create_pdf):
        pdf_renderer.render_whitepaper_pdf(
            markdown_text=malicious_body,
            title="Clean Title",
        )

    assert len(captured_html) == 1
    rendered = captured_html[0]
    # Only the single template <style> tag in <head> should exist
    assert rendered.count("<style>") == 1
    assert ".confidential { display: none; }" not in rendered
    assert "<script" not in rendered.lower()
    assert "<link" not in rendered.lower()
    assert "<iframe" not in rendered.lower()
    assert "<strong>bold</strong>" in rendered
