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

"""Integration tests for agent tool routing.

Each test sends a natural language prompt via InMemoryRunner and verifies
the agent selects the correct tool(s).  GCP backends are mocked — only
the LLM routing decision is exercised.
"""

import pytest

from tests.integration.conftest import (
    collect_function_calls,
    make_user_message,
)


@pytest.mark.integration
class TestToolRouting:
    """Verify the agent routes prompts to the correct FunctionTool."""

    # ------------------------------------------------------------------ #
    # Job Submission
    # ------------------------------------------------------------------ #

    async def test_monomer_submission(self, runner, session_id, run_config):
        """Monomer prediction prompt routes to submit_af2_monomer_prediction."""
        prompt = (
            "Submit a monomer prediction for this sequence:\n"
            ">test_protein\n"
            "MKTIALSYIFCLVFADYKDDDDKGSAATTDSTNGEEEEE"
        )
        # Turn 1: agent shows confirmation plan
        async for _ in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(prompt),
            run_config=run_config,
        ):
            pass

        # Turn 2: confirm submission
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Yes, submit it."),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "submit_af2_monomer_prediction" in calls

    async def test_multimer_submission(self, runner, session_id, run_config):
        """Multimer prediction prompt routes to submit_af2_multimer_prediction."""
        prompt = (
            "Submit a multimer prediction for these two chains:\n"
            ">chain_A\nMKTIALSYIFCLVFADYKDDDDK\n"
            ">chain_B\nACDEFGHIKLMNPQRSTVWYACDE"
        )
        # Turn 1: agent shows confirmation plan
        async for _ in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(prompt),
            run_config=run_config,
        ):
            pass

        # Turn 2: confirm submission
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Yes, submit it."),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "submit_af2_multimer_prediction" in calls

    # ------------------------------------------------------------------ #
    # Job Management
    # ------------------------------------------------------------------ #

    async def test_check_status(self, runner, session_id, run_config):
        """Status inquiry routes to check_job_status."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("What is the status of job pipeline-run-abc123?"),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "check_job_status" in calls

    async def test_list_jobs(self, runner, session_id, run_config):
        """Listing jobs routes to list_jobs."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Show me all running jobs"),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "list_jobs" in calls

    async def test_gpu_quota(self, runner, session_id, run_config):
        """GPU quota inquiry routes to check_gpu_quota."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Check GPU quota availability"),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "check_gpu_quota" in calls

    async def test_delete_job(self, runner, session_id, run_config):
        """Job deletion prompt routes to delete_job."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Delete job pipeline-run-abc123. Yes I confirm."),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "delete_job" in calls

    async def test_get_job_details(self, runner, session_id, run_config):
        """Job details prompt routes to get_job_details."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Get me the full details for job pipeline-run-abc123"),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "get_job_details" in calls

    # ------------------------------------------------------------------ #
    # Results & Analysis
    # ------------------------------------------------------------------ #

    async def test_analyze_results(self, runner, session_id, run_config):
        """Analysis request routes to analyze_job."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Analyze pipeline-run-abc123 in detail"),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        # Agent may pick analyze_job, analyze_prediction_quality, or
        # analyze_job_parallel depending on phrasing — any is acceptable.
        analysis_tools = {
            "analyze_job",
            "analyze_prediction_quality",
            "analyze_job_parallel",
        }
        assert analysis_tools & set(calls), f"Expected one of {analysis_tools}, got {calls}"

    async def test_open_viewer(self, runner, session_id, run_config):
        """3D viewer prompt routes to open_structure_viewer."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(
                "Open the 3D structure viewer for job pipeline-run-abc123"
            ),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "open_structure_viewer" in calls

    # ------------------------------------------------------------------ #
    # Database Queries
    # ------------------------------------------------------------------ #

    async def test_alphafold_db_query(self, runner, session_id, run_config):
        """AlphaFold DB query routes to a query_alphafold_db_* tool."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(
                "Check if UniProt P69905 (hemoglobin) already has a structure in AlphaFold DB"
            ),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        db_tools = {
            "query_alphafold_db_prediction",
            "query_alphafold_db_summary",
            "query_alphafold_db_annotations",
        }
        assert db_tools & set(calls), f"Expected one of {db_tools}, got {calls}"

    # ------------------------------------------------------------------ #
    # Storage Management
    # ------------------------------------------------------------------ #

    async def test_cleanup(self, runner, session_id, run_config):
        """Cleanup request routes to cleanup_gcs_files."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(
                "Use the cleanup_gcs_files tool to search for GCS files "
                "associated with job pipeline-run-abc123"
            ),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "cleanup_gcs_files" in calls

    async def test_find_orphaned(self, runner, session_id, run_config):
        """Orphan search routes to find_orphaned_gcs_files."""
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(
                "Find any orphaned GCS files that don't have Vertex AI jobs"
            ),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "find_orphaned_gcs_files" in calls

    # ------------------------------------------------------------------ #
    # Genetic Databases
    # ------------------------------------------------------------------ #

    async def test_mmseqs2_monomer_submission(self, runner, session_id, run_config):
        """MMseqs2 monomer submission routes to submit_af2_monomer_prediction."""
        prompt = (
            "Submit a monomer prediction using MMseqs2 GPU-accelerated MSA "
            "for this sequence:\n"
            ">test_mmseqs2\n"
            "MKTIALSYIFCLVFADYKDDDDKGSAATTDSTNGEEEEE"
        )
        # Turn 1: agent shows confirmation plan
        async for _ in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message(prompt),
            run_config=run_config,
        ):
            pass

        # Turn 2: confirm submission
        events = []
        async for event in runner.run_async(
            user_id="test",
            session_id=session_id,
            new_message=make_user_message("Yes, submit it."),
            run_config=run_config,
        ):
            events.append(event)

        calls = collect_function_calls(events)
        assert "submit_af2_monomer_prediction" in calls

    # ------------------------------------------------------------------ #
    # Infrastructure
    # ------------------------------------------------------------------ #
