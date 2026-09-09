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

import os
from pathlib import Path

from google.adk.skills import load_skills_from_dir
from google.adk.skills.models import Skill as AdkSkill
from google.adk.tools import FunctionTool
from google.adk.tools.skill_toolset import SkillToolset

from foldrun_app.skills.base import Skill
from foldrun_app.skills.cost_estimation.instruction import COST_ESTIMATION_INSTRUCTION
from foldrun_app.skills.cost_estimation.tools import (
    estimate_job_cost,
    estimate_monthly_cost,
    get_actual_job_costs,
)
from foldrun_app.skills.database_queries.instruction import DATABASE_QUERIES_INSTRUCTION
from foldrun_app.skills.database_queries.tools import (
    query_alphafold_db_annotations,
    query_alphafold_db_prediction,
    query_alphafold_db_summary,
)
from foldrun_app.skills.job_management.instruction import JOB_MANAGEMENT_INSTRUCTION
from foldrun_app.skills.job_management.tools import (
    check_af3_endpoint,
    check_gpu_quota,
    check_job_status,
    delete_job,
    deploy_af3_endpoint,
    get_job_details,
    list_jobs,
    undeploy_af3_endpoint,
)
from foldrun_app.skills.job_submission.instruction import JOB_SUBMISSION_INSTRUCTION
from foldrun_app.skills.job_submission.tools import (
    submit_af2_batch_predictions,
    submit_af2_monomer_prediction,
    submit_af2_multimer_prediction,
    submit_af3_endpoint_prediction,
    submit_boltz2_prediction,
    submit_of3_prediction,
)
from foldrun_app.skills.results_analysis.instruction import RESULTS_ANALYSIS_INSTRUCTION
from foldrun_app.skills.results_analysis.tools import (
    analyze_job,
    analyze_job_parallel,
    analyze_prediction_quality,
    boltz2_analyze_job_parallel,
    boltz2_get_analysis_results,
    get_af3_results,
    get_analysis_results,
    get_prediction_results,
    of3_analyze_job_parallel,
    of3_get_analysis_results,
)
from foldrun_app.skills.storage_management.instruction import STORAGE_MANAGEMENT_INSTRUCTION
from foldrun_app.skills.storage_management.tools import (
    cleanup_gcs_files,
    find_orphaned_gcs_files,
)
from foldrun_app.skills.visualization.instruction import VISUALIZATION_INSTRUCTION
from foldrun_app.skills.visualization.tools import (
    open_af3_structure_viewer,
    open_boltz2_structure_viewer,
    open_of3_structure_viewer,
    open_structure_viewer,
)

CORE_HEADER = """You are an expert FoldRun protein structure prediction assistant supporting AlphaFold2, AlphaFold3, OpenFold3, and Boltz-2.

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
        self._register_defaults()

    def _register_defaults(self):
        """Register the baseline FoldRun domain skills with environment-conditional tools."""
        has_of3 = bool(os.getenv("OPENFOLD3_COMPONENTS_IMAGE"))
        has_boltz2 = bool(os.getenv("BOLTZ2_COMPONENTS_IMAGE"))
        has_af3 = bool(
            os.getenv("AF3_ENDPOINT")
            or os.getenv("AF3_AGENT_PLATFORM_ENDPOINT")
            or os.getenv("AF3_ENDPOINT_ID")
            or os.getenv("AF3_VERTEX_ENDPOINT")
        )

        # Load instruction bodies from packages/ if available to keep SKILL.md as single source of truth
        adk_skills = {s.name.replace("-", "_"): s for s in self.load_adk_skills()}

        def _get_instruction(name: str, fallback: str) -> str:
            if name in adk_skills and getattr(adk_skills[name], "instructions", None):
                return adk_skills[name].instructions
            return fallback

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
        if has_af3:
            submission_tools.append(submit_af3_endpoint_prediction)

        self.register(
            Skill(
                name="job_submission",
                description="Submission of protein, RNA, ligand, and multimer predictions via AlphaFold2, AlphaFold3, OpenFold3, and Boltz-2",
                instruction=_get_instruction("job_submission", JOB_SUBMISSION_INSTRUCTION),
                tool_functions=submission_tools,
            )
        )

        management_tools = [
            check_gpu_quota,
            list_jobs,
            check_job_status,
            get_job_details,
            delete_job,
        ]
        if has_af3:
            management_tools.extend(
                [check_af3_endpoint, deploy_af3_endpoint, undeploy_af3_endpoint]
            )

        self.register(
            Skill(
                name="job_management",
                description="Job monitoring, GPU quota inspection, AF3 endpoint status, retry with caching, and job deletion",
                instruction=_get_instruction("job_management", JOB_MANAGEMENT_INSTRUCTION),
                tool_functions=management_tools,
            )
        )
        self.register(
            Skill(
                name="storage_management",
                description="GCS bucket hygiene, identifying orphaned prediction outputs, and safe two-step file cleanup",
                instruction=_get_instruction("storage_management", STORAGE_MANAGEMENT_INSTRUCTION),
                tool_functions=[
                    find_orphaned_gcs_files,
                    cleanup_gcs_files,
                ],
            )
        )

        # 4. Results & Analysis
        analysis_tools = [
            get_prediction_results,
            analyze_prediction_quality,
            analyze_job_parallel,
            get_analysis_results,
            analyze_job,
        ]
        if has_of3:
            analysis_tools.extend([of3_analyze_job_parallel, of3_get_analysis_results])
        if has_boltz2:
            analysis_tools.extend([boltz2_analyze_job_parallel, boltz2_get_analysis_results])
        if has_af3:
            analysis_tools.append(get_af3_results)

        self.register(
            Skill(
                name="results_analysis",
                description="Quality assessment (pLDDT, PAE, PDE, ipTM, pTM), AF3 confidence metrics, parallel batch analysis via Cloud Run, and troubleshooting",
                instruction=_get_instruction("results_analysis", RESULTS_ANALYSIS_INSTRUCTION),
                tool_functions=analysis_tools,
            )
        )

        # 5. Visualization
        vis_tools = [
            open_structure_viewer,
        ]
        if has_of3:
            vis_tools.append(open_of3_structure_viewer)
        if has_boltz2:
            vis_tools.append(open_boltz2_structure_viewer)
        if has_af3:
            vis_tools.append(open_af3_structure_viewer)

        self.register(
            Skill(
                name="visualization",
                description="Interactive 3D structure visualization URLs with Mol* for AF2, AF3, OF3, and Boltz-2",
                instruction=_get_instruction("visualization", VISUALIZATION_INSTRUCTION),
                tool_functions=vis_tools,
            )
        )
        self.register(
            Skill(
                name="cost_estimation",
                description="Per-job and monthly infrastructure cost modeling with DWS FLEX_START spot vs on-demand comparison",
                instruction=_get_instruction("cost_estimation", COST_ESTIMATION_INSTRUCTION),
                tool_functions=[
                    estimate_job_cost,
                    estimate_monthly_cost,
                    get_actual_job_costs,
                ],
            )
        )
        self.register(
            Skill(
                name="database_queries",
                description="AlphaFold DB structure lookups, annotations, and variant summary checks",
                instruction=_get_instruction("database_queries", DATABASE_QUERIES_INSTRUCTION),
                tool_functions=[
                    query_alphafold_db_summary,
                    query_alphafold_db_prediction,
                    query_alphafold_db_annotations,
                ],
            )
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

    def get_skill_toolset(self) -> SkillToolset:
        """Construct an ADK SkillToolset with progressive disclosure and dynamic additional tools."""
        skills = self.load_adk_skills()
        additional_tools = self.compile_tools()
        return SkillToolset(skills=skills, additional_tools=additional_tools)

    def compile_instruction(
        self,
        skill_names: list[str] | None = None,
        include_header: bool = True,
        include_guidelines: bool = True,
        progressive: bool = False,
    ) -> str:
        """Compile modular instructions for the requested skills.

        Args:
            skill_names: Optional list of skill names to compile. Defaults to all registered skills.
            include_header: Whether to prepend the core agent header.
            include_guidelines: Whether to append the response guidelines.
            progressive: If True, uses progressive disclosure by generating a concise
                skills overview manifest instead of concatenating full instruction bodies.
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


# Global registry instance
skill_registry = SkillRegistry()
