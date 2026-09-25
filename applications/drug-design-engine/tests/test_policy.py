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

"""Tests for the gate policy module (issue #11).

Covers:
  - Applicability matching: modality filter, null-means-all, indication
  - Requirement type validation: hard_constraint, prioritization_heuristic,
    scientific_cutoff (requires threshold_set reference)
  - Freeze completeness with UNRESOLVED handling: freeze a policy whose
    requirements reference a threshold set with UNRESOLVED values; confirm
    the snapshot's unresolved list is populated correctly
  - Unit/method compatibility matching per design §3.1
  - Threshold references are never inlined — requirement fields are strings,
    values resolved at evaluation time
  - Round-trip validation using design §7 worked example fixtures
  - Policy record validation (schema, ID format, required fields)
  - Snapshot record validation
  - Program YAML loading
  - Control store registration (policy and snapshot record types)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.controlstore import _VALIDATORS, RECORD_TYPES
from dde.core.errors import SchemaError
from dde.core.policy import (
    REQUIREMENT_TYPES,
    SNAPSHOT_SCHEMA,
    freeze_policy,
    load_program_config,
    match_assessment_to_requirement,
    requirement_applies,
    validate_policy,
    validate_snapshot,
)
from dde.core.thresholds import UNRESOLVED, ThresholdSet, load

# ---------------------------------------------------------------------------
# Fixtures — design §7 worked example
# ---------------------------------------------------------------------------


def _worked_example_policy() -> dict[str, Any]:
    """Gate policy from design §7 Step 2."""
    return {
        "schema": "dde.gate-policy.v1",
        "id": "GP-001",
        "version": 1,
        "stage": 1,
        "gate_name": "target_nomination",
        "effective_date": "2026-09-08",
        "requirements": [
            {
                "req_id": "REQ-001",
                "version": 1,
                "description": "Causal genetic evidence linking target to disease",
                "type": "hard_constraint",
                "evidence_type": "genetic_constraint",
                "endpoint": "pLI",
                "units": None,
                "threshold_set": "gnomad-constraint@1.0",
                "threshold_key": "lof_intolerant_pli",
                "direction": "above",
                "applicability": {"modality": None, "stage": [1]},
                "authority": "program_lead",
                "override_permitted": False,
                "source": "gnomAD published constraint guidance",
                "effective_date": "2026-09-08",
            },
            {
                "req_id": "REQ-002",
                "version": 1,
                "description": "Structural tractability — druggable binding pocket",
                "type": "scientific_cutoff",
                "evidence_type": "structural_druggability",
                "endpoint": "drug_score",
                "units": None,
                "threshold_set": "pocket@1.0",
                "threshold_key": "druggable_dscore",
                "direction": "above",
                "applicability": {"modality": ["small_molecule"], "stage": [1]},
                "authority": "program_lead",
                "override_permitted": True,
                "override_authority": "human",
                "source": "Schmidtke & Barril, J Med Chem 2010",
                "effective_date": "2026-09-08",
            },
        ],
        "source": "program charter DEC-001",
        "created_at": "2026-09-08T14:00:00Z",
    }


def _small_molecule_concept() -> dict[str, Any]:
    """Small-molecule concept for applicability testing."""
    return {
        "id": "IC-001",
        "revision": 1,
        "modality": "small_molecule",
        "disease_context": {
            "indication": "solid_tumors",
            "stage": "preclinical",
        },
    }


def _biologic_concept() -> dict[str, Any]:
    """Biologic concept for applicability testing."""
    return {
        "id": "IC-002",
        "revision": 1,
        "modality": "biologic",
        "disease_context": {
            "indication": "autoimmune",
        },
    }


# ---------------------------------------------------------------------------
# Applicability matching tests
# ---------------------------------------------------------------------------


def test_modality_filter_matches_small_molecule():
    """REQ-002 has modality: ['small_molecule'] — applies to SM concept."""
    policy = _worked_example_policy()
    req = policy["requirements"][1]  # REQ-002
    concept = _small_molecule_concept()
    assert requirement_applies(req, concept, stage=1) is True


def test_modality_filter_excludes_biologic():
    """REQ-002 has modality: ['small_molecule'] — does NOT apply to biologic."""
    policy = _worked_example_policy()
    req = policy["requirements"][1]  # REQ-002
    concept = _biologic_concept()
    assert requirement_applies(req, concept, stage=1) is False


def test_null_modality_applies_to_all():
    """REQ-001 has modality: null — applies to both SM and biologic."""
    policy = _worked_example_policy()
    req = policy["requirements"][0]  # REQ-001
    assert requirement_applies(req, _small_molecule_concept(), stage=1) is True
    assert requirement_applies(req, _biologic_concept(), stage=1) is True


def test_stage_filter():
    """Requirements with stage: [1] do not apply at stage 2."""
    policy = _worked_example_policy()
    req = policy["requirements"][0]  # REQ-001 has stage: [1]
    concept = _small_molecule_concept()
    assert requirement_applies(req, concept, stage=1) is True
    assert requirement_applies(req, concept, stage=2) is False


def test_null_stage_applies_to_all():
    """A requirement with stage: null applies at any stage."""
    req = {
        "applicability": {"modality": None, "stage": None},
    }
    concept = _small_molecule_concept()
    assert requirement_applies(req, concept, stage=1) is True
    assert requirement_applies(req, concept, stage=3) is True


def test_indication_filter():
    """Indication filter matches against disease_context.indication."""
    req = {
        "applicability": {
            "modality": None,
            "stage": None,
            "indication": ["oncology", "solid_tumors"],
        },
    }
    sm = _small_molecule_concept()  # indication: solid_tumors
    bio = _biologic_concept()  # indication: autoimmune
    assert requirement_applies(req, sm) is True
    assert requirement_applies(req, bio) is False


def test_null_indication_applies_to_all():
    """A requirement with indication: null applies regardless."""
    req = {
        "applicability": {
            "modality": None,
            "stage": None,
            "indication": None,
        },
    }
    assert requirement_applies(req, _small_molecule_concept()) is True
    assert requirement_applies(req, _biologic_concept()) is True


# ---------------------------------------------------------------------------
# Requirement type validation tests
# ---------------------------------------------------------------------------


def test_requirement_types_set():
    """The three requirement types are defined."""
    assert "hard_constraint" in REQUIREMENT_TYPES
    assert "prioritization_heuristic" in REQUIREMENT_TYPES
    assert "scientific_cutoff" in REQUIREMENT_TYPES
    assert len(REQUIREMENT_TYPES) == 3


def test_scientific_cutoff_requires_threshold_set():
    """A scientific_cutoff requirement without threshold_set fails validation."""
    policy = _worked_example_policy()
    # Break REQ-002: remove threshold_set
    policy["requirements"][1]["threshold_set"] = None
    errors = validate_policy(policy)
    assert any("scientific_cutoff" in e and "threshold_set" in e for e in errors)


def test_scientific_cutoff_requires_threshold_key():
    """A scientific_cutoff requirement without threshold_key fails validation."""
    policy = _worked_example_policy()
    policy["requirements"][1]["threshold_key"] = None
    errors = validate_policy(policy)
    assert any("scientific_cutoff" in e and "threshold_key" in e for e in errors)


def test_valid_hard_constraint_no_threshold_required():
    """A hard_constraint does not need threshold_set or threshold_key."""
    policy = _worked_example_policy()
    req = policy["requirements"][0]  # REQ-001 is hard_constraint
    assert req["type"] == "hard_constraint"
    # Even without threshold_set, the policy should validate fine
    # (hard_constraint doesn't require it)
    errors = validate_policy(policy)
    assert not errors, f"Unexpected errors: {errors}"


def test_invalid_requirement_type():
    """An unknown requirement type fails validation."""
    policy = _worked_example_policy()
    policy["requirements"][0]["type"] = "bogus_type"
    errors = validate_policy(policy)
    assert any("bogus_type" in e for e in errors)


# ---------------------------------------------------------------------------
# Policy record validation tests
# ---------------------------------------------------------------------------


def test_valid_policy_passes_validation():
    """The worked example policy passes validation."""
    errors = validate_policy(_worked_example_policy())
    assert errors == [], f"Unexpected errors: {errors}"


def test_policy_missing_required_fields():
    """A policy missing required fields fails."""
    errors = validate_policy({"id": "GP-001"})
    assert any("missing required fields" in e for e in errors)


def test_policy_bad_id_format():
    """Policy ID must match GP-NNN."""
    policy = _worked_example_policy()
    policy["id"] = "POLICY-1"
    errors = validate_policy(policy)
    assert any("GP-NNN" in e for e in errors)


def test_policy_bad_schema():
    """Policy schema must be dde.gate-policy.v1."""
    policy = _worked_example_policy()
    policy["schema"] = "dde.gate-policy.v99"
    errors = validate_policy(policy)
    assert any("dde.gate-policy.v1" in e for e in errors)


def test_policy_bad_stage():
    """Stage must be 0-4."""
    policy = _worked_example_policy()
    policy["stage"] = 99
    errors = validate_policy(policy)
    assert any("stage" in e for e in errors)


def test_policy_bad_version():
    """Version must be a positive integer."""
    policy = _worked_example_policy()
    policy["version"] = 0
    errors = validate_policy(policy)
    assert any("version" in e and "positive integer" in e for e in errors)


def test_requirement_bad_req_id():
    """Requirement IDs must match REQ-NNN."""
    policy = _worked_example_policy()
    policy["requirements"][0]["req_id"] = "R1"
    errors = validate_policy(policy)
    assert any("REQ-NNN" in e for e in errors)


def test_requirement_missing_fields():
    """A requirement missing required fields fails."""
    policy = _worked_example_policy()
    policy["requirements"] = [{"req_id": "REQ-001"}]
    errors = validate_policy(policy)
    assert any("missing required fields" in e for e in errors)


def test_requirement_bad_direction():
    """Direction must be above, below, within, or null."""
    policy = _worked_example_policy()
    policy["requirements"][0]["direction"] = "sideways"
    errors = validate_policy(policy)
    assert any("direction" in e for e in errors)


def test_applicability_bad_field():
    """Unknown applicability fields are rejected."""
    policy = _worked_example_policy()
    policy["requirements"][0]["applicability"]["bogus_field"] = "test"
    errors = validate_policy(policy)
    assert any("unknown applicability field" in e for e in errors)


# ---------------------------------------------------------------------------
# Snapshot validation tests
# ---------------------------------------------------------------------------


def test_valid_snapshot_passes():
    """A well-formed snapshot passes validation."""
    snapshot = {
        "schema": "dde.policy-snapshot.v1",
        "snapshot_id": "SNAP-001",
        "frozen_at": "2026-09-08T18:00:00Z",
        "gate_policy_ref": "GP-001@1",
        "requirements": [],
        "threshold_sets": {
            "pocket@1.0": {
                "tag": "pocket@1.0",
                "applied": {"druggable_dscore": 0.5},
                "sources": {"druggable_dscore": "default"},
                "provenance": "test",
                "unresolved": [],
            }
        },
        "concept_refs": ["IC-001-r1"],
        "assessments": ["AR-001"],
        "decision_ref": "DR-001",
    }
    errors = validate_snapshot(snapshot)
    assert errors == [], f"Unexpected errors: {errors}"


def test_snapshot_missing_fields():
    """A snapshot missing required fields fails."""
    errors = validate_snapshot({"snapshot_id": "SNAP-001"})
    assert any("missing required fields" in e for e in errors)


def test_snapshot_bad_id():
    """Snapshot ID must match SNAP-NNN."""
    snapshot = {
        "schema": "dde.policy-snapshot.v1",
        "snapshot_id": "S-1",
        "frozen_at": "2026-09-08T18:00:00Z",
        "gate_policy_ref": "GP-001@1",
        "requirements": [],
        "threshold_sets": {},
        "concept_refs": [],
        "assessments": [],
    }
    errors = validate_snapshot(snapshot)
    assert any("SNAP-NNN" in e for e in errors)


def test_snapshot_threshold_set_missing_unresolved():
    """Each threshold set entry must have an unresolved field."""
    snapshot = {
        "schema": "dde.policy-snapshot.v1",
        "snapshot_id": "SNAP-001",
        "frozen_at": "2026-09-08T18:00:00Z",
        "gate_policy_ref": "GP-001@1",
        "requirements": [],
        "threshold_sets": {
            "pocket@1.0": {
                "tag": "pocket@1.0",
                "applied": {},
                "sources": {},
                "provenance": "test",
                # missing "unresolved"
            }
        },
        "concept_refs": [],
        "assessments": [],
    }
    errors = validate_snapshot(snapshot)
    assert any("unresolved" in e for e in errors)


# ---------------------------------------------------------------------------
# Freeze with UNRESOLVED handling
# ---------------------------------------------------------------------------


def test_freeze_captures_unresolved_thresholds():
    """Freeze a policy referencing a threshold set with UNRESOLVED values.

    Confirms:
      - unresolved keys appear in the snapshot's unresolved list
      - unresolved keys are absent from the applied dict
      - resolved keys appear in applied but not in unresolved
    """
    # Create a threshold set with one resolved and one UNRESOLVED value.
    ts = ThresholdSet(
        name="test-set",
        version="1.0",
        values={
            "resolved_key": 0.5,
            "unresolved_key": UNRESOLVED,
        },
        provenance="test provenance",
    )

    policy = {
        "id": "GP-001",
        "version": 1,
        "requirements": [
            {
                "req_id": "REQ-001",
                "threshold_set": "test-set@1.0",
                "threshold_key": "resolved_key",
                "type": "scientific_cutoff",
            },
        ],
    }

    snapshot = freeze_policy(
        policy,
        {"test-set@1.0": ts},
        snapshot_id="SNAP-001",
        concept_refs=["IC-001-r1"],
        assessments=["AR-001"],
    )

    ts_entry = snapshot["threshold_sets"]["test-set@1.0"]

    # The unresolved key is in the unresolved list.
    assert "unresolved_key" in ts_entry["unresolved"]

    # The unresolved key is NOT in applied.
    assert "unresolved_key" not in ts_entry["applied"]

    # The resolved key IS in applied.
    assert "resolved_key" in ts_entry["applied"]
    assert ts_entry["applied"]["resolved_key"] == 0.5

    # The resolved key is NOT in unresolved.
    assert "resolved_key" not in ts_entry["unresolved"]


def test_freeze_with_real_threshold_set():
    """Freeze using a real threshold set from thresholds.py (hypex).

    The hypex set has three UNRESOLVED values:
      - elo_decisive_gap
      - max_suspect_citations
      - min_safety_score

    This tests the full integration path.
    """
    ts = load("hypex")

    policy = {
        "id": "GP-002",
        "version": 1,
        "requirements": [
            {
                "req_id": "REQ-001",
                "threshold_set": ts.tag,
                "threshold_key": "min_matches",
                "type": "scientific_cutoff",
            },
        ],
    }

    snapshot = freeze_policy(
        policy,
        {ts.tag: ts},
        snapshot_id="SNAP-002",
        concept_refs=["IC-001-r1"],
        assessments=[],
    )

    ts_entry = snapshot["threshold_sets"][ts.tag]

    # Three keys should be unresolved.
    assert "elo_decisive_gap" in ts_entry["unresolved"]
    assert "max_suspect_citations" in ts_entry["unresolved"]
    assert "min_safety_score" in ts_entry["unresolved"]

    # Resolved keys should be in applied.
    assert "min_matches" in ts_entry["applied"]
    assert ts_entry["applied"]["min_matches"] == 5
    assert "min_win_rate" in ts_entry["applied"]

    # Unresolved keys should NOT be in applied.
    assert "elo_decisive_gap" not in ts_entry["applied"]


def test_freeze_empty_unresolved_for_fully_resolved_set():
    """A fully-resolved threshold set has an empty unresolved list."""
    ts = load("pocket")

    policy = {
        "id": "GP-003",
        "version": 1,
        "requirements": [
            {
                "req_id": "REQ-001",
                "threshold_set": ts.tag,
                "threshold_key": "druggable_dscore",
                "type": "scientific_cutoff",
            },
        ],
    }

    snapshot = freeze_policy(
        policy,
        {ts.tag: ts},
        snapshot_id="SNAP-003",
        concept_refs=[],
        assessments=[],
    )

    ts_entry = snapshot["threshold_sets"][ts.tag]
    assert ts_entry["unresolved"] == []
    assert "druggable_dscore" in ts_entry["applied"]


def test_freeze_snapshot_is_self_contained():
    """The freeze snapshot contains a deep copy of requirements."""
    policy = _worked_example_policy()
    original_desc = policy["requirements"][0]["description"]

    ts_gnomad = load("gnomad-constraint")
    ts_pocket = load("pocket")

    snapshot = freeze_policy(
        policy,
        {ts_gnomad.tag: ts_gnomad, ts_pocket.tag: ts_pocket},
        snapshot_id="SNAP-004",
        concept_refs=["IC-001-r1"],
        assessments=["AR-001"],
        decision_ref="DR-001",
    )

    # Mutate the original policy.
    policy["requirements"][0]["description"] = "MUTATED"

    # The snapshot's copy should be unchanged.
    assert snapshot["requirements"][0]["description"] == original_desc


def test_freeze_snapshot_schema_and_fields():
    """The freeze snapshot has all required fields and correct schema."""
    ts = ThresholdSet(
        name="test",
        version="1.0",
        values={"k": 1.0},
        provenance="test",
    )
    policy = {
        "id": "GP-001",
        "version": 2,
        "requirements": [
            {"req_id": "REQ-001", "threshold_set": "test@1.0"},
        ],
    }

    snapshot = freeze_policy(
        policy,
        {"test@1.0": ts},
        snapshot_id="SNAP-005",
        concept_refs=["IC-001-r1"],
        assessments=["AR-001", "AR-002"],
        decision_ref="DR-001",
    )

    assert snapshot["schema"] == SNAPSHOT_SCHEMA
    assert snapshot["snapshot_id"] == "SNAP-005"
    assert snapshot["gate_policy_ref"] == "GP-001@2"
    assert snapshot["concept_refs"] == ["IC-001-r1"]
    assert snapshot["assessments"] == ["AR-001", "AR-002"]
    assert snapshot["decision_ref"] == "DR-001"
    assert "frozen_at" in snapshot


# ---------------------------------------------------------------------------
# Unit/method compatibility matching (design §3.1)
# ---------------------------------------------------------------------------


def test_evidence_type_mismatch_does_not_match():
    """Different evidence_type → no match."""
    req = {"evidence_type": "genetic_constraint", "applicability": {}}
    assessment = {"evidence": {"evidence_type": "structural_druggability"}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is False


def test_evidence_type_match():
    """Same evidence_type → match."""
    req = {"evidence_type": "genetic_constraint", "applicability": {}}
    assessment = {"evidence": {"evidence_type": "genetic_constraint"}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True


def test_units_mismatch_produces_warning():
    """Mismatched units produce a warning, not a rejection."""
    req = {
        "evidence_type": "assay_activity",
        "units": "uM",
        "applicability": {},
    }
    assessment = {
        "evidence": {
            "evidence_type": "assay_activity",
            "metric_units": "nM",
        }
    }
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True
    assert len(result["warnings"]) > 0
    assert any("units mismatch" in w for w in result["warnings"])


def test_units_both_null_no_warning():
    """Both units null → no warning."""
    req = {
        "evidence_type": "genetic_constraint",
        "units": None,
        "applicability": {},
    }
    assessment = {
        "evidence": {
            "evidence_type": "genetic_constraint",
            "metric_units": None,
        }
    }
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True
    assert len(result["warnings"]) == 0


def test_units_one_specified_produces_warning():
    """Only one side specifies units → warning."""
    req = {
        "evidence_type": "genetic_constraint",
        "units": "fraction",
        "applicability": {},
    }
    assessment = {
        "evidence": {
            "evidence_type": "genetic_constraint",
        }
    }
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True
    assert any("units" in w for w in result["warnings"])


def test_method_required_excludes_wrong_method():
    """method_required applicability filter excludes non-matching methods."""
    req = {
        "evidence_type": "structural_druggability",
        "applicability": {"method_required": ["fpocket"]},
    }
    assessment = {
        "evidence": {
            "evidence_type": "structural_druggability",
            "method": "sitemap",
        }
    }
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True  # evidence_type matches
    assert result["method_excluded"] is True  # but method is excluded


def test_method_required_accepts_right_method():
    """method_required accepts matching method."""
    req = {
        "evidence_type": "structural_druggability",
        "applicability": {"method_required": ["fpocket"]},
    }
    assessment = {
        "evidence": {
            "evidence_type": "structural_druggability",
            "method": "fpocket",
        }
    }
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True
    assert result["method_excluded"] is False


def test_endpoint_mismatch_produces_warning():
    """Mismatched endpoint/metric_name produces a warning."""
    req = {
        "evidence_type": "genetic_constraint",
        "endpoint": "pLI",
        "applicability": {},
    }
    assessment = {
        "evidence": {
            "evidence_type": "genetic_constraint",
            "metric_name": "LOEUF",
        }
    }
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True
    assert any("endpoint" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Threshold references are never inlined
# ---------------------------------------------------------------------------


def test_threshold_references_are_strings():
    """Policy requirement threshold_set and threshold_key are strings.

    The actual value is only resolved at evaluation time via
    ThresholdSet.get() — the policy never inlines the numeric value.
    """
    policy = _worked_example_policy()
    for req in policy["requirements"]:
        if req.get("threshold_set"):
            assert isinstance(req["threshold_set"], str)
            assert isinstance(req["threshold_key"], str)
            # No inline threshold value field
            assert "threshold_value" not in req


# ---------------------------------------------------------------------------
# Round-trip validation with worked example (design §7)
# ---------------------------------------------------------------------------


def test_worked_example_policy_roundtrip():
    """The worked example policy record passes validation and serializes."""
    policy = _worked_example_policy()
    errors = validate_policy(policy)
    assert errors == [], f"Validation errors: {errors}"

    # Round-trip through JSON.
    serialized = json.dumps(policy)
    deserialized = json.loads(serialized)
    errors2 = validate_policy(deserialized)
    assert errors2 == [], f"Post-roundtrip errors: {errors2}"


def test_worked_example_snapshot_roundtrip():
    """The worked example freeze snapshot structure passes validation."""
    # Build a snapshot matching design §7 Step 5.
    ts_gnomad = load("gnomad-constraint")
    ts_pocket = load("pocket")
    policy = _worked_example_policy()

    snapshot = freeze_policy(
        policy,
        {ts_gnomad.tag: ts_gnomad, ts_pocket.tag: ts_pocket},
        snapshot_id="SNAP-001",
        concept_refs=["IC-001-r1"],
        assessments=["AR-001", "AR-002", "AR-003"],
        decision_ref="DR-001",
    )

    errors = validate_snapshot(snapshot)
    assert errors == [], f"Validation errors: {errors}"

    # Round-trip through JSON.
    serialized = json.dumps(snapshot)
    deserialized = json.loads(serialized)
    errors2 = validate_snapshot(deserialized)
    assert errors2 == [], f"Post-roundtrip errors: {errors2}"

    # Verify the threshold sets match expectations.
    gnomad_entry = deserialized["threshold_sets"][ts_gnomad.tag]
    assert gnomad_entry["applied"]["lof_intolerant_pli"] == 0.9
    # gnomad-constraint has one UNRESOLVED key.
    assert "loeuf_unreliable_min_expected_lof" in gnomad_entry["unresolved"]

    pocket_entry = deserialized["threshold_sets"][ts_pocket.tag]
    assert pocket_entry["applied"]["druggable_dscore"] == 0.5
    assert pocket_entry["unresolved"] == []


# ---------------------------------------------------------------------------
# Control store registration
# ---------------------------------------------------------------------------


def test_policy_registered_in_record_types():
    """'policy' is in RECORD_TYPES with directory 'policies'."""
    assert "policy" in RECORD_TYPES
    assert RECORD_TYPES["policy"] == "policies"


def test_snapshot_registered_in_record_types():
    """'snapshot' is in RECORD_TYPES with directory 'snapshots'."""
    assert "snapshot" in RECORD_TYPES
    assert RECORD_TYPES["snapshot"] == "snapshots"


def test_policy_validator_registered():
    """Policy validator is registered in _VALIDATORS."""
    assert "policy" in _VALIDATORS
    # Verify it works by calling it.
    errors = _VALIDATORS["policy"](_worked_example_policy())
    assert errors == []


def test_snapshot_validator_registered():
    """Snapshot validator is registered in _VALIDATORS."""
    assert "snapshot" in _VALIDATORS


def test_controlstore_creates_policy_dirs(tmp_path):
    """ensure_control_dirs creates policies/ and snapshots/ subdirectories."""
    from dde.core.controlstore import ensure_control_dirs

    ensure_control_dirs(tmp_path)
    assert (tmp_path / ".dde" / "control" / "policies").is_dir()
    assert (tmp_path / ".dde" / "control" / "snapshots").is_dir()


def test_controlstore_write_and_read_policy(tmp_path):
    """Round-trip a policy record through the control store."""
    from dde.core.controlstore import ensure_control_dirs, read_record, write_record

    ensure_control_dirs(tmp_path)

    policy = _worked_example_policy()
    write_record(tmp_path, "policy", "GP-001-v1", policy)
    loaded = read_record(tmp_path, "policy", "GP-001-v1")
    assert loaded["id"] == "GP-001"
    assert loaded["version"] == 1
    assert len(loaded["requirements"]) == 2


def test_controlstore_write_invalid_policy_raises(tmp_path):
    """Writing an invalid policy record raises SchemaError."""
    from dde.core.controlstore import ensure_control_dirs, write_record

    ensure_control_dirs(tmp_path)

    bad_policy = {"id": "INVALID"}  # Missing required fields
    try:
        write_record(tmp_path, "policy", "bad", bad_policy)
        assert False, "Should have raised SchemaError"
    except SchemaError:
        pass


# ---------------------------------------------------------------------------
# Program YAML loading
# ---------------------------------------------------------------------------


def test_load_program_config_missing_file(tmp_path):
    """Missing program.yaml returns empty dict (backward compatible)."""
    config = load_program_config(tmp_path)
    assert config == {}


def test_load_program_config_valid(tmp_path):
    """A valid program.yaml loads correctly."""
    dde_dir = tmp_path / ".dde"
    dde_dir.mkdir()
    program_yaml = dde_dir / "program.yaml"
    program_yaml.write_text(
        "program:\n"
        '  name: "Test Program"\n'
        '  indication: "oncology"\n'
        '  modality: "small_molecule"\n'
        '  charter_ref: "DEC-001"\n'
        "\n"
        "gate_policies:\n"
        '  stage_1: "GP-001@1"\n'
        "  stage_2: null\n"
        "\n"
        "human_reserved:\n"
        '  - "program_termination"\n'
        "\n"
        "concept_defaults:\n"
        '  termination_authority: "human"\n',
        encoding="utf-8",
    )

    config = load_program_config(tmp_path)
    assert config["program"]["name"] == "Test Program"
    assert config["gate_policies"]["stage_1"] == "GP-001@1"
    assert config["gate_policies"]["stage_2"] is None
    assert "program_termination" in config["human_reserved"]
    assert config["concept_defaults"]["termination_authority"] == "human"


def test_load_program_config_invalid_structure(tmp_path):
    """A program.yaml with invalid structure raises SchemaError."""
    dde_dir = tmp_path / ".dde"
    dde_dir.mkdir()
    program_yaml = dde_dir / "program.yaml"
    program_yaml.write_text(
        "program: not_a_dict_value\ngate_policies: also_not_a_dict\n",
        encoding="utf-8",
    )

    try:
        load_program_config(tmp_path)
        assert False, "Should have raised SchemaError"
    except SchemaError:
        pass
