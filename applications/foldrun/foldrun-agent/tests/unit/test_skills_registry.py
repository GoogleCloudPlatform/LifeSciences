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
        """Compilation produces all 30 baseline tools without duplicates."""
        tools = skill_registry.compile_tools()
        tool_names = [
            getattr(t, "name", None)
            or getattr(getattr(t, "func", None), "__name__", None)
            or str(t)
            for t in tools
        ]
        assert len(tool_names) == 30
        assert len(tool_names) == len(set(tool_names))
        assert "submit_af2_monomer_prediction" in tool_names
        assert "submit_of3_prediction" in tool_names
        assert "submit_boltz2_prediction" in tool_names

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
        """When OF3, Boltz-2, and AF3 endpoint are missing, only AF2 tools (22 tools) are registered."""
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
        assert len(tools) == 22
        assert "submit_of3_prediction" not in tool_names
        assert "submit_boltz2_prediction" not in tool_names
        assert "submit_af3_endpoint_prediction" not in tool_names
        assert "submit_af2_monomer_prediction" in tool_names

    def test_conditional_af3_tools(self, monkeypatch):
        """When AF3_VERTEX_ENDPOINT is configured, all 6 AF3 tools are registered."""
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
        # 22 baseline AF2 + 6 AF3 = 28
        assert len(tools) == 28
        assert "submit_af3_endpoint_prediction" in tool_names
        assert "check_af3_endpoint" in tool_names
        assert "deploy_af3_endpoint" in tool_names
        assert "undeploy_af3_endpoint" in tool_names
        assert "get_af3_results" in tool_names
        assert "open_af3_structure_viewer" in tool_names

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

    def test_get_skill_toolset(self):
        """get_skill_toolset returns a SkillToolset with loaded skills and candidate tools."""
        toolset = skill_registry.get_skill_toolset()
        assert isinstance(toolset, SkillToolset)
        skill_names = {s.frontmatter.name for s in toolset._skills.values()}
        assert "job-submission" in skill_names
        assert "submit_af2_monomer_prediction" in toolset._provided_tools_by_name
