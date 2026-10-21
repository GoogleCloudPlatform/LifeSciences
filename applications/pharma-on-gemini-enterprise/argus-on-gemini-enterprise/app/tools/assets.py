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

"""Per-session asset store for generated images/charts backed by ADK ArtifactService.

Chart and infographic tools persist PNG bytes as session-scoped ADK artifacts via
`tool_context.save_artifact` and return a lightweight `asset://<id>` token. The
whitepaper markdown references assets with standard markdown image syntax, e.g.
`![Cash runway](asset://chart_runway)`, and `generate_whitepaper_pdf` loads only
the active user/session's artifacts via `tool_context.load_artifact` for in-memory
PDF rendering.
"""

from __future__ import annotations

import re

from google.adk.tools import ToolContext
from google.genai import types

_TOKEN_RE = re.compile(r"asset://([A-Za-z0-9_\-]+)")


def sanitize_asset_id(asset_id: str) -> str:
    """Sanitize an asset identifier to safe token characters (`[A-Za-z0-9_-]`)."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "_", asset_id)
    return cleaned or "asset"


def artifact_filename(asset_id: str) -> str:
    """Return a session-scoped ADK artifact filename (never `user:`-prefixed)."""
    return f"asset_{sanitize_asset_id(asset_id)}.png"


async def save_asset(
    asset_id: str,
    png_bytes: bytes,
    tool_context: ToolContext | None,
) -> str:
    """Persist PNG bytes in the current session's ADK ArtifactService and return `asset://<id>`."""
    if tool_context is None:
        raise ValueError(
            "tool_context is required to save session-scoped assets in ArtifactService."
        )
    clean_id = sanitize_asset_id(asset_id)
    filename = artifact_filename(clean_id)
    part = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
    await tool_context.save_artifact(filename=filename, artifact=part)

    # Intermediate chart/infographic PNGs are embedded into the whitepaper PDF;
    # suppress their individual artifact_delta entries so Gemini Enterprise only
    # renders the final whitepaper PDF card.
    actions = getattr(tool_context, "actions", None)
    artifact_delta = getattr(actions, "artifact_delta", None)
    if isinstance(artifact_delta, dict):
        artifact_delta.pop(filename, None)

    return f"asset://{clean_id}"


async def load_asset(
    asset_id: str,
    tool_context: ToolContext | None,
) -> bytes | None:
    """Load PNG bytes for `asset_id` from the current session's ADK ArtifactService."""
    if tool_context is None:
        raise ValueError(
            "tool_context is required to load session-scoped assets from ArtifactService."
        )
    filename = artifact_filename(asset_id)
    part = await tool_context.load_artifact(filename=filename)
    if part is not None and getattr(part, "inline_data", None) is not None:
        return part.inline_data.data
    return None


async def load_markdown_assets(
    markdown_text: str,
    tool_context: ToolContext | None,
) -> dict[str, bytes]:
    """Load all `asset://<id>` tokens referenced in `markdown_text` for the current session."""
    if tool_context is None:
        raise ValueError(
            "tool_context is required to load session-scoped assets from ArtifactService."
        )
    assets: dict[str, bytes] = {}
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(markdown_text):
        clean_id = sanitize_asset_id(match.group(1))
        if clean_id in seen:
            continue
        seen.add(clean_id)
        data = await load_asset(clean_id, tool_context)
        if data is not None:
            assets[f"asset://{clean_id}"] = data
    return assets
