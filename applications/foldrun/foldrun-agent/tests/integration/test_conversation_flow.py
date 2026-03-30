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

"""Integration tests for multi-turn conversation flows.

These tests verify the agent maintains context across multiple turns
within the same session and calls the expected sequence of tools.
"""

import pytest

from tests.integration.conftest import (
    collect_function_calls,
    make_user_message,
)


@pytest.mark.integration
class TestConversationFlow:
    """Multi-turn conversation tests using the same session."""

    async def test_submit_then_check_status(self, runner, session_id, run_config):
        """Submit a job in turn 1, then ask for status in turn 2.

        The agent should remember the job context and call check_job_status
        in the second turn.
        """
        # Turn 1: Submit a prediction
        turn1_events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(
                "Submit a monomer prediction for:\n"
                ">hemoglobin_beta\n"
                "MVHLTPEEKSAVTALWGKVNVDEVGGEALGRLLVVYPWTQRFFESFGDLSTPDAVMGNPKVKAHGK"
            ),
            run_config=run_config,
        ):
            turn1_events.append(event)

        turn1_calls = collect_function_calls(turn1_events)
        assert "submit_af2_monomer_prediction" in turn1_calls

        # Turn 2: Ask about status (agent should use context from turn 1)
        turn2_events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("What's the status of the job I just submitted?"),
            run_config=run_config,
        ):
            turn2_events.append(event)

        turn2_calls = collect_function_calls(turn2_events)
        status_tools = {"check_job_status", "list_jobs"}
        assert status_tools & set(turn2_calls), (
            f"Expected one of {status_tools} in turn 2, got {turn2_calls}"
        )

    async def test_check_db_before_submit(self, runner, session_id, run_config):
        """Query AlphaFold DB first, then submit a prediction.

        Verifies the agent can handle a two-step research-then-submit flow.
        """
        # Turn 1: Check if structure already exists
        turn1_events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Does UniProt P69905 have a structure in AlphaFold DB?"),
            run_config=run_config,
        ):
            turn1_events.append(event)

        turn1_calls = collect_function_calls(turn1_events)
        db_tools = {
            "query_alphafold_db_prediction",
            "query_alphafold_db_summary",
            "query_alphafold_db_annotations",
        }
        assert db_tools & set(turn1_calls), f"Expected a DB query tool in turn 1, got {turn1_calls}"

        # Turn 2: Submit prediction anyway
        turn2_events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(
                "Submit a monomer prediction for it anyway. "
                "Here is the sequence:\n"
                ">P69905_hemoglobin\n"
                "MVHLTPEEKSAVTALWGKVNVDEVGGEALGRLLVVYPWTQRFFESFGDLSTPDAVMGNPKVKAHGK"
            ),
            run_config=run_config,
        ):
            turn2_events.append(event)

        turn2_calls = collect_function_calls(turn2_events)
        assert "submit_af2_monomer_prediction" in turn2_calls

        # Turn 2: Set up infrastructure
        turn2_events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Go ahead and set up everything that's missing"),
            run_config=run_config,
        ):
            turn2_events.append(event)

        turn2_calls = collect_function_calls(turn2_events)
        assert "setup_infrastructure" in turn2_calls
