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

"""dde patent -- patent landscape and FTO assessment.

NOTE: Patent search currently uses the Google Patents XHR endpoint,
which is undocumented and may change without notice.  An official
integration with EPO Open Patent Services (OPS) is planned but
requires OAuth credentials not yet available.  Treat results from
this source as preliminary.

Two phases:

  search          one query -> patent results from Google Patents,
                  written verbatim to Layer 0 with a sidecar.
  analyze         reads stored search results and classifies the
                  patent landscape for freedom-to-operate assessment.
                  No network.

Google Patents XHR is an undocumented internal endpoint that powers
the Google Patents web UI.  It returns JSON, requires no
authentication, and supports the same query syntax as the web search
(free text, boolean, CPC codes, assignee filters).  Rate limiting is
aggressive — HTTP 503 after approximately five rapid requests — so
queries are paced at 0.5 QPS.
"""

from __future__ import annotations

import datetime
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
from ..core.qps import qps_for_host

GOOGLE_PATENTS_API = "https://patents.google.com/xhr/query"
TOOL = "dde.patent"
ARTIFACT_CLASS = "ip"

# Maximum pages to fetch (conservative given rate limits).
_MAX_PAGES = 3
# Maximum patents to retain across all pages.
_MAX_PATENTS = 300


def _fetch_google_patents(query: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch patent results from the Google Patents XHR endpoint.

    NOTE: This uses an undocumented XHR endpoint that powers the Google
    Patents web UI.  It is not an official API — it may change or become
    unavailable without notice.  Rate limiting is aggressive (~5 requests
    before HTTP 503).

    Handles pagination up to ``_MAX_PAGES`` pages, capped at
    ``_MAX_PATENTS`` total patents.

    Returns (verbatim response bytes, structured artifact dict).
    """
    all_patents: list[dict[str, Any]] = []
    raw_pages: list[bytes] = []
    total_results: int = 0

    for page in range(_MAX_PAGES):
        # Build the URL — page parameter is embedded in the url= value.
        encoded_query = quote(query, safe="")
        if page == 0:
            url = f"{GOOGLE_PATENTS_API}?url=q%3D{encoded_query}&exp="
        else:
            url = f"{GOOGLE_PATENTS_API}?url=q%3D{encoded_query}%26page%3D{page}&exp="

        response = http.request(
            "GET",
            url,
            qps=qps_for_host("patents.google.com"),
            timeout=60.0,
            tolerate_status=(503,),
        )

        if response.status_code == 503:
            if not all_patents:
                raise Refusal(
                    "Google Patents rate limit exceeded",
                    detail="HTTP 503 on first request",
                    remedy=(
                        "wait a few minutes and retry; the Google Patents "
                        "XHR endpoint is undocumented and aggressively "
                        "rate-limited (~5 requests before 503)"
                    ),
                )
            # Got some results before hitting the limit — use what we have.
            break

        raw_pages.append(response.content)

        try:
            payload = json.loads(response.content.decode("utf-8"))
        except Exception as exc:
            raise SchemaError(
                "Google Patents did not return JSON", detail=str(exc)
            ) from exc

        # Extract patents from the response structure.
        results = payload.get("results") or {}
        clusters = results.get("cluster") or []

        # Capture total result count from the first page.
        if page == 0:
            total_results = results.get("total_num_results", 0)

        page_patents: list[dict[str, Any]] = []
        for cluster in clusters:
            for result in cluster.get("result", []):
                patent_data = result.get("patent")
                if not patent_data:
                    continue
                page_patents.append(_extract_patent(patent_data))

        if not page_patents:
            break

        for p in page_patents:
            if len(all_patents) >= _MAX_PATENTS:
                break
            all_patents.append(p)

        if len(all_patents) >= _MAX_PATENTS:
            break

    # Build verbatim bytes — combine pages if paginated.
    if len(raw_pages) == 1:
        raw = raw_pages[0]
    elif raw_pages:
        combined = {
            "pages": [json.loads(p.decode("utf-8")) for p in raw_pages],
            "total_pages": len(raw_pages),
        }
        raw = json.dumps(combined, indent=2).encode("utf-8")
    else:
        raw = b"{}"

    # Build summary.
    assignee_counts: dict[str, int] = {}
    year_counts: dict[str, int] = {}
    dates: list[str] = []
    for p in all_patents:
        assignee = p.get("assignee", "") or "Unknown"
        assignee_counts[assignee] = assignee_counts.get(assignee, 0) + 1

        priority_date = p.get("priority_date", "")
        if priority_date and len(priority_date) >= 4:
            year = priority_date[:4]
            year_counts[year] = year_counts.get(year, 0) + 1

        for date_field in ("priority_date", "filing_date", "publication_date"):
            d = p.get(date_field, "")
            if d:
                dates.append(d)

    # Top 10 assignees by count.
    top_assignees = dict(
        sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    )

    sorted_dates = sorted(d for d in dates if d)
    date_range = {
        "earliest": sorted_dates[0] if sorted_dates else None,
        "latest": sorted_dates[-1] if sorted_dates else None,
    }

    artifact: dict[str, Any] = {
        "schema": "dde.patent.v1",
        "query": {"term": query, "source": "google-patents"},
        "summary": {
            "n_patents": len(all_patents),
            "total_results": total_results,
            "by_assignee": top_assignees,
            "by_year": dict(sorted(year_counts.items())),
            "date_range": date_range,
        },
        "patents": all_patents,
    }

    return raw, artifact


def _extract_patent(patent_data: dict[str, Any]) -> dict[str, Any]:
    """Extract structured fields from a single Google Patents result."""
    return {
        "publication_number": patent_data.get("publication_number", ""),
        "title": patent_data.get("title", ""),
        "snippet": patent_data.get("snippet", ""),
        "assignee": patent_data.get("assignee", ""),
        "priority_date": patent_data.get("priority_date", ""),
        "filing_date": patent_data.get("filing_date", ""),
        "publication_date": patent_data.get("publication_date", ""),
        "language": patent_data.get("language", ""),
    }


@click.group()
def patent() -> None:
    """Patent landscape search and FTO assessment."""


# Verb aliases — see docs/tool-design-guidance.md and issue #92.
#
#   fetch  = retrieve the record for a known identifier (gene, CID, …)
#   search = query and get back a result set
#
# Both verbs are accepted as aliases for discoverability.  The primary
# verb for this group is ``search`` (Google Patents returns a result set
# for a text query); ``fetch`` is the alias.


@patent.command("search")
@click.argument("query")
@click.option(
    "--source",
    type=click.Choice(["google-patents"]),
    default="google-patents",
    help="Which patent database to query (default: google-patents).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    source: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search patent databases for QUERY.

    NOTE: The Google Patents search uses an undocumented XHR endpoint.
    This endpoint is not an official API -- it may change or become
    unavailable without notice. Rate limiting is aggressive (~5 requests
    before 503). For official patent data, use the EPO Open Patent
    Services API (requires registration at developers.epo.org).
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = re.sub(r"[^a-z0-9._-]+", "-", query.lower()).strip("-")[:80] or "query"

    raw, artifact = _fetch_google_patents(query)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=GOOGLE_PATENTS_API,
        parameters={
            "query_term": query,
            "source": source,
        },
    )
    sidecar.note("source_db", "google-patents")
    sidecar.note("n_patents", artifact["summary"]["n_patents"])
    sidecar.note("total_results", artifact["summary"]["total_results"])

    if artifact["summary"]["n_patents"] >= _MAX_PATENTS:
        sidecar.warn(
            f"Results capped at {_MAX_PATENTS} patents; the total result set "
            f"contains {artifact['summary']['total_results']} patents. "
            "Refine the query for a more focused search."
        )

    sidecar.warn(
        "Google Patents XHR is an undocumented endpoint. Results should "
        "be treated as preliminary. For authoritative patent data, use "
        "the EPO Open Patent Services API (developers.epo.org)."
    )

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.patent-{source}.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.patent-{source}.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.patent-{source}.meta.json")

    emit.data("query", query)
    emit.data("source", source)
    emit.data("n_patents", artifact["summary"]["n_patents"])
    emit.data("total_results", artifact["summary"]["total_results"])
    emit.data("by_assignee", artifact["summary"]["by_assignee"])
    emit.data("by_year", artifact["summary"]["by_year"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@patent.command("analyze")
@click.argument("query_term")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    query_term: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Assess patent landscape from stored search results. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = re.sub(r"[^a-z0-9._-]+", "-", query_term.lower()).strip("-")[:80] or "query"
    artifact_path = source_dir / f"{slug}.patent-google-patents.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no patent search artifact for {query_term!r}",
            remedy=f"run `dde patent search {query_term}` first",
        )

    artifact = provenance.read_json(artifact_path, "patent artifact")
    if artifact.get("schema") != "dde.patent.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.patent.v1, got {artifact.get('schema')!r}",
        )

    patents = artifact.get("patents", [])

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    metrics, assessment = _analyze_patents(query_term, patents, add_relay)

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.patent-google-patents.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="patent-google-patents",
        thresholds_applied={
            "recent_years": 5,
            "high_risk_threshold": 10,
        },
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("relays", relays)

    verdict = assessment["verdict"]
    emit.line(
        f"{query_term!r} -> {verdict.upper()} "
        f"({metrics['total_patents']} patents, "
        f"{metrics['recent_patents']} recent)"
    )
    top_assignees = assessment.get("top_assignees", [])
    if top_assignees:
        emit.line(f"top assignees: {', '.join(top_assignees[:5])}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


def _analyze_patents(
    query_term: str,
    patents: list[dict[str, Any]],
    add_relay: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Analyze patent landscape for FTO assessment.

    Heuristic:
      - >10 patents with priority_date in the last 5 years -> fto_risk_high
      - 1-10 patents in the last 5 years -> fto_risk_moderate
      - 0 patents in the last 5 years but older patents exist -> fto_risk_low
      - 0 patents total -> no_patents

    Returns (metrics, assessment).
    """
    current_year = datetime.date.today().year
    cutoff_year = current_year - 5

    assignee_counts: dict[str, int] = {}
    year_counts: dict[str, int] = {}
    recent_patents: list[dict[str, Any]] = []

    for p in patents:
        assignee = p.get("assignee", "") or "Unknown"
        assignee_counts[assignee] = assignee_counts.get(assignee, 0) + 1

        priority_date = p.get("priority_date", "")
        year: int | None = None
        if priority_date and len(priority_date) >= 4:
            try:
                year = int(priority_date[:4])
            except ValueError:
                pass

        if year is not None:
            year_str = str(year)
            year_counts[year_str] = year_counts.get(year_str, 0) + 1
            if year >= cutoff_year:
                recent_patents.append(p)

    # Top assignees by count.
    top_assignees = [
        name
        for name, _ in sorted(
            assignee_counts.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    # Determine verdict.
    n_recent = len(recent_patents)
    if not patents:
        verdict = "no_patents"
    elif n_recent > 10:
        verdict = "fto_risk_high"
    elif n_recent >= 1:
        verdict = "fto_risk_moderate"
    else:
        verdict = "fto_risk_low"

    # Relay for high or moderate FTO risk.
    if verdict == "fto_risk_high" or verdict == "fto_risk_moderate":
        recent_assignees = set()
        for p in recent_patents:
            a = p.get("assignee", "")
            if a:
                recent_assignees.add(a)
        add_relay(
            "patent.fto_risk_identified",
            f"{n_recent} patent(s) with priority dates in the last 5 years "
            f"found for {query_term!r}; assignees include "
            f"{', '.join(sorted(recent_assignees)[:5])}. This indicates a "
            "crowded patent landscape that may constrain freedom to operate. "
            "A formal FTO analysis by patent counsel is recommended before "
            "advancing this target.",
        )

    # Date range.
    dates: list[str] = []
    for p in patents:
        for df in ("priority_date", "filing_date", "publication_date"):
            d = p.get(df, "")
            if d:
                dates.append(d)
    sorted_dates = sorted(d for d in dates if d)
    date_range = {
        "earliest": sorted_dates[0] if sorted_dates else None,
        "latest": sorted_dates[-1] if sorted_dates else None,
    }

    metrics: dict[str, Any] = {
        "total_patents": len(patents),
        "recent_patents": n_recent,
        "recent_cutoff_year": cutoff_year,
        "by_assignee": dict(
            sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ),
        "by_year": dict(sorted(year_counts.items())),
        "top_assignees": top_assignees,
        "date_range": date_range,
    }
    assessment: dict[str, Any] = {
        "verdict": verdict,
        "query_term": query_term,
        "total_patents": len(patents),
        "recent_patents": n_recent,
        "recent_cutoff_year": cutoff_year,
        "top_assignees": top_assignees,
    }

    return metrics, assessment


# Register ``fetch`` as an alias for ``search`` (issue #92).
patent.add_command(search_cmd, "fetch")
