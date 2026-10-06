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

"""`dde scp` -- Broad Institute Single Cell Portal study search.

Queries the Broad Single Cell Portal (singlecell.broadinstitute.org) for
studies matching keyword terms.  The SCP hosts 1,042+ studies with ~85.9M
cells across diverse tissues and conditions.

Two phases:

  search   queries the SCP public REST API and writes structured study
           metadata to Layer 0 with a provenance sidecar.
  analyze  reads stored search results and produces a summary analysis:
           total studies, top studies by cell count.  No network.

A study matching a search term does not confirm expression of any specific
gene.  SCP search returns study-level metadata; gene expression values
require authenticated access to individual studies.  The relay
`scp.search_is_study_metadata` carries this caveat into the finding so a
report cannot present search results as evidence of gene expression.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus

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

TOOL = "scp"
ARTIFACT_CLASS = "single-cell"

SCP_BASE = "https://singlecell.broadinstitute.org/single_cell/api/v1"
SCP_PORTAL = "https://singlecell.broadinstitute.org/single_cell"


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "scp-search"


def _parse_studies(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured study records from the SCP search response.

    Gracefully handles missing fields; every record gets at least an
    accession.
    """
    studies: list[dict[str, Any]] = []
    raw_studies = data.get("studies", [])

    for study in raw_studies:
        accession = study.get("accession")
        if not accession:
            continue

        studies.append(
            {
                "accession": accession,
                "name": study.get("name"),
                "description": study.get("description"),
                "cell_count": study.get("cell_count"),
                "gene_count": study.get("gene_count"),
                "study_url": f"{SCP_PORTAL}/study/{accession}",
            }
        )

    return studies


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def scp() -> None:
    """Broad Institute Single Cell Portal study search."""


@scp.command("search")
@click.argument("query")
@click.option(
    "--max-results",
    default=20,
    type=click.IntRange(1, 100),
    help="Number of results to return (1-100, default 20).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search the Single Cell Portal for QUERY and write structured results.

    QUERY is one or more keywords (space-separated).  The SCP search
    endpoint matches against study names, descriptions, and metadata.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    # Fetch studies from SCP -- paginate if needed to reach max_results.
    all_studies: list[dict[str, Any]] = []
    page = 1
    total_found = 0

    while len(all_studies) < max_results:
        search_url = (
            f"{SCP_BASE}/search?type=study&terms={quote_plus(query)}&page={page}"
        )
        response_data = http.get_json(search_url, timeout=60.0)

        if not isinstance(response_data, dict):
            raise SchemaError(
                "SCP search response is not a JSON object",
                detail=f"got {type(response_data).__name__}",
            )

        # Extract total count from matching_results on first page.
        if page == 1:
            matching = response_data.get("matching_results")
            if isinstance(matching, dict):
                total_found = sum(v for v in matching.values() if isinstance(v, int))
            elif isinstance(matching, int):
                total_found = matching

        page_studies = _parse_studies(response_data)
        if not page_studies:
            break

        all_studies.extend(page_studies)
        page += 1

    # Trim to max_results.
    studies = all_studies[:max_results]

    # Build structured artifact.
    artifact: dict[str, Any] = {
        "schema": "dde.scp-search.v1",
        "query": {
            "terms": query,
            "max_results": max_results,
            "total_found": total_found,
        },
        "summary": {
            "n_results": len(studies),
            "total_found": total_found,
        },
        "results": studies,
    }

    artifact_path = target_dir / f"{slug}.scp.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    # Sidecar.
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=SCP_BASE,
        parameters={
            "query": query,
            "max_results": max_results,
        },
    )
    sidecar.note("source", "Broad Institute Single Cell Portal")
    sidecar.note("n_results", str(len(studies)))
    sidecar.note("total_found", str(total_found))

    sidecar.add_output(artifact_path)

    meta_path = sidecar.write(target_dir / f"{slug}.scp.meta.json")

    # Emit.
    emit.data("query", query)
    emit.data("total_found", total_found)
    emit.data("n_results", len(studies))
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"SCP search: {query!r}")
    emit.line(f"  {total_found} total found, {len(studies)} retrieved")
    if studies:
        for s in studies[:5]:
            cell_info = f" ({s['cell_count']} cells)" if s.get("cell_count") else ""
            emit.line(
                f"  {s['accession']}: {(s.get('name') or '(no name)')[:60]}{cell_info}"
            )
        if len(studies) > 5:
            emit.line(f"  ... {len(studies) - 5} more in the artifact")
    emit.flush()


@scp.command("analyze")
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
    """Summarise stored SCP search results for QUERY. No network.

    Reads what ``search`` wrote and produces a summary analysis: total
    studies found, top studies by cell count.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    # Locate the structured artifact.
    artifact_path = source_dir / f"{slug}.scp.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no SCP search artifact for {query!r} under {source_dir}",
            remedy=f"run `dde scp search {query!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "SCP search artifact")
    if artifact.get("schema") != "dde.scp-search.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.scp-search.v1, got {artifact.get('schema')!r}",
        )
    if not isinstance(artifact, dict) or "results" not in artifact:
        raise SchemaError(
            "SCP search artifact has no results array",
            detail=(
                f"keys: {list(artifact.keys()) if isinstance(artifact, dict) else type(artifact).__name__}"
            ),
        )

    results = artifact["results"]
    query_meta = artifact.get("query", {})
    total_found = query_meta.get("total_found", 0)

    outcome = "results_found" if results else "no_results"

    # Top studies by cell count (descending).
    studies_with_cells = [
        s
        for s in results
        if s.get("cell_count") and isinstance(s["cell_count"], (int, float))
    ]
    studies_with_cells.sort(key=lambda s: s["cell_count"], reverse=True)
    top_by_cells = studies_with_cells[:10]

    total_cells = sum(s["cell_count"] for s in studies_with_cells)

    relays: list[dict[str, str]] = []
    relays.append(
        provenance.relay(
            "scp.search_is_study_metadata",
            f"SCP search for {query!r} returned {len(results)} study "
            "records; these are study-level metadata, not gene expression "
            "data. A matching study does not confirm expression of any "
            "specific gene",
        )
    )

    assessment: dict[str, Any] = {
        "outcome": outcome,
        "query": query,
        "total_found": total_found,
        "n_retrieved": len(results),
        "total_cells_across_studies": total_cells,
        "top_studies_by_cell_count": [
            {
                "accession": s["accession"],
                "name": s.get("name"),
                "cell_count": s["cell_count"],
                "study_url": s.get("study_url"),
            }
            for s in top_by_cells
        ],
    }

    metrics: dict[str, Any] = {
        "total_found": total_found,
        "n_retrieved": len(results),
        "n_with_cell_count": len(studies_with_cells),
        "total_cells": total_cells,
        "max_cell_count": top_by_cells[0]["cell_count"] if top_by_cells else None,
    }

    # Locate the meta file written by search for the source reference.
    meta_path = source_dir / f"{slug}.scp.meta.json"
    source_ref = (
        state.project().relative(meta_path)
        if meta_path.is_file()
        else state.project().relative(artifact_path)
    )

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.scp.analysis.json",
        source=source_ref,
        threshold_set="scp-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"SCP search analysis: {query!r}")
    emit.line(f"  Outcome: {outcome}")
    emit.line(f"  {total_found} total found, {len(results)} retrieved")
    if top_by_cells:
        emit.line(f"  Total cells across studies: {total_cells:,}")
        emit.line("  Top studies by cell count:")
        for s in top_by_cells[:5]:
            emit.line(
                f"    {s['accession']}: {s['cell_count']:,} cells - "
                f"{(s.get('name') or '(no name)')[:50]}"
            )
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
