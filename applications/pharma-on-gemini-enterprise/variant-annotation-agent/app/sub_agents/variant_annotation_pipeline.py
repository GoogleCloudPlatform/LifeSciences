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

"""Variant annotation pipeline orchestrating GCS ingestion, parallel database annotation, clinical synthesis, and a 2-iteration critic loop."""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.context import Context
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.workflow import START, JoinNode, Workflow
from google.genai import types

from app.app_utils.models import FLASH_MODEL, PRO_MODEL, get_gemini_model
from app.tools.alphagenome_scoring import (
    build_alphagenome_atlas_url,
    compute_avi_score,
    parse_and_score_alphagenome_output,
    resolve_variant_coordinates,
)
from app.tools.clinvar import search_clinvar
from app.tools.gcs_bucket import (
    _resolve_default_bucket,
    import_alphagenome_from_gcs,
    save_dossier_to_gcs,
)
from app.tools.gnomad import query_gene_constraint, query_gnomad_variant
from app.tools.gwas_catalog import query_gwas_catalog
from app.tools.pharmgkb import query_pharmgkb_variant

_GCS_REGEX = re.compile(r"gs://[^\s\"\'<>]+", re.IGNORECASE)
_VARIANT_TOKEN_RE = re.compile(
    r"\b(?:rs\d+|(?:chr)?(?:[0-9]{1,2}|X|Y|M|MT):\d+:[ACGTN]+>[ACGTN]+)\b",
    re.IGNORECASE,
)


class PrepVariantRequest(BaseAgent):
    """Auto-detects GCS URIs or variant coordinates in user queries and populates session state."""

    async def _run_impl(
        self,
        *,
        ctx: Context,
        node_input: Any = None,
    ) -> AsyncGenerator[Event]:
        if isinstance(node_input, str) and node_input.strip():
            ctx.state["_pipeline_node_input"] = node_input.strip()
        elif isinstance(node_input, dict) and node_input.get("request"):
            ctx.state["_pipeline_node_input"] = str(node_input["request"]).strip()
        async for event in super()._run_impl(ctx=ctx, node_input=node_input):
            yield event

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        prompt_parts: list[str] = []
        session_state = getattr(getattr(ctx, "session", None), "state", None)
        if isinstance(session_state, dict) and session_state.get(
            "_pipeline_node_input"
        ):
            prompt_parts.append(str(session_state["_pipeline_node_input"]))
        if ctx.user_content and ctx.user_content.parts:
            for part in ctx.user_content.parts:
                if getattr(part, "text", None) and part.text not in prompt_parts:
                    prompt_parts.append(part.text)
        prompt = "\n".join(prompt_parts).strip()

        state_delta: dict[str, object] = {
            "alphagenome_notes": "Pending AlphaGenome scoring.",
            "clinvar_notes": "Pending ClinVar lookup.",
            "gnomad_notes": "Pending gnomAD v4 lookup.",
            "gwas_notes": "Pending GWAS Catalog lookup.",
            "pharmgkb_notes": "Pending PharmGKB lookup.",
            "draft_report": "",
            "critic_feedback": "",
            "critic_round": 0,
        }

        gcs_matches = _GCS_REGEX.findall(prompt)
        if gcs_matches:
            gcs_uri = gcs_matches[0].rstrip(".,;:)")
            gcs_result = await asyncio.to_thread(import_alphagenome_from_gcs, gcs_uri)
            payload = {
                "source": "gcs",
                "user_prompt": prompt,
                "gcs_uri": gcs_uri,
                "parsed_data": gcs_result,
            }
            state_delta["variant_request"] = json.dumps(payload, indent=2)
            if gcs_result.get("status") != "success":
                err_msg = (
                    f"Failed importing AlphaGenome file from '{gcs_uri}': "
                    f"{gcs_result.get('message', 'Unknown error')}"
                )
                yield Event(
                    author=self.name,
                    output=err_msg,
                    content=types.Content(
                        role="model", parts=[types.Part.from_text(text=err_msg)]
                    ),
                    actions=EventActions(
                        state_delta=state_delta,
                        route="error",
                        escalate=True,
                    ),
                )
                return

            count = len(gcs_result.get("variants", []))
            msg = (
                f"Detected GCS URI '{gcs_uri}'. Imported and scored {count} variants "
                f"from AlphaGenome file into session state."
            )
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=msg)]
                ),
                actions=EventActions(state_delta=state_delta, route="annotate"),
            )
            return

        # Coordinate or rsID resolution (supports one or multiple variants in prompt)
        candidate_tokens: list[str] = []
        for m in _VARIANT_TOKEN_RE.finditer(prompt):
            tok = m.group(0).strip(".,;:()[]'\"")
            if tok and tok.lower() not in {t.lower() for t in candidate_tokens}:
                candidate_tokens.append(tok)

        resolved_variants: list[dict[str, object]] = []
        summaries: list[str] = []
        for clean_w in candidate_tokens:
            coord_res = await resolve_variant_coordinates(clean_w)
            if coord_res.get("status") == "success":
                resolved_variants.append(coord_res)
                chrom_clean = re.sub(
                    r"^chr",
                    "",
                    str(coord_res.get("chromosome", "")).strip(),
                    flags=re.IGNORECASE,
                )
                summaries.append(
                    f"'{clean_w}' -> chr{chrom_clean}:"
                    f"{coord_res.get('position')}:{coord_res.get('ref')}>{coord_res.get('alt')} "
                    f"(gene: {coord_res.get('gene_symbol')})"
                )

        if resolved_variants:
            first_coord = resolved_variants[0]
            payload = {
                "source": "direct_input",
                "user_prompt": prompt,
                "raw_identifier": candidate_tokens[0],
                "coordinates": first_coord,
                "variants": resolved_variants,
            }
            state_delta["variant_request"] = json.dumps(payload, indent=2)
            msg = f"Resolved genomic variant(s): {'; '.join(summaries)}. Stored in session state."
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=msg)]
                ),
                actions=EventActions(state_delta=state_delta, route="annotate"),
            )
            return

        state_delta["variant_request"] = json.dumps(
            {"source": "raw_prompt", "user_prompt": prompt}, indent=2
        )
        yield Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text="Variant request recorded in session state."
                    )
                ],
            ),
            actions=EventActions(state_delta=state_delta, route="annotate"),
        )


# 1. Parallel Specialized Annotator Agents (Flash Model + Direct Python Tools)
alphagenome_scorer = LlmAgent(
    name="alphagenome_scorer",
    description="Calculates AlphaGenome multimodal track deltas and AVI Phred impact tiers.",
    model=get_gemini_model(FLASH_MODEL),
    include_contents="none",
    output_key="alphagenome_notes",
    instruction="""You are the AlphaGenome Scoring Specialist.
Target variant request from session state:
{variant_request}

Instructions:
1. Inspect `variant_request` above. If `avi_score` and `atlas_links` are already present in `variant_request` (under `coordinates`, `variants`, or `parsed_data`), use those pre-computed metrics directly without redundant tool calls.
2. If any target variant lacks `avi_score` or `atlas_links`, call `resolve_variant_coordinates`, `compute_avi_score`, or `build_alphagenome_atlas_url` at most ONCE per variant, then immediately write your final summary. Do NOT loop on tools.
3. Check `scoring_source` on each variant or `avi_score`:
   - If `scoring_source == "ensembl_vep_heuristic"`, explicitly state that the AVI score is an **Ensembl VEP Consequence Heuristic** (derived from VEP consequence/SIFT/PolyPhen annotations because raw AlphaGenome 18-track model output files were not provided) rather than an AlphaGenome foundation model output.
   - If `scoring_source == "alphagenome_model"`, report the AlphaGenome 18-modality track deltas (`multimodal_tracks` / `modalities`).

Report for every target variant:
- Canonical GRCh38 coordinates (`chr:pos:ref>alt`), rsID, gene symbol, and HGVS consequence (`hgvsc`, `hgvsp`)
- Scoring source (`alphagenome_model` vs `ensembl_vep_heuristic`)
- AVI Raw, Quantile, and Phred scores (`Phred = -10 * log10(1 - quantile)`)
- AVI Impact Tier (`High Impact`: >=20.0 [top 1%], `Moderate-High Impact`: 15.0-19.99 [top 3.2%], `Moderate Impact`: 10.0-14.99 [top 10%], `Low / Baseline Impact`: <10.0)
- Perturbed biological tracks across the 18 AlphaGenome modalities when `scoring_source == "alphagenome_model"`, or VEP consequence/SIFT/PolyPhen summary when `scoring_source == "ensembl_vep_heuristic"`
- Clickable AlphaGenome Atlas deep-links (Variant View and Locus View)
""",
    tools=[
        resolve_variant_coordinates,
        compute_avi_score,
        parse_and_score_alphagenome_output,
        build_alphagenome_atlas_url,
    ],
)

clinvar_annotator = LlmAgent(
    name="clinvar_annotator",
    description="Queries NCBI ClinVar for clinical significance assertions and star ratings.",
    model=get_gemini_model(FLASH_MODEL),
    include_contents="none",
    output_key="clinvar_notes",
    instruction="""You are the ClinVar Clinical Annotation Specialist.
Target variant request from session state:
{variant_request}

Instructions:
1. Call `search_clinvar` ONCE for each target variant (pass the rsID such as `rs121913527` or coordinate/gene term).
2. Immediately after receiving the `search_clinvar` response, write your structured clinical report and finish your turn. Never repeat tool calls.

Report for each target variant:
- Germline clinical significance (Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign, Risk factor, Drug response)
- Review status and ClinVar Gold Star rating (0-4 stars: 4=practice guideline, 3=expert panel, 2=multiple submitters no conflicts, 1=single submitter or conflicting, 0=no assertion criteria)
- Associated disease traits/phenotypes and canonical SPDI / HGVS title
- Mandatory citation: ClinVar (v.2024–06-03) (Landrum et al. 2018)
""",
    tools=[
        search_clinvar,
        resolve_variant_coordinates,
    ],
)

gnomad_annotator = LlmAgent(
    name="gnomad_annotator",
    description="Queries Broad Institute gnomAD v4 for population frequencies and gene constraint.",
    model=get_gemini_model(FLASH_MODEL),
    include_contents="none",
    output_key="gnomad_notes",
    instruction="""You are the gnomAD Population Genetics Specialist.
Target variant request from session state:
{variant_request}

Instructions:
1. For each target variant in `variant_request`, call `query_gnomad_variant(chromosome, position, ref, alt)` and `query_gene_constraint(gene_symbol)` ONCE.
2. Immediately after receiving the tool responses, write your structured population genetics report and finish your turn. Never repeat tool calls.

Report for each target variant:
- Joint, exome, and genome allele frequencies (AF) and counts across continental ancestries (AFR, AMR, ASJ, EAS, FIN, MID, NFE, SAS)
- ACMG frequency tier (`BA1`: AF >= 0.05 Stand-alone Benign, `BS1`: AF >= 0.01 Strong Benign, `PM2_Supporting`: AF <= 0.0001 or absent in gnomAD v4)
- Gene constraint metrics: `pLI` (>= 0.9 = LoF intolerant) and `LOEUF` (< 0.35 = LoF intolerant; PVS1 eligible)
- Mandatory citation: gnomAD (v4) (Karczewski et al. 2020)
""",
    tools=[
        query_gnomad_variant,
        query_gene_constraint,
        resolve_variant_coordinates,
    ],
)

gwas_catalog_annotator = LlmAgent(
    name="gwas_catalog_annotator",
    description="Queries NHGRI-EBI GWAS Catalog for complex trait associations.",
    model=get_gemini_model(FLASH_MODEL),
    include_contents="none",
    output_key="gwas_notes",
    instruction="""You are the GWAS Complex Trait Specialist.
Target variant request from session state:
{variant_request}

Instructions:
1. Call `query_gwas_catalog(rs_id)` ONCE for each target variant with an rsID.
2. If `query_gwas_catalog` returns `count: 0` (common for rare Mendelian or somatic coding variants), immediately report that no genome-wide significant GWAS associations were found in GWAS Catalog for that variant, explain whether the variant acts primarily via a coding/Mendelian/somatic mechanism rather than a common complex-trait GWAS signal, include the mandatory citation, and finish your turn. NEVER retry or call additional tools when `count: 0`.

Report for each target variant:
- Significant trait associations and EFO ontology terms (or explicit note that 0 associations exist in GWAS Catalog)
- P-values, odds ratios (OR) / beta effect sizes, and risk alleles
- Study accessions and PubMed IDs
- Mandatory citation: GWAS Catalog (v.1.0) (Sollis et al. 2023)
""",
    tools=[
        query_gwas_catalog,
        resolve_variant_coordinates,
    ],
)

pharmgkb_annotator = LlmAgent(
    name="pharmgkb_annotator",
    description="Queries PharmGKB and CPIC for pharmacogenomic star-alleles and dosing guidelines.",
    model=get_gemini_model(FLASH_MODEL),
    include_contents="none",
    output_key="pharmgkb_notes",
    instruction="""You are the Pharmacogenomics (PGx) Specialist.
Target variant request from session state:
{variant_request}

Instructions:
1. Call `query_pharmgkb_variant(variant_or_rsid, gene_symbol)` ONCE for each target variant.
2. Immediately after receiving the tool response (even if no direct CPIC germline star-allele guideline exists for that gene/variant), write your structured pharmacogenomics summary, include the mandatory citation, and finish your turn. Note any relevant clinical/targeted-therapy context accurately (for example, for KRAS codon 146 p.Ala146 variants, note anti-EGFR monoclonal antibody resistance in colorectal cancer without conflating with KRAS G12C inhibitors).

Report for each target variant:
- Pharmacogene, star-allele assignment, and activity score
- PharmGKB clinical evidence level (1A, 1B, 2A, 2B, 3, 4, or None)
- Affected therapeutic drugs and CPIC dosing recommendations
- Mandatory citation: PharmGKB (Whirl-Carrillo et al. 2012, 2021)
""",
    tools=[
        query_pharmgkb_variant,
        resolve_variant_coordinates,
    ],
)

parallel_annotator_stage = (
    alphagenome_scorer,
    clinvar_annotator,
    gnomad_annotator,
    gwas_catalog_annotator,
    pharmgkb_annotator,
)

join_annotator_findings = JoinNode(
    name="join_annotator_findings",
    description="Synchronizes and merges findings from all five parallel genomic annotators.",
)

# 2. Clinical Genomic Synthesis Specialist
SYNTHESIZER_PROMPT = """You are the Senior Clinical Genomic Scientist.

Synthesize the multi-agent findings from all 5 specialized annotators below into an authoritative, publication-grade Genomic Variant Dossier.

### Target Variant Request:
{variant_request}

### 1. AlphaGenome Foundation Scoring Findings:
{alphagenome_notes}

### 2. ClinVar Clinical Significance Findings:
{clinvar_notes}

### 3. gnomAD v4 Population Genetics & Constraint Findings:
{gnomad_notes}

### 4. GWAS Catalog Complex Trait Findings:
{gwas_notes}

### 5. PharmGKB / CPIC Pharmacogenomics Findings:
{pharmgkb_notes}

---

Format your report into the following exact 6-section structure:

## 1. Variant Identity & AlphaGenome Atlas Deep-Links
- Canonical HGVS / GRCh38 Coordinates (chr:pos:ref>alt) and dbSNP rsID.
- Gene symbol, exact coding/protein consequence (`hgvsc` / `hgvsp`), and transcript ID.
- Direct Exploration Links:
  * Variant View: [AlphaGenome Atlas Variant View](https://deepmind.google.com/science/alphagenome/atlas?variant=...)
  * Locus View: [AlphaGenome Atlas Locus View](https://deepmind.google.com/science/alphagenome/atlas?locus=...)

## 2. AlphaGenome Multimodal Effect & AVI Score Tiering
- AVI Metric Table: Scoring Source (`AlphaGenome Foundation Model` vs `Ensembl VEP Consequence Heuristic`), Raw Score, Quantile, Phred Score (`Phred = -10 * log10(1 - quantile)`), and Impact Tier.
- Phred Tier Classification:
  * High Impact (>= 20.0, top 1%)
  * Moderate-High Impact (15.0 - 19.99, top 3.2%)
  * Moderate Impact (10.0 - 14.99, top 10%)
  * Low / Baseline Impact (< 10.0)
- Biological Modality Breakdown: When `scoring_source` is `alphagenome_model`, highlight key drivers among the 18 AlphaGenome tracks (Splicing, RNA-seq, ATAC/DNase chromatin accessibility, ChIP TF/Histone, AlphaMissense, Conservation). When `scoring_source` is `ensembl_vep_heuristic`, explicitly note that raw AlphaGenome 18-track outputs were not provided and present the Ensembl VEP consequence/SIFT/PolyPhen basis.

## 3. ACMG/AMP Clinical Triage
- ClinVar Consensus Classification and Review Status Gold Stars (0-4 stars).
- gnomAD v4 Population Genetics: Global and ancestry-specific allele frequencies (AF), allele counts, and homozygotes.
- Evaluated ACMG Criteria:
  * Population: BA1 (AF >= 5%), BS1 (AF >= 1%), or PM2_Supporting (AF <= 0.01% or absent).
  * Gene Constraint: pLI (>= 0.9) and LOEUF (< 0.35) for LoF intolerance and PVS1 eligibility.
  * In Silico: AlphaGenome AVI / VEP computational evidence as PP3 (pathogenic supporting) or BP4 (benign supporting).
- Integrated Final ACMG Recommendation (Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign).

## 4. Complex Trait & GWAS Regulatory Mechanism
- Genome-wide significant trait associations from GWAS Catalog (v.1.0) (Sollis et al. 2023).
- Effect sizes (odds ratios / beta), risk alleles, and p-values.
- Molecular mechanism hypothesis: How predicted regulatory or coding changes explain the phenotype signal.

## 5. Pharmacogenomics & Star-Allele Actionability
- PharmGKB Clinical Evidence Level (1A to 4).
- Star-allele designation and functional activity score.
- Impacted therapeutic medications and CPIC dosing guidance.

## 6. Formal Database Citations
Include the exact citations:
- ClinVar (v.2024–06-03) (Landrum et al. 2018)
- gnomAD (v4) (Karczewski et al. 2020)
- GWAS Catalog (v.1.0) (Sollis et al. 2023)
- PharmGKB (Whirl-Carrillo et al. 2012, 2021)
"""

annotation_synthesizer = LlmAgent(
    name="annotation_synthesizer",
    description="Synthesizes parallel annotator findings into a 6-section clinical genomic dossier.",
    model=get_gemini_model(PRO_MODEL),
    include_contents="none",
    output_key="draft_report",
    instruction=SYNTHESIZER_PROMPT,
)

# 3. Two-Iteration Critic Loop
annotation_critic = LlmAgent(
    name="annotation_critic",
    description="Audits the synthesized report for exact citations, ACMG compliance, AVI Phred calibration, and consistency.",
    model=get_gemini_model(FLASH_MODEL),
    include_contents="none",
    output_key="critic_feedback",
    instruction="""You are the Quality & Hallucination Auditor.

Audit the synthesized genomic report below against strict clinical criteria:

{draft_report}

Checklist:
1. Verify Exact Citations:
   - ClinVar (v.2024–06-03) (Landrum et al. 2018)
   - gnomAD (v4) (Karczewski et al. 2020)
   - GWAS Catalog (v.1.0) (Sollis et al. 2023)
   - PharmGKB (Whirl-Carrillo et al. 2012, 2021)
2. Verify AVI Phred Math and Tiering:
   - Check that AVI Phred = -10 * log10(1 - quantile).
   - Check that >=20 is High, 15-19.99 Moderate-High, 10-14.99 Moderate, <10 Low.
3. Verify ACMG Logic:
   - Check that BA1 is only applied for AF >= 5% and PM2_Supporting for AF <= 0.01% or absent.
   - Verify pLI >= 0.9 or LOEUF < 0.35 before endorsing LoF intolerance.

If the report meets all criteria with no issues, state: 'AUDIT_COMPLETE: The dossier meets all clinical criteria and citations.'
Otherwise, provide specific line-item corrections for the refiner.
""",
)

report_refiner = LlmAgent(
    name="report_refiner",
    description="Refines the clinical genomic dossier based on auditor critique.",
    model=get_gemini_model(FLASH_MODEL),
    include_contents="none",
    output_key="draft_report",
    instruction="""You are the Report Refiner.
Current Dossier:
{draft_report}

Auditor Critique:
{critic_feedback}

If the auditor responded with 'AUDIT_COMPLETE', output the complete Current Dossier unchanged.
If the auditor noted corrections or missing citations, fix them comprehensively and output the complete refined 6-section dossier.
""",
)


_MAX_CRITIC_ITERATIONS = 2


def decide_critic_loop(ctx: Context, node_input: Any) -> Event:
    """Routes the critic loop to 'finalize' when AUDIT_COMPLETE or max iterations reached, otherwise 'refine'."""
    iteration = int(ctx.state.get("critic_round", 0)) + 1
    ctx.state["critic_round"] = iteration
    feedback = str(ctx.state.get("critic_feedback", ""))
    if "AUDIT_COMPLETE" in feedback or iteration >= _MAX_CRITIC_ITERATIONS:
        return Event(
            output=node_input,
            actions=EventActions(route="finalize"),
        )
    return Event(
        output=node_input,
        actions=EventActions(route="refine"),
    )


class FinalizeVariantReport(BaseAgent):
    """Saves the validated dossier to Google Cloud Storage (dossiers/) and emits it as the terminal event."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        final_text = str(ctx.session.state.get("draft_report", "")).strip()
        if not final_text:
            return

        variant_label = "genomic_dossier"
        fallback_bucket = ""
        try:
            req_raw = str(ctx.session.state.get("variant_request", "{}") or "{}")
            req_data = json.loads(req_raw)
        except Exception:
            req_data = {}

        if req_data.get("source") == "gcs" and req_data.get("gcs_uri"):
            gcs_uri = str(req_data["gcs_uri"])
            m = re.match(r"^gs://([^/]+)/(.*)$", gcs_uri)
            if m:
                fallback_bucket = m.group(1)
                stem = pathlib.Path(m.group(2)).stem.replace(".vcf", "")
                if stem:
                    variant_label = stem
        elif req_data.get("raw_identifier"):
            variant_label = str(req_data["raw_identifier"])

        atlas_urls = sorted(
            set(
                re.findall(
                    r"https://deepmind\.google\.com/science/alphagenome/atlas\?[^\s\)\]\">]+",
                    final_text,
                )
            )
        )

        target_bucket = _resolve_default_bucket(fallback_bucket)
        if target_bucket:
            save_res = await asyncio.to_thread(
                save_dossier_to_gcs,
                dossier_markdown=final_text,
                variant_label=variant_label,
                metadata={
                    "request_source": req_data.get("source"),
                    "input_gcs_uri": req_data.get("gcs_uri"),
                    "raw_identifier": req_data.get("raw_identifier"),
                    "atlas_deep_links": atlas_urls,
                    "alphagenome_notes": ctx.session.state.get("alphagenome_notes", ""),
                    "clinvar_notes": ctx.session.state.get("clinvar_notes", ""),
                    "gnomad_notes": ctx.session.state.get("gnomad_notes", ""),
                    "gwas_notes": ctx.session.state.get("gwas_notes", ""),
                    "pharmgkb_notes": ctx.session.state.get("pharmgkb_notes", ""),
                },
                gcs_bucket=target_bucket,
                prefix="dossiers",
            )
            if save_res.get("status") == "success":
                final_text += (
                    "\n\n---\n"
                    "### Saved Output Artifacts in Google Cloud Storage\n"
                    f"* **Markdown Dossier (`.md`):** `{save_res['dossier_gcs_uri']}` "
                    f"([Open in Cloud Console]({save_res['console_dossier_url']}))\n"
                    f"* **Structured JSON Metadata & Atlas Links (`.json`):** `{save_res['metadata_gcs_uri']}`\n"
                    f"* **GCS Dossiers Folder:** [Browse `gs://{save_res['bucket']}/dossiers/`]({save_res['console_folder_url']})"
                )

        yield Event(
            author=self.name,
            output=final_text,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=final_text)]
            ),
        )


prep_variant_request = PrepVariantRequest(
    name="prep_variant_request",
    description="Inspects user queries for gs:// GCS URIs or genomic coordinates, pre-parsing data into session state.",
)

finalize_variant_report = FinalizeVariantReport(
    name="finalize_variant_report",
    description="Saves the validated dossier to Google Cloud Storage (dossiers/) and emits it as the terminal event.",
)

# Root Workflow Pipeline (ADK v2)
variant_annotation_pipeline = Workflow(
    name="variant_annotation_pipeline",
    description="End-to-end variant annotation pipeline: GCS pre-parsing, 5 parallel annotators, clinical synthesis, and 2-iteration critic loop.",
    input_schema=str,
    edges=[
        (START, prep_variant_request),
        (
            prep_variant_request,
            {"annotate": parallel_annotator_stage},
        ),
        (parallel_annotator_stage, join_annotator_findings),
        (join_annotator_findings, annotation_synthesizer),
        (annotation_synthesizer, annotation_critic),
        (annotation_critic, decide_critic_loop),
        (
            decide_critic_loop,
            {
                "refine": report_refiner,
                "finalize": finalize_variant_report,
            },
        ),
        (report_refiner, annotation_critic),
    ],
)
