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

"""Check that all hypothesis-assessment producers use the assessment_core
envelope and conform to the dde.hypothesis-assessment.v1 score shape.

Three producers emit dde.hypothesis-assessment.v1:

  - hypothesis.py  (strategy: adopted — score is always null)
  - coscientist.py (strategy: co-scientist — score is {value, basis})
  - hypex.py       (strategy: hypex — score is {value, basis} or null)

This checker verifies two things by AST inspection of the source:

  1. **Envelope:** Every producer assigns the assessment_core dict via
     ``assessment["assessment_core"] = {...}``.  A producer that builds
     assessment fields at the flat level (no ``assessment_core`` wrapper)
     is the drift this gate prevents.

  2. **Score shape:** Every score literal in the assessment_core block is
     either ``None`` or a dict with ``value`` and ``basis`` keys.  A bare
     number is the ONLY thing forbidden (constraint 4).

Additionally, the schema module's ``validate_score()`` is exercised with
positive and negative cases so the checker doubles as a test.

Usage:  PYTHONPATH=tools python3 tools/check_assessment_core.py

Exit codes:
  0  checked, nothing wrong
  1  checked, found a problem
  2  could not run — the checker never got as far as an opinion
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Reserved for "the checker could not run".
CANNOT_RUN = 2

try:
    from dde.core.assessment_schema import (
        SCHEMA_TAG,
        validate_assessment_core,
        validate_score,
    )
except Exception as exc:
    print(
        f"CANNOT RUN - {type(exc).__name__}: {exc}\n"
        "  This checker imports assessment_schema to exercise validation.\n"
        "  Remedy: run as `PYTHONPATH=tools python3 tools/check_assessment_core.py`\n"
        "  from the repository root with the tools venv active.",
        file=sys.stderr,
    )
    sys.exit(CANNOT_RUN)


ROOT = Path(__file__).resolve().parent.parent
COMMANDS = ROOT / "tools" / "dde" / "commands"

#: The three producers that must all use the assessment_core envelope.
PRODUCERS = ("hypothesis.py", "coscientist.py", "hypex.py")


def _find_assessment_core_assignments(tree: ast.Module) -> list[int]:
    """Find line numbers where ``assessment["assessment_core"] = ...`` appears."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "assessment"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "assessment_core"
            ):
                lines.append(node.lineno)
    return lines


def _find_flat_assessment_schema(tree: ast.Module) -> list[int]:
    """Find lines where assessment is built with schema key at the flat level.

    Detects the pattern::

        assessment = {
            "schema": "dde.hypothesis-assessment.v1",
            ...
        }

    This is the OLD pattern (no assessment_core wrapper) that we want to flag.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id != "assessment":
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and key.value == "schema":
                    # Check if the value is our schema tag
                    idx = node.value.keys.index(key)
                    val = node.value.values[idx]
                    if (
                        isinstance(val, ast.Constant)
                        and isinstance(val.value, str)
                        and "hypothesis-assessment" in val.value
                    ):
                        lines.append(node.lineno)
    return lines


def check_envelope() -> list[str]:
    """Verify every producer uses assessment["assessment_core"] = {...}."""
    errors: list[str] = []

    for filename in PRODUCERS:
        path = COMMANDS / filename
        if not path.is_file():
            errors.append(f"{filename}: file not found at {path}")
            continue

        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"{filename}: could not parse — {exc}")
            continue

        core_lines = _find_assessment_core_assignments(tree)
        flat_lines = _find_flat_assessment_schema(tree)

        if not core_lines:
            errors.append(
                f'{filename}: no assessment["assessment_core"] = {{...}} '
                f"assignment found. The assessment_core envelope is missing."
            )

        if flat_lines:
            errors.append(
                f"{filename}: flat assessment dict with schema key found at "
                f"line(s) {flat_lines}. The assessment fields should be "
                f'nested under assessment["assessment_core"], not at the '
                f"flat level."
            )

    return errors


def check_score_validation() -> list[str]:
    """Exercise validate_score() with positive and negative cases."""
    errors: list[str] = []

    # --- Positive cases: should pass (no errors) ---

    # score: null (adopted set)
    result = validate_score(None)
    if result:
        errors.append(f"validate_score(None) should pass, got errors: {result}")

    # score: {{value, basis}} (tournament result)
    result = validate_score({"value": 1612.4, "basis": "hypex-elo@1.0"})
    if result:
        errors.append(
            f'validate_score({{"value": 1612.4, "basis": "hypex-elo@1.0"}}) '
            f"should pass, got errors: {result}"
        )

    result = validate_score({"value": 1580, "basis": "coscientist-elo@1.1"})
    if result:
        errors.append(
            f'validate_score({{"value": 1580, "basis": "coscientist-elo@1.1"}}) '
            f"should pass, got errors: {result}"
        )

    # --- Negative cases: should fail ---

    # Bare number (FORBIDDEN)
    result = validate_score(1612.4)
    if not result:
        errors.append(
            "validate_score(1612.4) should FAIL (bare number forbidden), "
            "but got no errors"
        )

    result = validate_score(0)
    if not result:
        errors.append(
            "validate_score(0) should FAIL (bare number forbidden), but got no errors"
        )

    # Missing basis
    result = validate_score({"value": 1612.4})
    if not result:
        errors.append(
            'validate_score({"value": 1612.4}) should FAIL (missing basis), '
            "but got no errors"
        )

    # Missing value
    result = validate_score({"basis": "elo"})
    if not result:
        errors.append(
            'validate_score({"basis": "elo"}) should FAIL (missing value), '
            "but got no errors"
        )

    return errors


def check_full_core_validation() -> list[str]:
    """Exercise validate_assessment_core() with a well-formed and malformed core."""
    errors: list[str] = []

    # Well-formed core
    good_core = {
        "schema": SCHEMA_TAG,
        "strategy": "adopted",
        "source_artifact": "raw/hypotheses/test.adopted.json",
        "candidates": [
            {
                "candidate_id": "1",
                "statement": "Test hypothesis",
                "rank": None,
                "score": None,
                "origin": "adopted",
            },
        ],
    }
    result = validate_assessment_core(good_core)
    if result:
        errors.append(f"validate_assessment_core(good_core) should pass, got: {result}")

    # Core with bare-number score
    bad_core = {
        "schema": SCHEMA_TAG,
        "strategy": "hypex",
        "source_artifact": "raw/hypotheses/test.hypex.json",
        "candidates": [
            {
                "candidate_id": "H-0001",
                "statement": "Test",
                "rank": 1,
                "score": 1612.4,  # bare number — forbidden
                "origin": "generated",
            },
        ],
    }
    result = validate_assessment_core(bad_core)
    if not result:
        errors.append(
            "validate_assessment_core(bad_core with bare score) should FAIL, "
            "but got no errors"
        )

    # Core missing schema
    missing_schema = {
        "strategy": "adopted",
        "source_artifact": "test.json",
        "candidates": [],
    }
    result = validate_assessment_core(missing_schema)
    if not result:
        errors.append(
            "validate_assessment_core(missing schema) should FAIL, but got no errors"
        )

    return errors


def main() -> int:
    errors: list[str] = []

    # --- Check 1: envelope structure ---
    envelope_errors = check_envelope()
    errors.extend(envelope_errors)

    # --- Check 2: score validation ---
    score_errors = check_score_validation()
    errors.extend(score_errors)

    # --- Check 3: full core validation ---
    core_errors = check_full_core_validation()
    errors.extend(core_errors)

    # --- Coverage reporting ---
    n_producers = len(PRODUCERS)
    producer_files = [COMMANDS / f for f in PRODUCERS]
    n_found = sum(1 for p in producer_files if p.is_file())

    print(f"producers checked: {n_found} of {n_producers}")
    print(f"  files: {', '.join(PRODUCERS)}")
    print(f"check 1 (envelope): {len(envelope_errors)} error(s)")
    print(f"check 2 (score validation): {len(score_errors)} error(s)")
    print(f"check 3 (full core validation): {len(core_errors)} error(s)")

    if n_found == 0:
        print(
            "CANNOT RUN: no producer files found — the checker inspected "
            "nothing, which is not the same as finding nothing",
            file=sys.stderr,
        )
        return CANNOT_RUN

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(
        "\nAll producers use assessment_core envelope. Score validation "
        "passes positive and negative cases."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
