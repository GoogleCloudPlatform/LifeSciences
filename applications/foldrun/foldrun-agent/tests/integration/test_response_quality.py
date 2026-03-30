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

"""Integration tests for response content quality.

These tests verify that the agent's textual responses contain expected
information, not just that the right tools are called.
"""

import pytest

from tests.integration.conftest import (
    get_final_text,
    make_user_message,
)


@pytest.mark.integration
class TestResponseQuality:
    """Verify the agent produces useful, well-structured responses."""

    async def test_greeting_includes_config_table(self, runner, session_id, run_config):
        """Initial greeting should include the configuration table.

        The agent instruction says to show Project ID, Region, GCS Bucket,
        etc. in a table on the first message.
        """
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Hello!"),
            run_config=run_config,
        ):
            events.append(event)

        text = get_final_text(events)
        assert text, "Agent should produce a text response"

        # The agent instruction specifies a table with these fields
        # Check for at least Project ID and Region in the response
        text_lower = text.lower()
        assert "project" in text_lower, f"Greeting should mention project. Got: {text[:200]}"
        assert "region" in text_lower, f"Greeting should mention region. Got: {text[:200]}"

    async def test_invalid_sequence_guidance(self, runner, session_id, run_config):
        """Submitting an invalid sequence should produce helpful guidance.

        The agent should either explain the error or ask for a valid
        FASTA sequence rather than blindly calling the tool.
        """
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Predict the structure of this: ABC123XYZ"),
            run_config=run_config,
        ):
            events.append(event)

        text = get_final_text(events)
        assert text, "Agent should produce a text response"

        # The response should mention sequence format issues or FASTA
        text_lower = text.lower()
        has_guidance = (
            "fasta" in text_lower
            or "sequence" in text_lower
            or "format" in text_lower
            or "invalid" in text_lower
            or "amino acid" in text_lower
            or "header" in text_lower
        )
        assert has_guidance, f"Response should contain FASTA/sequence guidance. Got: {text[:300]}"
