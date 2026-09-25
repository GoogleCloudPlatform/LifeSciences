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

"""Run record management for the control plane.

A run is a single execution attempt against a committed work-order
revision.  Multiple runs can reference the same revision (retries),
and each run tracks its own state through the run state machine
defined in ``core/statemachine.py``.

Runs are CRUD + state-machine operations over control records — not
science tools.  They do not follow the two-phase pattern, produce no
Layer 0 artifacts, and never touch the network.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import click

from ..common import AppState, DDEGroup, pass_state
from ..core import controlstore
from ..core.errors import Refusal, UsageError
from ..core.output import Emitter
from ..core.statemachine import (
    TERMINAL_RUN_STATES,
    TERMINAL_WO_STATES,
    validate_transition,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_FAILURE_CLASSES = frozenset(
    {
        "transient_infrastructure",
        "persistent_infrastructure",
        "contract_failure",
        "scientific_block",
        "critical_alert",
        "policy_boundary",
    }
)

_WO_REV_RE = re.compile(r"^.*-r(\d+)\.json$")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_latest_revision(project_root: Path, wo_id: str) -> int:
    """Scan work-orders/ for the highest revision of *wo_id*."""
    wo_dir = project_root / controlstore.CONTROL_DIR / "work-orders"
    if not wo_dir.is_dir():
        raise Refusal(
            f"no work-order directory found for {wo_id}",
            remedy="create a work order first",
        )
    pattern = f"{wo_id}-r*.json"
    matches = sorted(wo_dir.glob(pattern))
    if not matches:
        raise Refusal(
            f"no revisions found for work order {wo_id}",
            remedy="create a work order first",
        )
    max_rev = 0
    for path in matches:
        m = _WO_REV_RE.match(path.name)
        if m:
            max_rev = max(max_rev, int(m.group(1)))
    if max_rev == 0:
        raise Refusal(
            f"no valid revisions found for work order {wo_id}",
            remedy="create a work order first",
        )
    return max_rev


def _count_attempts(project_root: Path, wo_id: str, revision: int) -> int:
    """Count existing runs for the same WO+revision."""
    runs = controlstore.list_records(
        project_root,
        "run",
        filter_fn=lambda r: (
            r.get("work_order_id") == wo_id and r.get("work_order_revision") == revision
        ),
    )
    return len(runs)


def _log_transition(
    project_root: Path,
    subject_id: str,
    from_state: str | None,
    to_state: str,
    detail: str | None = None,
) -> None:
    """Append a run.transition event to events.ndjson."""
    event: dict = {
        "type": "run.transition",
        "subject_id": subject_id,
        "from_state": from_state,
        "to_state": to_state,
    }
    if detail is not None:
        event["detail"] = detail
    controlstore.append_event(project_root, event)


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def run():
    """Run record management for the control plane."""


# ---------------------------------------------------------------------------
# run create
# ---------------------------------------------------------------------------


@run.command()
@click.argument("work_order_id")
@click.option(
    "--revision", type=int, default=None, help="Work-order revision to target."
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a machine-readable JSON record."
)
@click.option("--quiet", is_flag=True, help="Emit output paths only.")
@pass_state
def create(
    state: AppState,
    work_order_id: str,
    revision: int | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Create a run record for a committed work-order revision."""
    root = state.project().root

    # Resolve revision.
    if revision is None:
        revision = _find_latest_revision(root, work_order_id)

    # Read and validate the work-order record.
    wo_identifier = f"{work_order_id}-r{revision}"
    wo = controlstore.read_record(root, "work-order", wo_identifier)
    wo_state = wo.get("state")

    # Must be in committed or later, but not terminal.
    if wo_state in TERMINAL_WO_STATES:
        raise Refusal(
            f"work order {wo_identifier} is in terminal state {wo_state!r}",
            detail="cannot create a run against a terminated work order",
            remedy="use a non-terminal work-order revision",
        )

    # The design says "committed state or later (but not terminal)".
    # The ordering in the state machine is:
    #   proposed → committed → queued → in_progress → submitted → ...
    # "proposed" is before committed, so reject it.
    if wo_state == "proposed":
        raise Refusal(
            f"work order {wo_identifier} is in {wo_state!r} state",
            detail="a run can only be created against a committed or later (non-terminal) work order",
            remedy=f"commit the work order first: dde workorder commit {work_order_id}",
        )

    # Validate the initial state transition (design §7 acceptance #3).
    validate_transition("run", None, "queued")

    # Assign next run ID.
    run_id = controlstore.next_id(root, "run")

    # Count attempts for this WO+revision.
    attempt = _count_attempts(root, work_order_id, revision) + 1

    # Build the run record.
    now = _now_iso()
    record = {
        "run_id": run_id,
        "work_order_id": work_order_id,
        "work_order_revision": revision,
        "state": "queued",
        "attempt": attempt,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "result": None,
        "failure_class": None,
        "failure_detail": None,
    }

    # Write and log.
    path = controlstore.write_record(root, "run", run_id, record)
    _log_transition(root, run_id, None, "queued")

    # Output.
    em = Emitter(as_json=as_json, quiet=quiet)
    em.path(path, role="run_record")
    if as_json:
        for k, v in record.items():
            em.data(k, v)
    else:
        em.line(f"{run_id} created (queued)")
        em.line(f"  work order: {wo_identifier}")
        em.line(f"  attempt: {attempt}")
    em.flush()


# ---------------------------------------------------------------------------
# run transition
# ---------------------------------------------------------------------------


@run.command()
@click.argument("run_id")
@click.argument("target_state")
@click.option(
    "--failure-class",
    default=None,
    help="Failure classification (required for failed/blocked).",
)
@click.option("--detail", default=None, help="Failure detail message.")
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a machine-readable JSON record."
)
@click.option("--quiet", is_flag=True, help="Emit output paths only.")
@pass_state
def transition(
    state: AppState,
    run_id: str,
    target_state: str,
    failure_class: str | None,
    detail: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Transition a run record to a new state."""
    root = state.project().root

    # Read current record.
    record = controlstore.read_record(root, "run", run_id)
    current_state = record["state"]

    # Validate the transition.
    validate_transition("run", current_state, target_state)

    # Require --failure-class for failed/blocked.
    if target_state in ("failed", "blocked"):
        if failure_class is None:
            raise UsageError(
                f"--failure-class is required when transitioning to {target_state!r}",
                detail=f"valid classes: {', '.join(sorted(VALID_FAILURE_CLASSES))}",
                remedy="add --failure-class <class> to specify the failure classification",
            )
        if failure_class not in VALID_FAILURE_CLASSES:
            raise UsageError(
                f"unknown failure class {failure_class!r}",
                detail=f"valid classes: {', '.join(sorted(VALID_FAILURE_CLASSES))}",
            )

    # Update record fields.
    now = _now_iso()
    record["state"] = target_state

    if target_state == "running":
        record["started_at"] = now

    if target_state in TERMINAL_RUN_STATES:
        record["completed_at"] = now

    if failure_class is not None:
        record["failure_class"] = failure_class
        record["failure_detail"] = detail

    if target_state == "succeeded":
        record["result"] = "succeeded"
    elif target_state == "failed":
        record["result"] = "failed"

    # Write and log.
    path = controlstore.write_record(root, "run", run_id, record)
    _log_transition(root, run_id, current_state, target_state, detail=detail)

    # Output.
    em = Emitter(as_json=as_json, quiet=quiet)
    em.path(path, role="run_record")
    if as_json:
        for k, v in record.items():
            em.data(k, v)
    else:
        em.line(f"{run_id}: {current_state} -> {target_state}")
    em.flush()


# ---------------------------------------------------------------------------
# run show
# ---------------------------------------------------------------------------


@run.command()
@click.argument("run_id")
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a machine-readable JSON record."
)
@click.option("--quiet", is_flag=True, help="Emit output paths only.")
@pass_state
def show(state: AppState, run_id: str, as_json: bool, quiet: bool) -> None:
    """Display a run record."""
    root = state.project().root

    record = controlstore.read_record(root, "run", run_id)
    path = root / controlstore.CONTROL_DIR / "runs" / f"{run_id}.json"

    em = Emitter(as_json=as_json, quiet=quiet)
    em.path(path, role="run_record")

    if as_json:
        for k, v in record.items():
            em.data(k, v)
    else:
        em.line(f"Run:       {record.get('run_id', run_id)}")
        em.line(f"State:     {record.get('state')}")
        wo_id = record.get("work_order_id", "")
        wo_rev = record.get("work_order_revision", "")
        em.line(f"Work order: {wo_id}-r{wo_rev}")
        em.line(f"Attempt:   {record.get('attempt')}")
        em.line(f"Created:   {record.get('created_at')}")
        started = record.get("started_at")
        if started:
            em.line(f"Started:   {started}")
        completed = record.get("completed_at")
        if completed:
            em.line(f"Completed: {completed}")
        result = record.get("result")
        if result:
            em.line(f"Result:    {result}")
        fc = record.get("failure_class")
        if fc:
            em.line(f"Failure:   {fc}")
            fd = record.get("failure_detail")
            if fd:
                em.line(f"  detail:  {fd}")

    em.flush()


# ---------------------------------------------------------------------------
# run list
# ---------------------------------------------------------------------------


@run.command(name="list")
@click.option("--work-order", default=None, help="Filter by work-order ID.")
@click.option("--state", "filter_state", default=None, help="Filter by run state.")
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a machine-readable JSON array."
)
@click.option("--quiet", is_flag=True, help="Emit output paths only.")
@pass_state
def list_runs(
    state: AppState,
    work_order: str | None,
    filter_state: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """List run records with optional filters."""
    root = state.project().root

    def match(record: dict) -> bool:
        if work_order is not None and record.get("work_order_id") != work_order:
            return False
        if filter_state is not None and record.get("state") != filter_state:
            return False
        return True

    records = controlstore.list_records(root, "run", filter_fn=match)

    em = Emitter(as_json=as_json, quiet=quiet)

    if as_json:
        em.data("runs", records)
        em.data("count", len(records))
    elif quiet:
        for r in records:
            rid = r.get("run_id", "")
            p = root / controlstore.CONTROL_DIR / "runs" / f"{rid}.json"
            em.path(p)
    else:
        if not records:
            em.line("No run records found.")
        else:
            em.line(f"{'RUN ID':<12} {'WORK ORDER':<16} {'STATE':<14} {'ATTEMPT':<8}")
            em.line("-" * 50)
            for r in records:
                rid = r.get("run_id", "")
                wo = f"{r.get('work_order_id', '')}-r{r.get('work_order_revision', '')}"
                st = r.get("state", "")
                att = r.get("attempt", "")
                em.line(f"{rid:<12} {wo:<16} {st:<14} {att:<8}")

    em.flush()
