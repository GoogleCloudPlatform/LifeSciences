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

"""Comprehensive end-to-end tests for ``dde program resume``.

Tests the cross-phase state import command that brings accepted work
orders (and associated runs, contexts, validations) from a prior phase's
control plane into the current project.

Run with:
    PYTHONPATH=tools python3 tests/test_program_resume.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import json
import shutil
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

from click.testing import CliRunner
from dde.cli import cli
from dde.core import controlstore
from dde.core.controlstore import (
    CONTROL_DIR,
    ensure_control_dirs,
    next_id,
    read_record,
    write_record,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_project(base: Path, name: str) -> Path:
    """Create a minimal dde project directory with control plane."""
    project = base / name
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    ensure_control_dirs(project)
    return project


def _wo_data(
    wo_id: str,
    revision: int,
    state: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a valid work-order record dict."""
    base = {
        "id": wo_id,
        "revision": revision,
        "state": state,
        "decision_question": f"Test question for {wo_id}",
        "requested_role": "test-agent",
        "stage": "test",
        "cycle": 1,
        "context": {"summary": "test context"},
        "dependencies": [],
        "capabilities": ["test"],
        "deliverables": {"artifact": "test.json"},
        "acceptance_criteria": "tests pass",
        "alert_policy": {"on_failure": "notify"},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "test-lead",
        "created_at": _NOW,
    }
    base.update(overrides)
    return base


def _run_data(
    run_id: str,
    wo_id: str,
    wo_revision: int,
    state: str,
    attempt: int = 1,
) -> dict[str, Any]:
    """Build a valid run record dict."""
    return {
        "run_id": run_id,
        "work_order_id": wo_id,
        "work_order_revision": wo_revision,
        "state": state,
        "attempt": attempt,
        "created_at": _NOW,
    }


def _context_data(
    wo_id: str,
    revision: int,
) -> dict[str, Any]:
    """Build a valid context record dict."""
    return {
        "work_order_id": wo_id,
        "revision": revision,
        "artifact_links": [f"raw/test/{wo_id}.json"],
        "content": {"data": "test context content"},
        "content_sha256": "abc123def456",
        "created_at": _NOW,
    }


def _validation_data(
    wo_id: str,
    wo_revision: int,
    run_id: str,
) -> dict[str, Any]:
    """Build a valid validation record dict."""
    return {
        "work_order_id": wo_id,
        "work_order_revision": wo_revision,
        "run_id": run_id,
        "validated_at": _NOW,
        "result": "pass",
        "checks": [{"name": "schema_check", "passed": True}],
    }


def _write_wo(
    project: Path, wo_id: str, revision: int, state: str, **overrides
) -> None:
    """Write a work-order record to a project's control plane."""
    ident = f"{wo_id}-r{revision}"
    data = _wo_data(wo_id, revision, state, **overrides)
    write_record(project, "work-order", ident, data)


def _write_run(project: Path, run_id: str, wo_id: str, wo_rev: int, state: str) -> None:
    """Write a run record to a project's control plane."""
    data = _run_data(run_id, wo_id, wo_rev, state)
    write_record(project, "run", run_id, data)


def _write_context(project: Path, ident: str, wo_id: str, revision: int) -> None:
    """Write a context record to a project's control plane."""
    data = _context_data(wo_id, revision)
    write_record(project, "context", ident, data)


def _write_validation(
    project: Path, ident: str, wo_id: str, wo_rev: int, run_id: str
) -> None:
    """Write a validation record to a project's control plane."""
    data = _validation_data(wo_id, wo_rev, run_id)
    write_record(project, "validation", ident, data)


def _read_events(project: Path) -> list[dict[str, Any]]:
    """Read all events from the project's event log."""
    events_path = project / CONTROL_DIR / "events.ndjson"
    if not events_path.is_file():
        return []
    events = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _run_resume(
    dest_project: Path,
    source: str,
    extra_args: list[str] | None = None,
) -> Any:
    """Invoke ``dde program resume`` via click's test runner.

    Returns the click test result.
    """
    runner = CliRunner()
    args = ["--project", str(dest_project), "program", "resume", source]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_results: list[tuple[str, str, str | None]] = []  # (name, status, detail)


def _test(name: str):
    """Decorator that registers and runs a test function."""

    def decorator(fn):
        try:
            fn()
            _results.append((name, "PASS", None))
        except AssertionError as exc:
            _results.append((name, "FAIL", str(exc)))
        except Exception as exc:
            _results.append(
                (
                    name,
                    "ERROR",
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
            )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# We use a single tmpdir for the whole run — each test creates its own
# sub-projects inside it.
_TMPBASE = Path(tempfile.mkdtemp(prefix="dde-test-resume-"))


# ---- Test 1: Happy path ----
@_test("1. Happy path — accepted WOs imported with all associated records")
def test_happy_path():
    base = _TMPBASE / "t1"
    base.mkdir()

    # Phase 1 source project
    source = _make_project(base, "phase1")
    # WO-001: accepted with 2 revisions (r1 committed, r2 accepted)
    _write_wo(source, "WO-001", 1, "committed")
    _write_wo(source, "WO-001", 2, "scientifically_accepted")
    # WO-002: accepted (single revision)
    _write_wo(source, "WO-002", 1, "scientifically_accepted")
    # WO-003: rejected — should be skipped
    _write_wo(source, "WO-003", 1, "scientifically_rejected")
    # WO-004: in_progress — should be skipped
    _write_wo(source, "WO-004", 1, "in_progress")

    # Associated records for accepted WOs
    _write_run(source, "RUN-001", "WO-001", 2, "succeeded")
    _write_run(source, "RUN-002", "WO-002", 1, "succeeded")
    _write_run(source, "RUN-003", "WO-003", 1, "failed")  # belongs to skipped WO
    _write_context(source, "CTX-001", "WO-001", 2)
    _write_context(source, "CTX-002", "WO-002", 1)
    _write_context(source, "CTX-003", "WO-003", 1)  # belongs to skipped WO
    _write_validation(source, "VAL-001", "WO-001", 2, "RUN-001")
    _write_validation(source, "VAL-002", "WO-002", 1, "RUN-002")

    # Phase 2 destination project
    dest = _make_project(base, "phase2")

    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # Verify accepted WOs imported (both revisions of WO-001)
    wo_dir = dest / CONTROL_DIR / "work-orders"
    assert (wo_dir / "WO-001-r1.json").is_file(), "WO-001-r1 not imported"
    assert (wo_dir / "WO-001-r2.json").is_file(), "WO-001-r2 not imported"
    assert (wo_dir / "WO-002-r1.json").is_file(), "WO-002-r1 not imported"

    # Verify skipped WOs NOT imported
    assert not (wo_dir / "WO-003-r1.json").is_file(), "WO-003 should not be imported"
    assert not (wo_dir / "WO-004-r1.json").is_file(), "WO-004 should not be imported"

    # Verify associated runs for accepted WOs imported
    run_dir = dest / CONTROL_DIR / "runs"
    assert (run_dir / "RUN-001.json").is_file(), "RUN-001 not imported"
    assert (run_dir / "RUN-002.json").is_file(), "RUN-002 not imported"
    assert not (run_dir / "RUN-003.json").is_file(), "RUN-003 should not be imported"

    # Verify contexts for accepted WOs imported
    ctx_dir = dest / CONTROL_DIR / "contexts"
    assert (ctx_dir / "CTX-001.json").is_file(), "CTX-001 not imported"
    assert (ctx_dir / "CTX-002.json").is_file(), "CTX-002 not imported"
    assert not (ctx_dir / "CTX-003.json").is_file(), "CTX-003 should not be imported"

    # Verify validations for accepted WOs imported
    val_dir = dest / CONTROL_DIR / "validations"
    assert (val_dir / "VAL-001.json").is_file(), "VAL-001 not imported"
    assert (val_dir / "VAL-002.json").is_file(), "VAL-002 not imported"

    # Verify human-readable output mentions imported and skipped
    assert "WO-001" in result.output, "output should mention WO-001"
    assert "WO-002" in result.output, "output should mention WO-002"
    assert "WO-003" in result.output, "output should mention skipped WO-003"
    assert "WO-004" in result.output, "output should mention skipped WO-004"
    assert "2 work orders imported" in result.output, "summary should say 2 imported"
    assert "2 skipped" in result.output, "summary should say 2 skipped"


# ---- Test 2: next_id continuity ----
@_test(
    "2. next_id continuity — after importing WO-001 and WO-002, next_id returns WO-003"
)
def test_next_id_continuity():
    base = _TMPBASE / "t2"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")
    _write_wo(source, "WO-002", 1, "scientifically_accepted")
    _write_run(source, "RUN-001", "WO-001", 1, "succeeded")
    _write_run(source, "RUN-002", "WO-002", 1, "succeeded")

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # next_id for work-orders should be WO-003
    nid = next_id(dest, "work-order")
    assert nid == "WO-003", f"expected WO-003, got {nid}"

    # next_id for runs should be RUN-003
    nid_run = next_id(dest, "run")
    assert nid_run == "RUN-003", f"expected RUN-003, got {nid_run}"


# ---- Test 3: ID conflict / idempotency ----
@_test("3. Idempotency — second resume refuses with exit 9")
def test_idempotency_conflict():
    base = _TMPBASE / "t3"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")

    dest = _make_project(base, "phase2")

    # First run succeeds
    result1 = _run_resume(dest, str(source))
    assert result1.exit_code == 0, f"first run exit code {result1.exit_code}"

    # Second run must refuse with exit 9
    runner = CliRunner()
    result2 = runner.invoke(
        cli,
        ["--project", str(dest), "program", "resume", str(source)],
    )
    assert result2.exit_code == 9, (
        f"expected exit 9 on second run, got {result2.exit_code}; "
        f"output={result2.output}, stderr={getattr(result2, 'stderr', '')}"
    )


# ---- Test 4: Edge case — empty source (zero accepted WOs) ----
@_test("4. Edge case — empty source with zero accepted work orders")
def test_empty_source():
    base = _TMPBASE / "t4"
    base.mkdir()

    source = _make_project(base, "phase1")
    # Only rejected WOs — nothing to import
    _write_wo(source, "WO-001", 1, "scientifically_rejected")
    _write_wo(source, "WO-002", 1, "cancelled")

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # No WOs in destination
    wo_dir = dest / CONTROL_DIR / "work-orders"
    wo_files = list(wo_dir.glob("*.json"))
    assert len(wo_files) == 0, f"expected 0 WO files, found {len(wo_files)}"

    # Summary should say 0 imported
    assert "0 work orders imported" in result.output, (
        f"summary should mention 0 imported; output: {result.output}"
    )

    # Event should still be logged
    events = _read_events(dest)
    resumed_events = [e for e in events if e.get("type") == "program.resumed"]
    assert len(resumed_events) == 1, (
        f"expected 1 program.resumed event, got {len(resumed_events)}"
    )
    assert resumed_events[0]["imported_work_orders"] == [], (
        "imported_work_orders should be empty"
    )


# ---- Test 5: Edge case — source path variants ----
@_test("5. Source path variants — project root AND direct .dde/control/ path")
def test_source_path_variants():
    base = _TMPBASE / "t5"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")

    # Variant A: using project root path
    dest_a = _make_project(base, "phase2a")
    result_a = _run_resume(dest_a, str(source))
    assert result_a.exit_code == 0, f"project root path: exit {result_a.exit_code}"

    # Variant B: using direct .dde/control/ path
    dest_b = _make_project(base, "phase2b")
    control_path = str(source / ".dde" / "control")
    result_b = _run_resume(dest_b, control_path)
    assert result_b.exit_code == 0, f".dde/control/ path: exit {result_b.exit_code}"

    # Both should have imported the same WO
    assert (dest_a / CONTROL_DIR / "work-orders" / "WO-001-r1.json").is_file(), (
        "variant A: WO not imported"
    )
    assert (dest_b / CONTROL_DIR / "work-orders" / "WO-001-r1.json").is_file(), (
        "variant B: WO not imported"
    )


# ---- Test 6: Edge case — invalid source ----
@_test("6. Invalid source — non-existent path and path without .dde/control/")
def test_invalid_source():
    base = _TMPBASE / "t6"
    base.mkdir()

    dest = _make_project(base, "phase2")

    # 6a: Non-existent path
    runner = CliRunner()
    result_a = runner.invoke(
        cli,
        ["--project", str(dest), "program", "resume", "/nonexistent/path/xyz"],
    )
    assert result_a.exit_code != 0, (
        f"non-existent path should fail, got exit {result_a.exit_code}"
    )

    # 6b: Existing directory without .dde/control/
    no_control = base / "no-control-plane"
    no_control.mkdir()
    result_b = runner.invoke(
        cli,
        ["--project", str(dest), "program", "resume", str(no_control)],
    )
    assert result_b.exit_code != 0, (
        f"path without control plane should fail, got exit {result_b.exit_code}"
    )


# ---- Test 7: imported_from field ----
@_test("7. imported_from field — present on all imported records")
def test_imported_from_field():
    base = _TMPBASE / "t7"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")
    _write_run(source, "RUN-001", "WO-001", 1, "succeeded")
    _write_context(source, "CTX-001", "WO-001", 1)
    _write_validation(source, "VAL-001", "WO-001", 1, "RUN-001")

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    source_str = str(source.resolve())

    # Check imported_from on work order
    wo = read_record(dest, "work-order", "WO-001-r1")
    assert "imported_from" in wo, "WO missing imported_from"
    assert wo["imported_from"] == source_str, (
        f"WO imported_from mismatch: {wo['imported_from']} != {source_str}"
    )

    # Check imported_from on run
    run = read_record(dest, "run", "RUN-001")
    assert "imported_from" in run, "run missing imported_from"
    assert run["imported_from"] == source_str, "run imported_from mismatch"

    # Check imported_from on context
    ctx = read_record(dest, "context", "CTX-001")
    assert "imported_from" in ctx, "context missing imported_from"
    assert ctx["imported_from"] == source_str, "context imported_from mismatch"

    # Check imported_from on validation
    val = read_record(dest, "validation", "VAL-001")
    assert "imported_from" in val, "validation missing imported_from"
    assert val["imported_from"] == source_str, "validation imported_from mismatch"


# ---- Test 8: events.ndjson — program.resumed event ----
@_test("8. events.ndjson — program.resumed event logged with correct content")
def test_events_ndjson():
    base = _TMPBASE / "t8"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")
    _write_wo(source, "WO-002", 1, "scientifically_rejected")
    _write_run(source, "RUN-001", "WO-001", 1, "succeeded")

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    events = _read_events(dest)
    resumed = [e for e in events if e.get("type") == "program.resumed"]
    assert len(resumed) == 1, f"expected 1 program.resumed, got {len(resumed)}"

    evt = resumed[0]
    assert evt["type"] == "program.resumed"
    assert "source" in evt, "event missing 'source'"
    assert "timestamp" in evt, "event missing 'timestamp'"
    assert "imported_work_orders" in evt, "event missing 'imported_work_orders'"
    assert "imported_runs" in evt, "event missing 'imported_runs'"
    assert "skipped_work_orders" in evt, "event missing 'skipped_work_orders'"

    assert "WO-001" in evt["imported_work_orders"], (
        "WO-001 should be in imported_work_orders"
    )
    assert "WO-001" not in evt["skipped_work_orders"], "WO-001 should not be in skipped"
    assert "WO-002" in evt["skipped_work_orders"], "WO-002 should be in skipped"
    assert evt["skipped_work_orders"]["WO-002"] == "scientifically_rejected", (
        f"WO-002 skip reason should be 'scientifically_rejected', got {evt['skipped_work_orders'].get('WO-002')}"
    )
    assert "RUN-001" in evt["imported_runs"], "RUN-001 should be in imported_runs"


# ---- Test 9: --json output ----
@_test("9. --json output mode works")
def test_json_output():
    base = _TMPBASE / "t9"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")
    _write_run(source, "RUN-001", "WO-001", 1, "succeeded")

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source), extra_args=["--json"])
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    # Output should be valid JSON
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"--json output is not valid JSON: {result.output[:500]}"
        ) from exc

    assert "imported_work_orders" in payload, "JSON missing imported_work_orders"
    assert "WO-001" in payload["imported_work_orders"], (
        "JSON missing WO-001 in imported list"
    )
    assert "imported_runs" in payload, "JSON missing imported_runs"
    assert "RUN-001" in payload["imported_runs"], (
        "JSON missing RUN-001 in imported list"
    )
    assert "source" in payload, "JSON missing source field"
    assert "skipped_work_orders" in payload, "JSON missing skipped_work_orders"
    assert "imported_contexts" in payload, "JSON missing imported_contexts count"
    assert "imported_validations" in payload, "JSON missing imported_validations count"


# ---- Test 10: --quiet output ----
@_test("10. --quiet output mode works")
def test_quiet_output():
    base = _TMPBASE / "t10"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source), extra_args=["--quiet"])
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    # Quiet mode should not print the human-readable summary lines
    assert "Imported:" not in result.output, (
        f"--quiet should suppress human summary; output: {result.output}"
    )
    assert "Summary:" not in result.output, (
        f"--quiet should suppress summary line; output: {result.output}"
    )
    assert "Source:" not in result.output, (
        f"--quiet should suppress Source: line; output: {result.output}"
    )


# ---- Test 11: Leases are never imported ----
@_test("11. Leases are never imported")
def test_leases_not_imported():
    base = _TMPBASE / "t11"
    base.mkdir()

    source = _make_project(base, "phase1")
    _write_wo(source, "WO-001", 1, "scientifically_accepted")

    # Write a lease record manually (directly to disk, bypassing write_record)
    lease_dir = source / CONTROL_DIR / "leases"
    lease_dir.mkdir(parents=True, exist_ok=True)
    lease_data = {
        "resource": "gpu-0",
        "state": "released",
        "work_order_id": "WO-001",
        "work_order_revision": 1,
        "run_id": "RUN-001",
        "holder_agent": "test-agent",
        "granted_at": _NOW,
        "ttl_minutes": 60,
        "expires_at": _NOW,
        "extensions": 0,
    }
    (lease_dir / "LEASE-001.json").write_text(
        json.dumps(lease_data, indent=2), encoding="utf-8"
    )

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    # Verify no leases were imported
    dest_lease_dir = dest / CONTROL_DIR / "leases"
    lease_files = list(dest_lease_dir.glob("*.json")) if dest_lease_dir.is_dir() else []
    assert len(lease_files) == 0, f"expected 0 lease files, found {len(lease_files)}"


# ---- Test 12: Multiple revisions — only latest determines acceptance ----
@_test("12. Multiple revisions — latest revision determines acceptance classification")
def test_multiple_revisions_latest_state():
    base = _TMPBASE / "t12"
    base.mkdir()

    source = _make_project(base, "phase1")
    # WO-001: revision 1 accepted, but revision 2 rejected → should be skipped
    _write_wo(source, "WO-001", 1, "scientifically_accepted")
    _write_wo(source, "WO-001", 2, "scientifically_rejected")

    # WO-002: revision 1 rejected, revision 2 accepted → should be imported
    _write_wo(source, "WO-002", 1, "scientifically_rejected")
    _write_wo(source, "WO-002", 2, "scientifically_accepted")

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    wo_dir = dest / CONTROL_DIR / "work-orders"
    # WO-001 should not be imported (latest revision rejected)
    assert not (wo_dir / "WO-001-r1.json").is_file(), "WO-001-r1 should NOT be imported"
    assert not (wo_dir / "WO-001-r2.json").is_file(), "WO-001-r2 should NOT be imported"

    # WO-002 should be imported (all revisions, because latest is accepted)
    assert (wo_dir / "WO-002-r1.json").is_file(), "WO-002-r1 should be imported"
    assert (wo_dir / "WO-002-r2.json").is_file(), "WO-002-r2 should be imported"


# ---------------------------------------------------------------------------
# Markdown work-order test helpers
# ---------------------------------------------------------------------------

_ACCEPTED_MD = """\
---
id: WO-001
state: scientifically_accepted
decision_question: "What is the effect of compound X on target Y?"
requested_role: analyst
stage: exploration
cycle: 1
priority: normal
resource_class: standard
report_to: science-lead
dependencies: []
capabilities:
  - statistical-analysis
---

## Context
Background context for the analysis of compound X on target Y.
Prior studies suggest a positive interaction.

## Deliverables
- analysis_report: Statistical analysis report with findings
- summary_chart: Visualization of key results

## Acceptance Criteria
- Report includes p-values and confidence intervals
- Analysis accounts for potential confounders

## Alert Policy
- data_quality: missing values exceed 10%
- runtime: execution exceeds 30 minutes
"""


def _write_markdown_wo(directory: Path, filename: str, content: str) -> Path:
    """Write a markdown work-order file to a directory."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def _make_md_source_with_control(base: Path, name: str) -> Path:
    """Create a project with .dde/control/ for placing markdown files."""
    project = _make_project(base, name)
    wo_dir = project / CONTROL_DIR / "work-orders"
    wo_dir.mkdir(parents=True, exist_ok=True)
    return project


# ---------------------------------------------------------------------------
# Markdown import tests
# ---------------------------------------------------------------------------


# ---- Test 13: Markdown happy path — accepted markdown WOs imported ----
@_test("13. Markdown happy path — accepted markdown WOs imported from control plane")
def test_md_happy_path():
    base = _TMPBASE / "t13"
    base.mkdir()

    source = _make_md_source_with_control(base, "phase1")
    wo_dir = source / CONTROL_DIR / "work-orders"
    _write_markdown_wo(wo_dir, "WO-001.md", _ACCEPTED_MD)

    # Also write a non-accepted markdown WO.
    rejected_md = _ACCEPTED_MD.replace(
        "state: scientifically_accepted",
        "state: scientifically_rejected",
    ).replace("id: WO-001", "id: WO-002")
    _write_markdown_wo(wo_dir, "WO-002.md", rejected_md)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # WO-001 should be imported.
    imported = dest / CONTROL_DIR / "work-orders" / "WO-001-r1.json"
    assert imported.is_file(), "WO-001-r1.json not created"

    # Verify record contents.
    data = json.loads(imported.read_text(encoding="utf-8"))
    assert data["id"] == "WO-001"
    assert data["state"] == "scientifically_accepted"
    assert data["decision_question"] == "What is the effect of compound X on target Y?"
    assert data["requested_role"] == "analyst"
    assert data["revision"] == 1
    assert isinstance(data["context"], dict)
    assert "compound X" in data["context"]["content"]
    assert isinstance(data["deliverables"], dict)
    assert "analysis_report" in data["deliverables"]
    assert isinstance(data["acceptance_criteria"], list)
    assert len(data["acceptance_criteria"]) == 2
    assert isinstance(data["alert_policy"], dict)
    assert "data_quality" in data["alert_policy"]
    assert data["imported_from_format"] == "markdown"

    # WO-002 should NOT be imported (rejected).
    assert not (dest / CONTROL_DIR / "work-orders" / "WO-002-r1.json").is_file(), (
        "WO-002 should not be imported"
    )

    # Output should mention both.
    assert "WO-001" in result.output
    assert "WO-002" in result.output
    assert "1 work orders imported" in result.output
    assert "1 skipped" in result.output


# ---- Test 14: Markdown-only source (no .dde/control/) ----
@_test("14. Markdown-only source — directory of .md files without control plane")
def test_md_only_source():
    base = _TMPBASE / "t14"
    base.mkdir()

    # Source is just a directory with markdown files, no .dde/control/.
    source = base / "phase1-legacy"
    source.mkdir()
    _write_markdown_wo(source, "WO-001.md", _ACCEPTED_MD)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # WO should be imported.
    imported = dest / CONTROL_DIR / "work-orders" / "WO-001-r1.json"
    assert imported.is_file(), "WO-001-r1.json not created from markdown-only source"

    data = json.loads(imported.read_text(encoding="utf-8"))
    assert data["id"] == "WO-001"
    assert data["state"] == "scientifically_accepted"
    assert data["imported_from_format"] == "markdown"


# ---- Test 15: Mixed JSON and markdown WOs ----
@_test("15. Mixed source — JSON and markdown WOs imported together")
def test_mixed_json_and_markdown():
    base = _TMPBASE / "t15"
    base.mkdir()

    source = _make_md_source_with_control(base, "phase1")

    # JSON WO (accepted).
    _write_wo(source, "WO-001", 1, "scientifically_accepted")

    # Markdown WO (accepted, different ID).
    md_accepted = _ACCEPTED_MD.replace("id: WO-001", "id: WO-002")
    wo_dir = source / CONTROL_DIR / "work-orders"
    _write_markdown_wo(wo_dir, "WO-002.md", md_accepted)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # Both should be imported.
    assert (dest / CONTROL_DIR / "work-orders" / "WO-001-r1.json").is_file(), (
        "JSON WO-001 not imported"
    )
    assert (dest / CONTROL_DIR / "work-orders" / "WO-002-r1.json").is_file(), (
        "Markdown WO-002 not imported"
    )

    assert "2 work orders imported" in result.output


# ---- Test 16: Non-standard markdown ID reconciliation ----
@_test("16. Non-standard ID — markdown WO with legacy ID gets remapped to WO-NNN")
def test_md_id_reconciliation():
    base = _TMPBASE / "t16"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()

    # Markdown WO with a non-standard ID.
    legacy_md = _ACCEPTED_MD.replace("id: WO-001", "id: LEGACY-PHASE1-001")
    _write_markdown_wo(source, "legacy.md", legacy_md)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # Should be remapped to WO-001.
    imported = dest / CONTROL_DIR / "work-orders" / "WO-001-r1.json"
    assert imported.is_file(), "remapped WO-001-r1.json not created"

    data = json.loads(imported.read_text(encoding="utf-8"))
    assert data["id"] == "WO-001"
    assert data["imported_original_id"] == "LEGACY-PHASE1-001"
    assert data["imported_from_format"] == "markdown"

    # Report should mention original ID.
    assert "LEGACY-PHASE1-001" in result.output


# ---- Test 17: Markdown WO missing required frontmatter field ----
@_test("17. Markdown refused — missing required frontmatter field")
def test_md_missing_field():
    base = _TMPBASE / "t17"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()

    # Remove decision_question from frontmatter.
    bad_md = _ACCEPTED_MD.replace(
        'decision_question: "What is the effect of compound X on target Y?"\n',
        "",
    )
    _write_markdown_wo(source, "bad.md", bad_md)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, (
        f"exit code {result.exit_code}, output: {result.output}"
    )

    # No WOs should be imported (the only file was refused).
    wo_dir = dest / CONTROL_DIR / "work-orders"
    wo_files = list(wo_dir.glob("*.json"))
    assert len(wo_files) == 0, f"expected 0 WO files, found {len(wo_files)}"

    # Output should report the refusal.
    assert "refused" in result.output.lower() or "Refused" in result.output


# ---- Test 18: Markdown WO missing body section ----
@_test("18. Markdown refused — missing body section (## Deliverables)")
def test_md_missing_section():
    base = _TMPBASE / "t18"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()

    # Remove Deliverables section.
    lines = _ACCEPTED_MD.splitlines()
    filtered = []
    skip = False
    for line in lines:
        if line.startswith("## Deliverables"):
            skip = True
            continue
        if skip and line.startswith("## "):
            skip = False
        if not skip:
            filtered.append(line)
    bad_md = "\n".join(filtered)
    _write_markdown_wo(source, "no-deliverables.md", bad_md)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    # No WOs imported.
    wo_files = list((dest / CONTROL_DIR / "work-orders").glob("*.json"))
    assert len(wo_files) == 0, f"expected 0, got {len(wo_files)}"


# ---- Test 19: Markdown WO without frontmatter ----
@_test("19. Markdown refused — no YAML frontmatter at all")
def test_md_no_frontmatter():
    base = _TMPBASE / "t19"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()

    # Just plain markdown, no frontmatter.
    plain_md = "# Work Order\n\nSome description\n"
    _write_markdown_wo(source, "plain.md", plain_md)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0, f"exit code {result.exit_code}"

    # No WOs imported.
    wo_files = list((dest / CONTROL_DIR / "work-orders").glob("*.json"))
    assert len(wo_files) == 0


# ---- Test 20: Markdown idempotency — second resume refuses ----
@_test("20. Markdown idempotency — second resume against same source refuses")
def test_md_idempotency():
    base = _TMPBASE / "t20"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()
    _write_markdown_wo(source, "WO-001.md", _ACCEPTED_MD)

    dest = _make_project(base, "phase2")

    # First run succeeds.
    result1 = _run_resume(dest, str(source))
    assert result1.exit_code == 0, f"first run exit {result1.exit_code}"

    # Second run must refuse with exit 9 (ID conflict).
    runner = CliRunner()
    result2 = runner.invoke(
        cli,
        ["--project", str(dest), "program", "resume", str(source)],
    )
    assert result2.exit_code == 9, (
        f"expected exit 9, got {result2.exit_code}; output: {result2.output}"
    )


# ---- Test 20b: Non-canonical ID idempotency (the #111 regression) ----
@_test(
    "20b. Non-canonical ID idempotency — second resume refuses (not silently re-imports)"
)
def test_md_noncanonical_idempotency():
    base = _TMPBASE / "t20b"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()

    # Non-canonical ID — exactly the scenario that triggered the bug.
    legacy_md = _ACCEPTED_MD.replace("id: WO-001", "id: WO-1")
    _write_markdown_wo(source, "legacy-wo1.md", legacy_md)

    dest = _make_project(base, "phase2")

    # First run: imports WO-1 as WO-001.
    result1 = _run_resume(dest, str(source))
    assert result1.exit_code == 0, f"first run exit {result1.exit_code}"

    wo_dir = dest / CONTROL_DIR / "work-orders"
    assert (wo_dir / "WO-001-r1.json").is_file(), (
        "WO-001-r1 should exist after first run"
    )

    # Second run: must refuse, not silently create WO-002.
    runner = CliRunner()
    result2 = runner.invoke(
        cli,
        ["--project", str(dest), "program", "resume", str(source)],
    )
    assert result2.exit_code == 9, (
        f"expected exit 9 on re-run with non-canonical ID, got {result2.exit_code}; "
        f"output: {result2.output}"
    )

    # Must NOT have created a second WO.
    wo_files = sorted(p.name for p in wo_dir.glob("*.json"))
    assert wo_files == ["WO-001-r1.json"], (
        f"expected only WO-001-r1.json, got {wo_files} — "
        "duplicate import was not prevented"
    )


# ---- Test 20c: Non-canonical ID idempotency with intervening creates ----
@_test("20c. Non-canonical ID idempotency with intervening workorder creates")
def test_md_noncanonical_idempotency_with_creates():
    base = _TMPBASE / "t20c"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()
    legacy_md = _ACCEPTED_MD.replace("id: WO-001", "id: LEGACY-7")
    _write_markdown_wo(source, "legacy.md", legacy_md)

    dest = _make_project(base, "phase2")

    # First run: imports LEGACY-7 as WO-001.
    result1 = _run_resume(dest, str(source))
    assert result1.exit_code == 0, f"first run exit {result1.exit_code}"

    # Create WO-002 natively (simulating normal workflow between runs).
    _write_wo(dest, "WO-002", 1, "proposed")

    # Re-run resume: must still refuse despite WO-002 now existing
    # (the bug was that reconciliation would produce WO-003, which
    # _conflict_exists wouldn't catch).
    runner = CliRunner()
    result2 = runner.invoke(
        cli,
        ["--project", str(dest), "program", "resume", str(source)],
    )
    assert result2.exit_code == 9, (
        f"expected exit 9, got {result2.exit_code}; output: {result2.output}"
    )

    wo_dir = dest / CONTROL_DIR / "work-orders"
    wo_files = sorted(p.name for p in wo_dir.glob("*.json"))
    assert "WO-003-r1.json" not in wo_files, (
        "WO-003 should not exist — duplicate import was not prevented"
    )


# ---- Test 21: Markdown imported_from field ----
@_test("21. Markdown imported_from field present on imported records")
def test_md_imported_from():
    base = _TMPBASE / "t21"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()
    _write_markdown_wo(source, "WO-001.md", _ACCEPTED_MD)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0

    data = read_record(dest, "work-order", "WO-001-r1")
    assert "imported_from" in data
    assert data["imported_from"] == str(source.resolve())
    assert data["imported_from_format"] == "markdown"


# ---- Test 22: Markdown WOs pass schema validation ----
@_test("22. Markdown WOs pass controlstore._validate_work_order")
def test_md_schema_validation():
    base = _TMPBASE / "t22"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()
    _write_markdown_wo(source, "WO-001.md", _ACCEPTED_MD)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0

    # Read the imported record and validate it explicitly.
    data = read_record(dest, "work-order", "WO-001-r1")
    errors = controlstore._VALIDATORS["work-order"](data)
    assert errors == [], f"validation errors: {errors}"


# ---- Test 23: Markdown + JSON duplicate ID refuses ----
@_test("23. Duplicate ID across JSON and markdown — refuses with exit 9")
def test_md_json_duplicate_id():
    base = _TMPBASE / "t23"
    base.mkdir()

    source = _make_md_source_with_control(base, "phase1")

    # JSON WO with ID WO-001.
    _write_wo(source, "WO-001", 1, "scientifically_accepted")

    # Markdown WO with same ID WO-001.
    wo_dir = source / CONTROL_DIR / "work-orders"
    _write_markdown_wo(wo_dir, "WO-001.md", _ACCEPTED_MD)

    dest = _make_project(base, "phase2")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--project", str(dest), "program", "resume", str(source)],
    )
    assert result.exit_code == 9, (
        f"expected exit 9 for duplicate ID, got {result.exit_code}; "
        f"output: {result.output}"
    )


# ---- Test 24: Markdown next_id continuity ----
@_test(
    "24. Markdown next_id continuity — after importing WO-001, next_id returns WO-002"
)
def test_md_next_id_continuity():
    base = _TMPBASE / "t24"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()
    _write_markdown_wo(source, "WO-001.md", _ACCEPTED_MD)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0

    nid = next_id(dest, "work-order")
    assert nid == "WO-002", f"expected WO-002, got {nid}"


# ---- Test 25: Markdown event logged correctly ----
@_test("25. Markdown program.resumed event logged correctly")
def test_md_event():
    base = _TMPBASE / "t25"
    base.mkdir()

    source = base / "phase1-legacy"
    source.mkdir()
    _write_markdown_wo(source, "WO-001.md", _ACCEPTED_MD)

    # Also add a non-accepted markdown WO.
    rejected_md = _ACCEPTED_MD.replace(
        "state: scientifically_accepted",
        "state: in_progress",
    ).replace("id: WO-001", "id: WO-002")
    _write_markdown_wo(source, "WO-002.md", rejected_md)

    dest = _make_project(base, "phase2")
    result = _run_resume(dest, str(source))
    assert result.exit_code == 0

    events = _read_events(dest)
    resumed = [e for e in events if e.get("type") == "program.resumed"]
    assert len(resumed) == 1

    evt = resumed[0]
    assert "WO-001" in evt["imported_work_orders"]
    assert "WO-002" in evt["skipped_work_orders"]
    assert evt["skipped_work_orders"]["WO-002"] == "in_progress"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _print_report():
    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    errors = sum(1 for _, s, _ in _results if s == "ERROR")
    total = len(_results)

    print("\n" + "=" * 72)
    print("TEST REPORT: dde program resume")
    print("=" * 72)
    for name, status, detail in _results:
        icon = {"PASS": "✓", "FAIL": "✗", "ERROR": "⚠"}.get(status, "?")
        print(f"  [{icon}] {status}: {name}")
        if detail:
            for line in detail.strip().splitlines():
                print(f"        {line}")
    print("-" * 72)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed} | Errors: {errors}")
    print("=" * 72)

    if failed + errors == 0:
        print("\nVERDICT: ALL TESTS PASSED")
    else:
        print(f"\nVERDICT: {failed + errors} issue(s) found")

    return 0 if (failed + errors) == 0 else 1


if __name__ == "__main__":
    exit_code = _print_report()
    # Clean up
    shutil.rmtree(_TMPBASE, ignore_errors=True)
    sys.exit(exit_code)
