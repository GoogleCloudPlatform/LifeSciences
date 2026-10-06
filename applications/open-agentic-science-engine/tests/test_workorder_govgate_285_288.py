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

"""Governance-gate regression tests for issues #285 and #288.

Covers:
  - Issue #285: Non-dict truthy value bypasses dict-specific validation
                in _perform_commit() liability_justification handling.
  - Issue #288: Key naming mismatch between error storage and override
                checks — path-escape issues stored under detail["issues"]
                were not inspected by override_cmd.

Run with:
    PYTHONPATH=tools python3 tests/test_workorder_govgate_285_288.py

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


def _create_liability_tracker(project: Path, liabilities: list[str]) -> None:
    """Create a liability-tracker.md with Critical active liabilities."""
    tracker_dir = project / "program-state"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    content = "# Liability Tracker\n\n"
    for lid in liabilities:
        content += f"## {lid}\n\n"
        content += "**Severity**: Critical\n"
        content += "**Status**: open\n\n"
    (tracker_dir / "liability-tracker.md").write_text(content, encoding="utf-8")


class _InvokeResult:
    """Thin wrapper around CliRunner result for test assertions."""

    def __init__(self, exit_code: int, output: str):
        self.exit_code = exit_code
        self.output = output


def _invoke_commit(
    project: Path,
    wo_id: str,
) -> _InvokeResult:
    """Invoke commit_cmd programmatically via click's testing utility."""
    from click.testing import CliRunner
    from dde.commands.workorder import workorder
    from dde.common import AppState

    runner = CliRunner(env={"DDE_PROJECT": str(project)})
    result = runner.invoke(
        workorder,
        ["commit", wo_id],
        obj=AppState(project_override=str(project)),
    )
    return _InvokeResult(result.exit_code, result.output)


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


# ---------------------------------------------------------------------------
# Tests for issue #285: Non-dict liability_justification bypass
# ---------------------------------------------------------------------------


def test_285_string_justification_rejected() -> None:
    """liability_justification='bypass' (a truthy non-dict) must be rejected."""
    project = _make_project()
    _create_liability_tracker(project, ["L-1"])

    record = _make_wo_record(
        state="proposed",
        liability_justification="bypass",
    )
    _write_wo(project, record)

    result = _invoke_commit(project, "WO-001")
    assert result.exit_code != 0, (
        f"expected non-zero exit for string justification, got {result.exit_code}"
    )
    assert "dict" in result.output.lower() or "schema" in result.output.lower(), (
        f"expected type error in output: {result.output}"
    )


def test_285_bool_justification_rejected() -> None:
    """liability_justification=True (a truthy non-dict) must be rejected."""
    project = _make_project()
    _create_liability_tracker(project, ["L-1"])

    record = _make_wo_record(
        state="proposed",
        liability_justification=True,
    )
    _write_wo(project, record)

    result = _invoke_commit(project, "WO-001")
    assert result.exit_code != 0, (
        f"expected non-zero exit for bool justification, got {result.exit_code}"
    )
    assert "dict" in result.output.lower() or "schema" in result.output.lower(), (
        f"expected type error in output: {result.output}"
    )


def test_285_int_justification_rejected() -> None:
    """liability_justification=1 (a truthy non-dict) must be rejected."""
    project = _make_project()
    _create_liability_tracker(project, ["L-1"])

    record = _make_wo_record(
        state="proposed",
        liability_justification=1,
    )
    _write_wo(project, record)

    result = _invoke_commit(project, "WO-001")
    assert result.exit_code != 0, (
        f"expected non-zero exit for int justification, got {result.exit_code}"
    )
    assert "dict" in result.output.lower() or "schema" in result.output.lower(), (
        f"expected type error in output: {result.output}"
    )


def test_285_valid_dict_justification_accepted() -> None:
    """liability_justification={'L-1': 'Valid reason'} must pass the check."""
    project = _make_project()
    _create_liability_tracker(project, ["L-1"])

    record = _make_wo_record(
        state="proposed",
        liability_justification={"L-1": "Valid reason for proceeding"},
    )
    _write_wo(project, record)

    result = _invoke_commit(project, "WO-001")
    assert result.exit_code == 0, (
        f"expected success for valid dict justification, "
        f"got exit {result.exit_code}: {result.output}"
    )


# ---------------------------------------------------------------------------
# Tests for issue #288: Path-escape issues in detail["issues"]
# ---------------------------------------------------------------------------


def test_288_override_blocked_when_issues_contain_path_escape_dict() -> None:
    """Override must be blocked when detail['issues'] contains a dict-format
    path-escape violation (the format _check_analysis_citations produces)."""
    project = _make_project()

    record = _make_wo_record(state="validation_failed")
    _write_wo(project, record)

    checks = [
        _make_check(
            "report_headings",
            "fail",
            detail={
                "issues": [
                    {
                        "file": "raw/docking/result.json",
                        "issue": "source path escapes project root: ../../../etc/passwd",
                    },
                ]
            },
        ),
    ]
    _write_validation_record(project, "WO-001", 1, checks)

    result = _invoke_override(project, "WO-001", "report_headings")
    assert result.exit_code != 0, (
        f"expected non-zero exit for dict path-escape in issues, "
        f"got {result.exit_code}: {result.output}"
    )
    assert "path-confinement" in result.output.lower(), (
        f"expected 'path-confinement' in output: {result.output}"
    )


def test_288_override_blocked_when_issues_contain_path_escape_str() -> None:
    """Override must be blocked when detail['issues'] contains a plain-string
    path-escape violation (defense-in-depth for alternate issue formats)."""
    project = _make_project()

    record = _make_wo_record(state="validation_failed")
    _write_wo(project, record)

    checks = [
        _make_check(
            "report_headings",
            "fail",
            detail={
                "issues": [
                    "citation '/etc/passwd' escapes project root",
                ]
            },
        ),
    ]
    _write_validation_record(project, "WO-001", 1, checks)

    result = _invoke_override(project, "WO-001", "report_headings")
    assert result.exit_code != 0, (
        f"expected non-zero exit for string path-escape in issues, "
        f"got {result.exit_code}: {result.output}"
    )
    assert "path-confinement" in result.output.lower(), (
        f"expected 'path-confinement' in output: {result.output}"
    )


def test_288_override_allowed_when_issues_have_no_path_escape() -> None:
    """Override should succeed when detail['issues'] exists but contains
    no path-escape violations (using dict format matching validate.py output)."""
    project = _make_project()

    record = _make_wo_record(state="validation_failed")
    _write_wo(project, record)

    checks = [
        _make_check(
            "report_headings",
            "fail",
            detail={
                "issues": [
                    {"file": "report.html", "issue": "heading level mismatch"},
                    {
                        "file": "report.html",
                        "issue": "missing required section: Methods",
                    },
                ]
            },
        ),
    ]
    _write_validation_record(project, "WO-001", 1, checks)

    result = _invoke_override(project, "WO-001", "report_headings")
    assert result.exit_code == 0, (
        f"expected success for non-path-escape issues, "
        f"got exit {result.exit_code}: {result.output}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        # Issue #285: Non-dict liability_justification bypass
        (
            "#285: string justification rejected",
            test_285_string_justification_rejected,
        ),
        (
            "#285: bool justification rejected",
            test_285_bool_justification_rejected,
        ),
        (
            "#285: int justification rejected",
            test_285_int_justification_rejected,
        ),
        (
            "#285: valid dict justification accepted",
            test_285_valid_dict_justification_accepted,
        ),
        # Issue #288: Path-escape in detail["issues"]
        (
            "#288: override blocked when issues contain path escape (dict)",
            test_288_override_blocked_when_issues_contain_path_escape_dict,
        ),
        (
            "#288: override blocked when issues contain path escape (str)",
            test_288_override_blocked_when_issues_contain_path_escape_str,
        ),
        (
            "#288: override allowed when issues have no path escape",
            test_288_override_allowed_when_issues_have_no_path_escape,
        ),
    ]

    print(f"\nRunning {len(tests)} governance-gate tests (issues #285, #288)...\n")
    for name, fn in tests:
        _run(name, fn)

    print(f"\n{_PASS} passed, {_FAIL} failed out of {_PASS + _FAIL} tests")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
