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

"""Regression tests for biomarker rationale governance gate bypass (Issue #289).

The rationale check in ``validate_biomarker()`` previously used a condition
that only rejected ``None`` and empty/whitespace strings but silently
permitted non-string, non-null values like ``False``, ``0``, ``[]``, or
``{}``.  These tests verify that the fix correctly rejects all non-string
rationale values when status is ``'unknown'`` or ``'not_applicable'``.

Run with:
    PYTHONPATH=tools python3 tests/test_concepts_govgate_289.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.core.concepts import validate_biomarker

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _make_biomarker_entry(**overrides: Any) -> dict[str, Any]:
    """Build a minimal biomarker entry with optional overrides."""
    entry: dict[str, Any] = {
        "name": "PD-L1",
        "category": "patient_selection",
        "status": "unknown",
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tests: validate_biomarker rejects non-string rationale (Issue #289)
# ---------------------------------------------------------------------------


def test_rationale_false_rejected() -> None:
    """rationale=False must not bypass the rationale requirement."""
    entry = _make_biomarker_entry(status="unknown", rationale=False)
    errors = validate_biomarker(entry, 0)
    assert any("rationale" in e for e in errors), (
        f"False bypassed rationale gate, errors={errors}"
    )


def test_rationale_zero_rejected() -> None:
    """rationale=0 must not bypass the rationale requirement."""
    entry = _make_biomarker_entry(status="unknown", rationale=0)
    errors = validate_biomarker(entry, 0)
    assert any("rationale" in e for e in errors), (
        f"0 bypassed rationale gate, errors={errors}"
    )


def test_rationale_empty_list_rejected() -> None:
    """rationale=[] must not bypass the rationale requirement."""
    entry = _make_biomarker_entry(status="unknown", rationale=[])
    errors = validate_biomarker(entry, 0)
    assert any("rationale" in e for e in errors), (
        f"[] bypassed rationale gate, errors={errors}"
    )


def test_rationale_empty_dict_rejected() -> None:
    """rationale={} must not bypass the rationale requirement."""
    entry = _make_biomarker_entry(status="unknown", rationale={})
    errors = validate_biomarker(entry, 0)
    assert any("rationale" in e for e in errors), (
        f"{{}} bypassed rationale gate, errors={errors}"
    )


def test_rationale_valid_string_accepted() -> None:
    """rationale='Valid scientific rationale' must pass validation."""
    entry = _make_biomarker_entry(
        status="unknown", rationale="Valid scientific rationale"
    )
    errors = validate_biomarker(entry, 0)
    assert not errors, f"valid rationale rejected, errors={errors}"


def test_rationale_none_rejected() -> None:
    """rationale=None must be rejected when status requires rationale."""
    entry = _make_biomarker_entry(status="unknown", rationale=None)
    errors = validate_biomarker(entry, 0)
    assert any("rationale is required" in e for e in errors), (
        f"None did not trigger rationale required error, errors={errors}"
    )


def test_rationale_empty_string_rejected() -> None:
    """rationale='' must be rejected when status requires rationale."""
    entry = _make_biomarker_entry(status="unknown", rationale="")
    errors = validate_biomarker(entry, 0)
    assert any("rationale is required" in e for e in errors), (
        f"empty string did not trigger rationale required error, errors={errors}"
    )


def test_rationale_whitespace_rejected() -> None:
    """rationale='  ' must be rejected when status requires rationale."""
    entry = _make_biomarker_entry(status="unknown", rationale="  ")
    errors = validate_biomarker(entry, 0)
    assert any("rationale is required" in e for e in errors), (
        f"whitespace did not trigger rationale required error, errors={errors}"
    )


def test_rationale_not_applicable_false_rejected() -> None:
    """rationale=False with status='not_applicable' must also be rejected."""
    entry = _make_biomarker_entry(status="not_applicable", rationale=False)
    errors = validate_biomarker(entry, 0)
    assert any("rationale" in e for e in errors), (
        f"False bypassed rationale gate for not_applicable, errors={errors}"
    )


def test_rationale_type_error_for_known_status() -> None:
    """Non-string rationale with status='known' should still get a type error."""
    entry = _make_biomarker_entry(status="known", rationale=42)
    errors = validate_biomarker(entry, 0)
    assert any("rationale" in e and "string" in e for e in errors), (
        f"non-string rationale with known status not flagged, errors={errors}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Issue #289 regressions: non-string rationale must be rejected
        ("289_rationale_false_rejected", test_rationale_false_rejected),
        ("289_rationale_zero_rejected", test_rationale_zero_rejected),
        ("289_rationale_empty_list_rejected", test_rationale_empty_list_rejected),
        ("289_rationale_empty_dict_rejected", test_rationale_empty_dict_rejected),
        # Positive case
        ("289_rationale_valid_string_accepted", test_rationale_valid_string_accepted),
        # Existing behavior preserved
        ("289_rationale_none_rejected", test_rationale_none_rejected),
        ("289_rationale_empty_string_rejected", test_rationale_empty_string_rejected),
        ("289_rationale_whitespace_rejected", test_rationale_whitespace_rejected),
        # Cross-status checks
        (
            "289_rationale_not_applicable_false_rejected",
            test_rationale_not_applicable_false_rejected,
        ),
        (
            "289_rationale_type_error_known_status",
            test_rationale_type_error_for_known_status,
        ),
    ]

    print(f"\nRunning {len(tests)} governance-gate regression tests (Issue #289)...\n")
    for name, fn in tests:
        _check(name, fn)

    print(f"\n{_PASS} passed, {_FAIL} failed out of {_PASS + _FAIL} tests")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
