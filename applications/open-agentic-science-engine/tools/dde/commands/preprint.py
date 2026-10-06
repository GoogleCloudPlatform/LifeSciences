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

"""`dde preprint` -- preprint search across preprint servers.

Searches preprint repositories (arXiv, bioRxiv) for papers matching a
query.  Preprints are not peer-reviewed; use this when looking for
recent, not-yet-peer-reviewed work.  For peer-reviewed literature, use
`dde pubmed`.

Two phases:

  search   queries a preprint server and writes structured results to
           Layer 0 with a sidecar.
  analyze  reads those results and produces a summary analysis: total
           results, source breakdown. No network.

The `--source` flag selects the preprint server: `arxiv` or `biorxiv`.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from ..core.errors import ArtifactError, SchemaError, UsageError
from ..core.paths import confine_path, sanitize_slug
from ..core.qps import qps_for_host

TOOL = "preprint"
ARTIFACT_CLASS = "literature"  # same as pubmed/litref

ARXIV_API_BASE = "https://export.arxiv.org/api/query"
BIORXIV_API_BASE = "https://api.biorxiv.org/details/biorxiv"

# Atom/XML namespaces used by the arXiv API
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"

# bioRxiv API returns up to 100 records per page.
_BIORXIV_PAGE_SIZE = 100
# Maximum number of pages to fetch when scanning bioRxiv (cap at 3,000 preprints).
_BIORXIV_MAX_PAGES = 30
# Default window (days) to search when querying bioRxiv by date range.
_BIORXIV_SEARCH_DAYS = 60

VALID_SOURCES = ("arxiv", "biorxiv")


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "preprint-search"


def _parse_arxiv_entries(xml_bytes: bytes) -> tuple[list[dict[str, Any]], int]:
    """Parse arXiv Atom XML response into structured paper records.

    Returns (results_list, total_results).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SchemaError(
            "arXiv API response is not valid XML", detail=str(exc)
        ) from exc

    # Total results from opensearch namespace
    total_results = 0
    opensearch_ns = "http://a9.com/-/spec/opensearch/1.1/"
    total_el = root.find(f"{{{opensearch_ns}}}totalResults")
    if total_el is not None and total_el.text:
        try:
            total_results = int(total_el.text)
        except ValueError:
            pass

    results: list[dict[str, Any]] = []

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        # arXiv ID — extract from the id URL
        id_el = entry.find(f"{{{ATOM_NS}}}id")
        if id_el is None or not id_el.text:
            continue
        raw_id = id_el.text.strip()
        # Extract the arXiv ID from URLs like http://arxiv.org/abs/2301.12345v1
        arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if "/abs/" in raw_id else raw_id
        # Strip version suffix for the canonical ID
        arxiv_id_base = re.sub(r"v\d+$", "", arxiv_id)

        # Title
        title_el = entry.find(f"{{{ATOM_NS}}}title")
        title = (
            title_el.text.strip() if title_el is not None and title_el.text else None
        )
        # Clean up whitespace in title
        if title:
            title = re.sub(r"\s+", " ", title)

        # Authors
        authors: list[str] = []
        for author_el in entry.findall(f"{{{ATOM_NS}}}author"):
            name_el = author_el.find(f"{{{ATOM_NS}}}name")
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # Abstract/summary
        summary_el = entry.find(f"{{{ATOM_NS}}}summary")
        abstract = None
        if summary_el is not None and summary_el.text:
            abstract = re.sub(r"\s+", " ", summary_el.text.strip())

        # Published date
        published_el = entry.find(f"{{{ATOM_NS}}}published")
        published = (
            published_el.text.strip()
            if published_el is not None and published_el.text
            else None
        )

        # Updated date
        updated_el = entry.find(f"{{{ATOM_NS}}}updated")
        updated = (
            updated_el.text.strip()
            if updated_el is not None and updated_el.text
            else None
        )

        # Categories
        categories: list[str] = []
        for cat_el in entry.findall(f"{{{ATOM_NS}}}category"):
            term = cat_el.get("term")
            if term:
                categories.append(term)

        # PDF link
        pdf_url = None
        for link_el in entry.findall(f"{{{ATOM_NS}}}link"):
            if link_el.get("title") == "pdf":
                pdf_url = link_el.get("href")
                break
        if pdf_url is None:
            # Construct from ID
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id_base}"

        # DOI (arXiv-specific namespace)
        doi = None
        doi_el = entry.find(f"{{{ARXIV_NS}}}doi")
        if doi_el is not None and doi_el.text:
            doi = doi_el.text.strip()

        results.append(
            {
                "id": arxiv_id_base,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "published": published,
                "updated": updated,
                "categories": categories,
                "pdf_url": pdf_url,
                "doi": doi,
                "source": "arxiv",
            }
        )

    return results, total_results


def _parse_biorxiv_collection(
    collection: list[dict[str, Any]],
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Convert raw bioRxiv API collection items into structured paper records.

    When *query* is provided, only items whose title or abstract contain
    every whitespace-delimited token (case-insensitive) are returned.
    """
    query_tokens: list[str] = []
    if query:
        query_tokens = [t.lower() for t in query.split() if t]

    results: list[dict[str, Any]] = []
    for item in collection:
        title = (item.get("title") or "").strip()
        abstract = (item.get("abstract") or "").strip()

        # Client-side keyword filtering — bioRxiv has no search endpoint.
        if query_tokens:
            haystack = f"{title} {abstract}".lower()
            if not all(tok in haystack for tok in query_tokens):
                continue

        # Clean up whitespace
        if title:
            title = re.sub(r"\s+", " ", title)
        if abstract:
            abstract = re.sub(r"\s+", " ", abstract)

        # Authors — bioRxiv returns a single comma-separated string.
        raw_authors = item.get("authors") or ""
        authors: list[str] = [a.strip() for a in raw_authors.split(";") if a.strip()]
        if not authors and raw_authors:
            # Fallback: some records use comma separation.
            authors = [a.strip() for a in raw_authors.split(",") if a.strip()]

        doi = (item.get("doi") or "").strip() or None
        date = (item.get("date") or "").strip() or None
        version = item.get("version")
        category = (item.get("category") or "").strip() or None

        # Build a PDF URL from the DOI when available.
        pdf_url = (
            f"https://www.biorxiv.org/content/{doi}v{version}.full.pdf"
            if doi and version
            else None
        )

        results.append(
            {
                "id": doi or "",
                "title": title or None,
                "authors": authors,
                "abstract": abstract or None,
                "published": date,
                "updated": date,  # bioRxiv does not distinguish published/updated per version
                "categories": [category] if category else [],
                "pdf_url": pdf_url,
                "doi": doi,
                "source": "biorxiv",
            }
        )

    return results


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def preprint() -> None:
    """Preprint search across preprint servers."""


@preprint.command("search")
@click.argument("query")
@click.option(
    "--source",
    type=click.Choice(VALID_SOURCES, case_sensitive=False),
    required=True,
    help="Preprint source to search (arxiv, biorxiv).",
)
@click.option(
    "--max-results",
    default=20,
    type=click.IntRange(1, 200),
    help="Number of results to return (1-200, default 20).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    source: str,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search preprints for QUERY and write structured results.

    QUERY is a free-text search string.  --source selects the preprint
    server ('arxiv' or 'biorxiv').
    """
    source = source.lower()
    if source not in VALID_SOURCES:
        raise UsageError(
            f"unknown preprint source: {source!r}",
            remedy=f"valid sources: {', '.join(VALID_SOURCES)}",
        )

    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    if source == "arxiv":
        _search_arxiv(state, emit, target_dir, slug, query, max_results)
    elif source == "biorxiv":
        _search_biorxiv(state, emit, target_dir, slug, query, max_results)


def _search_arxiv(
    state: AppState,
    emit: Any,
    target_dir: Any,
    slug: str,
    query: str,
    max_results: int,
) -> None:
    """Execute arXiv search and write artifacts."""
    url = (
        f"{ARXIV_API_BASE}"
        f"?search_query={quote_plus(query)}"
        f"&max_results={max_results}"
        f"&sortBy=relevance"
        f"&sortOrder=descending"
    )

    response = http.request(
        "GET",
        url,
        qps=qps_for_host("export.arxiv.org"),
        timeout=60.0,
    )
    xml_bytes = response.content

    # Save verbatim API response
    raw_path = target_dir / f"{slug}.arxiv-response.xml"
    raw_path.write_bytes(xml_bytes)

    # Parse the Atom XML
    results, total_results = _parse_arxiv_entries(xml_bytes)

    # Build structured artifact
    searched_at = datetime.now(timezone.utc).isoformat()
    artifact = {
        "schema": "dde.preprint-search.v1",
        "source": "arxiv",
        "query": query,
        "searched_at": searched_at,
        "total_results": total_results,
        "results": results,
    }

    artifact_path = target_dir / f"{slug}.preprint-search.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    # Sidecar
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint="arxiv",
        parameters={
            "query": query,
            "max_results": max_results,
            "source": "arxiv",
        },
    )
    sidecar.note("source", "arXiv (Cornell University)")
    sidecar.note("licence", "arXiv API Terms of Use")
    sidecar.note("n_results", str(len(results)))
    sidecar.note("total_results", str(total_results))

    sidecar.add_output(raw_path)
    sidecar.add_output(artifact_path)

    # Relay codes — fire conditionally
    if len(results) == 0:
        sidecar.warn(
            f"arXiv search for {query!r} returned no results",
            code="preprint.no_results",
        )

    if len(results) >= max_results and total_results > max_results:
        sidecar.warn(
            f"arXiv search returned {total_results} total results but only "
            f"{max_results} were retrieved; additional matching preprints may exist",
            code="preprint.query_truncated",
        )

    meta_path = sidecar.write(target_dir / f"{slug}.meta.json")

    # Emit
    emit.data("query", query)
    emit.data("source", "arxiv")
    emit.data("total_results", total_results)
    emit.data("n_results", len(results))
    emit.path(raw_path, role="arxiv-response")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"Preprint search (arXiv): {query!r}")
    emit.line(f"  {total_results} total found, {len(results)} retrieved")
    if results:
        for r in results[:5]:
            emit.line(f"  {r['id']}: {(r.get('title') or '(no title)')[:70]}")
        if len(results) > 5:
            emit.line(f"  ... {len(results) - 5} more in the artifact")
    emit.flush()


def _search_biorxiv(
    state: AppState,
    emit: Any,
    target_dir: Any,
    slug: str,
    query: str,
    max_results: int,
) -> None:
    """Execute bioRxiv search and write artifacts.

    bioRxiv has no keyword-search endpoint, so we fetch recent preprints
    via the date-range details API and filter client-side by keyword match
    in title and abstract.
    """
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=_BIORXIV_SEARCH_DAYS)
    interval = f"{start_date.isoformat()}/{today.isoformat()}"

    qps = qps_for_host("api.biorxiv.org")
    results: list[dict[str, Any]] = []
    cursor = 0
    total_scanned = 0
    scan_capped = False
    raw_collections: list[dict[str, Any]] = []

    while len(results) < max_results:
        url = f"{BIORXIV_API_BASE}/{interval}/{cursor}/json"
        data = http.get_json(url, qps=qps, timeout=60.0)

        collection = data.get("collection", [])
        if not collection:
            break

        raw_collections.extend(collection)
        total_scanned += len(collection)

        matched = _parse_biorxiv_collection(collection, query=query)
        results.extend(matched)

        # The API returns up to 100 per page.  If fewer come back, we
        # have exhausted the date range.
        if len(collection) < _BIORXIV_PAGE_SIZE:
            break

        cursor += _BIORXIV_PAGE_SIZE

        if cursor // _BIORXIV_PAGE_SIZE >= _BIORXIV_MAX_PAGES:
            scan_capped = True
            break

    # Trim to requested size.
    results = results[:max_results]

    # Save verbatim API response (aggregated collection items).
    raw_path = target_dir / f"{slug}.biorxiv-response.json"
    raw_path.write_text(json.dumps(raw_collections, indent=2) + "\n", encoding="utf-8")

    # Build structured artifact
    searched_at = datetime.now(timezone.utc).isoformat()
    artifact = {
        "schema": "dde.preprint-search.v1",
        "source": "biorxiv",
        "query": query,
        "searched_at": searched_at,
        "total_results": len(results),
        "results": results,
    }

    artifact_path = target_dir / f"{slug}.preprint-search.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    # Sidecar
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint="biorxiv",
        parameters={
            "query": query,
            "max_results": max_results,
            "source": "biorxiv",
            "search_days": _BIORXIV_SEARCH_DAYS,
            "interval": interval,
        },
    )
    sidecar.note("source", "bioRxiv (Cold Spring Harbor Laboratory)")
    sidecar.note("licence", "bioRxiv API Terms and Conditions")
    sidecar.note("n_results", str(len(results)))
    sidecar.note("total_scanned", str(total_scanned))
    sidecar.note(
        "search_method",
        "date-range fetch with client-side keyword filtering "
        "(bioRxiv has no keyword search endpoint)",
    )
    if scan_capped:
        sidecar.note("biorxiv_scan_capped", True)

    sidecar.add_output(raw_path)
    sidecar.add_output(artifact_path)

    # Relay codes
    if len(results) == 0:
        sidecar.warn(
            f"bioRxiv search for {query!r} returned no results",
            code="preprint.no_results",
        )

    if len(results) >= max_results and total_scanned > len(results):
        sidecar.warn(
            f"bioRxiv search scanned {total_scanned} preprints and found "
            f"{len(results)} matches (capped at {max_results}); additional "
            f"matching preprints may exist",
            code="preprint.query_truncated",
        )

    meta_path = sidecar.write(target_dir / f"{slug}.meta.json")

    # Emit
    emit.data("query", query)
    emit.data("source", "biorxiv")
    emit.data("total_results", len(results))
    emit.data("n_results", len(results))
    emit.path(raw_path, role="biorxiv-response")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"Preprint search (bioRxiv): {query!r}")
    emit.line(f"  {len(results)} matching preprints found ({total_scanned} scanned)")
    if results:
        for r in results[:5]:
            emit.line(f"  {r['id']}: {(r.get('title') or '(no title)')[:70]}")
        if len(results) > 5:
            emit.line(f"  ... {len(results) - 5} more in the artifact")
    emit.flush()


@preprint.command("analyze")
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
    """Summarise stored preprint search results from ARTIFACT. No network.

    Reads what `search` wrote and produces a summary analysis: total
    results, source breakdown.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Locate the structured artifact — confine to source_dir.
    artifact_path = confine_path(source_dir, Path(artifact))
    if artifact_path is None:
        raise UsageError(
            f"artifact path escapes source directory: {artifact}",
            remedy="provide a filename within the artifact directory",
        )
    if not artifact_path.is_file():
        # Try with .preprint-search.json suffix
        slug = sanitize_slug(_slugify(artifact))
        artifact_path = source_dir / f"{slug}.preprint-search.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no preprint search artifact: {artifact} under {source_dir}",
            remedy="run `dde preprint search --source <SOURCE> <QUERY>` first",
        )

    data = provenance.read_json(artifact_path, "preprint search artifact")
    if not isinstance(data, dict) or "results" not in data:
        raise SchemaError(
            "preprint search artifact has no results array",
            detail=f"keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}",
        )

    results = data["results"]
    total_results = data.get("total_results", 0)
    query_str = data.get("query", "")
    source_name = data.get("source", "unknown")

    # Compute metrics
    source_counts: dict[str, int] = {}
    for r in results:
        s = r.get("source", "unknown")
        source_counts[s] = source_counts.get(s, 0) + 1

    outcome = "results-found" if results else "no-results"

    relays: list[dict[str, str]] = []
    # No additional relays in analyze — relays fire in search phase

    assessment = {
        "outcome": outcome,
        "query": query_str,
        "source": source_name,
        "total_results": total_results,
        "n_retrieved": len(results),
        "source_breakdown": source_counts,
    }

    metrics = {
        "total_results": total_results,
        "n_retrieved": len(results),
        "source_breakdown": source_counts,
    }

    # Locate the meta file written by search for the source reference
    meta_path = artifact_path.parent / artifact_path.name.replace(
        ".preprint-search.json", ".meta.json"
    )
    source_ref = (
        state.project().relative(meta_path)
        if meta_path.is_file()
        else state.project().relative(artifact_path)
    )

    analysis_path = provenance.write_analysis(
        target_dir
        / artifact_path.name.replace(
            ".preprint-search.json", ".preprint-search.analysis.json"
        ),
        source=source_ref,
        threshold_set="preprint-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"Preprint search analysis: {query_str!r}")
    emit.line(f"  Outcome: {outcome}")
    emit.line(f"  {total_results} total found, {len(results)} retrieved")
    if source_counts:
        emit.line("  Source breakdown:")
        for s, c in source_counts.items():
            emit.line(f"    {s}: {c}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
