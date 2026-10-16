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

"""Annotation Agent for AlphaGenome on Gemini.

Engineered for deployment to Google Cloud Agent Development Kit (ADK)
and Agent Runtime.
"""

from __future__ import annotations

import logging
import os
import re

# Disable gcsfs ExtendedGcsFileSystem bucket-type probe (storagecontrol API)
os.environ.setdefault("GCSFS_EXPERIMENTAL_ZB_HNS_SUPPORT", "false")
logging.getLogger("gcsfs").setLevel(logging.ERROR)

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

from app.app_utils.models import PRO_MODEL, get_gemini_model
from app.app_utils.skills_loader import (
    CUSTOM_SKILL_NAMES,
    SCIENCE_SKILL_IDS,
    create_agent_skill_toolset,
)
from app.sub_agents.database_lookup_agent import database_lookup_agent
from app.sub_agents.variant_annotation_pipeline import variant_annotation_pipeline
from app.sub_agents.variant_scoring_agent import variant_scoring_agent
from app.tools.alphagenome_scoring import (
    build_alphagenome_atlas_url,
    compute_avi_score,
    parse_and_score_alphagenome_output,
    resolve_variant_coordinates,
)
from app.tools.clinvar import search_clinvar
from app.tools.gcs_bucket import (
    import_alphagenome_from_gcs,
    list_alphagenome_bucket_files,
)
from app.tools.gnomad import query_gene_constraint, query_gnomad_variant
from app.tools.gwas_catalog import query_gwas_catalog
from app.tools.pharmgkb import query_pharmgkb_variant

# Enforce global model location
os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("MODEL_LOCATION", "global")

# ---------------------------------------------------------------------------
# Load Custom Clinical Playbooks + Scoped Google Cloud Agent Registry Skills
# ---------------------------------------------------------------------------
agent_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=SCIENCE_SKILL_IDS,
    local_skill_names=CUSTOM_SKILL_NAMES,
)

# ---------------------------------------------------------------------------
# File Attachment Rehydration Callback for Gemini
# ---------------------------------------------------------------------------
_FILE_MARKER_RE = re.compile(r"<start_of_user_uploaded_file:\s*(?P<name>[^>]+?)\s*>")
_INLINEABLE_MIMES = ("image/", "application/pdf", "text/plain", "text/csv")


def _is_inlineable(mime: str | None) -> bool:
    return bool(mime) and any(mime.startswith(p) for p in _INLINEABLE_MIMES)


async def _inject_uploaded_artifacts(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Rehydrates user-uploaded files from Gemini ArtifactService."""
    if not llm_request.contents:
        return None
    artifact_keys = set(await callback_context.list_artifacts())
    for content in llm_request.contents:
        if getattr(content, "role", None) != "user" or not content.parts:
            continue
        injected: set[str] = set()
        for part in content.parts:
            text = part.text
            if not text:
                continue
            for match in _FILE_MARKER_RE.finditer(text):
                name = match.group("name").strip()
                if name in injected or name not in artifact_keys:
                    continue
                artifact = await callback_context.load_artifact(name)
                if artifact is None or artifact.inline_data is None:
                    continue
                if not _is_inlineable(artifact.inline_data.mime_type):
                    continue
                content.parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=artifact.inline_data.mime_type,
                            data=artifact.inline_data.data,
                        )
                    )
                )
                injected.add(name)
    return None


ROOT_INSTRUCTION = """You are the Annotation Agent for AlphaGenome — an AI system for precision genomics, clinical variant interpretation, and regulatory genomics.

Built with Google DeepMind's AlphaGenome foundation model, you analyze human genetic variation across 5 comprehensive pillars:
1. AlphaGenome Foundation Predictions: 18 multimodal tracks (splicing disruption, RNA-seq, ATAC/DNase chromatin accessibility, ChIP TF/histone marks, CAGE, AlphaMissense, conservation), AlphaGenome Variant Impact (AVI) score calculation across raw, quantile, and Phred scales, and AlphaGenome Atlas deep-links.
2. Clinical Assertions: NCBI ClinVar (v.2024–06-03) (Landrum et al. 2018) consensus classifications, gold star review status (0-4 stars), and disease phenotypes.
3. Population Genetics & Gene Constraint: Broad Institute gnomAD (v4) (Karczewski et al. 2020) joint/exome/genome allele frequencies across 8 continental ancestries, ACMG frequency criteria (BA1 >= 5%, BS1 >= 1%, PM2_Supporting <= 0.01% or absent), and pLI (>= 0.9) / LOEUF (< 0.35) LoF constraint.
4. Complex Disease & Regulatory GWAS: NHGRI-EBI GWAS Catalog (v.1.0) (Sollis et al. 2023) trait associations, p-values, odds ratios/betas, and functional molecular mechanisms.
5. Pharmacogenomics: PharmGKB (Whirl-Carrillo et al. 2012, 2021) evidence levels (1A to 4), VIP gene star-alleles, activity scores, and CPIC therapeutic dosing guidelines.

# Triage & Routing Guidelines

1. **Comprehensive Dossier & Pipeline Lane (Default for full analysis or GCS import)**:
   - When the user asks for a complete annotation report, ACMG triage, multi-omics dossier, or provides a `gs://` URI.
   - Invoke `variant_annotation_pipeline`. It automatically ingests GCS files, runs 5 specialized annotators in parallel, synthesizes a 6-section dossier, and passes through a 2-iteration critic loop.

2. **Scoring & GCS Storage Lane**:
   - For listing GCS buckets, ingesting raw AlphaGenome outputs, computing AVI Phred scores, or getting Atlas URLs.
   - Route to `variant_scoring_agent` or use `list_alphagenome_bucket_files` / `compute_avi_score`.

3. **Database Lookup Lane**:
   - For fast, focused lookups in a single database (e.g. "What does ClinVar say about rs121913527?", "What is the gnomAD frequency of this variant?", "Is this SNP in GWAS Catalog?").
   - Route to `database_lookup_agent` or use the respective direct tool (`search_clinvar`, `query_gnomad_variant`, `query_gene_constraint`, `query_gwas_catalog`, `query_pharmgkb_variant`).

4. **Tool Execution Rule**:
   - Always execute the direct Python function tools (`variant_annotation_pipeline`, `search_clinvar`, `query_gnomad_variant`, `query_gene_constraint`, `query_gwas_catalog`, `query_pharmgkb_variant`, `resolve_variant_coordinates`, `compute_avi_score`, `build_alphagenome_atlas_url`, `list_alphagenome_bucket_files`, `import_alphagenome_from_gcs`) to retrieve live genomic data.
   - Never use `load_skill_resource` to load `scripts/*.py` files, as all live API integrations are already built into your direct Python tools.

# Citations Requirement
Every report and claim MUST include the exact citations:
- ClinVar (v.2024–06-03) (Landrum et al. 2018)
- gnomAD (v4) (Karczewski et al. 2020)
- GWAS Catalog (v.1.0) (Sollis et al. 2023)
- PharmGKB (Whirl-Carrillo et al. 2012, 2021)
"""

alphagenome_variant_agent = Agent(
    model=get_gemini_model(PRO_MODEL),
    name="alphagenome_variant_agent",
    description=(
        "Annotation Agent for AlphaGenome: Multi-agent system for "
        "AlphaGenome foundation predictions, AVI Phred scoring, ClinVar, gnomAD v4, "
        "GWAS Catalog, and PharmGKB annotation."
    ),
    instruction=ROOT_INSTRUCTION,
    sub_agents=[variant_scoring_agent, database_lookup_agent],
    tools=[
        variant_annotation_pipeline,
        list_alphagenome_bucket_files,
        import_alphagenome_from_gcs,
        compute_avi_score,
        parse_and_score_alphagenome_output,
        build_alphagenome_atlas_url,
        resolve_variant_coordinates,
        search_clinvar,
        query_gnomad_variant,
        query_gene_constraint,
        query_gwas_catalog,
        query_pharmgkb_variant,
        agent_skill_toolset,
    ],
    before_model_callback=_inject_uploaded_artifacts,
)

root_agent = alphagenome_variant_agent
app = App(root_agent=alphagenome_variant_agent, name="alphagenome_variant_agent")
