#!/usr/bin/env python3
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

"""Comprehensive tests for intervention-concept records (#74).

Covers:
  - Concept state machine transitions, including terminal states and
    the ``pivoting`` gate
  - Revision-trigger logic: changing a key field creates a new revision;
    changing a non-key field does not
  - Biomarker assumption validation: all 4 categories, rationale required
    when status != "known"
  - Charter-linkage gate: ``draft`` -> ``active`` fails without
    ``charter_ref``
  - Backward compatibility: an empty/missing concepts directory does not
    break existing control-store operations
  - Full validation round-trip using design §7 Step 1's concept record
    as a fixture
  - Control store registration: "concept" in RECORD_TYPES, validator
    registered
  - State machine registration: validate_transition("concept", ...) works
  - next_id generation for concepts

Run with:
    PYTHONPATH=tools python3 tests/test_concepts.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.core.concepts import (
    BIOMARKER_CATEGORIES,
    CONCEPT_ID_RE,
    CONCEPT_RECORD_KEY_RE,
    CONCEPT_SCHEMA,
    CONCEPT_STATES,
    CONCEPT_TRANSITIONS,
    TERMINAL_CONCEPT_STATES,
    check_charter_linkage,
    requires_new_revision,
    validate_biomarker,
    validate_concept,
)
from dde.core.controlstore import (
    _VALIDATORS,
    CONTROL_DIR,
    RECORD_TYPES,
    ensure_control_dirs,
    list_records,
    next_id,
    read_record,
    write_record,
)
from dde.core.errors import Refusal, SchemaError
from dde.core.statemachine import _MACHINES, validate_transition

# ---------------------------------------------------------------------------
# Fixture: design §7 Step 1 concept record
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

WORKED_EXAMPLE_CONCEPT: dict[str, Any] = {
    "schema": "dde.intervention-concept.v1",
    "id": "IC-001",
    "revision": 1,
    "state": "active",
    "disease_context": {
        "indication": "solid_tumors",
        "stage": "preclinical",
        "patient_population": None,
    },
    "target_pathway": {
        "gene": "CDK4",
        "protein": "CDK4",
        "pathway": "Rb/E2F cell cycle regulation",
        "mechanism_hypothesis": "CDK4 inhibition restores Rb-mediated cell cycle arrest",
    },
    "modality": "small_molecule",
    "charter_ref": "DEC-001",
    "hypothesis_refs": ["raw/hypotheses/cdk4-targets.adopted.json"],
    "applicable_policies": ["GP-001@1"],
    "termination_authority": "human",
    "created_at": "2026-09-08T14:00:00Z",
}


def _make_minimal_concept(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid concept record with optional overrides."""
    record: dict[str, Any] = {
        "schema": CONCEPT_SCHEMA,
        "id": "IC-001",
        "revision": 1,
        "state": "draft",
        "disease_context": {"indication": "oncology"},
        "target_pathway": {"gene": "CDK4"},
        "modality": "small_molecule",
        "created_at": _NOW,
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _run(name: str, fn: Any) -> None:
    global _PASS, _FAIL
    try:
        fn()
        _PASS += 1
        print(f"  PASS  {name}")
    except Exception:
        _FAIL += 1
        print(f"  FAIL  {name}")
        traceback.print_exc()
        print()


# ---------------------------------------------------------------------------
# Tests: Concept ID format
# ---------------------------------------------------------------------------


def test_concept_id_regex() -> None:
    assert CONCEPT_ID_RE.match("IC-001")
    assert CONCEPT_ID_RE.match("IC-042")
    assert CONCEPT_ID_RE.match("IC-1234")
    assert not CONCEPT_ID_RE.match("IC-01")  # need 3+ digits
    assert not CONCEPT_ID_RE.match("WO-001")
    assert not CONCEPT_ID_RE.match("ic-001")
    assert not CONCEPT_ID_RE.match("")


def test_concept_record_key_regex() -> None:
    assert CONCEPT_RECORD_KEY_RE.match("IC-001-r1")
    assert CONCEPT_RECORD_KEY_RE.match("IC-042-r3")
    assert not CONCEPT_RECORD_KEY_RE.match("IC-001")
    assert not CONCEPT_RECORD_KEY_RE.match("WO-001-r1")


# ---------------------------------------------------------------------------
# Tests: State machine transitions
# ---------------------------------------------------------------------------


def test_concept_transitions_initial() -> None:
    """Only 'draft' is reachable from None (initial state)."""
    assert CONCEPT_TRANSITIONS[None] == {"draft"}


def test_concept_transitions_draft() -> None:
    assert CONCEPT_TRANSITIONS["draft"] == {"active", "withdrawn"}


def test_concept_transitions_active() -> None:
    assert CONCEPT_TRANSITIONS["active"] == {
        "under_review",
        "pivoting",
        "parked",
        "terminated",
    }


def test_concept_transitions_pivoting() -> None:
    """Pivoting can only go to active or terminated."""
    assert CONCEPT_TRANSITIONS["pivoting"] == {"active", "terminated"}


def test_concept_transitions_terminal() -> None:
    """Terminal states have no outgoing transitions."""
    assert CONCEPT_TRANSITIONS["terminated"] == set()
    assert CONCEPT_TRANSITIONS["withdrawn"] == set()


def test_terminal_concept_states() -> None:
    assert "terminated" in TERMINAL_CONCEPT_STATES
    assert "withdrawn" in TERMINAL_CONCEPT_STATES
    assert "active" not in TERMINAL_CONCEPT_STATES


def test_all_states_derived() -> None:
    """CONCEPT_STATES should contain all states mentioned in transitions."""
    expected = {
        "draft",
        "active",
        "under_review",
        "pivoting",
        "parked",
        "terminated",
        "withdrawn",
    }
    assert CONCEPT_STATES == expected


# ---------------------------------------------------------------------------
# Tests: State machine registered in statemachine.py
# ---------------------------------------------------------------------------


def test_concept_registered_in_machines() -> None:
    assert "concept" in _MACHINES
    assert _MACHINES["concept"] is CONCEPT_TRANSITIONS


def test_validate_transition_concept_legal() -> None:
    """Legal transitions should not raise."""
    validate_transition("concept", None, "draft")
    validate_transition("concept", "draft", "active")
    validate_transition("concept", "active", "pivoting")
    validate_transition("concept", "pivoting", "active")
    validate_transition("concept", "pivoting", "terminated")


def test_validate_transition_concept_illegal() -> None:
    """Illegal transitions should raise Refusal."""
    try:
        validate_transition("concept", "draft", "terminated")
        assert False, "should have raised Refusal"
    except Refusal:
        pass

    try:
        validate_transition("concept", "terminated", "active")
        assert False, "should have raised Refusal"
    except Refusal:
        pass

    try:
        validate_transition("concept", "withdrawn", "draft")
        assert False, "should have raised Refusal"
    except Refusal:
        pass


# ---------------------------------------------------------------------------
# Tests: Concept validation
# ---------------------------------------------------------------------------


def test_validate_minimal_concept() -> None:
    record = _make_minimal_concept()
    errors = validate_concept(record)
    assert errors == [], f"unexpected errors: {errors}"


def test_validate_worked_example() -> None:
    """The §7 Step 1 worked example should pass validation."""
    errors = validate_concept(WORKED_EXAMPLE_CONCEPT)
    assert errors == [], f"unexpected errors: {errors}"


def test_validate_missing_required_fields() -> None:
    errors = validate_concept({})
    assert any("missing required fields" in e for e in errors)


def test_validate_bad_schema() -> None:
    record = _make_minimal_concept(schema="wrong.schema.v1")
    errors = validate_concept(record)
    assert any("schema must be" in e for e in errors)


def test_validate_bad_id() -> None:
    record = _make_minimal_concept(id="WO-001")
    errors = validate_concept(record)
    assert any("id must match IC-NNN" in e for e in errors)


def test_validate_bad_revision() -> None:
    record = _make_minimal_concept(revision=0)
    errors = validate_concept(record)
    assert any("revision must be a positive integer" in e for e in errors)

    record2 = _make_minimal_concept(revision="one")
    errors2 = validate_concept(record2)
    assert any("revision must be a positive integer" in e for e in errors2)


def test_validate_bad_state() -> None:
    record = _make_minimal_concept(state="nonexistent")
    errors = validate_concept(record)
    assert any("not a legal concept state" in e for e in errors)


def test_validate_disease_context_not_dict() -> None:
    record = _make_minimal_concept(disease_context="string")
    errors = validate_concept(record)
    assert any("disease_context must be a dict" in e for e in errors)


def test_validate_disease_context_missing_indication() -> None:
    record = _make_minimal_concept(disease_context={"stage": "preclinical"})
    errors = validate_concept(record)
    assert any("disease_context.indication is required" in e for e in errors)


def test_validate_target_pathway_not_dict() -> None:
    record = _make_minimal_concept(target_pathway="CDK4")
    errors = validate_concept(record)
    assert any("target_pathway must be a dict" in e for e in errors)


def test_validate_target_pathway_missing_gene() -> None:
    record = _make_minimal_concept(target_pathway={"protein": "CDK4"})
    errors = validate_concept(record)
    assert any("target_pathway.gene is required" in e for e in errors)


def test_validate_modality_not_string() -> None:
    record = _make_minimal_concept(modality=42)
    errors = validate_concept(record)
    assert any("modality must be a string or null" in e for e in errors)


def test_validate_modality_null_ok() -> None:
    """Null modality is valid — gap declared as null per §3.4."""
    record = _make_minimal_concept(modality=None)
    errors = validate_concept(record)
    assert not any("modality" in e for e in errors)


def test_validate_termination_authority_enum() -> None:
    # Valid values.
    for val in ("human", "program_lead", None):
        record = _make_minimal_concept(termination_authority=val)
        errors = validate_concept(record)
        assert not any("termination_authority" in e for e in errors), (
            f"unexpected error for termination_authority={val!r}: {errors}"
        )

    # Invalid value.
    record = _make_minimal_concept(termination_authority="auto")
    errors = validate_concept(record)
    assert any("termination_authority" in e for e in errors)


# ---------------------------------------------------------------------------
# Tests: Biomarker validation
# ---------------------------------------------------------------------------


def test_biomarker_all_categories() -> None:
    """All 4 categories are accepted."""
    for cat in BIOMARKER_CATEGORIES:
        entry = {"name": "test", "category": cat, "status": "known"}
        errors = validate_biomarker(entry, 0)
        assert errors == [], f"category {cat!r} failed: {errors}"


def test_biomarker_invalid_category() -> None:
    entry = {"name": "test", "category": "invalid", "status": "known"}
    errors = validate_biomarker(entry, 0)
    assert any("category" in e for e in errors)


def test_biomarker_known_no_rationale_needed() -> None:
    entry = {"name": "PD-L1", "category": "patient_selection", "status": "known"}
    errors = validate_biomarker(entry, 0)
    assert errors == []


def test_biomarker_unknown_requires_rationale() -> None:
    entry = {
        "name": "CDK4 amp",
        "category": "target_engagement",
        "status": "unknown",
    }
    errors = validate_biomarker(entry, 0)
    assert any("rationale is required" in e for e in errors)


def test_biomarker_not_applicable_requires_rationale() -> None:
    entry = {
        "name": "companion",
        "category": "companion_diagnostic",
        "status": "not_applicable",
    }
    errors = validate_biomarker(entry, 0)
    assert any("rationale is required" in e for e in errors)


def test_biomarker_unknown_with_rationale_passes() -> None:
    entry = {
        "name": "CDK4 amp",
        "category": "target_engagement",
        "status": "unknown",
        "rationale": "No validated assay available yet",
    }
    errors = validate_biomarker(entry, 0)
    assert errors == []


def test_biomarker_not_applicable_with_rationale_passes() -> None:
    entry = {
        "name": "companion",
        "category": "companion_diagnostic",
        "status": "not_applicable",
        "rationale": "Not pursuing companion diagnostic at this stage",
    }
    errors = validate_biomarker(entry, 0)
    assert errors == []


def test_biomarker_empty_rationale_rejected() -> None:
    """An empty or whitespace-only rationale should be rejected."""
    entry = {
        "name": "test",
        "category": "response",
        "status": "unknown",
        "rationale": "   ",
    }
    errors = validate_biomarker(entry, 0)
    assert any("rationale is required" in e for e in errors)


def test_validate_concept_with_biomarkers() -> None:
    """Biomarker validation is integrated into concept validation."""
    record = _make_minimal_concept(
        biomarker_assumptions=[
            {"name": "PD-L1", "category": "patient_selection", "status": "known"},
            {
                "name": "CDK4 amp",
                "category": "target_engagement",
                "status": "unknown",
                # Missing rationale — should trigger an error.
            },
        ],
    )
    errors = validate_concept(record)
    assert any("rationale is required" in e for e in errors)


def test_validate_concept_null_biomarkers_ok() -> None:
    """Null biomarker_assumptions is valid (optional field)."""
    record = _make_minimal_concept(biomarker_assumptions=None)
    errors = validate_concept(record)
    assert not any("biomarker" in e for e in errors)


def test_validate_concept_biomarkers_not_list() -> None:
    record = _make_minimal_concept(biomarker_assumptions="not a list")
    errors = validate_concept(record)
    assert any("biomarker_assumptions must be a list or null" in e for e in errors)


# ---------------------------------------------------------------------------
# Tests: Revision triggers
# ---------------------------------------------------------------------------


def test_key_field_change_triggers_revision() -> None:
    old = _make_minimal_concept()
    new = _make_minimal_concept()
    new["disease_context"] = {"indication": "hematology"}
    assert requires_new_revision(old, new) is True


def test_gene_change_triggers_revision() -> None:
    old = _make_minimal_concept()
    new = _make_minimal_concept()
    new["target_pathway"] = {"gene": "TP53"}
    assert requires_new_revision(old, new) is True


def test_modality_change_triggers_revision() -> None:
    old = _make_minimal_concept()
    new = _make_minimal_concept(modality="biologic")
    assert requires_new_revision(old, new) is True


def test_mechanism_hypothesis_change_triggers_revision() -> None:
    old = _make_minimal_concept()
    old["target_pathway"]["mechanism_hypothesis"] = "hypothesis A"
    new = _make_minimal_concept()
    new["target_pathway"]["mechanism_hypothesis"] = "hypothesis B"
    assert requires_new_revision(old, new) is True


def test_delivery_assumptions_non_null_triggers_revision() -> None:
    old = _make_minimal_concept(delivery_assumptions=None)
    new = _make_minimal_concept(
        delivery_assumptions={"route": "oral", "formulation": "tablet"},
    )
    assert requires_new_revision(old, new) is True


def test_delivery_assumptions_to_null_does_not_trigger() -> None:
    """Per design: delivery_assumptions triggers only 'when non-null'."""
    old = _make_minimal_concept(
        delivery_assumptions={"route": "oral"},
    )
    new = _make_minimal_concept(delivery_assumptions=None)
    assert requires_new_revision(old, new) is False


def test_non_key_field_change_no_revision() -> None:
    old = _make_minimal_concept(notes="initial")
    new = _make_minimal_concept(notes="updated notes")
    assert requires_new_revision(old, new) is False


def test_work_order_refs_change_no_revision() -> None:
    old = _make_minimal_concept(work_order_refs=["WO-001"])
    new = _make_minimal_concept(work_order_refs=["WO-001", "WO-002"])
    assert requires_new_revision(old, new) is False


def test_decision_log_refs_change_no_revision() -> None:
    old = _make_minimal_concept(decision_log_refs=["DEC-001"])
    new = _make_minimal_concept(decision_log_refs=["DEC-001", "DEC-002"])
    assert requires_new_revision(old, new) is False


def test_patient_population_change_triggers_revision() -> None:
    old = _make_minimal_concept()
    old["disease_context"]["patient_population"] = "adult"
    new = _make_minimal_concept()
    new["disease_context"]["patient_population"] = "pediatric"
    assert requires_new_revision(old, new) is True


# ---------------------------------------------------------------------------
# Tests: Charter-linkage gate
# ---------------------------------------------------------------------------


def test_charter_linkage_blocks_activation() -> None:
    """Cannot go active without charter_ref."""
    data = _make_minimal_concept(charter_ref=None)
    msg = check_charter_linkage(data, "active")
    assert msg is not None
    assert "charter_ref" in msg


def test_charter_linkage_empty_string_blocks() -> None:
    data = _make_minimal_concept(charter_ref="")
    msg = check_charter_linkage(data, "active")
    assert msg is not None


def test_charter_linkage_whitespace_blocks() -> None:
    data = _make_minimal_concept(charter_ref="   ")
    msg = check_charter_linkage(data, "active")
    assert msg is not None


def test_charter_linkage_set_allows_activation() -> None:
    data = _make_minimal_concept(charter_ref="DEC-001")
    msg = check_charter_linkage(data, "active")
    assert msg is None


def test_charter_linkage_not_checked_for_other_states() -> None:
    """Charter linkage only gates the 'active' transition."""
    data = _make_minimal_concept(charter_ref=None)
    for target in ("under_review", "pivoting", "parked", "terminated", "withdrawn"):
        msg = check_charter_linkage(data, target)
        assert msg is None, f"unexpected block for target={target!r}"


# ---------------------------------------------------------------------------
# Tests: Charter-linkage gate wired into validate_concept (Refusal)
# ---------------------------------------------------------------------------


def test_validate_concept_active_without_charter_raises_refusal() -> None:
    """validate_concept() raises Refusal for active state without charter_ref.

    This is the wired-in enforcement, not just the isolated function.
    """
    record = _make_minimal_concept(state="active", charter_ref=None)
    try:
        validate_concept(record)
        assert False, "should have raised Refusal"
    except Refusal as exc:
        assert "charter_ref" in exc.message
        assert exc.exit_code == 9


def test_validate_concept_active_with_charter_no_refusal() -> None:
    """Active concept with charter_ref should not raise Refusal."""
    record = _make_minimal_concept(state="active", charter_ref="DEC-001")
    errors = validate_concept(record)
    assert errors == [], f"unexpected errors: {errors}"


def test_validate_concept_draft_without_charter_no_refusal() -> None:
    """Draft concept without charter_ref is fine — gate only applies to active."""
    record = _make_minimal_concept(state="draft", charter_ref=None)
    errors = validate_concept(record)
    assert not any("charter" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Tests: Charter-linkage through the REAL write_record() path
# ---------------------------------------------------------------------------


def test_write_record_active_concept_without_charter_raises_refusal() -> None:
    """write_record() must raise Refusal for active concept without charter_ref.

    This is the critical integration test: the real, unmodified
    write_record() -> validate_concept() path must enforce the
    charter-linkage gate with Refusal (exit 9), not SchemaError.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        record = _make_minimal_concept(state="active", charter_ref=None)
        try:
            write_record(root, "concept", "IC-001-r1", record)
            assert False, "should have raised Refusal"
        except Refusal as exc:
            assert exc.exit_code == 9
            assert "charter_ref" in exc.message
        except SchemaError:
            assert False, (
                "got SchemaError instead of Refusal — the charter-linkage "
                "gate is not wired in with the correct error class"
            )


def test_write_record_active_concept_with_charter_succeeds() -> None:
    """write_record() succeeds for active concept with charter_ref set."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        record = _make_minimal_concept(state="active", charter_ref="DEC-001")
        path = write_record(root, "concept", "IC-001-r1", record)
        assert path.is_file()

        read_back = read_record(root, "concept", "IC-001-r1")
        assert read_back["state"] == "active"
        assert read_back["charter_ref"] == "DEC-001"


def test_write_record_active_concept_empty_charter_raises_refusal() -> None:
    """Empty-string charter_ref on active concept: Refusal through write_record."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        record = _make_minimal_concept(state="active", charter_ref="")
        try:
            write_record(root, "concept", "IC-001-r1", record)
            assert False, "should have raised Refusal"
        except Refusal as exc:
            assert exc.exit_code == 9


def test_write_record_draft_concept_without_charter_succeeds() -> None:
    """Draft concept without charter_ref writes successfully — no gate."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        record = _make_minimal_concept(state="draft", charter_ref=None)
        path = write_record(root, "concept", "IC-001-r1", record)
        assert path.is_file()


# ---------------------------------------------------------------------------
# Tests: Migration-shaped record through real write_record() path
# ---------------------------------------------------------------------------


def test_write_record_migration_shaped_record() -> None:
    """A migration-shaped record (modality=None, gaps as null) must write
    successfully through the real write_record() path.

    This is the integration test for the migrate-concepts -> write_record
    -> validate_concept chain.  The record mirrors exactly what
    _propose_concept_record() produces: draft state, modality=None,
    indication=None, most optional fields null.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        migration_record = {
            "schema": CONCEPT_SCHEMA,
            "id": "IC-001",
            "revision": 1,
            "state": "draft",
            "disease_context": {
                "indication": None,
                "stage": None,
                "patient_population": None,
            },
            "target_pathway": {
                "gene": "CDK4",
                "protein": None,
                "pathway": None,
                "mechanism_hypothesis": None,
            },
            "modality": None,
            "entity_ref": None,
            "delivery_assumptions": None,
            "biomarker_assumptions": None,
            "charter_ref": None,
            "hypothesis_refs": None,
            "work_order_refs": None,
            "decision_log_refs": None,
            "applicable_policies": None,
            "termination_authority": None,
            "notes": "Migrated from active-series.md entry: CDK4",
            "created_at": _NOW,
        }

        # Must succeed — no SchemaError, no Refusal.
        path = write_record(root, "concept", "IC-001-r1", migration_record)
        assert path.is_file()

        read_back = read_record(root, "concept", "IC-001-r1")
        assert read_back["modality"] is None
        assert read_back["state"] == "draft"
        assert read_back["disease_context"]["indication"] is None
        assert read_back["charter_ref"] is None


# ---------------------------------------------------------------------------
# Tests: Control store registration
# ---------------------------------------------------------------------------


def test_concept_in_record_types() -> None:
    assert "concept" in RECORD_TYPES
    assert RECORD_TYPES["concept"] == "concepts"


def test_concept_validator_registered() -> None:
    assert "concept" in _VALIDATORS
    assert _VALIDATORS["concept"] is validate_concept


# ---------------------------------------------------------------------------
# Tests: Control store round-trip (integration)
# ---------------------------------------------------------------------------


def test_write_and_read_concept() -> None:
    """Full write-read round-trip through the control store."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        record = _make_minimal_concept()
        written_path = write_record(root, "concept", "IC-001-r1", record)
        assert written_path.is_file()

        read_back = read_record(root, "concept", "IC-001-r1")
        assert read_back["id"] == "IC-001"
        assert read_back["schema"] == CONCEPT_SCHEMA


def test_write_concept_validation_failure() -> None:
    """Writing an invalid concept should raise SchemaError."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        # Missing required fields.
        try:
            write_record(root, "concept", "bad-001", {})
            assert False, "should have raised SchemaError"
        except SchemaError:
            pass


def test_list_concepts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        r1 = _make_minimal_concept(id="IC-001")
        r2 = _make_minimal_concept(id="IC-002")
        write_record(root, "concept", "IC-001-r1", r1)
        write_record(root, "concept", "IC-002-r1", r2)

        all_records = list_records(root, "concept")
        assert len(all_records) == 2

        # Filter.
        filtered = list_records(
            root,
            "concept",
            filter_fn=lambda r: r.get("id") == "IC-002",
        )
        assert len(filtered) == 1
        assert filtered[0]["id"] == "IC-002"


def test_worked_example_round_trip() -> None:
    """The §7 Step 1 worked example should round-trip through the store."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        write_record(root, "concept", "IC-001-r1", WORKED_EXAMPLE_CONCEPT)
        read_back = read_record(root, "concept", "IC-001-r1")

        assert read_back["id"] == "IC-001"
        assert read_back["revision"] == 1
        assert read_back["state"] == "active"
        assert read_back["modality"] == "small_molecule"
        assert read_back["charter_ref"] == "DEC-001"
        assert read_back["target_pathway"]["gene"] == "CDK4"
        assert read_back["disease_context"]["indication"] == "solid_tumors"
        assert read_back["termination_authority"] == "human"


# ---------------------------------------------------------------------------
# Tests: next_id for concepts
# ---------------------------------------------------------------------------


def test_next_id_concept_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)
        assert next_id(root, "concept") == "IC-001"


def test_next_id_concept_increments() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)
        write_record(root, "concept", "IC-001-r1", _make_minimal_concept(id="IC-001"))
        write_record(root, "concept", "IC-002-r1", _make_minimal_concept(id="IC-002"))
        assert next_id(root, "concept") == "IC-003"


# ---------------------------------------------------------------------------
# Tests: Backward compatibility
# ---------------------------------------------------------------------------


def test_empty_concepts_dir_no_breakage() -> None:
    """An empty concepts directory should not break existing operations."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        # The concepts directory exists but is empty.
        concepts_dir = root / CONTROL_DIR / "concepts"
        assert concepts_dir.is_dir()

        # Listing returns empty.
        assert list_records(root, "concept") == []

        # next_id returns IC-001.
        assert next_id(root, "concept") == "IC-001"

        # Existing work-order operations still work.
        wo = {
            "id": "WO-001",
            "revision": 1,
            "state": "proposed",
            "decision_question": "test",
            "requested_role": "test",
            "stage": 1,
            "cycle": 1,
            "context": {},
            "dependencies": [],
            "capabilities": [],
            "deliverables": {},
            "acceptance_criteria": ["test"],
            "alert_policy": {},
            "priority": "normal",
            "resource_class": "standard",
            "report_to": "lead",
            "created_at": _NOW,
        }
        write_record(root, "work-order", "WO-001-r1", wo)
        read_back = read_record(root, "work-order", "WO-001-r1")
        assert read_back["id"] == "WO-001"


def test_ensure_control_dirs_creates_concepts() -> None:
    """ensure_control_dirs should create the concepts subdirectory."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)
        assert (root / CONTROL_DIR / "concepts").is_dir()


def test_missing_concepts_dir_list_returns_empty() -> None:
    """Listing concepts when the directory does not exist should return []."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Don't call ensure_control_dirs — just create the bare minimum.
        (root / CONTROL_DIR).mkdir(parents=True)
        assert list_records(root, "concept") == []


# ---------------------------------------------------------------------------
# Tests: Two modalities for the same target (validation fixture)
# ---------------------------------------------------------------------------


def test_two_modalities_same_target() -> None:
    """Two concept records for the same gene with different modalities."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        small_mol = _make_minimal_concept(
            id="IC-001",
            modality="small_molecule",
        )
        small_mol["target_pathway"]["gene"] = "CDK4"

        biologic = _make_minimal_concept(
            id="IC-002",
            modality="biologic",
        )
        biologic["target_pathway"]["gene"] = "CDK4"

        write_record(root, "concept", "IC-001-r1", small_mol)
        write_record(root, "concept", "IC-002-r1", biologic)

        all_records = list_records(root, "concept")
        assert len(all_records) == 2
        modalities = {r["modality"] for r in all_records}
        assert modalities == {"small_molecule", "biologic"}


# ---------------------------------------------------------------------------
# Tests: Scoped rejection leaves alternative concept available
# ---------------------------------------------------------------------------


def test_scoped_rejection() -> None:
    """Terminating one concept leaves another for the same target available."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        ic1 = _make_minimal_concept(id="IC-001", state="terminated")
        ic2 = _make_minimal_concept(id="IC-002", state="active")
        ic2["charter_ref"] = "DEC-001"

        write_record(root, "concept", "IC-001-r1", ic1)
        write_record(root, "concept", "IC-002-r1", ic2)

        active = list_records(
            root,
            "concept",
            filter_fn=lambda r: r.get("state") == "active",
        )
        assert len(active) == 1
        assert active[0]["id"] == "IC-002"


# ---------------------------------------------------------------------------
# Tests: Major concept revision (key field change)
# ---------------------------------------------------------------------------


def test_major_revision() -> None:
    """A key field change creates a new revision record."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_control_dirs(root)

        r1 = _make_minimal_concept(id="IC-001", revision=1, state="active")
        r1["charter_ref"] = "DEC-001"
        write_record(root, "concept", "IC-001-r1", r1)

        # Change a key field — new revision required.
        r2 = dict(r1)
        r2["revision"] = 2
        r2["target_pathway"] = {"gene": "TP53"}
        assert requires_new_revision(r1, r2)

        write_record(root, "concept", "IC-001-r2", r2)

        all_records = list_records(root, "concept")
        assert len(all_records) == 2


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # ID format
        ("concept_id_regex", test_concept_id_regex),
        ("concept_record_key_regex", test_concept_record_key_regex),
        # State machine transitions
        ("transitions_initial", test_concept_transitions_initial),
        ("transitions_draft", test_concept_transitions_draft),
        ("transitions_active", test_concept_transitions_active),
        ("transitions_pivoting", test_concept_transitions_pivoting),
        ("transitions_terminal", test_concept_transitions_terminal),
        ("terminal_concept_states", test_terminal_concept_states),
        ("all_states_derived", test_all_states_derived),
        # State machine registration
        ("registered_in_machines", test_concept_registered_in_machines),
        ("validate_transition_legal", test_validate_transition_concept_legal),
        ("validate_transition_illegal", test_validate_transition_concept_illegal),
        # Concept validation
        ("validate_minimal", test_validate_minimal_concept),
        ("validate_worked_example", test_validate_worked_example),
        ("validate_missing_required", test_validate_missing_required_fields),
        ("validate_bad_schema", test_validate_bad_schema),
        ("validate_bad_id", test_validate_bad_id),
        ("validate_bad_revision", test_validate_bad_revision),
        ("validate_bad_state", test_validate_bad_state),
        ("validate_disease_context_not_dict", test_validate_disease_context_not_dict),
        (
            "validate_disease_context_missing_indication",
            test_validate_disease_context_missing_indication,
        ),
        ("validate_target_pathway_not_dict", test_validate_target_pathway_not_dict),
        (
            "validate_target_pathway_missing_gene",
            test_validate_target_pathway_missing_gene,
        ),
        ("validate_modality_not_string", test_validate_modality_not_string),
        ("validate_modality_null_ok", test_validate_modality_null_ok),
        (
            "validate_termination_authority_enum",
            test_validate_termination_authority_enum,
        ),
        # Biomarker validation
        ("biomarker_all_categories", test_biomarker_all_categories),
        ("biomarker_invalid_category", test_biomarker_invalid_category),
        ("biomarker_known_no_rationale", test_biomarker_known_no_rationale_needed),
        (
            "biomarker_unknown_requires_rationale",
            test_biomarker_unknown_requires_rationale,
        ),
        (
            "biomarker_not_applicable_requires_rationale",
            test_biomarker_not_applicable_requires_rationale,
        ),
        (
            "biomarker_unknown_with_rationale",
            test_biomarker_unknown_with_rationale_passes,
        ),
        (
            "biomarker_not_applicable_with_rationale",
            test_biomarker_not_applicable_with_rationale_passes,
        ),
        ("biomarker_empty_rationale", test_biomarker_empty_rationale_rejected),
        ("validate_concept_with_biomarkers", test_validate_concept_with_biomarkers),
        ("validate_concept_null_biomarkers", test_validate_concept_null_biomarkers_ok),
        (
            "validate_concept_biomarkers_not_list",
            test_validate_concept_biomarkers_not_list,
        ),
        # Revision triggers
        ("key_field_change_triggers", test_key_field_change_triggers_revision),
        ("gene_change_triggers", test_gene_change_triggers_revision),
        ("modality_change_triggers", test_modality_change_triggers_revision),
        (
            "mechanism_hypothesis_triggers",
            test_mechanism_hypothesis_change_triggers_revision,
        ),
        (
            "delivery_non_null_triggers",
            test_delivery_assumptions_non_null_triggers_revision,
        ),
        (
            "delivery_to_null_no_trigger",
            test_delivery_assumptions_to_null_does_not_trigger,
        ),
        ("non_key_no_revision", test_non_key_field_change_no_revision),
        ("work_order_refs_no_revision", test_work_order_refs_change_no_revision),
        ("decision_log_refs_no_revision", test_decision_log_refs_change_no_revision),
        (
            "patient_population_triggers",
            test_patient_population_change_triggers_revision,
        ),
        # Charter-linkage gate (isolated function)
        ("charter_blocks_activation", test_charter_linkage_blocks_activation),
        ("charter_empty_blocks", test_charter_linkage_empty_string_blocks),
        ("charter_whitespace_blocks", test_charter_linkage_whitespace_blocks),
        ("charter_set_allows", test_charter_linkage_set_allows_activation),
        ("charter_other_states_ok", test_charter_linkage_not_checked_for_other_states),
        # Charter-linkage wired into validate_concept (Refusal)
        (
            "validate_active_no_charter_refusal",
            test_validate_concept_active_without_charter_raises_refusal,
        ),
        (
            "validate_active_with_charter_ok",
            test_validate_concept_active_with_charter_no_refusal,
        ),
        (
            "validate_draft_no_charter_ok",
            test_validate_concept_draft_without_charter_no_refusal,
        ),
        # Charter-linkage through real write_record() path
        (
            "write_record_active_no_charter_refusal",
            test_write_record_active_concept_without_charter_raises_refusal,
        ),
        (
            "write_record_active_with_charter_ok",
            test_write_record_active_concept_with_charter_succeeds,
        ),
        (
            "write_record_active_empty_charter_refusal",
            test_write_record_active_concept_empty_charter_raises_refusal,
        ),
        (
            "write_record_draft_no_charter_ok",
            test_write_record_draft_concept_without_charter_succeeds,
        ),
        # Migration-shaped record through real write_record() path
        ("write_record_migration_shaped", test_write_record_migration_shaped_record),
        # Control store registration
        ("concept_in_record_types", test_concept_in_record_types),
        ("concept_validator_registered", test_concept_validator_registered),
        # Integration: round-trip
        ("write_read_concept", test_write_and_read_concept),
        ("write_validation_failure", test_write_concept_validation_failure),
        ("list_concepts", test_list_concepts),
        ("worked_example_round_trip", test_worked_example_round_trip),
        # next_id
        ("next_id_empty", test_next_id_concept_empty),
        ("next_id_increments", test_next_id_concept_increments),
        # Backward compatibility
        ("empty_concepts_no_breakage", test_empty_concepts_dir_no_breakage),
        ("ensure_dirs_creates_concepts", test_ensure_control_dirs_creates_concepts),
        ("missing_dir_list_empty", test_missing_concepts_dir_list_returns_empty),
        # Multi-modality / scoped rejection
        ("two_modalities_same_target", test_two_modalities_same_target),
        ("scoped_rejection", test_scoped_rejection),
        # Major revision
        ("major_revision", test_major_revision),
    ]

    print(f"\nRunning {len(tests)} concept tests...\n")
    for name, fn in tests:
        _run(name, fn)

    print(f"\n{_PASS} passed, {_FAIL} failed out of {_PASS + _FAIL} tests")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
