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

"""Skill registry for FoldRun modular agent architecture."""

from pathlib import Path

from dotenv import load_dotenv
from google.adk.skills import load_skills_from_dir
from google.adk.skills.models import Skill as AdkSkill
from google.adk.tools import FunctionTool
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset

from foldrun_app.core.model_registry import get_model
from foldrun_app.skills.base import Skill
from foldrun_app.skills.cost_estimation.tools import (
    estimate_job_cost,
    estimate_monthly_cost,
    get_actual_job_costs,
)
from foldrun_app.skills.database_queries.tools import (
    query_alphafold_db_annotations,
    query_alphafold_db_prediction,
    query_alphafold_db_summary,
)
from foldrun_app.skills.job_management.tools import (
    check_af3_endpoint,
    check_gpu_quota,
    check_job_status,
    delete_job,
    get_job_details,
    list_jobs,
)
from foldrun_app.skills.job_submission.tools import (
    submit_af2_batch_predictions,
    submit_af2_monomer_prediction,
    submit_af2_multimer_prediction,
    submit_af3_batch_predictions,
    submit_af3_endpoint_prediction,
    submit_bindcraft_design,
    submit_boltz2_prediction,
    submit_boltzgen_design,
    submit_esmfold2_prediction,
    submit_of3_prediction,
    submit_rfd4_proteina_design,
)
from foldrun_app.skills.results_analysis.tools import (
    analyze_job,
    analyze_job_parallel,
    analyze_prediction_quality,
    download_job_artifacts,
    get_analysis_results,
    get_prediction_results,
)
from foldrun_app.skills.storage_management.tools import (
    cleanup_gcs_files,
    find_orphaned_gcs_files,
)
from foldrun_app.skills.visualization.tools import (
    open_structure_viewer,
)

CORE_HEADER = """You are an expert FoldRun protein structure prediction assistant supporting AlphaFold2, AlphaFold3, OpenFold3, Boltz-2, ESMFold2 / ESMFold2-Fast, BoltzGen, RFD4-Proteina, and BindCraft.

Your role is to help researchers and scientists with:
1. Submitting protein structure predictions (monomers and multimers)
2. Monitoring job progress and status
3. Analyzing prediction quality and results
4. Visualizing structures
5. Providing guidance on best practices

## Key Capabilities
"""

RESPONSE_GUIDELINES = """## Response Guidelines
- Always provide clear, actionable guidance
- Explain job IDs and how to use them for follow-up queries
- Recommend checking AlphaFold DB before submitting new predictions
- Provide console URLs for tracking in GCP when available

**IMPORTANT: Always suggest next steps with numbered options.**
After every response, offer 2-3 contextual next actions as numbered choices.
The user can reply with just the number (e.g., "1") to proceed. Examples:

After submitting a job:
```
What would you like to do next?
1. Check the job status
2. Submit another prediction
3. Open the structure viewer (available when complete)
```

After checking job status (running):
```
What would you like to do next?
1. Check status again
2. View job details
3. Check GPU quota for new jobs
```

After analyzing results:
```
What would you like to do next?
1. Open the structure in 3D viewer
2. Submit another prediction
3. Run detailed quality analysis
```

After cost estimation:
```
What would you like to do next?
1. Submit this prediction job
2. Check GPU quota
3. Estimate monthly cost for a batch of jobs
```

Always tailor the suggestions to the current state and model used.
Keep the choices concise and relevant.

**CRITICAL: NEVER hallucinate job completion or status.**
- ONLY report a job as SUCCEEDED, FAILED, or CANCELLED if check_job_status explicitly returned that state.
- If check_job_status returned NOT_FOUND or an error, tell the user the job was not found. Do NOT assume it succeeded or failed.
- NEVER invent pipeline task steps, runtimes, or output paths that were not in the tool response.
"""


class SkillRegistry:
    """Central registry managing modular skills for the FoldRun agent."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._adk_skills: dict[str, AdkSkill] = {}
        self._register_defaults()

    def _register_defaults(self):
        """Register the baseline FoldRun domain skills with environment-conditional tools."""
        of3_model = get_model("of3")
        boltz2_model = get_model("boltz2")
        esmfold2_model = get_model("esmfold2")
        boltzgen_model = get_model("boltzgen")
        rfd4_model = get_model("rfd4_proteina")
        bindcraft_model = get_model("bindcraft")
        af3_model = get_model("af3")
        has_of3 = bool(of3_model and of3_model.is_enabled())
        has_boltz2 = bool(boltz2_model and boltz2_model.is_enabled())
        has_esmfold2 = bool(esmfold2_model and esmfold2_model.is_enabled())
        has_boltzgen = bool(boltzgen_model and boltzgen_model.is_enabled())
        has_rfd4 = bool(rfd4_model and rfd4_model.is_enabled())
        has_bindcraft = bool(bindcraft_model and bindcraft_model.is_enabled())
        has_af3 = bool(af3_model and af3_model.is_enabled())

        # Load instruction bodies and descriptions strictly from packages/*/SKILL.md (single source of truth)
        if not self._adk_skills:
            self._adk_skills = {s.name.replace("-", "_"): s for s in self.load_adk_skills()}

        def _get_skill_meta(name: str) -> tuple[str, str]:
            skill_obj = self._adk_skills.get(name)
            body = getattr(skill_obj, "instructions", None) if skill_obj is not None else None
            if not body:
                raise RuntimeError(
                    f"Missing SKILL.md instruction body for built-in skill '{name}' under skills/packages/."
                )
            desc = (
                getattr(getattr(skill_obj, "frontmatter", None), "description", None)
                or skill_obj.description
            )
            return desc, body

        def _register_builtin(name: str, tool_functions: list):
            description, instruction = _get_skill_meta(name)
            self.register(
                Skill(
                    name=name,
                    description=description,
                    instruction=instruction,
                    tool_functions=tool_functions,
                )
            )

        # 1. Job Submission
        submission_tools = [
            submit_af2_monomer_prediction,
            submit_af2_multimer_prediction,
            submit_af2_batch_predictions,
        ]
        if has_of3:
            submission_tools.append(submit_of3_prediction)
        if has_boltz2:
            submission_tools.append(submit_boltz2_prediction)
        if has_esmfold2:
            submission_tools.append(submit_esmfold2_prediction)
        if has_boltzgen:
            submission_tools.append(submit_boltzgen_design)
        if has_rfd4:
            submission_tools.append(submit_rfd4_proteina_design)
        if has_bindcraft:
            submission_tools.append(submit_bindcraft_design)
        if has_af3:
            submission_tools.extend([submit_af3_endpoint_prediction, submit_af3_batch_predictions])

        _register_builtin("job_submission", submission_tools)

        # 2. Job Management
        management_tools = [
            check_gpu_quota,
            list_jobs,
            check_job_status,
            get_job_details,
            delete_job,
        ]
        if has_af3:
            management_tools.append(check_af3_endpoint)

        _register_builtin("job_management", management_tools)

        # 3. Storage Management
        _register_builtin(
            "storage_management",
            [
                find_orphaned_gcs_files,
                cleanup_gcs_files,
            ],
        )

        # 4. Results & Analysis
        _register_builtin(
            "results_analysis",
            [
                get_prediction_results,
                download_job_artifacts,
                analyze_prediction_quality,
                analyze_job_parallel,
                get_analysis_results,
                analyze_job,
            ],
        )

        # 5. Visualization
        _register_builtin("visualization", [open_structure_viewer])

        # 6. Cost Estimation
        _register_builtin(
            "cost_estimation",
            [
                estimate_job_cost,
                estimate_monthly_cost,
                get_actual_job_costs,
            ],
        )

        # 7. Database Queries
        _register_builtin(
            "database_queries",
            [
                query_alphafold_db_summary,
                query_alphafold_db_prediction,
                query_alphafold_db_annotations,
            ],
        )

    def register(self, skill: Skill):
        """Register a new skill in the registry."""
        self._skills[skill.name] = skill

    def get_skill(self, name: str) -> Skill:
        """Retrieve a registered skill by name."""
        if name in self._skills:
            return self._skills[name]
        normalized = name.replace("-", "_")
        if normalized in self._skills:
            return self._skills[normalized]
        raise KeyError(
            f"Skill '{name}' not found in registry. Available: {list(self._skills.keys())}"
        )

    def list_skills(self) -> list[Skill]:
        """List all registered skills."""
        return list(self._skills.values())

    def load_adk_skills(self) -> list[AdkSkill]:
        """Load native ADK skills from packages/ directory according to agentskills.io standard."""
        packages_dir = Path(__file__).parent / "packages"
        if not packages_dir.exists():
            return []
        return load_skills_from_dir(packages_dir)

    def get_skill_toolset(
        self,
        discovery_mode: SkillDiscoveryMode = SkillDiscoveryMode.EAGER,
    ) -> SkillToolset:
        """Construct an ADK SkillToolset with progressive disclosure and dynamic additional tools."""
        skills = list(self._adk_skills.values()) or self.load_adk_skills()
        additional_tools = self.compile_tools()
        allowed_tool_names = [
            "load_skill",
            "load_skill_resource",
            *[t.name for t in additional_tools],
        ]
        return SkillToolset(
            skills=skills,
            additional_tools=additional_tools,
            tool_filter=allowed_tool_names,
            discovery_mode=discovery_mode,
        )

    def _load_skill_references(self, skill_name: str) -> list[str]:
        """Load any Markdown files under packages/<skill-dir>/references/*.md for static compilation."""
        normalized = skill_name.replace("-", "_")
        adk_skill = self._adk_skills.get(normalized)
        resources = getattr(adk_skill, "resources", None) if adk_skill is not None else None
        cached_refs = getattr(resources, "references", None) if resources is not None else None
        if isinstance(cached_refs, dict) and cached_refs:
            return [
                str(content).strip()
                for _, content in sorted(cached_refs.items())
                if str(content).strip()
            ]

        pkg_dir = Path(__file__).parent / "packages" / skill_name.replace("_", "-") / "references"
        if not pkg_dir.is_dir():
            return []
        refs: list[str] = []
        for md_file in sorted(pkg_dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8").strip()
            if text:
                refs.append(text)
        return refs

    def compile_instruction(
        self,
        skill_names: list[str] | None = None,
        include_header: bool = True,
        include_guidelines: bool = True,
        progressive: bool = False,
        include_references: bool = True,
    ) -> str:
        """Compile modular instructions for the requested skills.

        Args:
            skill_names: Optional list of skill names to compile. Defaults to all registered skills.
            include_header: Whether to prepend the core agent header.
            include_guidelines: Whether to append the response guidelines.
            progressive: If True, uses progressive disclosure by generating a concise
                skills overview manifest instead of concatenating full instruction bodies.
            include_references: If True (and progressive=False), appends packages/<skill>/references/*.md
                beneath each skill's body for static v1 prompt compilation.
        """
        selected_names = skill_names or list(self._skills.keys())

        sections = []
        if include_header:
            sections.append(CORE_HEADER.strip())

        if progressive:
            manifest_lines = ["## Available Domain Skills (Progressive Disclosure)"]
            for name in selected_names:
                skill = self.get_skill(name)
                manifest_lines.append(f"- **{skill.name}**: {skill.description}")
            sections.append("\n".join(manifest_lines))
        else:
            for name in selected_names:
                skill = self.get_skill(name)
                sections.append(skill.instruction.strip())
                if include_references:
                    sections.extend(self._load_skill_references(skill.name))

        if include_guidelines:
            sections.append(RESPONSE_GUIDELINES.strip())

        return "\n\n".join(sections)

    def compile_tools(self, skill_names: list[str] | None = None) -> list[FunctionTool]:
        """Compile a deduplicated list of FunctionTools for the requested skills."""
        selected_names = skill_names or list(self._skills.keys())
        tools: list[FunctionTool] = []
        seen_names = set()

        for name in selected_names:
            skill = self.get_skill(name)
            for tool in skill.tools:
                tool_name = (
                    getattr(tool, "name", None)
                    or getattr(getattr(tool, "func", None), "__name__", None)
                    or str(tool)
                )
                if tool_name not in seen_names:
                    seen_names.add(tool_name)
                    tools.append(tool)
        return tools


# Global registry instance (ensure agent .env is loaded before evaluating tool flags)
_agent_env = Path(__file__).resolve().parents[2] / ".env"
if _agent_env.is_file():
    load_dotenv(_agent_env, override=False)
skill_registry = SkillRegistry()
