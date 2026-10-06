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

"""Pre-mortem review and evidence-backed dissent structures.

Implements issue #76: bounded pre-mortem review with scoped failure
hypotheses, review budgets, and four-type objection resolution.

This module provides validation for pre-mortem records and the
dissent-preservation check that ensures unresolved objections survive
gate-document generation.  It builds on #75's decision-record schema
(``evidence.py``) without adding new required fields — resolution
types are encoded in the existing ``conditions`` list field.

Resolution type encoding in ``conditions``
------------------------------------------
Each objection resolution is recorded as a structured condition string
on the decision record's ``conditions: list[str]`` field, using the
prefix ``objection_resolution:`` followed by the resolution type and
a reference to the objection::

    "objection_resolution:accepted:OBJ-001"
    "objection_resolution:rebutted:OBJ-002"
    "objection_resolution:accepted_risk:OBJ-003:GP-001"
    "objection_resolution:unresolved:OBJ-004:owner=computational-biologist"

This reuses the existing ``conditions`` field from the decision-record
schema (#75, already merged) without adding new required fields or
breaking backward compatibility.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import SchemaError

# ---------------------------------------------------------------------------
# Resolution types  (issue #76 AC4)
# ---------------------------------------------------------------------------

RESOLUTION_TYPES = {
    "accepted",  # Lead agrees — plan changes
    "rebutted",  # Lead disagrees — cites evidence
    "accepted_risk",  # Lead acknowledges risk but proceeds under policy
    "unresolved",  # Neither accepted nor rebutted — follow-up assigned
}

# Prefix used in decision-record ``conditions`` to encode resolutions.
_RESOLUTION_PREFIX = "objection_resolution:"

_RESOLUTION_RE = re.compile(
    r"^objection_resolution:"
    r"(accepted|rebutted|accepted_risk|unresolved)"
    r":([A-Z]+-\d{3,})"
    r"(?::(.+))?$"
)

# ---------------------------------------------------------------------------
# Failure hypothesis validation  (issue #76 AC2)
# ---------------------------------------------------------------------------

_HYPOTHESIS_REQUIRED = ["id", "description", "plausibility", "consequence"]
_PLAUSIBILITY_LEVELS = {"high", "moderate", "low"}


def validate_failure_hypothesis(data: dict[str, Any]) -> list[str]:
    """Validate a single failure hypothesis entry.

    A failure hypothesis must have: id, description, plausibility,
    consequence.  It may optionally include evidence and
    discriminating_check.

    A hypothesis *without* a ``discriminating_check`` is speculative.
    It is recorded but does not block progress.

    Returns a list of error strings (empty if valid).
    """
    errors: list[str] = []

    missing = [f for f in _HYPOTHESIS_REQUIRED if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    # ID format: OBJ-NNN
    obj_id = data.get("id")
    if obj_id is not None and not re.match(r"^OBJ-\d{3,}$", str(obj_id)):
        errors.append(f"id must match OBJ-NNN, got {obj_id!r}")

    # Plausibility
    plausibility = data.get("plausibility")
    if plausibility is not None and plausibility not in _PLAUSIBILITY_LEVELS:
        errors.append(
            f"plausibility must be one of {sorted(_PLAUSIBILITY_LEVELS)}, "
            f"got {plausibility!r}"
        )

    return errors


def is_speculative(hypothesis: dict[str, Any]) -> bool:
    """Return True if the hypothesis has no discriminating check.

    A speculative hypothesis is recorded but cannot gate progress.
    This is the structural enforcement of the rule that "a speculative
    narrative with no discriminating check is not an accepted fatal
    flaw."
    """
    check = hypothesis.get("discriminating_check")
    return check is None or (isinstance(check, str) and not check.strip())


# ---------------------------------------------------------------------------
# Pre-mortem record validation  (issue #76 AC2, AC3)
# ---------------------------------------------------------------------------

_PREMORTEM_REQUIRED = [
    "schema",
    "review_id",
    "finding_ref",
    "review_budget",
    "failure_hypotheses",
]


def validate_premortem(data: dict[str, Any]) -> list[str]:
    """Validate a complete pre-mortem review record.

    Required fields: schema, review_id, finding_ref, review_budget,
    failure_hypotheses.

    ``review_budget`` declares the maximum number of objections
    the lead will pursue.  The pre-mortem process is bounded: when
    the budget is exhausted, remaining unaddressed hypotheses are
    recorded as unresolved follow-ups, not silently dropped.

    Returns a list of error strings (empty if valid).
    """
    errors: list[str] = []

    # Required fields
    missing = [f for f in _PREMORTEM_REQUIRED if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    # Schema string
    schema = data.get("schema")
    if schema is not None and schema != "dde.premortem-review.v1":
        errors.append(f"schema must be 'dde.premortem-review.v1', got {schema!r}")

    # Review budget
    budget = data.get("review_budget")
    if budget is not None:
        if not isinstance(budget, int) or budget < 1:
            errors.append(f"review_budget must be a positive integer, got {budget!r}")

    # Failure hypotheses
    hypotheses = data.get("failure_hypotheses")
    if hypotheses is not None:
        if not isinstance(hypotheses, list):
            errors.append("failure_hypotheses must be a list")
        else:
            ids_seen: set[str] = set()
            for i, hyp in enumerate(hypotheses):
                if not isinstance(hyp, dict):
                    errors.append(f"failure_hypotheses[{i}] must be a dict")
                    continue
                hyp_errors = validate_failure_hypothesis(hyp)
                for e in hyp_errors:
                    errors.append(f"failure_hypotheses[{i}]: {e}")
                # Duplicate ID check
                hyp_id = hyp.get("id")
                if hyp_id is not None:
                    if hyp_id in ids_seen:
                        errors.append(
                            f"failure_hypotheses[{i}]: duplicate id {hyp_id!r}"
                        )
                    ids_seen.add(str(hyp_id))

    # Resolutions (optional — populated by the lead)
    resolutions = data.get("resolutions")
    if resolutions is not None:
        if not isinstance(resolutions, list):
            errors.append("resolutions must be a list")
        else:
            for i, res in enumerate(resolutions):
                if not isinstance(res, dict):
                    errors.append(f"resolutions[{i}] must be a dict")
                    continue
                res_errors = validate_objection_resolution(res)
                for e in res_errors:
                    errors.append(f"resolutions[{i}]: {e}")

    return errors


# ---------------------------------------------------------------------------
# Objection resolution validation  (issue #76 AC4)
# ---------------------------------------------------------------------------

_RESOLUTION_REQUIRED = ["objection_id", "resolution_type", "rationale"]


def validate_objection_resolution(data: dict[str, Any]) -> list[str]:
    """Validate a single objection resolution record.

    Each resolution must have: objection_id, resolution_type, rationale.

    For ``accepted_risk``: ``policy_ref`` is required (the policy
    under which the risk is accepted).

    For ``unresolved``: ``owner`` is required (who will investigate).

    For ``rebutted``: ``evidence_refs`` should be populated (the
    evidence supporting the rebuttal).

    Returns a list of error strings (empty if valid).
    """
    errors: list[str] = []

    missing = [f for f in _RESOLUTION_REQUIRED if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    # Resolution type
    rtype = data.get("resolution_type")
    if rtype is not None and rtype not in RESOLUTION_TYPES:
        errors.append(
            f"resolution_type must be one of {sorted(RESOLUTION_TYPES)}, got {rtype!r}"
        )

    # Type-specific requirements
    if rtype == "accepted_risk":
        if not data.get("policy_ref"):
            errors.append(
                "accepted_risk resolution requires a non-empty policy_ref "
                "naming the policy under which the risk is accepted"
            )

    if rtype == "unresolved":
        if not data.get("owner"):
            errors.append(
                "unresolved resolution requires a non-empty owner "
                "naming who will investigate the follow-up"
            )

    if rtype == "rebutted":
        evidence = data.get("evidence_refs")
        if not evidence:
            errors.append(
                "rebutted resolution should include evidence_refs "
                "citing the evidence that supports the rebuttal"
            )

    return errors


# ---------------------------------------------------------------------------
# Condition-string encoding for decision records  (issue #76)
# ---------------------------------------------------------------------------


def encode_resolution_condition(
    resolution_type: str,
    objection_id: str,
    *,
    policy_ref: str | None = None,
    owner: str | None = None,
) -> str:
    """Encode an objection resolution as a decision-record condition string.

    This encodes resolution information into the existing ``conditions``
    field of a decision record (#75 schema), avoiding new schema fields.

    Examples::

        encode_resolution_condition("accepted", "OBJ-001")
        # => "objection_resolution:accepted:OBJ-001"

        encode_resolution_condition("accepted_risk", "OBJ-003",
                                    policy_ref="GP-001")
        # => "objection_resolution:accepted_risk:OBJ-003:GP-001"

        encode_resolution_condition("unresolved", "OBJ-004",
                                    owner="computational-biologist")
        # => "objection_resolution:unresolved:OBJ-004:owner=computational-biologist"
    """
    if resolution_type not in RESOLUTION_TYPES:
        raise SchemaError(
            f"unknown resolution type {resolution_type!r}",
            detail=f"valid types: {', '.join(sorted(RESOLUTION_TYPES))}",
        )

    parts = [_RESOLUTION_PREFIX + resolution_type, objection_id]

    if resolution_type == "accepted_risk" and policy_ref:
        parts.append(policy_ref)
    elif resolution_type == "unresolved" and owner:
        parts.append(f"owner={owner}")

    return ":".join(parts)


def decode_resolution_condition(condition: str) -> dict[str, str] | None:
    """Parse an objection-resolution condition string.

    Returns a dict with keys ``resolution_type``, ``objection_id``,
    and optionally ``detail`` — or None if the string is not a
    resolution condition.
    """
    m = _RESOLUTION_RE.match(condition)
    if m is None:
        return None
    result: dict[str, str] = {
        "resolution_type": m.group(1),
        "objection_id": m.group(2),
    }
    if m.group(3):
        result["detail"] = m.group(3)
    return result


def extract_resolutions_from_decision(
    decision: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract all objection resolutions from a decision record's conditions.

    Scans ``decision["conditions"]`` for entries matching the
    ``objection_resolution:`` prefix and returns parsed resolution dicts.
    """
    conditions = decision.get("conditions")
    if not isinstance(conditions, list):
        return []
    results: list[dict[str, str]] = []
    for cond in conditions:
        if isinstance(cond, str):
            parsed = decode_resolution_condition(cond)
            if parsed is not None:
                results.append(parsed)
    return results


# ---------------------------------------------------------------------------
# Dissent preservation check  (issue #76 AC5)
# ---------------------------------------------------------------------------


def find_unresolved_objections(
    premortem: dict[str, Any],
    decision: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Identify failure hypotheses that remain unresolved.

    An objection is unresolved if:
    1. It is not speculative (has a discriminating check), AND
    2. It has no resolution in the pre-mortem's resolutions list, AND
    3. It has no resolution encoded in the decision record's conditions.

    Speculative hypotheses (no discriminating check) are always excluded
    — they are recorded but never gate progress.

    Returns a list of hypothesis dicts that are unresolved.
    """
    hypotheses = premortem.get("failure_hypotheses", [])
    resolutions = premortem.get("resolutions", [])

    # Collect resolved objection IDs from pre-mortem resolutions
    resolved_ids: set[str] = set()
    for res in resolutions:
        obj_id = res.get("objection_id")
        if obj_id:
            resolved_ids.add(obj_id)

    # Also check decision record conditions
    if decision is not None:
        for parsed in extract_resolutions_from_decision(decision):
            resolved_ids.add(parsed["objection_id"])

    unresolved: list[dict[str, Any]] = []
    for hyp in hypotheses:
        if not isinstance(hyp, dict):
            continue
        hyp_id = hyp.get("id")
        if hyp_id is None:
            continue
        # Skip speculative hypotheses — they don't gate
        if is_speculative(hyp):
            continue
        # Check if resolved
        if hyp_id not in resolved_ids:
            unresolved.append(hyp)

    return unresolved


def check_dissent_in_liabilities(
    unresolved_objections: list[dict[str, Any]],
    liability_entries: list[dict[str, Any]],
) -> list[str]:
    """Verify every unresolved objection has a liability-tracker entry.

    Returns a list of error strings for objections missing from the
    liability tracker.  Empty list means all unresolved objections
    are properly tracked.

    Each liability entry is expected to have a ``source`` field that
    references the objection ID (e.g. "pre-mortem OBJ-001").
    """
    # Build a set of objection IDs mentioned in liability entries
    tracked_ids: set[str] = set()
    for entry in liability_entries:
        source = str(entry.get("source", ""))
        # Look for OBJ-NNN pattern in the source string
        for m in re.finditer(r"OBJ-\d{3,}", source):
            tracked_ids.add(m.group(0))
        # Also check liability name/description
        name = str(entry.get("name", ""))
        for m in re.finditer(r"OBJ-\d{3,}", name):
            tracked_ids.add(m.group(0))

    errors: list[str] = []
    for obj in unresolved_objections:
        obj_id = obj.get("id", "unknown")
        if obj_id not in tracked_ids:
            errors.append(
                f"unresolved objection {obj_id} "
                f"({obj.get('description', 'no description')}) "
                "has no corresponding entry in liability-tracker"
            )
    return errors


def generate_gate_dissent_section(
    unresolved_objections: list[dict[str, Any]],
    resolutions: list[dict[str, Any]] | None = None,
) -> str:
    """Generate the dissent section for a gate document.

    This function produces the markdown content that must appear in
    gate documents to preserve unresolved objections and dissent.
    The content is derived from the decision state, not editorially
    curated — "presentation is derived from the decision, not its
    authority."

    Parameters
    ----------
    unresolved_objections:
        Failure hypotheses with no resolution (from
        ``find_unresolved_objections``).
    resolutions:
        All resolution records (including resolved objections),
        for the full dissent picture.

    Returns
    -------
    str
        Markdown text for the gate document's dissent section.
        Empty string if there are no objections to report.
    """
    parts: list[str] = []

    if unresolved_objections:
        parts.append("### Unresolved Objections\n")
        parts.append(
            "The following objections from pre-mortem review remain "
            "unresolved at the time of this gate decision. Each has a "
            "discriminating check that has not been executed or resolved.\n"
        )
        for obj in unresolved_objections:
            obj_id = obj.get("id", "unknown")
            desc = obj.get("description", "No description")
            plaus = obj.get("plausibility", "unspecified")
            consequence = obj.get("consequence", "unspecified")
            check = obj.get("discriminating_check", "none")
            parts.append(f"- **{obj_id}**: {desc}")
            parts.append(f"  - Plausibility: {plaus}")
            parts.append(f"  - Consequence: {consequence}")
            parts.append(f"  - Discriminating check: {check}")
            parts.append("")

    if resolutions:
        accepted_risk = [
            r for r in resolutions if r.get("resolution_type") == "accepted_risk"
        ]
        if accepted_risk:
            parts.append("### Accepted Risks\n")
            parts.append(
                "The following objections were acknowledged as risks "
                "and accepted under policy.\n"
            )
            for res in accepted_risk:
                obj_id = res.get("objection_id", "unknown")
                rationale = res.get("rationale", "No rationale")
                policy = res.get("policy_ref", "unspecified")
                parts.append(f"- **{obj_id}**: {rationale}")
                parts.append(f"  - Policy: {policy}")
                parts.append("")

    if not parts:
        return ""

    return "## Pre-Mortem Dissent Record\n\n" + "\n".join(parts)
