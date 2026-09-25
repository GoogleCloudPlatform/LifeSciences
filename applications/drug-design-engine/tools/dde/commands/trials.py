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

"""`dde trials` -- Clinical trial pipeline search and analysis.

Two phases:

  search          one query -> clinical trials from ClinicalTrials.gov
                  v2 API, written verbatim to Layer 0 with a sidecar.
  analyze         reads stored search results and classifies whether
                  there is an active drug development pipeline. No
                  network.

ClinicalTrials.gov v2 is a free, unauthenticated JSON API. The search
supports full-text, condition, and intervention queries. Pagination is
cursor-based (nextPageToken); results are capped at 500 studies to
avoid excessive API load.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import click

from ..common import (
    AppState,
    emitter,
    from_option,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
)
from ..core import http, provenance
from ..core.errors import (
    ArtifactError,
    SchemaError,
)
from ..core.qps import qps_for_host

CLINICALTRIALS_API = "https://clinicaltrials.gov/api/v2/studies"
TOOL = "dde.trials"
ARTIFACT_CLASS = "pipeline"

# Maximum studies to fetch across all pages.
_MAX_STUDIES = 500

# Phase string to numeric mapping for comparison.  ClinicalTrials.gov v2
# returns phase values like "PHASE1", "PHASE2", etc.  A trial may list
# multiple phases (e.g. ["PHASE1", "PHASE2"] for a combined study).
_PHASE_ORDER: dict[str, float] = {
    "EARLY_PHASE1": 0.5,
    "PHASE1": 1,
    "PHASE2": 2,
    "PHASE3": 3,
    "PHASE4": 4,
}

# Statuses that indicate a trial is actively running.
_ACTIVE_STATUSES = frozenset(
    {
        "RECRUITING",
        "ENROLLING_BY_INVITATION",
        "NOT_YET_RECRUITING",
        "ACTIVE_NOT_RECRUITING",
    }
)


def _phase_label(phases: list[str]) -> str:
    """Human-readable phase label from the API's phase list."""
    if not phases or phases == ["NA"]:
        return "N/A"
    return "/".join(p.replace("_", " ").title() for p in phases)


def _max_phase_number(phases: list[str]) -> float:
    """Highest numeric phase from a list of phase strings."""
    if not phases:
        return 0
    return max((_PHASE_ORDER.get(p, 0) for p in phases), default=0)


def _fetch_clinicaltrials(query: str, search_by: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch clinical trials from ClinicalTrials.gov v2.

    Supports three search modes:
      - target: full-text search (query.term)
      - condition: condition/disease search (query.cond)
      - intervention: intervention search (query.intr)

    Handles cursor-based pagination, capped at 500 studies.

    Returns (verbatim response bytes, structured artifact dict).
    """
    # Build query parameter based on search mode.
    if search_by == "condition":
        query_param = f"query.cond={quote(query, safe='')}"
    elif search_by == "intervention":
        query_param = f"query.intr={quote(query, safe='')}"
    else:
        query_param = f"query.term={quote(query, safe='')}"

    base_url = f"{CLINICALTRIALS_API}?{query_param}&pageSize=100&format=json"

    all_studies: list[dict[str, Any]] = []
    raw_pages: list[bytes] = []
    page_token: str | None = None

    while len(all_studies) < _MAX_STUDIES:
        url = base_url
        if page_token:
            url += f"&pageToken={quote(page_token, safe='')}"

        response = http.request(
            "GET",
            url,
            qps=qps_for_host("clinicaltrials.gov"),
            timeout=60.0,
        )
        raw_pages.append(response.content)

        try:
            payload = json.loads(response.content.decode("utf-8"))
        except Exception as exc:
            raise SchemaError(
                "ClinicalTrials.gov did not return JSON", detail=str(exc)
            ) from exc

        studies = payload.get("studies", [])
        if not studies:
            break

        for study in studies:
            if len(all_studies) >= _MAX_STUDIES:
                break
            all_studies.append(_extract_study(study))

        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    # Build verbatim bytes -- combine pages if paginated.
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
    by_phase: dict[str, int] = {}
    by_status: dict[str, int] = {}
    sponsor_counts: dict[str, int] = {}
    for s in all_studies:
        phase = s.get("phase_label", "N/A")
        status = s.get("status", "") or "Unknown"
        sponsor = s.get("sponsor", "") or "Unknown"
        by_phase[phase] = by_phase.get(phase, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        sponsor_counts[sponsor] = sponsor_counts.get(sponsor, 0) + 1

    top_sponsors = [
        name
        for name, _ in sorted(sponsor_counts.items(), key=lambda x: x[1], reverse=True)[
            :5
        ]
    ]

    artifact: dict[str, Any] = {
        "schema": "dde.clinical-trials.v1",
        "query": {
            "term": query,
            "search_by": search_by,
            "source": "clinicaltrials.gov",
        },
        "summary": {
            "n_studies": len(all_studies),
            "by_phase": by_phase,
            "by_status": by_status,
            "top_sponsors": top_sponsors,
        },
        "studies": all_studies,
    }

    return raw, artifact


def _extract_study(study: dict[str, Any]) -> dict[str, Any]:
    """Extract structured fields from a single ClinicalTrials.gov study."""
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status_mod = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    arms = protocol.get("armsInterventionsModule", {})
    conditions_mod = protocol.get("conditionsModule", {})
    sponsor_mod = protocol.get("sponsorCollaboratorsModule", {})

    # Extract phases.
    phases = design.get("phases", [])

    # Extract interventions.
    interventions: list[dict[str, str]] = []
    for interv in arms.get("interventions", []):
        interventions.append(
            {
                "type": interv.get("type", ""),
                "name": interv.get("name", ""),
            }
        )

    # Extract sponsor.
    lead_sponsor = (sponsor_mod.get("leadSponsor") or {}).get("name", "")

    # Extract enrollment.
    enrollment_info = design.get("enrollmentInfo") or {}
    enrollment = (
        enrollment_info.get("count") if isinstance(enrollment_info, dict) else None
    )

    # Extract dates.
    start_struct = status_mod.get("startDateStruct") or {}
    completion_struct = (
        status_mod.get("primaryCompletionDateStruct")
        or status_mod.get("completionDateStruct")
        or {}
    )

    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "status": status_mod.get("overallStatus", ""),
        "phases": phases,
        "phase_label": _phase_label(phases),
        "conditions": conditions_mod.get("conditions", []),
        "interventions": interventions,
        "sponsor": lead_sponsor,
        "enrollment": enrollment,
        "start_date": start_struct.get("date", ""),
        "completion_date": completion_struct.get("date", ""),
    }


@click.group()
def trials() -> None:
    """Clinical trial pipeline search and analysis."""


# Verb aliases — see docs/tool-design-guidance.md and issue #92.
#
#   fetch  = retrieve the record for a known identifier (gene, CID, …)
#   search = query and get back a result set
#
# Both verbs are accepted as aliases for discoverability.  The primary
# verb for this group is ``search`` (ClinicalTrials.gov returns a result
# set for a text query); ``fetch`` is the alias.


@trials.command("search")
@click.argument("query")
@click.option(
    "--by",
    "search_by",
    type=click.Choice(["target", "condition", "intervention"]),
    default="target",
    help="Search mode: target (full-text), condition, or intervention.",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    search_by: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search clinical trials for QUERY."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = re.sub(r"[^a-z0-9._-]+", "-", query.lower()).strip("-")[:80] or "query"

    raw, artifact = _fetch_clinicaltrials(query, search_by)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=CLINICALTRIALS_API,
        parameters={
            "query_term": query,
            "search_by": search_by,
        },
    )
    sidecar.note("source_db", "clinicaltrials.gov")
    sidecar.note("n_studies", artifact["summary"]["n_studies"])

    if artifact["summary"]["n_studies"] >= _MAX_STUDIES:
        sidecar.warn(
            f"Results capped at {_MAX_STUDIES} studies; the full result set "
            "may be larger. Refine the query for a more focused search."
        )

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.trials-clinicaltrials.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.trials-clinicaltrials.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.trials-clinicaltrials.meta.json")

    emit.data("query", query)
    emit.data("search_by", search_by)
    emit.data("n_studies", artifact["summary"]["n_studies"])
    emit.data("by_phase", artifact["summary"]["by_phase"])
    emit.data("by_status", artifact["summary"]["by_status"])
    emit.data("top_sponsors", artifact["summary"]["top_sponsors"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@trials.command("analyze")
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
    """Classify pipeline from stored trial search results. No network.

    The underlying search uses full-text matching against
    ClinicalTrials.gov study records.  Short gene symbols (<=4
    characters) may match many unrelated studies, producing inflated
    hit counts and misleading verdicts.  When this is detected, the
    output includes a 'confidence' field set to 'low_text_match_only'
    and fires a mandatory relay.  Always verify trial
    titles/interventions manually for short queries.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = re.sub(r"[^a-z0-9._-]+", "-", query_term.lower()).strip("-")[:80] or "query"
    artifact_path = source_dir / f"{slug}.trials-clinicaltrials.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no clinical trials search artifact for {query_term!r}",
            remedy=f"run `dde trials search {query_term}` first",
        )

    artifact = provenance.read_json(artifact_path, "clinical trials artifact")
    if artifact.get("schema") != "dde.clinical-trials.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=(f"expected dde.clinical-trials.v1, got {artifact.get('schema')!r}"),
        )

    studies = artifact.get("studies", [])
    thresholds = load_thresholds(state, "trials")
    active_min_phase = thresholds.get("active_pipeline_min_phase")

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    # Classify trials.
    _significant, metrics, assessment = _analyze_trials(
        query_term,
        studies,
        active_min_phase,
        add_relay,
    )

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.trials-clinicaltrials.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
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
        f"({len(studies)} studies, "
        f"{metrics['recruiting_count']} recruiting, "
        f"max phase {metrics['max_phase']})"
    )
    if assessment.get("confidence") == "low_text_match_only":
        emit.line(
            f"⚠ LOW CONFIDENCE: text-match query on short symbol "
            f"{query_term!r} — results likely include false positives. "
            f"Verify trial titles/interventions manually before citing "
            f"this verdict."
        )
    top_sponsors = assessment.get("top_sponsors", [])
    if top_sponsors:
        emit.line(f"top sponsors: {', '.join(top_sponsors[:5])}")
    top_interventions = assessment.get("top_interventions", [])
    if top_interventions:
        emit.line(f"top interventions: {', '.join(top_interventions[:5])}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


def _analyze_trials(
    query_term: str,
    studies: list[dict[str, Any]],
    active_min_phase: float,
    add_relay: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Analyze clinical trial pipeline.

    Returns (active_studies, metrics, assessment).
    """
    max_phase = 0.0
    recruiting_count = 0
    phase_2_plus_recruiting = 0
    phase_3_plus: list[dict[str, Any]] = []
    active_studies: list[dict[str, Any]] = []

    by_phase: dict[str, int] = {}
    by_status: dict[str, int] = {}
    sponsor_counts: dict[str, int] = {}
    intervention_counts: dict[str, int] = {}
    dates: list[str] = []

    for s in studies:
        phases = s.get("phases", [])
        phase_num = _max_phase_number(phases)
        status = s.get("status", "")
        sponsor = s.get("sponsor", "")
        phase_label = s.get("phase_label", "N/A")

        if phase_num > max_phase:
            max_phase = phase_num

        by_phase[phase_label] = by_phase.get(phase_label, 0) + 1
        if status:
            by_status[status] = by_status.get(status, 0) + 1
        if sponsor:
            sponsor_counts[sponsor] = sponsor_counts.get(sponsor, 0) + 1

        for interv in s.get("interventions", []):
            name = interv.get("name", "")
            if name:
                intervention_counts[name] = intervention_counts.get(name, 0) + 1

        if status in _ACTIVE_STATUSES:
            recruiting_count += 1
            active_studies.append(s)
            if phase_num >= active_min_phase:
                phase_2_plus_recruiting += 1

        if phase_num >= 3:
            phase_3_plus.append(s)

        for date_field in ("start_date", "completion_date"):
            d = s.get(date_field, "")
            if d:
                dates.append(d)

    # Determine verdict.
    if phase_2_plus_recruiting > 0:
        verdict = "active_pipeline"
    elif studies:
        verdict = "early_pipeline"
    else:
        verdict = "no_pipeline"

    # Text-match confidence safeguard.  ClinicalTrials.gov full-text search
    # matches any mention of the query string — a short gene symbol like
    # "ARTN" will match hundreds of unrelated studies.  When any of the
    # low-specificity heuristics fire, downgrade confidence and fire a
    # mandatory relay so downstream consumers cannot cite the verdict
    # without acknowledging that the result set is text-matched.
    _SHORT_QUERY_THRESHOLD = 4
    _HIGH_HIT_THRESHOLD = 200
    _HIGH_PHASE3_RATIO = 0.05

    confidence_triggers: list[str] = []
    n_total = len(studies)

    if len(query_term) <= _SHORT_QUERY_THRESHOLD:
        confidence_triggers.append(
            f"query {query_term!r} is short ({len(query_term)} chars)"
        )
    if n_total > _HIGH_HIT_THRESHOLD:
        confidence_triggers.append(
            f"hit count ({n_total}) exceeds {_HIGH_HIT_THRESHOLD}"
        )
    if n_total > 0 and len(phase_3_plus) / n_total > _HIGH_PHASE3_RATIO:
        confidence_triggers.append(
            f"Phase 3+ ratio ({len(phase_3_plus)}/{n_total} = "
            f"{len(phase_3_plus) / n_total:.1%}) exceeds "
            f"{_HIGH_PHASE3_RATIO:.0%} threshold"
        )

    if confidence_triggers:
        confidence = "low_text_match_only"
        confidence_reason = (
            f"Query {query_term!r} ({len(query_term)} chars) returned "
            f"{n_total} studies — text matching of short gene symbols is "
            f"unreliable. Triggers: {'; '.join(confidence_triggers)}. "
            f"Manual review of trial titles/interventions is required."
        )
        add_relay(
            "trials.text_match_not_mechanism",
            f"Trial search for {query_term!r} used full-text matching, not "
            f"mechanism-specific filtering. The {n_total} results may include "
            f"incidental mentions. Any finding citing this verdict MUST note "
            f"that results are text-matched, not mechanism-verified.",
        )
    else:
        confidence = None
        confidence_reason = None

    # Top sponsors and interventions.
    top_sponsors = [
        name
        for name, _ in sorted(sponsor_counts.items(), key=lambda x: x[1], reverse=True)[
            :5
        ]
    ]
    top_interventions = [
        name
        for name, _ in sorted(
            intervention_counts.items(), key=lambda x: x[1], reverse=True
        )[:5]
    ]

    # Date range.
    sorted_dates = sorted(d for d in dates if d)
    date_range = {
        "earliest": sorted_dates[0] if sorted_dates else None,
        "latest": sorted_dates[-1] if sorted_dates else None,
    }

    # Relay: competitor pipeline when Phase 3+ trials exist.
    if phase_3_plus:
        sponsors_in_p3 = sorted(
            {s.get("sponsor", "") for s in phase_3_plus if s.get("sponsor")}
        )
        add_relay(
            "trials.active_competitor_pipeline",
            f"{len(phase_3_plus)} Phase 3+ trial(s) found for "
            f"{query_term!r}; sponsors include "
            f"{', '.join(sponsors_in_p3[:5])}. Late-stage clinical "
            "development may affect freedom to operate or competitive "
            "positioning.",
        )

    metrics: dict[str, Any] = {
        "total_studies": len(studies),
        "by_phase": by_phase,
        "by_status": by_status,
        "recruiting_count": recruiting_count,
        "phase_2_plus_recruiting": phase_2_plus_recruiting,
        "max_phase": max_phase,
        "top_sponsors": top_sponsors,
        "top_interventions": top_interventions,
        "date_range": date_range,
    }
    assessment: dict[str, Any] = {
        "verdict": verdict,
        "query_term": query_term,
        "n_studies": len(studies),
        "max_phase": max_phase,
        "recruiting_count": recruiting_count,
        "phase_2_plus_recruiting": phase_2_plus_recruiting,
        "phase_3_plus_count": len(phase_3_plus),
        "top_sponsors": top_sponsors,
        "top_interventions": top_interventions,
    }
    if confidence:
        assessment["confidence"] = confidence
        assessment["confidence_reason"] = confidence_reason

    return active_studies, metrics, assessment


# Register ``fetch`` as an alias for ``search`` (issue #92).
trials.add_command(search_cmd, "fetch")
