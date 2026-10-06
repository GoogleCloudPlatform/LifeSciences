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

"""Regression tests for stale concept loader bypass (issue #222).

The ``_default_concept_loader`` must return the latest versioned
revision record (IC-NNN-rN.json) when one exists, falling back to the
unversioned file (IC-NNN.json) only when no versioned records are
present.  Before this fix, the loader checked for the unversioned file
first and returned it immediately, which allowed a stale record with
``termination_authority='program_lead'`` to bypass the human-approval
gate even when a newer revision set it to ``'human'``.

Run with:
    PYTHONPATH=tools python3 tests/test_controlstore_govgate.py

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
    _default_concept_loader,
    ensure_control_dirs,
    write_record,
)
from dde.core.errors import Refusal

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


def _make_concept_record(
    concept_id: str = "IC-001",
    revision: int = 1,
    termination_authority: str = "program_lead",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal valid concept record."""
    record: dict[str, Any] = {
        "schema": "dde.intervention-concept.v1",
        "id": concept_id,
        "revision": revision,
        "state": "active",
        "disease_context": {"indication": "test"},
        "target_pathway": {"gene": "TEST"},
        "modality": "small_molecule",
        "created_at": _NOW,
        "charter_ref": "DEC-001",
        "termination_authority": termination_authority,
    }
    record.update(overrides)
    return record


def _write_concept_file(
    project: Path,
    concept_id: str,
    termination_authority: str,
    *,
    revision: int | None = None,
) -> Path:
    """Write a concept record directly to .dde/control/concepts/."""
    concepts_dir = project / CONTROL_DIR / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    if revision is not None:
        filename = f"{concept_id}-r{revision}.json"
    else:
        filename = f"{concept_id}.json"

    data = _make_concept_record(
        concept_id=concept_id,
        revision=revision or 1,
        termination_authority=termination_authority,
    )
    path = concepts_dir / filename
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


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
print("test_controlstore_govgate.py — issue #222 stale concept loader")
print("=" * 60)

# ---------------------------------------------------------------------------
# 1. Loader prefers latest revision over unversioned
# ---------------------------------------------------------------------------
print("\n--- Concept loader revision ordering ---")


def test_concept_loader_prefers_latest_revision_over_unversioned():
    """When both IC-001.json (program_lead) and IC-001-r2.json (human)
    exist, the loader must return the r2 record."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_file(project, "IC-001", "program_lead")
        _write_concept_file(project, "IC-001", "human", revision=2)

        loader = _default_concept_loader(project)
        result = loader("IC-001")
        assert result is not None, "loader returned None"
        assert result["termination_authority"] == "human", (
            f"expected 'human', got {result['termination_authority']!r}"
        )


_check(
    "loader prefers latest revision over unversioned file",
    test_concept_loader_prefers_latest_revision_over_unversioned,
)


# ---------------------------------------------------------------------------
# 2. Loader falls back to unversioned when no revisions exist
# ---------------------------------------------------------------------------


def test_concept_loader_falls_back_to_unversioned_when_no_revisions():
    """When only IC-001.json exists (no versioned records), the loader
    must return that record."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_file(project, "IC-001", "program_lead")

        loader = _default_concept_loader(project)
        result = loader("IC-001")
        assert result is not None, "loader returned None"
        assert result["termination_authority"] == "program_lead", (
            f"expected 'program_lead', got {result['termination_authority']!r}"
        )


_check(
    "loader falls back to unversioned when no revisions exist",
    test_concept_loader_falls_back_to_unversioned_when_no_revisions,
)


# ---------------------------------------------------------------------------
# 3. Loader returns highest revision
# ---------------------------------------------------------------------------


def test_concept_loader_returns_highest_revision():
    """When multiple versioned records exist, the loader must return the
    one with the highest revision number."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _write_concept_file(project, "IC-001", "program_lead", revision=1)
        _write_concept_file(project, "IC-001", "program_lead", revision=2)
        _write_concept_file(project, "IC-001", "human", revision=3)

        loader = _default_concept_loader(project)
        result = loader("IC-001")
        assert result is not None, "loader returned None"
        assert result["termination_authority"] == "human", (
            f"expected 'human' (r3), got {result['termination_authority']!r}"
        )
        assert result["revision"] == 3, f"expected revision 3, got {result['revision']}"


_check(
    "loader returns highest revision among multiple versioned records",
    test_concept_loader_returns_highest_revision,
)


# ---------------------------------------------------------------------------
# 4. Stale unversioned does not bypass human approval (integration)
# ---------------------------------------------------------------------------
print("\n--- Integration: stale record does not bypass gate ---")


def test_stale_unversioned_does_not_bypass_human_approval():
    """Full integration test: a stale unversioned record with
    termination_authority='program_lead' must NOT prevent the
    human-approval gate from firing when a newer versioned record
    sets termination_authority='human'."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))

        # Stale unversioned record: program_lead (would bypass gate)
        _write_concept_file(project, "IC-001", "program_lead")
        # Newer versioned record: human (requires approval)
        _write_concept_file(project, "IC-001", "human", revision=2)

        decision = _valid_decision(
            action="terminate",
            affected_entity={"entity_type": "concept", "entity_ref": "IC-001"},
            human_approval=None,
        )

        try:
            write_record(project, "decision", "DR-001", decision)
            raise AssertionError(
                "expected Refusal — stale unversioned record bypassed "
                "the human-approval gate"
            )
        except Refusal as exc:
            assert exc.exit_code == 9, f"expected exit code 9, got {exc.exit_code}"
            assert "human approval" in exc.message.lower(), exc.message


_check(
    "stale unversioned record does not bypass human-approval gate",
    test_stale_unversioned_does_not_bypass_human_approval,
)


# ---------------------------------------------------------------------------
# 5. Loader returns None for missing concept
# ---------------------------------------------------------------------------
print("\n--- Edge cases ---")


def test_concept_loader_returns_none_for_missing_concept():
    """Loader called with a non-existent concept ID returns None."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))

        loader = _default_concept_loader(project)
        result = loader("IC-999")
        assert result is None, f"expected None, got {result!r}"


_check(
    "loader returns None for missing concept",
    test_concept_loader_returns_none_for_missing_concept,
)


# ---------------------------------------------------------------------------
# 6. Loader validates concept ID format
# ---------------------------------------------------------------------------


def test_concept_loader_validates_concept_id_format():
    """Loader called with a malformed ID like '../../evil' returns None."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))

        loader = _default_concept_loader(project)

        assert loader("../../evil") is None, "path traversal should be rejected"
        assert loader("IC-01") is None, "too-short ID should be rejected"
        assert loader("XC-001") is None, "wrong prefix should be rejected"
        assert loader("") is None, "empty string should be rejected"
        assert loader("IC-001; rm -rf /") is None, (
            "injection attempt should be rejected"
        )


_check(
    "loader validates concept ID format",
    test_concept_loader_validates_concept_id_format,
)


# ---------------------------------------------------------------------------
# 7. Corrupted versioned file does not fall back to unversioned
# ---------------------------------------------------------------------------


def test_corrupted_versioned_file_does_not_fall_back_to_unversioned():
    """When a versioned file exists but is corrupted, the loader must
    return None (fail-closed), not fall back to a stale unversioned file."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        # Write a valid unversioned file with program_lead
        _write_concept_file(project, "IC-001", "program_lead")
        # Write a corrupted versioned file
        concepts_dir = project / CONTROL_DIR / "concepts"
        corrupted = concepts_dir / "IC-001-r2.json"
        corrupted.write_text("NOT VALID JSON {{{", encoding="utf-8")

        loader = _default_concept_loader(project)
        result = loader("IC-001")
        # Must return None (fail-closed), not the stale unversioned record
        assert result is None, (
            f"expected None for corrupted versioned file, got {result}"
        )


_check(
    "corrupted versioned file does not fall back to unversioned",
    test_corrupted_versioned_file_does_not_fall_back_to_unversioned,
)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
