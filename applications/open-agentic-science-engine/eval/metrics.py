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

"""Measurement collection for the DDE evaluation harness.

Every rate metric carries an explicit denominator per issue #82
acceptance criteria.  No metric is a bare count without context.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FixtureMetrics:
    """Measurements collected for a single fixture run."""

    fixture_id: str
    fixture_label: str
    scenario_category: str  # e.g. "no_genetic_support", "tool_failure"

    # Timing
    start_time: float = 0.0
    end_time: float = 0.0

    # State transitions recorded as (from_state, to_state) pairs
    state_transitions: list[tuple[str | None, str]] = field(default_factory=list)

    # CLI invocations: (command, exit_code)
    cli_invocations: list[dict[str, Any]] = field(default_factory=list)

    # Validation check results: check_name -> result
    validation_checks: dict[str, str] = field(default_factory=dict)

    # Relay codes fired during the fixture
    relay_codes_fired: list[str] = field(default_factory=list)

    # Artifacts produced: list of (path_relative, schema)
    artifacts_produced: list[dict[str, str]] = field(default_factory=list)

    # Repeated operations (same command run >1 time within this fixture).
    # Tracked by _seen_commands; incremented in record_invocation().
    repeated_operations: int = 0

    # Internal: command strings already seen (for repeat detection)
    _seen_commands: set[str] = field(default_factory=set, repr=False)

    # Error messages collected
    error_messages: list[str] = field(default_factory=list)

    # Whether the fixture completed without unexpected errors
    completed: bool = False

    # Free-form notes about what was observed
    observations: list[str] = field(default_factory=list)

    @property
    def wall_clock_seconds(self) -> float:
        if self.end_time and self.start_time:
            return round(self.end_time - self.start_time, 4)
        return 0.0

    @property
    def transition_count(self) -> int:
        return len(self.state_transitions)

    @property
    def invocation_count(self) -> int:
        return len(self.cli_invocations)

    @property
    def success_count(self) -> int:
        return sum(1 for inv in self.cli_invocations if inv.get("exit_code") == 0)

    @property
    def failure_count(self) -> int:
        return sum(1 for inv in self.cli_invocations if inv.get("exit_code") != 0)

    def start(self) -> None:
        self.start_time = time.monotonic()

    def stop(self) -> None:
        self.end_time = time.monotonic()
        self.completed = len(self.error_messages) == 0

    def record_invocation(
        self,
        command: str,
        exit_code: int,
        output: str = "",
    ) -> None:
        if command in self._seen_commands:
            self.repeated_operations += 1
        self._seen_commands.add(command)
        self.cli_invocations.append(
            {
                "command": command,
                "exit_code": exit_code,
                "output_length": len(output),
            }
        )

    def record_transition(self, from_state: str | None, to_state: str) -> None:
        self.state_transitions.append((from_state, to_state))

    def record_validation(self, check_name: str, result: str) -> None:
        self.validation_checks[check_name] = result

    def record_relay(self, code: str) -> None:
        if code not in self.relay_codes_fired:
            self.relay_codes_fired.append(code)

    def record_artifact(self, path: str, schema: str = "unknown") -> None:
        self.artifacts_produced.append({"path": path, "schema": schema})

    def observe(self, note: str) -> None:
        self.observations.append(note)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "fixture_label": self.fixture_label,
            "scenario_category": self.scenario_category,
            "wall_clock_seconds": self.wall_clock_seconds,
            "state_transitions": [
                {"from": f, "to": t} for f, t in self.state_transitions
            ],
            "transition_count": self.transition_count,
            "cli_invocations": self.cli_invocations,
            "invocation_count": self.invocation_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "validation_checks": self.validation_checks,
            "relay_codes_fired": self.relay_codes_fired,
            "artifacts_produced": self.artifacts_produced,
            "repeated_operations": self.repeated_operations,
            "error_messages": self.error_messages,
            "completed": self.completed,
            "observations": self.observations,
        }


@dataclass
class BaselineReport:
    """Aggregated baseline report across all fixture runs."""

    eval_version: str = "1.0"
    run_timestamp: str = ""
    fixture_results: list[FixtureMetrics] = field(default_factory=list)

    def add_result(self, metrics: FixtureMetrics) -> None:
        self.fixture_results.append(metrics)

    @property
    def total_fixtures(self) -> int:
        return len(self.fixture_results)

    @property
    def completed_fixtures(self) -> int:
        return sum(1 for r in self.fixture_results if r.completed)

    @property
    def total_invocations(self) -> int:
        return sum(r.invocation_count for r in self.fixture_results)

    @property
    def total_successes(self) -> int:
        return sum(r.success_count for r in self.fixture_results)

    @property
    def total_failures(self) -> int:
        return sum(r.failure_count for r in self.fixture_results)

    @property
    def total_transitions(self) -> int:
        return sum(r.transition_count for r in self.fixture_results)

    @property
    def total_relays(self) -> int:
        return sum(len(r.relay_codes_fired) for r in self.fixture_results)

    @property
    def total_artifacts(self) -> int:
        return sum(len(r.artifacts_produced) for r in self.fixture_results)

    @property
    def total_wall_clock(self) -> float:
        return round(sum(r.wall_clock_seconds for r in self.fixture_results), 4)

    @property
    def total_repeated_operations(self) -> int:
        return sum(r.repeated_operations for r in self.fixture_results)

    def summary_metrics(self) -> dict[str, Any]:
        """Return aggregate metrics with explicit denominators."""
        return {
            "fixtures_completed": {
                "value": self.completed_fixtures,
                "denominator": self.total_fixtures,
                "rate": (
                    round(self.completed_fixtures / self.total_fixtures, 4)
                    if self.total_fixtures > 0
                    else None
                ),
            },
            "cli_success_rate": {
                "value": self.total_successes,
                "denominator": self.total_invocations,
                "rate": (
                    round(self.total_successes / self.total_invocations, 4)
                    if self.total_invocations > 0
                    else None
                ),
            },
            "cli_failure_rate": {
                "value": self.total_failures,
                "denominator": self.total_invocations,
                "rate": (
                    round(self.total_failures / self.total_invocations, 4)
                    if self.total_invocations > 0
                    else None
                ),
            },
            "total_state_transitions": self.total_transitions,
            "total_relay_codes_fired": self.total_relays,
            "total_artifacts_produced": self.total_artifacts,
            "total_wall_clock_seconds": self.total_wall_clock,
            "repeated_operations": {
                "value": self.total_repeated_operations,
                "denominator": self.total_invocations,
                "rate": (
                    round(self.total_repeated_operations / self.total_invocations, 4)
                    if self.total_invocations > 0
                    else None
                ),
            },
            # Metrics that require full workflow instrumentation (LLM agents
            # making decisions) — recorded as N/A for baseline.
            "unsupported_claims_accepted": {
                "value": "N/A",
                "denominator": "N/A",
                "note": "Requires LLM agent workflow execution with scientific review",
            },
            "mistaken_rejections": {
                "value": "N/A",
                "denominator": "N/A",
                "note": "Requires LLM agent workflow execution with review of declined candidates",
            },
            "decision_reversals": {
                "value": "N/A",
                "denominator": "N/A",
                "note": "Requires multi-cycle workflow execution tracking",
            },
            "expensive_work_avoided": {
                "value": "N/A",
                "denominator": "N/A",
                "note": "Requires cost-instrumented workflow with early-termination tracking",
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_version": self.eval_version,
            "run_timestamp": self.run_timestamp,
            "summary": self.summary_metrics(),
            "fixture_results": [r.to_dict() for r in self.fixture_results],
        }

    def write_json(self, path: Path) -> Path:
        """Write the full report as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def write_markdown(self, path: Path) -> Path:
        """Write a human-readable markdown summary."""
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.summary_metrics()
        lines: list[str] = []
        lines.append("# DDE Evaluation Baseline Report")
        lines.append("")
        lines.append(f"**Evaluation version**: {self.eval_version}")
        lines.append(f"**Run timestamp**: {self.run_timestamp}")
        lines.append(f"**Total fixtures**: {self.total_fixtures}")
        lines.append(f"**Completed**: {self.completed_fixtures}/{self.total_fixtures}")
        lines.append("")

        # Summary table
        lines.append("## Aggregate Metrics")
        lines.append("")
        lines.append("| Metric | Value | Denominator | Rate |")
        lines.append("|--------|-------|-------------|------|")
        for key, val in summary.items():
            if isinstance(val, dict):
                v = val.get("value", "N/A")
                d = val.get("denominator", "N/A")
                r = val.get("rate", "N/A")
                note = val.get("note", "")
                if note:
                    lines.append(f"| {key} | {v} | {d} | {note} |")
                else:
                    lines.append(f"| {key} | {v} | {d} | {r} |")
            else:
                lines.append(f"| {key} | {val} | - | - |")
        lines.append("")

        # Per-fixture results
        lines.append("## Per-Fixture Results")
        lines.append("")
        for result in self.fixture_results:
            lines.append(f"### {result.fixture_label}")
            lines.append("")
            lines.append(f"- **ID**: {result.fixture_id}")
            lines.append(f"- **Category**: {result.scenario_category}")
            lines.append(f"- **Completed**: {result.completed}")
            lines.append(f"- **Wall clock**: {result.wall_clock_seconds}s")
            lines.append(
                f"- **CLI invocations**: {result.invocation_count} "
                f"({result.success_count} ok, {result.failure_count} failed)"
            )
            lines.append(f"- **State transitions**: {result.transition_count}")
            lines.append(
                f"- **Relay codes**: {', '.join(result.relay_codes_fired) or 'none'}"
            )
            lines.append(f"- **Artifacts produced**: {len(result.artifacts_produced)}")
            lines.append(f"- **Repeated operations**: {result.repeated_operations}")

            if result.validation_checks:
                lines.append("")
                lines.append("**Validation checks**:")
                lines.append("")
                lines.append("| Check | Result |")
                lines.append("|-------|--------|")
                for check, res in result.validation_checks.items():
                    lines.append(f"| {check} | {res} |")

            if result.error_messages:
                lines.append("")
                lines.append("**Errors**:")
                for err in result.error_messages:
                    lines.append(f"- {err}")

            if result.observations:
                lines.append("")
                lines.append("**Observations**:")
                for obs in result.observations:
                    lines.append(f"- {obs}")

            lines.append("")

        # Regression criteria
        lines.append("## Regression Criteria")
        lines.append("")
        lines.append(
            "This baseline establishes the following measurable properties "
            "of the current workflow. Future workflow changes (tracker #73) "
            "must not regress these without explicit justification:"
        )
        lines.append("")
        lines.append(
            "1. **Fixture completion rate**: "
            f"{self.completed_fixtures}/{self.total_fixtures} fixtures complete without unexpected errors"
        )
        lines.append(
            "2. **CLI reliability**: "
            f"{self.total_successes}/{self.total_invocations} expected CLI invocations succeed"
        )
        lines.append(
            "3. **State machine integrity**: All state transitions follow "
            "the declared transition graph; illegal transitions produce exit 9"
        )
        lines.append(
            "4. **Relay propagation**: Mandatory relays fire when conditions are met "
            "and carry forward through analysis"
        )
        lines.append(
            "5. **Validation mechanical checks**: Validation catches missing provenance, "
            "absent deliverables, and schema violations"
        )
        lines.append("")

        # Resource budget
        lines.append("## Resource Budget (Evaluation Policy v1.0)")
        lines.append("")
        lines.append("| Resource | Budget | Baseline Actual |")
        lines.append("|----------|--------|-----------------|")
        lines.append(f"| Wall clock (all fixtures) | 60s | {self.total_wall_clock}s |")
        lines.append(f"| CLI invocations | 200 | {self.total_invocations} |")
        lines.append(f"| State transitions | 100 | {self.total_transitions} |")
        lines.append("")

        # Provenance
        lines.append("## Provenance")
        lines.append("")
        lines.append(
            "All fixtures are synthetic, clearly labeled as such in their definitions."
        )
        lines.append("No real program data or patient data is used.")
        lines.append(
            "Fixture provenance is documented in "
            "`applications/DDE/eval/fixtures/definitions.py`."
        )
        lines.append("")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
