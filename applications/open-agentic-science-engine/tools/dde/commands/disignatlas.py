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

"""`dde disignatlas` -- Disease Signature Atlas (DisigNAtlas) search.

Queries the DisigNAtlas database at https://www.inbirg.com/disignatlas/
for curated differential expression signatures across diseases.
DisigNAtlas aggregates DEG results from public transcriptomics datasets
(GEO, ArrayExpress, TCGA) using standardised pipelines.

Two phases:

  search   queries DisigNAtlas by gene symbol or disease term and
           writes structured results to Layer 0 with a sidecar.
  analyze  reads stored search results and summarises disease
           distribution, regulation patterns, and expression
           statistics.  No network.

Search results are pre-computed differential expression summaries
(log2FC, adjusted p-value) from published studies.  Individual study
quality, sample sizes, and normalisation methods vary -- treat as a
discovery resource, not as primary evidence.

Note: DisigNAtlas returns results as HTML with embedded JavaScript
arrays.  The parser extracts the ``result_information`` variable from
the page source.  If the HTML structure changes, parsing may silently
return zero results; a warning is emitted when this is detected.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
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
from ..core.errors import ArtifactError, EndpointUnavailable, Refusal, SchemaError
from ..core.qps import qps_for_host

_log = logging.getLogger(__name__)

TOOL = "disignatlas"
ARTIFACT_CLASS = "transcriptomics"

DISIGNATLAS_BASE = "https://www.inbirg.com/disignatlas"

# Field names for gene-search result arrays, in positional order.
_GENE_FIELDS = (
    "studyid",
    "disease",
    "tissue_celltype",
    "data_source",
    "library_strategy",
    "organism",
    "regulation",
    "geneid",
    "log2fc",
    "padj",
    "symbol",
)

# Field names for dataset/disease-search result arrays, in positional order.
_DISEASE_FIELDS = (
    "studyid",
    "disease",
    "tissue_celltype",
    "data_source",
    "library_strategy",
    "organism",
    "diffall",
)


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "disignatlas-search"


def _parse_js_array(html: str) -> list[list[Any]]:
    """Extract the result_information JavaScript array from HTML."""
    match = re.search(r"var\s+result_information\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not match:
        return []
    raw_js = match.group(1)
    try:
        return json.loads(raw_js)
    except json.JSONDecodeError:
        return []


def _safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None or value == "" or value == "NA" or value == "null":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _safe_int(value: Any) -> int:
    """Convert a value to int, returning 0 on failure."""
    if value is None or value == "" or value == "NA" or value == "null":
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _build_gene_records(
    rows: list[list[Any]],
    organism: str | None,
) -> list[dict[str, Any]]:
    """Build structured records from gene-search result rows."""
    records: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < len(_GENE_FIELDS):
            continue
        mapping = dict(zip(_GENE_FIELDS, row, strict=False))

        if organism:
            org_value = str(mapping.get("organism", ""))
            if organism.lower() not in org_value.lower():
                continue

        records.append(
            {
                "study_id": str(mapping.get("studyid", "")),
                "disease": str(mapping.get("disease", "")),
                "tissue_celltype": str(mapping.get("tissue_celltype", "")),
                "data_source": str(mapping.get("data_source", "")),
                "library_strategy": str(mapping.get("library_strategy", "")),
                "organism": str(mapping.get("organism", "")),
                "gene_symbol": str(mapping.get("symbol", "")),
                "gene_id": str(mapping.get("geneid", "")),
                "log2fc": _safe_float(mapping.get("log2fc")),
                "padj": _safe_float(mapping.get("padj")),
                "regulation": str(mapping.get("regulation", "")),
            }
        )
    return records


def _build_disease_records(
    rows: list[list[Any]],
    organism: str | None,
) -> list[dict[str, Any]]:
    """Build structured records from disease-search result rows."""
    records: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < len(_DISEASE_FIELDS):
            continue
        mapping = dict(zip(_DISEASE_FIELDS, row, strict=False))

        if organism:
            org_value = str(mapping.get("organism", ""))
            if organism.lower() not in org_value.lower():
                continue

        records.append(
            {
                "study_id": str(mapping.get("studyid", "")),
                "disease": str(mapping.get("disease", "")),
                "tissue_celltype": str(mapping.get("tissue_celltype", "")),
                "data_source": str(mapping.get("data_source", "")),
                "library_strategy": str(mapping.get("library_strategy", "")),
                "organism": str(mapping.get("organism", "")),
                "n_degs": _safe_int(mapping.get("diffall")),
            }
        )
    return records


def _fetch_disignatlas(
    query: str,
    mode: str,
    organism: str | None,
) -> tuple[bytes, dict[str, Any]]:
    """Fetch DisigNAtlas results and return (verbatim bytes, structured artifact).

    Returns (raw HTML bytes, artifact dict).
    """
    search_method = "gene_search" if mode == "gene" else "dataset_search"
    url = f"{DISIGNATLAS_BASE}/result"

    try:
        response = http.request(
            "GET",
            url,
            qps=qps_for_host("www.inbirg.com"),
            timeout=120.0,
            params={"search_method": search_method, "query_content": query},
        )
    except EndpointUnavailable as exc:
        raise Refusal(
            "DisigNAtlas endpoint is unreachable",
            detail=str(exc),
            remedy="The DisigNAtlas service at www.inbirg.com may be temporarily "
            "down. Run dde doctor to check endpoint status. GEO search may "
            "provide partial compensation for transcriptomics data.",
        ) from exc
    raw = response.content
    html = response.text

    rows = _parse_js_array(html)

    if not rows and len(html) > 500:
        _log.warning(
            "DisigNAtlas returned a non-empty HTML response (%d bytes) "
            "but zero result rows were parsed; the page structure may "
            "have changed — inspect the verbatim HTML artifact",
            len(html),
        )

    if mode == "gene":
        records = _build_gene_records(rows, organism)
        unique_diseases = {r["disease"] for r in records if r["disease"]}
        unique_tissues = {r["tissue_celltype"] for r in records if r["tissue_celltype"]}
        summary = {
            "n_records": len(records),
            "n_unique_diseases": len(unique_diseases),
            "n_unique_tissues": len(unique_tissues),
        }
    else:
        records = _build_disease_records(rows, organism)
        unique_tissues = {r["tissue_celltype"] for r in records if r["tissue_celltype"]}
        summary = {
            "n_records": len(records),
            "n_unique_tissues": len(unique_tissues),
        }

    artifact: dict[str, Any] = {
        "schema": "dde.disignatlas-search.v1",
        "query": {"text": query, "mode": mode, "organism": organism},
        "summary": summary,
        "records": records,
    }

    return raw, artifact


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def disignatlas() -> None:
    """Disease Signature Atlas (DisigNAtlas) transcriptomics search."""


@disignatlas.command("search")
@click.argument("query")
@click.option(
    "--mode",
    type=click.Choice(["gene", "disease"], case_sensitive=False),
    default="gene",
    help="Search mode: 'gene' for gene symbol search, 'disease' for dataset search (default: gene).",
)
@click.option(
    "--organism",
    default=None,
    help="Filter results by organism (case-insensitive substring, e.g. 'Homo sapiens').",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    mode: str,
    organism: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search DisigNAtlas for disease signatures.

    QUERY is a gene symbol (gene mode) or disease term (disease mode).

    Gene mode returns differential expression records (log2FC, adjusted
    p-value, regulation direction) across diseases and tissues.

    Disease mode returns study-level records with counts of differentially
    expressed genes.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    raw, artifact = _fetch_disignatlas(query, mode, organism)

    search_method = "gene_search" if mode == "gene" else "dataset_search"
    endpoint = (
        f"{DISIGNATLAS_BASE}/result?search_method={search_method}&query_content={query}"
    )

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=endpoint,
        parameters={
            "query": query,
            "mode": mode,
            "organism": organism,
        },
    )
    sidecar.note("source", "DisigNAtlas (www.inbirg.com/disignatlas)")
    sidecar.note("n_records", artifact["summary"]["n_records"])

    # Write verbatim HTML response.
    verbatim_path = target_dir / f"{slug}.disignatlas.html"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.disignatlas.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.disignatlas.meta.json")

    n_records = artifact["summary"]["n_records"]
    emit.data("query", query)
    emit.data("mode", mode)
    emit.data("n_records", n_records)
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"DisigNAtlas search ({mode}): {query!r}")
    emit.line(f"  {n_records} record(s) found")
    if mode == "gene":
        emit.line(
            f"  {artifact['summary']['n_unique_diseases']} unique disease(s), "
            f"{artifact['summary']['n_unique_tissues']} unique tissue(s)"
        )
    else:
        emit.line(f"  {artifact['summary']['n_unique_tissues']} unique tissue(s)")
    if artifact["records"]:
        for r in artifact["records"][:5]:
            if mode == "gene":
                fc = r.get("log2fc")
                fc_str = f"log2fc={fc:.2f}" if fc is not None else "log2fc=NA"
                emit.line(
                    f"  {r['study_id']}: {r['disease'][:40]} "
                    f"({r['regulation']}, {fc_str})"
                )
            else:
                emit.line(
                    f"  {r['study_id']}: {r['disease'][:40]} ({r['n_degs']} DEGs)"
                )
        if len(artifact["records"]) > 5:
            emit.line(f"  ... {len(artifact['records']) - 5} more in the artifact")
    emit.flush()


@disignatlas.command("analyze")
@click.argument("query")
@click.option(
    "--mode",
    type=click.Choice(["gene", "disease"], case_sensitive=False),
    default="gene",
    help="Search mode used in the original search (default: gene).",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    query: str,
    mode: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored DisigNAtlas search results. No network.

    Reads what ``search`` wrote and produces a summary analysis:
    disease distribution, regulation patterns, tissue distribution,
    and log2FC statistics (gene mode) or study counts (disease mode).
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    artifact_path = source_dir / f"{slug}.disignatlas.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no DisigNAtlas search artifact for {query!r} under {source_dir}",
            remedy=f"run `dde disignatlas search {query!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "DisigNAtlas search artifact")
    if artifact.get("schema") != "dde.disignatlas-search.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.disignatlas-search.v1, got {artifact.get('schema')!r}",
        )

    records = artifact.get("records", [])
    query_meta = artifact.get("query", {})
    search_mode = query_meta.get("mode", mode)

    relays: list[dict[str, str]] = []

    if search_mode == "gene":
        # Gene-mode analysis.
        disease_counts: Counter[str] = Counter()
        tissue_counts: Counter[str] = Counter()
        data_source_counts: Counter[str] = Counter()
        up_count = 0
        down_count = 0
        log2fc_values: list[float] = []

        for r in records:
            disease = r.get("disease", "")
            if disease:
                disease_counts[disease] += 1
            tissue = r.get("tissue_celltype", "")
            if tissue:
                tissue_counts[tissue] += 1
            ds = r.get("data_source", "")
            if ds:
                data_source_counts[ds] += 1
            reg = r.get("regulation", "")
            if reg == "up":
                up_count += 1
            elif reg == "down":
                down_count += 1
            fc = r.get("log2fc")
            if fc is not None:
                log2fc_values.append(fc)

        # Compute log2fc stats.
        log2fc_stats: dict[str, float | None] = {
            "min": None,
            "max": None,
            "median": None,
        }
        if log2fc_values:
            log2fc_stats = {
                "min": min(log2fc_values),
                "max": max(log2fc_values),
                "median": statistics.median(log2fc_values),
            }

        # Top signatures by |log2fc|.
        records_with_fc = [r for r in records if r.get("log2fc") is not None]
        records_with_fc.sort(key=lambda r: abs(r["log2fc"]), reverse=True)
        top_signatures = [
            {
                "study_id": r["study_id"],
                "disease": r["disease"],
                "log2fc": r["log2fc"],
                "padj": r.get("padj"),
                "regulation": r["regulation"],
            }
            for r in records_with_fc[:10]
        ]

        n = len(records)
        if n > 0:
            relays.append(
                provenance.relay(
                    "disignatlas.curated_signatures",
                    f"DisigNAtlas returned {n} disease signature record(s) for {query!r}; "
                    "these are curated differential expression results (log2FC, adjusted "
                    "p-value) from published studies. The signatures are pre-computed from "
                    "public data (GEO, ArrayExpress, TCGA) using standardized pipelines. "
                    "Individual study quality, sample sizes, and normalization methods "
                    "vary — treat as a discovery resource, not as primary evidence.",
                )
            )

        assessment: dict[str, Any] = {
            "outcome": "signatures_found" if records else "no_signatures",
            "query": query,
            "mode": search_mode,
            "n_records": len(records),
            "disease_distribution": [
                {"disease": d, "count": c} for d, c in disease_counts.most_common()
            ],
            "regulation_summary": {"up": up_count, "down": down_count},
            "tissue_distribution": [
                {"tissue": t, "count": c} for t, c in tissue_counts.most_common()
            ],
            "data_source_distribution": [
                {"data_source": ds, "count": c}
                for ds, c in data_source_counts.most_common()
            ],
            "log2fc_stats": log2fc_stats,
            "top_signatures": top_signatures,
        }

        metrics: dict[str, Any] = {
            "n_records": len(records),
            "n_unique_diseases": len(disease_counts),
            "n_unique_tissues": len(tissue_counts),
            "n_data_sources": len(data_source_counts),
            "up_regulated": up_count,
            "down_regulated": down_count,
        }

    else:
        # Disease-mode analysis.
        study_counts: Counter[str] = Counter()
        tissue_counts_d: Counter[str] = Counter()
        organism_counts: Counter[str] = Counter()

        for r in records:
            sid = r.get("study_id", "")
            if sid:
                study_counts[sid] += 1
            tissue = r.get("tissue_celltype", "")
            if tissue:
                tissue_counts_d[tissue] += 1
            org = r.get("organism", "")
            if org:
                organism_counts[org] += 1

        n = len(records)
        if n > 0:
            relays.append(
                provenance.relay(
                    "disignatlas.curated_signatures",
                    f"DisigNAtlas returned {n} disease signature record(s) for {query!r}; "
                    "these are curated differential expression results (log2FC, adjusted "
                    "p-value) from published studies. The signatures are pre-computed from "
                    "public data (GEO, ArrayExpress, TCGA) using standardized pipelines. "
                    "Individual study quality, sample sizes, and normalization methods "
                    "vary — treat as a discovery resource, not as primary evidence.",
                )
            )

        assessment = {
            "outcome": "signatures_found" if records else "no_signatures",
            "query": query,
            "mode": search_mode,
            "n_records": len(records),
            "n_studies": len(study_counts),
            "tissue_distribution": [
                {"tissue": t, "count": c} for t, c in tissue_counts_d.most_common()
            ],
            "organism_breakdown": dict(organism_counts),
        }

        metrics = {
            "n_records": len(records),
            "n_studies": len(study_counts),
            "n_unique_tissues": len(tissue_counts_d),
            "n_unique_organisms": len(organism_counts),
        }

    # Locate the meta file written by search for the source reference.
    meta_path = source_dir / f"{slug}.disignatlas.meta.json"
    source_ref = (
        state.project().relative(meta_path)
        if meta_path.is_file()
        else state.project().relative(artifact_path)
    )

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.disignatlas.analysis.json",
        source=source_ref,
        threshold_set="disignatlas-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"DisigNAtlas analysis ({search_mode}): {query!r}")
    emit.line(f"  Outcome: {assessment['outcome']}")
    emit.line(f"  {len(records)} record(s)")
    if search_mode == "gene":
        emit.line(
            f"  {metrics['n_unique_diseases']} disease(s), "
            f"  {metrics['n_unique_tissues']} tissue(s)"
        )
        emit.line(
            f"  Regulation: {assessment['regulation_summary']['up']} up, "
            f"{assessment['regulation_summary']['down']} down"
        )
        if assessment["log2fc_stats"]["median"] is not None:
            emit.line(
                f"  log2FC: min={assessment['log2fc_stats']['min']:.2f}, "
                f"max={assessment['log2fc_stats']['max']:.2f}, "
                f"median={assessment['log2fc_stats']['median']:.2f}"
            )
        if assessment["top_signatures"]:
            emit.line("  Top signatures by |log2FC|:")
            for sig in assessment["top_signatures"][:5]:
                emit.line(
                    f"    {sig['study_id']}: {sig['disease'][:30]} "
                    f"(log2fc={sig['log2fc']:.2f}, {sig['regulation']})"
                )
    else:
        emit.line(
            f"  {metrics.get('n_studies', 0)} study(ies), "
            f"{metrics['n_unique_tissues']} tissue(s)"
        )
        if assessment.get("organism_breakdown"):
            emit.line(f"  Organisms: {assessment['organism_breakdown']}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
