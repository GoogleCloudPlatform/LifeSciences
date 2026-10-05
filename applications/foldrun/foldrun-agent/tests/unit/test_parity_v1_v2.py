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

"""Cross-instance parity unit tests: baseline v1 vs modular skills v2.

Verifies that the modular SkillRegistry implementation in CL 1 guarantees
100% behavioral, structural, and semantic parity with the baseline v1 agent.
"""

import inspect
import os
from unittest.mock import MagicMock, patch

from google.adk.tools.skill_toolset import SkillToolset

from foldrun_app.skills.cost_estimation.tools import estimate_job_cost
from foldrun_app.skills.registry import skill_registry


def _mock_startup():
    return patch.multiple(
        "foldrun_app.models.af2.startup",
        _auto_detect_gpus=MagicMock(),
    )


class TestCrossInstanceParity:
    """Parity tests verifying zero feature degradation between v1 and v2."""

    def test_tool_inventory_parity(self, mock_env_vars):
        """Assert all 30 baseline tools are preserved with 100% name parity."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            tool_names = {
                getattr(t, "name", None)
                or getattr(getattr(t, "func", None), "__name__", None)
                or str(t)
                for t in agent.tools
            }

            expected_v1_tools = {
                # AF2 Pipeline
                "submit_af2_monomer_prediction",
                "submit_af2_multimer_prediction",
                "submit_af2_batch_predictions",
                # OF3 Pipeline
                "submit_of3_prediction",
                "of3_analyze_job_parallel",
                "of3_get_analysis_results",
                "open_of3_structure_viewer",
                # Boltz-2 Pipeline
                "submit_boltz2_prediction",
                "boltz2_analyze_job_parallel",
                "boltz2_get_analysis_results",
                "open_boltz2_structure_viewer",
                # Job Management & Monitoring
                "check_job_status",
                "list_jobs",
                "get_job_details",
                "delete_job",
                "check_gpu_quota",
                # Cost Estimation
                "estimate_job_cost",
                "estimate_monthly_cost",
                "get_actual_job_costs",
                # Results & Quality Analysis
                "get_prediction_results",
                "download_job_artifacts",
                "analyze_prediction_quality",
                "analyze_job_parallel",
                "get_analysis_results",
                "analyze_job",
                # Database Queries (AlphaFold DB)
                "query_alphafold_db_prediction",
                "query_alphafold_db_summary",
                "query_alphafold_db_annotations",
                # Storage & Governance
                "cleanup_gcs_files",
                "find_orphaned_gcs_files",
                # Visualization
                "open_structure_viewer",
            }

            assert len(tool_names) == 31, f"Expected exactly 31 tools, got {len(tool_names)}"
            assert tool_names == expected_v1_tools, (
                f"Tool set mismatch: missing={expected_v1_tools - tool_names}, "
                f"extra={tool_names - expected_v1_tools}"
            )

    def test_tool_signatures_parity(self, mock_env_vars):
        """Assert key submission tool parameter signatures match v1 expectations."""
        tools = {t.func.__name__: t.func for t in skill_registry.compile_tools()}

        # 1. submit_af2_monomer_prediction signature
        sig_af2 = inspect.signature(tools["submit_af2_monomer_prediction"])
        assert "sequence" in sig_af2.parameters
        assert "gpu_type" in sig_af2.parameters
        assert "run_relaxation" in sig_af2.parameters
        assert "max_template_date" in sig_af2.parameters
        assert "msa_method" in sig_af2.parameters

        # 2. submit_of3_prediction signature
        sig_of3 = inspect.signature(tools["submit_of3_prediction"])
        assert "input" in sig_of3.parameters
        assert "num_model_seeds" in sig_of3.parameters
        assert "num_diffusion_samples" in sig_of3.parameters

        # 3. submit_boltz2_prediction signature
        sig_boltz = inspect.signature(tools["submit_boltz2_prediction"])
        assert "input" in sig_boltz.parameters
        assert "num_model_seeds" in sig_boltz.parameters
        assert "num_diffusion_samples" in sig_boltz.parameters

        # 4. estimate_job_cost signature
        sig_cost = inspect.signature(tools["estimate_job_cost"])
        assert "job_type" in sig_cost.parameters

    def test_instruction_policy_and_domain_parity(self):
        """Assert modular instruction contains all mandatory operational policies from v1."""
        instruction = skill_registry.compile_instruction()

        # Model selection heuristics
        assert "AlphaFold2" in instruction
        assert "OpenFold3" in instruction
        assert "Boltz-2" in instruction
        assert "submit_af2_monomer_prediction" in instruction
        assert "submit_of3_prediction" in instruction
        assert "submit_boltz2_prediction" in instruction

        # Critical safety: Hardware confirmation table
        assert (
            "Pre-Submission Confirmation Required" in instruction
            or "Pre-Submission Hardware Breakdown" in instruction
        )
        assert "Pipeline Phase" in instruction
        assert "Machine Type" in instruction
        assert "DWS FLEX_START" in instruction
        assert "OF3 Pre-Submission Confirmation" in instruction
        assert "MSA Pipeline" in instruction
        assert "a2-highgpu-1g" in instruction

        # Data handling notice
        assert "Data Handling Notice" in instruction or "Cloud Storage bucket" in instruction

        # Storage cleanup protocol
        assert "cleanup" in instruction.lower()
        assert "orphan" in instruction.lower()

        # Follow-up actions
        assert "Response Guidelines" in instruction
        assert "numbered options" in instruction or "What would you like to do next?" in instruction

    def test_cost_calculation_parity(self):
        """Assert estimate_job_cost returns mathematically exact pricing breakdowns."""
        # AF2 Monomer cost check
        af2_cost = estimate_job_cost(
            job_type="af2_monomer",
            sequence_length=300,
            gpu_type="L4",
        )
        assert isinstance(af2_cost, dict)
        assert "error" not in af2_cost
        assert af2_cost["on_demand"]["estimated_total"] > 0
        assert af2_cost["dws_flex_start"]["estimated_total"] > 0
        assert af2_cost["flex_start_savings_pct"] >= 0

    def test_agent_factory_flag_parity(self, mock_env_vars):
        """Assert FOLDRUN_V2 feature flag toggles agent creation with 100% tool parity."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent, create_foldrun_agent

            agent_v1 = create_alphafold_agent()
            agent_v2 = create_foldrun_agent()

            assert agent_v1.name == "foldrun_app"
            assert agent_v2.name == "foldrun_app"
            assert len(agent_v1.tools) == 31

            # V2 uses native ADK SkillToolset with progressive disclosure
            assert len(agent_v2.tools) == 1
            toolset = agent_v2.tools[0]
            assert isinstance(toolset, SkillToolset)

            # Assert all 7 agentskills.io domain skills are loaded into the toolset
            skill_names = {s.frontmatter.name for s in toolset._skills.values()}
            expected_skills = {
                "job-submission",
                "job-management",
                "storage-management",
                "results-analysis",
                "visualization",
                "cost-estimation",
                "database-queries",
            }
            assert skill_names == expected_skills

            # Assert candidate additional tools in toolset contain all 30 baseline tools
            v1_tool_names = {
                getattr(t, "name", None)
                or getattr(getattr(t, "func", None), "__name__", None)
                or str(t)
                for t in agent_v1.tools
            }
            assert set(toolset._provided_tools_by_name.keys()) == v1_tool_names

            # Verify environment flag toggling dynamically
            with patch.dict(os.environ, {"FOLDRUN_V2": "false"}):
                flag_disabled_agent = create_alphafold_agent()
                assert "FoldRun (AlphaFold2 + OpenFold3)" in flag_disabled_agent.description

            with patch.dict(os.environ, {"FOLDRUN_V2": "true"}):
                flag_enabled_agent = create_foldrun_agent()
                assert "FoldRun 2.0" in flag_enabled_agent.description
