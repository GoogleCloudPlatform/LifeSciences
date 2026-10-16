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

"""Smoke tests verifying agent structure, sub-agent hierarchy, tools, and skills loading."""

import pytest
from google.adk.workflow import JoinNode, Workflow

from app.agent import alphagenome_variant_agent, app, root_agent
from app.app_utils.skills_loader import (
    CUSTOM_SKILL_NAMES,
    create_agent_skill_toolset,
    get_local_skill,
    strip_skill_prefix,
)
from app.sub_agents.variant_annotation_pipeline import (
    decide_critic_loop,
    join_annotator_findings,
    parallel_annotator_stage,
    variant_annotation_pipeline,
)


class TestAgentSmoke:
    def test_root_agent_initialization(self):
        assert root_agent is not None
        assert root_agent.name == "alphagenome_variant_agent"
        assert alphagenome_variant_agent is root_agent
        assert app.root_agent is root_agent

    def test_sub_agents_registered(self):
        sub_agent_names = [sa.name for sa in root_agent.sub_agents]
        assert "variant_scoring_agent" in sub_agent_names
        assert "database_lookup_agent" in sub_agent_names

    def test_pipeline_structure(self):
        assert isinstance(variant_annotation_pipeline, Workflow)
        assert variant_annotation_pipeline.name == "variant_annotation_pipeline"
        assert isinstance(join_annotator_findings, JoinNode)
        assert len(parallel_annotator_stage) == 5

        assert variant_annotation_pipeline.graph is not None
        node_names = {node.name for node in variant_annotation_pipeline.graph.nodes}
        expected_nodes = {
            "__START__",
            "prep_variant_request",
            "alphagenome_scorer",
            "clinvar_annotator",
            "gnomad_annotator",
            "gwas_catalog_annotator",
            "pharmgkb_annotator",
            "join_annotator_findings",
            "annotation_synthesizer",
            "annotation_critic",
            "decide_critic_loop",
            "report_refiner",
            "finalize_variant_report",
        }
        assert expected_nodes.issubset(node_names)

    def test_decide_critic_loop_routes(self):
        from unittest.mock import MagicMock

        # 1. Round 1 with critique -> refine
        ctx_refine = MagicMock()
        ctx_refine.state = {
            "critic_round": 0,
            "critic_feedback": "Missing gnomAD citation.",
        }
        ev1 = decide_critic_loop(ctx_refine, "draft")
        assert ev1.actions.route == "refine"
        assert ctx_refine.state["critic_round"] == 1

        # 2. AUDIT_COMPLETE -> finalize early
        ctx_done = MagicMock()
        ctx_done.state = {
            "critic_round": 0,
            "critic_feedback": "AUDIT_COMPLETE: The dossier meets all clinical criteria.",
        }
        ev2 = decide_critic_loop(ctx_done, "draft")
        assert ev2.actions.route == "finalize"

        # 3. Max iterations reached -> finalize
        ctx_max = MagicMock()
        ctx_max.state = {"critic_round": 1, "critic_feedback": "Minor tweak."}
        ev3 = decide_critic_loop(ctx_max, "draft")
        assert ev3.actions.route == "finalize"
        assert ctx_max.state["critic_round"] == 2

    def test_skills_loaded(self):
        from google.adk.tools.skill_toolset import SkillToolset

        skill_toolsets = [t for t in root_agent.tools if isinstance(t, SkillToolset)]
        assert len(skill_toolsets) == 1
        st = skill_toolsets[0]
        assert len(st.skills) >= 4

        skill_names = {s.name for s in st.skills}
        assert "acmg-clinvar-gnomad-triage" in skill_names
        assert "alphagenome-avi-interpretation" in skill_names
        assert "gwas-noncoding-variant-mechanisms" in skill_names
        assert "pharmacogenomics-star-allele-review" in skill_names

    def test_local_skill_loading_and_prefix_stripping(self):
        assert strip_skill_prefix("private-clinvar-database") == "clinvar-database"
        assert (
            strip_skill_prefix("cloud.google.com-gnomad-database") == "gnomad-database"
        )
        assert strip_skill_prefix("gnomad-database") == "gnomad-database"

        for custom_name in CUSTOM_SKILL_NAMES:
            skill = get_local_skill(custom_name)
            assert skill.name == custom_name

        with pytest.raises(ValueError, match="Invalid skill name"):
            get_local_skill("../outside")

        toolset = create_agent_skill_toolset(
            local_skill_names=CUSTOM_SKILL_NAMES,
            include_registry=False,
        )
        assert len(toolset.skills) == 4

    def test_citations_present_in_root_instruction(self):
        instruction = root_agent.instruction
        assert "ClinVar (v.2024–06-03) (Landrum et al. 2018)" in instruction
        assert "gnomAD (v4) (Karczewski et al. 2020)" in instruction
        assert "GWAS Catalog (v.1.0) (Sollis et al. 2023)" in instruction
        assert "PharmGKB (Whirl-Carrillo et al. 2012, 2021)" in instruction

    @pytest.mark.asyncio
    async def test_prep_variant_request_short_circuits_on_gcs_error(self):
        from unittest.mock import MagicMock, patch

        from google.genai import types

        from app.sub_agents.variant_annotation_pipeline import PrepVariantRequest

        prep = PrepVariantRequest(name="prep_variant_request")
        ctx = MagicMock()
        ctx.user_content = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text="Annotate gs://bad-bucket/missing_variants.vcf"
                )
            ],
        )

        with patch(
            "app.sub_agents.variant_annotation_pipeline.import_alphagenome_from_gcs",
            return_value={
                "status": "error",
                "message": "Blob not found",
                "variants": [],
            },
        ):
            events = [ev async for ev in prep._run_async_impl(ctx)]

        assert len(events) == 1
        assert events[0].actions.escalate is True
        assert "Failed importing AlphaGenome file" in events[0].content.parts[0].text
