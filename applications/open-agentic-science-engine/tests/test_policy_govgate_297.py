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

"""Governance-gate regression tests for policy evidence_type matching.

Covers:
  - Issue #297: Null/None values triggering false-positive policy matching

Run with:
    PYTHONPATH=tools python3 tests/test_policy_govgate_297.py

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

from dde.core.policy import match_assessment_to_requirement

# ---------------------------------------------------------------------------
# Helpers
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
# Issue #297 regression tests
# ---------------------------------------------------------------------------


def test_297_req_none_assessment_empty_evidence() -> None:
    """requirement evidence_type=None + empty evidence dict → matches=False."""
    req = {"evidence_type": None}
    assessment = {"evidence": {}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is False, (
        f"expected matches=False, got {result['matches']}"
    )


def test_297_both_evidence_type_none() -> None:
    """Both sides evidence_type=None → matches=False."""
    req = {"evidence_type": None}
    assessment = {"evidence": {"evidence_type": None}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is False, (
        f"expected matches=False, got {result['matches']}"
    )


def test_297_both_evidence_type_empty_string() -> None:
    """Both sides evidence_type='' → matches=False."""
    req = {"evidence_type": ""}
    assessment = {"evidence": {"evidence_type": ""}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is False, (
        f"expected matches=False, got {result['matches']}"
    )


def test_297_same_nonempty_evidence_type_matches() -> None:
    """Both sides same non-empty evidence_type → matches=True."""
    req = {"evidence_type": "genetic_constraint"}
    assessment = {"evidence": {"evidence_type": "genetic_constraint"}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is True, f"expected matches=True, got {result['matches']}"


def test_297_different_nonempty_evidence_type_no_match() -> None:
    """Different non-empty evidence_type strings → matches=False."""
    req = {"evidence_type": "genetic_constraint"}
    assessment = {"evidence": {"evidence_type": "structural_druggability"}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is False, (
        f"expected matches=False, got {result['matches']}"
    )


def test_297_one_none_other_valid_string() -> None:
    """One side None, other side valid string → matches=False."""
    # req None, assessment valid
    req = {"evidence_type": None}
    assessment = {"evidence": {"evidence_type": "genetic_constraint"}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is False, (
        f"expected matches=False when req=None, got {result['matches']}"
    )

    # req valid, assessment None
    req = {"evidence_type": "genetic_constraint"}
    assessment = {"evidence": {"evidence_type": None}}
    result = match_assessment_to_requirement(req, assessment)
    assert result["matches"] is False, (
        f"expected matches=False when assessment=None, got {result['matches']}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Issue #297: Null/None evidence_type regression
        (
            "297_req_none_assessment_empty_evidence",
            test_297_req_none_assessment_empty_evidence,
        ),
        (
            "297_both_evidence_type_none",
            test_297_both_evidence_type_none,
        ),
        (
            "297_both_evidence_type_empty_string",
            test_297_both_evidence_type_empty_string,
        ),
        (
            "297_same_nonempty_evidence_type_matches",
            test_297_same_nonempty_evidence_type_matches,
        ),
        (
            "297_different_nonempty_evidence_type_no_match",
            test_297_different_nonempty_evidence_type_no_match,
        ),
        (
            "297_one_none_other_valid_string",
            test_297_one_none_other_valid_string,
        ),
    ]

    print(f"\nRunning {len(tests)} issue #297 governance-gate tests...\n")
    for name, fn in tests:
        _run(name, fn)

    print(f"\n{_PASS} passed, {_FAIL} failed out of {_PASS + _FAIL} tests")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
