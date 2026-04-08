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

"""Tests for AlphaFold2 ADK Agent creation and configuration."""

from unittest.mock import MagicMock, patch

import pytest


def _mock_startup():
    """Context manager that mocks startup dependencies (GPU detection, Vertex AI)."""
    return patch.multiple(
        "foldrun_app.models.af2.startup",
        _auto_detect_gpus=MagicMock(),
    )


class TestCreateAgent:
    """Tests for create_alphafold_agent function."""

    def test_create_agent_returns_agent(self, mock_env_vars):
        """Agent creation returns an Agent instance."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            assert agent is not None

    def test_create_agent_default_model(self, mock_env_vars):
        """Default model is gemini-3-flash-preview."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            assert agent.model == "gemini-3-flash-preview"

    def test_create_agent_custom_model(self, mock_env_vars):
        """Accepts gemini-3-pro-preview as a model."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent(model="gemini-3-pro-preview")
            assert agent.model == "gemini-3-pro-preview"

    def test_create_agent_invalid_model(self, mock_env_vars):
        """Invalid model raises ValueError."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            with pytest.raises(ValueError, match="not supported"):
                create_alphafold_agent(model="invalid-model")

    def test_agent_has_all_tools(self, mock_env_vars):
        """Agent has all FunctionTools (AF2 + OF3)."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            assert len(agent.tools) == 23

    def test_agent_tool_names_complete(self, mock_env_vars):
        """Every expected tool function is present by name."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            tool_func_names = {t.func.__name__ for t in agent.tools}
            expected = {
                # AF2
                "submit_af2_monomer_prediction",
                "submit_af2_multimer_prediction",
                "submit_af2_batch_predictions",
                # OF3
                "submit_of3_prediction",
                "of3_analyze_job_parallel",
                "of3_get_analysis_results",
                "open_of3_structure_viewer",
                # Job management
                "check_job_status",
                "list_jobs",
                "get_job_details",
                "delete_job",
                "check_gpu_quota",
                # Results & Analysis
                "get_prediction_results",
                "analyze_prediction_quality",
                "analyze_job_parallel",
                "get_analysis_results",
                "analyze_job",
                # Database queries
                "query_alphafold_db_prediction",
                "query_alphafold_db_summary",
                "query_alphafold_db_annotations",
                # Storage management
                "cleanup_gcs_files",
                "find_orphaned_gcs_files",
                # Visualization
                "open_structure_viewer",
            }
            assert tool_func_names == expected

    def test_agent_instruction_contains_key_sections(self, mock_env_vars):
        """Instruction contains all expected sections."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            assert "Job Submission" in instruction
            assert "Job Management" in instruction
            assert "Results & Analysis" in instruction
            assert "Database Queries" in instruction
            assert "MSA Method" in instruction
            assert "mmseqs2" in instruction
            assert "FASTA Sequence Format" in instruction
            assert "AF2 Reference" in instruction

    def test_agent_instruction_model_selection(self, mock_env_vars):
        """Instruction includes model selection table with all tools."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            # Model selection table present
            assert "Model Selection" in instruction
            # All submission tools referenced
            assert "submit_af2_monomer_prediction" in instruction
            assert "submit_af2_multimer_prediction" in instruction
            assert "submit_af2_batch_predictions" in instruction
            assert "submit_of3_prediction" in instruction
            # Decision rule present
            assert "RNA" in instruction
            assert "DNA" in instruction
            assert "ligand" in instruction

    def test_agent_name(self, mock_env_vars):
        """Agent name is foldrun_app."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            assert agent.name == "foldrun_app"

    def test_agent_greeting_has_model_overview(self, mock_env_vars):
        """Initial greeting explains both models and when to use each."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            # Both models introduced
            assert "AlphaFold2" in instruction
            assert "OpenFold3" in instruction
            # Decision guidance
            assert "protein-only" in instruction or "protein-protein" in instruction
            assert "ligand" in instruction

    def test_agent_greeting_has_example_prompts(self, mock_env_vars):
        """Initial greeting includes example prompts for new users."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            assert "Getting started" in instruction
            assert (
                "ubiquitin" in instruction.lower() or "predict the structure" in instruction.lower()
            )

    def test_agent_has_af2_reference_knowledge(self, mock_env_vars):
        """Agent has AF2 structural biology reference (pLDDT, PAE, model variants)."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            # AF2 model variants
            assert "model_1" in instruction
            assert "multimer_v3" in instruction or "multimer" in instruction
            # Quality metrics with thresholds
            assert "pLDDT" in instruction
            assert ">90" in instruction
            assert "PAE" in instruction
            # Biological context
            assert "disordered" in instruction.lower()

    def test_agent_has_of3_reference_knowledge(self, mock_env_vars):
        """Agent has OF3 reference (JSON format, CCD codes, confidence metrics)."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            # JSON format knowledge
            assert "molecule_type" in instruction
            assert "chain_ids" in instruction
            assert "ccd_codes" in instruction
            assert "smiles" in instruction
            # Common CCD codes
            assert "ATP" in instruction
            assert "NAD" in instruction
            assert "HEM" in instruction
            # OF3 confidence metrics
            assert "sample_ranking_score" in instruction
            assert "iptm" in instruction
            assert "chain_pair_iptm" in instruction

    def test_agent_knowledge_is_not_dumped_unprompted(self, mock_env_vars):
        """Reference sections are marked as internal knowledge, not for dumping."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            assert "don't dump unprompted" in instruction
            # Both reference sections marked as internal
            assert instruction.count("internal knowledge") >= 2

    def test_agent_seeds_vs_samples_guidance(self, mock_env_vars):
        """Agent explains seeds vs samples and recommends AF3 paper protocol."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            assert "Seeds" in instruction or "seeds" in instruction
            assert "diffusion" in instruction
            assert "AF3 paper" in instruction
            assert "5 seeds" in instruction or "5 × 5" in instruction

    def test_agent_config_context_in_instruction(self, mock_env_vars):
        """Instruction includes dynamic config context with project details."""
        with (
            _mock_startup(),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.storage.Client"),
        ):
            from foldrun_app.agent import create_alphafold_agent

            agent = create_alphafold_agent()
            instruction = agent.instruction
            assert "Your Current Configuration" in instruction
            assert mock_env_vars["GCP_PROJECT_ID"] in instruction
