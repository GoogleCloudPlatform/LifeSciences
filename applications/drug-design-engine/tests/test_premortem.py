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

"""Tests for pre-mortem review and evidence-backed dissent (#76).

Covers the five required validation scenarios from the task brief:

1. A well-supported objection that changes the plan — resolves as
   "accepted objection" and the plan/decision reflects the change.
2. A speculative objection (no discriminating check) that does not
   block unrelated work — recorded but doesn't gate.
3. A budget-exhausted review with an unresolved material risk —
   recorded as "unresolved follow-up", not silently dropped.
4. Dissent survives gate-document generation — unresolved objection
   appears in the generated gate-document section.
5. An attempted autonomous termination from a pre-mortem accepted
   objection still hits the #75 human-approval Refusal gate.

Run with:
    PYTHONPATH=tools python3 tests/test_premortem.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.core.controlstore import (
    CONTROL_DIR,
    ensure_control_dirs,
    write_record,
)
from dde.core.errors import Refusal, SchemaError
from dde.core.evidence import validate_decision
from dde.core.premortem import (
    RESOLUTION_TYPES,
    check_dissent_in_liabilities,
    decode_resolution_condition,
    encode_resolution_condition,
    extract_resolutions_from_decision,
    find_unresolved_objections,
    generate_gate_dissent_section,
    is_speculative,
    validate_failure_hypothesis,
    validate_objection_resolution,
    validate_premortem,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
_PASS = 0
_FAIL = 0


def _check(name: str, fn: Any) -> None:
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


def _make_project(base: Path) -> Path:
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    ensure_control_dirs(project)
    return project


def _write_concept_to_disk(
    project: Path,
    concept_id: str,
    termination_authority: str,
) -> Path:
    """Write a concept record directly to .dde/control/concepts/."""
    concepts_dir = project / CONTROL_DIR / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "dde.intervention-concept.v1",
        "id": concept_id,
        "revision": 1,
        "state": "active",
        "termination_authority": termination_authority,
        "disease_context": {"indication": "solid_tumors"},
        "target_pathway": {"gene": "CDK4"},
        "modality": "small_molecule",
        "created_at": _NOW,
    }
    path = concepts_dir / f"{concept_id}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _substantive_hypothesis(**overrides: Any) -> dict[str, Any]:
    """A failure hypothesis WITH a discriminating check."""
    hyp: dict[str, Any] = {
        "id": "OBJ-001",
        "description": "CDK4 binding pocket may not accommodate the lead scaffold",
        "plausibility": "moderate",
        "consequence": "Lead optimization fails at Stage 2",
        "evidence": "fpocket drug_score 0.52, borderline by pocket@1.0",
        "discriminating_check": (
            "Dock the lead scaffold into the CDK4 binding pocket "
            "and check if the binding mode is consistent with the "
            "pharmacophore hypothesis"
        ),
    }
    hyp.update(overrides)
    return hyp


def _speculative_hypothesis(**overrides: Any) -> dict[str, Any]:
    """A failure hypothesis WITHOUT a discriminating check."""
    hyp: dict[str, Any] = {
        "id": "OBJ-002",
        "description": "CDK4 inhibitor might cause cardiac toxicity",
        "plausibility": "low",
        "consequence": "Program termination at Stage 3",
        "evidence": "CDK4 is expressed in cardiac tissue (TPM 2.1)",
        "discriminating_check": None,  # speculative — no test named
    }
    hyp.update(overrides)
    return hyp


def _valid_premortem(**overrides: Any) -> dict[str, Any]:
    """A valid pre-mortem review record."""
    record: dict[str, Any] = {
        "schema": "dde.premortem-review.v1",
        "review_id": "PM-001",
        "finding_ref": "findings/computational-biology/cdk4-druggability.md",
        "review_budget": 3,
        "failure_hypotheses": [
            _substantive_hypothesis(),
            _speculative_hypothesis(),
            _substantive_hypothesis(
                id="OBJ-003",
                description="Selectivity over CDK6 may be unachievable",
                plausibility="high",
                consequence="Cannot achieve therapeutic window",
                discriminating_check=(
                    "Compare CDK4 vs CDK6 binding pocket residues at "
                    "positions 14, 51, 100 using aligned crystal structures"
                ),
            ),
        ],
        "resolutions": [],
    }
    record.update(overrides)
    return record


def _valid_decision(**overrides: Any) -> dict[str, Any]:
    """A minimal valid decision record."""
    record: dict[str, Any] = {
        "schema": "dde.decision-record.v1",
        "id": "DR-001",
        "action": "advance_with_budget",
        "affected_entity": {
            "entity_type": "concept",
            "entity_ref": "IC-001",
        },
        "rationale": "Evidence supports advancement",
        "decided_at": _NOW,
        "decided_by": "program_lead",
    }
    record.update(overrides)
    return record


# ===========================================================================
# Tests
# ===========================================================================

print("=" * 60)
print("test_premortem.py — issue #76 pre-mortem review & dissent")
print("=" * 60)


# ---------------------------------------------------------------------------
# 1. Failure hypothesis validation
# ---------------------------------------------------------------------------
print("\n--- Failure hypothesis validation ---")


def test_valid_substantive_hypothesis():
    """A hypothesis with all required fields and a discriminating check."""
    hyp = _substantive_hypothesis()
    errors = validate_failure_hypothesis(hyp)
    assert errors == [], f"unexpected errors: {errors}"
    assert not is_speculative(hyp), "should not be speculative"


_check("valid substantive hypothesis", test_valid_substantive_hypothesis)


def test_valid_speculative_hypothesis():
    """A hypothesis without a discriminating check is speculative."""
    hyp = _speculative_hypothesis()
    errors = validate_failure_hypothesis(hyp)
    assert errors == [], f"unexpected errors: {errors}"
    assert is_speculative(hyp), "should be speculative"


_check("valid speculative hypothesis", test_valid_speculative_hypothesis)


def test_speculative_with_empty_check():
    """A hypothesis with an empty discriminating_check is also speculative."""
    hyp = _substantive_hypothesis(discriminating_check="   ")
    assert is_speculative(hyp), "empty whitespace check should be speculative"


_check("empty discriminating_check => speculative", test_speculative_with_empty_check)


def test_hypothesis_missing_fields():
    """Missing required fields produce errors."""
    errors = validate_failure_hypothesis({})
    assert any("missing required fields" in e for e in errors)


_check("hypothesis missing required fields => error", test_hypothesis_missing_fields)


def test_hypothesis_bad_id():
    """Hypothesis ID must match OBJ-NNN."""
    hyp = _substantive_hypothesis(id="BAD-001")
    errors = validate_failure_hypothesis(hyp)
    assert any("OBJ-NNN" in e for e in errors)


_check("hypothesis bad id format => error", test_hypothesis_bad_id)


def test_hypothesis_bad_plausibility():
    """Plausibility must be high/moderate/low."""
    hyp = _substantive_hypothesis(plausibility="maybe")
    errors = validate_failure_hypothesis(hyp)
    assert any("plausibility" in e for e in errors)


_check("hypothesis bad plausibility => error", test_hypothesis_bad_plausibility)


# ---------------------------------------------------------------------------
# 2. Pre-mortem record validation
# ---------------------------------------------------------------------------
print("\n--- Pre-mortem record validation ---")


def test_valid_premortem():
    """A complete pre-mortem record validates cleanly."""
    pm = _valid_premortem()
    errors = validate_premortem(pm)
    assert errors == [], f"unexpected errors: {errors}"


_check("valid pre-mortem record", test_valid_premortem)


def test_premortem_missing_fields():
    """Missing required fields produce errors."""
    errors = validate_premortem({})
    assert any("missing required fields" in e for e in errors)


_check("pre-mortem missing required fields => error", test_premortem_missing_fields)


def test_premortem_bad_schema():
    """Wrong schema string is rejected."""
    pm = _valid_premortem(schema="dde.wrong.v1")
    errors = validate_premortem(pm)
    assert any("schema" in e for e in errors)


_check("pre-mortem wrong schema => error", test_premortem_bad_schema)


def test_premortem_bad_budget():
    """Review budget must be a positive integer."""
    pm = _valid_premortem(review_budget=0)
    errors = validate_premortem(pm)
    assert any("review_budget" in e for e in errors)


_check("pre-mortem budget=0 => error", test_premortem_bad_budget)


def test_premortem_duplicate_hypothesis_ids():
    """Duplicate hypothesis IDs are rejected."""
    hyps = [_substantive_hypothesis(), _substantive_hypothesis()]  # both OBJ-001
    pm = _valid_premortem(failure_hypotheses=hyps)
    errors = validate_premortem(pm)
    assert any("duplicate" in e for e in errors)


_check(
    "pre-mortem duplicate hypothesis ids => error",
    test_premortem_duplicate_hypothesis_ids,
)


# ---------------------------------------------------------------------------
# 3. Objection resolution validation
# ---------------------------------------------------------------------------
print("\n--- Objection resolution validation ---")


def test_valid_accepted_resolution():
    """An accepted resolution with rationale validates."""
    res = {
        "objection_id": "OBJ-001",
        "resolution_type": "accepted",
        "rationale": "Objection is valid — revising plan to add docking validation",
    }
    errors = validate_objection_resolution(res)
    assert errors == [], f"unexpected errors: {errors}"


_check("valid accepted resolution", test_valid_accepted_resolution)


def test_valid_rebutted_resolution():
    """A rebutted resolution requires evidence_refs."""
    res = {
        "objection_id": "OBJ-002",
        "resolution_type": "rebutted",
        "rationale": "CDK4 cardiac expression is low; no functional hERG data supports this concern",
        "evidence_refs": ["AR-005", "AR-006"],
    }
    errors = validate_objection_resolution(res)
    assert errors == [], f"unexpected errors: {errors}"


_check("valid rebutted resolution", test_valid_rebutted_resolution)


def test_rebutted_without_evidence():
    """Rebutted resolution without evidence_refs produces error."""
    res = {
        "objection_id": "OBJ-002",
        "resolution_type": "rebutted",
        "rationale": "I disagree",
    }
    errors = validate_objection_resolution(res)
    assert any("evidence_refs" in e for e in errors)


_check("rebutted without evidence_refs => error", test_rebutted_without_evidence)


def test_valid_accepted_risk_resolution():
    """Accepted risk requires policy_ref."""
    res = {
        "objection_id": "OBJ-003",
        "resolution_type": "accepted_risk",
        "rationale": "Risk acknowledged; selectivity addressed at Stage 2 per policy",
        "policy_ref": "GP-001",
    }
    errors = validate_objection_resolution(res)
    assert errors == [], f"unexpected errors: {errors}"


_check("valid accepted_risk resolution", test_valid_accepted_risk_resolution)


def test_accepted_risk_without_policy():
    """Accepted risk without policy_ref produces error."""
    res = {
        "objection_id": "OBJ-003",
        "resolution_type": "accepted_risk",
        "rationale": "Risk acknowledged",
    }
    errors = validate_objection_resolution(res)
    assert any("policy_ref" in e for e in errors)


_check("accepted_risk without policy_ref => error", test_accepted_risk_without_policy)


def test_valid_unresolved_resolution():
    """Unresolved resolution requires owner."""
    res = {
        "objection_id": "OBJ-004",
        "resolution_type": "unresolved",
        "rationale": "Budget exhausted; needs structural biology follow-up",
        "owner": "structural-biologist",
    }
    errors = validate_objection_resolution(res)
    assert errors == [], f"unexpected errors: {errors}"


_check("valid unresolved resolution", test_valid_unresolved_resolution)


def test_unresolved_without_owner():
    """Unresolved resolution without owner produces error."""
    res = {
        "objection_id": "OBJ-004",
        "resolution_type": "unresolved",
        "rationale": "Needs follow-up",
    }
    errors = validate_objection_resolution(res)
    assert any("owner" in e for e in errors)


_check("unresolved without owner => error", test_unresolved_without_owner)


def test_invalid_resolution_type():
    """Unknown resolution type is rejected."""
    res = {
        "objection_id": "OBJ-001",
        "resolution_type": "dismissed",
        "rationale": "Don't care",
    }
    errors = validate_objection_resolution(res)
    assert any("resolution_type" in e for e in errors)


_check("invalid resolution type => error", test_invalid_resolution_type)


# ---------------------------------------------------------------------------
# 4. Condition-string encoding/decoding
# ---------------------------------------------------------------------------
print("\n--- Condition-string encoding/decoding ---")


def test_encode_accepted():
    s = encode_resolution_condition("accepted", "OBJ-001")
    assert s == "objection_resolution:accepted:OBJ-001"


_check("encode accepted", test_encode_accepted)


def test_encode_rebutted():
    s = encode_resolution_condition("rebutted", "OBJ-002")
    assert s == "objection_resolution:rebutted:OBJ-002"


_check("encode rebutted", test_encode_rebutted)


def test_encode_accepted_risk_with_policy():
    s = encode_resolution_condition("accepted_risk", "OBJ-003", policy_ref="GP-001")
    assert s == "objection_resolution:accepted_risk:OBJ-003:GP-001"


_check("encode accepted_risk with policy", test_encode_accepted_risk_with_policy)


def test_encode_unresolved_with_owner():
    s = encode_resolution_condition(
        "unresolved", "OBJ-004", owner="computational-biologist"
    )
    assert s == "objection_resolution:unresolved:OBJ-004:owner=computational-biologist"


_check("encode unresolved with owner", test_encode_unresolved_with_owner)


def test_decode_accepted():
    parsed = decode_resolution_condition("objection_resolution:accepted:OBJ-001")
    assert parsed is not None
    assert parsed["resolution_type"] == "accepted"
    assert parsed["objection_id"] == "OBJ-001"


_check("decode accepted", test_decode_accepted)


def test_decode_accepted_risk():
    parsed = decode_resolution_condition(
        "objection_resolution:accepted_risk:OBJ-003:GP-001"
    )
    assert parsed is not None
    assert parsed["resolution_type"] == "accepted_risk"
    assert parsed["objection_id"] == "OBJ-003"
    assert parsed["detail"] == "GP-001"


_check("decode accepted_risk with detail", test_decode_accepted_risk)


def test_decode_non_resolution():
    """Non-resolution condition strings return None."""
    assert decode_resolution_condition("some other condition") is None
    assert decode_resolution_condition("") is None


_check("decode non-resolution => None", test_decode_non_resolution)


def test_encode_invalid_type():
    """Encoding an invalid resolution type raises SchemaError."""
    try:
        encode_resolution_condition("dismissed", "OBJ-001")
        raise AssertionError("expected SchemaError")
    except SchemaError:
        pass


_check("encode invalid resolution type => SchemaError", test_encode_invalid_type)


def test_extract_resolutions_from_decision():
    """Extract resolution conditions from a decision record."""
    decision = _valid_decision(
        conditions=[
            "objection_resolution:accepted:OBJ-001",
            "some other condition",
            "objection_resolution:unresolved:OBJ-003:owner=comp-bio",
        ]
    )
    resolutions = extract_resolutions_from_decision(decision)
    assert len(resolutions) == 2
    assert resolutions[0]["resolution_type"] == "accepted"
    assert resolutions[0]["objection_id"] == "OBJ-001"
    assert resolutions[1]["resolution_type"] == "unresolved"


_check(
    "extract resolutions from decision conditions",
    test_extract_resolutions_from_decision,
)


# ===========================================================================
# SCENARIO TESTS (from issue #76 validation requirements)
# ===========================================================================

print("\n" + "=" * 60)
print("SCENARIO TESTS")
print("=" * 60)


# ---------------------------------------------------------------------------
# Scenario 1: Well-supported objection that changes the plan
# ---------------------------------------------------------------------------
print("\n--- Scenario 1: Well-supported objection changes the plan ---")


def test_scenario_1_accepted_objection_changes_plan():
    """A well-supported objection resolves as 'accepted' and the
    decision record reflects the plan change.

    Setup:
    - A pre-mortem raises OBJ-001 (substantive, with discriminating check)
    - The lead accepts the objection
    - A new decision record is created with action=investigate
      (plan changes from advance to investigate)
    """
    # 1. Pre-mortem with a substantive hypothesis
    pm = _valid_premortem()
    pm["resolutions"] = [
        {
            "objection_id": "OBJ-001",
            "resolution_type": "accepted",
            "rationale": "Docking validation needed before advancing",
        }
    ]
    errors = validate_premortem(pm)
    assert errors == [], f"pre-mortem validation: {errors}"

    # 2. Decision record reflects the plan change
    decision = _valid_decision(
        action="investigate",  # changed from advance_with_budget
        rationale=(
            "Pre-mortem OBJ-001 accepted: CDK4 binding pocket "
            "accommodation uncertain. Investigating via docking "
            "before advancing."
        ),
        conditions=[
            encode_resolution_condition("accepted", "OBJ-001"),
        ],
    )
    dec_errors = validate_decision(decision)
    assert dec_errors == [], f"decision validation: {dec_errors}"

    # 3. The resolution is extractable from the decision
    resolutions = extract_resolutions_from_decision(decision)
    assert len(resolutions) == 1
    assert resolutions[0]["resolution_type"] == "accepted"
    assert resolutions[0]["objection_id"] == "OBJ-001"

    # 4. The objection is no longer unresolved
    unresolved = find_unresolved_objections(pm, decision)
    obj_ids = [o["id"] for o in unresolved]
    assert "OBJ-001" not in obj_ids, "accepted objection should be resolved"


_check(
    "Scenario 1: accepted objection changes plan",
    test_scenario_1_accepted_objection_changes_plan,
)


# ---------------------------------------------------------------------------
# Scenario 2: Speculative objection does not block
# ---------------------------------------------------------------------------
print("\n--- Scenario 2: Speculative objection does not block ---")


def test_scenario_2_speculative_does_not_block():
    """A speculative objection (no discriminating check) is recorded
    but does not gate progress or appear as unresolved.

    Setup:
    - A pre-mortem raises OBJ-002 (speculative, no discriminating check)
    - No resolution is recorded for it (not in budget / not substantive)
    - The lead advances the program normally
    """
    pm = _valid_premortem()
    # No resolutions — the speculative hypothesis is not addressed
    pm["resolutions"] = []

    errors = validate_premortem(pm)
    assert errors == [], f"pre-mortem validation: {errors}"

    # The speculative hypothesis IS in the record
    hyp_ids = [h["id"] for h in pm["failure_hypotheses"]]
    assert "OBJ-002" in hyp_ids, "speculative hypothesis should be recorded"

    # But it is NOT among the unresolved objections
    unresolved = find_unresolved_objections(pm)
    unresolved_ids = [o["id"] for o in unresolved]
    assert "OBJ-002" not in unresolved_ids, (
        "speculative objection should not appear as unresolved"
    )

    # And the substantive unresolved ones ARE flagged
    assert "OBJ-001" in unresolved_ids, "substantive OBJ-001 should be unresolved"
    assert "OBJ-003" in unresolved_ids, "substantive OBJ-003 should be unresolved"

    # Decision to advance is valid — speculative objection doesn't block
    decision = _valid_decision(
        action="advance_with_budget",
        rationale="Evidence supports advancement; speculative cardiac concern recorded but lacks discriminating check",
    )
    dec_errors = validate_decision(decision)
    assert dec_errors == [], f"decision validation: {dec_errors}"


_check(
    "Scenario 2: speculative objection does not block",
    test_scenario_2_speculative_does_not_block,
)


# ---------------------------------------------------------------------------
# Scenario 3: Budget-exhausted review with unresolved material risk
# ---------------------------------------------------------------------------
print("\n--- Scenario 3: Budget-exhausted review with unresolved risk ---")


def test_scenario_3_budget_exhausted_unresolved_risk():
    """When the review budget is exhausted, remaining substantive
    hypotheses are recorded as 'unresolved follow-up' — not dropped.

    Setup:
    - Budget is 1 (only 1 objection can be pursued)
    - OBJ-001 is accepted (uses the budget)
    - OBJ-003 is substantive but budget is exhausted
    - OBJ-003 must be recorded as unresolved with an owner
    """
    pm = _valid_premortem(review_budget=1)
    pm["resolutions"] = [
        {
            "objection_id": "OBJ-001",
            "resolution_type": "accepted",
            "rationale": "Valid objection — adding docking validation",
        },
        {
            "objection_id": "OBJ-003",
            "resolution_type": "unresolved",
            "rationale": "Budget exhausted; selectivity concern needs follow-up",
            "owner": "computational-chemist",
        },
    ]
    errors = validate_premortem(pm)
    assert errors == [], f"pre-mortem validation: {errors}"

    # OBJ-003 is resolved (as unresolved-with-owner), so it should
    # NOT appear in find_unresolved_objections
    decision = _valid_decision(
        conditions=[
            encode_resolution_condition("accepted", "OBJ-001"),
            encode_resolution_condition(
                "unresolved", "OBJ-003", owner="computational-chemist"
            ),
        ],
        rationale=(
            "Review budget (1) exhausted. OBJ-001 accepted (plan change). "
            "OBJ-003 unresolved — assigned to computational-chemist."
        ),
    )

    unresolved = find_unresolved_objections(pm, decision)
    unresolved_ids = [o["id"] for o in unresolved]
    assert "OBJ-003" not in unresolved_ids, (
        "OBJ-003 has an 'unresolved' resolution — it's tracked, not dropped"
    )

    # But if we check liability tracking, OBJ-003 should have an entry
    liability_entries = [
        {
            "name": "CDK4/CDK6 selectivity concern (OBJ-003)",
            "source": "pre-mortem OBJ-003 — findings/reviews/cdk4-druggability-premortem.md",
            "severity": "monitor",
            "status": "open",
        },
    ]
    missing = check_dissent_in_liabilities(
        [_substantive_hypothesis(id="OBJ-003")],  # simulate unresolved
        liability_entries,
    )
    assert missing == [], f"OBJ-003 should be tracked in liabilities: {missing}"


_check(
    "Scenario 3: budget-exhausted with unresolved risk",
    test_scenario_3_budget_exhausted_unresolved_risk,
)


# ---------------------------------------------------------------------------
# Scenario 4: Dissent survives gate-document generation
# ---------------------------------------------------------------------------
print("\n--- Scenario 4: Dissent survives gate-document generation ---")


def test_scenario_4_dissent_survives_gate_document():
    """Unresolved objections must appear in the generated gate document
    section, not get dropped in the generation step.

    Setup:
    - A pre-mortem has OBJ-003 unresolved (substantive, no resolution)
    - Generate the gate-document dissent section
    - Confirm OBJ-003 appears in the output
    """
    pm = _valid_premortem()
    pm["resolutions"] = [
        {
            "objection_id": "OBJ-001",
            "resolution_type": "accepted",
            "rationale": "Valid — plan revised",
        },
    ]

    # OBJ-003 has no resolution and has a discriminating check
    # OBJ-002 is speculative (no discriminating check)
    unresolved = find_unresolved_objections(pm)
    unresolved_ids = [o["id"] for o in unresolved]
    assert "OBJ-003" in unresolved_ids, "OBJ-003 should be unresolved"
    assert "OBJ-002" not in unresolved_ids, "OBJ-002 is speculative"

    # Generate the gate-document dissent section
    section = generate_gate_dissent_section(
        unresolved,
        pm["resolutions"],
    )

    # OBJ-003 MUST appear
    assert "OBJ-003" in section, (
        f"OBJ-003 must appear in gate document dissent section. Got:\n{section}"
    )

    # OBJ-002 (speculative) should NOT appear in unresolved section
    assert "OBJ-002" not in section, (
        f"OBJ-002 (speculative) should not appear in dissent section. Got:\n{section}"
    )

    # The section should have the heading
    assert "Pre-Mortem Dissent Record" in section
    assert "Unresolved Objections" in section

    # The section should include the discriminating check
    assert (
        "discriminating check" in section.lower() or "Discriminating check" in section
    )


_check(
    "Scenario 4: dissent survives gate-document generation",
    test_scenario_4_dissent_survives_gate_document,
)


def test_scenario_4b_accepted_risk_appears_in_gate_document():
    """Accepted-risk resolutions also appear in the gate document."""
    unresolved: list[dict[str, Any]] = []  # no unresolved
    resolutions = [
        {
            "objection_id": "OBJ-003",
            "resolution_type": "accepted_risk",
            "rationale": "Selectivity addressed at Stage 2",
            "policy_ref": "GP-001",
        },
    ]
    section = generate_gate_dissent_section(unresolved, resolutions)
    assert "OBJ-003" in section
    assert "Accepted Risks" in section
    assert "GP-001" in section


_check(
    "Scenario 4b: accepted risk appears in gate document",
    test_scenario_4b_accepted_risk_appears_in_gate_document,
)


def test_scenario_4c_empty_dissent_produces_empty_section():
    """No objections → empty section (no noise in the gate document)."""
    section = generate_gate_dissent_section([], [])
    assert section == ""


_check(
    "Scenario 4c: empty dissent => empty section",
    test_scenario_4c_empty_dissent_produces_empty_section,
)


# ---------------------------------------------------------------------------
# Scenario 5: Pre-mortem cannot bypass human-approval Refusal gate
# ---------------------------------------------------------------------------
print("\n--- Scenario 5: No autonomous termination from pre-mortem ---")


def test_scenario_5_accepted_objection_cannot_bypass_refusal():
    """An accepted objection from a pre-mortem that leads to a
    termination decision STILL hits the #75 human-approval Refusal
    gate. Nothing in the pre-mortem code creates a bypass.

    Setup:
    - Pre-mortem OBJ-001 is accepted
    - The lead decides to terminate the concept
    - concept has termination_authority='human'
    - No human_approval is provided
    - The write MUST raise Refusal (exit 9)
    """
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_to_disk(project, "IC-001", "human")

        # Build a terminate decision motivated by the pre-mortem
        decision = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            rationale=(
                "Pre-mortem OBJ-001 accepted: binding pocket cannot "
                "accommodate the lead scaffold. Terminating concept."
            ),
            conditions=[
                encode_resolution_condition("accepted", "OBJ-001"),
            ],
            human_approval=None,
        )

        # This MUST raise Refusal — the pre-mortem conditions do NOT
        # bypass the existing human-approval gate
        try:
            write_record(project, "decision", "DR-001", decision)
            raise AssertionError(
                "Expected Refusal (exit 9) — pre-mortem must not bypass "
                "human-approval gate"
            )
        except Refusal as exc:
            assert exc.exit_code == 9, f"expected exit code 9, got {exc.exit_code}"
            assert "human approval" in exc.message.lower(), exc.message


_check(
    "Scenario 5: pre-mortem accepted objection => terminate => Refusal(9)",
    test_scenario_5_accepted_objection_cannot_bypass_refusal,
)


def test_scenario_5b_terminate_with_approval_succeeds():
    """The same termination WITH human_approval succeeds.
    Confirms the pre-mortem conditions don't interfere with the
    normal approval path."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_to_disk(project, "IC-001", "human")

        decision = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            rationale=("Pre-mortem OBJ-001 accepted. Terminating with approval."),
            conditions=[
                encode_resolution_condition("accepted", "OBJ-001"),
            ],
            human_approval={
                "approver": "Dr. Smith",
                "approved_at": _NOW,
                "approval_method": "charter_authority",
                "evidence": "Meeting notes 2026-09-08",
            },
        )

        path = write_record(project, "decision", "DR-001", decision)
        assert path.is_file(), "should succeed with human approval"


_check(
    "Scenario 5b: pre-mortem + terminate + approval => success",
    test_scenario_5b_terminate_with_approval_succeeds,
)


# ---------------------------------------------------------------------------
# 6. Dissent preservation: liability tracking
# ---------------------------------------------------------------------------
print("\n--- Dissent preservation: liability tracking ---")


def test_dissent_tracked_in_liabilities():
    """Unresolved objections with liability-tracker entries pass."""
    unresolved = [
        _substantive_hypothesis(id="OBJ-001"),
        _substantive_hypothesis(id="OBJ-003", description="Selectivity concern"),
    ]
    liabilities = [
        {"name": "Binding pocket concern", "source": "pre-mortem OBJ-001"},
        {"name": "Selectivity (OBJ-003)", "source": "pre-mortem review"},
    ]
    errors = check_dissent_in_liabilities(unresolved, liabilities)
    assert errors == [], f"unexpected errors: {errors}"


_check("dissent tracked in liabilities => pass", test_dissent_tracked_in_liabilities)


def test_dissent_missing_from_liabilities():
    """Unresolved objections WITHOUT liability-tracker entries fail."""
    unresolved = [
        _substantive_hypothesis(id="OBJ-001"),
        _substantive_hypothesis(id="OBJ-003", description="Selectivity concern"),
    ]
    liabilities = [
        {"name": "Binding pocket concern", "source": "pre-mortem OBJ-001"},
        # OBJ-003 is missing
    ]
    errors = check_dissent_in_liabilities(unresolved, liabilities)
    assert len(errors) == 1, f"expected 1 error, got: {errors}"
    assert "OBJ-003" in errors[0]


_check(
    "dissent missing from liabilities => error", test_dissent_missing_from_liabilities
)


def test_dissent_empty_liabilities():
    """When there are unresolved objections but no liability entries at all."""
    unresolved = [_substantive_hypothesis(id="OBJ-001")]
    errors = check_dissent_in_liabilities(unresolved, [])
    assert len(errors) == 1
    assert "OBJ-001" in errors[0]


_check("unresolved with empty liabilities => error", test_dissent_empty_liabilities)


def test_no_unresolved_no_errors():
    """No unresolved objections → no errors regardless of liabilities."""
    errors = check_dissent_in_liabilities([], [])
    assert errors == []


_check("no unresolved objections => no errors", test_no_unresolved_no_errors)


# ---------------------------------------------------------------------------
# 7. Resolution types completeness
# ---------------------------------------------------------------------------
print("\n--- Resolution types ---")


def test_resolution_types_complete():
    """All four resolution types exist."""
    assert "accepted" in RESOLUTION_TYPES
    assert "rebutted" in RESOLUTION_TYPES
    assert "accepted_risk" in RESOLUTION_TYPES
    assert "unresolved" in RESOLUTION_TYPES
    assert len(RESOLUTION_TYPES) == 4


_check("resolution types complete", test_resolution_types_complete)


# ---------------------------------------------------------------------------
# 8. Integration: pre-mortem with existing evidence.py decision records
# ---------------------------------------------------------------------------
print("\n--- Integration with evidence.py ---")


def test_decision_with_resolution_conditions_validates():
    """A decision record carrying objection_resolution conditions
    passes the existing evidence.py validator — no new schema needed."""
    decision = _valid_decision(
        conditions=[
            "objection_resolution:accepted:OBJ-001",
            "objection_resolution:rebutted:OBJ-002",
            "objection_resolution:accepted_risk:OBJ-003:GP-001",
            "objection_resolution:unresolved:OBJ-004:owner=comp-bio",
            "proceed despite borderline druggability score",
        ],
    )
    errors = validate_decision(decision)
    assert errors == [], f"unexpected errors: {errors}"


_check(
    "decision with resolution conditions validates in evidence.py",
    test_decision_with_resolution_conditions_validates,
)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
