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

"""`dde cbioportal` -- cBioPortal cancer genomics data search.

Queries the cBioPortal public REST API for cancer genomics studies matching
a query term.  cBioPortal aggregates genomic data from large-scale cancer
studies including TCGA, AACR GENIE, and institutional cohorts.

Two phases:

  search   queries the cBioPortal studies endpoint and writes structured
           results to Layer 0 with a provenance sidecar.
  analyze  reads stored search results and summarises: total results,
           cancer types represented, and a verdict.  No network.

The search returns study-level metadata — study ID, name, description,
cancer type, and reference genome.  It does NOT return mutation data,
expression profiles, or patient-level clinical data.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
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

TOOL = "cbioportal"
ARTIFACT_CLASS = "expression"  # → raw/expression/

CBIOPORTAL_API = "https://www.cbioportal.org/api"


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "cbioportal-search"


def _matches_query(study: dict[str, Any], query: str, cancer_type: str | None) -> bool:
    """Check whether a study matches the free-text query and optional cancer type filter."""
    query_lower = query.lower()

    # Match query against name, description, and cancerTypeId
    name = (study.get("name") or "").lower()
    description = (study.get("description") or "").lower()
    cancer_type_id = (study.get("cancerTypeId") or "").lower()
    study_id = (study.get("studyId") or "").lower()

    text_match = (
        query_lower in name
        or query_lower in description
        or query_lower in cancer_type_id
        or query_lower in study_id
    )

    if not text_match:
        return False

    # Optional cancer type filter
    if cancer_type:
        ct_lower = cancer_type.lower()
        if ct_lower not in cancer_type_id and ct_lower not in name:
            return False

    return True


def _extract_study_record(study: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured record from a cBioPortal study object."""
    return {
        "study_id": study.get("studyId", ""),
        "name": study.get("name", ""),
        "description": (study.get("description") or "")[:1000],
        "cancer_type": study.get("cancerTypeId", ""),
        "reference_genome": study.get("referenceGenome", ""),
        "sample_count": study.get("allSampleCount", 0),
        "citation": study.get("citation") or None,
        "source": "cbioportal",
    }


def _fetch_studies(
    query: str,
    cancer_type: str | None,
    study_filter: str | None,
    max_results: int,
) -> tuple[bytes, dict[str, Any], bool]:
    """Fetch cBioPortal studies matching query.

    Returns (verbatim response bytes, structured artifact dict, truncated).
    """
    url = f"{CBIOPORTAL_API}/studies"
    raw_data = http.get_json(
        url,
        qps=qps_for_host("www.cbioportal.org"),
        timeout=60.0,
    )

    if not isinstance(raw_data, list):
        raise SchemaError(
            "cBioPortal studies endpoint did not return a JSON array",
            detail=f"got {type(raw_data).__name__}",
        )

    raw = json.dumps(raw_data, indent=2).encode("utf-8")

    # Filter by query and optional cancer type
    matched: list[dict[str, Any]] = []
    for study in raw_data:
        if not _matches_query(study, query, cancer_type):
            continue
        if study_filter:
            sid = (study.get("studyId") or "").lower()
            if study_filter.lower() not in sid:
                continue
        matched.append(_extract_study_record(study))

    # Cap results
    truncated = len(matched) > max_results
    matched = matched[:max_results]

    searched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    artifact: dict[str, Any] = {
        "schema": "dde.cbioportal-search.v1",
        "query": query,
        "searched_at": searched_at,
        "total_results": len(matched),
        "results": matched,
    }

    return raw, artifact, truncated


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def cbioportal() -> None:
    """cBioPortal cancer genomics study search."""


@cbioportal.command("search")
@click.argument("query")
@click.option(
    "--cancer-type",
    default=None,
    help="Filter studies by cancer type (case-insensitive substring match).",
)
@click.option(
    "--study",
    "study_filter",
    default=None,
    help="Filter studies by study ID substring.",
)
@click.option(
    "--max-results",
    default=25,
    type=click.IntRange(1, 200),
    help="Maximum number of results (1-200, default 25).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    cancer_type: str | None,
    study_filter: str | None,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search cBioPortal for cancer genomics studies matching QUERY.

    QUERY is a free-text search across study names, descriptions, and
    cancer type identifiers.  Use --cancer-type and --study to narrow
    results.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    raw, artifact, truncated = _fetch_studies(
        query,
        cancer_type,
        study_filter,
        max_results,
    )

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=f"{CBIOPORTAL_API}/studies",
        parameters={
            "query": query,
            "cancer_type": cancer_type,
            "study": study_filter,
            "max_results": max_results,
        },
    )
    sidecar.note("source", "cBioPortal (www.cbioportal.org)")
    sidecar.note("n_results", len(artifact["results"]))

    # Relay codes — fire conditionally
    if len(artifact["results"]) == 0:
        sidecar.warn(
            "The cBioPortal search returned no results. Consider broadening "
            "the query or checking alternative cancer genomics databases.",
            code="cbioportal.no_results",
        )

    if truncated:
        sidecar.warn(
            "Results were capped at the requested maximum. Additional "
            "matching studies may exist.",
            code="cbioportal.query_truncated",
        )

    # Write verbatim response
    verbatim_path = target_dir / f"{slug}.cbioportal.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact
    artifact_path = target_dir / f"{slug}.cbioportal-search.json"
    artifact_path.write_text(
        json.dumps(artifact, indent=2) + "\n",
        encoding="utf-8",
    )
    sidecar.add_output(artifact_path)

    # Write sidecar
    meta_path = sidecar.write(target_dir / f"{slug}.cbioportal.meta.json")

    n_results = len(artifact["results"])
    emit.data("query", query)
    emit.data("n_results", n_results)
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"cBioPortal search: {query!r}")
    emit.line(f"  {n_results} studies found")
    if artifact["results"]:
        for r in artifact["results"][:5]:
            emit.line(
                f"  {r['study_id']}: {r['name'][:60]} ({r['sample_count']} samples)"
            )
        if n_results > 5:
            emit.line(f"  ... {n_results - 5} more in the artifact")
    emit.flush()


@cbioportal.command("analyze")
@click.argument("artifact")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    artifact: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored cBioPortal search results from ARTIFACT. No network.

    Reads what ``search`` wrote and produces a summary: total results,
    cancer types represented, and a verdict.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Locate the structured artifact
    artifact_path = source_dir / artifact
    if not artifact_path.is_file():
        slug = _slugify(artifact)
        artifact_path = source_dir / f"{slug}.cbioportal-search.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no cBioPortal search artifact: {artifact} under {source_dir}",
            remedy=f"run `dde cbioportal search {artifact!r}` first",
        )

    data = provenance.read_json(artifact_path, "cBioPortal search artifact")
    if not isinstance(data, dict) or data.get("schema") != "dde.cbioportal-search.v1":
        raise SchemaError(
            "unexpected schema in cBioPortal search artifact",
            detail=f"expected dde.cbioportal-search.v1, got {data.get('schema')!r}",
        )

    results = data.get("results", [])
    query_str = data.get("query", "")

    # Compute metrics
    cancer_type_counts: Counter[str] = Counter()
    total_samples = 0
    for r in results:
        ct = r.get("cancer_type", "")
        if ct:
            cancer_type_counts[ct] += 1
        total_samples += r.get("sample_count", 0)

    outcome = "results-found" if results else "no-results"

    relays: list[dict[str, str]] = []

    assessment: dict[str, Any] = {
        "outcome": outcome,
        "query": query_str,
        "n_results": len(results),
        "total_samples": total_samples,
        "cancer_types": dict(cancer_type_counts.most_common()),
        "n_cancer_types": len(cancer_type_counts),
    }

    metrics: dict[str, Any] = {
        "n_results": len(results),
        "total_samples": total_samples,
        "n_cancer_types": len(cancer_type_counts),
    }

    analysis_path = provenance.write_analysis(
        target_dir
        / artifact_path.name.replace(
            ".cbioportal-search.json",
            ".cbioportal-search.analysis.json",
        ),
        source=state.project().relative(artifact_path),
        threshold_set="cbioportal-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"cBioPortal analysis: {query_str!r}")
    emit.line(f"  Outcome: {outcome}")
    emit.line(f"  {len(results)} studies, {total_samples} total samples")
    if cancer_type_counts:
        emit.line("  Cancer types:")
        for ct, c in cancer_type_counts.most_common(10):
            emit.line(f"    {ct}: {c}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
