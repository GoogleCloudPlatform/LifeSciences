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

"""`dde geo` -- NCBI GEO (Gene Expression Omnibus) dataset search.

Queries NCBI E-utilities to find GEO DataSets/Series matching gene,
disease, organism, or assay criteria.  GEO hosts expression profiling,
genome variation, and other high-throughput functional genomics datasets.

Two phases:

  search   queries NCBI esearch + esummary for GEO datasets and writes
           structured results to Layer 0 with a provenance sidecar.
  analyze  reads stored search results and produces a summary: dataset
           count, assay type distribution, organism breakdown, sample
           count statistics, and platform distribution.  No network.

The search returns dataset-level metadata — title, summary, sample list,
platform, and FTP links.  It does NOT return expression values or
differential expression results.  Determining whether a gene is
differentially expressed in a dataset requires downloading and analysing
the expression data (GEO2R, supplementary files, or SRA raw data).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from statistics import median
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
from ..core.ncbi import api_key_params
from ..core.qps import qps_for_host

TOOL = "geo"
ARTIFACT_CLASS = "transcriptomics"

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

VALID_ENTRY_TYPES = ("gse", "gds", "gpl", "gsm")


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "geo-search"


def _build_term(
    query: str,
    organism: str | None,
    entry_type: str | None,
    data_type: str | None,
    year_from: int | None,
    year_to: int | None,
) -> str:
    """Construct the GEO search term from user options."""
    parts: list[str] = [query]
    if organism:
        parts.append(f"{organism}[Organism]")
    if entry_type:
        parts.append(f"{entry_type}[Entry Type]")
    if data_type:
        parts.append(f"{data_type}[DataSet Type]")
    if year_from or year_to:
        yf = f"{year_from}/01/01" if year_from else "1900/01/01"
        yt = f"{year_to}/12/31" if year_to else "3000/12/31"
        parts.append(f"{yf}:{yt}[Publication Date]")
    return " AND ".join(parts)


def _extract_result(doc: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured record from an esummary document."""
    # Samples may be a dict of accession->title pairs or a list.
    raw_samples = doc.get("samples", [])
    samples: list[dict[str, str]] = []
    if isinstance(raw_samples, dict):
        for acc, info in list(raw_samples.items())[:20]:
            title = info.get("title", "") if isinstance(info, dict) else str(info)
            samples.append({"accession": acc, "title": title})
    elif isinstance(raw_samples, list):
        for s in raw_samples[:20]:
            if isinstance(s, dict):
                samples.append(
                    {
                        "accession": s.get("accession", s.get("Accession", "")),
                        "title": s.get("title", s.get("Title", "")),
                    }
                )

    # PubMed IDs may come as a list of ints/strings or a single value.
    raw_pmids = doc.get("pubmedids", [])
    if isinstance(raw_pmids, list):
        pubmed_ids = [int(p) for p in raw_pmids if p]
    elif raw_pmids:
        pubmed_ids = [int(raw_pmids)]
    else:
        pubmed_ids = []

    summary_raw = doc.get("summary", "")
    summary = summary_raw[:1000] if isinstance(summary_raw, str) else ""

    return {
        "accession": doc.get("accession", ""),
        "title": doc.get("title", ""),
        "summary": summary,
        "taxon": doc.get("taxon", ""),
        "gdstype": doc.get("gdstype", ""),
        "n_samples": int(doc.get("n_samples", 0)),
        "platform": doc.get("gpl", ""),
        "publication_date": doc.get("pdat", ""),
        "ftp_link": doc.get("ftplink", ""),
        "pubmed_ids": pubmed_ids,
        "samples": samples,
    }


def _fetch_geo(
    query: str,
    organism: str | None,
    entry_type: str | None,
    data_type: str | None,
    year_from: int | None,
    year_to: int | None,
    max_results: int,
) -> tuple[bytes, dict[str, Any]]:
    """Query NCBI esearch + esummary and return (verbatim bytes, artifact).

    Raises SchemaError on unexpected response shapes, Refusal on input
    issues.
    """
    constructed_term = _build_term(
        query,
        organism,
        entry_type,
        data_type,
        year_from,
        year_to,
    )

    # Phase 1a: esearch — get matching IDs.
    esearch_data = http.get_json(
        ESEARCH_URL,
        params={
            "db": "gds",
            "retmode": "json",
            "retmax": max_results,
            "term": constructed_term,
            **api_key_params(),
        },
        qps=qps_for_host("eutils.ncbi.nlm.nih.gov"),
        timeout=60.0,
    )

    if not isinstance(esearch_data, dict):
        raise SchemaError(
            "NCBI esearch did not return a JSON object",
            detail=f"got {type(esearch_data).__name__}",
        )

    esearch_result = esearch_data.get("esearchresult", {})
    id_list = esearch_result.get("idlist", [])
    esearch_count = int(esearch_result.get("count", 0))

    if not id_list:
        # No results — return empty artifact.
        artifact: dict[str, Any] = {
            "schema": "dde.geo-search.v1",
            "query": {
                "text": query,
                "organism": organism or "Homo sapiens",
                "entry_type": entry_type or "gse",
                "data_type": data_type,
                "year_from": year_from,
                "year_to": year_to,
                "constructed_term": constructed_term,
                "esearch_count": 0,
            },
            "summary": {
                "n_results": 0,
                "esearch_count": 0,
            },
            "results": [],
        }
        return json.dumps(esearch_data, indent=2).encode("utf-8"), artifact

    # Phase 1b: esummary — get details for each ID.
    esummary_data = http.get_json(
        ESUMMARY_URL,
        params={
            "db": "gds",
            "id": ",".join(id_list),
            "retmode": "json",
            **api_key_params(),
        },
        qps=qps_for_host("eutils.ncbi.nlm.nih.gov"),
        timeout=120.0,
    )

    if not isinstance(esummary_data, dict):
        raise SchemaError(
            "NCBI esummary did not return a JSON object",
            detail=f"got {type(esummary_data).__name__}",
        )

    raw = json.dumps(esummary_data, indent=2).encode("utf-8")

    # Parse results from esummary.  The 'result' key holds a dict
    # keyed by UID, plus a 'uids' list.
    result_block = esummary_data.get("result", {})
    uids = result_block.get("uids", [])

    results: list[dict[str, Any]] = []
    for uid in uids:
        doc = result_block.get(str(uid))
        if not isinstance(doc, dict):
            continue
        results.append(_extract_result(doc))

    artifact = {
        "schema": "dde.geo-search.v1",
        "query": {
            "text": query,
            "organism": organism or "Homo sapiens",
            "entry_type": entry_type or "gse",
            "data_type": data_type,
            "year_from": year_from,
            "year_to": year_to,
            "constructed_term": constructed_term,
            "esearch_count": esearch_count,
        },
        "summary": {
            "n_results": len(results),
            "esearch_count": esearch_count,
        },
        "results": results,
    }

    return raw, artifact


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def geo() -> None:
    """NCBI GEO (Gene Expression Omnibus) dataset search."""


@geo.command("search")
@click.argument("query")
@click.option(
    "--organism",
    default="Homo sapiens",
    help="Filter by organism (default: 'Homo sapiens').",
)
@click.option(
    "--entry-type",
    default="gse",
    type=click.Choice(VALID_ENTRY_TYPES, case_sensitive=False),
    help="GEO entry type (default: gse).",
)
@click.option(
    "--data-type",
    default=None,
    help="GEO DataSet type (e.g. 'Expression profiling by high throughput sequencing').",
)
@click.option(
    "--year-from",
    default=None,
    type=int,
    help="Include only datasets published in or after this year.",
)
@click.option(
    "--year-to",
    default=None,
    type=int,
    help="Include only datasets published in or before this year.",
)
@click.option(
    "--max-results",
    default=20,
    type=click.IntRange(1, 200),
    help="Maximum number of results to return (1-200, default 20).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    organism: str | None,
    entry_type: str | None,
    data_type: str | None,
    year_from: int | None,
    year_to: int | None,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search NCBI GEO for datasets matching QUERY.

    QUERY is free-text search terms for the GEO DataSets database.
    Use --organism, --entry-type, --data-type, and --year-from/--year-to
    to narrow results.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    raw, artifact = _fetch_geo(
        query,
        organism,
        entry_type,
        data_type,
        year_from,
        year_to,
        max_results,
    )

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=ESEARCH_URL,
        parameters={
            "query": query,
            "organism": organism or "Homo sapiens",
            "entry_type": entry_type or "gse",
            "data_type": data_type,
            "year_from": year_from,
            "year_to": year_to,
            "max_results": max_results,
            "constructed_term": artifact["query"]["constructed_term"],
        },
    )
    sidecar.note("source", "NCBI GEO (ncbi.nlm.nih.gov/geo)")
    sidecar.note("esearch_count", artifact["query"]["esearch_count"])
    sidecar.note("n_results", len(artifact["results"]))

    # Write verbatim esummary response.
    verbatim_path = target_dir / f"{slug}.geo.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.geo.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.geo.meta.json")

    n_results = len(artifact["results"])
    emit.data("query", query)
    emit.data("esearch_count", artifact["query"]["esearch_count"])
    emit.data("n_results", n_results)
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"GEO search: {query!r}")
    emit.line(
        f"  {artifact['query']['esearch_count']} total matches, {n_results} returned"
    )
    if artifact["results"]:
        for r in artifact["results"][:5]:
            emit.line(
                f"  {r['accession']}: {r['title'][:60]} ({r['n_samples']} samples)"
            )
        if n_results > 5:
            emit.line(f"  ... {n_results - 5} more in the artifact")
    emit.flush()


@geo.command("analyze")
@click.argument("query")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    query: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored GEO search results for QUERY. No network.

    Reads what ``search`` wrote and produces a summary: dataset count,
    assay type distribution, organism breakdown, sample count statistics,
    and platform distribution.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    artifact_path = source_dir / f"{slug}.geo.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no GEO search artifact for {query!r} under {source_dir}",
            remedy=f"run `dde geo search {query!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "GEO search artifact")
    if artifact.get("schema") != "dde.geo-search.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.geo-search.v1, got {artifact.get('schema')!r}",
        )

    results = artifact.get("results", [])
    query_meta = artifact.get("query", {})

    # Compute distributions.
    assay_type_counts: Counter[str] = Counter()
    organism_counts: Counter[str] = Counter()
    platform_counts: Counter[str] = Counter()
    year_counts: Counter[str] = Counter()
    sample_counts: list[int] = []

    for r in results:
        gdstype = r.get("gdstype", "")
        if gdstype:
            assay_type_counts[gdstype] += 1
        taxon = r.get("taxon", "")
        if taxon:
            organism_counts[taxon] += 1
        platform = r.get("platform", "")
        if platform:
            platform_counts[platform] += 1
        pdat = r.get("publication_date", "")
        if pdat:
            # Extract year from date string (e.g. "2023/05/15" -> "2023").
            year = pdat.split("/")[0] if "/" in pdat else pdat[:4]
            if year:
                year_counts[year] += 1
        n_samples = r.get("n_samples", 0)
        sample_counts.append(n_samples)

    n_datasets = len(results)

    sample_count_stats: dict[str, Any] = {}
    if sample_counts:
        sample_count_stats = {
            "min": min(sample_counts),
            "max": max(sample_counts),
            "median": median(sample_counts),
            "total": sum(sample_counts),
        }

    # Top datasets by sample count.
    sorted_by_samples = sorted(
        results, key=lambda r: r.get("n_samples", 0), reverse=True
    )
    top_datasets = [
        {
            "accession": r["accession"],
            "title": r["title"],
            "n_samples": r["n_samples"],
            "taxon": r.get("taxon", ""),
        }
        for r in sorted_by_samples[:10]
    ]

    relays: list[dict[str, str]] = []
    if n_datasets > 0:
        relays.append(
            provenance.relay(
                "geo.search_is_metadata_only",
                f"GEO search for {query_meta.get('text', query)!r} returned "
                f"{n_datasets} dataset(s); these are dataset-level metadata "
                "(title, summary, sample list). They do not contain expression "
                "values or differential expression results. Determining whether "
                "a gene is differentially expressed in a dataset requires "
                "downloading and analyzing the expression data (GEO2R, "
                "supplementary files, or SRA raw data).",
            )
        )

    assessment: dict[str, Any] = {
        "outcome": "datasets_found" if n_datasets else "no_datasets",
        "query_text": query_meta.get("text", ""),
        "query_organism": query_meta.get("organism", ""),
        "query_entry_type": query_meta.get("entry_type", ""),
        "query_data_type": query_meta.get("data_type"),
        "query_year_from": query_meta.get("year_from"),
        "query_year_to": query_meta.get("year_to"),
        "n_datasets": n_datasets,
        "assay_type_distribution": dict(assay_type_counts.most_common()),
        "organism_breakdown": dict(organism_counts),
        "sample_count_stats": sample_count_stats,
        "platform_distribution": dict(platform_counts.most_common()),
        "year_distribution": dict(sorted(year_counts.items())),
        "top_datasets": top_datasets,
    }

    metrics: dict[str, Any] = {
        "n_datasets": n_datasets,
        "n_unique_assay_types": len(assay_type_counts),
        "n_unique_organisms": len(organism_counts),
        "n_unique_platforms": len(platform_counts),
        "total_samples": sum(sample_counts) if sample_counts else 0,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.geo.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="geo-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"GEO analysis: {query!r}")
    emit.line(f"  Outcome: {assessment['outcome']}")
    emit.line(f"  {n_datasets} datasets")
    if sample_count_stats:
        emit.line(
            f"  Samples: min={sample_count_stats['min']}, "
            f"max={sample_count_stats['max']}, "
            f"median={sample_count_stats['median']}, "
            f"total={sample_count_stats['total']}"
        )
    if assay_type_counts:
        emit.line("  Assay types:")
        for atype, c in assay_type_counts.most_common(5):
            emit.line(f"    {atype}: {c}")
    if organism_counts:
        emit.line(f"  Organisms: {dict(organism_counts)}")
    if top_datasets:
        emit.line("  Top datasets by sample count:")
        for td in top_datasets[:5]:
            emit.line(
                f"    {td['accession']}: {td['title'][:50]} ({td['n_samples']} samples)"
            )
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
