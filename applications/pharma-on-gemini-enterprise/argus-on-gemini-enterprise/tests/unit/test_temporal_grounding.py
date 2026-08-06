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

import datetime
from unittest.mock import MagicMock

import pytest
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin
from google.genai import types

from app import prompts
from app.agent import app


def test_get_temporal_grounding_instruction() -> None:
    now = datetime.datetime.now(datetime.UTC)
    current_year_str = str(now.year)
    iso_date_str = now.strftime("%Y-%m-%d")

    instruction = prompts.get_temporal_grounding_instruction()

    assert current_year_str in instruction
    assert iso_date_str in instruction
    assert "TEMPORAL CONTEXT & REAL-TIME GROUNDING" in instruction
    assert "AUTHENTIC REAL-WORLD DATES" in instruction
    assert "NEVER insert historical knowledge-cutoff years" in instruction


def test_shared_evidence_rules_contain_temporal_grounding() -> None:
    rules = prompts.SHARED_EVIDENCE_RULES
    assert 'Temporal Grounding & "Latest" Data Rules:' in rules
    assert "Live tool timestamps are ground truth" in rules
    assert "DO NOT hardcode past knowledge-cutoff years" in rules
    assert "Latest financial period" in rules


def test_app_has_temporal_grounding_plugin() -> None:
    assert len(app.plugins) >= 1
    plugins = [p for p in app.plugins if isinstance(p, GlobalInstructionPlugin)]
    assert len(plugins) == 1
    plugin = plugins[0]
    assert plugin.name == "argus_temporal_grounding"
    assert plugin.global_instruction == prompts.get_temporal_grounding_instruction


@pytest.mark.asyncio
async def test_global_instruction_plugin_prepends_to_system_instruction() -> None:
    plugin = GlobalInstructionPlugin(
        global_instruction=prompts.get_temporal_grounding_instruction,
        name="argus_temporal_grounding",
    )

    mock_callback_context = MagicMock(spec=CallbackContext)
    mock_callback_context.state = {}

    llm_request = LlmRequest(
        config=types.GenerateContentConfig(
            system_instruction="Base agent instruction text."
        ),
        contents=[],
    )

    result = await plugin.before_model_callback(
        callback_context=mock_callback_context,
        llm_request=llm_request,
    )

    assert result is None  # Callback allows request to proceed
    sys_instruction = llm_request.config.system_instruction
    assert isinstance(sys_instruction, str)

    now = datetime.datetime.now(datetime.UTC)
    assert str(now.year) in sys_instruction
    assert "Base agent instruction text." in sys_instruction
    # Verify temporal grounding is leading (prepended)
    assert sys_instruction.startswith("TEMPORAL CONTEXT & REAL-TIME GROUNDING")
