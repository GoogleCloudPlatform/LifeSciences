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

"""`dde disco` — DISCO single-cell immune dataset search.

Queries the DISCO database (immunesinglecell.org / disco.bii.a-star.edu.sg)
for single-cell datasets matching tissue, disease, or other metadata
criteria.  DISCO hosts curated immune cell atlas data across tissues and
conditions.

Two phases:

  search   queries the DISCO REST API and filters by tissue, disease,
           species, or free-text query; writes structured results to
           Layer 0 with a sidecar.
  analyze  reads stored search results and produces a summary analysis:
           tissue distribution, disease distribution, platform breakdown,
           and cell count statistics.  No network.

The search returns sample-level metadata — tissue annotations, disease
labels, platform, and cell counts.  It does NOT return gene expression
values.  A sample listed as containing a tissue does not confirm that a
specific gene is expressed there; confirming expression requires
downloading the expression data (H5 files) from DISCO.
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
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.qps import qps_for_host

TOOL = "disco"
ARTIFACT_CLASS = "single-cell"

DISCO_API = "https://immunesinglecell.com/disco_v3_api"


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "disco-search"


def _extract_sample_record(sample: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured record from a raw DISCO sample object."""
    return {
        "sample_id": sample.get("sample_id", ""),
        "project": sample.get("project", ""),
        "tissue": sample.get("tissue", ""),
        "disease": sample.get("disease", ""),
        "platform": sample.get("platform", ""),
        "cell_number": sample.get("cell_number", 0),
        "species": sample.get("species", ""),
        "cell_type_annotation": sample.get("cell_type_annotation", ""),
    }


def _fetch_samples(
    query: str | None,
    tissue: str | None,
    disease: str | None,
    species: str,
    max_results: int,
) -> tuple[bytes, dict[str, Any]]:
    """Fetch and filter DISCO samples.

    Returns (verbatim response bytes, structured artifact dict).
    """
    # Build the filter body for the DISCO API.
    body: dict[str, Any] = {"species": [species]}
    if tissue:
        body["tissue"] = [tissue]
    if disease:
        body["disease"] = [disease]

    url = f"{DISCO_API}/repository/get_all_metadata"
    resp = http.request(
        "POST", url, json=body, qps=qps_for_host("immunesinglecell.com"), timeout=120.0
    )

    try:
        raw_data = resp.json()
    except ValueError as exc:
        raise SchemaError(
            "DISCO metadata endpoint did not return valid JSON",
            detail=str(exc),
        ) from exc

    raw = resp.content

    if not isinstance(raw_data, list):
        raise SchemaError(
            "DISCO metadata endpoint did not return a JSON array",
            detail=f"got {type(raw_data).__name__}",
        )

    # Apply free-text query filter across project names and sample fields.
    query_lower = query.lower() if query else None
    matched_samples: list[dict[str, Any]] = []

    for sample in raw_data:
        if query_lower:
            project = (sample.get("project") or "").lower()
            sample_id = (sample.get("sample_id") or "").lower()
            tissue_val = (sample.get("tissue") or "").lower()
            disease_val = (sample.get("disease") or "").lower()
            if (
                query_lower not in project
                and query_lower not in sample_id
                and query_lower not in tissue_val
                and query_lower not in disease_val
            ):
                continue

        matched_samples.append(_extract_sample_record(sample))

    # Trim to max_results.
    samples = matched_samples[:max_results]

    total_cells = sum(
        s.get("cell_number", 0)
        for s in samples
        if isinstance(s.get("cell_number"), (int, float))
    )

    artifact: dict[str, Any] = {
        "schema": "dde.disco-search.v1",
        "query": {
            "text": query or "",
            "tissue": tissue or "",
            "disease": disease or "",
            "species": species,
        },
        "summary": {
            "n_samples": len(samples),
            "total_cells": total_cells,
        },
        "samples": samples,
    }

    return raw, artifact


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def disco() -> None:
    """DISCO single-cell immune dataset search."""


@disco.command("search")
@click.argument("query", required=False, default=None)
@click.option(
    "--tissue",
    default=None,
    help="Filter samples by tissue (case-insensitive).",
)
@click.option(
    "--disease",
    default=None,
    help="Filter samples by disease condition (case-insensitive).",
)
@click.option(
    "--species",
    default="Human",
    help="Filter samples by species (default: 'Human').",
)
@click.option(
    "--max-results",
    default=50,
    type=click.IntRange(1, 500),
    help="Maximum number of sample results to return (1-500, default 50).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str | None,
    tissue: str | None,
    disease: str | None,
    species: str,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search DISCO for single-cell datasets.

    QUERY is optional free-text search matched against project names
    and sample descriptions.  Use --tissue, --disease, and --species
    to filter samples by metadata fields.

    At least one of QUERY or a filter option must be provided.
    """
    if not query and not tissue and not disease:
        raise Refusal(
            "no search criteria provided",
            remedy="provide a QUERY argument and/or filter options "
            "(--tissue, --disease)",
        )

    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Build slug from the most specific criterion.
    slug_source = query or tissue or disease or "disco"
    slug = _slugify(slug_source)

    raw, artifact = _fetch_samples(query, tissue, disease, species, max_results)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=f"{DISCO_API}/repository/get_all_metadata",
        parameters={
            "query": query or "",
            "tissue": tissue or "",
            "disease": disease or "",
            "species": species,
            "max_results": max_results,
        },
    )
    sidecar.note("source", "DISCO (immunesinglecell.org)")
    sidecar.note("n_samples", artifact["summary"]["n_samples"])

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.disco.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.disco.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.disco.meta.json")

    emit.data("query", query or "")
    emit.data("n_samples", artifact["summary"]["n_samples"])
    emit.data("total_cells", artifact["summary"]["total_cells"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"DISCO search: {slug_source!r}")
    emit.line(
        f"  {artifact['summary']['n_samples']} samples, "
        f"{artifact['summary']['total_cells']:,} cells"
    )
    if artifact["samples"]:
        for s in artifact["samples"][:5]:
            cell_info = f" ({s['cell_number']} cells)" if s.get("cell_number") else ""
            emit.line(
                f"  {s['sample_id']}: {s.get('project', '(no project)')[:60]}{cell_info}"
            )
        if len(artifact["samples"]) > 5:
            emit.line(f"  ... {len(artifact['samples']) - 5} more in the artifact")
    emit.flush()


@disco.command("analyze")
@click.argument("query", required=False, default=None)
@click.option(
    "--tissue",
    default=None,
    help="Tissue filter used in the original search (for slug matching).",
)
@click.option(
    "--disease",
    default=None,
    help="Disease filter used in the original search (for slug matching).",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    query: str | None,
    tissue: str | None,
    disease: str | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored DISCO search results. No network.

    Reads what ``search`` wrote and produces a summary: total samples,
    tissue distribution, disease distribution, platform breakdown, and
    cell count statistics.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug_source = query or tissue or disease or "disco"
    slug = _slugify(slug_source)

    artifact_path = source_dir / f"{slug}.disco.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no DISCO search artifact for {slug_source!r} under {source_dir}",
            remedy=f"run `dde disco search {slug_source!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "DISCO search artifact")
    if artifact.get("schema") != "dde.disco-search.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.disco-search.v1, got {artifact.get('schema')!r}",
        )

    samples = artifact.get("samples", [])
    query_meta = artifact.get("query", {})

    # Compute distributions across all samples.
    tissue_counts: Counter[str] = Counter()
    disease_counts: Counter[str] = Counter()
    platform_counts: Counter[str] = Counter()
    project_counts: Counter[str] = Counter()
    total_cells = 0
    min_cells: int | None = None
    max_cells: int | None = None

    for sample in samples:
        t = sample.get("tissue", "")
        if t:
            tissue_counts[t] += 1
        d = sample.get("disease", "")
        if d:
            disease_counts[d] += 1
        p = sample.get("platform", "")
        if p:
            platform_counts[p] += 1
        proj = sample.get("project", "")
        if proj:
            project_counts[proj] += 1
        cn = sample.get("cell_number", 0)
        if isinstance(cn, (int, float)) and cn > 0:
            total_cells += cn
            if min_cells is None or cn < min_cells:
                min_cells = cn
            if max_cells is None or cn > max_cells:
                max_cells = cn

    n = len(samples)

    relays: list[dict[str, str]] = []
    relays.append(
        provenance.relay(
            "disco.search_is_sample_metadata",
            f"DISCO search returned metadata for {n} sample(s); "
            "these are sample-level descriptors (tissue, disease, cell count). "
            "Expression values and cell type markers are not included in search "
            "results. Confirming gene expression or cell type enrichment requires "
            "downloading the expression data (H5 files) from DISCO.",
        )
    )

    assessment: dict[str, Any] = {
        "outcome": "samples_found" if n else "no_samples",
        "query_text": query_meta.get("text", ""),
        "query_tissue": query_meta.get("tissue", ""),
        "query_disease": query_meta.get("disease", ""),
        "query_species": query_meta.get("species", ""),
        "n_samples": n,
        "tissue_distribution": [
            {"tissue": t, "count": c} for t, c in tissue_counts.most_common()
        ],
        "disease_distribution": [
            {"disease": d, "count": c} for d, c in disease_counts.most_common()
        ],
        "platform_distribution": [
            {"platform": p, "count": c} for p, c in platform_counts.most_common()
        ],
        "project_distribution": [
            {"project": proj, "count": c} for proj, c in project_counts.most_common()
        ],
        "cell_count_stats": {
            "min": min_cells,
            "max": max_cells,
            "total": total_cells,
        },
    }

    metrics: dict[str, Any] = {
        "n_samples": n,
        "total_cells": total_cells,
        "n_unique_tissues": len(tissue_counts),
        "n_unique_diseases": len(disease_counts),
        "n_unique_platforms": len(platform_counts),
        "n_unique_projects": len(project_counts),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.disco.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="disco-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"DISCO analysis: {slug_source!r}")
    emit.line(f"  Outcome: {assessment['outcome']}")
    emit.line(f"  {n} samples, {total_cells:,} total cells")
    if tissue_counts:
        emit.line("  Tissue distribution:")
        for t, c in tissue_counts.most_common(5):
            emit.line(f"    {t}: {c}")
    if disease_counts:
        emit.line("  Disease distribution:")
        for d, c in disease_counts.most_common(5):
            emit.line(f"    {d}: {c}")
    if platform_counts:
        emit.line("  Platform distribution:")
        for p, c in platform_counts.most_common(5):
            emit.line(f"    {p}: {c}")
    if min_cells is not None and max_cells is not None:
        emit.line(
            f"  Cell counts: min={min_cells:,}, max={max_cells:,}, total={total_cells:,}"
        )
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
