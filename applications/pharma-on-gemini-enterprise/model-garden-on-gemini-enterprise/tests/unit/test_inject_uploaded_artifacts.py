# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import AsyncMock, MagicMock

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.agent import _inject_uploaded_artifacts


@pytest.mark.asyncio
async def test_multi_turn_does_not_duplicate_attachments() -> None:
    """Verify that multi-turn conversations do not duplicate uploaded attachments."""
    ctx = MagicMock()
    ctx.list_artifacts = AsyncMock(return_value=["doc.pdf"])
    ctx.load_artifact = AsyncMock(
        return_value=types.Part(
            inline_data=types.Blob(mime_type="application/pdf", data=b"pdf-bytes")
        )
    )

    turn1_user = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text="Please analyze <start_of_user_uploaded_file: doc.pdf>"
            )
        ],
    )
    req = LlmRequest(contents=[turn1_user])

    # Turn 1
    await _inject_uploaded_artifacts(ctx, req)
    inline_parts = [p for p in turn1_user.parts if p.inline_data is not None]
    assert len(inline_parts) == 1
    assert ctx.load_artifact.await_count == 1

    # Simulate 3 subsequent turns in the same session without new attachments
    for turn in range(2, 5):
        req.contents.append(
            types.Content(
                role="model",
                parts=[types.Part.from_text(text=f"Response {turn - 1}")],
            )
        )
        req.contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=f"Follow-up question {turn}")],
            )
        )
        await _inject_uploaded_artifacts(ctx, req)
        inline_parts = [p for p in turn1_user.parts if p.inline_data is not None]
        assert len(inline_parts) == 1, f"Duplicate attachment injected on turn {turn}"

    assert ctx.load_artifact.await_count == 1


@pytest.mark.asyncio
async def test_existing_inline_data_prevents_duplication_even_with_marker() -> None:
    """Verify that if a Content already has the inline_data blob, it is not duplicated even if the marker text is present."""
    ctx = MagicMock()
    ctx.list_artifacts = AsyncMock(return_value=["doc.pdf"])
    blob = types.Blob(mime_type="application/pdf", data=b"pdf-bytes")
    ctx.load_artifact = AsyncMock(return_value=types.Part(inline_data=blob))

    content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text="Analyze <start_of_user_uploaded_file: doc.pdf>"),
            types.Part(
                inline_data=types.Blob(mime_type="application/pdf", data=b"pdf-bytes")
            ),
        ],
    )
    req = LlmRequest(contents=[content])

    await _inject_uploaded_artifacts(ctx, req)
    inline_parts = [p for p in content.parts if p.inline_data is not None]
    assert len(inline_parts) == 1


@pytest.mark.asyncio
async def test_unresolved_marker_is_preserved() -> None:
    """Verify that unresolved markers are preserved in message text."""
    ctx = MagicMock()
    ctx.list_artifacts = AsyncMock(return_value=[])
    ctx.load_artifact = AsyncMock(return_value=None)

    content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text="Check <start_of_user_uploaded_file: missing.pdf>"
            )
        ],
    )
    req = LlmRequest(contents=[content])

    await _inject_uploaded_artifacts(ctx, req)
    assert content.parts[0].text == "Check <start_of_user_uploaded_file: missing.pdf>"
    assert len([p for p in content.parts if p.inline_data is not None]) == 0
