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

"""Unit tests for session-scoped ADK ArtifactService asset storage in app.tools.assets."""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext

from app.tools import assets

_TEST_AGENT = LlmAgent(name="test_asset_agent", model="gemini-3.8-flash")


async def _make_tool_context(
    session_service: InMemorySessionService,
    artifact_service: InMemoryArtifactService,
    *,
    user_id: str,
    app_name: str = "argus",
) -> ToolContext:
    session = await session_service.create_session(app_name=app_name, user_id=user_id)
    inv_ctx = InvocationContext(
        session_service=session_service,
        artifact_service=artifact_service,
        invocation_id=f"inv-{session.id}",
        agent=_TEST_AGENT,
        session=session,
    )
    return ToolContext(inv_ctx)


@pytest.mark.asyncio
async def test_save_and_load_asset_roundtrip_and_delta_suppression() -> None:
    """Saved assets persist in ADK ArtifactService, suppress intermediate artifact_delta, and resolve in markdown."""
    session_svc = InMemorySessionService()
    artifact_svc = InMemoryArtifactService()
    ctx = await _make_tool_context(session_svc, artifact_svc, user_id="analyst_1")

    payload = b"\x89PNG\r\n\x1a\npayload_runway"
    token = await assets.save_asset("chart_runway_test", payload, tool_context=ctx)
    assert token == "asset://chart_runway_test"

    # Intermediate chart PNG must not remain in event.actions.artifact_delta
    assert (
        assets.artifact_filename("chart_runway_test") not in ctx.actions.artifact_delta
    )

    loaded = await assets.load_asset("chart_runway_test", tool_context=ctx)
    assert loaded == payload

    md = "Here is ![Runway](asset://chart_runway_test) and ![Unknown](asset://missing)"
    resolved_map = await assets.load_markdown_assets(md, tool_context=ctx)
    assert resolved_map == {"asset://chart_runway_test": payload}


@pytest.mark.asyncio
async def test_cross_session_and_cross_user_isolation() -> None:
    """Assets saved in one session cannot be read or overwritten by another session or user (OSB-v1-292)."""
    session_svc = InMemorySessionService()
    artifact_svc = InMemoryArtifactService()

    ctx_session_1 = await _make_tool_context(
        session_svc, artifact_svc, user_id="user_a"
    )
    ctx_session_2 = await _make_tool_context(
        session_svc, artifact_svc, user_id="user_a"
    )
    ctx_user_b = await _make_tool_context(session_svc, artifact_svc, user_id="user_b")

    secret_png_s1 = b"\x89PNG\r\n\x1a\nconfidential_session_1_burn"
    await assets.save_asset("chart_burn", secret_png_s1, tool_context=ctx_session_1)

    # Another session for the same user or a different user cannot load Session 1's asset
    assert await assets.load_asset("chart_burn", tool_context=ctx_session_2) is None
    assert await assets.load_asset("chart_burn", tool_context=ctx_user_b) is None
    assert (
        await assets.load_markdown_assets(
            "![Burn](asset://chart_burn)", tool_context=ctx_session_2
        )
        == {}
    )
    assert (
        await assets.load_markdown_assets(
            "![Burn](asset://chart_burn)", tool_context=ctx_user_b
        )
        == {}
    )

    # Saving the same predictable asset_id in Session 2 does not overwrite Session 1
    png_s2 = b"\x89PNG\r\n\x1a\nsession_2_burn"
    await assets.save_asset("chart_burn", png_s2, tool_context=ctx_session_2)

    assert (
        await assets.load_asset("chart_burn", tool_context=ctx_session_1)
        == secret_png_s1
    )
    assert await assets.load_asset("chart_burn", tool_context=ctx_session_2) == png_s2


@pytest.mark.asyncio
async def test_user_namespace_prefix_and_traversal_sanitization() -> None:
    """Malicious `user:` prefix or path traversal characters in asset_id cannot escape session scope."""
    session_svc = InMemorySessionService()
    artifact_svc = InMemoryArtifactService()

    ctx_1 = await _make_tool_context(session_svc, artifact_svc, user_id="same_user")
    ctx_2 = await _make_tool_context(session_svc, artifact_svc, user_id="same_user")

    # Attempt to inject ADK `user:` cross-session namespace prefix
    token = await assets.save_asset("user:chart_burn", b"s1_data", tool_context=ctx_1)
    assert token == "asset://user_chart_burn"
    filename = assets.artifact_filename("user:chart_burn")
    assert not filename.startswith("user:")
    assert filename == "asset_user_chart_burn.png"

    # Must not be visible in another session of the same user
    assert await assets.load_asset("user:chart_burn", tool_context=ctx_2) is None
    assert await assets.load_asset("user_chart_burn", tool_context=ctx_2) is None

    # Traversal characters are sanitized
    assert assets.sanitize_asset_id("../../etc/passwd") == "______etc_passwd"


@pytest.mark.asyncio
async def test_missing_tool_context_rejected() -> None:
    """All asset store operations require a non-null ToolContext."""
    with pytest.raises(ValueError, match="tool_context is required"):
        await assets.save_asset("chart_burn", b"data", tool_context=None)
    with pytest.raises(ValueError, match="tool_context is required"):
        await assets.load_asset("chart_burn", tool_context=None)
    with pytest.raises(ValueError, match="tool_context is required"):
        await assets.load_markdown_assets("![x](asset://chart_burn)", tool_context=None)
