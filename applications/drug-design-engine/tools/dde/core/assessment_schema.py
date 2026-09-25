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

"""Schema definition for dde.hypothesis-assessment.v1.

Defines the shared assessment_core structure that all three hypothesis
producers (hypothesis.py, coscientist.py, hypex.py) must emit inside
``assessment["assessment_core"]``.

Score shape rule (constraint 4 from the brief):
  - score is ``None`` (null) — valid.  Adopted sets carry no score.
  - score is ``{"value": <number>, "basis": <string>}`` — valid.
  - score is a bare number (int or float) — FORBIDDEN.

This module is importable by the checker and by tests.
"""

from __future__ import annotations

from typing import Any

SCHEMA_TAG = "dde.hypothesis-assessment.v1"

#: Required top-level keys inside assessment_core.
REQUIRED_CORE_KEYS = ("schema", "strategy", "source_artifact", "candidates")

#: Required keys on each candidate.
REQUIRED_CANDIDATE_KEYS = ("candidate_id", "statement", "rank", "score", "origin")


def validate_score(score: Any) -> list[str]:
    """Validate a single candidate's score field.

    Returns a list of error strings (empty means valid).

    Valid shapes:
      - None (null)
      - {"value": <number>, "basis": <string>}

    Invalid shapes:
      - bare int or float
      - dict missing "value" or "basis"
      - any other type
    """
    errors: list[str] = []

    if score is None:
        return errors

    if isinstance(score, (int, float)):
        errors.append(
            f"score must not be a bare number, got {score!r}. "
            "Use null or {{value, basis}}."
        )
        return errors

    if not isinstance(score, dict):
        errors.append(
            f"score must be null or a dict with value+basis, got {type(score).__name__}"
        )
        return errors

    if "value" not in score:
        errors.append("score object missing required key 'value'")
    elif not isinstance(score["value"], (int, float)):
        errors.append(
            f"score.value must be a number, got {type(score['value']).__name__}"
        )

    if "basis" not in score:
        errors.append("score object missing required key 'basis'")
    elif not isinstance(score["basis"], str):
        errors.append(
            f"score.basis must be a string, got {type(score['basis']).__name__}"
        )

    return errors


def validate_assessment_core(core: Any) -> list[str]:
    """Validate an assessment_core dict against the schema.

    Returns a list of error strings (empty means valid).
    """
    errors: list[str] = []

    if not isinstance(core, dict):
        errors.append(f"assessment_core must be a dict, got {type(core).__name__}")
        return errors

    # Check required keys
    for key in REQUIRED_CORE_KEYS:
        if key not in core:
            errors.append(f"assessment_core missing required key '{key}'")

    # Validate schema tag
    if core.get("schema") != SCHEMA_TAG:
        errors.append(
            f"assessment_core.schema must be {SCHEMA_TAG!r}, got {core.get('schema')!r}"
        )

    # Validate strategy is a string
    strategy = core.get("strategy")
    if strategy is not None and not isinstance(strategy, str):
        errors.append(
            f"assessment_core.strategy must be a string, got {type(strategy).__name__}"
        )

    # Validate source_artifact is a string
    source = core.get("source_artifact")
    if source is not None and not isinstance(source, str):
        errors.append(
            f"assessment_core.source_artifact must be a string, "
            f"got {type(source).__name__}"
        )

    # Validate candidates
    candidates = core.get("candidates")
    if candidates is not None:
        if not isinstance(candidates, list):
            errors.append(
                f"assessment_core.candidates must be a list, "
                f"got {type(candidates).__name__}"
            )
        else:
            for i, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    errors.append(
                        f"candidate[{i}] must be a dict, got {type(candidate).__name__}"
                    )
                    continue
                for key in REQUIRED_CANDIDATE_KEYS:
                    if key not in candidate:
                        errors.append(f"candidate[{i}] missing required key '{key}'")
                # Validate score shape
                if "score" in candidate:
                    score_errors = validate_score(candidate["score"])
                    for err in score_errors:
                        errors.append(f"candidate[{i}]: {err}")

    return errors
