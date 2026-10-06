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

"""`dde phenotype` — model organism phenotype lookup (MGI, HPO).

Two sources, two phases:

  search   one gene -> phenotype annotations from MGI (Mouse Genome
           Informatics) or HPO (Human Phenotype Ontology), written
           verbatim to Layer 0 with a sidecar.
  analyze  reads stored search results and summarises phenotype
           associations relevant to target validation. No network.

MGI is the primary source — free, no authentication — and provides
mouse knockout/knockin phenotype data via the Mammalian Phenotype (MP)
ontology. HPO is secondary, also free, and maps genes to human
phenotype terms through disease associations.

Both APIs return clean HTTP status codes for errors, so the shared HTTP
client's retry logic handles transient failures.

The safety relay is the core design point: model organism phenotypes are
informative for target validation but a knockout phenotype in mouse is
not a prediction of human clinical outcomes. Species differences in gene
function, expression patterns, and compensatory mechanisms mean that
every inference from mouse to human carries a gap that must be stated.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import click

from ..common import (
    AppState,
    emitter,
    from_option,
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
from ..core.paths import sanitize_slug
from ..core.qps import qps_for_host

TOOL = "phenotype"
ARTIFACT_CLASS = "genomics"

MGI_API = "https://www.informatics.jax.org"

HPO_API = "https://ontology.jax.org/api"

AGR_API = "https://www.alliancegenome.org/api"


def _fetch_mgi(gene: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch phenotype annotations from MGI for a gene symbol.

    MGI does not expose a clean JSON REST API for marker search, so this
    uses the ``/marker/json`` endpoint (which embeds HTML fragments in its
    JSON values) to resolve the gene symbol to an internal marker key,
    then scrapes the MGI accession ID from the marker detail HTML page.

    Phenotype annotations are retrieved from the Alliance of Genome
    Resources (AGR) REST API, which aggregates MGI data and returns clean
    JSON.

    Returns (verbatim response bytes, structured artifact dict).
    """
    # Step 1: Search for the gene marker via MGI marker/json.
    search_url = f"{MGI_API}/marker/json?nomen={quote(gene, safe='')}*"
    search_data = http.get_json(
        search_url,
        qps=qps_for_host("www.informatics.jax.org"),
        timeout=60.0,
    )

    # MGI marker/json returns summaryRows with HTML-embedded values.
    markers = _extract_mgi_markers(search_data, gene)
    if not markers:
        raise Refusal(
            f"MGI has no marker record for {gene!r}",
            remedy="check the gene symbol at informatics.jax.org",
        )

    # Take the best match — prefer exact symbol match.
    marker = _best_mgi_marker(markers, gene)
    symbol = marker.get("symbol") or gene
    marker_key = marker.get("marker_key", "")

    # Step 2: Resolve the internal marker key to an MGI accession ID.
    # The marker detail HTML page contains the MGI:nnnnnn ID.
    mgi_id = ""
    if marker_key:
        detail_url = f"{MGI_API}/marker/key/{quote(str(marker_key), safe='')}"
        detail_response = http.request(
            "GET",
            detail_url,
            qps=qps_for_host("www.informatics.jax.org"),
            timeout=60.0,
            tolerate_status=(404,),
        )
        if detail_response.status_code == 200:
            mgi_match = re.search(r"MGI:\d+", detail_response.text)
            if mgi_match:
                mgi_id = mgi_match.group(0)

    if not mgi_id:
        raise Refusal(
            f"could not resolve MGI accession ID for {gene!r}",
            remedy="check the gene symbol at informatics.jax.org",
        )

    # Step 3: Fetch phenotype annotations from the Alliance of Genome
    # Resources API, which provides MGI phenotype data as clean JSON.
    pheno_url = f"{AGR_API}/gene/{quote(mgi_id, safe=':')}/phenotypes?limit=200"
    pheno_data = http.get_json(
        pheno_url,
        qps=qps_for_host("www.alliancegenome.org"),
        timeout=60.0,
    )

    phenotypes = _extract_mgi_phenotypes(pheno_data, mgi_id, symbol)

    if not phenotypes:
        raise Refusal(
            f"No phenotype annotations found for '{gene}' in MGI/Alliance",
            remedy="Verify the gene symbol or try --source hpo for human phenotypes",
        )

    # Build the combined verbatim response.
    combined = {
        "marker_search": search_data,
        "phenotype_annotations": pheno_data,
    }
    raw = json.dumps(combined, indent=2).encode("utf-8")

    artifact = _build_artifact(gene, "mgi", mgi_id, symbol, "Mus musculus", phenotypes)
    return raw, artifact


def _extract_mgi_markers(data: Any, gene: str) -> list[dict[str, Any]]:
    """Extract marker records from an MGI ``/marker/json`` response.

    The endpoint returns ``{"summaryRows": [...], "totalCount": N}``
    where each row has an HTML ``symbol`` field like::

        <a href=".../marker/key/24690">Brca1</a>, breast cancer 1

    This function parses the HTML to extract clean symbol text and the
    internal marker key.
    """
    if not isinstance(data, dict):
        return []
    rows = data.get("summaryRows")
    if not isinstance(rows, list):
        return []

    markers: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol_html = row.get("symbol", "")
        # Extract marker key and symbol from HTML:
        #   <a href=".../marker/key/24690">Brca1</a>, description
        key_match = re.search(r"marker/key/(\d+)", symbol_html)
        sym_match = re.search(r">([^<]+)</a>", symbol_html)
        if not sym_match:
            continue
        marker: dict[str, Any] = {
            "symbol": sym_match.group(1),
            "marker_key": key_match.group(1) if key_match else "",
            "featureType": row.get("featureType", ""),
            "coordinates": row.get("coordinates", ""),
        }
        markers.append(marker)
    return markers


def _best_mgi_marker(markers: list[dict[str, Any]], gene: str) -> dict[str, Any]:
    """Pick the best marker from a search result list.

    Prefer an exact case-insensitive symbol match. Fall back to the
    first result.
    """
    upper = gene.upper()
    for m in markers:
        if (m.get("symbol") or "").upper() == upper:
            return m
    return markers[0]


def _extract_mgi_phenotypes(
    data: Any, mgi_id: str, symbol: str
) -> list[dict[str, Any]]:
    """Extract phenotype records from an AGR phenotype API response.

    The Alliance of Genome Resources API returns results with the
    structure::

        {"results": [{"primaryAnnotations": [{"phenotypeTerms":
         [{"curie": "MP:...", "name": "..."}], ...}], ...}], ...}

    Each result represents one phenotype annotation.
    """
    phenotypes: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return phenotypes

    results = data.get("results")
    if not isinstance(results, list):
        return phenotypes

    for result in results:
        if not isinstance(result, dict):
            continue
        annotations = result.get("primaryAnnotations") or []
        for annot in annotations:
            if not isinstance(annot, dict):
                continue
            terms = annot.get("phenotypeTerms") or []
            if not terms:
                continue
            term_obj = terms[0] if isinstance(terms[0], dict) else {}
            mp_id = term_obj.get("curie", "")
            term_name = term_obj.get("name", "")

            # Extract allele symbol if available.
            allele_info = annot.get("inferredAllele") or {}
            allele_sym = ""
            if isinstance(allele_info, dict):
                sym_obj = allele_info.get("alleleSymbol") or {}
                allele_sym = (
                    sym_obj.get("formatText", "") if isinstance(sym_obj, dict) else ""
                )

            # Extract PMID references.
            evidence = annot.get("evidenceItem") or {}
            refs: list[str] = []
            if isinstance(evidence, dict):
                for xref in evidence.get("crossReferences") or []:
                    curie = (
                        xref.get("referencedCurie", "")
                        if isinstance(xref, dict)
                        else ""
                    )
                    if curie.startswith("PMID:"):
                        refs.append(curie)

            phenotypes.append(
                {
                    "source_db": "mgi",
                    "gene_id": mgi_id,
                    "gene_symbol": symbol,
                    "organism": "Mus musculus",
                    "allele_type": allele_sym,
                    "phenotype_term": term_name,
                    "mp_id": mp_id,
                    "references": refs,
                }
            )

    return phenotypes


def _fetch_hpo(gene: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch phenotype annotations from HPO for a gene symbol.

    Uses the ontology.jax.org network API:
      1. ``/api/network/search/gene?q=GENE`` resolves the symbol to an
         NCBI Gene ID (e.g. ``NCBIGene:672``).
      2. ``/api/network/annotation/{geneId}`` returns phenotype terms
         and disease associations for that gene.

    Returns (verbatim response bytes, structured artifact dict).
    """
    # Step 1: Resolve gene symbol to NCBI Gene ID.
    search_url = f"{HPO_API}/network/search/gene?q={quote(gene, safe='')}"
    search_data = http.get_json(
        search_url,
        qps=qps_for_host("ontology.jax.org"),
        timeout=60.0,
    )

    gene_results = search_data.get("results") if isinstance(search_data, dict) else []
    if not isinstance(gene_results, list) or not gene_results:
        raise Refusal(
            f"HPO has no gene record for {gene!r}",
            remedy="verify the gene symbol at ontology.jax.org",
        )

    # Pick the best match — prefer exact name match.
    gene_entry = _best_hpo_gene(gene_results, gene)
    gene_id = gene_entry.get("id", "")  # e.g. "NCBIGene:672"
    resolved_name = gene_entry.get("name", gene.upper())

    # Step 2: Fetch phenotype and disease annotations.
    annot_url = f"{HPO_API}/network/annotation/{quote(gene_id, safe=':')}"
    annot_data = http.get_json(
        annot_url,
        qps=qps_for_host("ontology.jax.org"),
        timeout=60.0,
    )

    phenotypes: list[dict[str, Any]] = []
    hpo_phenotypes = (
        annot_data.get("phenotypes") if isinstance(annot_data, dict) else []
    )
    hpo_diseases = annot_data.get("diseases") if isinstance(annot_data, dict) else []
    if not isinstance(hpo_phenotypes, list):
        hpo_phenotypes = []
    if not isinstance(hpo_diseases, list):
        hpo_diseases = []

    # Build a lookup from disease ID to disease name for enrichment.
    disease_names: dict[str, str] = {}
    for d in hpo_diseases:
        if isinstance(d, dict):
            did = d.get("id", "")
            dname = d.get("name", "")
            if did:
                disease_names[did] = dname

    for term in hpo_phenotypes:
        if not isinstance(term, dict):
            continue
        hpo_id = term.get("id", "")
        name = term.get("name", "")
        phenotypes.append(
            {
                "source_db": "hpo",
                "gene_id": gene_id,
                "gene_symbol": resolved_name,
                "organism": "Homo sapiens",
                "hpo_id": hpo_id,
                "phenotype_term": name,
                "frequency": "",
            }
        )

    if not phenotypes:
        raise Refusal(
            f"HPO has no phenotype associations for {gene!r}",
            remedy="verify the gene symbol at ontology.jax.org",
        )

    # Build the combined verbatim response.
    combined = {
        "gene_search": search_data,
        "annotations": annot_data,
    }
    raw = json.dumps(combined, indent=2).encode("utf-8")
    artifact = _build_artifact(
        gene,
        "hpo",
        gene_id,
        resolved_name,
        "Homo sapiens",
        phenotypes,
    )
    # Disease associations from HPO are gene-level, not per-phenotype.
    # Place them in the summary to avoid implying each phenotype term
    # is individually linked to every disease.
    if disease_names:
        artifact["summary"]["disease_associations"] = [
            {"id": did, "name": dname} for did, dname in disease_names.items()
        ]
    return raw, artifact


def _best_hpo_gene(results: list[dict[str, Any]], gene: str) -> dict[str, Any]:
    """Pick the best gene match from HPO gene search results.

    Prefer an exact case-insensitive name match. Fall back to the first
    result.
    """
    upper = gene.upper()
    for r in results:
        if isinstance(r, dict) and (r.get("name") or "").upper() == upper:
            return r
    return results[0] if results else {}


def _build_artifact(
    gene: str,
    source: str,
    gene_id: str,
    resolved_symbol: str,
    organism: str,
    phenotypes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the structured dde.phenotype.v1 artifact."""
    model_organisms = sorted(
        {p.get("organism", organism) for p in phenotypes if p.get("organism")}
    ) or [organism]

    artifact: dict[str, Any] = {
        "schema": "dde.phenotype.v1",
        "query": {
            "gene": gene.upper(),
            "source": source,
        },
        "summary": {
            "n_phenotypes": len(phenotypes),
            "model_organisms": model_organisms,
        },
        "phenotypes": phenotypes,
    }
    if gene_id:
        artifact["query"]["gene_id"] = gene_id
    return artifact


@click.group()
def phenotype() -> None:
    """Model organism phenotype lookup (MGI, HPO)."""


@phenotype.command("search")
@click.argument("gene")
@click.option(
    "--source",
    type=click.Choice(["mgi", "hpo"]),
    default="mgi",
    help="Which database to query (default: mgi).",
)
@click.option("--name", default=None, help="Human-readable alias for filenames.")
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    gene: str,
    source: str,
    name: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search for phenotype annotations for GENE."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = sanitize_slug(gene.lower())
    if source == "mgi":
        raw, artifact = _fetch_mgi(gene)
        endpoint = AGR_API
    else:
        raw, artifact = _fetch_hpo(gene)
        endpoint = HPO_API

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=endpoint,
        parameters={
            "query_gene": gene,
            "resolved_gene": gene.upper(),
            "source": source,
        },
    )
    sidecar.note("source_db", source)
    if source == "mgi":
        sidecar.note(
            "endpoint_detail",
            f"Gene resolution via {MGI_API}; phenotype data via {AGR_API}",
        )
    sidecar.note("n_phenotypes", artifact["summary"]["n_phenotypes"])
    sidecar.note("model_organisms", artifact["summary"]["model_organisms"])

    if name:
        sidecar.note("display_name", name)

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.phenotype-{source}.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.phenotype-{source}.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.phenotype-{source}.meta.json")

    emit.data("gene", gene.upper())
    emit.data("source", source)
    emit.data("n_phenotypes", artifact["summary"]["n_phenotypes"])
    emit.data("model_organisms", artifact["summary"]["model_organisms"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@phenotype.command("analyze")
@click.argument("gene")
@click.option(
    "--source",
    type=click.Choice(["mgi", "hpo"]),
    default="mgi",
    help="Which source to analyze (must match a prior search).",
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
    """Summarise phenotype data from a stored search. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = sanitize_slug(gene.lower())
    artifact_path = source_dir / f"{slug}.phenotype-{source}.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no phenotype search artifact for {gene.upper()} (source={source})",
            remedy=f"run `dde phenotype search {gene} --source {source}` first",
        )

    artifact = provenance.read_json(artifact_path, "phenotype artifact")
    if artifact.get("schema") != "dde.phenotype.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.phenotype.v1, got {artifact.get('schema')!r}",
        )

    phenotypes = artifact.get("phenotypes", [])
    n_phenotypes = len(phenotypes)

    verdict = "phenotypes_found" if phenotypes else "no_phenotypes"

    # Collect unique phenotype terms for summary.
    unique_terms: list[str] = []
    seen: set[str] = set()
    for p in phenotypes:
        term = p.get("phenotype_term", "")
        if term and term not in seen:
            unique_terms.append(term)
            seen.add(term)
        if len(unique_terms) >= 10:
            break

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    if phenotypes and source == "mgi":
        add_relay(
            "phenotype.model_organism_not_human",
            "Phenotype data from model organisms (mouse, rat) may not translate "
            "directly to humans. Species differences in gene function, expression "
            "patterns, and compensatory mechanisms mean that a knockout phenotype "
            "in mouse is informative but not predictive of human clinical outcomes.",
        )

    metrics = {
        "total_phenotypes": n_phenotypes,
        "unique_terms": len(unique_terms),
        "source": source,
        "top_phenotypes": unique_terms,
    }
    assessment = {
        "verdict": verdict,
        "gene": gene.upper(),
        "n_phenotypes": n_phenotypes,
        "top_phenotypes": unique_terms,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.phenotype-{source}.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set=f"phenotype-{source}",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("relays", relays)
    emit.line(
        f"{gene.upper()} -> {verdict.upper()} "
        f"({n_phenotypes} phenotype(s) from {source})"
    )
    if unique_terms:
        emit.line(f"top phenotypes: {', '.join(unique_terms[:5])}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
