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

"""`dde pathway` — pathway membership and gene ontology lookup.

Looks up which biological pathways a gene participates in and which GO
terms annotate it. Two sources are supported:

  **Reactome** — curated pathway database. Returns pathway memberships
  for a gene symbol, queried against human (species 9606).

  **GO (Gene Ontology via QuickGO)** — annotations from the EBI QuickGO
  service. Returns GO term IDs, names, aspect (MF/BP/CC), and evidence
  codes for a gene product.

Two phases:

  search   one gene symbol -> pathway memberships or GO annotations,
           written verbatim to Layer 0 with a sidecar.
  analyze  reads stored results, summarizes pathway memberships,
           identifies enriched categories. No network.

Pathway membership is annotation, not activity: a gene annotated to
"Immune System" in Reactome is a participant in that pathway's
reactions, but the annotation says nothing about whether the gene is
active, rate-limiting, or causally involved in the tissue or condition
of interest. Expression, activity, and essentiality are separate
questions. See relay code `pathway.membership_not_activity`.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import click

from ..common import (
    AppState,
    emitter,
    from_option,
    name_option,
    out_option,
    output_options,
    pass_state,
)
from ..core import http, provenance
from ..core.errors import (
    ArtifactError,
    Refusal,
    SchemaError,
)
from ..core.gene import resolve_gene
from ..core.paths import sanitize_slug
from ..core.qps import qps_for_host

TOOL = "pathway"
ARTIFACT_CLASS = "genomics"  # pathway data is genomics-adjacent

REACTOME_API = "https://reactome.org/ContentService"

QUICKGO_API = "https://www.ebi.ac.uk/QuickGO/services"

UNIPROT_API = "https://rest.uniprot.org"


def _resolve_uniprot_accession(gene: str) -> str:
    """Resolve a gene symbol to a UniProt accession (human, reviewed).

    QuickGO's ``geneProductId`` parameter requires a UniProt accession
    (e.g. ``P38398``), not a gene symbol.  This function queries the
    UniProt search endpoint to map a human gene symbol to its canonical
    Swiss-Prot accession.

    Raises :class:`Refusal` if no mapping is found.
    """
    url = (
        f"{UNIPROT_API}/uniprotkb/search"
        f"?query=gene_exact:{quote(gene, safe='')}"
        f"+AND+organism_id:9606"
        f"+AND+reviewed:true"
        f"&fields=accession"
        f"&size=1"
    )
    data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"), timeout=30.0)
    results = data.get("results", [])
    if not results:
        raise Refusal(
            f"no UniProt accession found for gene symbol {gene!r}",
            remedy=(
                "check the gene symbol; UniProt could not map it to a "
                "human protein accession"
            ),
        )
    accession = results[0].get("primaryAccession", "")
    if not accession:
        raise Refusal(
            f"UniProt returned a record without an accession for {gene!r}",
            remedy="check the gene symbol against uniprot.org",
        )
    return accession


def _search_reactome(gene: str) -> tuple[bytes, list[dict[str, Any]]]:
    """Query Reactome for pathway memberships of a gene symbol.

    Uses the search endpoint which accepts a gene symbol directly and
    returns matching pathways filtered to Homo sapiens.
    """
    url = (
        f"{REACTOME_API}/search/query"
        f"?query={quote(gene, safe='')}&types=Pathway&species=Homo+sapiens"
    )
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("reactome.org"),
        timeout=60.0,
        headers={"Accept": "application/json"},
    )
    raw = response.content
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("Reactome did not return JSON", detail=str(exc)) from exc

    # Reactome search returns results grouped by type; extract pathway entries.
    entries: list[dict[str, Any]] = []
    results = payload.get("results", [])
    for group in results:
        for entry in group.get("entries", []):
            entries.append(
                {
                    "source_db": "reactome",
                    "pathway_id": entry.get("stId", ""),
                    "name": entry.get("name", ""),
                    "species": entry.get("species", ["Homo sapiens"])[0]
                    if isinstance(entry.get("species"), list)
                    else entry.get("species", "Homo sapiens"),
                }
            )

    return raw, entries


def _search_go(gene: str) -> tuple[bytes, list[dict[str, Any]]]:
    """Query QuickGO for GO annotations of a gene product.

    Resolves the gene symbol to a UniProt accession first, because
    QuickGO's ``geneProductId`` parameter expects accessions (e.g.
    ``P38398``), not gene symbols.  Filtered to human (taxon 9606).
    """
    accession = _resolve_uniprot_accession(gene)
    base_url = (
        f"{QUICKGO_API}/annotation/search"
        f"?geneProductId={quote(accession, safe='')}&taxonId=9606"
    )

    # Fetch all pages from QuickGO.  The default page size is 25; genes
    # with many annotations (e.g. TP53 >200) would be silently truncated.
    all_annotations: list[dict[str, Any]] = []
    raw_parts: list[bytes] = []
    page = 1

    while True:
        url = f"{base_url}&page={page}"
        response = http.request(
            "GET",
            url,
            qps=qps_for_host("www.ebi.ac.uk"),
            timeout=60.0,
            headers={"Accept": "application/json"},
        )
        raw_parts.append(response.content)
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except Exception as exc:
            raise SchemaError("QuickGO did not return JSON", detail=str(exc)) from exc

        results = payload.get("results", [])
        all_annotations.extend(results)

        # QuickGO includes pageInfo with total number of pages.
        page_info = payload.get("pageInfo", {})
        total_pages = page_info.get("total", 1)
        if page >= total_pages:
            break
        page += 1

    # Use the first page's raw bytes as the canonical raw response.
    raw = raw_parts[0] if raw_parts else b"{}"

    # QuickGO returns annotations in payload["results"].
    # Deduplicate by GO term ID but preserve all distinct evidence codes
    # per term — an experimentally validated annotation (IDA) is
    # qualitatively different from an inferred one (IEA).
    entries: list[dict[str, Any]] = []
    term_index: dict[str, int] = {}  # go_id -> index in entries
    for annotation in all_annotations:
        go_id = annotation.get("goId", "")
        evidence = annotation.get("goEvidence", "")
        if go_id in term_index:
            # Append new evidence code if not already recorded.
            existing = entries[term_index[go_id]]
            if evidence and evidence not in existing["evidence_codes"]:
                existing["evidence_codes"].append(evidence)
            continue
        term_index[go_id] = len(entries)
        entries.append(
            {
                "source_db": "go",
                "term_id": go_id,
                "name": annotation.get("goName", ""),
                "aspect": annotation.get("goAspect", ""),
                "evidence_codes": [evidence] if evidence else [],
            }
        )

    return raw, entries


def _build_output(
    gene: str,
    source: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the structured output artifact.

    Reactome and GO return structurally different records, so they use
    distinct schema identifiers (``dde.pathway-reactome.v1`` and
    ``dde.pathway-go.v1``) rather than overloading a single schema.
    """
    if source == "reactome":
        top_names = [e["name"] for e in entries[:5]]
        return {
            "schema": "dde.pathway-reactome.v1",
            "query": {"gene": gene, "source": source},
            "summary": {
                "n_pathways": len(entries),
                "top_pathways": top_names,
            },
            "pathways": entries,
        }
    else:
        # GO source
        top_names = [e["name"] for e in entries[:5]]
        return {
            "schema": "dde.pathway-go.v1",
            "query": {"gene": gene, "source": source},
            "summary": {
                "n_terms": len(entries),
                "top_terms": top_names,
            },
            "annotations": entries,
        }


@click.group()
def pathway() -> None:
    """Pathway and gene ontology lookup."""


@pathway.command("search")
@click.argument("gene")
@click.option(
    "--source",
    type=click.Choice(["reactome", "go"], case_sensitive=False),
    default="reactome",
    help="Data source: reactome (default) or go (Gene Ontology via QuickGO).",
)
@out_option
@output_options
@name_option
@pass_state
def search_cmd(
    state: AppState,
    gene: str,
    source: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
    name: str | None,
) -> None:
    """Search pathway memberships or GO annotations for GENE."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # ── HGNC gene symbol resolution (#147) ────────────────────────────
    gene_res = resolve_gene(gene)
    if not gene_res.resolved:
        suggestions = ", ".join(gene_res.suggestions) if gene_res.suggestions else ""
        hint = f" Did you mean: {suggestions}?" if suggestions else ""
        provenance.relay(
            "gene.unresolved_symbol",
            f"Gene symbol {gene!r} could not be resolved via HGNC. "
            f"No query was attempted. This is a lookup failure, not "
            f"evidence of gene absence.{hint}",
        )
        raise Refusal(
            f"could not resolve {gene!r} to a known gene via HGNC",
            detail=f"suggestions: {suggestions}"
            if suggestions
            else "no near matches found",
            remedy="check the gene symbol or pass an Ensembl gene ID (ENSG...)",
        )
    echo = gene_res.echo_line()
    if echo:
        emit.line(echo)
    resolved = (gene_res.canonical_symbol or gene).upper()

    if source == "reactome":
        raw, entries = _search_reactome(resolved)
        endpoint = REACTOME_API
    else:
        raw, entries = _search_go(resolved)
        endpoint = QUICKGO_API

    if not entries:
        provenance.relay(
            "pathway.no_data_found",
            f"Gene {resolved} resolved successfully via HGNC "
            f"({gene_res.source}) but no {source} results were found. "
            f"This is a coverage gap, not evidence that the gene has "
            f"no pathway involvement.",
        )
        raise Refusal(
            f"no {source} results for {resolved!r}",
            detail=(
                f"HGNC resolved {gene!r} to {resolved} (source: {gene_res.source})"
            ),
            remedy=(
                "check the gene symbol; if querying GO, try the UniProt "
                "accession instead of the gene symbol"
            ),
        )

    artifact = _build_output(resolved, source, entries)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=endpoint,
        parameters={
            "query_gene": gene,
            "resolved_gene": resolved,
            "source": source,
            "display_name": name,
            "gene_resolution": gene_res.to_dict(),
        },
    )
    sidecar.note("source_db", source)
    sidecar.note("n_results", len(entries))

    # Mandatory relay: pathway membership is not activity.
    sidecar.warn(
        "Pathway membership means the gene product is annotated to a pathway "
        "or GO term. It does not indicate that the gene is active, rate-limiting, "
        "or causally involved in the pathway in the tissue or condition of interest. "
        "Expression, activity, and essentiality are separate questions.",
        code="pathway.membership_not_activity",
    )

    # Always use the resolved gene symbol for filenames so that
    # analyze can find them.  The --name value is recorded in the
    # sidecar parameters for display/metadata only — matching the
    # genetics.py pattern.
    suffix = f"pathway-{source}"

    # Write verbatim API response
    raw_path = target_dir / f"{resolved}.{suffix}.raw.json"
    raw_path.write_bytes(raw)
    sidecar.add_output(raw_path)

    # Write structured artifact
    artifact_path = target_dir / f"{resolved}.{suffix}.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    meta_path = sidecar.write(target_dir / f"{resolved}.{suffix}.meta.json")

    emit.data("gene", resolved)
    emit.data("source", source)
    if source == "reactome":
        emit.data("n_pathways", len(entries))
        emit.line(f"{resolved}: {len(entries)} Reactome pathway(s) found")
        for entry in entries[:5]:
            emit.line(f"  {entry['pathway_id']} — {entry['name']}")
        if len(entries) > 5:
            emit.line(f"  ... and {len(entries) - 5} more")
    else:
        emit.data("n_terms", len(entries))
        emit.line(f"{resolved}: {len(entries)} GO annotation(s) found")
        for entry in entries[:5]:
            emit.line(f"  {entry['term_id']} [{entry['aspect']}] — {entry['name']}")
        if len(entries) > 5:
            emit.line(f"  ... and {len(entries) - 5} more")

    emit.path(raw_path, role="raw")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@pathway.command("analyze")
@click.argument("gene")
@click.option(
    "--source",
    type=click.Choice(["reactome", "go"], case_sensitive=False),
    default="reactome",
    help="Which source's stored results to analyze.",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    gene: str,
    source: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarize stored pathway/GO results for GENE. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    resolved = sanitize_slug(gene.upper())
    suffix = f"pathway-{source}"
    path = source_dir / f"{resolved}.{suffix}.json"

    if not path.is_file():
        raise ArtifactError(
            f"no stored {source} pathway data for {resolved}",
            remedy=f"run `dde pathway search {gene} --source {source}` first",
        )

    data = provenance.read_json(path, f"{source} pathway record")

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    # Always relay: membership is not activity
    add_relay(
        "pathway.membership_not_activity",
        f"{resolved} is annotated to pathways/GO terms, but annotation does "
        "not indicate activity, rate-limiting involvement, or causal role in "
        "any specific tissue or condition",
    )

    if source == "reactome":
        pathways = data.get("pathways", [])
        # Categorize by top-level pathway name (crude grouping)
        categories: dict[str, int] = {}
        for p in pathways:
            name = p.get("name", "Unknown")
            categories[name] = categories.get(name, 0) + 1

        sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)

        assessment = {
            "gene": resolved,
            "source": source,
            "n_pathways": len(pathways),
            "categories": dict(sorted_cats),
        }
        metrics = {
            "total_pathways": len(pathways),
            "unique_categories": len(categories),
            "top_category": sorted_cats[0][0] if sorted_cats else None,
        }
    else:
        annotations = data.get("annotations", [])
        # Group by aspect
        aspects: dict[str, list[str]] = {}
        for a in annotations:
            aspect = a.get("aspect", "unknown")
            aspects.setdefault(aspect, []).append(a.get("name", ""))

        assessment = {
            "gene": resolved,
            "source": source,
            "n_annotations": len(annotations),
            "by_aspect": {k: len(v) for k, v in aspects.items()},
        }
        metrics = {
            "total_annotations": len(annotations),
            "aspects": list(aspects.keys()),
            "molecular_function_count": len(aspects.get("molecular_function", [])),
            "biological_process_count": len(aspects.get("biological_process", [])),
            "cellular_component_count": len(aspects.get("cellular_component", [])),
        }

    analysis_path = provenance.write_analysis(
        target_dir / f"{resolved}.{suffix}.analysis.json",
        source=state.project().relative(path),
        threshold_set="pathway-membership@1.0",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("relays", relays)
    if source == "reactome":
        emit.line(f"{resolved}: {metrics['total_pathways']} pathway(s)")
        for cat, count in sorted_cats[:5]:
            emit.line(f"  {cat}: {count}")
    else:
        emit.line(f"{resolved}: {metrics['total_annotations']} GO annotation(s)")
        for aspect, terms in aspects.items():
            emit.line(f"  {aspect}: {len(terms)}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
