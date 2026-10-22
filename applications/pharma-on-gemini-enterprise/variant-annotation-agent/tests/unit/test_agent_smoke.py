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

"""Smoke, workflow orchestration, artifact, and deployability tests for Variant Annotation Agent."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models import LlmRequest, LlmResponse
from google.adk.models.google_llm import Gemini
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.skill_toolset import SkillToolset
from google.adk.workflow import JoinNode, Workflow
from google.genai import types

from app.agent import (
    AGENT_DESCRIPTION,
    AGENT_DISPLAY_NAME,
    alphagenome_variant_agent,
    app,
    coordinator_agent,
    root_agent,
)
from app.app_utils.skills_loader import (
    CUSTOM_SKILL_NAMES,
    create_agent_skill_toolset,
    get_local_skill,
    strip_skill_prefix,
)
from app.sub_agents.database_lookup_agent import database_lookup_agent
from app.sub_agents.variant_annotation_pipeline import (
    PrepVariantRequest,
    annotation_critic,
    annotation_synthesizer,
    decide_critic_loop,
    join_annotator_findings,
    parallel_annotator_stage,
    report_refiner,
    variant_annotation_pipeline,
)
from app.sub_agents.variant_scoring_agent import variant_scoring_agent


class TestAgentSmoke:
    def test_root_workflow_initialization_and_gallery_metadata(self):
        assert root_agent is not None
        assert isinstance(root_agent, Workflow)
        assert root_agent.name == "alphagenome_variant_agent"
        assert alphagenome_variant_agent is root_agent
        assert app.root_agent is root_agent

        # Gallery card leads with "AlphaGenome" so ~20-char card titles never hide brand
        assert AGENT_DISPLAY_NAME.startswith("AlphaGenome")
        assert "rs12740374" in AGENT_DESCRIPTION
        assert "chr11:5227002:T>A" in AGENT_DESCRIPTION
        assert "samples/sample_alphagenome_variants.vcf" in AGENT_DESCRIPTION

        tf_main = (
            Path(__file__).resolve().parent.parent.parent / "terraform" / "main.tf"
        ).read_text(encoding="utf-8")
        assert 'display_name     = "AlphaGenome — Variant Annotation"' in tf_main
        assert "samples/sample_alphagenome_variants.vcf" in tf_main
        assert "Landrum et al." not in tf_main

    def test_sub_agents_and_thinking_planners_registered(self):
        sub_agent_names = [sa.name for sa in coordinator_agent.sub_agents]
        assert "variant_scoring_agent" in sub_agent_names
        assert "database_lookup_agent" in sub_agent_names

        for llm_agent in (
            coordinator_agent,
            variant_scoring_agent,
            database_lookup_agent,
            annotation_synthesizer,
            annotation_critic,
            report_refiner,
        ):
            assert isinstance(llm_agent.planner, BuiltInPlanner)
            assert llm_agent.planner.thinking_config is not None
            assert llm_agent.planner.thinking_config.include_thoughts is True

    def test_no_hardcoded_fallback_variants_in_instructions(self):
        for llm_agent in (
            coordinator_agent,
            variant_scoring_agent,
            database_lookup_agent,
            annotation_synthesizer,
            annotation_critic,
            report_refiner,
        ):
            instr = str(llm_agent.instruction)
            assert "rs121913527" not in instr
            assert "codon 146" not in instr

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
        ctx_refine = MagicMock()
        ctx_refine.state = {
            "critic_round": 0,
            "critic_feedback": "Missing gnomAD citation.",
        }
        ev1 = decide_critic_loop(ctx_refine, "draft")
        assert ev1.actions.route == "refine"
        assert ctx_refine.state["critic_round"] == 1

        ctx_done = MagicMock()
        ctx_done.state = {
            "critic_round": 0,
            "critic_feedback": "AUDIT_COMPLETE: The dossier meets all clinical criteria.",
        }
        ev2 = decide_critic_loop(ctx_done, "draft")
        assert ev2.actions.route == "finalize"

        ctx_max = MagicMock()
        ctx_max.state = {"critic_round": 1, "critic_feedback": "Minor tweak."}
        ev3 = decide_critic_loop(ctx_max, "draft")
        assert ev3.actions.route == "finalize"
        assert ctx_max.state["critic_round"] == 2

    def test_skills_loaded(self):
        skill_toolsets = [
            t for t in coordinator_agent.tools if isinstance(t, SkillToolset)
        ]
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
        instruction = str(coordinator_agent.instruction)
        assert "ClinVar (v.2024–06-03) (Landrum et al. 2018)" in instruction
        assert "gnomAD (v4) (Karczewski et al. 2020)" in instruction
        assert "GWAS Catalog (v.1.0) (Sollis et al. 2023)" in instruction
        assert "PharmGKB (Whirl-Carrillo et al. 2012, 2021)" in instruction

    @pytest.mark.asyncio
    async def test_prep_variant_request_short_circuits_on_gcs_error(self):
        prep = PrepVariantRequest(name="prep_variant_request")
        ctx = MagicMock()
        ctx.session.state = {}
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

    @pytest.mark.asyncio
    async def test_prep_variant_request_relative_path_and_bucket_listing_silent(self):
        prep = PrepVariantRequest(name="prep_variant_request")
        ctx = MagicMock()
        ctx.session.state = {}
        ctx.user_content = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "List the AlphaGenome output files in the bucket and annotate "
                        "samples/sample_alphagenome_variants.vcf"
                    )
                )
            ],
        )

        mock_list_res = {
            "status": "success",
            "bucket": "test-ag-bucket",
            "files": [
                {
                    "name": "samples/sample_alphagenome_variants.vcf",
                    "gcs_uri": "gs://test-ag-bucket/samples/sample_alphagenome_variants.vcf",
                    "size_bytes": 512,
                }
            ],
        }
        mock_import_res = {
            "status": "success",
            "source_gcs_uri": "gs://test-ag-bucket/samples/sample_alphagenome_variants.vcf",
            "format": "vcf",
            "count": 1,
            "variants": [
                {
                    "chromosome": "1",
                    "position": 109274968,
                    "ref": "G",
                    "alt": "T",
                    "rsid": "rs12740374",
                    "gene_symbol": "SORT1",
                }
            ],
        }

        with (
            patch(
                "app.sub_agents.variant_annotation_pipeline.list_alphagenome_bucket_files",
                return_value=mock_list_res,
            ) as mock_list,
            patch(
                "app.sub_agents.variant_annotation_pipeline.import_alphagenome_from_gcs",
                return_value=mock_import_res,
            ) as mock_imp,
        ):
            events = [ev async for ev in prep._run_async_impl(ctx)]

        mock_list.assert_called_once()
        mock_imp.assert_called_once_with("samples/sample_alphagenome_variants.vcf")
        assert len(events) == 1
        assert events[0].content is None
        assert events[0].actions.route == "annotate"
        saved_req = json.loads(events[0].actions.state_delta["variant_request"])
        assert saved_req["source"] == "gcs"
        assert (
            saved_req["gcs_uri"]
            == "gs://test-ag-bucket/samples/sample_alphagenome_variants.vcf"
        )
        assert events[0].actions.state_delta["bucket_inventory"] == mock_list_res

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "prompt_text",
        [
            "Annotate rs12740374 and chr1:109274968:G>T",
            "Annotate chr1:109274968:G>T and rs12740374",
        ],
    )
    async def test_prep_variant_request_deduplicates_rsid_and_coordinates(
        self, prompt_text: str
    ):
        prep = PrepVariantRequest(name="prep_variant_request")
        ctx = MagicMock()
        ctx.session.state = {}
        ctx.artifact_service = None
        ctx.user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt_text)],
        )

        mock_vep_hit = [
            {
                "id": "rs12740374",
                "seq_region_name": "1",
                "start": 109274968,
                "allele_string": "G/T",
                "most_severe_consequence": "3_prime_UTR_variant",
                "transcript_consequences": [
                    {
                        "gene_symbol": "CELSR2",
                        "transcript_id": "ENST00000271332",
                        "mane_select": "NM_001408.3",
                        "mane": ["MANE_Select"],
                        "canonical": 1,
                        "consequence_terms": ["3_prime_UTR_variant"],
                        "biotype": "protein_coding",
                    }
                ],
            }
        ]
        with patch(
            "app.tools.alphagenome_scoring.fetch_json",
            new_callable=AsyncMock,
            return_value=mock_vep_hit,
        ):
            events = [ev async for ev in prep._run_async_impl(ctx)]

        assert len(events) == 1
        assert events[0].content is None
        assert events[0].actions.route == "annotate"
        saved_req = json.loads(events[0].actions.state_delta["variant_request"])
        assert len(saved_req["variants"]) == 1
        merged = saved_req["variants"][0]
        assert merged["chromosome"] == "1"
        assert merged["position"] == 109274968
        assert merged["rsid"] == "rs12740374"
        assert merged["gene_symbol"] == "CELSR2"
        assert merged["transcript_id"] == "ENST00000271332"
        assert merged["mane_select"] == "NM_001408.3"
        assert merged["source"] == "ensembl_vep"
        assert merged["avi_score"] is None
        assert merged["scoring_source"] == "atlas_link_only"

    @pytest.mark.asyncio
    async def test_prep_variant_request_zero_variant_guard(self):
        prep = PrepVariantRequest(name="prep_variant_request")
        ctx = MagicMock()
        ctx.session.state = {}
        ctx.artifact_service = None
        ctx.user_content = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text="Please annotate my patient's unspecified mutation."
                )
            ],
        )

        events = [ev async for ev in prep._run_async_impl(ctx)]
        assert len(events) == 1
        assert events[0].actions.escalate is True
        assert events[0].actions.route == "error"
        assert "No resolvable genomic variants" in events[0].content.parts[0].text

    @pytest.mark.asyncio
    async def test_end_to_end_root_workflow_single_emission_and_native_artifacts(
        self, monkeypatch
    ):
        monkeypatch.setenv("ALPHAGENOME_GCS_BUCKET", "test-ag-bucket")

        user_prompt = (
            "List the AlphaGenome output files in the bucket and annotate "
            "samples/sample_alphagenome_variants.vcf"
        )

        async def _fake_generate_content_async(
            self: Gemini, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse]:
            del self, stream
            sys_instr = str(getattr(llm_request.config, "system_instruction", "") or "")
            if "Triage & Routing Guidelines" in sys_instr:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                text="Routing request to variant annotation pipeline.",
                                thought=True,
                            ),
                            types.Part.from_function_call(
                                name="launch_variant_annotation_pipeline",
                                args={"request": user_prompt},
                            ),
                        ],
                    )
                )
            elif "Senior Clinical Genomic Scientist" in sys_instr:
                dossier_draft = (
                    "# Genomic Variant Dossier\n\n"
                    "## 1. Variant Identity & AlphaGenome Atlas Deep-Links\n"
                    "- `rs12740374` (`chr1:109274968:G>T`, $SORT1$)\n"
                    "- [AlphaGenome Atlas Variant View](https://deepmind.google.com/science/alphagenome/atlas?variant=chr1:109274968:G>T)\n"
                    "- [AlphaGenome Atlas Locus View](https://deepmind.google.com/science/alphagenome/atlas?locus=chr1:109273968-109275968)\n\n"
                    "## 2. AlphaGenome Multimodal Effect & AVI Score Tiering\n"
                    "- Creates hepatic $CEBPA/CEBPB$ binding with $H3K27ac, H3K4me1$ chromatin activation (AVI Phred 27.45, High Impact).\n\n"
                    "## 3. ACMG/AMP Clinical Triage\n"
                    "- Benign common regulatory eQTL (BA1).\n\n"
                    "## 4. Complex Trait & GWAS Regulatory Mechanism\n"
                    "- LDL cholesterol association.\n\n"
                    "## 5. Pharmacogenomics & Star-Allele Actionability\n"
                    "- Statin response context.\n\n"
                    "## 6. Formal Database Citations\n"
                    "- ClinVar (v.2024–06-03) (Landrum et al. 2018)\n"
                    "- gnomAD (v4) (Karczewski et al. 2020)\n"
                    "- GWAS Catalog (v.1.0) (Sollis et al. 2023)\n"
                    "- PharmGKB (Whirl-Carrillo et al. 2012, 2021)"
                )
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                text="Synthesizing all 5 parallel genomic annotations.",
                                thought=True,
                            ),
                            types.Part.from_text(text=dossier_draft),
                        ],
                    )
                )
            else:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                text="Auditing clinical dossier criteria.",
                                thought=True,
                            ),
                            types.Part.from_text(
                                text="AUDIT_COMPLETE: The dossier meets all clinical criteria and citations."
                            ),
                        ],
                    )
                )

        mock_list_res = {
            "status": "success",
            "bucket": "test-ag-bucket",
            "files": [
                {
                    "name": "samples/sample_alphagenome_variants.vcf",
                    "gcs_uri": "gs://test-ag-bucket/samples/sample_alphagenome_variants.vcf",
                    "size_bytes": 640,
                }
            ],
        }
        mock_import_res = {
            "status": "success",
            "source_gcs_uri": "gs://test-ag-bucket/samples/sample_alphagenome_variants.vcf",
            "format": "vcf",
            "count": 1,
            "variants": [
                {
                    "chromosome": "1",
                    "position": 109274968,
                    "ref": "G",
                    "alt": "T",
                    "rsid": "rs12740374",
                    "gene_symbol": "CELSR2",
                    "transcript_id": "ENST00000271332",
                    "mane_select": "NM_001408.3",
                    "most_severe_consequence": "3_prime_UTR_variant",
                    "scoring_source": "alphagenome_model",
                    "avi_score": {
                        "raw_score": 0.884,
                        "quantile": 0.0018,
                        "top_percentile": 0.18,
                        "phred": 27.45,
                        "impact_tier": "High Impact",
                        "scoring_source": "alphagenome_model",
                    },
                    "modalities": {
                        "MAX_ABS_CHIP_TF": 0.94,
                        "MAX_ABS_ATAC": 0.89,
                        "MAX_ABS_DNASE": 0.84,
                    },
                    "atlas_links": {
                        "variant_url": "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109274968:G%3ET&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES",
                        "locus_url": "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109273968-109275968&m=locus&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES",
                    },
                }
            ],
        }
        mock_gnomad_var = {
            "status": "success",
            "variant_id": "1-109274968-G-T",
            "allele_frequency": 0.228,
            "populations": {"afr": {"af": 0.28}, "nfe": {"af": 0.21}},
            "acmg_frequency_tier": "BA1",
            "citation": "gnomAD (v4) (Karczewski et al. 2020)",
        }
        mock_save_gcs = {
            "status": "success",
            "bucket": "test-ag-bucket",
            "dossier_gcs_uri": "gs://test-ag-bucket/dossiers/20261021_dossier.md",
            "metadata_gcs_uri": "gs://test-ag-bucket/dossiers/20261021_metadata.json",
            "console_dossier_url": "https://console.cloud.google.com/storage/browser/_details/test-ag-bucket/dossiers/20261021_dossier.md",
            "console_folder_url": "https://console.cloud.google.com/storage/browser/test-ag-bucket/dossiers",
        }

        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        runner = Runner(
            app=app,
            session_service=session_service,
            artifact_service=artifact_service,
            auto_create_session=True,
        )

        with (
            patch.object(
                Gemini, "generate_content_async", _fake_generate_content_async
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.list_alphagenome_bucket_files",
                return_value=mock_list_res,
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.import_alphagenome_from_gcs",
                return_value=mock_import_res,
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.search_clinvar",
                new_callable=AsyncMock,
                return_value={"status": "success", "clinical_significance": "Benign"},
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.query_gnomad_variant",
                new_callable=AsyncMock,
                return_value=mock_gnomad_var,
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.query_gene_constraint",
                new_callable=AsyncMock,
                return_value={"status": "success", "symbol": "SORT1", "pli": 0.12},
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.query_gwas_catalog",
                new_callable=AsyncMock,
                return_value={"status": "success", "associations": []},
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.query_pharmgkb_variant",
                new_callable=AsyncMock,
                return_value={"status": "success", "evidence_level": None},
            ),
            patch(
                "app.sub_agents.variant_annotation_pipeline.save_dossier_to_gcs",
                return_value=mock_save_gcs,
            ),
        ):
            events = [
                ev
                async for ev in runner.run_async(
                    user_id="test_user",
                    session_id="test_session",
                    new_message=types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=user_prompt)],
                    ),
                )
            ]

        visible_events = []
        thought_texts = []
        for ev in events:
            if not ev.content or not ev.content.parts:
                continue
            for p in ev.content.parts:
                if getattr(p, "thought", False) and p.text:
                    thought_texts.append(p.text)
                elif p.text:
                    visible_events.append((ev.author, p.text))

        # Exactly ONE visible text emission (from finalize_variant_report) — zero duplication!
        assert len(visible_events) == 1
        author, final_markdown = visible_events[0]
        assert author == "finalize_variant_report"

        # Thought summaries preserved for Gemini Enterprise live progress UI
        assert len(thought_texts) >= 2

        # No leaked AUDIT_COMPLETE, unstripped $...$ math delimiters, or legacy ?variant= / ?locus= Atlas URLs
        assert "AUDIT_COMPLETE" not in final_markdown
        assert "$CEBPA/CEBPB$" not in final_markdown
        assert "$H3K27ac, H3K4me1$" not in final_markdown
        assert "?variant=" not in final_markdown
        assert "?locus=" not in final_markdown
        assert (
            "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109274968:G%3ET&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
            in final_markdown
        )
        assert (
            "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109273968-109275968&m=locus&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
            in final_markdown
        )
        assert final_markdown.startswith("\n\n")
        assert final_markdown.endswith("\n\n")

        # Combined bucket inventory + clickable Cloud Console HTTPS links preserved
        assert "Available AlphaGenome Bucket Files" in final_markdown
        assert (
            "[Open in Cloud Console](https://console.cloud.google.com/storage/browser/_details/test-ag-bucket/dossiers/20261021_dossier.md)"
            in final_markdown
        )

        # Native downloadable artifacts registered via save_artifact (.md + 2 PNG charts)
        artifact_keys = await artifact_service.list_artifact_keys(
            app_name=app.name,
            user_id="test_user",
            session_id="test_session",
        )
        assert "dossier_sample_alphagenome_variants.md" in artifact_keys
        assert "alphagenome_modalities_sample_alphagenome_variants.png" in artifact_keys
        assert "gnomad_ancestry_af_sample_alphagenome_variants.png" in artifact_keys

    def test_deployability_fastapi_a2a_and_adk_app(self, monkeypatch):
        from agentplatform.frameworks.adk import AdkApp
        from fastapi.testclient import TestClient

        from app.app_utils import services
        from app.fast_api_app import app as fastapi_app

        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
        monkeypatch.setenv("CLOUD_ML_PROJECT_ID", "test-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

        # 1. Verify Agent Runtime / Vertex AI AdkApp packaging & operations
        mock_auth = (MagicMock(), "test-project")
        with patch("google.auth.default", return_value=mock_auth):
            adk_runtime = AdkApp(
                app=app,
                session_service_builder=services.get_session_service,
                artifact_service_builder=services.get_artifact_service,
                instrumentor_builder=lambda _pid: None,
            )
            adk_runtime.set_up()
            ops = adk_runtime.register_operations()
        assert "stream_query" in ops.get(
            "stream", []
        ) or "async_stream_query" in ops.get("async_stream", [])

        # 2. Verify FastAPI lifespan + A2A v0.3 AgentCard registration
        mock_fb_logger = MagicMock()
        with (
            patch("app.fast_api_app.logger", mock_fb_logger),
            TestClient(fastapi_app) as client,
        ):
            card_resp = client.get("/a2a/app/.well-known/agent-card.json")
            assert card_resp.status_code == 200
            card_json = card_resp.json()
            assert card_json["name"] == "alphagenome_variant_agent"
            assert "rs12740374" in card_json["description"]
            assert card_json["capabilities"]["streaming"] is True

            fb_resp = client.post(
                "/feedback",
                json={
                    "score": 5,
                    "comment": "Smoke test",
                    "session_id": "sess-1",
                },
            )
            assert fb_resp.status_code == 200
            mock_fb_logger.log_struct.assert_called_once()
