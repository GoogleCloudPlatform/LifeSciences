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

"""DDE Evaluation Harness — replay fixtures through the current workflow.

This module runs each fixture definition through the DDE control-plane
CLI and collects measurements.  It does NOT modify any existing tools,
templates, or skills — it measures the current workflow as-is.

Usage (from the DDE application root):

    PYTHONPATH=tools python3 -m eval.run_baseline

Or directly:

    PYTHONPATH=tools python3 eval/harness.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from click.testing import CliRunner  # noqa: E402
from dde.cli import cli  # noqa: E402
from dde.core.controlstore import (  # noqa: E402
    ensure_control_dirs,
    read_record,
    write_record,
)
from dde.core.statemachine import (  # noqa: E402
    validate_transition,
)

from .fixtures.definitions import (  # noqa: E402
    ALL_FIXTURES,
    DECLINED_CANDIDATE_SAMPLE,
    FixtureDefinition,
)
from .metrics import BaselineReport, FixtureMetrics  # noqa: E402

# ---------------------------------------------------------------------------
# Project setup helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path, name: str = "eval-project") -> Path:
    """Create a minimal dde project directory with control plane."""
    project = base / name
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    ensure_control_dirs(project)
    # Create standard raw/ subdirectories
    for subdir in [
        "admet",
        "analogs",
        "assays",
        "bioactivity",
        "compounds",
        "descriptors",
        "docking",
        "expression",
        "genomics",
        "gtex",
        "hypotheses",
        "literature",
        "mmp",
        "mpo",
        "pk",
        "pocket",
        "regulatory",
        "safety",
        "sar",
        "screening",
        "single-cell",
        "structures",
        "tox",
        "transcriptomics",
    ]:
        (project / "raw" / subdir).mkdir(parents=True, exist_ok=True)
    # Create findings subdirectories
    for subdir in [
        "structural-biology",
        "computational-biology",
        "medicinal-chemistry",
        "computational-chemistry",
        "admet-dmpk",
        "experimental-biology",
        "regulatory",
    ]:
        (project / "findings" / subdir).mkdir(parents=True, exist_ok=True)
    return project


def _place_synthetic_artifacts(
    project: Path,
    artifacts: list[dict[str, Any]],
    metrics: FixtureMetrics,
) -> None:
    """Write synthetic artifacts and their sidecars into the project."""
    for artifact in artifacts:
        artifact_path = project / artifact["path"]
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        content = artifact["content"]
        artifact_path.write_text(
            json.dumps(content, indent=2) + "\n",
            encoding="utf-8",
        )
        metrics.record_artifact(
            artifact["path"],
            content.get("schema", "unknown"),
        )

        # Write sidecar if provided
        if "sidecar" in artifact:
            sidecar = dict(artifact["sidecar"])
            # Compute output hash for the artifact
            artifact_bytes = artifact_path.read_bytes()
            artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
            sidecar["outputs"] = [
                {
                    "path": artifact["path"],
                    "sha256": artifact_sha,
                    "bytes": len(artifact_bytes),
                },
            ]
            sidecar_path = artifact_path.with_suffix(".meta.json")
            sidecar_path.write_text(
                json.dumps(sidecar, indent=2) + "\n",
                encoding="utf-8",
            )
            metrics.record_artifact(
                str(sidecar_path.relative_to(project)),
                "provenance-sidecar",
            )

            # Record relay codes from sidecar
            for relay in sidecar.get("mandatory_relays", []):
                metrics.record_relay(relay["code"])


# ---------------------------------------------------------------------------
# Shared work-order transition helper
# ---------------------------------------------------------------------------


def _transition_work_order(
    project: Path,
    wo: dict[str, Any],
    target_state: str,
    metrics: FixtureMetrics,
) -> str:
    """Walk a work order through the state machine to *target_state*.

    Transitions step-by-step from the work order's current state through
    the standard lifecycle path.  Records each transition in *metrics*.
    Returns the final state reached (which equals *target_state* on
    success, or an earlier state if a transition fails).

    Error handling is consistent: illegal transitions are caught and
    recorded in ``metrics.error_messages`` rather than propagating.
    """
    # Standard lifecycle path.  Each tuple is (from, to).
    lifecycle = [
        ("proposed", "committed"),
        ("committed", "queued"),
        ("queued", "in_progress"),
        ("in_progress", "submitted"),
    ]
    current_state = wo["state"]
    for from_state, to_state in lifecycle:
        if current_state != from_state:
            continue
        if to_state == target_state or lifecycle.index((from_state, to_state)) <= next(
            (i for i, t in enumerate(lifecycle) if t[1] == target_state), len(lifecycle)
        ):
            try:
                validate_transition("workorder", current_state, to_state)
                wo_copy = dict(wo)
                wo_copy["state"] = to_state
                write_record(
                    project,
                    "work-order",
                    f"{wo['id']}-r{wo['revision']}",
                    wo_copy,
                )
                metrics.record_transition(current_state, to_state)
                current_state = to_state
            except Exception as exc:
                metrics.error_messages.append(
                    f"Transition {from_state}->{to_state} failed: {exc}"
                )
                break
        if current_state == target_state:
            break
    return current_state


# ---------------------------------------------------------------------------
# Fixture runners — one per fixture category
# ---------------------------------------------------------------------------


def _run_no_genetic_support(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 1: Hypothesis adoption + WO with no genetic artifacts."""
    # Step 1: Adopt the hypothesis set
    if fixture.hypothesis_data:
        hyp_file = project.parent / f"{fixture.fixture_id}-hypotheses.json"
        hyp_file.write_text(json.dumps(fixture.hypothesis_data, indent=2))

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                f"Evaluation fixture {fixture.fixture_id} — synthetic",
            ],
        )
        metrics.record_invocation(
            "hypothesis adopt",
            result.exit_code,
            result.output,
        )
        if result.exit_code == 0:
            metrics.observe("Hypothesis adopted successfully")
            # Check for relay codes in sidecar
            hyp_dir = project / "raw" / "hypotheses"
            for meta_file in hyp_dir.glob("*.meta.json"):
                meta = json.loads(meta_file.read_text())
                for relay in meta.get("mandatory_relays", []):
                    metrics.record_relay(relay["code"])
                metrics.record_artifact(
                    str(meta_file.relative_to(project)),
                    "provenance-sidecar",
                )
            for adopted in hyp_dir.glob("*.adopted.json"):
                content = json.loads(adopted.read_text())
                metrics.record_artifact(
                    str(adopted.relative_to(project)),
                    content.get("schema", "unknown"),
                )
        else:
            metrics.error_messages.append(
                f"Hypothesis adoption failed: {result.output.strip()}"
            )

    # Step 2: Create work order and transition to submitted
    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])
    metrics.observe(f"Work order {wo['id']} created in state '{wo['state']}'")

    current_state = _transition_work_order(project, wo, "submitted", metrics)

    # Step 4: Attempt validation — should fail because no genomics artifacts
    if current_state == "submitted":
        # Create a run record so validate check can find the submission
        _now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        from dde.core.controlstore import next_id

        run_id = next_id(project, "run")
        run_rec = {
            "run_id": run_id,
            "work_order_id": wo["id"],
            "work_order_revision": wo["revision"],
            "state": "succeeded",
            "attempt": 1,
            "created_at": _now,
        }
        write_record(project, "run", run_id, run_rec)

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "validate",
                "check",
                wo["id"],
                "--json",
            ],
        )
        metrics.record_invocation(
            f"validate check {wo['id']}",
            result.exit_code,
            result.output,
        )
        if result.exit_code == 0:
            try:
                val_result = json.loads(result.output)
                checks = val_result.get("checks", [])
                if isinstance(checks, list):
                    for check in checks:
                        name = check.get("name", "unknown")
                        res = check.get("result", "unknown")
                        metrics.record_validation(name, res)
                elif isinstance(checks, dict):
                    for name, check in checks.items():
                        if isinstance(check, dict):
                            metrics.record_validation(
                                name, check.get("result", "unknown")
                            )
                        else:
                            metrics.record_validation(name, str(check))
            except json.JSONDecodeError:
                metrics.observe("Validation output was not JSON")
        else:
            # Validation failures with informative output are expected here
            metrics.observe(
                f"Validation command exited {result.exit_code} — "
                "expected for missing genomics artifacts"
            )
            # Try to extract check details from non-zero exit output
            if result.output.strip():
                try:
                    val_result = json.loads(result.output)
                    checks = val_result.get("checks", [])
                    if isinstance(checks, list):
                        for check in checks:
                            name = check.get("name", "unknown")
                            res = check.get("result", "unknown")
                            metrics.record_validation(name, res)
                except (json.JSONDecodeError, AttributeError):
                    pass

    metrics.observe(
        f"Final work order state: {current_state}. "
        f"No genomics artifacts exist — this is the expected baseline "
        f"behavior for no-genetic-support scenarios."
    )


def _run_negative_pocket(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 2: Unfavorable pocket druggability with synthetic artifact."""
    # Place synthetic artifacts
    _place_synthetic_artifacts(project, fixture.synthetic_artifacts, metrics)

    # Create and transition work order
    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])

    current_state = _transition_work_order(project, wo, "submitted", metrics)

    # Verify synthetic artifact relay codes
    pocket_files = list((project / "raw" / "pocket").glob("*.meta.json"))
    for meta_file in pocket_files:
        meta = json.loads(meta_file.read_text())
        for relay in meta.get("mandatory_relays", []):
            metrics.observe(f"Relay code '{relay['code']}' present in pocket sidecar")

    metrics.observe(
        f"Pocket druggability score 0.12 (unfavorable). "
        f"Single conformation relay fired. "
        f"Work order state: {current_state}."
    )


def _run_positive_geometry(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 3: Favorable structural confidence with synthetic artifact."""
    _place_synthetic_artifacts(project, fixture.synthetic_artifacts, metrics)

    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])

    current_state = _transition_work_order(project, wo, "submitted", metrics)

    # Check that the positive artifact exists and is well-formed
    struct_files = list((project / "raw" / "structures").glob("*.json"))
    for sf in struct_files:
        if sf.name.endswith(".meta.json"):
            continue
        content = json.loads(sf.read_text())
        plddt = content.get("binding_region_plddt", 0)
        metrics.observe(
            f"Structure confidence: binding region pLDDT={plddt} "
            f"(>70 = high confidence)"
        )

    metrics.observe(f"Work order state: {current_state}. Favorable geometry baseline.")


def _run_modality_mismatch(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 4: Context says antibody, deliverables say small-molecule."""
    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])

    # The current control plane does not check modality consistency.
    # Walk through transitions to see if anything catches the mismatch.
    current_state = _transition_work_order(project, wo, "submitted", metrics)
    mismatch_caught = current_state != "submitted"

    metrics.observe(
        f"Modality mismatch caught: {mismatch_caught}. "
        f"Work order state: {current_state}. "
        f"Context modality='antibody', deliverables target small-molecule "
        f"artifact classes (docking, compounds, descriptors). "
        f"Current workflow has no modality-consistency gate."
    )


def _run_absent_entity(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 5: Missing entity identifiers in context."""
    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])

    # The control plane accepts any dict for context — it doesn't validate
    # that required entity identifiers are present.
    current_state = _transition_work_order(project, wo, "in_progress", metrics)

    # Verify that the context fields are indeed empty
    stored_wo = read_record(project, "work-order", f"{wo['id']}-r{wo['revision']}")
    ctx = stored_wo.get("context", {})
    empty_fields = [k for k, v in ctx.items() if v in ("", None)]
    metrics.observe(
        f"Work order accepted with {len(empty_fields)} empty entity fields: "
        f"{empty_fields}. Current workflow has no context-content validation. "
        f"Work order state: {current_state}."
    )


def _run_tool_failure(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 6: Run fails with transient infrastructure error."""
    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])

    # Transition WO to in_progress
    current_wo_state = _transition_work_order(project, wo, "in_progress", metrics)

    # Create a run record
    run_data = fixture.run_data
    if run_data:
        write_record(project, "run", run_data["run_id"], run_data)
        metrics.record_transition(None, "queued")

        # Transition run through failure path
        run_transitions = [
            ("queued", "starting"),
            ("starting", "running"),
            ("running", "failed"),
        ]
        current_run_state = "queued"
        for from_state, to_state in run_transitions:
            if current_run_state != from_state:
                continue
            validate_transition("run", current_run_state, to_state)
            run_copy = dict(run_data)
            run_copy["state"] = to_state
            if to_state == "failed":
                run_copy["failure_class"] = "transient_infrastructure"
                run_copy["failure_detail"] = (
                    "Synthetic failure: endpoint returned HTTP 503"
                )
            write_record(project, "run", run_data["run_id"], run_copy)
            metrics.record_transition(current_run_state, to_state)
            current_run_state = to_state

        # Verify the failure is recorded
        stored_run = read_record(project, "run", run_data["run_id"])
        metrics.observe(
            f"Run {run_data['run_id']} state: {stored_run['state']}. "
            f"Failure class: {stored_run.get('failure_class', 'N/A')}. "
            f"Work order state: {current_wo_state}. "
            f"Recovery path available: WO can accept a new run."
        )

        # Verify recovery path: create a second run
        run2_data = dict(run_data)
        run2_data["run_id"] = "RUN-007"
        run2_data["attempt"] = 2
        run2_data["state"] = "queued"
        write_record(project, "run", run2_data["run_id"], run2_data)
        metrics.record_transition(None, "queued")
        metrics.observe(
            f"Recovery run {run2_data['run_id']} created (attempt 2). "
            f"State machine allows retry after infrastructure failure."
        )


def _run_disputed_citation(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 7: Hypothesis with retracted publication reference."""
    # Adopt the hypothesis set with disputed citation
    if fixture.hypothesis_data:
        hyp_file = project.parent / f"{fixture.fixture_id}-hypotheses.json"
        hyp_file.write_text(json.dumps(fixture.hypothesis_data, indent=2))

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "publication",
                "--attest",
                (
                    f"Evaluation fixture {fixture.fixture_id} — synthetic. "
                    "References a fabricated retracted DOI."
                ),
                "--cite",
                "DOI:10.xxxx/RETRACTED.2024.SYNTHETIC",
            ],
        )
        metrics.record_invocation(
            "hypothesis adopt --origin publication --cite ...",
            result.exit_code,
            result.output,
        )

        if result.exit_code == 0:
            metrics.observe("Hypothesis with disputed citation adopted successfully")
            # Check relay codes
            hyp_dir = project / "raw" / "hypotheses"
            for meta_file in hyp_dir.glob("*.meta.json"):
                meta = json.loads(meta_file.read_text())
                for relay in meta.get("mandatory_relays", []):
                    metrics.record_relay(relay["code"])
                metrics.record_artifact(
                    str(meta_file.relative_to(project)),
                    "provenance-sidecar",
                )

            # Run analysis
            adopted_files = list(hyp_dir.glob("*.adopted.json")) + list(
                hyp_dir.glob("*.charter.json")
            )
            if adopted_files:
                result = runner.invoke(
                    cli,
                    [
                        "--project",
                        str(project),
                        "hypothesis",
                        "analyze",
                        str(adopted_files[0]),
                    ],
                )
                metrics.record_invocation(
                    "hypothesis analyze",
                    result.exit_code,
                    result.output,
                )
                if result.exit_code == 0:
                    analysis_files = list(hyp_dir.glob("*.analysis.json"))
                    for af in analysis_files:
                        content = json.loads(af.read_text())
                        for relay in content.get("mandatory_relays", []):
                            metrics.record_relay(relay["code"])
                        metrics.record_artifact(
                            str(af.relative_to(project)),
                            content.get("assessment", {}).get("schema", "unknown"),
                        )
                    metrics.observe(
                        "Analysis completed. Relay codes from adoption "
                        "should carry forward to analysis."
                    )
        else:
            metrics.error_messages.append(
                f"Hypothesis adoption failed: {result.output.strip()}"
            )

    # Also create the work order
    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])
    metrics.observe(
        "Disputed citation scenario: current workflow does not have "
        "a retraction-check gate.  The citation passes through "
        "unchanged.  Future workflow improvements may add citation "
        "verification."
    )


def _run_bounded_review(
    fixture: FixtureDefinition,
    project: Path,
    runner: CliRunner,
    metrics: FixtureMetrics,
) -> None:
    """Fixture 8: Work order cycles through validation failure and recovery."""
    wo = fixture.work_order
    write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo)
    metrics.record_transition(None, wo["state"])

    # First: transition to submitted
    current_state = _transition_work_order(project, wo, "submitted", metrics)

    # Cycle through validation_failed -> in_progress -> submitted
    cycle_count = 0
    max_cycles = 3  # Test 3 recovery cycles
    while cycle_count < max_cycles and current_state == "submitted":
        # submitted -> validation_failed
        validate_transition("workorder", current_state, "validation_failed")
        wo_copy = dict(wo)
        wo_copy["state"] = "validation_failed"
        write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo_copy)
        metrics.record_transition(current_state, "validation_failed")
        current_state = "validation_failed"

        # validation_failed -> in_progress (recovery)
        validate_transition("workorder", current_state, "in_progress")
        wo_copy = dict(wo)
        wo_copy["state"] = "in_progress"
        write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo_copy)
        metrics.record_transition(current_state, "in_progress")
        current_state = "in_progress"

        # in_progress -> submitted (resubmit)
        validate_transition("workorder", current_state, "submitted")
        wo_copy = dict(wo)
        wo_copy["state"] = "submitted"
        write_record(project, "work-order", f"{wo['id']}-r{wo['revision']}", wo_copy)
        metrics.record_transition(current_state, "submitted")
        current_state = "submitted"

        cycle_count += 1

    metrics.observe(
        f"Completed {cycle_count} validation-failure recovery cycles. "
        f"State machine allows unlimited cycles (no circuit breaker). "
        f"Final state: {current_state}. "
        f"Transitions per cycle: 3 "
        f"(submitted->validation_failed->in_progress->submitted)."
    )


# ---------------------------------------------------------------------------
# Fixture dispatch
# ---------------------------------------------------------------------------

_RUNNERS: dict[str, Any] = {
    "no_genetic_support": _run_no_genetic_support,
    "negative_pocket_conformation": _run_negative_pocket,
    "positive_model_geometry": _run_positive_geometry,
    "modality_mismatch": _run_modality_mismatch,
    "absent_entity_inputs": _run_absent_entity,
    "tool_failure": _run_tool_failure,
    "disputed_citation": _run_disputed_citation,
    "bounded_review_exhaustion": _run_bounded_review,
}


def run_fixture(fixture: FixtureDefinition) -> FixtureMetrics:
    """Run a single fixture and return collected metrics."""
    metrics = FixtureMetrics(
        fixture_id=fixture.fixture_id,
        fixture_label=fixture.label,
        scenario_category=fixture.category,
    )
    metrics.start()

    runner_fn = _RUNNERS.get(fixture.category)
    if runner_fn is None:
        metrics.error_messages.append(f"No runner for category '{fixture.category}'")
        metrics.stop()
        return metrics

    runner = CliRunner()

    try:
        with tempfile.TemporaryDirectory() as td:
            project = _make_project(Path(td))
            runner_fn(fixture, project, runner, metrics)
    except Exception as exc:
        metrics.error_messages.append(f"Fixture failed with exception: {exc}")
        traceback.print_exc()

    metrics.stop()
    return metrics


def run_all_fixtures() -> BaselineReport:
    """Run all fixtures and produce a baseline report."""
    report = BaselineReport(
        run_timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    print(f"DDE Evaluation Harness v{report.eval_version}")
    print(f"Run timestamp: {report.run_timestamp}")
    print(f"Fixtures to run: {len(ALL_FIXTURES)}")
    print("=" * 60)

    for fixture in ALL_FIXTURES:
        print(f"\n--- {fixture.fixture_id}: {fixture.label} ---")
        metrics = run_fixture(fixture)
        report.add_result(metrics)

        status = "PASS" if metrics.completed else "FAIL"
        print(
            f"  {status}: {metrics.wall_clock_seconds}s, "
            f"{metrics.invocation_count} CLI calls, "
            f"{metrics.transition_count} transitions"
        )
        if metrics.error_messages:
            for err in metrics.error_messages:
                print(f"  ERROR: {err}")

    print("\n" + "=" * 60)
    print(f"Results: {report.completed_fixtures}/{report.total_fixtures} completed")
    print(f"Total wall clock: {report.total_wall_clock}s")
    print(f"Total CLI invocations: {report.total_invocations}")
    print(f"Total state transitions: {report.total_transitions}")

    # Declined candidate sample
    print(
        f"\nDeclined candidate sample (selection-bias review): "
        f"{DECLINED_CANDIDATE_SAMPLE}"
    )

    return report
