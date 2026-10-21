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

"""Unit tests for PDF whitepaper rendering and per-session asset security controls."""

import io
from unittest.mock import patch

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from PIL import Image

from app.tools import pdf_renderer
from app.tools.charts import bar_chart
from app.tools.whitepaper import generate_whitepaper_pdf

_TEST_AGENT = LlmAgent(name="test_pdf_agent", model="gemini-3.8-flash")


def _make_valid_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="#0b2545").save(buf, format="PNG")
    return buf.getvalue()


_VALID_PNG = _make_valid_png_bytes()


async def _make_tool_context(
    session_service: InMemorySessionService,
    artifact_service: InMemoryArtifactService,
    *,
    user_id: str,
) -> ToolContext:
    session = await session_service.create_session(app_name="argus", user_id=user_id)
    inv_ctx = InvocationContext(
        session_service=session_service,
        artifact_service=artifact_service,
        invocation_id=f"inv-{session.id}",
        agent=_TEST_AGENT,
        session=session,
    )
    return ToolContext(inv_ctx)


def test_render_whitepaper_pdf_with_valid_asset():
    """Legitimate markdown and in-memory session asset:// images render into PDF bytes."""
    token = "asset://unit_test_chart"
    pdf_bytes = pdf_renderer.render_whitepaper_pdf(
        markdown_text=f"## Executive Summary\n\n![Runway Chart]({token})\n",
        title="Target Assessment",
        subtitle="Phase 2 Oncology Pipeline",
        assets={token: _VALID_PNG},
    )
    assert pdf_bytes.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_blocks_cross_session_asset_leakage_in_whitepaper_pdf():
    """Reproduces b/575340811 PoC: Session 2 cannot embed Session 1's asset://chart_burn."""
    session_svc = InMemorySessionService()
    artifact_svc = InMemoryArtifactService()

    ctx_session_1 = await _make_tool_context(
        session_svc, artifact_svc, user_id="user_1"
    )
    ctx_session_2 = await _make_tool_context(
        session_svc, artifact_svc, user_id="user_2"
    )

    # 1. Session 1 creates a confidential chart under predictable ID "chart_burn"
    chart_res = await bar_chart(
        asset_id="chart_burn",
        title="Target Confidential Burn",
        categories=["Q1", "Q2"],
        values=[10.0, 20.0],
        tool_context=ctx_session_1,
    )
    assert chart_res == {"status": "success", "token": "asset://chart_burn"}

    # 2. Session 2 attempts to render a whitepaper referencing asset://chart_burn
    res_session_2 = await generate_whitepaper_pdf(
        markdown_body="## Financials\n\n![Financials](asset://chart_burn)\n",
        target_name="Competitor Target",
        subtitle="Assessment",
        tool_context=ctx_session_2,
    )
    assert res_session_2["status"] == "error"
    assert "Unauthorized or missing session asset" in res_session_2["message"]

    # 3. Session 1 can render its own chart_burn into a whitepaper PDF artifact
    res_session_1 = await generate_whitepaper_pdf(
        markdown_body="## Financials\n\n![Financials](asset://chart_burn)\n",
        target_name="Confidential Target",
        subtitle="Assessment",
        tool_context=ctx_session_1,
    )
    assert res_session_1["status"] == "success"
    assert res_session_1["bytes"] > 0


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
