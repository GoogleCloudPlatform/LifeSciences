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

"""Structural homology search via RCSB PDB BLAST.

Phase 1: submit a domain subsequence to RCSB Search API v2 (sequence
service, BLAST), then fetch structure metadata from the RCSB Data API
GraphQL endpoint. Writes a search manifest and provenance sidecar.

Phase 2: read the stored search manifest and classify each hit using
the `homology` threshold set. Writes a `.homology.analysis.json`.

Phase 3: download a single PDB coordinate file and write a provenance
sidecar recording the download endpoint and SHA-256 hash.

  search          — phase 1. BLAST a UniProt subsequence against the PDB.
  analyze         — phase 2. Classify hits by identity, resolution, coverage.
  fetch-structure — phase 3. Download a PDB coordinate file from RCSB.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    from_option,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
)
from ..core import http, provenance
from ..core.errors import ArtifactError, EndpointError, Refusal, UsageError
from ..core.output import Emitter
from ..core.paths import confine_path, sanitize_slug
from ..core.qps import qps_for_host

TOOL = "homology-search"
TOOL_FETCH = "homology-fetch"
TOOL_ORTHOLOGS = "homology-orthologs"
ARTIFACT_CLASS = "structures"
ORTHOLOGS_ARTIFACT_CLASS = "genomics"

UNIPROT_API = "https://rest.uniprot.org/uniprotkb"

RCSB_SEARCH_API = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_SEARCH_TIMEOUT = 30.0

RCSB_GRAPHQL_API = "https://data.rcsb.org/graphql"

RCSB_DOWNLOAD_BASE = "https://files.rcsb.org/download"

_UNIPROT_RE = re.compile(
    r"^[A-NR-Z][0-9][A-Z0-9]{3}[0-9]$"
    r"|^[OPQ][0-9][A-Z0-9]{3}[0-9]$"
    r"|^[A-Z0-9]{10}$"
)


def _fetch_uniprot(accession: str) -> tuple[str, int, str | None]:
    """Fetch canonical sequence, length, and gene symbol from UniProt.

    Returns (sequence, length, gene_symbol). Raises EndpointError on
    failure — unlike alphafold's lenient cross-check, the sequence is
    required input here, not optional metadata.
    """
    url = f"{UNIPROT_API}/{accession}.json?fields=length,gene_primary,sequence"
    record = http.get_json(url, qps=qps_for_host("rest.uniprot.org"))

    seq_block = record.get("sequence") or {}
    sequence = seq_block.get("value")
    length = seq_block.get("length")
    if not sequence or not isinstance(length, int):
        raise EndpointError(
            f"UniProt record for {accession} has no usable sequence",
            detail=f"sequence.value={'present' if sequence else 'missing'}, "
            f"sequence.length={length}",
        )

    genes = record.get("genes") or []
    symbol: str | None = None
    if genes and isinstance(genes[0], dict):
        symbol = (genes[0].get("geneName") or {}).get("value")

    return sequence, length, symbol


def _parse_range(residue_range: str, canonical_length: int) -> tuple[int, int]:
    """Parse 'START-END' and validate against canonical length."""
    parts = residue_range.split("-")
    if len(parts) != 2:
        raise UsageError(
            f"invalid range format {residue_range!r}",
            detail="expected START-END (e.g. 400-575)",
        )
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise UsageError(
            f"range values must be integers, got {residue_range!r}",
        ) from e
    if start > end:
        raise UsageError(
            f"range START ({start}) must be ≤ END ({end})",
        )
    if start < 1:
        raise UsageError(
            f"range START ({start}) must be ≥ 1",
        )
    if end > canonical_length:
        raise UsageError(
            f"range END ({end}) exceeds canonical length ({canonical_length})",
        )
    return start, end


def _blast_search(
    subsequence: str,
    evalue: float,
    identity: float,
    max_hits: int,
) -> list[dict[str, Any]]:
    """Submit subsequence to RCSB Search API v2 BLAST.

    Returns the raw result_set list, or an empty list when no hits are
    found (204 or empty result_set).
    """
    payload = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": evalue,
                "identity_cutoff": identity,
                "sequence_type": "protein",
                "value": subsequence,
            },
        },
        "return_type": "polymer_entity",
        "request_options": {
            "results_verbosity": "verbose",
            "paginate": {"start": 0, "rows": max_hits},
        },
    }

    response = http.request(
        "POST",
        RCSB_SEARCH_API,
        json=payload,
        qps=qps_for_host("search.rcsb.org"),
        timeout=RCSB_SEARCH_TIMEOUT,
        tolerate_status=(204,),
    )

    if response.status_code == 204:
        return []

    try:
        data = response.json()
    except ValueError as exc:
        raise EndpointError(
            "RCSB Search API did not return valid JSON",
            detail=str(exc),
        ) from exc

    return data.get("result_set") or []


def _extract_hit(result: dict[str, Any], subseq_len: int) -> dict[str, Any]:
    """Extract alignment data from a single RCSB search result."""
    identifier = result.get("identifier", "")
    # identifier format: "PDB_ENTITY" e.g. "7FD3_1"
    parts = identifier.split("_")
    pdb_id = parts[0] if parts else identifier
    try:
        entity_id = int(parts[1]) if len(parts) > 1 else 1
    except ValueError:
        entity_id = 1

    # Navigate to match_context
    match_context: dict[str, Any] = {}
    services = result.get("services") or []
    for svc in services:
        nodes = svc.get("nodes") or []
        for node in nodes:
            mc_list = node.get("match_context") or []
            if mc_list:
                match_context = mc_list[0]
                break
        if match_context:
            break

    query_beg = match_context.get("query_beg", 0)
    query_end = match_context.get("query_end", 0)
    coverage = (query_end - query_beg + 1) / subseq_len if subseq_len else 0

    return {
        "pdb_entity_id": identifier,
        "pdb_id": pdb_id,
        "entity_id": entity_id,
        "alignment": {
            "query_beg": query_beg,
            "query_end": query_end,
            "subject_beg": match_context.get("subject_beg", 0),
            "subject_end": match_context.get("subject_end", 0),
            "sequence_identity": match_context.get("sequence_identity", 0),
            "evalue": match_context.get("evalue", 0),
            "bitscore": match_context.get("bitscore", 0),
            "alignment_length": match_context.get("alignment_length", 0),
            "query_coverage_fraction": round(coverage, 4),
        },
    }


def _fetch_structure_metadata(hits: list[dict[str, Any]], query_accession: str) -> None:
    """Fetch structure metadata from RCSB GraphQL and update hits in-place.

    Batches all PDB IDs into a single GraphQL query to minimise requests.
    """
    if not hits:
        return

    pdb_ids = sorted({h["pdb_id"] for h in hits})
    ids_str = ", ".join(f'"{pid}"' for pid in pdb_ids)
    query = f"""{{
  entries(entry_ids: [{ids_str}]) {{
    rcsb_id
    struct {{ title }}
    rcsb_entry_info {{ resolution_combined experimental_method }}
    polymer_entities {{
      rcsb_id
      rcsb_entity_source_organism {{ ncbi_scientific_name }}
      rcsb_polymer_entity_align {{
        reference_database_accession
        reference_database_name
        aligned_regions {{ ref_beg_seq_id length }}
      }}
    }}
  }}
}}"""

    response = http.request(
        "POST",
        RCSB_GRAPHQL_API,
        json={"query": query},
        qps=qps_for_host("data.rcsb.org"),
    )

    try:
        gql_data = response.json()
    except ValueError as exc:
        raise EndpointError(
            "RCSB GraphQL API did not return valid JSON",
            detail=str(exc),
        ) from exc

    entries = (gql_data.get("data") or {}).get("entries") or []
    entry_map: dict[str, dict[str, Any]] = {}
    for entry in entries:
        rcsb_id = entry.get("rcsb_id", "")
        entry_map[rcsb_id] = entry

    query_acc_upper = query_accession.upper()

    for hit in hits:
        entry = entry_map.get(hit["pdb_id"], {})
        struct = entry.get("struct") or {}
        info = entry.get("rcsb_entry_info") or {}

        hit["title"] = struct.get("title") or ""
        hit["experimental_method"] = info.get("experimental_method") or ""

        resolution_combined = info.get("resolution_combined") or []
        hit["resolution_angstrom"] = (
            resolution_combined[0] if resolution_combined else None
        )

        # Determine source UniProt and whether this is a direct structure.
        # Only inspect the polymer entity that the BLAST hit matched —
        # other entities in the same PDB entry (e.g. a co-crystallised
        # partner in a heterocomplex) must not influence the decision.
        polymer_entities = entry.get("polymer_entities") or []
        source_uniprot: str | None = None
        is_direct = False
        hit_entity_id = hit["pdb_entity_id"]  # e.g., "7FD3_1"

        for pe in polymer_entities:
            if pe.get("rcsb_id") != hit_entity_id:
                continue
            aligns = pe.get("rcsb_polymer_entity_align") or []
            for align in aligns:
                db_name = (align.get("reference_database_name") or "").upper()
                db_acc = (align.get("reference_database_accession") or "").upper()
                if db_name == "UNIPROT" and db_acc:
                    if source_uniprot is None:
                        source_uniprot = align.get("reference_database_accession", "")
                    if db_acc == query_acc_upper:
                        is_direct = True
            organisms = pe.get("rcsb_entity_source_organism") or []
            if organisms:
                hit["source_organism"] = organisms[0].get("ncbi_scientific_name") or ""

        hit["source_uniprot"] = source_uniprot or ""
        hit.setdefault("source_organism", "")
        hit["is_direct_structure"] = is_direct


@click.group()
def homology() -> None:
    """Structural homology search via RCSB PDB BLAST."""


@homology.command()
@click.argument("uniprot_id")
@click.option(
    "--range",
    "residue_range",
    required=True,
    help="Residue range START-END (1-indexed, inclusive).",
)
@click.option("--evalue", type=float, default=0.001, help="E-value cutoff.")
@click.option("--identity", type=float, default=0.2, help="Sequence identity cutoff.")
@click.option(
    "--max-hits", type=int, default=25, help="Maximum number of hits to return."
)
@out_option
@output_options
@pass_state
def search(
    state: AppState,
    uniprot_id: str,
    residue_range: str,
    evalue: float,
    identity: float,
    max_hits: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search for structural homologs of a UniProt domain subsequence.

    Submits the specified residue range to the RCSB PDB BLAST service,
    fetches structure metadata for each hit, and writes a search manifest
    and provenance sidecar. Makes no judgement.
    """
    accession = uniprot_id.strip().upper()
    if not _UNIPROT_RE.match(accession):
        raise UsageError(
            f"{accession!r} does not look like a UniProt accession",
            detail="expected e.g. P04637, P00520, A0A1B0GX81",
            remedy="resolve the gene or protein name to a UniProt accession first",
        )

    # Fetch canonical sequence from UniProt
    sequence, canonical_length, gene_symbol = _fetch_uniprot(accession)

    # Parse and validate range
    start, end = _parse_range(residue_range, canonical_length)

    # Extract subsequence (1-indexed inclusive)
    subsequence = sequence[start - 1 : end]

    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=RCSB_SEARCH_API,
        parameters={
            "uniprot": accession,
            "residue_range": [start, end],
            "evalue_cutoff": evalue,
            "identity_cutoff": identity,
            "max_hits": max_hits,
        },
    )

    # Run BLAST search
    result_set = _blast_search(subsequence, evalue, identity, max_hits)

    # Extract hits
    subseq_len = len(subsequence)
    hits = [_extract_hit(r, subseq_len) for r in result_set]

    # Fetch structure metadata for all hits
    if hits:
        _fetch_structure_metadata(hits, accession)

    # Build manifest
    manifest = {
        "query": {
            "uniprot_accession": accession,
            "gene_symbol": gene_symbol,
            "residue_range": [start, end],
            "query_sequence": subsequence,
            "canonical_length": canonical_length,
        },
        "search_parameters": {
            "evalue_cutoff": evalue,
            "identity_cutoff": identity,
            "max_hits": max_hits,
            "search_type": "domain_subsequence",
        },
        "hits": hits,
        "hit_count": len(hits),
        "search_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    stem = f"HOMOLOGY-{accession}-{start}-{end}"

    manifest_path = target_dir / f"{stem}.search.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(manifest_path)

    meta_path = target_dir / f"{stem}.meta.json"
    meta = sidecar.write(meta_path)

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("hit_count", len(hits))
    emit.path(project.relative(manifest_path), "manifest")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Ortholog / paralog search
# ---------------------------------------------------------------------------


def _resolve_gene_to_accession(gene: str) -> str:
    """Resolve a gene symbol to a UniProt accession via UniProt search.

    Searches for a reviewed (Swiss-Prot) entry matching the gene name
    exactly. Returns the first accession found. Raises UsageError if
    nothing matches.
    """
    url = (
        f"{UNIPROT_API}/search?query=gene_exact:{gene}"
        "+AND+reviewed:true&fields=accession&size=1&format=json"
    )
    data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"))
    results = data.get("results") or []
    if not results:
        raise UsageError(
            f"no reviewed UniProt entry found for gene symbol {gene!r}",
            remedy="pass a UniProt accession directly, or check the gene name",
        )
    return results[0]["primaryAccession"]


def _search_orthologs(
    gene: str,
    max_orthologs: int,
    organism_filter: str | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Query UniProt for orthologs of a gene.

    Returns (query_accession, list_of_ortholog_records).
    """
    query_parts = [f"gene_exact:{gene}"]
    if organism_filter:
        query_parts.append(f"organism_name:{organism_filter}")
    query_str = "+AND+".join(query_parts)
    url = (
        f"{UNIPROT_API}/search?query={query_str}"
        f"&fields=accession,gene_names,organism_name,sequence"
        f"&size={max_orthologs}&format=json"
    )
    data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"))
    results = data.get("results") or []

    orthologs: list[dict[str, Any]] = []
    for entry in results:
        acc = entry.get("primaryAccession", "")
        genes = entry.get("genes") or []
        gene_names_list: list[str] = []
        for g in genes:
            gn = (g.get("geneName") or {}).get("value")
            if gn:
                gene_names_list.append(gn)
        organism = (entry.get("organism") or {}).get("scientificName", "")
        seq_block = entry.get("sequence") or {}
        sequence = seq_block.get("value", "")
        length = seq_block.get("length", 0)

        orthologs.append(
            {
                "accession": acc,
                "gene_names": gene_names_list,
                "organism": organism,
                "sequence": sequence,
                "length": length,
            }
        )

    return gene, orthologs


def _format_fasta(orthologs: list[dict[str, Any]]) -> str:
    """Format ortholog records as FASTA text."""
    lines: list[str] = []
    for rec in orthologs:
        acc = rec["accession"]
        organism = rec.get("organism", "")
        gene_names = rec.get("gene_names", [])
        gene_str = gene_names[0] if gene_names else ""
        header = f">{acc} {gene_str} OS={organism}"
        lines.append(header)
        seq = rec.get("sequence", "")
        # Wrap sequence at 70 characters
        for i in range(0, len(seq), 70):
            lines.append(seq[i : i + 70])
    return "\n".join(lines) + "\n" if lines else ""


@homology.command()
@click.argument("gene_or_uniprot_id")
@click.option(
    "--organism-filter",
    default=None,
    help="Filter orthologs by organism name (e.g. 'Mammalia').",
)
@click.option(
    "--max-orthologs",
    type=int,
    default=20,
    show_default=True,
    help="Maximum number of orthologs to return.",
)
@out_option
@output_options
@pass_state
def orthologs(
    state: AppState,
    gene_or_uniprot_id: str,
    organism_filter: str | None,
    max_orthologs: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search for orthologs of a gene or UniProt accession.

    Accepts a UniProt accession (e.g. P04637) or gene symbol (e.g. TP53).
    Queries UniProt for orthologous sequences across species and writes
    the results in FASTA format ready for multiple sequence alignment.
    """
    raw_input = gene_or_uniprot_id.strip()

    # Determine if this is a UniProt accession or a gene symbol
    accession_candidate = raw_input.upper()
    if _UNIPROT_RE.match(accession_candidate):
        # It's a UniProt accession — fetch gene symbol for query
        _seq, _len, gene_symbol = _fetch_uniprot(accession_candidate)
        if not gene_symbol:
            raise UsageError(
                f"UniProt entry {accession_candidate} has no gene symbol; "
                "cannot search for orthologs by gene",
                remedy="provide a gene symbol directly instead",
            )
        query_gene = gene_symbol
        query_accession = accession_candidate
    else:
        # Treat as a gene symbol — resolve to accession for provenance
        query_gene = raw_input
        query_accession = _resolve_gene_to_accession(query_gene)

    # Search for orthologs
    _gene, ortholog_list = _search_orthologs(query_gene, max_orthologs, organism_filter)

    project = state.project()
    target_dir = project.artifact_dir(ORTHOLOGS_ARTIFACT_CLASS, out)

    # Build FASTA output
    fasta_text = _format_fasta(ortholog_list)

    stem = f"ORTHOLOGS-{sanitize_slug(query_accession)}-{sanitize_slug(query_gene)}"

    # Write FASTA
    fasta_path = target_dir / f"{stem}.fasta"
    fasta_path.write_text(fasta_text, encoding="utf-8")

    # Write JSON manifest
    manifest = {
        "query": {
            "gene_symbol": query_gene,
            "uniprot_accession": query_accession,
            "organism_filter": organism_filter,
            "max_orthologs": max_orthologs,
        },
        "orthologs": ortholog_list,
        "ortholog_count": len(ortholog_list),
        "search_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    manifest_path = target_dir / f"{stem}.orthologs.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Write provenance sidecar
    sidecar = provenance.Sidecar(
        tool=TOOL_ORTHOLOGS,
        subcommand="orthologs",
        endpoint=UNIPROT_API,
        parameters={
            "gene_or_uniprot_id": raw_input,
            "resolved_gene": query_gene,
            "resolved_accession": query_accession,
            "organism_filter": organism_filter,
            "max_orthologs": max_orthologs,
        },
    )
    sidecar.add_output(fasta_path)
    sidecar.add_output(manifest_path)

    meta_path = target_dir / f"{stem}.orthologs.meta.json"
    meta = sidecar.write(meta_path)

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("ortholog_count", len(ortholog_list))
    emit.data("gene_symbol", query_gene)
    emit.data("uniprot_accession", query_accession)
    emit.line(f"{query_gene} ({query_accession}): {len(ortholog_list)} orthologs found")
    for orth in ortholog_list[:5]:
        emit.line(f"  {orth['accession']}  {orth['organism']}")
    if len(ortholog_list) > 5:
        emit.line(f"  … {len(ortholog_list) - 5} more")
    emit.path(project.relative(fasta_path), "fasta")
    emit.path(project.relative(manifest_path), "manifest")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2 — homology analysis
# ---------------------------------------------------------------------------


def _classify_identity(sequence_identity: float, t) -> str:
    """Classify a hit's sequence identity into a band."""
    if sequence_identity >= t.get("identity_high"):
        return "high"
    if sequence_identity >= t.get("identity_moderate"):
        return "moderate"
    return "remote"


def _classify_resolution(resolution_angstrom: float | None, t) -> str:
    """Classify a hit's resolution into a quality tier."""
    if resolution_angstrom is None:
        return "not-applicable"
    if resolution_angstrom <= t.get("resolution_high"):
        return "high"
    if resolution_angstrom <= t.get("resolution_low"):
        return "moderate"
    return "low"


def _classify_coverage(query_coverage_fraction: float, t) -> str:
    """Classify a hit's query coverage."""
    if query_coverage_fraction >= t.get("coverage_minimum"):
        return "adequate"
    return "insufficient"


def _assess_hit(hit: dict[str, Any], t) -> dict[str, Any]:
    """Classify a single hit across identity, resolution, and coverage."""
    alignment = hit.get("alignment", {})
    seq_id = alignment.get("sequence_identity", 0)
    resolution = hit.get("resolution_angstrom")
    coverage = alignment.get("query_coverage_fraction", 0)

    return {
        "pdb_entity_id": hit.get("pdb_entity_id", ""),
        "pdb_id": hit.get("pdb_id", ""),
        "sequence_identity": seq_id,
        "resolution_angstrom": resolution,
        "query_coverage_fraction": coverage,
        "identity_band": _classify_identity(seq_id, t),
        "resolution_quality": _classify_resolution(resolution, t),
        "coverage": _classify_coverage(coverage, t),
        "source_uniprot": hit.get("source_uniprot", ""),
        "is_direct_structure": hit.get("is_direct_structure", False),
        "title": hit.get("title", ""),
    }


def _compute_verdict(hit_assessments: list[dict[str, Any]], hit_count: int) -> str:
    """Compute the overall verdict from classified hits.

    Priority order (first match wins):
      1. strong-candidates  — at least one high-identity + adequate coverage
      2. moderate-candidates — best is moderate band with adequate coverage,
                               OR high band but inadequate coverage
      3. remote-only        — all hits below identity_moderate
      4. no-coverage        — hits exist but none have adequate coverage
      5. no-hits            — hit_count == 0
    """
    if hit_count == 0:
        return "no-hits"

    has_high_adequate = any(
        h["identity_band"] == "high" and h["coverage"] == "adequate"
        for h in hit_assessments
    )
    if has_high_adequate:
        return "strong-candidates"

    has_high_or_moderate = any(
        h["identity_band"] in ("high", "moderate") for h in hit_assessments
    )
    if has_high_or_moderate:
        return "moderate-candidates"

    has_adequate = any(h["coverage"] == "adequate" for h in hit_assessments)
    if not has_adequate:
        return "no-coverage"

    return "remote-only"


def _build_statement(
    verdict: str,
    hit_assessments: list[dict[str, Any]],
    query: dict[str, Any],
) -> str:
    """Build a brief human-readable assessment statement."""
    accession = query.get("uniprot_accession", "?")
    residue_range = query.get("residue_range", [0, 0])
    n = len(hit_assessments)

    if verdict == "no-hits":
        return (
            f"No homologous structures found for the query region "
            f"(residues {residue_range[0]}-{residue_range[1]}) of {accession}."
        )

    identities = [h["sequence_identity"] for h in hit_assessments]
    best_id = max(identities) if identities else 0
    worst_id = min(identities) if identities else 0

    if best_id >= 0.5:
        zone = "high similarity"
    elif best_id >= 0.3:
        zone = "twilight zone"
    else:
        zone = "remote homology"

    direct_count = sum(1 for h in hit_assessments if h["is_direct_structure"])

    parts = [
        f"{n} homologous structure{'s' if n != 1 else ''} found covering the "
        f"region (residues {residue_range[0]}-{residue_range[1]}) of {accession}, "
        f"with sequence identities {worst_id:.1%}-{best_id:.1%} ({zone}).",
    ]

    if direct_count == 0:
        parts.append(
            f"No direct experimental structure of {accession} covers this range."
        )
    elif direct_count == n:
        parts.append(
            f"All {n} hit{'s' if n != 1 else ''} {'are' if n != 1 else 'is a'} "
            f"direct experimental structure{'s' if n != 1 else ''} of {accession}."
        )

    return " ".join(parts)


@homology.command()
@click.argument("uniprot_id_or_path")
@click.option(
    "--identity-high",
    type=float,
    default=None,
    help="Override identity_high threshold.",
)
@click.option(
    "--identity-moderate",
    type=float,
    default=None,
    help="Override identity_moderate threshold.",
)
@from_option
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    uniprot_id_or_path: str,
    identity_high: float | None,
    identity_moderate: float | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Analyze a homology search manifest. Accepts a UniProt ID or path.

    Reads the stored search manifest and applies the `homology` threshold
    set to classify each hit by identity band, resolution quality, and
    coverage. Does not re-query any endpoint.
    """
    project = state.project()
    source_dir = project.artifact_dir(ARTIFACT_CLASS, from_dir)

    candidate = Path(uniprot_id_or_path)
    if candidate.suffix:
        # Treat as a path to the manifest file
        manifest_path = (
            candidate if candidate.is_absolute() else project.root / candidate
        )
        confined = confine_path(project.root, manifest_path)
        if confined is None:
            raise ArtifactError(f"manifest path escapes project root: {manifest_path}")
        manifest_path = confined
    else:
        # Treat as a UniProt accession — search for the manifest
        accession = uniprot_id_or_path.strip().upper()
        matches = sorted(source_dir.glob(f"HOMOLOGY-{accession}-*.search.json"))
        if len(matches) == 1:
            manifest_path = matches[0]
        elif len(matches) > 1:
            raise Refusal(
                f"{accession} has {len(matches)} search manifests with different "
                "ranges; the accession alone does not say which to analyse",
                detail=", ".join(p.name for p in matches),
                remedy="pass the path of the manifest you mean rather than the "
                "bare accession",
            )
        else:
            manifest_path = source_dir / f"HOMOLOGY-{accession}.search.json"

    if not manifest_path.is_file():
        raise ArtifactError(
            f"no search manifest at {manifest_path}",
            remedy=f"run `dde homology search {uniprot_id_or_path}` first",
        )

    manifest = provenance.read_json(manifest_path, "homology search manifest")

    # Load the homology threshold set with overrides
    thresholds = load_thresholds(
        state,
        "homology",
        {
            "identity_high": identity_high,
            "identity_moderate": identity_moderate,
        },
    )

    query = manifest.get("query", {})
    accession = query.get("uniprot_accession", "unknown")
    hits = manifest.get("hits", [])
    hit_count = manifest.get("hit_count", len(hits))

    # Classify each hit
    hit_assessments = [_assess_hit(h, thresholds) for h in hits]

    # Compute overall verdict
    verdict = _compute_verdict(hit_assessments, hit_count)

    # Build statement
    statement = _build_statement(verdict, hit_assessments, query)

    # Build relays
    relays: list[dict[str, str]] = []
    non_direct = [h for h in hits if not h.get("is_direct_structure", False)]
    if non_direct:
        sources = sorted({h.get("source_uniprot", "unknown") for h in non_direct})
        sources_str = ", ".join(sources[:5])
        if len(sources) > 5:
            sources_str += f" and {len(sources) - 5} more"
        total = len(hits)
        if len(non_direct) == total:
            count_str = f"All {total}"
        else:
            count_str = f"{len(non_direct)} of {total}"
        relays.append(
            provenance.relay(
                "homology.structure_is_not_target",
                f"{count_str} hit(s) are structures of homologous proteins "
                f"({sources_str}), not {accession} itself. Structural features "
                "attributed to the query protein from these structures are hypotheses "
                "transferred by sequence similarity, not direct observations.",
            )
        )

    # Compute metrics
    metrics = {
        "total_hits": len(hits),
        "hits_covering_range": sum(
            1 for h in hit_assessments if h["coverage"] == "adequate"
        ),
        "best_identity": max(
            (h["sequence_identity"] for h in hit_assessments), default=0
        ),
        "best_resolution_angstrom": min(
            (
                h["resolution_angstrom"]
                for h in hit_assessments
                if h["resolution_angstrom"] is not None
            ),
            default=None,
        ),
        "direct_structure_count": sum(
            1 for h in hits if h.get("is_direct_structure", False)
        ),
    }

    # Build advisories
    advisories: list[str] = []
    coverage_min = thresholds.get("coverage_minimum")
    insufficient = [h for h in hit_assessments if h["coverage"] == "insufficient"]
    if insufficient:
        advisories.append(
            f"{len(insufficient)} of {len(hit_assessments)} hit(s) have query "
            f"coverage below {coverage_min:.0%}; alignment may cover only a "
            "fragment of the query domain."
        )
    if verdict == "remote-only":
        advisories.append(
            "All hits are remote homologs (sequence identity below "
            f"{thresholds.get('identity_moderate'):.0%}). Structural transfer "
            "at this distance is unreliable; fold conservation cannot be assumed."
        )

    assessment = {
        "verdict": verdict,
        "statement": statement,
        "hit_assessments": hit_assessments,
        "advisories": advisories,
    }

    # Resolve the meta.json sidecar path for the source field
    meta_path = manifest_path.with_name(
        manifest_path.name.replace(".search.json", ".meta.json")
    )
    source_ref = (
        project.relative(meta_path)
        if meta_path.is_file()
        else project.relative(manifest_path)
    )

    analysis_path = beside_or_out(
        state,
        manifest_path,
        manifest_path.name.replace(".search.json", ".homology.analysis.json"),
        out,
    )
    provenance.write_analysis(
        analysis_path,
        source=source_ref,
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    # Emit output
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)
    stem = manifest_path.stem.replace(".search", "")
    emit.line(f"{stem}  [threshold_set {thresholds.tag}]")
    emit.line(f"Verdict: {verdict}")
    emit.line(statement)
    for _i, ha in enumerate(hit_assessments[:10]):
        emit.line(
            f"  {ha['pdb_entity_id']}  identity={ha['sequence_identity']:.1%} "
            f"({ha['identity_band']})  resolution={ha['resolution_angstrom'] or '?'} "
            f"({ha['resolution_quality']})  coverage={ha['query_coverage_fraction']:.1%} "
            f"({ha['coverage']})"
        )
    if len(hit_assessments) > 10:
        emit.line(f"  … {len(hit_assessments) - 10} more hit(s) omitted")
    for advisory in advisories[:3]:
        emit.line(f"* {advisory}")
    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 3 — fetch-structure
# ---------------------------------------------------------------------------


def _parse_pdb_entity_id(pdb_entity_id: str) -> tuple[str, str]:
    """Parse a PDB entity identifier into (pdb_id, original_input).

    Accepts ``7FD3_1`` (entity suffix) or ``7FD3`` (bare PDB ID).
    Returns the uppercased PDB ID and the original input string.
    """
    raw = pdb_entity_id.strip()
    parts = raw.split("_")
    pdb_id = parts[0].upper()
    return pdb_id, raw


@homology.command("fetch-structure")
@click.argument("pdb_entity_id")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["cif", "pdb"], case_sensitive=False),
    default="cif",
    help="Coordinate file format (cif or pdb).",
)
@out_option
@output_options
@pass_state
def fetch_structure(
    state: AppState,
    pdb_entity_id: str,
    fmt: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Download a PDB coordinate file from RCSB.

    Fetches the structure file for the given PDB entity identifier
    (e.g. ``7FD3_1`` or ``7FD3``) and writes a provenance sidecar
    recording the download endpoint and SHA-256 hash.
    """
    pdb_id, raw_input = _parse_pdb_entity_id(pdb_entity_id)
    pdb_id = sanitize_slug(pdb_id)

    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    download_url = f"{RCSB_DOWNLOAD_BASE}/{pdb_id}.{fmt}"

    # Download the coordinate file
    structure_path = target_dir / f"{pdb_id}.{fmt}"
    structure_path.write_bytes(
        http.get_bytes(download_url, qps=qps_for_host("files.rcsb.org"))
    )

    # Write provenance sidecar
    sidecar = provenance.Sidecar(
        tool=TOOL_FETCH,
        subcommand="fetch-structure",
        endpoint=download_url,
        parameters={
            "pdb_entity_id": raw_input,
            "pdb_id": pdb_id,
            "format": fmt,
        },
    )
    sidecar.add_output(structure_path)

    meta_path = target_dir / f"{pdb_id}.meta.json"
    meta = sidecar.write(meta_path)

    # Emit output
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.path(project.relative(structure_path), "structure")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()
