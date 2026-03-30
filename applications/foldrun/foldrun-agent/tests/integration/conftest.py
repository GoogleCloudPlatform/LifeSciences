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

"""Integration test fixtures using ADK InMemoryRunner.

These fixtures create a real agent backed by InMemoryRunner for testing
tool routing and conversation flow with actual LLM calls but mocked
GCP tool backends.
"""

import contextlib
import uuid
from unittest.mock import MagicMock, patch

import pytest
from google.adk.runners import InMemoryRunner, RunConfig
from google.genai import types

# Agent app name — must match the agent's name attribute in agent.py
_APP_NAME = "foldrun_app"

# All skill modules that import get_tool from the registry
_SKILL_MODULES = [
    "foldrun_app.skills.job_submission.tools",
    "foldrun_app.skills.job_management.tools",
    "foldrun_app.skills.results_analysis.tools",
    "foldrun_app.skills.database_queries.tools",
    "foldrun_app.skills.storage_management.tools",
    "foldrun_app.skills.visualization.tools",
]


# ---------------------------------------------------------------------------
# Helper functions (importable by test modules)
# ---------------------------------------------------------------------------


def make_user_message(text: str) -> types.Content:
    """Build a user Content message from text."""
    return types.Content(
        role="user",
        parts=[types.Part(text=text)],
    )


def collect_function_calls(events) -> list[str]:
    """Extract all function call names from a list of events."""
    names = []
    for event in events:
        for fc in event.get_function_calls():
            names.append(fc.name)
    return names


def get_final_text(events) -> str:
    """Extract text from the final response event."""
    for event in reversed(events):
        if event.is_final_response() and event.content and event.content.parts:
            texts = [p.text for p in event.content.parts if p.text]
            if texts:
                return "\n".join(texts)
    return ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_config():
    """RunConfig with bounded LLM calls to prevent runaway loops."""
    return RunConfig(max_llm_calls=10)


@pytest.fixture
def mock_tool_backends():
    """Mock all tool registry calls so tools don't hit real GCP.

    Patches get_tool at every skill module use site so it works
    regardless of Python module import caching order.

    The LLM still sees the tool schema and can make function calls,
    but the tool execution is intercepted with mock responses.
    """

    def _mock_get_tool(name):
        mock = MagicMock()
        mock.run.return_value = {
            "status": "success",
            "message": f"Mock result for {name}",
            "tool_name": name,
        }
        return mock

    # Stack patches for all skill modules + the registry itself
    with contextlib.ExitStack() as stack:
        for module_path in _SKILL_MODULES:
            stack.enter_context(patch(f"{module_path}.get_tool", side_effect=_mock_get_tool))
        # Also patch the registry definition for any direct callers
        stack.enter_context(
            patch(
                "foldrun_app.skills._tool_registry.get_tool",
                side_effect=_mock_get_tool,
            )
        )
        yield


@pytest.fixture
def mock_env_vars(mock_env_vars, monkeypatch):
    """Override parent mock_env_vars for integration tests.

    Configures the genai SDK to use Vertex AI with ADC so integration
    tests make real LLM calls via Application Default Credentials.
    """
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    # Use global endpoint for preview models (Gemini API location only)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    # GCP_REGION must be a valid Compute Engine region (used by GPU quota checks)
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3-flash-preview")
    return mock_env_vars


@pytest.fixture
def runner(mock_env_vars, mock_tool_backends):
    """InMemoryRunner wrapping the real agent with mocked backends.

    The agent is created with real ADK configuration but:
    - GPU auto-detection is mocked
    - Vertex AI / GCS clients are mocked
    - Tool backends return mock success responses
    - LLM calls are real (requires Vertex AI access via ADC)
    """
    with (
        patch.multiple(
            "foldrun_app.models.af2.startup",
            _auto_detect_gpus=MagicMock(),
        ),
        patch("google.cloud.aiplatform.init"),
        patch("google.cloud.storage.Client"),
    ):
        from foldrun_app.agent import create_alphafold_agent

        agent = create_alphafold_agent()

        runner = InMemoryRunner(
            agent=agent,
            app_name=_APP_NAME,
        )
        return runner


@pytest.fixture
async def session_id(runner):
    """Create a unique session in the runner's session service.

    InMemoryRunner requires sessions to be created before run_async
    can be called.  This fixture creates one and returns its ID.
    """
    sid = f"test-session-{uuid.uuid4().hex[:8]}"
    await runner.session_service.create_session(
        app_name=_APP_NAME,
        user_id="test",
        session_id=sid,
    )
    return sid
