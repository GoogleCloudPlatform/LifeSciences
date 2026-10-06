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

"""Unit tests for the evaluation harness metric aggregation.

Covers:
  - FixtureMetrics.success_count / failure_count partitioning
  - FixtureMetrics.completed semantics (success path + error path)
  - FixtureMetrics.wall_clock_seconds with known start/end times
  - FixtureMetrics.repeated_operations tracking
  - BaselineReport.summary_metrics() denominator math
  - JSON serialize -> deserialize round-trip for BaselineReport

Run with:
    PYTHONPATH=tools python3 tests/test_eval_metrics.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ and DDE root to sys.path
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT))

from eval.metrics import BaselineReport, FixtureMetrics

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def _test(name: str):
    """Decorator that runs a test at decoration time."""

    def decorator(fn):
        try:
            fn()
            _results.append((name, True, ""))
            print(f"  PASS: {name}")
        except Exception as exc:
            _results.append((name, False, str(exc)))
            print(f"  FAIL: {name} -- {exc}")
            traceback.print_exc()
        return fn

    return decorator


# ---------------------------------------------------------------------------
# FixtureMetrics tests
# ---------------------------------------------------------------------------


@_test("success_count and failure_count partition correctly")
def test_success_failure_partition():
    m = FixtureMetrics(
        fixture_id="T1",
        fixture_label="Test",
        scenario_category="test",
    )
    m.record_invocation("cmd-a", exit_code=0)
    m.record_invocation("cmd-b", exit_code=2)
    m.record_invocation("cmd-c", exit_code=0)
    m.record_invocation("cmd-d", exit_code=9)
    m.record_invocation("cmd-e", exit_code=0)

    assert m.success_count == 3, f"Expected 3 successes, got {m.success_count}"
    assert m.failure_count == 2, f"Expected 2 failures, got {m.failure_count}"
    assert m.invocation_count == 5, f"Expected 5 invocations, got {m.invocation_count}"
    # Partition: success + failure = total
    assert m.success_count + m.failure_count == m.invocation_count


@_test("success_count zero when no invocations")
def test_success_count_zero():
    m = FixtureMetrics(
        fixture_id="T2",
        fixture_label="Test",
        scenario_category="test",
    )
    assert m.success_count == 0
    assert m.failure_count == 0
    assert m.invocation_count == 0


@_test("completed=True when stop() with no errors")
def test_completed_true_no_errors():
    m = FixtureMetrics(
        fixture_id="T3",
        fixture_label="Test",
        scenario_category="test",
    )
    m.start()
    m.stop()
    assert m.completed is True, f"Expected True, got {m.completed}"


@_test("completed=False when stop() after error")
def test_completed_false_with_errors():
    m = FixtureMetrics(
        fixture_id="T4",
        fixture_label="Test",
        scenario_category="test",
    )
    m.start()
    m.error_messages.append("Something went wrong")
    m.stop()
    assert m.completed is False, f"Expected False, got {m.completed}"


@_test("completed=False when stop() after multiple errors")
def test_completed_false_multiple_errors():
    m = FixtureMetrics(
        fixture_id="T5",
        fixture_label="Test",
        scenario_category="test",
    )
    m.start()
    m.error_messages.append("Error 1")
    m.error_messages.append("Error 2")
    m.stop()
    assert m.completed is False, f"Expected False, got {m.completed}"
    assert len(m.error_messages) == 2


@_test("completed=False initially (before stop)")
def test_completed_default_false():
    m = FixtureMetrics(
        fixture_id="T6",
        fixture_label="Test",
        scenario_category="test",
    )
    assert m.completed is False


@_test("wall_clock_seconds with known start/end times")
def test_wall_clock_known_times():
    m = FixtureMetrics(
        fixture_id="T7",
        fixture_label="Test",
        scenario_category="test",
    )
    m.start_time = 100.0
    m.end_time = 103.5
    assert m.wall_clock_seconds == 3.5, f"Expected 3.5, got {m.wall_clock_seconds}"


@_test("wall_clock_seconds zero when not started")
def test_wall_clock_zero():
    m = FixtureMetrics(
        fixture_id="T8",
        fixture_label="Test",
        scenario_category="test",
    )
    assert m.wall_clock_seconds == 0.0


@_test("wall_clock_seconds uses monotonic (positive after start/stop)")
def test_wall_clock_monotonic():
    m = FixtureMetrics(
        fixture_id="T9",
        fixture_label="Test",
        scenario_category="test",
    )
    m.start()
    time.sleep(0.005)
    m.stop()
    assert m.wall_clock_seconds > 0.0, f"Expected >0, got {m.wall_clock_seconds}"
    assert m.wall_clock_seconds < 1.0, f"Expected <1s, got {m.wall_clock_seconds}"


@_test("repeated_operations increments on duplicate commands")
def test_repeated_operations_tracking():
    m = FixtureMetrics(
        fixture_id="T10",
        fixture_label="Test",
        scenario_category="test",
    )
    m.record_invocation("hypothesis adopt", 0)
    assert m.repeated_operations == 0
    m.record_invocation("hypothesis analyze", 0)
    assert m.repeated_operations == 0
    m.record_invocation("hypothesis adopt", 0)  # duplicate
    assert m.repeated_operations == 1
    m.record_invocation("hypothesis adopt", 0)  # another duplicate
    assert m.repeated_operations == 2
    m.record_invocation("validate check WO-001", 0)
    assert m.repeated_operations == 2  # new command, no increment


@_test("repeated_operations zero when all commands unique")
def test_repeated_operations_all_unique():
    m = FixtureMetrics(
        fixture_id="T11",
        fixture_label="Test",
        scenario_category="test",
    )
    m.record_invocation("cmd-a", 0)
    m.record_invocation("cmd-b", 0)
    m.record_invocation("cmd-c", 0)
    assert m.repeated_operations == 0


@_test("record_transition stores from/to pairs")
def test_record_transition():
    m = FixtureMetrics(
        fixture_id="T12",
        fixture_label="Test",
        scenario_category="test",
    )
    m.record_transition(None, "proposed")
    m.record_transition("proposed", "committed")
    assert m.transition_count == 2
    assert m.state_transitions[0] == (None, "proposed")
    assert m.state_transitions[1] == ("proposed", "committed")


@_test("record_relay deduplicates")
def test_record_relay_dedup():
    m = FixtureMetrics(
        fixture_id="T13",
        fixture_label="Test",
        scenario_category="test",
    )
    m.record_relay("relay.one")
    m.record_relay("relay.two")
    m.record_relay("relay.one")  # duplicate
    assert len(m.relay_codes_fired) == 2
    assert "relay.one" in m.relay_codes_fired
    assert "relay.two" in m.relay_codes_fired


@_test("to_dict produces expected keys")
def test_to_dict_keys():
    m = FixtureMetrics(
        fixture_id="T14",
        fixture_label="Test Label",
        scenario_category="test_cat",
    )
    d = m.to_dict()
    expected_keys = {
        "fixture_id",
        "fixture_label",
        "scenario_category",
        "wall_clock_seconds",
        "state_transitions",
        "transition_count",
        "cli_invocations",
        "invocation_count",
        "success_count",
        "failure_count",
        "validation_checks",
        "relay_codes_fired",
        "artifacts_produced",
        "repeated_operations",
        "error_messages",
        "completed",
        "observations",
    }
    assert set(d.keys()) == expected_keys, (
        f"Key mismatch: missing={expected_keys - set(d.keys())}, "
        f"extra={set(d.keys()) - expected_keys}"
    )
    # Internal _seen_commands must NOT appear in serialized output
    assert "_seen_commands" not in d


# ---------------------------------------------------------------------------
# BaselineReport tests
# ---------------------------------------------------------------------------


def _make_fixture_metrics(
    fixture_id: str,
    n_success: int = 0,
    n_failure: int = 0,
    errors: list[str] | None = None,
    transitions: int = 0,
    relays: list[str] | None = None,
    artifacts: int = 0,
    repeated: int = 0,
    wall_clock: float = 0.1,
) -> FixtureMetrics:
    """Build a FixtureMetrics with known values for aggregation tests."""
    m = FixtureMetrics(
        fixture_id=fixture_id,
        fixture_label=f"Test {fixture_id}",
        scenario_category="test",
    )
    m.start_time = 100.0
    m.end_time = 100.0 + wall_clock

    for i in range(n_success):
        m.record_invocation(f"ok-{fixture_id}-{i}", 0)
    for i in range(n_failure):
        m.record_invocation(f"fail-{fixture_id}-{i}", 1)

    if errors:
        m.error_messages.extend(errors)

    for i in range(transitions):
        m.record_transition(f"state-{i}", f"state-{i + 1}")

    for code in relays or []:
        m.record_relay(code)

    for i in range(artifacts):
        m.record_artifact(f"path/{fixture_id}/{i}.json", "test-schema")

    m.repeated_operations = repeated
    m.completed = len(m.error_messages) == 0
    return m


@_test("summary_metrics fixtures_completed denominator")
def test_summary_fixtures_completed():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1"))
    report.add_result(_make_fixture_metrics("F2"))
    report.add_result(_make_fixture_metrics("F3", errors=["boom"]))

    summary = report.summary_metrics()
    fc = summary["fixtures_completed"]
    assert fc["value"] == 2, f"Expected 2 completed, got {fc['value']}"
    assert fc["denominator"] == 3, f"Expected denominator 3, got {fc['denominator']}"
    assert fc["rate"] == round(2 / 3, 4), f"Expected ~0.6667, got {fc['rate']}"


@_test("summary_metrics cli_success_rate denominator")
def test_summary_cli_success_rate():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1", n_success=3, n_failure=1))
    report.add_result(_make_fixture_metrics("F2", n_success=2, n_failure=0))

    summary = report.summary_metrics()
    sr = summary["cli_success_rate"]
    assert sr["value"] == 5, f"Expected 5 successes, got {sr['value']}"
    assert sr["denominator"] == 6, f"Expected denominator 6, got {sr['denominator']}"
    assert sr["rate"] == round(5 / 6, 4), f"Expected ~0.8333, got {sr['rate']}"


@_test("summary_metrics cli_failure_rate denominator")
def test_summary_cli_failure_rate():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1", n_success=1, n_failure=2))
    report.add_result(_make_fixture_metrics("F2", n_success=0, n_failure=1))

    summary = report.summary_metrics()
    fr = summary["cli_failure_rate"]
    assert fr["value"] == 3, f"Expected 3 failures, got {fr['value']}"
    assert fr["denominator"] == 4, f"Expected denominator 4, got {fr['denominator']}"
    assert fr["rate"] == 0.75, f"Expected 0.75, got {fr['rate']}"


@_test("summary_metrics repeated_operations denominator")
def test_summary_repeated_operations():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1", n_success=2, repeated=1))
    report.add_result(_make_fixture_metrics("F2", n_success=3, repeated=0))

    summary = report.summary_metrics()
    ro = summary["repeated_operations"]
    assert ro["value"] == 1, f"Expected 1 repeated, got {ro['value']}"
    assert ro["denominator"] == 5, f"Expected denominator 5, got {ro['denominator']}"
    assert ro["rate"] == 0.2, f"Expected 0.2, got {ro['rate']}"


@_test("summary_metrics with zero invocations avoids division by zero")
def test_summary_zero_invocations():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1"))

    summary = report.summary_metrics()
    assert summary["cli_success_rate"]["rate"] is None
    assert summary["cli_failure_rate"]["rate"] is None
    assert summary["repeated_operations"]["rate"] is None


@_test("summary_metrics N/A metrics have explanatory notes")
def test_summary_na_metrics():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1"))

    summary = report.summary_metrics()
    na_keys = [
        "unsupported_claims_accepted",
        "mistaken_rejections",
        "decision_reversals",
        "expensive_work_avoided",
    ]
    for key in na_keys:
        assert key in summary, f"Missing N/A metric: {key}"
        assert summary[key]["value"] == "N/A", f"{key} should be N/A"
        assert "note" in summary[key], f"{key} should have a note"
        assert len(summary[key]["note"]) > 10, f"{key} note too short"


@_test("total_wall_clock sums per-fixture times")
def test_total_wall_clock():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1", wall_clock=1.5))
    report.add_result(_make_fixture_metrics("F2", wall_clock=2.3))
    report.add_result(_make_fixture_metrics("F3", wall_clock=0.7))

    assert report.total_wall_clock == 4.5, (
        f"Expected 4.5, got {report.total_wall_clock}"
    )


@_test("total_transitions sums per-fixture transitions")
def test_total_transitions():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1", transitions=5))
    report.add_result(_make_fixture_metrics("F2", transitions=3))

    assert report.total_transitions == 8, f"Expected 8, got {report.total_transitions}"


@_test("total_artifacts sums per-fixture artifacts")
def test_total_artifacts():
    report = BaselineReport(run_timestamp="2026-09-08T00:00:00Z")
    report.add_result(_make_fixture_metrics("F1", artifacts=2))
    report.add_result(_make_fixture_metrics("F2", artifacts=3))

    assert report.total_artifacts == 5, f"Expected 5, got {report.total_artifacts}"


@_test("JSON round-trip preserves report structure")
def test_json_round_trip():
    report = BaselineReport(run_timestamp="2026-09-08T12:00:00Z")
    report.add_result(
        _make_fixture_metrics(
            "F1",
            n_success=2,
            n_failure=1,
            transitions=5,
            relays=["relay.a"],
            artifacts=3,
            wall_clock=1.5,
        )
    )
    report.add_result(
        _make_fixture_metrics(
            "F2",
            n_success=1,
            errors=["test error"],
            transitions=3,
            wall_clock=0.8,
        )
    )

    # Serialize
    d = report.to_dict()
    json_str = json.dumps(d, indent=2)

    # Deserialize
    parsed = json.loads(json_str)

    # Verify structure
    assert parsed["eval_version"] == "1.0"
    assert parsed["run_timestamp"] == "2026-09-08T12:00:00Z"
    assert len(parsed["fixture_results"]) == 2

    # Verify first fixture
    f1 = parsed["fixture_results"][0]
    assert f1["fixture_id"] == "F1"
    assert f1["success_count"] == 2
    assert f1["failure_count"] == 1
    assert f1["completed"] is True
    assert f1["transition_count"] == 5
    assert f1["relay_codes_fired"] == ["relay.a"]
    assert len(f1["artifacts_produced"]) == 3
    assert f1["wall_clock_seconds"] == 1.5

    # Verify second fixture (has errors → not completed)
    f2 = parsed["fixture_results"][1]
    assert f2["fixture_id"] == "F2"
    assert f2["completed"] is False
    assert f2["error_messages"] == ["test error"]

    # Verify summary
    summary = parsed["summary"]
    assert summary["fixtures_completed"]["value"] == 1
    assert summary["fixtures_completed"]["denominator"] == 2


@_test("JSON write/read round-trip via file")
def test_json_file_round_trip():
    report = BaselineReport(run_timestamp="2026-09-08T12:00:00Z")
    report.add_result(_make_fixture_metrics("F1", n_success=3, transitions=4))

    with tempfile.TemporaryDirectory() as td:
        json_path = Path(td) / "test-report.json"
        report.write_json(json_path)

        assert json_path.is_file(), "JSON file not created"
        loaded = json.loads(json_path.read_text())
        assert loaded["eval_version"] == "1.0"
        assert len(loaded["fixture_results"]) == 1
        assert loaded["fixture_results"][0]["success_count"] == 3


@_test("Markdown report writes without error")
def test_markdown_report_writes():
    report = BaselineReport(run_timestamp="2026-09-08T12:00:00Z")
    report.add_result(
        _make_fixture_metrics(
            "F1",
            n_success=2,
            transitions=5,
            wall_clock=0.5,
        )
    )

    with tempfile.TemporaryDirectory() as td:
        md_path = Path(td) / "test-report.md"
        report.write_markdown(md_path)

        assert md_path.is_file(), "Markdown file not created"
        content = md_path.read_text()
        assert "# DDE Evaluation Baseline Report" in content
        assert "Aggregate Metrics" in content
        assert "Per-Fixture Results" in content
        assert "Regression Criteria" in content
        assert "Resource Budget" in content
        assert "Provenance" in content


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"\n{'=' * 60}")
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        print("\nFailed tests:")
        for name, ok, msg in _results:
            if not ok:
                print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
