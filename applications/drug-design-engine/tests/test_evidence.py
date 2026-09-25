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

"""Tests for evidence assessment and decision record schemas.

Covers issue #75 acceptance criteria:
- Human-approval Refusal (the most important test — review finding R1)
- Evidence/execution mutual constraint
- OOD/uncalibrated mapping with relay codes
- entity_ref format validation for all four entity types
- EvidenceReference round-trip and distinguishability
- Assessment and decision record validation via controlstore

Run with:
    PYTHONPATH=tools python3 tests/test_evidence.py

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
    read_record,
    write_record,
)
from dde.core.errors import Refusal
from dde.core.evidence import (
    ACTIONS,
    EVIDENCE_STATUSES,
    EXECUTION_OUTCOMES,
    EvidenceReference,
    validate_assessment,
    validate_decision,
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
    *,
    revision: int | None = None,
) -> Path:
    """Write a concept record directly to .dde/control/concepts/.

    Since #74 has not yet landed (no concepts.py, no 'concept' record
    type in RECORD_TYPES), we write the file directly.  The default
    concept_loader in write_record() reads from this path.
    """
    concepts_dir = project / CONTROL_DIR / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    if revision is not None:
        filename = f"{concept_id}-r{revision}.json"
    else:
        filename = f"{concept_id}.json"

    data = {
        "schema": "dde.intervention-concept.v1",
        "id": concept_id,
        "revision": revision or 1,
        "state": "active",
        "termination_authority": termination_authority,
        "disease_context": {"indication": "solid_tumors"},
        "target_pathway": {"gene": "CDK4"},
        "modality": "small_molecule",
        "created_at": _NOW,
    }
    path = concepts_dir / filename
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fixture: assessment record from design SS7 Step 3
# ---------------------------------------------------------------------------


def _step3_assessment() -> dict[str, Any]:
    """The worked example from design SS7 Step 3."""
    return {
        "schema": "dde.evidence-assessment.v1",
        "id": "AR-001",
        "concept_ref": "IC-001-r1",
        "claim": "CDK4 shows loss-of-function intolerance",
        "evidence_status": "supported",
        "execution_outcome": "completed",
        "requirement_ref": "GP-001/REQ-001@1",
        "evidence": {
            "artifact_path": "raw/genomics/CDK4.gnomad-constraint.analysis.json",
            "evidence_type": "genetic_constraint",
            "metric_name": "pLI",
            "metric_value": 0.95,
            "threshold_set": "gnomad-constraint@1.0",
            "method": "gnomad_v4",
        },
        "finding_ref": "findings/computational-biology/cdk4-genetic-constraint.md",
        "confidence": "high",
        "assessed_at": _NOW,
        "assessed_by": "computational-biologist",
    }


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
print("test_evidence.py — issue #75 evidence/decision contracts")
print("=" * 60)


# ---------------------------------------------------------------------------
# 1. Human-approval Refusal (THE most important test)
#
# These tests exercise the REAL write_record() path — no fictional
# fields on the decision record, no caller-supplied concept_loader.
# The concept record lives on disk and write_record()'s built-in
# default loader finds it.
# ---------------------------------------------------------------------------
print("\n--- Human-approval Refusal (real write path) ---")


def test_real_write_path_human_auth_no_approval_raises_refusal():
    """write_record() for a terminate decision on a concept with
    termination_authority='human' and no human_approval MUST raise
    Refusal (exit 9) — through the real, unmodified call path."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_to_disk(project, "IC-001", "human")
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            human_approval=None,
        )
        # No termination_authority on the decision record, no concept_loader
        # argument — the real write path must find the concept on disk.
        try:
            write_record(project, "decision", "DR-001", record)
            raise AssertionError("expected Refusal, got no exception")
        except Refusal as exc:
            assert exc.exit_code == 9, f"expected exit code 9, got {exc.exit_code}"
            assert "human approval" in exc.message.lower(), exc.message


_check(
    "REAL write_record: terminate + human concept + no approval => Refusal(9)",
    test_real_write_path_human_auth_no_approval_raises_refusal,
)


def test_real_write_path_human_auth_with_approval_succeeds():
    """The same write path WITH human_approval populated must succeed."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_to_disk(project, "IC-001", "human")
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            human_approval={
                "approver": "Dr. Smith",
                "approved_at": _NOW,
                "approval_method": "charter_authority",
                "evidence": None,
            },
        )
        path = write_record(project, "decision", "DR-001", record)
        assert path.is_file()


_check(
    "REAL write_record: terminate + human concept + approval => success",
    test_real_write_path_human_auth_with_approval_succeeds,
)


def test_real_write_path_program_lead_no_approval_succeeds():
    """A concept with termination_authority='program_lead' does NOT
    require human_approval through the real write path."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_to_disk(project, "IC-001", "program_lead")
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            human_approval=None,
        )
        path = write_record(project, "decision", "DR-001", record)
        assert path.is_file()


_check(
    "REAL write_record: terminate + program_lead concept + no approval => success",
    test_real_write_path_program_lead_no_approval_succeeds,
)


def test_real_write_path_versioned_concept_ref():
    """write_record() strips the -rN suffix to find the concept record."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_to_disk(project, "IC-001", "human", revision=3)
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001-r3"},
            human_approval=None,
        )
        try:
            write_record(project, "decision", "DR-001", record)
            raise AssertionError("expected Refusal")
        except Refusal as exc:
            assert exc.exit_code == 9


_check(
    "REAL write_record: versioned entity_ref (IC-001-r3) => finds concept",
    test_real_write_path_versioned_concept_ref,
)


def test_real_write_path_unknown_concept_safe_default():
    """When the concept record does not exist, the safe default
    (termination_authority='human') kicks in and Refusal fires."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        # No concept record written — IC-999 does not exist.
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-999"},
            human_approval=None,
        )
        try:
            write_record(project, "decision", "DR-001", record)
            raise AssertionError("expected Refusal for unknown concept")
        except Refusal as exc:
            assert exc.exit_code == 9


_check(
    "REAL write_record: unknown concept => safe default => Refusal(9)",
    test_real_write_path_unknown_concept_safe_default,
)


def test_real_write_path_unknown_concept_with_approval_succeeds():
    """When the concept is unknown but human_approval is provided,
    the write succeeds (safe default = require approval, not reject)."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-999"},
            human_approval={
                "approver": "Dr. Smith",
                "approved_at": _NOW,
                "approval_method": "charter_authority",
                "evidence": None,
            },
        )
        path = write_record(project, "decision", "DR-001", record)
        assert path.is_file()


_check(
    "REAL write_record: unknown concept + approval => success",
    test_real_write_path_unknown_concept_with_approval_succeeds,
)


# --- Unit tests for validate_decision with explicit concept_loader ---

print("\n--- Human-approval Refusal (unit tests) ---")


def test_validate_decision_concept_loader_human():
    """Refusal fires when concept_loader returns termination_authority='human'."""

    def loader(concept_id: str) -> dict[str, Any] | None:
        if concept_id == "IC-001":
            return {"termination_authority": "human"}
        return None

    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001-r2"},
        human_approval=None,
    )
    try:
        validate_decision(record, concept_loader=loader)
        raise AssertionError("expected Refusal")
    except Refusal as exc:
        assert exc.exit_code == 9


_check(
    "validate_decision: concept_loader(human) + no approval => Refusal(9)",
    test_validate_decision_concept_loader_human,
)


def test_validate_decision_concept_loader_program_lead():
    """concept_loader returning termination_authority='program_lead'
    allows termination without approval."""

    def loader(concept_id: str) -> dict[str, Any] | None:
        if concept_id == "IC-001":
            return {"termination_authority": "program_lead"}
        return None

    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval=None,
    )
    errors = validate_decision(record, concept_loader=loader)
    assert errors == [], f"unexpected errors: {errors}"


_check(
    "validate_decision: concept_loader(program_lead) + no approval => success",
    test_validate_decision_concept_loader_program_lead,
)


def test_validate_decision_no_loader_safe_default():
    """Without a concept_loader, terminate on a concept defaults to
    requiring human approval (safe failure)."""
    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval=None,
    )
    try:
        validate_decision(record)
        raise AssertionError("expected Refusal")
    except Refusal as exc:
        assert exc.exit_code == 9


_check(
    "validate_decision: no loader + no approval => safe default => Refusal(9)",
    test_validate_decision_no_loader_safe_default,
)


def test_non_terminate_no_approval_ok():
    """Non-terminate actions never require human_approval."""
    for action in ("advance_with_budget", "investigate", "pivot", "park"):
        record = _valid_decision(action=action)
        errors = validate_decision(record)
        assert errors == [], f"{action}: unexpected errors: {errors}"


_check(
    "non-terminate actions do not require human_approval",
    test_non_terminate_no_approval_ok,
)


def test_terminate_series_claim_no_approval_ok():
    """Terminate on series/claim entities does not trigger the
    human-approval gate (these are not named in human_reserved)."""
    for etype, eref in [
        ("series", "cdk4-series"),
        ("claim", "AR-001"),
    ]:
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": etype, "entity_ref": eref},
        )
        errors = validate_decision(record)
        assert errors == [], f"{etype}: unexpected errors: {errors}"


_check(
    "terminate on series/claim => no Refusal",
    test_terminate_series_claim_no_approval_ok,
)


# --- Fail-open regression tests (issue #176 S1 fix) ---

print("\n--- Fail-open authorization regression (issue #176) ---")


def test_fail_open_unrecognized_authority_requires_approval():
    """Issue #176 PoC: unrecognized termination_authority values like
    'autonomous', 'auto', 'agent', '', 0, False must NOT bypass the
    human-approval gate.  Before the fix, the gate only checked for
    term_auth == 'human', so any other non-None value silently
    passed through — a classic fail-open."""
    invalid_authorities = ["autonomous", "auto", "agent", "", 0, False]
    for bad_auth in invalid_authorities:

        def loader(concept_id: str, _auth=bad_auth) -> dict[str, Any] | None:
            if concept_id == "IC-001":
                return {"termination_authority": _auth}
            return None

        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            human_approval=None,
        )
        try:
            validate_decision(record, concept_loader=loader)
            raise AssertionError(
                f"expected Refusal for termination_authority={bad_auth!r}, "
                f"but no exception was raised"
            )
        except Refusal as exc:
            assert exc.exit_code == 9, (
                f"term_auth={bad_auth!r}: expected exit_code 9, got {exc.exit_code}"
            )


_check(
    "validate_decision: unrecognized authority values => Refusal(9) [#176]",
    test_fail_open_unrecognized_authority_requires_approval,
)


def test_program_lead_bypasses_approval():
    """program_lead is the only authority that legitimately bypasses
    human approval for concept termination."""

    def loader(concept_id: str) -> dict[str, Any] | None:
        if concept_id == "IC-001":
            return {"termination_authority": "program_lead"}
        return None

    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval=None,
    )
    errors = validate_decision(record, concept_loader=loader)
    assert errors == [], f"unexpected errors for program_lead: {errors}"


_check(
    "validate_decision: program_lead bypasses approval => success [#176]",
    test_program_lead_bypasses_approval,
)


def test_invalid_authority_with_approval_succeeds():
    """Even with an unrecognized authority value, providing valid
    human_approval must succeed — the gate only blocks when approval
    is missing."""
    invalid_authorities = ["autonomous", "auto", "agent", "", 0, False, None]
    for bad_auth in invalid_authorities:

        def loader(concept_id: str, _auth=bad_auth) -> dict[str, Any] | None:
            if concept_id == "IC-001":
                return {"termination_authority": _auth}
            return None

        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            human_approval={
                "approver": "Dr. Smith",
                "approved_at": _NOW,
                "approval_method": "charter_authority",
                "evidence": "Meeting notes 2026-09-08",
            },
        )
        errors = validate_decision(record, concept_loader=loader)
        assert errors == [], (
            f"term_auth={bad_auth!r} with approval should succeed, got errors: {errors}"
        )


_check(
    "validate_decision: invalid authority + approval => success [#176]",
    test_invalid_authority_with_approval_succeeds,
)


def test_case_variant_program_lead_requires_approval():
    """Issue #176: the allowlist is case-sensitive.  Case variants of
    'program_lead' such as 'Program_Lead', 'PROGRAM_LEAD', and
    'Program_lead' must NOT bypass the human-approval gate."""
    case_variants = ["Program_Lead", "PROGRAM_LEAD", "Program_lead"]
    for bad_auth in case_variants:

        def loader(concept_id: str, _auth=bad_auth) -> dict[str, Any] | None:
            if concept_id == "IC-001":
                return {"termination_authority": _auth}
            return None

        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            human_approval=None,
        )
        try:
            validate_decision(record, concept_loader=loader)
            raise AssertionError(
                f"expected Refusal for termination_authority={bad_auth!r}, "
                f"but no exception was raised"
            )
        except Refusal as exc:
            assert exc.exit_code == 9, (
                f"term_auth={bad_auth!r}: expected exit_code 9, got {exc.exit_code}"
            )


_check(
    "validate_decision: case-variant program_lead => Refusal(9) [#176]",
    test_case_variant_program_lead_requires_approval,
)


def test_none_authority_no_loader_requires_approval():
    """When no concept_loader is provided, term_auth stays None, which
    is not in the allowlist — must require approval (safe default)."""
    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval=None,
    )
    try:
        validate_decision(record)
        raise AssertionError("expected Refusal for None authority (no loader)")
    except Refusal as exc:
        assert exc.exit_code == 9


_check(
    "validate_decision: None authority (no loader) => Refusal(9) [#176]",
    test_none_authority_no_loader_requires_approval,
)


# --- Program termination gate (security audit fix) ---

print("\n--- Program termination gate ---")


def test_terminate_program_no_approval_raises_refusal():
    """A terminate decision on a program entity without human_approval
    MUST raise Refusal (exit 9) — program termination is unconditionally
    human-reserved per design principle #4 and program.yaml human_reserved."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "program", "entity_ref": "DEC-001"},
            human_approval=None,
        )
        try:
            write_record(project, "decision", "DR-002", record)
            raise AssertionError("expected Refusal, got no exception")
        except Refusal as exc:
            assert exc.exit_code == 9, f"expected exit code 9, got {exc.exit_code}"
            assert "program" in exc.message.lower(), exc.message


_check(
    "REAL write_record: terminate program + no approval => Refusal(9)",
    test_terminate_program_no_approval_raises_refusal,
)


def test_terminate_program_with_approval_succeeds():
    """Program termination WITH human_approval populated must succeed."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        record = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "program", "entity_ref": "DEC-001"},
            human_approval={
                "approver": "Dr. Smith",
                "approved_at": _NOW,
                "approval_method": "charter_authority",
                "evidence": "Meeting notes 2026-09-08",
            },
        )
        path = write_record(project, "decision", "DR-002", record)
        assert path.is_file()


_check(
    "REAL write_record: terminate program + approval => success",
    test_terminate_program_with_approval_succeeds,
)


def test_terminate_program_unit_test():
    """validate_decision directly: program terminate without approval
    raises Refusal."""
    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "program", "entity_ref": "DEC-001"},
        human_approval=None,
    )
    try:
        validate_decision(record)
        raise AssertionError("expected Refusal")
    except Refusal as exc:
        assert exc.exit_code == 9


_check(
    "validate_decision: terminate program + no approval => Refusal(9)",
    test_terminate_program_unit_test,
)


# ---------------------------------------------------------------------------
# 2. Evidence/execution mutual constraint
# ---------------------------------------------------------------------------
print("\n--- Evidence/execution mutual constraint ---")


def test_incomplete_execution_must_be_not_assessed():
    """execution_outcome != 'completed' with evidence_status != 'not_assessed'
    must be rejected."""
    for outcome in ("tool_unavailable", "tool_failed", "data_unavailable", "blocked"):
        for status in (
            "supported",
            "contradicted",
            "insufficient",
            "not_yet_applicable",
        ):
            record = _step3_assessment()
            record["execution_outcome"] = outcome
            record["evidence_status"] = status
            errors = validate_assessment(record)
            assert any("not_assessed" in e for e in errors), (
                f"expected mutual-constraint error for {outcome}/{status}, "
                f"got: {errors}"
            )


_check(
    "execution_outcome != completed + evidence_status != not_assessed => error",
    test_incomplete_execution_must_be_not_assessed,
)


def test_incomplete_execution_with_not_assessed_passes():
    """execution_outcome != 'completed' with evidence_status = 'not_assessed'
    is the valid combination."""
    record = _step3_assessment()
    record["execution_outcome"] = "tool_failed"
    record["evidence_status"] = "not_assessed"
    errors = validate_assessment(record)
    assert errors == [], f"unexpected errors: {errors}"


_check(
    "execution_outcome != completed + evidence_status = not_assessed => valid",
    test_incomplete_execution_with_not_assessed_passes,
)


def test_completed_with_any_status_passes():
    """execution_outcome = 'completed' allows any evidence_status."""
    for status in EVIDENCE_STATUSES:
        record = _step3_assessment()
        record["execution_outcome"] = "completed"
        record["evidence_status"] = status
        errors = validate_assessment(record)
        assert errors == [], f"status {status}: unexpected errors: {errors}"


_check(
    "execution_outcome = completed + any evidence_status => valid",
    test_completed_with_any_status_passes,
)


# ---------------------------------------------------------------------------
# 3. OOD/uncalibrated mapping
# ---------------------------------------------------------------------------
print("\n--- OOD/uncalibrated mapping ---")


def test_ood_insufficient_with_relay_is_valid():
    """execution_outcome=completed + evidence_status=insufficient + relay code
    is the valid OOD/uncalibrated pattern."""
    record = _step3_assessment()
    record["evidence_status"] = "insufficient"
    record["execution_outcome"] = "completed"
    # The relay code would be on the evidence or sidecar, not on the assessment
    # record itself — the assessment records the status, the sidecar carries the code.
    # The assessment is valid with this combination.
    errors = validate_assessment(record)
    assert errors == [], f"unexpected errors: {errors}"


_check(
    "OOD mapping: completed + insufficient => valid",
    test_ood_insufficient_with_relay_is_valid,
)


# ---------------------------------------------------------------------------
# 4. entity_ref format validation
# ---------------------------------------------------------------------------
print("\n--- entity_ref format validation ---")


def test_entity_ref_concept_valid():
    """Concept entity_ref: IC-NNN or IC-NNN-rN."""
    for ref in ("IC-001", "IC-042", "IC-001-r3", "IC-0001-r12"):
        record = _valid_decision(
            affected_entity={"entity_type": "concept", "entity_ref": ref},
        )
        errors = validate_decision(record)
        assert errors == [], f"concept ref {ref}: unexpected errors: {errors}"


_check("entity_ref concept (IC-NNN, IC-NNN-rN) => valid", test_entity_ref_concept_valid)


def test_entity_ref_concept_invalid():
    """Invalid concept references are rejected."""
    for ref in ("XC-001", "IC-01", "001", "IC001"):
        record = _valid_decision(
            affected_entity={"entity_type": "concept", "entity_ref": ref},
        )
        errors = validate_decision(record)
        assert any("entity_ref" in e for e in errors), (
            f"concept ref {ref} should fail: {errors}"
        )


_check("entity_ref concept invalid format => error", test_entity_ref_concept_invalid)


def test_entity_ref_claim_valid():
    """Claim entity_ref: AR-NNN."""
    record = _valid_decision(
        affected_entity={"entity_type": "claim", "entity_ref": "AR-005"},
    )
    errors = validate_decision(record)
    assert errors == [], f"unexpected errors: {errors}"


_check("entity_ref claim (AR-NNN) => valid", test_entity_ref_claim_valid)


def test_entity_ref_claim_invalid():
    """Invalid claim references are rejected."""
    record = _valid_decision(
        affected_entity={"entity_type": "claim", "entity_ref": "DR-005"},
    )
    errors = validate_decision(record)
    assert any("entity_ref" in e for e in errors), f"should fail: {errors}"


_check("entity_ref claim invalid format => error", test_entity_ref_claim_invalid)


def test_entity_ref_program_valid():
    """Program entity_ref: DEC-NNN."""
    record = _valid_decision(
        affected_entity={"entity_type": "program", "entity_ref": "DEC-001"},
    )
    errors = validate_decision(record)
    assert errors == [], f"unexpected errors: {errors}"


_check("entity_ref program (DEC-NNN) => valid", test_entity_ref_program_valid)


def test_entity_ref_program_invalid():
    """Invalid program references are rejected."""
    record = _valid_decision(
        affected_entity={"entity_type": "program", "entity_ref": "IC-001"},
    )
    errors = validate_decision(record)
    assert any("entity_ref" in e for e in errors), f"should fail: {errors}"


_check("entity_ref program invalid format => error", test_entity_ref_program_invalid)


def test_entity_ref_series_valid():
    """Series entity_ref: hyphenated lowercase."""
    for ref in ("cdk4-series", "alk-scaffold-3", "series1"):
        record = _valid_decision(
            affected_entity={"entity_type": "series", "entity_ref": ref},
        )
        errors = validate_decision(record)
        assert errors == [], f"series ref {ref}: unexpected errors: {errors}"


_check(
    "entity_ref series (hyphenated lowercase) => valid", test_entity_ref_series_valid
)


def test_entity_ref_series_invalid():
    """Series names with uppercase or special chars are rejected."""
    for ref in ("CDK4-Series", "alk scaffold", "series_1"):
        record = _valid_decision(
            affected_entity={"entity_type": "series", "entity_ref": ref},
        )
        errors = validate_decision(record)
        assert any("entity_ref" in e for e in errors), (
            f"series ref {ref} should fail: {errors}"
        )


_check("entity_ref series invalid format => error", test_entity_ref_series_invalid)


def test_entity_type_invalid():
    """Unknown entity_type is rejected."""
    record = _valid_decision(
        affected_entity={"entity_type": "unknown", "entity_ref": "X-001"},
    )
    errors = validate_decision(record)
    assert any("entity_type" in e for e in errors), f"should fail: {errors}"


_check("entity_type unknown => error", test_entity_type_invalid)


# ---------------------------------------------------------------------------
# 5. EvidenceReference round-trip and distinguishability
# ---------------------------------------------------------------------------
print("\n--- EvidenceReference round-trip ---")


def test_evidence_reference_round_trip():
    """EvidenceReference from_dict -> to_dict preserves data."""
    data = {
        "artifact_path": "raw/structures/cdk4-1hck.pocket.analysis.json",
        "evidence_type": "structural_druggability",
        "metric_name": "drug_score",
        "metric_value": 0.939,
        "threshold_set": "pocket@1.0",
        "method": "fpocket_4.2",
    }
    ref = EvidenceReference.from_dict(data)
    out = ref.to_dict()
    assert out["artifact_path"] == data["artifact_path"]
    assert out["evidence_type"] == data["evidence_type"]
    assert out["metric_value"] == data["metric_value"]
    assert out["method"] == data["method"]


_check("EvidenceReference round-trip", test_evidence_reference_round_trip)


def test_evidence_reference_distinguishability():
    """Two assessments with different evidence_type/method for the same
    concept are both valid and distinguishable (worked contradiction
    example from design SS2.2)."""
    supported = _step3_assessment()
    supported["id"] = "AR-003"
    supported["claim"] = "CDK4 has a druggable binding pocket"
    supported["evidence_status"] = "supported"
    supported["evidence"] = {
        "artifact_path": "raw/structures/cdk4-1hck.pocket.analysis.json",
        "evidence_type": "structural_druggability",
        "metric_name": "drug_score",
        "metric_value": 0.939,
        "threshold_set": "pocket@1.0",
        "method": "fpocket_4.2",
    }

    contradicted = _step3_assessment()
    contradicted["id"] = "AR-004"
    contradicted["claim"] = "CDK4 has a druggable binding pocket"
    contradicted["evidence_status"] = "contradicted"
    contradicted["evidence"] = {
        "artifact_path": "raw/structures/cdk4-2w1d.pocket.analysis.json",
        "evidence_type": "structural_druggability",
        "metric_name": "drug_score",
        "metric_value": 0.293,
        "threshold_set": "pocket@1.0",
        "method": "fpocket_4.2",
    }

    errors_s = validate_assessment(supported)
    errors_c = validate_assessment(contradicted)
    assert errors_s == [], f"supported errors: {errors_s}"
    assert errors_c == [], f"contradicted errors: {errors_c}"

    # They are distinguishable by id, evidence_status, and artifact_path
    ref_s = EvidenceReference.from_dict(supported["evidence"])
    ref_c = EvidenceReference.from_dict(contradicted["evidence"])
    assert ref_s.artifact_path != ref_c.artifact_path
    assert ref_s.metric_value != ref_c.metric_value


_check(
    "EvidenceReference distinguishability (supported vs contradicted)",
    test_evidence_reference_distinguishability,
)


# ---------------------------------------------------------------------------
# 6. Control store round-trip
# ---------------------------------------------------------------------------
print("\n--- Control store round-trip ---")


def test_assessment_controlstore_round_trip():
    """Assessment records write and read through controlstore."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        record = _step3_assessment()
        path = write_record(project, "assessment", "AR-001", record)
        assert path.is_file()
        loaded = read_record(project, "assessment", "AR-001")
        assert loaded["id"] == "AR-001"
        assert loaded["evidence_status"] == "supported"


_check(
    "assessment controlstore write/read round-trip",
    test_assessment_controlstore_round_trip,
)


def test_decision_controlstore_round_trip():
    """Decision records write and read through controlstore."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        record = _valid_decision()
        path = write_record(project, "decision", "DR-001", record)
        assert path.is_file()
        loaded = read_record(project, "decision", "DR-001")
        assert loaded["id"] == "DR-001"
        assert loaded["action"] == "advance_with_budget"


_check(
    "decision controlstore write/read round-trip", test_decision_controlstore_round_trip
)


# ---------------------------------------------------------------------------
# 7. Assessment validation edge cases
# ---------------------------------------------------------------------------
print("\n--- Assessment validation edge cases ---")


def test_assessment_missing_required_fields():
    """Missing required fields produce validation errors."""
    errors = validate_assessment({})
    assert any("missing required fields" in e for e in errors)


_check(
    "assessment missing required fields => error",
    test_assessment_missing_required_fields,
)


def test_assessment_schema_required():
    """schema field is required on assessment records (design Appendix A.2)."""
    record = _step3_assessment()
    del record["schema"]
    errors = validate_assessment(record)
    assert any("missing required fields" in e and "schema" in e for e in errors), (
        f"expected schema in missing required fields, got: {errors}"
    )


_check("assessment schema field required", test_assessment_schema_required)


def test_assessment_bad_id_format():
    """Assessment ID must match AR-NNN."""
    record = _step3_assessment()
    record["id"] = "DR-001"  # wrong prefix
    errors = validate_assessment(record)
    assert any("AR-NNN" in e for e in errors)


_check("assessment bad id format => error", test_assessment_bad_id_format)


def test_assessment_invalid_evidence_status():
    """Invalid evidence_status is rejected."""
    record = _step3_assessment()
    record["evidence_status"] = "maybe"
    errors = validate_assessment(record)
    assert any("evidence_status" in e for e in errors)


_check(
    "assessment invalid evidence_status => error",
    test_assessment_invalid_evidence_status,
)


def test_assessment_invalid_execution_outcome():
    """Invalid execution_outcome is rejected."""
    record = _step3_assessment()
    record["execution_outcome"] = "crashed"
    errors = validate_assessment(record)
    assert any("execution_outcome" in e for e in errors)


_check(
    "assessment invalid execution_outcome => error",
    test_assessment_invalid_execution_outcome,
)


def test_assessment_bad_schema():
    """Wrong schema string is rejected."""
    record = _step3_assessment()
    record["schema"] = "dde.wrong.v1"
    errors = validate_assessment(record)
    assert any("schema" in e for e in errors)


_check("assessment wrong schema string => error", test_assessment_bad_schema)


def test_assessment_supersedes_format():
    """supersedes must match AR-NNN if present."""
    record = _step3_assessment()
    record["supersedes"] = "WRONG-001"
    errors = validate_assessment(record)
    assert any("supersedes" in e for e in errors)


_check("assessment supersedes bad format => error", test_assessment_supersedes_format)


# ---------------------------------------------------------------------------
# 8. Decision validation edge cases
# ---------------------------------------------------------------------------
print("\n--- Decision validation edge cases ---")


def test_decision_missing_required_fields():
    """Missing required fields produce validation errors."""
    errors = validate_decision({})
    assert any("missing required fields" in e for e in errors)


_check(
    "decision missing required fields => error", test_decision_missing_required_fields
)


def test_decision_schema_required():
    """schema field is required on decision records (design Appendix A.3)."""
    record = _valid_decision()
    del record["schema"]
    errors = validate_decision(record)
    assert any("missing required fields" in e and "schema" in e for e in errors), (
        f"expected schema in missing required fields, got: {errors}"
    )


_check("decision schema field required", test_decision_schema_required)


def test_decision_bad_id_format():
    """Decision ID must match DR-NNN."""
    record = _valid_decision(id="AR-001")
    errors = validate_decision(record)
    assert any("DR-NNN" in e for e in errors)


_check("decision bad id format => error", test_decision_bad_id_format)


def test_decision_invalid_action():
    """Invalid action is rejected."""
    record = _valid_decision(action="destroy")
    errors = validate_decision(record)
    assert any("action" in e for e in errors)


_check("decision invalid action => error", test_decision_invalid_action)


def test_decision_bad_supporting_assessments():
    """supporting_assessments entries must match AR-NNN."""
    record = _valid_decision(supporting_assessments=["AR-001", "BAD-002"])
    errors = validate_decision(record)
    assert any("supporting_assessments" in e for e in errors)


_check(
    "decision bad supporting_assessments entry => error",
    test_decision_bad_supporting_assessments,
)


def test_decision_human_approval_missing_fields():
    """human_approval with missing required sub-fields."""

    # Use a concept_loader returning program_lead so the Refusal gate
    # doesn't fire — we're testing the sub-schema validation here.
    def loader(cid: str) -> dict[str, Any] | None:
        return {"termination_authority": "program_lead"}

    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval={"approver": "Dr. X"},  # missing approved_at, approval_method
    )
    errors = validate_decision(record, concept_loader=loader)
    assert any("human_approval missing required fields" in e for e in errors)


_check(
    "decision human_approval missing sub-fields => error",
    test_decision_human_approval_missing_fields,
)


# ---------------------------------------------------------------------------
# 9. Enum sets sanity
# ---------------------------------------------------------------------------
print("\n--- Enum sets ---")


def test_enum_sets_complete():
    """Enum sets contain the expected values."""
    assert "supported" in EVIDENCE_STATUSES
    assert "contradicted" in EVIDENCE_STATUSES
    assert "insufficient" in EVIDENCE_STATUSES
    assert "not_assessed" in EVIDENCE_STATUSES
    assert "not_yet_applicable" in EVIDENCE_STATUSES
    assert len(EVIDENCE_STATUSES) == 5

    assert "completed" in EXECUTION_OUTCOMES
    assert "tool_unavailable" in EXECUTION_OUTCOMES
    assert "tool_failed" in EXECUTION_OUTCOMES
    assert "data_unavailable" in EXECUTION_OUTCOMES
    assert "blocked" in EXECUTION_OUTCOMES
    assert len(EXECUTION_OUTCOMES) == 5

    assert "advance_with_budget" in ACTIONS
    assert "investigate" in ACTIONS
    assert "pivot" in ACTIONS
    assert "park" in ACTIONS
    assert "terminate" in ACTIONS
    assert len(ACTIONS) == 5


_check("enum sets contain expected values", test_enum_sets_complete)


# ---------------------------------------------------------------------------
# 10. Step 3 fixture from design SS7
# ---------------------------------------------------------------------------
print("\n--- Design SS7 Step 3 fixture ---")


def test_step3_fixture_validates():
    """The Step 3 assessment record from the worked example is valid."""
    record = _step3_assessment()
    errors = validate_assessment(record)
    assert errors == [], f"Step 3 fixture errors: {errors}"


_check("design SS7 Step 3 fixture validates cleanly", test_step3_fixture_validates)


def test_step3_fixture_controlstore_write():
    """Step 3 fixture writes through controlstore without error."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        record = _step3_assessment()
        path = write_record(project, "assessment", "AR-001", record)
        assert path.is_file()
        loaded = read_record(project, "assessment", "AR-001")
        assert loaded["evidence"]["metric_value"] == 0.95


_check(
    "design SS7 Step 3 fixture writes via controlstore",
    test_step3_fixture_controlstore_write,
)


# ---------------------------------------------------------------------------
# 11. ensure_control_dirs creates new subdirectories
# ---------------------------------------------------------------------------
print("\n--- Control directory creation ---")


def test_ensure_control_dirs_creates_assessment_decision_dirs():
    """ensure_control_dirs creates assessments/ and decisions/ subdirectories."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        (project / ".dde").mkdir()
        ensure_control_dirs(project)
        assert (project / ".dde" / "control" / "assessments").is_dir()
        assert (project / ".dde" / "control" / "decisions").is_dir()


_check(
    "ensure_control_dirs creates assessments/ and decisions/",
    test_ensure_control_dirs_creates_assessment_decision_dirs,
)


# ---------------------------------------------------------------------------
# 12. Defense-in-depth: concept_loader format check
# ---------------------------------------------------------------------------
print("\n--- Concept loader defense-in-depth ---")


def test_concept_loader_rejects_malformed_id():
    """The default concept_loader rejects IDs that don't match IC-NNN,
    even if a file with that name exists on disk (defense-in-depth)."""
    from dde.core.controlstore import _default_concept_loader

    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        # Write a file with a malformed name to disk
        concepts_dir = project / CONTROL_DIR / "concepts"
        concepts_dir.mkdir(parents=True, exist_ok=True)
        concepts_dir / "../../evil.json"
        # Don't actually write — just confirm the loader rejects the ID
        loader = _default_concept_loader(project)
        result = loader("../../evil")
        assert result is None, "should reject path-traversal ID"
        result = loader("IC-01")  # too short
        assert result is None, "should reject short ID"
        result = loader("XC-001")
        assert result is None, "should reject wrong prefix"


_check(
    "concept_loader rejects malformed IDs (defense-in-depth)",
    test_concept_loader_rejects_malformed_id,
)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
