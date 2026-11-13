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

"""Unit tests for cli.py retry logic (Finding 4.6 - CWE-670)."""

from unittest.mock import MagicMock, patch

import pytest
from google.genai import types
from google.genai.errors import ServerError

from foldrun_app.cli import run_with_retry


def _make_server_error(status: int = 500) -> ServerError:
    """Create a ServerError instance compatible with google.genai.errors."""
    err = ServerError.__new__(ServerError)
    err.status = status
    err.status_code = status
    err.message = "Internal Server Error"
    return err


@pytest.mark.asyncio
async def test_no_retry_after_mid_stream_event_or_tool_execution():
    """Verify run_with_retry does NOT re-run runner.run_async after events/tool calls occur."""
    tool_executions = []

    class FakeRunner:
        async def run_async(self, user_id, session_id, new_message):
            # Simulate a non-idempotent tool call execution (e.g., submitting a Vertex AI job)
            tool_executions.append("submit_job")
            event = MagicMock()
            event.content = types.Content(
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="af2_submit_monomer", args={"sequence": "ACDEFGHIK"}
                        )
                    )
                ],
                role="model",
            )
            yield event
            # Fail mid-stream with HTTP 500 after tool execution / yielded event
            raise _make_server_error(500)

    runner = FakeRunner()
    message = types.Content(parts=[types.Part(text="submit monomer")], role="user")

    with patch("asyncio.sleep", return_value=None):
        with pytest.raises(ServerError):
            async for _ in run_with_retry(
                runner=runner,
                user_id="test_user",
                session_id="test_session",
                message=message,
                max_retries=3,
            ):
                pass

    # The runner must only have executed once; mid-stream failure must not re-run the turn.
    assert len(tool_executions) == 1


@pytest.mark.asyncio
async def test_retry_allowed_when_failure_occurs_before_any_events():
    """Verify run_with_retry still retries 500 errors when no events have been emitted yet."""
    attempts = 0

    class FakeRunner:
        async def run_async(self, user_id, session_id, new_message):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _make_server_error(500)
            event = MagicMock()
            event.content = types.Content(parts=[types.Part(text="Success on retry")], role="model")
            yield event

    runner = FakeRunner()
    message = types.Content(parts=[types.Part(text="hello")], role="user")

    events = []
    with patch("asyncio.sleep", return_value=None):
        async for ev in run_with_retry(
            runner=runner,
            user_id="test_user",
            session_id="test_session",
            message=message,
            max_retries=3,
        ):
            events.append(ev)

    assert attempts == 2
    assert len(events) == 1
