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

"""Intervention-concept record schema, state machine, and validation.

An intervention concept is the central entity that policies gate and
evidence assesses.  It formalizes what is currently tracked informally
in ``active-series.md``.

Concept IDs follow the ``IC-NNN`` pattern (e.g. ``IC-001``).  Each
revision is an immutable snapshot; the latest revision is the current
state.  Record files are stored as ``IC-NNN-rN.json`` under
``.dde/control/concepts/``.

See the design document (tracker73-shared-contracts.md §2.1) for the
full schema rationale and §1.1 for the lifecycle state machine.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import Refusal

# ---------------------------------------------------------------------------
# Concept ID
# ---------------------------------------------------------------------------

CONCEPT_ID_RE = re.compile(r"^IC-\d{3,}$")
CONCEPT_RECORD_KEY_RE = re.compile(r"^IC-\d{3,}-r\d+$")

CONCEPT_SCHEMA = "dde.intervention-concept.v1"


# ---------------------------------------------------------------------------
# Concept state machine
# ---------------------------------------------------------------------------

CONCEPT_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"draft"},
    "draft": {"active", "withdrawn"},
    "active": {"under_review", "pivoting", "parked", "terminated"},
    "under_review": {"active", "pivoting", "parked", "terminated"},
    "pivoting": {"active", "terminated"},
    "parked": {"active", "terminated"},
    # Terminal states.
    "terminated": set(),
    "withdrawn": set(),
}

CONCEPT_STATES: set[str] = set()
for _src, _targets in CONCEPT_TRANSITIONS.items():
    if _src is not None:
        CONCEPT_STATES.add(_src)
    CONCEPT_STATES.update(_targets)

TERMINAL_CONCEPT_STATES: set[str] = {
    state
    for state, targets in CONCEPT_TRANSITIONS.items()
    if state is not None and not targets
}


# ---------------------------------------------------------------------------
# Biomarker assumption validation
# ---------------------------------------------------------------------------

BIOMARKER_CATEGORIES = {
    "patient_selection",
    "target_engagement",
    "response",
    "companion_diagnostic",
}

BIOMARKER_STATUSES = {"known", "unknown", "not_applicable"}


def validate_biomarker(entry: dict[str, Any], index: int) -> list[str]:
    """Validate a single biomarker assumption entry.

    Returns a list of validation error strings (empty if valid).
    """
    errors: list[str] = []
    prefix = f"biomarker_assumptions[{index}]"

    if not isinstance(entry, dict):
        return [f"{prefix}: must be a dict, got {type(entry).__name__}"]

    # Required fields.
    for field in ("name", "category", "status"):
        if field not in entry:
            errors.append(f"{prefix}: missing required field {field!r}")

    if "name" in entry and not isinstance(entry["name"], str):
        errors.append(f"{prefix}.name: must be a string")

    if "category" in entry:
        if entry["category"] not in BIOMARKER_CATEGORIES:
            errors.append(
                f"{prefix}.category: must be one of "
                f"{', '.join(sorted(BIOMARKER_CATEGORIES))}, "
                f"got {entry['category']!r}"
            )

    if "status" in entry:
        if entry["status"] not in BIOMARKER_STATUSES:
            errors.append(
                f"{prefix}.status: must be one of "
                f"{', '.join(sorted(BIOMARKER_STATUSES))}, "
                f"got {entry['status']!r}"
            )
        # Rationale required when status != "known".
        if entry["status"] in ("unknown", "not_applicable"):
            rationale = entry.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                errors.append(
                    f"{prefix}: rationale is required when status is "
                    f"{entry['status']!r}"
                )

    # Type validation: rationale, when present and non-null, must be a string.
    rationale = entry.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        errors.append(f"{prefix}.rationale: must be a string or null")

    return errors


# ---------------------------------------------------------------------------
# Key fields (revision triggers)
# ---------------------------------------------------------------------------

#: A material change to any of these fields requires a new revision.
#: Nested paths use dot notation for documentation; the actual check
#: extracts the nested value.
KEY_FIELDS: list[str] = [
    "disease_context.indication",
    "disease_context.patient_population",
    "target_pathway.gene",
    "target_pathway.protein",
    "target_pathway.mechanism_hypothesis",
    "modality",
    "delivery_assumptions",
]


def _get_nested(data: dict[str, Any], dotted_path: str) -> Any:
    """Extract a value from *data* using a dot-separated path."""
    parts = dotted_path.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def requires_new_revision(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """Return True if the change from *old* to *new* triggers a revision.

    A new revision is required when any key field has changed.
    ``delivery_assumptions`` triggers only when the new value is non-null
    (per design §2.1: "when non-null").

    **Call-site note (2026-09-08):** This function is intentionally not
    wired into ``validate_concept()`` or ``write_record()`` because no
    CLI command yet updates an existing concept's key fields in place.
    ``migrate-concepts`` creates new records from scratch (always r1),
    and there is no ``dde program update-concept`` command yet.  When
    such a command is added, it must call ``requires_new_revision()``
    to decide whether the update needs a new record key (``IC-NNN-rN+1``)
    or can overwrite the current one.  The function is tested and ready
    for that wiring; the deferral is an intentional scope boundary, not
    an oversight.
    """
    for field in KEY_FIELDS:
        old_val = _get_nested(old, field)
        new_val = _get_nested(new, field)
        if old_val != new_val:
            # delivery_assumptions only triggers when non-null.
            if field == "delivery_assumptions" and new_val is None:
                continue
            return True
    return False


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "schema",
    "id",
    "revision",
    "state",
    "disease_context",
    "target_pathway",
    "modality",
    "created_at",
]


def validate_concept(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid).

    This is the validator registered in ``controlstore._VALIDATORS``
    for the ``"concept"`` record type.
    """
    errors: list[str] = []

    # Required fields.
    missing = [f for f in _REQUIRED_FIELDS if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    # Schema string.
    if "schema" in data and data["schema"] != CONCEPT_SCHEMA:
        errors.append(f"schema must be {CONCEPT_SCHEMA!r}, got {data['schema']!r}")

    # ID format.
    if "id" in data and not CONCEPT_ID_RE.match(str(data["id"])):
        errors.append(f"id must match IC-NNN, got {data['id']!r}")

    # Revision.
    if "revision" in data:
        rev = data["revision"]
        if not isinstance(rev, int) or rev < 1:
            errors.append(f"revision must be a positive integer, got {rev!r}")

    # State.
    if "state" in data and data["state"] not in CONCEPT_STATES:
        errors.append(
            f"state {data['state']!r} is not a legal concept state; "
            f"legal states: {', '.join(sorted(CONCEPT_STATES))}"
        )

    # disease_context must be a dict with required sub-fields.
    if "disease_context" in data:
        dc = data["disease_context"]
        if not isinstance(dc, dict):
            errors.append(f"disease_context must be a dict, got {type(dc).__name__}")
        elif "indication" not in dc:
            errors.append("disease_context.indication is required")

    # target_pathway must be a dict with required sub-fields.
    if "target_pathway" in data:
        tp = data["target_pathway"]
        if not isinstance(tp, dict):
            errors.append(f"target_pathway must be a dict, got {type(tp).__name__}")
        elif "gene" not in tp:
            errors.append("target_pathway.gene is required")

    # modality must be a string or null (null = gap declared per §3.4).
    # The key is required (in _REQUIRED_FIELDS) but the value may be
    # None for migrated records that predate modality assignment.
    if (
        "modality" in data
        and data["modality"] is not None
        and not isinstance(data["modality"], str)
    ):
        errors.append(
            f"modality must be a string or null, got {type(data['modality']).__name__}"
        )

    # Biomarker assumptions.
    if "biomarker_assumptions" in data and data["biomarker_assumptions"] is not None:
        ba = data["biomarker_assumptions"]
        if not isinstance(ba, list):
            errors.append(
                f"biomarker_assumptions must be a list or null, got {type(ba).__name__}"
            )
        else:
            for i, entry in enumerate(ba):
                errors.extend(validate_biomarker(entry, i))

    # termination_authority enum.
    if "termination_authority" in data:
        ta = data["termination_authority"]
        if ta is not None and ta not in ("human", "program_lead"):
            errors.append(
                f"termination_authority must be 'human', 'program_lead', "
                f"or null, got {ta!r}"
            )

    # charter_ref type validation: when present and non-null, must be a string.
    if "charter_ref" in data and data["charter_ref"] is not None:
        if not isinstance(data["charter_ref"], str):
            errors.append(
                f"charter_ref must be a string or null, "
                f"got {type(data['charter_ref']).__name__}"
            )

    # ---------------------------------------------------------------
    # Charter-linkage gate (Refusal, not SchemaError).
    #
    # A concept in ``"active"`` state must have ``charter_ref`` set.
    # This is a governance prerequisite, not a data-quality issue:
    # the record is well-formed but the action is not permitted
    # without the charter link.  Raise ``Refusal`` (exit 9) so the
    # caller knows to set ``charter_ref`` and retry — the same
    # enforcement pattern as the human-approval gate on terminate
    # decisions (design §1.1, §2.1 "Charter linkage").
    # ---------------------------------------------------------------
    if "state" in data and data["state"] == "active":
        msg = check_charter_linkage(data, "active")
        if msg is not None:
            raise Refusal(
                msg,
                remedy="set charter_ref to the originating charter decision "
                "(e.g. 'DEC-001') before writing a concept in 'active' state",
            )

    return errors


# ---------------------------------------------------------------------------
# Charter-linkage gate
# ---------------------------------------------------------------------------


def check_charter_linkage(data: dict[str, Any], target_state: str) -> str | None:
    """Return an error message if the charter-linkage gate blocks transition.

    A concept cannot transition to ``"active"`` without ``charter_ref``
    being set to a valid charter decision identifier string.
    Returns ``None`` when the transition is permitted.
    """
    if target_state == "active":
        charter_ref = data.get("charter_ref")
        if not isinstance(charter_ref, str) or not charter_ref.strip():
            return (
                "concept cannot transition to 'active' without charter_ref; "
                "set charter_ref to the originating charter decision "
                "(e.g. 'DEC-001') before activating"
            )
    return None
