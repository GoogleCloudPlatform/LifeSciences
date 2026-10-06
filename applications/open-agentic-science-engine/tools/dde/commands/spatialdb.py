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

"""`dde spatialdb` — SpatialDB spatial transcriptomics dataset search.

Queries SpatialDB (www.spatialomics.org/SpatialDB/) for spatial
transcriptomics datasets matching a gene symbol.  SpatialDB indexes
published spatial transcriptomics experiments across human and mouse
tissues.

Two phases:

  search   queries the SpatialDB gene search endpoint and writes
           structured results to Layer 0 with a provenance sidecar.
  analyze  reads stored search results and produces a summary analysis:
           tissue distribution, technique distribution, species
           breakdown, and unique PMIDs.  No network.

SpatialDB captures gene expression with tissue coordinates but covers a
limited set of tissues and studies.  Absence from SpatialDB does not mean
a gene lacks spatial expression data — the database indexes published
spatial transcriptomics datasets, not all spatial experiments.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

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
from ..core.errors import ArtifactError, SchemaError
from ..core.qps import qps_for_host

TOOL = "spatialdb"
ARTIFACT_CLASS = "transcriptomics"

SPATIALDB_SEARCH_URL = "https://www.spatialomics.org/SpatialDB/server/searchpage.php"

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "spatialdb-search"


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return _HTML_TAG_RE.sub("", text)


def _parse_records(data: list[list[str]]) -> list[dict[str, Any]]:
    """Parse SpatialDB DataTables rows into structured records."""
    records: list[dict[str, Any]] = []
    for row in data:
        if len(row) < 9:
            continue
        records.append(
            {
                "ensembl_id": _strip_html(row[0]),
                "gene": _strip_html(row[1]),
                "species": _strip_html(row[2]),
                "tissue": _strip_html(row[3]),
                "sample": _strip_html(row[4]),
                "replication": _strip_html(row[5]),
                "platform": _strip_html(row[6]),
                "technique": _strip_html(row[7]),
                "pmid": _strip_html(row[8]),
            }
        )
    return records


def _fetch_gene(
    gene: str,
    species: str,
) -> tuple[bytes, dict[str, Any]]:
    """Fetch SpatialDB records for a gene.

    Returns (verbatim response bytes, structured artifact dict).
    """
    response = http.request(
        "POST",
        SPATIALDB_SEARCH_URL,
        data={"gene": gene, "species": species},
        qps=qps_for_host("www.spatialomics.org"),
        timeout=120.0,
    )

    raw = response.content

    try:
        raw_data = response.json()
    except ValueError as exc:
        raise SchemaError(
            "SpatialDB search endpoint did not return valid JSON",
            detail=str(exc),
        ) from exc

    if not isinstance(raw_data, dict):
        raise SchemaError(
            "SpatialDB search endpoint did not return a JSON object",
            detail=f"got {type(raw_data).__name__}",
        )

    data_rows = raw_data.get("data", [])
    if not isinstance(data_rows, list):
        raise SchemaError(
            "SpatialDB response 'data' field is not an array",
            detail=f"got {type(data_rows).__name__}",
        )

    records = _parse_records(data_rows)

    unique_tissues = {r["tissue"] for r in records if r["tissue"]}
    unique_techniques = {r["technique"] for r in records if r["technique"]}

    artifact: dict[str, Any] = {
        "schema": "dde.spatialdb-search.v1",
        "query": {"gene": gene, "species": species},
        "summary": {
            "n_records": len(records),
            "n_unique_tissues": len(unique_tissues),
            "n_unique_techniques": len(unique_techniques),
        },
        "records": records,
    }

    return raw, artifact


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def spatialdb() -> None:
    """SpatialDB spatial transcriptomics dataset search."""


@spatialdb.command("search")
@click.argument("gene")
@click.option(
    "--species",
    default="Human",
    type=click.Choice(["Human", "Mouse"], case_sensitive=False),
    help="Species to search (default: Human).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    gene: str,
    species: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search SpatialDB for spatial transcriptomics datasets for GENE.

    GENE is a gene symbol (e.g. GFRA3).  Use --species to select Human
    or Mouse (default: Human).
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(gene)

    raw, artifact = _fetch_gene(gene, species)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=SPATIALDB_SEARCH_URL,
        parameters={
            "gene": gene,
            "species": species,
        },
    )
    sidecar.note("source", "SpatialDB (www.spatialomics.org/SpatialDB/)")
    sidecar.note("n_records", artifact["summary"]["n_records"])
    sidecar.note("n_unique_tissues", artifact["summary"]["n_unique_tissues"])

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.spatialdb.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.spatialdb.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.spatialdb.meta.json")

    emit.data("gene", gene)
    emit.data("species", species)
    emit.data("n_records", artifact["summary"]["n_records"])
    emit.data("n_unique_tissues", artifact["summary"]["n_unique_tissues"])
    emit.data("n_unique_techniques", artifact["summary"]["n_unique_techniques"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"SpatialDB search: {gene!r} ({species})")
    emit.line(
        f"  {artifact['summary']['n_records']} records, "
        f"{artifact['summary']['n_unique_tissues']} unique tissues, "
        f"{artifact['summary']['n_unique_techniques']} unique techniques"
    )
    if artifact["records"]:
        seen_tissues: set[str] = set()
        for r in artifact["records"]:
            tissue = r["tissue"]
            if tissue not in seen_tissues:
                seen_tissues.add(tissue)
                emit.line(f"  {tissue} ({r['technique']})")
            if len(seen_tissues) >= 5:
                break
        remaining = artifact["summary"]["n_unique_tissues"] - len(seen_tissues)
        if remaining > 0:
            emit.line(f"  ... {remaining} more tissues in the artifact")
    emit.flush()


@spatialdb.command("analyze")
@click.argument("gene")
@click.option(
    "--species",
    default="Human",
    type=click.Choice(["Human", "Mouse"], case_sensitive=False),
    help="Species used in the original search (for slug matching).",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    gene: str,
    species: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored SpatialDB search results for GENE. No network.

    Reads what ``search`` wrote and produces a summary analysis: tissue
    distribution, technique distribution, species breakdown, and unique
    PMIDs.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(gene)

    artifact_path = source_dir / f"{slug}.spatialdb.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no SpatialDB search artifact for {gene!r} under {source_dir}",
            remedy=f"run `dde spatialdb search {gene!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "SpatialDB search artifact")
    if artifact.get("schema") != "dde.spatialdb-search.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.spatialdb-search.v1, got {artifact.get('schema')!r}",
        )

    records = artifact.get("records", [])
    query_meta = artifact.get("query", {})
    n = len(records)

    tissue_counts: Counter[str] = Counter()
    technique_counts: Counter[str] = Counter()
    platform_counts: Counter[str] = Counter()
    species_counts: Counter[str] = Counter()
    pmid_set: set[str] = set()

    for r in records:
        tissue = r.get("tissue", "")
        if tissue:
            tissue_counts[tissue] += 1
        technique = r.get("technique", "")
        if technique:
            technique_counts[technique] += 1
        platform = r.get("platform", "")
        if platform:
            platform_counts[platform] += 1
        sp = r.get("species", "")
        if sp:
            species_counts[sp] += 1
        pmid = r.get("pmid", "")
        if pmid:
            pmid_set.add(pmid)

    outcome = "records_found" if records else "no_records"

    relays: list[dict[str, str]] = []
    if n > 0:
        relays.append(
            provenance.relay(
                "spatialdb.spatial_not_bulk",
                f"SpatialDB returned {n} record(s) for {gene}; "
                "these describe spatially resolved expression experiments. "
                "Spatial transcriptomics captures gene expression with tissue "
                "coordinates but covers a limited set of tissues and studies. "
                "Absence from SpatialDB does not mean a gene lacks spatial "
                "expression data — the database indexes published spatial "
                "transcriptomics datasets, not all spatial experiments.",
            )
        )

    assessment: dict[str, Any] = {
        "outcome": outcome,
        "gene": query_meta.get("gene", gene),
        "species": query_meta.get("species", species),
        "n_records": n,
        "tissue_distribution": dict(tissue_counts.most_common()),
        "technique_distribution": dict(technique_counts.most_common()),
        "platform_distribution": dict(platform_counts.most_common()),
        "species_breakdown": dict(species_counts),
        "pmid_list": sorted(pmid_set),
    }

    metrics: dict[str, Any] = {
        "n_records": n,
        "n_unique_tissues": len(tissue_counts),
        "n_unique_techniques": len(technique_counts),
        "n_unique_platforms": len(platform_counts),
        "n_unique_pmids": len(pmid_set),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.spatialdb.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="spatialdb-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"SpatialDB analysis: {gene!r} ({species})")
    emit.line(f"  Outcome: {outcome}")
    emit.line(f"  {n} records, {len(tissue_counts)} unique tissues")
    if tissue_counts:
        emit.line("  Tissue distribution:")
        for t, c in tissue_counts.most_common(5):
            emit.line(f"    {t}: {c}")
    if technique_counts:
        emit.line("  Technique distribution:")
        for tech, c in technique_counts.most_common(5):
            emit.line(f"    {tech}: {c}")
    if species_counts:
        emit.line(f"  Species: {dict(species_counts)}")
    if pmid_set:
        emit.line(f"  Unique PMIDs: {', '.join(sorted(pmid_set))}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
