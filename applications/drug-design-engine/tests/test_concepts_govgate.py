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

"""Regression tests for charter-linkage governance gate bypass (Issue #220).

The charter-linkage gate in ``check_charter_linkage()`` previously used a
condition that only rejected ``None`` and empty/whitespace strings but
silently permitted non-string, non-null values like ``False``, ``0``,
``[]``, or ``{}``.  These tests verify that the fix correctly rejects
all non-string values and that ``validate_concept()`` also catches
non-string ``charter_ref`` at the schema level.

Run with:
    PYTHONPATH=tools python3 tests/test_concepts_govgate.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
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
    CONCEPT_SCHEMA,
    check_charter_linkage,
    validate_concept,
)
from dde.core.errors import Refusal

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
# Tests: check_charter_linkage rejects non-string values (Issue #220)
# ---------------------------------------------------------------------------


def test_charter_linkage_rejects_boolean_false() -> None:
    """charter_ref=False must not bypass the governance gate."""
    msg = check_charter_linkage({"charter_ref": False}, "active")
    assert msg is not None, "False bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_rejects_integer_zero() -> None:
    """charter_ref=0 must not bypass the governance gate."""
    msg = check_charter_linkage({"charter_ref": 0}, "active")
    assert msg is not None, "0 bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_rejects_integer_nonzero() -> None:
    """charter_ref=123 must not bypass the governance gate."""
    msg = check_charter_linkage({"charter_ref": 123}, "active")
    assert msg is not None, "123 bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_rejects_empty_list() -> None:
    """charter_ref=[] must not bypass the governance gate."""
    msg = check_charter_linkage({"charter_ref": []}, "active")
    assert msg is not None, "[] bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_rejects_empty_dict() -> None:
    """charter_ref={} must not bypass the governance gate."""
    msg = check_charter_linkage({"charter_ref": {}}, "active")
    assert msg is not None, "{} bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_rejects_none() -> None:
    """charter_ref=None must be rejected."""
    msg = check_charter_linkage({"charter_ref": None}, "active")
    assert msg is not None, "None bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_rejects_empty_string() -> None:
    """charter_ref='' must be rejected."""
    msg = check_charter_linkage({"charter_ref": ""}, "active")
    assert msg is not None, "empty string bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_rejects_whitespace() -> None:
    """charter_ref='  ' must be rejected."""
    msg = check_charter_linkage({"charter_ref": "  "}, "active")
    assert msg is not None, "whitespace bypassed charter-linkage gate"
    assert "charter_ref" in msg


def test_charter_linkage_accepts_valid_string() -> None:
    """charter_ref='DEC-001' must be accepted."""
    msg = check_charter_linkage({"charter_ref": "DEC-001"}, "active")
    assert msg is None, f"valid charter_ref rejected: {msg}"


def test_charter_linkage_ignores_non_active_state() -> None:
    """Gate only applies to 'active' transition; other states pass through."""
    msg = check_charter_linkage({"charter_ref": False}, "draft")
    assert msg is None, f"non-active state blocked: {msg}"


# ---------------------------------------------------------------------------
# Tests: validate_concept catches non-string charter_ref
# ---------------------------------------------------------------------------


def test_validate_concept_rejects_non_string_charter_ref() -> None:
    """validate_concept() should report a schema error for charter_ref=False."""
    record = _make_minimal_concept(state="draft", charter_ref=False)
    errors = validate_concept(record)
    assert any("charter_ref must be a string or null" in e for e in errors), (
        f"expected charter_ref type error, got: {errors}"
    )


def test_validate_concept_active_with_non_string_charter_ref_raises_refusal() -> None:
    """Active concept with charter_ref=0 must raise Refusal."""
    record = _make_minimal_concept(state="active", charter_ref=0)
    try:
        validate_concept(record)
        assert False, "should have raised Refusal"
    except Refusal as exc:
        assert "charter_ref" in exc.message
        assert exc.exit_code == 9


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # check_charter_linkage rejects non-string values
        ("charter_rejects_boolean_false", test_charter_linkage_rejects_boolean_false),
        ("charter_rejects_integer_zero", test_charter_linkage_rejects_integer_zero),
        (
            "charter_rejects_integer_nonzero",
            test_charter_linkage_rejects_integer_nonzero,
        ),
        ("charter_rejects_empty_list", test_charter_linkage_rejects_empty_list),
        ("charter_rejects_empty_dict", test_charter_linkage_rejects_empty_dict),
        ("charter_rejects_none", test_charter_linkage_rejects_none),
        ("charter_rejects_empty_string", test_charter_linkage_rejects_empty_string),
        ("charter_rejects_whitespace", test_charter_linkage_rejects_whitespace),
        ("charter_accepts_valid_string", test_charter_linkage_accepts_valid_string),
        (
            "charter_ignores_non_active_state",
            test_charter_linkage_ignores_non_active_state,
        ),
        # validate_concept catches non-string charter_ref
        (
            "validate_rejects_non_string_charter_ref",
            test_validate_concept_rejects_non_string_charter_ref,
        ),
        (
            "validate_active_non_string_charter_refusal",
            test_validate_concept_active_with_non_string_charter_ref_raises_refusal,
        ),
    ]

    print(f"\nRunning {len(tests)} governance-gate regression tests (Issue #220)...\n")
    for name, fn in tests:
        _run(name, fn)

    print(f"\n{_PASS} passed, {_FAIL} failed out of {_PASS + _FAIL} tests")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
