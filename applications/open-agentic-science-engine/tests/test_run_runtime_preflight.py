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

"""Regression tests for the specialist runtime-preflight gate.

A run must remain in ``starting`` until the specialist executes the
``oase run preflight`` canary from inside its own harness.  Completing that
command proves that one real tool call traversed the harness's PreToolUse hook
and records the runtime identity used for the attempt.

Run with:
    PYTHONPATH=tools python3 tests/test_run_runtime_preflight.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from click.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from oase.commands.run import run
from oase.common import AppState
from oase.core.controlstore import ensure_control_dirs, read_record, write_record

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


def _make_starting_run() -> tuple[Path, CliRunner]:
    base = Path(tempfile.mkdtemp())
    project = base / "test-project"
    project.mkdir(parents=True)
    (project / ".oase").mkdir()
    ensure_control_dirs(project)

    work_order = {
        "id": "WO-001",
        "revision": 1,
        "state": "committed",
        "decision_question": "test runtime readiness",
        "requested_role": "computational-biologist",
        "stage": "stage1-target-nomination",
        "cycle": "test",
        "context": {},
        "dependencies": [],
        "capabilities": [],
        "deliverables": {},
        "acceptance_criteria": [],
        "alert_policy": {},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "science-lead",
        "created_at": _NOW,
        "committed_at": _NOW,
    }
    write_record(project, "work-order", "WO-001-r1", work_order)

    runner = CliRunner(env={"OASE_PROJECT": str(project)})
    obj = AppState(project_override=str(project))
    created = runner.invoke(run, ["create", "WO-001"], obj=obj)
    assert created.exit_code == 0, created.output
    starting = runner.invoke(run, ["transition", "RUN-001", "starting"], obj=obj)
    assert starting.exit_code == 0, starting.output
    return project, runner


def _invoke(project: Path, runner: CliRunner, args: list[str]):
    return runner.invoke(
        run,
        args,
        obj=AppState(project_override=str(project)),
    )


def test_running_requires_successful_runtime_preflight() -> None:
    project, runner = _make_starting_run()

    result = _invoke(project, runner, ["transition", "RUN-001", "running"])

    assert result.exit_code != 0, "run entered running without a specialist canary"
    assert "preflight" in result.output.lower(), result.output
    assert read_record(project, "run", "RUN-001")["state"] == "starting"


def test_preflight_records_runtime_and_transitions_to_running() -> None:
    project, runner = _make_starting_run()

    result = _invoke(
        project,
        runner,
        [
            "preflight",
            "RUN-001",
            "--harness",
            "antigravity",
            "--runtime-version",
            "1.2.12",
        ],
    )

    assert result.exit_code == 0, result.output
    record = read_record(project, "run", "RUN-001")
    assert record["state"] == "running"
    assert record["started_at"]
    assert record["runtime_preflight"] == {
        "status": "passed",
        "harness": "antigravity",
        "runtime_version": "1.2.12",
        "canary": "oase.run.preflight.v1",
        "checked_at": record["runtime_preflight"]["checked_at"],
    }

    events_path = project / ".oase" / "control" / "events.ndjson"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    preflight_events = [e for e in events if e["type"] == "run.runtime_preflight"]
    assert len(preflight_events) == 1
    assert preflight_events[0]["subject_id"] == "RUN-001"
    assert preflight_events[0]["harness"] == "antigravity"
    assert preflight_events[0]["runtime_version"] == "1.2.12"


def test_preflight_requires_runtime_identity() -> None:
    project, runner = _make_starting_run()

    result = _invoke(
        project,
        runner,
        ["preflight", "RUN-001", "--harness", "antigravity"],
    )

    assert result.exit_code != 0
    assert "--runtime-version" in result.output, result.output
    assert read_record(project, "run", "RUN-001")["state"] == "starting"


def test_starting_run_can_fail_without_passing_preflight() -> None:
    project, runner = _make_starting_run()

    result = _invoke(
        project,
        runner,
        [
            "transition",
            "RUN-001",
            "failed",
            "--failure-class",
            "persistent_infrastructure",
            "--detail",
            "specialist tool-hook canary timed out",
        ],
    )

    assert result.exit_code == 0, result.output
    record = read_record(project, "run", "RUN-001")
    assert record["state"] == "failed"
    assert record["failure_class"] == "persistent_infrastructure"


print("=" * 68)
print("test_run_runtime_preflight.py — specialist runtime health gate")
print("=" * 68)

_check(
    "generic transition cannot mark an unverified specialist running",
    test_running_requires_successful_runtime_preflight,
)
_check(
    "preflight records runtime identity and marks the run healthy",
    test_preflight_records_runtime_and_transitions_to_running,
)
_check("preflight requires runtime identity", test_preflight_requires_runtime_identity)
_check(
    "a starting run can fail when its canary cannot execute",
    test_starting_run_can_fail_without_passing_preflight,
)

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
