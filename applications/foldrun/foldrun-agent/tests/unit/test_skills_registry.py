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

"""Unit tests for FoldRun modular Skill and SkillRegistry architecture."""

from pathlib import Path

import pytest
from google.adk.tools import FunctionTool
from google.adk.tools.skill_toolset import SkillToolset

from foldrun_app.skills.base import Skill
from foldrun_app.skills.registry import SkillRegistry, skill_registry


def dummy_tool_a():
    """Dummy tool A for testing."""
    return "A"


def dummy_tool_b():
    """Dummy tool B for testing."""
    return "B"


class TestSkillBase:
    """Tests for Skill dataclass."""

    def test_skill_properties(self):
        """Skill properties and FunctionTool wrapping."""
        skill = Skill(
            name="test_skill",
            description="Test skill description",
            instruction="Test instructions",
            tool_functions=[dummy_tool_a, dummy_tool_b],
        )
        assert skill.name == "test_skill"
        assert skill.description == "Test skill description"
        assert skill.instruction == "Test instructions"
        assert len(skill.tools) == 2
        assert all(isinstance(t, FunctionTool) for t in skill.tools)


class TestSkillRegistry:
    """Tests for SkillRegistry behavior and compilation."""

    def test_default_skills_registered(self):
        """Baseline registry contains the 7 baseline domain skills."""
        expected_skills = {
            "job_submission",
            "job_management",
            "storage_management",
            "results_analysis",
            "visualization",
            "cost_estimation",
            "database_queries",
        }
        registered_names = {s.name for s in skill_registry.list_skills()}
        assert expected_skills.issubset(registered_names)

    def test_get_skill(self):
        """Retrieving an existing skill returns the Skill object."""
        skill = skill_registry.get_skill("job_submission")
        assert skill.name == "job_submission"
        assert "AlphaFold2" in skill.instruction

    def test_get_unknown_skill_raises_keyerror(self):
        """Retrieving an unregistered skill raises KeyError."""
        with pytest.raises(KeyError, match="not found in registry"):
            skill_registry.get_skill("nonexistent_skill")

    def test_register_custom_skill(self):
        """Registry allows registering new skills dynamically."""
        custom_registry = SkillRegistry()
        new_skill = Skill(
            name="custom_skill",
            description="Custom domain skill",
            instruction="Custom instructions",
            tool_functions=[dummy_tool_a],
        )
        custom_registry.register(new_skill)
        retrieved = custom_registry.get_skill("custom_skill")
        assert retrieved.name == "custom_skill"
        assert len(custom_registry.compile_tools(["custom_skill"])) == 1

    def test_compile_instruction(self):
        """Instruction compilation merges header, skills, and guidelines."""
        instruction = skill_registry.compile_instruction()
        assert "You are an expert FoldRun protein structure prediction assistant" in instruction
        assert "Response Guidelines" in instruction
        assert "Job Submission" in instruction
        assert "Job Management" in instruction
        assert "Cost Estimation" in instruction

    def test_compile_instruction_subset(self):
        """Instruction compilation for specific subset."""
        instruction = skill_registry.compile_instruction(
            skill_names=["job_submission"],
            include_header=False,
            include_guidelines=False,
        )
        assert "Job Submission" in instruction
        assert "Job Management" not in instruction
        assert "Response Guidelines" not in instruction

    def test_compile_tools_baseline_count(self):
        """Compilation produces all 25 baseline tools without duplicates."""
        tools = skill_registry.compile_tools()
        tool_names = [
            getattr(t, "name", None)
            or getattr(getattr(t, "func", None), "__name__", None)
            or str(t)
            for t in tools
        ]
        assert len(tool_names) == 25
        assert len(tool_names) == len(set(tool_names))
        assert "submit_af2_monomer_prediction" in tool_names
        assert "submit_of3_prediction" in tool_names
        assert "submit_boltz2_prediction" in tool_names
        assert "download_job_artifacts" in tool_names

    def test_compile_instruction_dynamic_custom_skill_asymmetry_fix(self):
        """Registering a custom skill automatically includes it in compile_instruction."""
        custom_registry = SkillRegistry()
        custom_skill = Skill(
            name="custom_analysis",
            description="Custom analysis description",
            instruction="Custom analysis prompt instruction content",
            tool_functions=[dummy_tool_a],
        )
        custom_registry.register(custom_skill)

        # Calling without arguments should include custom skill instruction
        full_instruction = custom_registry.compile_instruction()
        assert "Custom analysis prompt instruction content" in full_instruction

    def test_compile_instruction_progressive_disclosure(self):
        """Progressive disclosure compiles skill overview manifest instead of full bodies."""
        progressive_instruction = skill_registry.compile_instruction(progressive=True)
        assert "Available Domain Skills (Progressive Disclosure)" in progressive_instruction
        assert "job_submission" in progressive_instruction
        assert "results_analysis" in progressive_instruction
        # Does not dump full bodies
        assert "### Smart Retry Guidance for Failed Jobs" not in progressive_instruction

    def test_conditional_tools_without_images(self, monkeypatch):
        """When OF3, Boltz-2, and AF3 endpoint are missing, only AF2 tools (23 tools) are registered."""
        monkeypatch.delenv("OPENFOLD3_COMPONENTS_IMAGE", raising=False)
        monkeypatch.delenv("BOLTZ2_COMPONENTS_IMAGE", raising=False)
        monkeypatch.delenv("AF3_VERTEX_ENDPOINT", raising=False)
        monkeypatch.delenv("AF3_ENDPOINT_ID", raising=False)

        af2_registry = SkillRegistry()
        tools = af2_registry.compile_tools()
        tool_names = {
            getattr(t, "name", None)
            or getattr(getattr(t, "func", None), "__name__", None)
            or str(t)
            for t in tools
        }
        assert len(tools) == 23
        assert "submit_of3_prediction" not in tool_names
        assert "submit_boltz2_prediction" not in tool_names
        assert "submit_af3_endpoint_prediction" not in tool_names
        assert "submit_af2_monomer_prediction" in tool_names
        assert "download_job_artifacts" in tool_names

    def test_conditional_af3_tools(self, monkeypatch):
        """When AF3_VERTEX_ENDPOINT is configured, the 3 AF3 submission/endpoint tools are registered."""
        monkeypatch.delenv("OPENFOLD3_COMPONENTS_IMAGE", raising=False)
        monkeypatch.delenv("BOLTZ2_COMPONENTS_IMAGE", raising=False)
        monkeypatch.setenv(
            "AF3_VERTEX_ENDPOINT", "projects/test-proj/locations/us-central1/endpoints/123"
        )

        af3_registry = SkillRegistry()
        tools = af3_registry.compile_tools()
        tool_names = {
            getattr(t, "name", None)
            or getattr(getattr(t, "func", None), "__name__", None)
            or str(t)
            for t in tools
        }
        # 23 baseline AF2 + 3 AF3 submission/endpoint tools = 26
        assert len(tools) == 26
        assert "submit_af3_endpoint_prediction" in tool_names
        assert "submit_af3_batch_predictions" in tool_names
        assert "check_af3_endpoint" in tool_names
        assert "deploy_af3_endpoint" not in tool_names
        assert "undeploy_af3_endpoint" not in tool_names
        assert "get_af3_results" not in tool_names
        assert "open_af3_structure_viewer" not in tool_names

    def test_tool_introspection_safety_custom_class(self):
        """Tool compilation safely inspects tools lacking a func attribute."""

        class CustomClassTool:
            name = "custom_class_tool"

        custom_registry = SkillRegistry()
        custom_registry._skills.clear()
        custom_skill = Skill(
            name="class_tool_skill",
            description="Skill with class-based tool",
            instruction="Some instruction",
            tool_functions=[],
        )
        # Directly attach custom class tool instance
        custom_skill.tools = [CustomClassTool()]
        custom_registry.register(custom_skill)

        tools = custom_registry.compile_tools()
        assert len(tools) == 1
        assert tools[0].name == "custom_class_tool"

    def test_get_skill_normalized_name(self):
        """Registry resolves both kebab-case and snake_case skill names."""
        skill_snake = skill_registry.get_skill("job_submission")
        skill_kebab = skill_registry.get_skill("job-submission")
        assert skill_snake is skill_kebab

    def test_load_adk_skills(self):
        """load_adk_skills loads all 7 valid agentskills.io packages from packages/ directory."""
        adk_skills = skill_registry.load_adk_skills()
        names = {s.frontmatter.name for s in adk_skills}
        expected = {
            "job-submission",
            "job-management",
            "storage-management",
            "results-analysis",
            "visualization",
            "cost-estimation",
            "database-queries",
        }
        assert expected.issubset(names)

        # Check frontmatter fields conform to agentskills.io standard
        for skill in adk_skills:
            assert skill.frontmatter.name
            assert skill.frontmatter.description
            assert len(skill.instructions) > 0

    def test_all_skill_md_additional_tools_resolve_to_registered_tools(self, monkeypatch):
        """Every tool in SKILL.md adk_additional_tools must exist in SkillRegistry."""
        monkeypatch.setenv("OPENFOLD3_COMPONENTS_IMAGE", "test-of3:latest")
        monkeypatch.setenv("BOLTZ2_COMPONENTS_IMAGE", "test-boltz2:latest")
        monkeypatch.setenv(
            "AF3_VERTEX_ENDPOINT", "projects/test-proj/locations/us-central1/endpoints/123"
        )

        full_registry = SkillRegistry()
        compiled_tools = full_registry.compile_tools()
        compiled_names = {
            getattr(t, "name", None)
            or getattr(getattr(t, "func", None), "__name__", None)
            or str(t)
            for t in compiled_tools
        }

        adk_skills = {s.frontmatter.name: s for s in full_registry.load_adk_skills()}
        for skill_name, skill_pkg in adk_skills.items():
            metadata = skill_pkg.frontmatter.metadata or {}
            additional_tools = metadata.get("adk_additional_tools", [])
            assert additional_tools, f"{skill_name} SKILL.md has empty adk_additional_tools"
            missing = [t for t in additional_tools if t not in compiled_names]
            assert not missing, f"{skill_name}/SKILL.md lists unregistered tools: {missing}"

        jm_tools = set(adk_skills["job-management"].frontmatter.metadata["adk_additional_tools"])
        assert {"check_job_status", "check_gpu_quota"}.issubset(jm_tools)
        assert "cancel_job" not in jm_tools

        ra_tools = set(adk_skills["results-analysis"].frontmatter.metadata["adk_additional_tools"])
        assert {
            "get_prediction_results",
            "analyze_prediction_quality",
            "analyze_job_parallel",
            "get_analysis_results",
            "analyze_job",
        }.issubset(ra_tools)
        assert "af2_analyze_job_parallel" not in ra_tools
        assert "af2_get_analysis_results" not in ra_tools

    @pytest.mark.asyncio
    async def test_get_skill_toolset(self):
        """get_skill_toolset exposes only load_skill/load_skill_resource initially and activates domain tools on load."""
        from types import SimpleNamespace

        from google.adk.tools.skill_toolset import SkillDiscoveryMode

        toolset = skill_registry.get_skill_toolset()
        assert isinstance(toolset, SkillToolset)
        assert toolset._discovery_mode == SkillDiscoveryMode.EAGER
        assert isinstance(toolset.tool_filter, list)
        assert "load_skill" in toolset.tool_filter
        assert "load_skill_resource" in toolset.tool_filter
        assert "run_skill_script" not in toolset.tool_filter
        skill_names = {s.frontmatter.name for s in toolset._skills.values()}
        assert "job-submission" in skill_names
        assert "submit_af2_monomer_prediction" in toolset._provided_tools_by_name

        # Verify job-submission has all 4 per-model submission references loaded
        js_refs = set(toolset._skills["job-submission"].resources.references.keys())
        assert {
            "af2-submission.md",
            "of3-submission.md",
            "boltz2-submission.md",
            "af3-submission.md",
        }.issubset(js_refs)

        # Before skill activation: only load_skill and load_skill_resource are exposed
        ctx_initial = SimpleNamespace(
            state={},
            agent_name="foldrun_app",
            invocation_id="inv-1",
            _invocation_context=None,
        )
        initial_tools = [t.name for t in await toolset.get_tools(ctx_initial)]
        assert initial_tools == ["load_skill", "load_skill_resource"]

        # After activating job-management: domain tools (e.g. check_gpu_quota, list_jobs) are exposed
        ctx_activated = SimpleNamespace(
            state={"_adk_activated_skill_foldrun_app": ["job-management"]},
            agent_name="foldrun_app",
            invocation_id="inv-1",
            _invocation_context=None,
        )
        active_tools = {t.name for t in await toolset.get_tools(ctx_activated)}
        assert {"load_skill", "load_skill_resource", "check_gpu_quota", "list_jobs"}.issubset(
            active_tools
        )

    def test_skill_descriptions_match_frontmatter_and_include_triggers(self):
        """All 7 skills use third-person descriptions with 'Use when' triggers and match SKILL.md frontmatter."""
        adk_by_name = {
            s.frontmatter.name.replace("-", "_"): s.frontmatter.description
            for s in skill_registry.load_adk_skills()
        }
        for skill in skill_registry.list_skills():
            if skill.name in adk_by_name:
                assert skill.description == adk_by_name[skill.name]
                assert "Use when" in skill.description or "Use before" in skill.description
                assert len(skill.instruction.splitlines()) < 120

    def test_compile_instruction_includes_references(self):
        """compile_instruction(include_references=True) embeds package references/*.md."""
        import re

        without_refs = skill_registry.compile_instruction(include_references=False)
        with_refs = skill_registry.compile_instruction(include_references=True)

        assert len(with_refs) > len(without_refs)
        # Verify both results-analysis/references/of3-metrics.md and af3-metrics.md are embedded
        assert "summary_confidences.json" in with_refs
        assert "chain_pair_pae_min" in with_refs
        assert "has_clash" in with_refs

        # Guard against bare {identifier} substrings that ADK inject_session_state
        # would treat as required session state context variables at runtime.
        bare_vars = [
            m.group()
            for m in re.finditer(r"\{+[^{}]*\}+", with_refs)
            if m.group().lstrip("{").rstrip("}").strip().isidentifier()
        ]
        assert bare_vars == [], (
            f"Found unescaped ADK template variables in instruction: {bare_vars}"
        )

    def test_single_source_prompt_and_tool_registry_hygiene(self):
        """Verify legacy duplicate prompt modules and unconsumed *_tools.json files are retired."""
        app_root = Path(__file__).resolve().parents[2] / "foldrun_app"
        assert not (app_root / "v1_instruction.py").exists()
        assert list((app_root / "skills").glob("*/instruction.py")) == []
        assert list((app_root / "models").glob("*/data/*_tools.json")) == []
        assert not (app_root / "models" / "af2" / "utils" / "vertex_utils.py").exists()
        assert not (app_root / "models" / "af2" / "utils" / "alphafold_db.py").exists()
