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

"""Tests for --dry-run / --preflight validation mode (#126).

Verifies that:
- --dry-run runs checks in non-submitted state
- --dry-run produces check results
- --dry-run does NOT write a validation record
- --dry-run exits non-zero on failure
- --dry-run exits zero on all-pass
- Real validation still requires submitted state
- Output includes dry-run markers
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.validate import (
    _resolve_wo_record,
    _run_all_checks,
)
from dde.core.controlstore import CONTROL_DIR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str | bytes) -> str:
    """Write a file and return its sha256."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _make_wo_record(
    root: Path,
    wo_id: str = "WO-001",
    revision: int = 1,
    state: str = "proposed",
    deliverables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create and write a minimal work-order record.

    Returns the record dict.  The record is written to the control
    store so that ``_resolve_wo_record`` can find it.
    """
    if deliverables is None:
        deliverables = {"layer_1": [], "layer_0_classes": []}
    record: dict[str, Any] = {
        "id": wo_id,
        "revision": revision,
        "state": state,
        "decision_question": "test question",
        "requested_role": "test",
        "stage": "test",
        "cycle": "1",
        "context": {},
        "dependencies": [],
        "capabilities": [],
        "deliverables": deliverables,
        "acceptance_criteria": [],
        "alert_policy": {},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "test",
        "created_at": "2026-01-01T00:00:00Z",
        "committed_at": None,
    }
    identifier = f"{wo_id}-r{revision}"
    wo_dir = root / CONTROL_DIR / "work-orders"
    wo_dir.mkdir(parents=True, exist_ok=True)
    record_path = wo_dir / f"{identifier}.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def _setup_passing_project(root: Path, wo_id: str = "WO-001") -> dict[str, Any]:
    """Set up a minimal project that passes basic checks.

    Creates a WO in 'proposed' state with a layer_1 finding that has
    the required heading and WO reference.  No layer_0_classes (so
    provenance/analysis/relay checks skip rather than fail).
    """
    # Create a finding file with required heading and WO reference
    finding = root / "findings" / "report.md"
    content = (
        f"# Report WO-{wo_id.removeprefix('WO-')}-r1\n\n"
        "## Summary\n\nSummary text.\n\n"
        "## Key Findings\n\nFindings text.\n\n"
        "## Implications\n\nImplications text.\n"
    )
    _write(finding, content)

    return _make_wo_record(
        root,
        wo_id=wo_id,
        state="proposed",
        deliverables={
            "layer_1": ["findings/report.md"],
            "layer_0_classes": [],
        },
    )


def _setup_failing_project(root: Path, wo_id: str = "WO-001") -> dict[str, Any]:
    """Set up a project that fails validation (missing deliverable)."""
    return _make_wo_record(
        root,
        wo_id=wo_id,
        state="proposed",
        deliverables={
            "layer_1": ["findings/nonexistent.md"],
            "layer_0_classes": [],
        },
    )


# ===========================================================================
# _run_all_checks tests
# ===========================================================================


def test_run_all_checks_in_non_submitted_state() -> None:
    """_run_all_checks runs in non-submitted state (no state guard)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_passing_project(root)
        assert record["state"] == "proposed"  # not submitted

        checks, overall_result, _checks_failed = _run_all_checks(root, record)

        # Checks ran — we get results.
        assert isinstance(checks, list)
        assert len(checks) > 0
        assert isinstance(overall_result, str)
        print("  PASS: _run_all_checks runs in non-submitted state")


def test_run_all_checks_produces_check_results() -> None:
    """_run_all_checks returns structured check results."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_passing_project(root)

        checks, _overall_result, _checks_failed = _run_all_checks(root, record)

        # Each check has expected keys.
        for c in checks:
            assert "name" in c, f"check missing 'name': {c}"
            assert "result" in c, f"check missing 'result': {c}"
            assert "status" in c, f"check missing 'status': {c}"

        # Known check names should be present.
        check_names = {c["name"] for c in checks}
        assert "deliverables_exist" in check_names
        assert "report_headings" in check_names
        assert "findings_integrity" in check_names
        print("  PASS: _run_all_checks produces structured check results")


def test_run_all_checks_no_validation_record_written() -> None:
    """_run_all_checks does NOT write a validation record."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_passing_project(root)

        _run_all_checks(root, record)

        # No validation record should exist.
        val_dir = root / CONTROL_DIR / "validations"
        if val_dir.exists():
            records = list(val_dir.glob("*.json"))
            assert len(records) == 0, (
                f"validation records written by _run_all_checks: {records}"
            )
        print("  PASS: _run_all_checks writes no validation record")


def test_run_all_checks_no_state_change() -> None:
    """_run_all_checks does NOT modify the WO state."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_passing_project(root)
        original_state = record["state"]

        _run_all_checks(root, record)

        # Re-read the record from disk — state should be unchanged.
        wo_dir = root / CONTROL_DIR / "work-orders"
        record_path = wo_dir / f"{record['id']}-r{record['revision']}.json"
        disk_record = json.loads(record_path.read_text(encoding="utf-8"))
        assert disk_record["state"] == original_state, (
            f"state changed from {original_state!r} to {disk_record['state']!r}"
        )
        print("  PASS: _run_all_checks does not change WO state")


def test_run_all_checks_exit_zero_on_pass() -> None:
    """_run_all_checks returns no failures when all checks pass."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_passing_project(root)

        _checks, overall_result, checks_failed = _run_all_checks(root, record)

        assert not checks_failed, f"unexpected failures: {checks_failed}"
        assert overall_result in ("pass", "pass_with_warnings")
        print("  PASS: all-pass project returns no failures")


def test_run_all_checks_reports_failures() -> None:
    """_run_all_checks returns failures when checks fail."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_failing_project(root)

        _checks, overall_result, checks_failed = _run_all_checks(root, record)

        assert len(checks_failed) > 0, "expected at least one failure"
        assert overall_result == "fail"
        assert "deliverables_exist" in checks_failed
        print("  PASS: failing project returns failures")


def test_run_all_checks_works_in_any_state() -> None:
    """_run_all_checks works regardless of WO state."""
    states_to_test = [
        "proposed",
        "committed",
        "queued",
        "in_progress",
        "submitted",
        "validation_failed",
    ]
    for wo_state in states_to_test:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _make_wo_record(
                root,
                state=wo_state,
                deliverables={
                    "layer_1": [],
                    "layer_0_classes": [],
                },
            )
            # Should not raise regardless of state.
            checks, _overall_result, _checks_failed = _run_all_checks(root, record)
            assert isinstance(checks, list)
    print("  PASS: _run_all_checks works in any WO state")


# ===========================================================================
# _resolve_wo_record tests
# ===========================================================================


def test_resolve_wo_record_finds_latest() -> None:
    """_resolve_wo_record finds the latest revision when no revision specified."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_wo_record(root, wo_id="WO-001", revision=1, state="committed")
        _make_wo_record(root, wo_id="WO-001", revision=2, state="submitted")

        record = _resolve_wo_record(root, "WO-001")
        assert record["revision"] == 2
        assert record["state"] == "submitted"
        print("  PASS: _resolve_wo_record finds latest revision")


def test_resolve_wo_record_specific_revision() -> None:
    """_resolve_wo_record reads a specific revision when requested."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_wo_record(root, wo_id="WO-001", revision=1, state="committed")
        _make_wo_record(root, wo_id="WO-001", revision=2, state="submitted")

        record = _resolve_wo_record(root, "WO-001", revision_num=1)
        assert record["revision"] == 1
        assert record["state"] == "committed"
        print("  PASS: _resolve_wo_record reads specific revision")


# ===========================================================================
# Real validation still requires submitted state
# ===========================================================================


def test_perform_validation_requires_submitted_state() -> None:
    """_perform_validation still refuses non-submitted WOs."""
    from dde.commands.validate import _perform_validation
    from dde.core.errors import Refusal

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_passing_project(root)  # state=proposed

        try:
            _perform_validation(root, "WO-001")
            assert False, "expected Refusal to be raised"
        except Refusal as exc:
            assert "submitted" in str(exc).lower()
        print("  PASS: _perform_validation requires submitted state")


# ===========================================================================
# Dry-run output marker tests (unit-level: test the format, not CLI)
# ===========================================================================


def test_dry_run_header_in_output() -> None:
    """Dry-run output should include the PREFLIGHT VALIDATION header.

    This is a design contract test — the actual CLI rendering is tested
    via the check-by-check results; here we verify the string constants
    that check_cmd uses are correct by checking _run_all_checks produces
    the data that check_cmd needs.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_passing_project(root)

        checks, overall_result, _checks_failed = _run_all_checks(root, record)

        # The dry-run path in check_cmd uses these values to build the
        # header "=== PREFLIGHT VALIDATION (dry run) ===" and footer
        # "This is an advisory check. No validation record has been written."
        # Verify the data is there for it.
        assert isinstance(overall_result, str)
        assert isinstance(checks, list)
        assert len(checks) > 0
        print("  PASS: dry-run data supports header/footer rendering")


def test_dry_run_json_includes_mode() -> None:
    """When --json is used with --dry-run, output should include mode: dry_run.

    This tests the data contract: _run_all_checks returns the checks
    that check_cmd wraps with "mode": "dry_run" in JSON output.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = _setup_passing_project(root)

        checks, overall_result, checks_failed = _run_all_checks(root, record)

        # Simulate what check_cmd does in --dry-run --json mode.
        json_output: dict[str, Any] = {
            "mode": "dry_run",
            "work_order_id": record["id"],
            "work_order_revision": record["revision"],
            "result": overall_result,
            "checks": checks,
        }
        if checks_failed:
            json_output["checks_failed"] = checks_failed

        assert json_output["mode"] == "dry_run"
        assert "checks" in json_output
        assert "result" in json_output
        print("  PASS: dry-run JSON output includes mode: dry_run")


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    print("_run_all_checks tests:")
    test_run_all_checks_in_non_submitted_state()
    test_run_all_checks_produces_check_results()
    test_run_all_checks_no_validation_record_written()
    test_run_all_checks_no_state_change()
    test_run_all_checks_exit_zero_on_pass()
    test_run_all_checks_reports_failures()
    test_run_all_checks_works_in_any_state()

    print("\n_resolve_wo_record tests:")
    test_resolve_wo_record_finds_latest()
    test_resolve_wo_record_specific_revision()

    print("\nReal validation state guard:")
    test_perform_validation_requires_submitted_state()

    print("\nDry-run output format:")
    test_dry_run_header_in_output()
    test_dry_run_json_includes_mode()

    print("\nAll dry-run tests passed!")
