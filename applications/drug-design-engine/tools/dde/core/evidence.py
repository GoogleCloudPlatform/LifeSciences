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

"""Evidence assessment and decision record schemas.

Defines the shared type vocabulary for issue #75: evidence statuses,
execution outcomes, actions, the ``EvidenceReference`` dataclass, and
validators for assessment and decision records.

The validators are registered in ``controlstore.py``'s ``_VALIDATORS``
so that ``write_record("assessment", ...)`` and
``write_record("decision", ...)`` enforce schemas on every write.

The decision-record validator enforces the human-approval gate: a
``terminate`` decision on a concept whose ``termination_authority`` is
``"human"`` is rejected at write time with ``Refusal`` (exit 9) when
``human_approval`` is absent.  This matches the ``statemachine.py``
pattern of check-before-write, refuse-if-missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any

from .errors import Refusal

# ---------------------------------------------------------------------------
# Enum sets  (design SS1.2, SS1.3)
# ---------------------------------------------------------------------------

EVIDENCE_STATUSES = {
    "supported",  # Evidence actively supports the claim
    "contradicted",  # Evidence contradicts the claim
    "insufficient",  # Evidence exists but is not decisive
    "not_assessed",  # No assessment has been performed
    "not_yet_applicable",  # Cannot be assessed at this stage
}

EXECUTION_OUTCOMES = {
    "completed",  # Tool ran successfully; result is in the evidence status
    "tool_unavailable",  # Required tool is not in the environment
    "tool_failed",  # Tool ran and errored (exit != 0)
    "data_unavailable",  # Required upstream data does not exist
    "blocked",  # Dependency not met; cannot attempt
}

ACTIONS = {
    "advance_with_budget",  # Proceed to next stage/phase with specified budget
    "investigate",  # Gather more evidence before deciding
    "pivot",  # Change direction (within program or concept)
    "park",  # Suspend without termination; revisit later
    "terminate",  # End the concept/program element (human-gated)
}

# ---------------------------------------------------------------------------
# EvidenceReference dataclass  (design SS1.5)
# ---------------------------------------------------------------------------


@dataclass
class EvidenceReference:
    """A reference to a specific piece of evidence.

    This is the shared type that concepts, assessments, and policies
    all use to point at evidence.  It preserves the measurement context
    so that different measurement types cannot silently substitute.
    """

    artifact_path: str
    artifact_sha256: str | None = None
    evidence_type: str = ""
    metric_name: str | None = None
    metric_value: Any = None
    metric_units: str | None = None
    threshold_set: str | None = None
    method: str | None = None
    context: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceReference:
        """Create an EvidenceReference from a dict, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict, omitting None values for compactness."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }


# ---------------------------------------------------------------------------
# ID patterns
# ---------------------------------------------------------------------------

_AR_ID_RE = re.compile(r"^AR-\d{3,}$")
_DR_ID_RE = re.compile(r"^DR-\d{3,}$")

# entity_ref format patterns per entity_type  (design SS2.2 resolution table)
_ENTITY_REF_PATTERNS: dict[str, re.Pattern[str]] = {
    "concept": re.compile(r"^IC-\d{3,}(-r\d+)?$"),
    "claim": re.compile(r"^AR-\d{3,}$"),
    "program": re.compile(r"^DEC-\d{3,}$"),
    "series": re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$"),
}

_ENTITY_TYPES = set(_ENTITY_REF_PATTERNS.keys())

# Authorities that may bypass human approval for concept termination.
# Everything not in this set — including None, unrecognized strings,
# empty strings, and invalid types — requires human approval (fail-closed).
_AUTONOMOUS_AUTHORITIES: frozenset[str] = frozenset({"program_lead"})

# ---------------------------------------------------------------------------
# Assessment record validation  (design SS2.2, Appendix A.2)
# ---------------------------------------------------------------------------

_ASSESSMENT_REQUIRED_FIELDS = [
    "schema",
    "id",
    "concept_ref",
    "claim",
    "evidence_status",
    "execution_outcome",
    "assessed_at",
    "assessed_by",
]


def validate_assessment(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    errors: list[str] = []

    # Required fields
    missing = [f for f in _ASSESSMENT_REQUIRED_FIELDS if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    # Schema string
    schema = data.get("schema")
    if schema is not None and schema != "dde.evidence-assessment.v1":
        errors.append(f"schema must be 'dde.evidence-assessment.v1', got {schema!r}")

    # ID format
    if "id" in data and not _AR_ID_RE.match(str(data["id"])):
        errors.append(f"id must match AR-NNN, got {data['id']!r}")

    # Evidence status
    if "evidence_status" in data and data["evidence_status"] not in EVIDENCE_STATUSES:
        errors.append(
            f"evidence_status {data['evidence_status']!r} is not valid; "
            f"valid values: {', '.join(sorted(EVIDENCE_STATUSES))}"
        )

    # Execution outcome
    if (
        "execution_outcome" in data
        and data["execution_outcome"] not in EXECUTION_OUTCOMES
    ):
        errors.append(
            f"execution_outcome {data['execution_outcome']!r} is not valid; "
            f"valid values: {', '.join(sorted(EXECUTION_OUTCOMES))}"
        )

    # Mutual constraint: execution_outcome != "completed" => evidence_status == "not_assessed"
    exec_out = data.get("execution_outcome")
    ev_status = data.get("evidence_status")
    if (
        exec_out is not None
        and ev_status is not None
        and exec_out != "completed"
        and ev_status != "not_assessed"
    ):
        errors.append(
            f"when execution_outcome is {exec_out!r}, evidence_status must be "
            f"'not_assessed', got {ev_status!r}"
        )

    # Confidence (optional but constrained)
    confidence = data.get("confidence")
    if confidence is not None and confidence not in {"high", "moderate", "low"}:
        errors.append(
            f"confidence must be 'high', 'moderate', or 'low', got {confidence!r}"
        )

    # Supersedes format (optional)
    supersedes = data.get("supersedes")
    if supersedes is not None and not _AR_ID_RE.match(str(supersedes)):
        errors.append(f"supersedes must match AR-NNN, got {supersedes!r}")

    return errors


# ---------------------------------------------------------------------------
# Decision record validation  (design SS2.2, Appendix A.3)
# ---------------------------------------------------------------------------

_DECISION_REQUIRED_FIELDS = [
    "schema",
    "id",
    "action",
    "affected_entity",
    "rationale",
    "decided_at",
    "decided_by",
]

_HUMAN_APPROVAL_REQUIRED_FIELDS = [
    "approver",
    "approved_at",
    "approval_method",
]


def _validate_human_approval(approval: dict[str, Any]) -> list[str]:
    """Validate the human_approval sub-schema.

    Fail-closed: every required field must be present, must be a string,
    and must contain at least one non-whitespace character.  Empty or
    whitespace-only strings are rejected (issue #245).
    """
    errors: list[str] = []
    missing = [f for f in _HUMAN_APPROVAL_REQUIRED_FIELDS if f not in approval]
    if missing:
        errors.append(f"human_approval missing required fields: {', '.join(missing)}")
    for field in _HUMAN_APPROVAL_REQUIRED_FIELDS:
        if field in approval:
            value = approval[field]
            if not isinstance(value, str):
                errors.append(
                    f"human_approval.{field} must be a string, "
                    f"got {type(value).__name__}"
                )
            elif not value.strip():
                # Issue #245: reject empty/whitespace-only strings.
                # An autonomous agent must not bypass the gate by
                # supplying blank approval fields.
                errors.append(
                    f"human_approval.{field} must not be empty or whitespace-only"
                )
    return errors


def validate_decision(
    data: dict[str, Any],
    *,
    concept_loader: Any = None,
) -> list[str]:
    """Return a list of validation error strings (empty if valid).

    Parameters
    ----------
    data:
        The decision record dict.
    concept_loader:
        Optional callable ``(concept_id: str) -> dict | None`` that
        looks up a concept record to retrieve ``termination_authority``.
        When not provided, the validator checks ``human_approval``
        against the ``termination_authority`` field *on the decision
        record itself* (callers are expected to populate it when the
        concept's authority is ``"human"``).

    Raises
    ------
    Refusal
        If the decision is a ``terminate`` action requiring human
        approval and ``human_approval`` is missing.
    """
    errors: list[str] = []

    # Required fields
    missing = [f for f in _DECISION_REQUIRED_FIELDS if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    # Schema string
    schema = data.get("schema")
    if schema is not None and schema != "dde.decision-record.v1":
        errors.append(f"schema must be 'dde.decision-record.v1', got {schema!r}")

    # ID format
    if "id" in data and not _DR_ID_RE.match(str(data["id"])):
        errors.append(f"id must match DR-NNN, got {data['id']!r}")

    # Action
    action = data.get("action")
    if action is not None and action not in ACTIONS:
        errors.append(
            f"action {action!r} is not valid; "
            f"valid values: {', '.join(sorted(ACTIONS))}"
        )

    # affected_entity validation
    #
    # Issue #286: the guard must be key-presence, not is-not-None.
    # When affected_entity is explicitly set to None, the old
    # ``if entity is not None:`` skipped ALL validation, letting
    # the terminate gate resolve etype to None and bypass both
    # the concept and program termination gates.  Fail-closed:
    # if the key is present, it MUST be a dict.
    entity = data.get("affected_entity")
    if "affected_entity" in data:
        if not isinstance(entity, dict):
            errors.append("affected_entity must be a dict")
        else:
            etype = entity.get("entity_type")
            eref = entity.get("entity_ref")
            if etype is None:
                errors.append("affected_entity missing 'entity_type'")
            elif etype not in _ENTITY_TYPES:
                errors.append(
                    f"entity_type {etype!r} is not valid; "
                    f"valid values: {', '.join(sorted(_ENTITY_TYPES))}"
                )
            if eref is None:
                errors.append("affected_entity missing 'entity_ref'")
            elif etype in _ENTITY_REF_PATTERNS:
                pattern = _ENTITY_REF_PATTERNS[etype]
                if not pattern.match(str(eref)):
                    errors.append(
                        f"entity_ref {eref!r} does not match expected "
                        f"format for entity_type {etype!r}"
                    )

    # supporting_assessments format (optional)
    sa = data.get("supporting_assessments")
    if sa is not None:
        if not isinstance(sa, list):
            errors.append("supporting_assessments must be a list")
        else:
            for ref in sa:
                if not _AR_ID_RE.match(str(ref)):
                    errors.append(
                        f"supporting_assessments entry {ref!r} must match AR-NNN"
                    )

    # human_approval sub-schema validation (when present)
    approval = data.get("human_approval")
    if approval is not None:
        if not isinstance(approval, dict):
            errors.append("human_approval must be a dict")
        else:
            errors.extend(_validate_human_approval(approval))

    # ---------------------------------------------------------------
    # Human-approval gate  (design SS1.1, SS2.2 — review finding R1)
    #
    # This is the primary enforcement.  A terminate decision on a
    # concept or program MUST carry a populated human_approval when
    # the termination requires human authorization.
    #
    # Covered entity types:
    #   - "concept": gated by the concept record's termination_authority
    #     field, with safe-failure default of "human" when the concept
    #     cannot be looked up.
    #   - "program": always requires human approval.  Design principle
    #     #4 ("must not enable autonomous program termination") and
    #     program.yaml's human_reserved list (§4.4.1) both name
    #     program_termination as unconditionally human-reserved.
    #
    # Not covered (by design):
    #   - "series": series termination (deprioritizing a compound
    #     series) is within the program lead's normal authority and is
    #     not named in human_reserved.
    #   - "claim": claim termination (ceasing to pursue a claim) is
    #     an operational decision, not a program-level gate.
    # ---------------------------------------------------------------
    if action == "terminate" and not errors:
        etype = entity.get("entity_type") if isinstance(entity, dict) else None
        eref = entity.get("entity_ref") if isinstance(entity, dict) else None

        if etype == "concept":
            # Determine termination_authority from the concept record.
            term_auth = None

            # 1. If a concept_loader is provided, look up the concept.
            if concept_loader is not None and eref:
                concept_id = re.sub(r"-r\d+$", "", str(eref))
                concept = concept_loader(concept_id)
                if concept is not None:
                    term_auth = concept.get("termination_authority")

            # 2. Fail-closed allowlist: only explicitly authorized
            # roles may bypass human approval.  Design principle #4
            # requires that the system never enable autonomous
            # termination.  Everything not in the allowlist —
            # including None, unrecognized strings, empty strings,
            # and invalid types — requires human approval.
            if term_auth not in _AUTONOMOUS_AUTHORITIES and approval is None:
                detail_ref = eref or "(unknown)"
                raise Refusal(
                    "cannot terminate: human approval is required",
                    detail=(
                        f"concept {detail_ref!r} has "
                        f"termination_authority {term_auth!r} which is not in "
                        f"the set of authorities that may bypass human "
                        f"approval {sorted(_AUTONOMOUS_AUTHORITIES)}; the "
                        f"decision record must include a populated "
                        f"human_approval field"
                    ),
                    remedy=(
                        "obtain human approval for this termination and populate "
                        "the human_approval field (approver, approved_at, "
                        "approval_method) before retrying"
                    ),
                )

        elif etype == "program":
            # Program termination is unconditionally human-reserved.
            # Design principle #4: "must not enable autonomous program
            # termination."  program.yaml human_reserved (§4.4.1)
            # names "program_termination" explicitly.
            if approval is None:
                detail_ref = eref or "(unknown)"
                raise Refusal(
                    "cannot terminate program: human approval is required",
                    detail=(
                        f"program {detail_ref!r} termination requires human "
                        "approval per design principle #4 and program.yaml "
                        "human_reserved policy"
                    ),
                    remedy=(
                        "obtain human approval for this program termination "
                        "and populate the human_approval field (approver, "
                        "approved_at, approval_method) before retrying"
                    ),
                )

    return errors
