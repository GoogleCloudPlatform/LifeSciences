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

"""Tests for the tool registry singleton."""

from unittest.mock import patch

import pytest


class TestToolRegistry:
    """Tests for foldrun_app.skills._tool_registry."""

    def _reset_registry(self):
        """Reset the registry global state between tests."""
        import foldrun_app.skills._tool_registry as reg

        reg._agents = {}
        reg._initialized = False

    def test_get_tool_initializes_on_first_call(self, mock_env_vars):
        """Lazy init works — first call triggers _initialize_all_tools."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            import foldrun_app.skills._tool_registry as reg
            from foldrun_app.skills._tool_registry import get_tool

            # Before call, not initialized
            assert reg._initialized is False

            tool = get_tool("af2_submit_monomer")
            assert reg._initialized is True
            assert tool is not None

    def test_get_tool_returns_same_instance(self, mock_env_vars):
        """Singleton behavior — same tool instance returned on repeated calls."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.skills._tool_registry import get_tool

            tool1 = get_tool("af2_submit_monomer")
            tool2 = get_tool("af2_submit_monomer")
            assert tool1 is tool2

    def test_get_tool_unknown_raises_keyerror(self, mock_env_vars):
        """Unknown tool name raises KeyError."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.skills._tool_registry import get_tool

            with pytest.raises(KeyError):
                get_tool("nonexistent_tool")

    def test_all_expected_tools_registered(self, mock_env_vars):
        """All tool names are registered (AF2 from JSON + 1 dynamic + OF3)."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            import foldrun_app.skills._tool_registry as reg
            from foldrun_app.skills._tool_registry import get_tool

            # Trigger initialization
            get_tool("af2_submit_monomer")

            expected_tools = [
                # AF2 tools (19 from JSON + 1 dynamic)
                "af2_submit_monomer",
                "af2_submit_multimer",
                "af2_submit_batch",
                "af2_check_job_status",
                "af2_list_jobs",
                "af2_get_results",
                "af2_get_job_details",
                "af2_delete_job",
                "af2_check_gpu_quota",
                "af2_cleanup_gcs_files",
                "af2_find_orphaned_files",
                "af2_analyze_quality",
                "af2_visualize_structure",
                "af2_get_analysis_results",
                "af2_analyze_parallel",
                "af2_open_viewer",
                "alphafold_db_get_prediction",
                "alphafold_db_get_summary",
                "alphafold_db_get_annotations",
                "af2_analyze_job_deep",
                # OF3 tools (4 from JSON)
                "of3_submit_prediction",
                "of3_analyze_parallel",
                "of3_get_analysis_results",
                "of3_open_viewer",
            ]

            assert len(reg._agents) == 24
            for tool_name in expected_tools:
                assert tool_name in reg._agents, f"Tool '{tool_name}' not registered"
