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

"""dde differentiation -- competitive differentiation assessment.

Produces structured competitive-differentiation findings that keep
three distinct dimensions separate:

  1. **Competitor activity** — what programs/compounds/publications
     exist for this target or modality+indication combination.
  2. **Patentability/novelty** — is there white space for new IP.
  3. **Freedom to operate (FTO)** — can the concept be pursued without
     infringing existing IP.

These dimensions are NEVER blended into a single score.  Each carries:
  - search date
  - search scope (what was queried)
  - coverage limits (what was NOT covered)

Every patent-related finding carries an explicit disclaimer that a
public/structural search is **not formal legal clearance** and that
material FTO conclusions require qualified review.

Two phases:

  assess         reads stored patent search results and concept
                 context, produces a three-dimension assessment.
                 No network.
  report         formats the assessment as evidence-assessment
                 records (``dde.evidence-assessment.v1``).
                 No network.

Usage with the patent tool::

    dde patent search "CDK4 inhibitor"
    dde differentiation assess CDK4 --concept IC-001 --indication solid_tumors --modality small_molecule
    dde differentiation report CDK4 --concept IC-001
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
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
from ..core import provenance
from ..core.errors import ArtifactError, SchemaError
from ..core.output import warn

TOOL = "dde.differentiation"
ARTIFACT_CLASS = "ip"

# ---------------------------------------------------------------------------
# Mandatory disclaimer — the brief's hard constraint.
# ---------------------------------------------------------------------------

FTO_DISCLAIMER = (
    "This assessment is based on public patent database searches and "
    "publicly available information. It is NOT formal legal clearance. "
    "A public search or structural similarity analysis cannot substitute "
    "for a formal freedom-to-operate opinion by qualified patent counsel. "
    "Material FTO conclusions require qualified legal review."
)

COVERAGE_DISCLAIMER_TEMPLATE = (
    "Search scope: {scope}. "
    "Coverage limits: {limits}. "
    "This search should not be treated as exhaustive."
)

# ---------------------------------------------------------------------------
# Evidence types following the naming convention from design §1.5
# ---------------------------------------------------------------------------

EVIDENCE_TYPE_COMPETITOR = "competitive_precedent"
EVIDENCE_TYPE_PATENT = "patent_landscape"

# ---------------------------------------------------------------------------
# Three-dimension assessment logic
# ---------------------------------------------------------------------------


def _assess_competitor_activity(
    patents: list[dict[str, Any]],
    query_term: str,
    *,
    search_date: str,
    search_scope: str,
    coverage_limits: str,
) -> dict[str, Any]:
    """Assess competitor activity from patent and literature data.

    Returns a structured finding for the competitor-activity dimension.
    Existing competitor activity is NOT by itself a veto — a crowded
    field with a differentiated angle should surface as
    "differentiated despite crowding", not as a rejection.
    """
    current_year = datetime.date.today().year
    cutoff_year = current_year - 5

    # Count recent patents by assignee.
    assignee_counts: dict[str, int] = {}
    recent_patents: list[dict[str, Any]] = []

    for p in patents:
        assignee = p.get("assignee", "") or "Unknown"
        assignee_counts[assignee] = assignee_counts.get(assignee, 0) + 1

        priority_date = p.get("priority_date", "")
        if priority_date and len(priority_date) >= 4:
            try:
                year = int(priority_date[:4])
                if year >= cutoff_year:
                    recent_patents.append(p)
            except ValueError:
                pass

    top_assignees = [
        name
        for name, _ in sorted(
            assignee_counts.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    n_total = len(patents)
    n_recent = len(recent_patents)
    n_assignees = len(assignee_counts)

    # Density classification — informational, not a veto.
    if n_recent == 0:
        density = "uncrowded"
        summary = f"No recent patent activity (last {current_year - cutoff_year} years) for {query_term!r}."
    elif n_recent <= 5:
        density = "low_activity"
        summary = (
            f"Low competitor activity: {n_recent} recent patent(s) "
            f"from {min(n_assignees, n_recent)} assignee(s) for {query_term!r}."
        )
    elif n_recent <= 20:
        density = "moderate_activity"
        summary = (
            f"Moderate competitor activity: {n_recent} recent patent(s) "
            f"from {n_assignees} assignee(s) for {query_term!r}."
        )
    else:
        density = "high_activity"
        summary = (
            f"High competitor activity: {n_recent} recent patent(s) "
            f"from {n_assignees} assignee(s) for {query_term!r}."
        )

    return {
        "dimension": "competitor_activity",
        "query_term": query_term,
        "density": density,
        "summary": summary,
        "total_patents": n_total,
        "recent_patents": n_recent,
        "recent_cutoff_year": cutoff_year,
        "unique_assignees": n_assignees,
        "top_assignees": top_assignees,
        "search_date": search_date,
        "search_scope": search_scope,
        "coverage_limits": coverage_limits,
        # Explicit: crowding alone is not a veto.
        "crowding_is_veto": False,
        "note": (
            "Existing competitor activity is informational, not a "
            "go/no-go gate. A crowded field with a genuinely "
            "differentiated angle should surface as 'differentiated "
            "despite crowding', not be rejected by a naive "
            "'many competitors = bad' heuristic."
        ),
    }


def _assess_patentability(
    patents: list[dict[str, Any]],
    query_term: str,
    *,
    modality: str | None,
    indication: str | None,
    search_date: str,
    search_scope: str,
    coverage_limits: str,
) -> dict[str, Any]:
    """Assess patentability/novelty from the patent landscape.

    Checks for white space — areas where new IP could be filed.
    """
    current_year = datetime.date.today().year
    current_year - 5

    # Analyze claim scope from patent titles/snippets.
    modality_patents: list[dict[str, Any]] = []
    indication_patents: list[dict[str, Any]] = []

    for p in patents:
        text = f"{p.get('title', '')} {p.get('snippet', '')}".lower()
        if modality and modality.lower().replace("_", " ") in text:
            modality_patents.append(p)
        if indication and indication.lower().replace("_", " ") in text:
            indication_patents.append(p)

    # White space analysis.
    has_modality_overlap = len(modality_patents) > 0
    has_indication_overlap = len(indication_patents) > 0

    if not patents:
        novelty = "high_novelty"
        summary = f"No patents found for {query_term!r}; high apparent novelty."
    elif not has_modality_overlap and not has_indication_overlap:
        novelty = "potential_novelty"
        summary = (
            f"Patents exist for {query_term!r} but none appear to cover "
            f"the specific modality ({modality or 'unspecified'}) or "
            f"indication ({indication or 'unspecified'}) combination."
        )
    elif has_modality_overlap and has_indication_overlap:
        novelty = "crowded_space"
        summary = (
            f"Patents exist covering both the modality "
            f"({modality or 'unspecified'}: {len(modality_patents)} patents) "
            f"and indication ({indication or 'unspecified'}: "
            f"{len(indication_patents)} patents). "
            "Differentiation angle needed for patentability."
        )
    else:
        novelty = "partial_overlap"
        overlap_dim = "modality" if has_modality_overlap else "indication"
        summary = (
            f"Patents overlap on {overlap_dim} but not on "
            f"{'indication' if has_modality_overlap else 'modality'}. "
            "Potential white space exists in the non-overlapping dimension."
        )

    return {
        "dimension": "patentability",
        "query_term": query_term,
        "novelty_assessment": novelty,
        "summary": summary,
        "modality_overlap_count": len(modality_patents),
        "indication_overlap_count": len(indication_patents),
        "modality": modality,
        "indication": indication,
        "search_date": search_date,
        "search_scope": search_scope,
        "coverage_limits": coverage_limits,
    }


def _assess_fto(
    patents: list[dict[str, Any]],
    query_term: str,
    *,
    search_date: str,
    search_scope: str,
    coverage_limits: str,
) -> dict[str, Any]:
    """Assess freedom to operate from patent landscape.

    Always carries the mandatory disclaimer that a public search
    is not formal legal clearance.
    """
    current_year = datetime.date.today().year
    cutoff_year = current_year - 5

    # Identify potentially blocking patents (recent, granted or pending).
    recent_patents: list[dict[str, Any]] = []
    for p in patents:
        priority_date = p.get("priority_date", "")
        if priority_date and len(priority_date) >= 4:
            try:
                year = int(priority_date[:4])
                if year >= cutoff_year:
                    recent_patents.append(p)
            except ValueError:
                pass

    # Jurisdiction analysis.
    jurisdictions: dict[str, int] = {}
    for p in recent_patents:
        pub_num = p.get("publication_number", "")
        if pub_num:
            # Extract jurisdiction prefix (e.g., "US", "EP", "WO", "CN").
            match = re.match(r"^([A-Z]{2})", pub_num)
            if match:
                j = match.group(1)
                jurisdictions[j] = jurisdictions.get(j, 0) + 1

    # Recent assignees with potential blocking filings.
    recent_assignees: set[str] = set()
    for p in recent_patents:
        a = p.get("assignee", "")
        if a:
            recent_assignees.add(a)

    n_recent = len(recent_patents)
    if n_recent == 0:
        risk_level = "no_recent_filings"
        summary = (
            f"No recent patent filings (since {cutoff_year}) found for "
            f"{query_term!r}. FTO risk appears low based on this search."
        )
    elif n_recent <= 5:
        risk_level = "low_risk"
        summary = (
            f"Few recent patent filings ({n_recent}) for {query_term!r}. "
            f"Assignees: {', '.join(sorted(recent_assignees)[:5])}."
        )
    elif n_recent <= 20:
        risk_level = "moderate_risk"
        summary = (
            f"Moderate number of recent patent filings ({n_recent}) for "
            f"{query_term!r}. Multiple assignees active. "
            "Detailed claim analysis recommended."
        )
    else:
        risk_level = "high_risk"
        summary = (
            f"Dense recent patent filings ({n_recent}) for {query_term!r}. "
            f"Assignees include: {', '.join(sorted(recent_assignees)[:5])}. "
            "Formal FTO analysis by patent counsel is strongly recommended."
        )

    # Unresolved questions — always present.
    unresolved: list[str] = []
    if not jurisdictions:
        unresolved.append(
            "No jurisdiction information could be extracted; "
            "coverage across patent offices is unknown."
        )
    else:
        # List jurisdictions NOT covered.
        major_jurisdictions = {"US", "EP", "WO", "CN", "JP", "KR"}
        missing = major_jurisdictions - set(jurisdictions.keys())
        if missing:
            unresolved.append(
                f"The following major jurisdictions had no results and "
                f"may not have been covered: {', '.join(sorted(missing))}."
            )

    unresolved.append(
        "Unpublished patent applications (typically 18 months from "
        "priority date) are not visible in public searches."
    )
    unresolved.append("Non-English filings may be underrepresented in this search.")

    return {
        "dimension": "freedom_to_operate",
        "query_term": query_term,
        "risk_level": risk_level,
        "summary": summary,
        "recent_filings": n_recent,
        "recent_cutoff_year": cutoff_year,
        "jurisdictions_found": jurisdictions,
        "recent_assignees": sorted(recent_assignees)[:10],
        "unresolved_questions": unresolved,
        "search_date": search_date,
        "search_scope": search_scope,
        "coverage_limits": coverage_limits,
        # Mandatory disclaimer — NEVER omit.
        "fto_disclaimer": FTO_DISCLAIMER,
    }


def assess_competitive_differentiation(
    patents: list[dict[str, Any]],
    query_term: str,
    *,
    modality: str | None = None,
    indication: str | None = None,
    entity: str | None = None,
    charter_constraints: list[str] | None = None,
    search_date: str | None = None,
    search_scope: str | None = None,
    coverage_limits: str | None = None,
) -> dict[str, Any]:
    """Produce a three-dimension competitive differentiation assessment.

    The three dimensions (competitor_activity, patentability,
    freedom_to_operate) are NEVER collapsed into a single score.
    Each dimension carries its own search metadata and is reported
    separately.

    Parameters
    ----------
    patents:
        Patent records from ``dde patent search``.
    query_term:
        The search term used.
    modality:
        Concept modality (e.g. "small_molecule", "biologic").
    indication:
        Concept indication (e.g. "solid_tumors").
    entity:
        Proposed entity identifier (compound, sequence, etc.).
    charter_constraints:
        Program-specific constraints from the charter.
    search_date:
        ISO 8601 date of the search.
    search_scope:
        Description of what was queried.
    coverage_limits:
        Description of what was NOT covered.

    Returns
    -------
    dict
        Structured assessment with three separate dimension findings.
    """
    now = search_date or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    scope = search_scope or (f"Google Patents public search for {query_term!r}")

    limits = coverage_limits or (
        "Non-English filings may be underrepresented; "
        "unpublished applications (within 18-month window) are not visible; "
        "patent databases other than Google Patents were not queried; "
        "this search is not exhaustive"
    )

    competitor = _assess_competitor_activity(
        patents,
        query_term,
        search_date=now,
        search_scope=scope,
        coverage_limits=limits,
    )
    patentability = _assess_patentability(
        patents,
        query_term,
        modality=modality,
        indication=indication,
        search_date=now,
        search_scope=scope,
        coverage_limits=limits,
    )
    fto = _assess_fto(
        patents,
        query_term,
        search_date=now,
        search_scope=scope,
        coverage_limits=limits,
    )

    result: dict[str, Any] = {
        "schema": "dde.competitive-differentiation.v1",
        "query_term": query_term,
        "concept_context": {
            "modality": modality,
            "indication": indication,
            "entity": entity,
        },
        "dimensions": {
            "competitor_activity": competitor,
            "patentability": patentability,
            "freedom_to_operate": fto,
        },
        "charter_constraints": charter_constraints or [],
        "search_metadata": {
            "search_date": now,
            "search_scope": scope,
            "coverage_limits": limits,
            "source": "google-patents",
        },
        # Top-level disclaimers.
        "fto_disclaimer": FTO_DISCLAIMER,
        "coverage_disclaimer": COVERAGE_DISCLAIMER_TEMPLATE.format(
            scope=scope, limits=limits
        ),
        # Explicit: dimensions are never blended.
        "dimensions_are_independent": True,
        "blended_score": None,  # Explicitly null — never populated.
    }

    return result


def build_assessment_records(
    differentiation: dict[str, Any],
    concept_ref: str,
    *,
    assessed_by: str = "dde.differentiation",
) -> list[dict[str, Any]]:
    """Build ``dde.evidence-assessment.v1`` records from the assessment.

    Produces one assessment record per dimension, plus one for any
    charter constraints.  Records follow the schema from
    ``core/evidence.py``.

    Parameters
    ----------
    differentiation:
        Output from ``assess_competitive_differentiation()``.
    concept_ref:
        The concept reference (e.g. "IC-001" or "IC-001-r2").
    assessed_by:
        Who performed the assessment.

    Returns
    -------
    list[dict]
        Assessment records ready for ``controlstore.write_record()``.
    """
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dims = differentiation.get("dimensions", {})
    query = differentiation.get("query_term", "")
    records: list[dict[str, Any]] = []

    # --- Competitor activity assessment ---
    comp = dims.get("competitor_activity", {})
    records.append(
        {
            "schema": "dde.evidence-assessment.v1",
            "id": "AR-PENDING",  # Caller assigns final ID.
            "concept_ref": concept_ref,
            "claim": (
                f"Competitive landscape for {query!r}: "
                f"{comp.get('density', 'unknown')} competitor activity"
            ),
            "evidence_status": "supported",
            "execution_outcome": "completed",
            "evidence": {
                "artifact_path": f"raw/ip/{_slug(query)}.differentiation.json",
                "evidence_type": EVIDENCE_TYPE_COMPETITOR,
                "metric_name": "recent_patents",
                "metric_value": comp.get("recent_patents", 0),
                "method": "google_patents_xhr",
                "context": comp.get("summary", ""),
            },
            "confidence": "moderate",
            "rationale": (
                f"{comp.get('summary', '')} "
                f"Search scope: {comp.get('search_scope', 'unspecified')}. "
                f"Coverage limits: {comp.get('coverage_limits', 'unspecified')}."
            ),
            "assessed_at": now,
            "assessed_by": assessed_by,
        }
    )

    # --- Patentability assessment ---
    pat = dims.get("patentability", {})
    records.append(
        {
            "schema": "dde.evidence-assessment.v1",
            "id": "AR-PENDING",
            "concept_ref": concept_ref,
            "claim": (
                f"Patentability for {query!r}: "
                f"{pat.get('novelty_assessment', 'unknown')}"
            ),
            "evidence_status": _patentability_to_evidence_status(
                pat.get("novelty_assessment", "")
            ),
            "execution_outcome": "completed",
            "evidence": {
                "artifact_path": f"raw/ip/{_slug(query)}.differentiation.json",
                "evidence_type": EVIDENCE_TYPE_PATENT,
                "metric_name": "modality_overlap_count",
                "metric_value": pat.get("modality_overlap_count", 0),
                "method": "google_patents_xhr",
                "context": pat.get("summary", ""),
            },
            "confidence": "low",
            "rationale": (
                f"{pat.get('summary', '')} "
                f"Search scope: {pat.get('search_scope', 'unspecified')}. "
                f"Coverage limits: {pat.get('coverage_limits', 'unspecified')}. "
                f"{FTO_DISCLAIMER}"
            ),
            "assessed_at": now,
            "assessed_by": assessed_by,
        }
    )

    # --- FTO assessment ---
    fto = dims.get("freedom_to_operate", {})
    records.append(
        {
            "schema": "dde.evidence-assessment.v1",
            "id": "AR-PENDING",
            "concept_ref": concept_ref,
            "claim": (
                f"Freedom to operate for {query!r}: {fto.get('risk_level', 'unknown')}"
            ),
            "evidence_status": _fto_to_evidence_status(fto.get("risk_level", "")),
            "execution_outcome": "completed",
            "evidence": {
                "artifact_path": f"raw/ip/{_slug(query)}.differentiation.json",
                "evidence_type": EVIDENCE_TYPE_PATENT,
                "metric_name": "recent_filings",
                "metric_value": fto.get("recent_filings", 0),
                "method": "google_patents_xhr",
                "context": (f"{fto.get('summary', '')} DISCLAIMER: {FTO_DISCLAIMER}"),
            },
            "confidence": "low",
            "rationale": (
                f"{fto.get('summary', '')} "
                f"Unresolved: {'; '.join(fto.get('unresolved_questions', []))}. "
                f"{FTO_DISCLAIMER}"
            ),
            "assessed_at": now,
            "assessed_by": assessed_by,
        }
    )

    # --- Charter constraints (if any) ---
    constraints = differentiation.get("charter_constraints", [])
    for constraint in constraints:
        records.append(
            {
                "schema": "dde.evidence-assessment.v1",
                "id": "AR-PENDING",
                "concept_ref": concept_ref,
                "claim": f"Charter constraint: {constraint}",
                "evidence_status": "supported",
                "execution_outcome": "completed",
                "evidence": {
                    "artifact_path": f"raw/ip/{_slug(query)}.differentiation.json",
                    "evidence_type": EVIDENCE_TYPE_COMPETITOR,
                    "method": "charter_review",
                    "context": (
                        f"Program-specific constraint from charter: {constraint}"
                    ),
                },
                "confidence": "high",
                "rationale": (
                    f"This is a program-specific constraint (charter-level "
                    f"exclusion), not a scientific rejection. Constraint: "
                    f"{constraint}"
                ),
                "assessed_at": now,
                "assessed_by": assessed_by,
            }
        )

    return records


def _patentability_to_evidence_status(novelty: str) -> str:
    """Map patentability assessment to evidence status."""
    if novelty in ("high_novelty", "potential_novelty"):
        return "supported"
    elif novelty == "crowded_space":
        return "contradicted"
    elif novelty == "partial_overlap":
        return "insufficient"
    return "not_assessed"


def _fto_to_evidence_status(risk_level: str) -> str:
    """Map FTO risk level to evidence status.

    This maps the informational risk level into the evidence status
    vocabulary.  Note: even a "supported" FTO status carries the
    mandatory disclaimer — it is NEVER treated as legal clearance.
    """
    if risk_level == "no_recent_filings":
        return "supported"
    elif risk_level == "low_risk":
        return "supported"
    elif risk_level == "moderate_risk":
        return "insufficient"
    elif risk_level == "high_risk":
        return "contradicted"
    return "not_assessed"


def _slug(text: str) -> str:
    """Slugify a string for filenames."""
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-")[:80] or "query"


# ---------------------------------------------------------------------------
# Source-transparency helpers
# ---------------------------------------------------------------------------

#: Glob pattern for trial artifacts produced by ``dde trials search``.
_TRIAL_ARTIFACT_GLOB = "*.trials-*.artifact.json"

#: The patent source tag used in artifact filenames and metadata.
_SOURCE_PATENT = "patent-google-patents"


def _scan_unconsumed_trial_artifacts(source_dir: Path) -> list[str]:
    """Return filenames of trial artifacts in *source_dir*.

    These are artifacts that ``assess_cmd`` recognises but does not
    consume — their presence is informational for the operator.
    """
    return sorted(p.name for p in source_dir.glob(_TRIAL_ARTIFACT_GLOB))


def _extract_trial_source_tags(filenames: list[str]) -> list[str]:
    """Extract source tags (e.g. ``trials-ctgov``) from trial artifact filenames.

    Given ``GENE.trials-clinicaltrials.artifact.json``, returns
    ``["trials-clinicaltrials"]``.
    """
    tags: list[str] = []
    for name in filenames:
        # Pattern: {slug}.{source_tag}.artifact.json
        # The source_tag is the part between the first dot and ".artifact.json".
        stem = name.removesuffix(".artifact.json")
        parts = stem.split(".", 1)
        if len(parts) == 2:
            tag = parts[1]
            if tag not in tags:
                tags.append(tag)
    return tags


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------


@click.group()
def differentiation() -> None:
    """Competitive differentiation assessment (three-dimension)."""


@differentiation.command("assess")
@click.argument("query_term")
@click.option("--concept", default=None, help="Concept ID (e.g. IC-001).")
@click.option(
    "--modality", default=None, help="Concept modality (e.g. small_molecule)."
)
@click.option(
    "--indication", default=None, help="Concept indication (e.g. solid_tumors)."
)
@click.option("--entity", default=None, help="Proposed entity identifier.")
@click.option(
    "--charter-constraint",
    "charter_constraints",
    multiple=True,
    help="Charter-level constraint (repeatable).",
)
@from_option
@out_option
@output_options
@pass_state
def assess_cmd(
    state: AppState,
    query_term: str,
    concept: str | None,
    modality: str | None,
    indication: str | None,
    entity: str | None,
    charter_constraints: tuple[str, ...],
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Assess competitive differentiation from stored patent search results.

    Reads patent data produced by ``dde patent search``.  Does NOT currently
    consume trial data from ``dde trials search/analyze``.

    Produces a three-dimension assessment (competitor activity,
    patentability, FTO).  The three dimensions are never collapsed into
    a single score.

    When the source directory contains trial artifacts that this command
    does not consume, a notice is emitted on stderr.

    Every FTO finding carries an explicit disclaimer that a public
    search is not formal legal clearance.  No network access.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slug(query_term)
    artifact_path = source_dir / f"{slug}.patent-google-patents.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no patent search artifact for {query_term!r}",
            remedy=f"run `dde patent search {query_term!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "patent artifact")
    if artifact.get("schema") != "dde.patent.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.patent.v1, got {artifact.get('schema')!r}",
        )

    patents = artifact.get("patents", [])

    # ------------------------------------------------------------------
    # Source-transparency: detect unconsumed trial artifacts (#98)
    # ------------------------------------------------------------------
    unconsumed_trial_files = _scan_unconsumed_trial_artifacts(source_dir)
    unconsumed_trial_tags = _extract_trial_source_tags(unconsumed_trial_files)

    if unconsumed_trial_files and not as_json:
        lines = [
            "Notice: source directory contains trial artifacts that "
            "this command does not consume:",
        ]
        for name in unconsumed_trial_files:
            lines.append(f"  - {name}")
        lines.append("Run `dde trials analyze` separately for clinical trial evidence.")
        for ln in lines:
            warn(ln)

    result = assess_competitive_differentiation(
        patents,
        query_term,
        modality=modality,
        indication=indication,
        entity=entity,
        charter_constraints=list(charter_constraints),
    )

    # Inject source-transparency metadata into the result.
    result["sources_used"] = [_SOURCE_PATENT]
    result["sources_available_but_unused"] = unconsumed_trial_tags

    # Build relays.
    relays: list[dict[str, str]] = []
    fto = result["dimensions"]["freedom_to_operate"]
    if fto["risk_level"] in ("moderate_risk", "high_risk"):
        relays.append(
            provenance.relay(
                "patent.fto_risk_identified",
                f"FTO risk identified for {query_term!r}: "
                f"{fto['risk_level']}. {FTO_DISCLAIMER}",
            )
        )

    comp = result["dimensions"]["competitor_activity"]
    if comp["density"] in ("moderate_activity", "high_activity"):
        relays.append(
            provenance.relay(
                "differentiation.crowded_landscape",
                f"Competitive landscape for {query_term!r} shows "
                f"{comp['density']}. Existing competitor activity is "
                "informational — a differentiated angle may still be "
                "viable. See coverage limits for search boundaries.",
            )
        )

    relays.append(
        provenance.relay(
            "differentiation.not_legal_clearance",
            FTO_DISCLAIMER,
        )
    )

    # Write analysis record first — it checks _may_write() internally
    # and raises Refusal if a differing analysis already exists. Writing
    # the primary assessment AFTER this check prevents inconsistent state
    # where the .differentiation.json is overwritten but the analysis
    # record is refused.
    out_path = target_dir / f"{slug}.differentiation.json"
    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.differentiation.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="differentiation",
        thresholds_applied={
            "recent_years": 5,
        },
        metrics={
            "competitor_density": comp["density"],
            "patentability": result["dimensions"]["patentability"][
                "novelty_assessment"
            ],
            "fto_risk": fto["risk_level"],
            "total_patents": comp["total_patents"],
            "recent_patents": comp["recent_patents"],
        },
        assessment={
            "dimensions": {
                "competitor_activity": comp["density"],
                "patentability": result["dimensions"]["patentability"][
                    "novelty_assessment"
                ],
                "freedom_to_operate": fto["risk_level"],
            },
            "dimensions_are_independent": True,
            "blended_score": None,
            "fto_disclaimer": FTO_DISCLAIMER,
        },
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    # Write assessment — only reached if provenance check passed.
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    # Emit output.
    dims = result["dimensions"]
    emit.data("query_term", query_term)
    emit.data("competitor_activity", dims["competitor_activity"]["density"])
    emit.data("patentability", dims["patentability"]["novelty_assessment"])
    emit.data("fto_risk", dims["freedom_to_operate"]["risk_level"])
    emit.data("dimensions_are_independent", True)
    emit.data("fto_disclaimer", FTO_DISCLAIMER)
    emit.data("relays", relays)

    if not as_json:
        emit.line(f"Competitive differentiation assessment for {query_term!r}")
        emit.line(f"  Competitor activity: {dims['competitor_activity']['density']}")
        emit.line(
            f"  Patentability:       {dims['patentability']['novelty_assessment']}"
        )
        emit.line(f"  FTO risk:            {dims['freedom_to_operate']['risk_level']}")
        emit.line("  Dimensions independent: YES (never blended)")
        emit.line(f"  FTO disclaimer: {FTO_DISCLAIMER}")

    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")

    emit.path(out_path, role="assessment")
    emit.path(analysis_path, role="analysis")
    emit.flush()


@differentiation.command("report")
@click.argument("query_term")
@click.option("--concept", required=True, help="Concept ID (e.g. IC-001 or IC-001-r2).")
@from_option
@out_option
@output_options
@pass_state
def report_cmd(
    state: AppState,
    query_term: str,
    concept: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Generate evidence-assessment records from a differentiation assessment.

    Reads the output of ``dde differentiation assess`` and produces
    ``dde.evidence-assessment.v1`` records for each dimension.
    No network access.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slug(query_term)
    assess_path = source_dir / f"{slug}.differentiation.json"
    if not assess_path.is_file():
        raise ArtifactError(
            f"no differentiation assessment for {query_term!r}",
            remedy=f"run `dde differentiation assess {query_term!r}` first",
        )

    differentiation_data = provenance.read_json(
        assess_path, "differentiation assessment"
    )
    if differentiation_data.get("schema") != "dde.competitive-differentiation.v1":
        raise SchemaError(
            f"unexpected schema in {assess_path.name}",
            detail=(
                "expected dde.competitive-differentiation.v1, "
                f"got {differentiation_data.get('schema')!r}"
            ),
        )

    records = build_assessment_records(differentiation_data, concept)

    # Write records as a batch file (caller assigns final IDs via controlstore).
    batch_path = target_dir / f"{slug}.differentiation-assessments.json"
    batch_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    emit.data("concept_ref", concept)
    emit.data("n_records", len(records))
    emit.data("records", records)

    if not as_json:
        emit.line(f"Generated {len(records)} assessment record(s) for {concept}")
        for r in records:
            emit.line(f"  - {r['claim'][:80]}...")

    emit.path(batch_path, role="assessment-records")
    emit.flush()
