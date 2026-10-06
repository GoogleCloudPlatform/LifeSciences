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

"""Governance-gate regression tests for evidence.py.

Covers:
  - Issue #245 (S1): Empty/whitespace human_approval strings bypass gate
  - Issue #286: Null affected_entity triggers is-None short-circuit

Run with:
    PYTHONPATH=tools python3 tests/test_evidence_govgate.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.core.errors import Refusal
from dde.core.evidence import validate_decision

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


def _human_concept_loader(concept_id: str) -> dict[str, Any] | None:
    """concept_loader that returns termination_authority='human'."""
    if concept_id == "IC-001":
        return {"termination_authority": "human"}
    return None


# ===========================================================================
# Tests
# ===========================================================================

print("=" * 60)
print("test_evidence_govgate.py -- issues #245, #286 regression")
print("=" * 60)


# ---------------------------------------------------------------------------
# Issue #245: Empty/whitespace human_approval strings bypass gate
# ---------------------------------------------------------------------------
print("\n--- Issue #245: whitespace/empty human_approval strings ---")


def test_245_whitespace_approval_rejected():
    """validate_decision() with whitespace-only human_approval fields on a
    terminate+concept action MUST either raise Refusal or return validation
    errors.  Before the fix, the whitespace strings passed the isinstance
    check and the gate was bypassed."""
    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval={
            "approver": "   ",
            "approved_at": "   ",
            "approval_method": "   ",
        },
    )
    try:
        errors = validate_decision(record, concept_loader=_human_concept_loader)
    except Refusal:
        # Refusal is also acceptable -- means the gate caught the
        # blank approval and refused.  This won't happen with the
        # current fix (errors are returned before the gate runs),
        # but either path is safe.
        return

    assert errors, (
        "expected validation errors for whitespace-only human_approval "
        f"fields, got none: {errors}"
    )
    assert any("empty or whitespace" in e for e in errors), (
        f"expected whitespace rejection errors, got: {errors}"
    )


_check(
    "#245 regression: whitespace-only human_approval => errors",
    test_245_whitespace_approval_rejected,
)


def test_245_empty_string_approval_rejected():
    """Same as above but with empty strings '' instead of whitespace."""
    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval={
            "approver": "",
            "approved_at": "",
            "approval_method": "",
        },
    )
    try:
        errors = validate_decision(record, concept_loader=_human_concept_loader)
    except Refusal:
        return

    assert errors, (
        "expected validation errors for empty-string human_approval "
        f"fields, got none: {errors}"
    )
    assert any("empty or whitespace" in e for e in errors), (
        f"expected empty-string rejection errors, got: {errors}"
    )


_check(
    "#245 regression: empty-string human_approval => errors",
    test_245_empty_string_approval_rejected,
)


def test_245_valid_approval_still_passes():
    """Valid non-empty human_approval with a terminate action must still
    pass (zero errors, no Refusal)."""
    record = _valid_decision(
        action="terminate",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
        human_approval={
            "approver": "Dr. Smith",
            "approved_at": _NOW,
            "approval_method": "charter_authority",
        },
    )
    errors = validate_decision(record, concept_loader=_human_concept_loader)
    assert errors == [], f"valid approval should pass, got errors: {errors}"


_check(
    "#245 positive: valid non-empty human_approval => success",
    test_245_valid_approval_still_passes,
)


# ---------------------------------------------------------------------------
# Issue #286: Null affected_entity triggers is-None short-circuit
# ---------------------------------------------------------------------------
print("\n--- Issue #286: null affected_entity bypass ---")


def test_286_null_entity_terminate_rejected():
    """validate_decision() with action='terminate' and affected_entity=None
    MUST either raise Refusal or return validation errors (not silently
    pass).  Before the fix, entity is None skipped all dict/entity_type
    validation and the terminate gate resolved etype to None, bypassing
    both the concept and program gates."""
    record = _valid_decision(
        action="terminate",
        affected_entity=None,
        human_approval=None,
    )
    try:
        errors = validate_decision(record)
    except Refusal:
        # Also acceptable -- the gate caught it.
        return

    assert errors, (
        "expected validation errors for affected_entity=None with "
        f"action='terminate', got none: {errors}"
    )
    assert any("affected_entity" in e for e in errors), (
        f"expected affected_entity error, got: {errors}"
    )


_check(
    "#286 regression: affected_entity=None + terminate => errors",
    test_286_null_entity_terminate_rejected,
)


def test_286_null_entity_non_terminate_rejected():
    """affected_entity=None with a non-terminate action should also return
    an error for affected_entity not being a dict."""
    record = _valid_decision(
        action="investigate",
        affected_entity=None,
    )
    errors = validate_decision(record)
    assert errors, (
        "expected validation errors for affected_entity=None with "
        f"action='investigate', got none: {errors}"
    )
    assert any("affected_entity" in e for e in errors), (
        f"expected affected_entity error, got: {errors}"
    )


_check(
    "#286 regression: affected_entity=None + non-terminate => errors",
    test_286_null_entity_non_terminate_rejected,
)


def test_286_valid_entity_still_passes():
    """Valid affected_entity dict still works correctly."""
    record = _valid_decision(
        action="advance_with_budget",
        affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
    )
    errors = validate_decision(record)
    assert errors == [], f"valid entity should pass, got errors: {errors}"


_check(
    "#286 positive: valid affected_entity dict => success",
    test_286_valid_entity_still_passes,
)


# ---------------------------------------------------------------------------
# Combined scenario: null entity + terminate must not silently pass
# ---------------------------------------------------------------------------
print("\n--- Combined: null entity + terminate gate integrity ---")


def test_286_null_entity_does_not_reach_terminate_gate():
    """When affected_entity=None, the validator must catch it as a type
    error BEFORE reaching the terminate gate.  The errors list will be
    non-empty, so the ``if action == 'terminate' and not errors:`` guard
    prevents the gate from executing with etype=None."""
    record = _valid_decision(
        action="terminate",
        affected_entity=None,
        human_approval=None,
    )
    # Must not raise Refusal (the type error should block before gate)
    # and must return errors.
    errors = validate_decision(record)
    assert errors, "expected errors for None affected_entity"
    has_entity_error = any("affected_entity" in e for e in errors)
    assert has_entity_error, f"expected affected_entity error, got: {errors}"


_check(
    "#286 combined: null entity blocks before terminate gate",
    test_286_null_entity_does_not_reach_terminate_gate,
)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
