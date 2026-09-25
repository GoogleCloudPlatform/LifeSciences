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

"""`dde faers` — FDA adverse event and drug label lookup.

Queries the openFDA API for FAERS (FDA Adverse Event Reporting System)
spontaneous reports and drug labeling/SPL data.  FAERS reports are
voluntary — they cannot establish incidence, causation, or comparative
safety; see the relay code registered below.

Two phases:

  search   drug name -> verbatim openFDA response + structured artifact,
           written to Layer 0 with a sidecar.
  analyze  reads stored FAERS results offline and produces frequency
           counts of reactions, outcomes, and serious-vs-non-serious
           breakdown.  No network.

The openFDA API returns HTTP 404 when no results match a query. That is
a legitimate answer ("no reports for this drug"), not a failure. The
search command handles it by writing an empty artifact rather than
raising, so downstream analysis can distinguish "looked and found
nothing" from "never looked".
"""

from __future__ import annotations

import json
from collections import Counter
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
from ..core.errors import ArtifactError, SchemaError
from ..core.qps import qps_for_host

TOOL = "faers"
ARTIFACT_CLASS = "safety"

OPENFDA_API = "https://api.fda.gov/drug"
_ENDPOINT_PATHS = {"events": "event.json", "labeling": "label.json"}


def _slugify(name: str) -> str:
    """Lowercase, strip non-alnum, collapse runs of hyphens."""
    slug = ""
    for ch in name.lower():
        if ch.isalnum():
            slug += ch
        elif slug and slug[-1] != "-":
            slug += "-"
    return slug.strip("-")


def _search_events(drug: str, max_results: int) -> tuple[bytes, dict[str, Any]]:
    """Query openFDA FAERS adverse event endpoint.

    Returns (verbatim response bytes, parsed payload).  A 404 — no
    matching reports — is returned as an empty result dict rather than
    raised, because "no reports" is a legitimate finding.
    """
    url = (
        f"{OPENFDA_API}/event.json"
        f"?search=patient.drug.openfda.generic_name:"
        f'"{quote(drug, safe="")}"'
        f"&limit={max_results}"
    )
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("api.fda.gov"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    raw = response.content
    if response.status_code == 404:
        empty: dict[str, Any] = {"meta": {"results": {"total": 0}}, "results": []}
        return json.dumps(empty).encode("utf-8"), empty
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("openFDA did not return valid JSON", detail=str(exc)) from exc
    return raw, payload


def _search_labels(drug: str, max_results: int) -> tuple[bytes, dict[str, Any]]:
    """Query openFDA drug labeling endpoint.

    Same 404-tolerance as the events endpoint.
    """
    limit = min(max_results, 5)
    url = (
        f"{OPENFDA_API}/label.json"
        f"?search=openfda.generic_name:"
        f'"{quote(drug, safe="")}"'
        f"&limit={limit}"
    )
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("api.fda.gov"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    raw = response.content
    if response.status_code == 404:
        empty: dict[str, Any] = {"meta": {"results": {"total": 0}}, "results": []}
        return json.dumps(empty).encode("utf-8"), empty
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("openFDA did not return valid JSON", detail=str(exc)) from exc
    return raw, payload


def _extract_events(
    payload: dict[str, Any], drug: str, max_results: int
) -> dict[str, Any]:
    """Build the structured dde.faers.v1 artifact from an events response."""
    results = payload.get("results") or []
    total_available = (payload.get("meta") or {}).get("results", {}).get("total", 0)

    reports: list[dict[str, Any]] = []
    reaction_counter: Counter[str] = Counter()
    serious_count = 0

    for event in results:
        serious = event.get("serious", 0) == 1

        # Outcomes
        outcomes: list[str] = []
        outcome_map = {
            "seriousnessdeath": "death",
            "seriousnesshospitalization": "hospitalization",
            "seriousnesslifethreatening": "life-threatening",
            "seriousnessdisabling": "disabling",
            "seriousnesscongenitalanomali": "congenital-anomaly",
            "seriousnessother": "other-serious",
        }
        for field, label in outcome_map.items():
            if event.get(field) == "1":
                outcomes.append(label)

        if serious:
            serious_count += 1

        # Reactions
        reactions: list[dict[str, str | None]] = []
        for reaction in event.get("patient", {}).get("reaction", []):
            term = reaction.get("reactionmeddrapt", "")
            outcome = reaction.get("reactionoutcome")
            outcome_label = (
                {
                    "1": "recovered",
                    "2": "recovering",
                    "3": "not recovered",
                    "4": "recovered with sequelae",
                    "5": "fatal",
                    "6": "unknown",
                }.get(str(outcome))
                if outcome
                else None
            )
            reactions.append({"term": term, "outcome": outcome_label})
            if term:
                reaction_counter[term] += 1

        # Drugs
        drugs: list[dict[str, str | None]] = []
        role_map = {
            "1": "primary suspect",
            "2": "secondary suspect",
            "3": "concomitant",
            "4": "interacting",
        }
        for drug_entry in event.get("patient", {}).get("drug", []):
            name = drug_entry.get("medicinalproduct", "")
            role = role_map.get(str(drug_entry.get("drugcharacterization", "")))
            indication = drug_entry.get("drugindication")
            drugs.append({"name": name, "role": role, "indication": indication})

        reports.append(
            {
                "safety_report_id": event.get("safetyreportid", ""),
                "report_date": event.get("receiptdate", ""),
                "serious": serious,
                "outcomes": outcomes,
                "reactions": reactions,
                "drugs": drugs,
            }
        )

    top_reactions = [term for term, _ in reaction_counter.most_common(10)]

    return {
        "schema": "dde.faers.v1",
        "query": {
            "drug": drug,
            "endpoint": "events",
            "max_results": max_results,
        },
        "summary": {
            "n_reports": len(reports),
            "total_available": total_available,
            "serious_count": serious_count,
            "top_reactions": top_reactions,
        },
        "reports": reports,
    }


def _extract_labels(payload: dict[str, Any], drug: str) -> dict[str, Any]:
    """Build the structured dde.faers.v1 artifact from a labeling response."""
    results = payload.get("results") or []
    total_available = (payload.get("meta") or {}).get("results", {}).get("total", 0)

    labels: list[dict[str, Any]] = []
    for label in results:
        openfda = label.get("openfda", {})
        labels.append(
            {
                "brand_name": (openfda.get("brand_name") or [None])[0],
                "generic_name": (openfda.get("generic_name") or [None])[0],
                "warnings": (label.get("warnings") or [None])[0],
                "adverse_reactions": (label.get("adverse_reactions") or [None])[0],
                "boxed_warning": (label.get("boxed_warning") or [None])[0],
            }
        )

    return {
        "schema": "dde.faers.v1",
        "query": {
            "drug": drug,
            "endpoint": "labeling",
            "max_results": len(labels),
        },
        "summary": {
            "n_labels": len(labels),
            "total_available": total_available,
        },
        "labels": labels,
    }


@click.group()
def faers() -> None:
    """FDA adverse event and drug label lookup."""


@faers.command("search")
@click.argument("drug")
@click.option(
    "--endpoint",
    type=click.Choice(["events", "labeling"]),
    default="events",
    help="Query FAERS adverse events or drug labeling/SPL.",
)
@click.option(
    "--max-results",
    type=int,
    default=50,
    help="Maximum results to return (max 100).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    drug: str,
    endpoint: str,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search openFDA for adverse events or drug labeling for DRUG."""
    emit = emitter(as_json, quiet)
    max_results = min(max_results, 100)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = _slugify(drug)

    if endpoint == "events":
        raw, payload = _search_events(drug, max_results)
        structured = _extract_events(payload, drug, max_results)
        suffix = "faers-events"
    else:
        raw, payload = _search_labels(drug, max_results)
        structured = _extract_labels(payload, drug)
        suffix = "faers-labeling"

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=f"{OPENFDA_API}/{_ENDPOINT_PATHS[endpoint]}",
        parameters={
            "drug": drug,
            "endpoint": endpoint,
            "max_results": max_results,
        },
    )
    sidecar.note("source", "openFDA (U.S. FDA)")
    sidecar.warn(
        "FAERS reports are spontaneous (voluntary) adverse event reports. They "
        "cannot establish incidence rates, causation, or comparative safety.",
        code="faers.spontaneous_reports_not_incidence",
    )

    # Write verbatim response
    raw_path = target_dir / f"{slug}.{suffix}.raw.json"
    raw_path.write_bytes(raw)
    sidecar.add_output(raw_path)

    # Write structured artifact
    structured_path = target_dir / f"{slug}.{suffix}.json"
    structured_path.write_text(
        json.dumps(structured, indent=2) + "\n", encoding="utf-8"
    )
    sidecar.add_output(structured_path)

    meta_path = sidecar.write(target_dir / f"{slug}.{suffix}.meta.json")

    emit.data("drug", drug)
    emit.data("endpoint", endpoint)
    if endpoint == "events":
        summary = structured.get("summary", {})
        emit.data("summary", summary)
        emit.line(
            f"{drug}: {summary.get('n_reports', 0)} reports fetched "
            f"(of {summary.get('total_available', 0)} available), "
            f"{summary.get('serious_count', 0)} serious"
        )
        top = summary.get("top_reactions", [])
        if top:
            emit.line(f"top reactions: {', '.join(top[:5])}")
    else:
        summary = structured.get("summary", {})
        emit.data("summary", summary)
        emit.line(
            f"{drug}: {summary.get('n_labels', 0)} labels fetched "
            f"(of {summary.get('total_available', 0)} available)"
        )

    emit.path(raw_path, role="raw")
    emit.path(structured_path, role="structured")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@faers.command("analyze")
@click.argument("drug")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    drug: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Analyze stored FAERS adverse event data for DRUG. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(drug)
    path = source_dir / f"{slug}.faers-events.json"
    if not path.is_file():
        raise ArtifactError(
            f"no fetched FAERS events for {drug!r}",
            remedy=f"run `dde faers search {drug}` first",
        )
    data = provenance.read_json(path, "FAERS events artifact")

    if not isinstance(data, dict) or data.get("schema") != "dde.faers.v1":
        raise SchemaError(
            f"unexpected schema in {path.name}",
            detail=f"expected dde.faers.v1, got {data.get('schema') if isinstance(data, dict) else type(data).__name__}",
        )

    reports = data.get("reports") or []
    total_available = data.get("summary", {}).get("total_available", 0)

    # Counts
    serious_count = sum(1 for r in reports if r.get("serious"))
    non_serious_count = len(reports) - serious_count

    # Reaction frequency
    reaction_counter: Counter[str] = Counter()
    for report in reports:
        for reaction in report.get("reactions", []):
            term = reaction.get("term")
            if term:
                reaction_counter[term] += 1

    # Outcome frequency
    outcome_counter: Counter[str] = Counter()
    for report in reports:
        for outcome in report.get("outcomes", []):
            outcome_counter[outcome] += 1

    top_reactions = [
        {"term": term, "count": count}
        for term, count in reaction_counter.most_common(20)
    ]
    top_outcomes = [
        {"outcome": outcome, "count": count}
        for outcome, count in outcome_counter.most_common(10)
    ]

    verdict = "reports_found" if reports else "no_reports"

    relays: list[dict[str, str]] = []
    relays.append(
        provenance.relay(
            "faers.spontaneous_reports_not_incidence",
            "FAERS reports are spontaneous (voluntary) adverse event reports. They "
            "cannot establish incidence rates, causation, or comparative safety. "
            "Reporting rates are affected by media attention, time on market, "
            "indication severity, and reporter awareness. Do not interpret report "
            "counts as incidence or compare raw counts between drugs.",
        )
    )

    metrics = {
        "n_reports_fetched": len(reports),
        "total_available": total_available,
        "serious_count": serious_count,
        "non_serious_count": non_serious_count,
        "top_reactions": top_reactions,
        "top_outcomes": top_outcomes,
    }
    assessment = {
        "verdict": verdict,
        "drug": drug,
        "n_reports": len(reports),
        "serious_fraction": round(serious_count / len(reports), 3) if reports else None,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.faers-events.analysis.json",
        source=state.project().relative(path),
        threshold_set="faers-analysis@1.0",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("relays", relays)
    emit.line(f"{drug}: {verdict.upper()}")
    emit.line(
        f"  {len(reports)} reports ({serious_count} serious, "
        f"{non_serious_count} non-serious)"
    )
    if top_reactions:
        top5 = ", ".join(r["term"] for r in top_reactions[:5])
        emit.line(f"  top reactions: {top5}")
    if top_outcomes:
        top5 = ", ".join(o["outcome"] for o in top_outcomes[:5])
        emit.line(f"  top outcomes: {top5}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
