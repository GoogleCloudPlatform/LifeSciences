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

"""Governance-gate regression tests for workorder override and transition.

Covers:
  - Issue #218: Partial validation override bypasses non-overridable checks
  - Issue #219: Direct transition from proposed to committed bypasses validation

Run with:
    PYTHONPATH=tools python3 tests/test_workorder_govgate.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.core.controlstore import (
    ensure_control_dirs,
    write_record,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def _make_project() -> Path:
    """Create a temporary project root with control directories."""
    base = Path(tempfile.mkdtemp())
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    ensure_control_dirs(project)
    return project


def _make_wo_record(
    wo_id: str = "WO-001",
    revision: int = 1,
    state: str = "proposed",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal valid work-order record."""
    record: dict[str, Any] = {
        "id": wo_id,
        "revision": revision,
        "state": state,
        "decision_question": "test question",
        "requested_role": "analyst",
        "stage": "preclinical",
        "cycle": 1,
        "context": {"target": "CDK4"},
        "dependencies": [],
        "capabilities": ["analysis"],
        "deliverables": {"report": "report.md"},
        "acceptance_criteria": "passes review",
        "alert_policy": {"on_failure": "notify"},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "manager",
        "created_at": _NOW,
    }
    record.update(overrides)
    return record


def _write_wo(project: Path, record: dict[str, Any]) -> None:
    """Write a work-order record to the control store."""
    identifier = f"{record['id']}-r{record['revision']}"
    write_record(project, "work-order", identifier, record)


def _write_validation_record(
    project: Path,
    wo_id: str,
    revision: int,
    checks: list[dict[str, Any]],
) -> None:
    """Write a validation record with the given checks."""
    val_record = {
        "work_order_id": wo_id,
        "work_order_revision": revision,
        "run_id": "RUN-001",
        "validated_at": _NOW,
        "result": "fail",
        "checks": checks,
    }
    identifier = f"{wo_id}-r{revision}"
    write_record(project, "validation", identifier, val_record)


def _make_check(name: str, result: str = "fail", detail: Any = None) -> dict[str, Any]:
    """Build a check entry for a validation record."""
    check: dict[str, Any] = {"name": name, "result": result}
    if detail is not None:
        check["detail"] = detail
    return check


class _InvokeResult:
    """Thin wrapper around CliRunner result for test assertions."""

    def __init__(self, exit_code: int, output: str):
        self.exit_code = exit_code
        self.output = output


def _invoke_override(
    project: Path,
    wo_id: str,
    check_names: str,
    reason: str = "test reason",
    evidence: str = "test-evidence.md",
    actor: str = "test-operator",
) -> _InvokeResult:
    """Invoke override_cmd programmatically via click's testing utility."""
    from click.testing import CliRunner
    from dde.commands.workorder import workorder
    from dde.common import AppState

    runner = CliRunner(env={"DDE_PROJECT": str(project)})
    result = runner.invoke(
        workorder,
        [
            "override",
            wo_id,
            "--reason",
            reason,
            "--evidence",
            evidence,
            "--checks",
            check_names,
            "--actor",
            actor,
        ],
        obj=AppState(project_override=str(project)),
    )
    return _InvokeResult(result.exit_code, result.output)


def _invoke_transition(
    project: Path,
    wo_id: str,
    target_state: str,
) -> _InvokeResult:
    """Invoke transition_cmd programmatically via click's testing utility."""
    from click.testing import CliRunner
    from dde.commands.workorder import workorder
    from dde.common import AppState

    runner = CliRunner(env={"DDE_PROJECT": str(project)})
    result = runner.invoke(
        workorder,
        ["transition", wo_id, target_state],
        obj=AppState(project_override=str(project)),
    )
    return _InvokeResult(result.exit_code, result.output)


# ---------------------------------------------------------------------------
# Tests for issue #218: Override guards
# ---------------------------------------------------------------------------


def test_override_blocked_when_non_overridable_check_failed() -> None:
    """Override must be blocked when a non-overridable check also failed,
    even if only overridable checks are named in --checks."""
    project = _make_project()

    # Create WO in validation_failed with both non-overridable and overridable failures
    record = _make_wo_record(state="validation_failed")
    _write_wo(project, record)

    checks = [
        _make_check("deliverables_exist", "fail"),  # non-overridable
        _make_check("report_headings", "fail"),  # overridable
    ]
    _write_validation_record(project, "WO-001", 1, checks)

    # Try to override with only the overridable check
    result = _invoke_override(project, "WO-001", "report_headings")
    assert result.exit_code != 0, "expected non-zero exit for non-overridable check"
    assert "non-overridable" in result.output.lower(), (
        f"expected 'non-overridable' in output: {result.output}"
    )
    assert "deliverables_exist" in result.output, (
        f"should mention the check: {result.output}"
    )


def test_override_blocked_when_checks_not_fully_covered() -> None:
    """Override must cover ALL failed checks; partial coverage is rejected."""
    project = _make_project()

    record = _make_wo_record(state="validation_failed")
    _write_wo(project, record)

    checks = [
        _make_check("report_headings", "fail"),
        _make_check("paths_resolve", "fail"),
    ]
    _write_validation_record(project, "WO-001", 1, checks)

    # Try to override naming only one of the two failed checks
    result = _invoke_override(project, "WO-001", "report_headings")
    assert result.exit_code != 0, "expected non-zero exit for uncovered checks"
    assert "uncovered" in result.output.lower(), (
        f"expected 'uncovered' in output: {result.output}"
    )
    assert "paths_resolve" in result.output, (
        f"should mention uncovered check: {result.output}"
    )


def test_override_succeeds_when_all_overridable_checks_covered() -> None:
    """Override succeeds when all failed checks are overridable and covered."""
    project = _make_project()

    record = _make_wo_record(state="validation_failed")
    _write_wo(project, record)

    checks = [
        _make_check("report_headings", "fail"),
        _make_check("paths_resolve", "fail"),
    ]
    _write_validation_record(project, "WO-001", 1, checks)

    # Override naming both failed checks — should succeed
    result = _invoke_override(project, "WO-001", "report_headings,paths_resolve")
    assert result.exit_code == 0, (
        f"expected success (exit 0), got {result.exit_code}: {result.output}"
    )

    # Verify the WO transitioned to mechanically_validated
    from dde.core.controlstore import read_record

    updated = read_record(project, "work-order", "WO-001-r1")
    assert updated["state"] == "mechanically_validated", (
        f"expected mechanically_validated, got {updated['state']}"
    )


def test_override_blocked_when_path_confinement_in_any_failed_check() -> None:
    """Path-confinement violations in ANY failed check (not just named ones)
    must block the override."""
    project = _make_project()

    record = _make_wo_record(state="validation_failed")
    _write_wo(project, record)

    # Two failed checks: report_headings and paths_resolve.
    # Both are overridable, both named, but paths_resolve has PCF.
    checks = [
        _make_check("report_headings", "fail"),
        _make_check(
            "paths_resolve",
            "fail",
            detail={"path_confinement_failures": ["/etc/passwd"]},
        ),
    ]
    _write_validation_record(project, "WO-001", 1, checks)

    result = _invoke_override(project, "WO-001", "report_headings,paths_resolve")
    assert result.exit_code != 0, "expected non-zero exit for path-confinement"
    assert "path-confinement" in result.output.lower(), (
        f"expected 'path-confinement' in output: {result.output}"
    )


# ---------------------------------------------------------------------------
# Tests for issue #219: Transition guard proposed → committed
# ---------------------------------------------------------------------------


def test_transition_proposed_to_committed_blocked() -> None:
    """Direct transition from proposed to committed must be blocked."""
    project = _make_project()

    record = _make_wo_record(state="proposed")
    _write_wo(project, record)

    result = _invoke_transition(project, "WO-001", "committed")
    assert result.exit_code != 0, "expected non-zero exit for proposed → committed"
    assert (
        "proposed" in result.output.lower() and "committed" in result.output.lower()
    ), f"expected 'proposed' and 'committed' in output: {result.output}"
    assert "dde workorder commit" in result.output, (
        f"should suggest commit cmd: {result.output}"
    )


def test_transition_committed_to_queued_still_works() -> None:
    """committed → queued must still work (regression check)."""
    project = _make_project()

    record = _make_wo_record(state="committed")
    _write_wo(project, record)

    result = _invoke_transition(project, "WO-001", "queued")
    assert result.exit_code == 0, (
        f"expected success (exit 0), got {result.exit_code}: {result.output}"
    )

    from dde.core.controlstore import read_record

    updated = read_record(project, "work-order", "WO-001-r1")
    assert updated["state"] == "queued", f"expected queued, got {updated['state']}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Issue #218: Override guards
        (
            "override_blocked_when_non_overridable_check_failed",
            test_override_blocked_when_non_overridable_check_failed,
        ),
        (
            "override_blocked_when_checks_not_fully_covered",
            test_override_blocked_when_checks_not_fully_covered,
        ),
        (
            "override_succeeds_when_all_overridable_checks_covered",
            test_override_succeeds_when_all_overridable_checks_covered,
        ),
        (
            "override_blocked_when_path_confinement_in_any_failed_check",
            test_override_blocked_when_path_confinement_in_any_failed_check,
        ),
        # Issue #219: Transition guard
        (
            "transition_proposed_to_committed_blocked",
            test_transition_proposed_to_committed_blocked,
        ),
        (
            "transition_committed_to_queued_still_works",
            test_transition_committed_to_queued_still_works,
        ),
    ]

    print(f"\nRunning {len(tests)} governance-gate tests...\n")
    for name, fn in tests:
        _run(name, fn)

    print(f"\n{_PASS} passed, {_FAIL} failed out of {_PASS + _FAIL} tests")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
