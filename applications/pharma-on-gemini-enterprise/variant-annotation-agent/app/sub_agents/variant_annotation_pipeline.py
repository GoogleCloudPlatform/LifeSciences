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
import inspect
import json
import pathlib
import re
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.context import Context
from google.adk.agents.invocation_context import InvocationContext
from google.adk.artifacts import BaseArtifactService
from google.adk.events import Event, EventActions
from google.adk.workflow import START, JoinNode, Workflow
from google.genai import types

from app.app_utils.formatting import (
    ensure_block_newlines,
    format_visible_markdown,
    normalize_alphagenome_atlas_urls,
    strip_biological_math_delimiters,
)
from app.app_utils.models import (
    FLASH_MODEL,
    PRO_MODEL,
    get_gemini_model,
    get_thinking_planner,
)
from app.tools.alphagenome_scoring import (
    build_alphagenome_atlas_url,
    parse_and_score_alphagenome_output,
    resolve_variant_coordinates,
)
from app.tools.charts import (
    build_alphagenome_modality_chart_png,
    build_avi_phred_comparison_chart_png,
    build_gnomad_ancestry_chart_png,
    slugify_artifact_label,
)
from app.tools.clinvar import CLINVAR_CITATION, search_clinvar
from app.tools.gcs_bucket import (
    _resolve_default_bucket,
    import_alphagenome_from_gcs,
    list_alphagenome_bucket_files,
    save_dossier_to_gcs,
)
from app.tools.gnomad import (
    GNOMAD_CITATION,
    query_gene_constraint,
    query_gnomad_variant,
)
from app.tools.gwas_catalog import GWAS_CATALOG_CITATION, query_gwas_catalog
from app.tools.pharmgkb import PHARMGKB_CITATION, query_pharmgkb_variant

_GCS_REGEX = re.compile(r"gs://[^\s\"\'<>`]+", re.IGNORECASE)
_HTTP_URL_REGEX = re.compile(r"https?://[^\s\"\'<>`]+", re.IGNORECASE)
_UPLOADED_FILE_MARKER_RE = re.compile(
    r"<start_of_user_uploaded_file:\s*(?P<name>[^>]+?)\s*>"
)
_RELATIVE_BLOB_REGEX = re.compile(
    r"(?<![:\w/])(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\.(?:vcf\.gz|vcf|jsonl|json|tsv|csv)\b",
    re.IGNORECASE,
)
_BUCKET_LIST_INTENT_RE = re.compile(
    r"\b(?:list|show|browse|inventory|what\s+files|available(?:\s+\w+){0,4}\s+files)\b[^\n]{0,100}\bbucket\b"
    r"|\bbucket\b[^\n]{0,100}\b(?:list|show|browse|inventory|available\s+files)\b",
    re.IGNORECASE,
)
_VARIANT_TOKEN_RE = re.compile(
    r"\b(?:rs\d+|(?:chr)?(?:[0-9]{1,2}|X|Y|M|MT):\d+:[ACGTN]+>[ACGTN]+)\b",
    re.IGNORECASE,
)


def _canonical_variant_key(variant: dict[str, Any]) -> tuple[str, int, str, str]:
    """Returns canonical `(CHROM_NO_CHR, POS, REF, ALT)` tuple for deduplication."""
    chrom = re.sub(
        r"^chr",
        "",
        str(variant.get("chromosome", "")).strip(),
        flags=re.IGNORECASE,
    ).upper()
    try:
        pos = int(variant.get("position") or 0)
    except (TypeError, ValueError):
        pos = 0
    ref = str(variant.get("ref", "")).strip().upper()
    alt = str(variant.get("alt", "")).strip().upper()
    return (chrom, pos, ref, alt)


def _merge_variant_records(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merges non-empty metadata across duplicate resolutions (e.g., coord + rsID)."""
    merged = dict(existing)
    for key, val in incoming.items():
        if val in (None, "", [], {}):
            continue
        cur = merged.get(key)
        if cur in (None, "", [], {}):
            merged[key] = val
        elif key == "source" and cur == "coordinate_format" and val == "ensembl_vep":
            merged[key] = val
    return merged


def _format_bucket_inventory_markdown(bucket_listing: dict[str, Any]) -> str:
    """Formats `list_alphagenome_bucket_files` output as a concise Markdown section."""
    if bucket_listing.get("status") != "success":
        return ""
    bucket_name = bucket_listing.get("bucket", "")
    files = bucket_listing.get("files") or []
    header = f"## Available AlphaGenome Bucket Files (`gs://{bucket_name}`)"
    if not files:
        return f"{header}\n\n- No supported AlphaGenome files found in `gs://{bucket_name}`."
    lines = [header, ""]
    for item in files:
        uri = item.get("gcs_uri") or item.get("name") or ""
        size_b = item.get("size_bytes")
        size_note = f" ({size_b:,} bytes)" if isinstance(size_b, int) else ""
        lines.append(f"- `{uri}`{size_note}")
    return "\n".join(lines)


def _extract_target_variants(ctx: InvocationContext) -> list[dict[str, Any]]:
    """Extracts resolved target variant dicts from `session.state['variant_request']`."""
    state = getattr(getattr(ctx, "session", None), "state", None)
    if not isinstance(state, dict):
        return []
    raw_req = state.get("variant_request")
    if not raw_req:
        return []
    try:
        req = json.loads(raw_req) if isinstance(raw_req, str) else raw_req
    except Exception:
        return []
    if not isinstance(req, dict):
        return []

    parsed_data = req.get("parsed_data")
    if isinstance(parsed_data, dict) and isinstance(parsed_data.get("variants"), list):
        return [v for v in parsed_data["variants"] if isinstance(v, dict)]

    variants = req.get("variants")
    if isinstance(variants, list):
        return [v for v in variants if isinstance(v, dict)]

    coords = req.get("coordinates")
    if isinstance(coords, dict) and coords.get("chromosome") and coords.get("position"):
        return [coords]

    return []


def _has_real_artifact_service(ctx: InvocationContext) -> bool:
    """Returns True when `ctx.artifact_service` is a concrete ADK ArtifactService or async mock."""
    svc = getattr(ctx, "artifact_service", None)
    if svc is None:
        return False
    if isinstance(svc, BaseArtifactService):
        return True
    return inspect.iscoroutinefunction(getattr(svc, "save_artifact", None))


class _SilentLlmAgent(LlmAgent):
    """Internal Workflow LlmAgent that writes to `output_key` in session state without streaming visible chat text.

    Preserves `thought=True` parts so Gemini Enterprise displays live thinking progress
    summaries, while stripping non-thought prose from `event.content` after ADK's
    `process_llm_agent_output` populates `ctx.actions.state_delta[output_key]`.
    """

    async def _run_impl(
        self,
        *,
        ctx: Context,
        node_input: Any = None,
    ) -> AsyncGenerator[Any]:
        async for event in super()._run_impl(ctx=ctx, node_input=node_input):
            content = getattr(event, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if not parts:
                yield event
                continue

            is_partial = bool(getattr(event, "partial", False))
            kept_parts = [
                p
                for p in parts
                if getattr(p, "thought", False)
                or getattr(p, "function_call", None) is not None
                or getattr(p, "function_response", None) is not None
            ]
            if getattr(event, "node_info", None) is not None:
                event.node_info.message_as_output = False

            if kept_parts:
                event.content = types.Content(role=content.role, parts=kept_parts)
                yield event
            else:
                if (
                    is_partial
                    and getattr(event, "output", None) is None
                    and not ctx.actions.state_delta
                    and not (
                        event.actions
                        and (
                            event.actions.state_delta
                            or event.actions.artifact_delta
                            or event.actions.route
                            or event.actions.escalate
                        )
                    )
                ):
                    continue
                event.content = None
                yield event


class PrepVariantRequest(BaseAgent):
    """Auto-detects GCS URIs, relative bucket paths, or variant coordinates and populates session state silently."""

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
            "_pipeline_node_input": "",
            "alphagenome_notes": "Pending AlphaGenome scoring.",
            "clinvar_notes": "Pending ClinVar lookup.",
            "gnomad_notes": "Pending gnomAD v4 lookup.",
            "gwas_notes": "Pending GWAS Catalog lookup.",
            "pharmgkb_notes": "Pending PharmGKB lookup.",
            "alphagenome_records": [],
            "gnomad_records": [],
            "bucket_inventory": None,
            "draft_report": "",
            "critic_feedback": "",
            "critic_round": 0,
        }

        # Optional bucket listing when user combines "list bucket files" + "annotate ..."
        bucket_listing: dict[str, Any] | None = None
        if _BUCKET_LIST_INTENT_RE.search(prompt):
            bucket_listing = await asyncio.to_thread(list_alphagenome_bucket_files)
            state_delta["bucket_inventory"] = bucket_listing

        # 1. Detect explicit gs:// URI or relative bucket blob path (.vcf/.json/.jsonl/.tsv/.csv)
        gcs_target: str | None = None
        gcs_matches = _GCS_REGEX.findall(prompt)
        if gcs_matches:
            gcs_target = gcs_matches[0].rstrip(".,;:)'\"`")
        else:
            scrubbed = _UPLOADED_FILE_MARKER_RE.sub(" ", prompt)
            scrubbed = _HTTP_URL_REGEX.sub(" ", scrubbed)
            rel_match = _RELATIVE_BLOB_REGEX.search(scrubbed)
            if rel_match:
                gcs_target = rel_match.group(0).rstrip(".,;:)'\"`")

        if gcs_target:
            gcs_result = await asyncio.to_thread(
                import_alphagenome_from_gcs, gcs_target
            )
            resolved_uri = str(gcs_result.get("source_gcs_uri") or gcs_target)
            payload: dict[str, Any] = {
                "source": "gcs",
                "user_prompt": prompt,
                "gcs_uri": resolved_uri,
                "parsed_data": gcs_result,
            }
            if bucket_listing is not None:
                payload["bucket_inventory"] = bucket_listing
            state_delta["variant_request"] = json.dumps(payload, indent=2)

            if gcs_result.get("status") != "success":
                err_msg = format_visible_markdown(
                    f"Failed importing AlphaGenome file from '{resolved_uri}': "
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

            if not gcs_result.get("variants"):
                empty_msg = format_visible_markdown(
                    f"AlphaGenome file '{resolved_uri}' contained zero valid variants to annotate."
                )
                yield Event(
                    author=self.name,
                    output=empty_msg,
                    content=types.Content(
                        role="model", parts=[types.Part.from_text(text=empty_msg)]
                    ),
                    actions=EventActions(
                        state_delta=state_delta,
                        route="error",
                        escalate=True,
                    ),
                )
                return

            yield Event(
                author=self.name,
                actions=EventActions(state_delta=state_delta, route="annotate"),
            )
            return

        # 2. Check uploaded composer file markers (<start_of_user_uploaded_file: ...>) if present
        uploaded_matches = list(_UPLOADED_FILE_MARKER_RE.finditer(prompt))
        if uploaded_matches and _has_real_artifact_service(ctx):
            cb_ctx = CallbackContext(ctx)
            for up_match in uploaded_matches:
                up_name = up_match.group("name").strip()
                artifact = await cb_ctx.load_artifact(up_name)
                blob_data = getattr(
                    getattr(artifact, "inline_data", None), "data", None
                )
                if blob_data:
                    parsed_up = parse_and_score_alphagenome_output(
                        blob_data, filename_hint=up_name
                    )
                    if parsed_up.get("status") == "success" and parsed_up.get(
                        "variants"
                    ):
                        payload = {
                            "source": "uploaded_artifact",
                            "user_prompt": prompt,
                            "raw_identifier": pathlib.Path(up_name).stem,
                            "parsed_data": parsed_up,
                        }
                        if bucket_listing is not None:
                            payload["bucket_inventory"] = bucket_listing
                        state_delta["variant_request"] = json.dumps(payload, indent=2)
                        yield Event(
                            author=self.name,
                            actions=EventActions(
                                state_delta=state_delta, route="annotate"
                            ),
                        )
                        return

        # 3. Coordinate or rsID resolution with canonical (chromosome, position, ref, alt) deduplication
        candidate_tokens: list[str] = []
        seen_tokens_lower: set[str] = set()
        for m in _VARIANT_TOKEN_RE.finditer(prompt):
            tok = m.group(0).strip(".,;:()[]'\"`")
            if tok and tok.lower() not in seen_tokens_lower:
                seen_tokens_lower.add(tok.lower())
                candidate_tokens.append(tok)

        deduped_by_coord: dict[tuple[str, int, str, str], dict[str, Any]] = {}
        primary_token_for_key: dict[tuple[str, int, str, str], str] = {}

        for clean_w in candidate_tokens:
            coord_res = await resolve_variant_coordinates(clean_w)
            if coord_res.get("status") != "success":
                continue
            canon_key = _canonical_variant_key(coord_res)
            if canon_key in deduped_by_coord:
                deduped_by_coord[canon_key] = _merge_variant_records(
                    deduped_by_coord[canon_key], coord_res
                )
                if clean_w.lower().startswith("rs") and not primary_token_for_key[
                    canon_key
                ].lower().startswith("rs"):
                    primary_token_for_key[canon_key] = clean_w
            else:
                deduped_by_coord[canon_key] = dict(coord_res)
                primary_token_for_key[canon_key] = clean_w

        resolved_variants = list(deduped_by_coord.values())
        if resolved_variants:
            first_key = next(iter(deduped_by_coord.keys()))
            first_coord = resolved_variants[0]
            primary_ident = (
                first_coord.get("rsid")
                or primary_token_for_key[first_key]
                or candidate_tokens[0]
            )
            payload = {
                "source": "direct_input",
                "user_prompt": prompt,
                "raw_identifier": str(primary_ident),
                "coordinates": first_coord,
                "variants": resolved_variants,
            }
            if bucket_listing is not None:
                payload["bucket_inventory"] = bucket_listing
            state_delta["variant_request"] = json.dumps(payload, indent=2)
            yield Event(
                author=self.name,
                actions=EventActions(state_delta=state_delta, route="annotate"),
            )
            return

        # 4. Inline VCF / JSON payload pasted directly in prompt
        if "#CHROM" in prompt or (
            ("{" in prompt and "}" in prompt) and "avi_" in prompt.lower()
        ):
            inline_parsed = parse_and_score_alphagenome_output(prompt)
            if inline_parsed.get("status") == "success" and inline_parsed.get(
                "variants"
            ):
                payload = {
                    "source": "inline_payload",
                    "user_prompt": prompt,
                    "raw_identifier": "inline_variants",
                    "parsed_data": inline_parsed,
                }
                if bucket_listing is not None:
                    payload["bucket_inventory"] = bucket_listing
                state_delta["variant_request"] = json.dumps(payload, indent=2)
                yield Event(
                    author=self.name,
                    actions=EventActions(state_delta=state_delta, route="annotate"),
                )
                return

        # 5. Bucket-only listing or zero-variant guard (never fan out with 0 variants)
        if bucket_listing is not None and bucket_listing.get("status") == "success":
            inv_md = format_visible_markdown(
                _format_bucket_inventory_markdown(bucket_listing)
            )
            yield Event(
                author=self.name,
                output=inv_md,
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=inv_md)]
                ),
                actions=EventActions(
                    state_delta=state_delta,
                    route="done",
                    escalate=True,
                ),
            )
            return

        guard_msg = format_visible_markdown(
            "No resolvable genomic variants or AlphaGenome output files were found in your request. "
            "Please provide a dbSNP rsID (e.g., `rs12740374`), a GRCh38 coordinate "
            "(e.g., `chr11:5227002:T>A`), or a GCS file path "
            "(e.g., `samples/sample_alphagenome_variants.vcf` or `gs://<bucket>/<path>`)."
        )
        state_delta["variant_request"] = json.dumps(
            {"source": "unresolved", "user_prompt": prompt, "variants": []}, indent=2
        )
        yield Event(
            author=self.name,
            output=guard_msg,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=guard_msg)]
            ),
            actions=EventActions(
                state_delta=state_delta,
                route="error",
                escalate=True,
            ),
        )


# 1. Silent Parallel Specialized Annotator Nodes (Deterministic Async API Execution)
class _AlphaGenomeScorerNode(BaseAgent):
    """Computes/formats AlphaGenome 18-modality track deltas, AVI Phred tiers, and Atlas deep-links into session state."""

    async def _score_one(self, var: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(var)
        chrom = str(enriched.get("chromosome") or "").strip()
        pos = int(enriched.get("position") or 0)
        ref = str(enriched.get("ref") or "").strip().upper()
        alt = str(enriched.get("alt") or "").strip().upper()

        need_vep = (
            not chrom
            or pos <= 0
            or not enriched.get("transcript_id")
            or not enriched.get("most_severe_consequence")
        )
        if need_vep and enriched.get("rsid"):
            resolved = await resolve_variant_coordinates(str(enriched["rsid"]))
            if resolved.get("status") == "success":
                enriched = _merge_variant_records(enriched, resolved)
                chrom = str(enriched.get("chromosome") or "").strip()
                pos = int(enriched.get("position") or 0)
                ref = str(enriched.get("ref") or "").strip().upper()
                alt = str(enriched.get("alt") or "").strip().upper()

        if (
            (not enriched.get("transcript_id") or not enriched.get("gene_symbol"))
            and chrom
            and pos > 0
            and ref
            and alt
        ):
            coord_str = f"chr{re.sub(r'^chr', '', chrom, flags=re.IGNORECASE)}:{pos}:{ref}>{alt}"
            resolved = await resolve_variant_coordinates(coord_str)
            if resolved.get("status") == "success":
                enriched = _merge_variant_records(enriched, resolved)

        if not enriched.get("avi_score"):
            enriched["avi_score"] = None
            enriched["scoring_source"] = "atlas_link_only"
        else:
            enriched.setdefault("scoring_source", "alphagenome_model")

        if not enriched.get("atlas_links") and chrom and pos > 0 and ref and alt:
            enriched["atlas_links"] = build_alphagenome_atlas_url(chrom, pos, ref, alt)

        modalities = (
            enriched.get("modalities") or enriched.get("multimodal_tracks") or {}
        )
        enriched["modalities"] = modalities
        return enriched

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        variants = _extract_target_variants(ctx)
        if not variants:
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={
                        "alphagenome_notes": "No resolved target variants were provided in the request.",
                        "alphagenome_records": [],
                    }
                ),
            )
            return

        scored = list(await asyncio.gather(*(self._score_one(v) for v in variants)))
        sections: list[str] = []
        for v in scored:
            chrom_clean = re.sub(
                r"^chr", "", str(v.get("chromosome", "")), flags=re.IGNORECASE
            )
            coord = (
                f"chr{chrom_clean}:{v.get('position')}:{v.get('ref')}>{v.get('alt')}"
            )
            avi = v.get("avi_score")
            has_avi = isinstance(avi, dict) and avi.get("phred") is not None
            atlas = v.get("atlas_links") or {}
            modalities = v.get("modalities") or {}
            mane_note = (
                f" (MANE Select: {v.get('mane_select')})"
                if v.get("mane_select")
                else ""
            )
            lines = [
                f"### Variant: {coord} (rsID: {v.get('rsid') or 'N/A'}, Gene: {v.get('gene_symbol') or 'Unknown'})",
                f"- Transcript / HGVS: transcript={v.get('transcript_id') or 'N/A'}{mane_note}, hgvsc={v.get('hgvsc') or 'N/A'}, hgvsp={v.get('hgvsp') or 'N/A'}, consequence={v.get('consequence') or v.get('most_severe_consequence') or 'N/A'}, impact={v.get('impact') or 'N/A'}",
            ]
            if has_avi:
                lines.extend(
                    [
                        "- Scoring Source: alphagenome_model (Pre-computed AlphaGenome output file)",
                        (
                            f"- AVI Metrics: raw_score={avi.get('raw_score')}, "
                            f"avi_quantile={avi.get('quantile')} (tail quantile, top {avi.get('top_percentile')}%), "
                            f"avi_phred={avi.get('phred')}, impact_tier={avi.get('impact_tier')}"
                        ),
                    ]
                )
            else:
                lines.extend(
                    [
                        (
                            "- Scoring Source: atlas_link_only (No pre-computed AlphaGenome AVI/18-track file "
                            "was supplied in this request. DO NOT invent, simulate, or estimate numeric AVI "
                            "raw, quantile, Phred, or 18-modality track scores. Report Ensembl VEP consequence "
                            "annotations and direct the user to open the AlphaGenome Atlas deep-links below for "
                            "live interactive AVI and 18-modality predictions.)"
                        ),
                        "- AVI Numeric Scores: Not supplied in input (inspect live in AlphaGenome Atlas via the Variant & Locus deep-links below)",
                    ]
                )
            lines.extend(
                [
                    f"- AlphaGenome Atlas Variant View: {atlas.get('variant_url', 'N/A')}",
                    f"- AlphaGenome Atlas Locus View: {atlas.get('locus_url', 'N/A')}",
                ]
            )
            if modalities:
                top_tracks = sorted(
                    modalities.items(), key=lambda kv: abs(float(kv[1])), reverse=True
                )
                track_summary = ", ".join(
                    f"{k}={float(val):.4f}" for k, val in top_tracks
                )
                lines.append(f"- 18-Modality Track Deltas: {track_summary}")
            if v.get("sift_prediction") or v.get("polyphen_prediction"):
                lines.append(
                    f"- VEP In-Silico Basis: SIFT={v.get('sift_prediction')} ({v.get('sift_score')}), "
                    f"PolyPhen={v.get('polyphen_prediction')} ({v.get('polyphen_score')})"
                )
            if v.get("mechanism"):
                lines.append(f"- Mechanism Context: {v['mechanism']}")
            sections.append("\n".join(lines))

        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "alphagenome_notes": "\n\n".join(sections),
                    "alphagenome_records": scored,
                }
            ),
        )


class _ClinVarAnnotatorNode(BaseAgent):
    """Queries NCBI ClinVar concurrently for all resolved target variants into session state."""

    async def _lookup_one(self, var: dict[str, Any]) -> str:
        chrom_clean = re.sub(
            r"^chr", "", str(var.get("chromosome", "")), flags=re.IGNORECASE
        )
        pos_val = int(var.get("position") or 0) or None
        ref_val = str(var.get("ref") or "").strip().upper() or None
        alt_val = str(var.get("alt") or "").strip().upper() or None
        coord = (
            f"chr{chrom_clean}:{var.get('position')}:{var.get('ref')}>{var.get('alt')}"
        )
        query_term = str(var.get("rsid") or coord)
        res = await search_clinvar(
            query_term,
            chromosome=chrom_clean or None,
            position=pos_val,
            ref=ref_val,
            alt=alt_val,
        )
        return (
            f"### ClinVar Lookup for {coord} (query: {query_term})\n"
            f"{json.dumps(res, indent=2)}"
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        variants = _extract_target_variants(ctx)
        if not variants:
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={
                        "clinvar_notes": "No resolved target variants were provided in the request."
                    }
                ),
            )
            return

        blocks = list(await asyncio.gather(*(self._lookup_one(v) for v in variants)))
        blocks.append(f"Mandatory citation: {CLINVAR_CITATION}")
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={"clinvar_notes": "\n\n".join(blocks)}),
        )


class _GnomadAnnotatorNode(BaseAgent):
    """Queries Broad Institute gnomAD v4 allele frequencies and gene constraint concurrently into session state."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        variants = _extract_target_variants(ctx)
        if not variants:
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={
                        "gnomad_notes": "No resolved target variants were provided in the request.",
                        "gnomad_records": [],
                    }
                ),
            )
            return

        async def _fetch_var(var: dict[str, Any]) -> dict[str, Any]:
            chrom = str(var.get("chromosome") or "").strip()
            pos = int(var.get("position") or 0)
            ref = str(var.get("ref") or "").strip()
            alt = str(var.get("alt") or "").strip()
            if not (chrom and pos > 0 and ref and alt):
                return {"status": "skipped", "reason": "Missing coordinates"}
            res = await query_gnomad_variant(chrom, pos, ref, alt)
            res["label"] = str(
                var.get("rsid")
                or f"chr{re.sub(r'^chr', '', chrom, flags=re.IGNORECASE)}:{pos}:{ref}>{alt}"
            )
            return res

        unique_genes: list[str] = []
        for v in variants:
            gene = str(v.get("gene_symbol") or "").strip()
            if gene and gene not in unique_genes:
                unique_genes.append(gene)

        var_results, gene_results = await asyncio.gather(
            asyncio.gather(*(_fetch_var(v) for v in variants)),
            asyncio.gather(*(query_gene_constraint(g) for g in unique_genes)),
        )

        notes_payload = {
            "variant_frequencies": list(var_results),
            "gene_constraints": list(gene_results),
            "citation": GNOMAD_CITATION,
        }
        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "gnomad_notes": json.dumps(notes_payload, indent=2),
                    "gnomad_records": list(var_results),
                }
            ),
        )


class _GwasAnnotatorNode(BaseAgent):
    """Queries NHGRI-EBI GWAS Catalog concurrently for all resolved target variants into session state."""

    async def _lookup_one(self, var: dict[str, Any]) -> dict[str, Any]:
        chrom_clean = re.sub(
            r"^chr", "", str(var.get("chromosome", "")), flags=re.IGNORECASE
        )
        coord = (
            f"chr{chrom_clean}:{var.get('position')}:{var.get('ref')}>{var.get('alt')}"
        )
        rsid = str(var.get("rsid") or "").strip()
        if not rsid.lower().startswith("rs"):
            return {
                "variant": coord,
                "rs_id": None,
                "count": 0,
                "associations": [],
                "note": "No dbSNP rsID mapped for this coordinate; 0 genome-wide significant GWAS Catalog associations.",
                "citation": GWAS_CATALOG_CITATION,
            }
        return await query_gwas_catalog(rsid)

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        variants = _extract_target_variants(ctx)
        if not variants:
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={
                        "gwas_notes": "No resolved target variants were provided in the request."
                    }
                ),
            )
            return

        results = list(await asyncio.gather(*(self._lookup_one(v) for v in variants)))
        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "gwas_notes": json.dumps(
                        {"gwas_results": results, "citation": GWAS_CATALOG_CITATION},
                        indent=2,
                    )
                }
            ),
        )


class _PharmGkbAnnotatorNode(BaseAgent):
    """Queries PharmGKB and CPIC concurrently for all resolved target variants into session state."""

    async def _lookup_one(self, var: dict[str, Any]) -> dict[str, Any]:
        chrom_clean = re.sub(
            r"^chr", "", str(var.get("chromosome", "")), flags=re.IGNORECASE
        )
        coord = (
            f"chr{chrom_clean}:{var.get('position')}:{var.get('ref')}>{var.get('alt')}"
        )
        ident = str(var.get("rsid") or coord)
        gene = str(var.get("gene_symbol") or "").strip() or None
        return await query_pharmgkb_variant(ident, gene_symbol=gene)

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        variants = _extract_target_variants(ctx)
        if not variants:
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={
                        "pharmgkb_notes": "No resolved target variants were provided in the request."
                    }
                ),
            )
            return

        results = list(await asyncio.gather(*(self._lookup_one(v) for v in variants)))
        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "pharmgkb_notes": json.dumps(
                        {"pharmgkb_results": results, "citation": PHARMGKB_CITATION},
                        indent=2,
                    )
                }
            ),
        )


alphagenome_scorer = _AlphaGenomeScorerNode(
    name="alphagenome_scorer",
    description="Calculates AlphaGenome multimodal track deltas and AVI Phred impact tiers silently into session state.",
)

clinvar_annotator = _ClinVarAnnotatorNode(
    name="clinvar_annotator",
    description="Queries NCBI ClinVar for clinical significance assertions and star ratings silently into session state.",
)

gnomad_annotator = _GnomadAnnotatorNode(
    name="gnomad_annotator",
    description="Queries Broad Institute gnomAD v4 for population frequencies and gene constraint silently into session state.",
)

gwas_catalog_annotator = _GwasAnnotatorNode(
    name="gwas_catalog_annotator",
    description="Queries NHGRI-EBI GWAS Catalog for complex trait associations silently into session state.",
)

pharmgkb_annotator = _PharmGkbAnnotatorNode(
    name="pharmgkb_annotator",
    description="Queries PharmGKB and CPIC for pharmacogenomic star-alleles and dosing guidelines silently into session state.",
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

# 2. Clinical Genomic Synthesis Specialist (Silent internal stage — emitted once in FinalizeVariantReport)
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

STRICT FORMATTING & GROUNDING RULES:
- NEVER wrap gene symbols, transcription factors, protein names, HGVS notations, rsIDs, genomic coordinates, or histone/epigenetic marks (such as `SORT1`, `CELSR2`, `CEBPA/CEBPB`, `CYP2C19*2`, `p.Glu7Val`, `H3K27ac`, `H3K4me1`) in dollar-sign math delimiters (`$...$` or `$$...$$`). Use plain text or Markdown backticks ONLY — dollar delimiters trigger broken KaTeX equation rendering in Gemini Enterprise.
- Do NOT use LaTeX/MathJax syntax anywhere in the dossier; write formulas in backticks (`Phred = -10 * log10(avi_quantile)`, where `avi_quantile` is the tail quantile `1 - CDF = 10^(-Phred / 10)`).
- Never invent or substitute example variants not present in `Target Variant Request` above.
- NEVER fabricate, simulate, or estimate numeric `AVI Raw`, `AVI Quantile`, `AVI Phred`, or `18-Modality Track` scores when `Scoring Source` is `atlas_link_only` (i.e., when no AlphaGenome score file was provided). Only report numeric AVI / modality values when `Scoring Source` is `alphagenome_model`.

Format your report into the following exact 6-section structure:

## 1. Variant Identity & AlphaGenome Atlas Deep-Links
- Canonical HGVS / GRCh38 Coordinates (chr:pos:ref>alt) and dbSNP rsID.
- Overlapping host gene symbol, MANE Select / canonical Ensembl & RefSeq transcript ID, and exact coding/UTR/non-coding consequence (`hgvsc` / `hgvsp`) from Ensembl VEP (note any distinct distal eQTL regulatory target gene separately if applicable).
- Direct Exploration Links (copy the exact URLs from `AlphaGenome Foundation Scoring Findings` verbatim; never use `?variant=` or `?locus=`):
  * Variant View: [AlphaGenome Atlas Variant View](https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109274968:G%3ET&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES)
  * Locus View: [AlphaGenome Atlas Locus View](https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109273968-109275968&m=locus&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES)

## 2. AlphaGenome Multimodal Effect & AVI Score Tiering
- **When `Scoring Source` is `alphagenome_model` (pre-computed AlphaGenome file supplied)**:
  * Present the AVI Metric Table: `avi_raw`, `avi_quantile` (tail quantile `1 - CDF`, smaller = stronger impact), `top_percentile` (`avi_quantile * 100%`), `avi_phred` (`Phred = -10 * log10(avi_quantile)`), and Impact Tier (`High >= 20.0` [top 1%], `Moderate-High 15.0 - 19.99` [top 3.2%], `Moderate 10.0 - 14.99` [top 10%], `Low < 10.0`).
  * Highlight key biological drivers across the 18 AlphaGenome tracks (Splicing, RNA-seq, ATAC/DNase chromatin accessibility, ChIP TF/Histone, AlphaMissense, Conservation).
- **When `Scoring Source` is `atlas_link_only` (bare rsID or coordinate query without an uploaded AlphaGenome score file)**:
  * Explicitly state that no pre-computed AlphaGenome numeric score file was supplied in the prompt, so numeric AVI and 18-modality values are not fabricated.
  * Direct the user to open the clickable AlphaGenome Atlas Variant & Locus deep-links in Section 1 to inspect live interactive AVI Phred and 18-modality track predictions directly in Atlas.
  * Summarize the live Ensembl VEP functional annotation (consequence class, impact tier, and SIFT / PolyPhen-2 predictions if coding).

## 3. ACMG/AMP Clinical Triage
- ClinVar Consensus Classification, Variation ID, and Review Status Gold Stars (0-4 stars), prioritizing canonical single-variant germline assertions over compound multi-variant haplotypes.
- gnomAD v4 Population Genetics: Joint, exome, and genome allele frequencies (AF), allele counts (AC/AN), homozygotes, filtering allele frequencies (`faf95` / `faf99`), and continental ancestry breakdown.
- Evaluated ACMG Criteria:
  * Population: BA1 (AF >= 5%), BS1 (AF >= 1%), or PM2_Supporting (AF <= 0.01% or absent).
  * Gene Constraint: gnomAD `pLI` (>= 0.9), `LOEUF` (`oe_lof_upper` < 0.35), and missense Z (`mis_z`) for LoF/missense intolerance and PVS1 eligibility.
  * In Silico: Computational evidence (AlphaGenome AVI when provided, or VEP SIFT/PolyPhen-2) as PP3 (pathogenic supporting) or BP4 (benign supporting).
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

annotation_synthesizer = _SilentLlmAgent(
    name="annotation_synthesizer",
    description="Synthesizes parallel annotator findings into a 6-section clinical genomic dossier in session state.",
    model=get_gemini_model(PRO_MODEL),
    planner=get_thinking_planner(),
    include_contents="none",
    output_key="draft_report",
    instruction=SYNTHESIZER_PROMPT,
)

# 3. Silent Two-Iteration Critic Loop
annotation_critic = _SilentLlmAgent(
    name="annotation_critic",
    description="Audits the synthesized report for exact citations, ACMG compliance, AVI Phred calibration, and formatting.",
    model=get_gemini_model(FLASH_MODEL),
    planner=get_thinking_planner(),
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
2. Verify AVI Phred Grounding and Math:
   - If numeric AVI scores are reported (from an uploaded/GCS AlphaGenome file), check that `AVI Phred = -10 * log10(avi_quantile)` where `avi_quantile` is the tail quantile `10^(-Phred / 10)` (`>=20` High, `15-19.99` Moderate-High, `10-14.99` Moderate, `<10` Low).
   - If no AlphaGenome score file was provided (`atlas_link_only`), ensure NO fabricated or heuristic numeric AVI raw, quantile, Phred, or 18-modality track scores are invented in the text.
3. Verify ACMG Logic:
   - Check that BA1 is only applied for AF >= 5% and PM2_Supporting for AF <= 0.01% or absent.
   - Verify pLI >= 0.9 or LOEUF < 0.35 before endorsing LoF intolerance.
4. Verify Markdown / No-KaTeX Formatting & Atlas Links:
   - Ensure NO dollar-sign math delimiters (`$...$`) wrap gene symbols, transcription factors, protein names, or histone marks (e.g., `$CEBPA/CEBPB$` or `$H3K27ac$` must be written in plain text or backticks).
   - Ensure AlphaGenome Atlas links use `?q=...&m=variant|locus&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES` (never `?variant=` or `?locus=`).

If the report meets all criteria with no issues, state: 'AUDIT_COMPLETE: The dossier meets all clinical criteria and citations.'
Otherwise, provide specific line-item corrections for the refiner.
""",
)

report_refiner = _SilentLlmAgent(
    name="report_refiner",
    description="Refines the clinical genomic dossier in session state based on auditor critique.",
    model=get_gemini_model(FLASH_MODEL),
    planner=get_thinking_planner(),
    include_contents="none",
    output_key="draft_report",
    instruction="""You are the Report Refiner.
Current Dossier:
{draft_report}

Auditor Critique:
{critic_feedback}

Formatting rule: Never wrap gene symbols, transcription factors, protein names, or histone marks in dollar-sign math delimiters (`$...$`); use plain text or backticks.
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
    """Saves native ADK downloadable artifacts + GCS dossier, and emits the finalized dossier ONCE."""

    async def _generate_and_save_native_artifacts(
        self,
        ctx: InvocationContext,
        dossier_markdown: str,
        variant_label: str,
    ) -> tuple[EventActions, list[str]]:
        """Saves downloadable Markdown dossier and PNG charts via ADK `save_artifact`."""
        if not _has_real_artifact_service(ctx):
            return EventActions(), []

        cb_ctx = CallbackContext(ctx)
        slug = slugify_artifact_label(variant_label)
        saved_filenames: list[str] = []

        # 1. Native downloadable Markdown dossier
        dossier_fname = f"dossier_{slug}.md"
        await cb_ctx.save_artifact(
            filename=dossier_fname,
            artifact=types.Part.from_bytes(
                data=dossier_markdown.encode("utf-8"),
                mime_type="text/markdown",
            ),
        )
        saved_filenames.append(dossier_fname)

        # 2. AlphaGenome 18-Modality Perturbation Chart (or AVI Phred Tier Chart fallback)
        ag_records = ctx.session.state.get("alphagenome_records") or []
        if isinstance(ag_records, list) and ag_records:
            primary_ag = ag_records[0] if isinstance(ag_records[0], dict) else {}
            modalities = (
                primary_ag.get("modalities")
                or primary_ag.get("multimodal_tracks")
                or {}
            )
            avi = primary_ag.get("avi_score") or {}
            ag_label = str(
                primary_ag.get("rsid")
                or (
                    f"chr{str(primary_ag.get('chromosome', '')).lstrip('chr')}:"
                    f"{primary_ag.get('position')}:{primary_ag.get('ref')}>{primary_ag.get('alt')}"
                )
            )
            if modalities:
                ag_png = await asyncio.to_thread(
                    build_alphagenome_modality_chart_png,
                    ag_label,
                    modalities,
                    avi.get("phred"),
                    avi.get("impact_tier"),
                )
            else:
                ag_png = await asyncio.to_thread(
                    build_avi_phred_comparison_chart_png,
                    ag_records,
                )
            if ag_png:
                ag_fname = f"alphagenome_modalities_{slug}.png"
                await cb_ctx.save_artifact(
                    filename=ag_fname,
                    artifact=types.Part.from_bytes(data=ag_png, mime_type="image/png"),
                )
                saved_filenames.append(ag_fname)

        # 3. gnomAD v4 Continental Ancestry AF Chart
        gnomad_records = ctx.session.state.get("gnomad_records") or []
        if isinstance(gnomad_records, list) and gnomad_records:
            primary_gn = next(
                (
                    r
                    for r in gnomad_records
                    if isinstance(r, dict) and r.get("status") == "success"
                ),
                None,
            )
            if primary_gn is not None:
                gn_label = str(
                    primary_gn.get("label")
                    or primary_gn.get("variant_id")
                    or variant_label
                )
                gn_png = await asyncio.to_thread(
                    build_gnomad_ancestry_chart_png,
                    gn_label,
                    primary_gn.get("populations") or {},
                    primary_gn.get("allele_frequency"),
                )
                if gn_png:
                    gn_fname = f"gnomad_ancestry_af_{slug}.png"
                    await cb_ctx.save_artifact(
                        filename=gn_fname,
                        artifact=types.Part.from_bytes(
                            data=gn_png, mime_type="image/png"
                        ),
                    )
                    saved_filenames.append(gn_fname)

        return cb_ctx._event_actions, saved_filenames

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        raw_text = str(ctx.session.state.get("draft_report", "")).strip()
        if not raw_text:
            return

        final_text = normalize_alphagenome_atlas_urls(
            strip_biological_math_delimiters(raw_text)
        )

        # Prepend optional GCS bucket inventory if requested in a combined prompt
        bucket_inventory = ctx.session.state.get("bucket_inventory")
        if isinstance(bucket_inventory, dict):
            inv_md = _format_bucket_inventory_markdown(bucket_inventory)
            if inv_md and "Available AlphaGenome Bucket Files" not in final_text:
                final_text = f"{inv_md}\n\n---\n\n{final_text}"

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

        # Save native Gemini Enterprise downloadable artifacts (Markdown dossier + PNG charts)
        (
            event_actions,
            native_artifacts,
        ) = await self._generate_and_save_native_artifacts(
            ctx=ctx,
            dossier_markdown=final_text,
            variant_label=variant_label,
        )

        footer_bullets: list[str] = []
        if native_artifacts:
            formatted_names = ", ".join(f"`{fn}`" for fn in native_artifacts)
            footer_bullets.append(
                f"* **Downloadable Artifacts (Artifacts Drawer):** {formatted_names}"
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
                    "native_artifacts": native_artifacts,
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
                footer_bullets.extend(
                    [
                        f"* **Markdown Dossier (`.md`):** `{save_res['dossier_gcs_uri']}` "
                        f"([Open in Cloud Console]({save_res['console_dossier_url']}))",
                        f"* **Structured JSON Metadata & Atlas Links (`.json`):** `{save_res['metadata_gcs_uri']}`",
                        f"* **GCS Dossiers Folder:** [Browse `gs://{save_res['bucket']}/dossiers/`]({save_res['console_folder_url']})",
                    ]
                )

        if footer_bullets:
            final_text += (
                "\n\n---\n"
                "### Saved Output Artifacts in Google Cloud Storage & Artifacts Drawer\n"
                + "\n".join(footer_bullets)
            )

        padded_output = ensure_block_newlines(final_text)
        yield Event(
            author=self.name,
            output=padded_output,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=padded_output)]
            ),
            actions=event_actions,
        )


prep_variant_request = PrepVariantRequest(
    name="prep_variant_request",
    description="Inspects user queries for gs:// URIs, relative bucket paths, or genomic coordinates, pre-parsing data into session state.",
)

finalize_variant_report = FinalizeVariantReport(
    name="finalize_variant_report",
    description="Saves native downloadable artifacts and GCS dossier, emitting the validated dossier once.",
)

# Variant Annotation Workflow Pipeline (ADK v2)
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
