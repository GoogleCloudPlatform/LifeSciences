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

"""Comparison report generation — baseline vs. Stage 0 workflow.

Reads the existing baseline report (Phase 1 output, frozen) and the
newly generated Stage 0 report, produces a side-by-side comparison with
explicit attention to:

  - Which metrics changed and by how much
  - Which metrics remain N/A and why (honest about what is not measurable)
  - Declined candidate handling differences (EVAL-001, EVAL-002)
  - Scope limitations (#77 evidence reuse is out of scope)
  - Resource budget characteristics of Stage 0

No fabricated numbers — every metric traces to an actual measurement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fixtures.definitions import DECLINED_CANDIDATE_SAMPLE
from .metrics import BaselineReport

# ---------------------------------------------------------------------------
# Comparison data structures
# ---------------------------------------------------------------------------


@dataclass
class MetricComparison:
    """Side-by-side comparison of a single metric."""

    metric_name: str
    baseline_value: Any
    stage0_value: Any
    baseline_denominator: Any = None
    stage0_denominator: Any = None
    baseline_rate: Any = None
    stage0_rate: Any = None
    baseline_note: str = ""
    stage0_note: str = ""
    comparison_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name,
            "baseline": {
                "value": self.baseline_value,
                "denominator": self.baseline_denominator,
                "rate": self.baseline_rate,
                "note": self.baseline_note,
            },
            "stage0": {
                "value": self.stage0_value,
                "denominator": self.stage0_denominator,
                "rate": self.stage0_rate,
                "note": self.stage0_note,
            },
            "comparison_note": self.comparison_note,
        }


@dataclass
class FixtureComparison:
    """Per-fixture comparison between baseline and Stage 0."""

    fixture_id: str
    fixture_label: str
    category: str
    baseline_completed: bool
    stage0_completed: bool
    baseline_observations: list[str] = field(default_factory=list)
    stage0_observations: list[str] = field(default_factory=list)
    baseline_wall_clock: float = 0.0
    stage0_wall_clock: float = 0.0
    baseline_invocations: int = 0
    stage0_invocations: int = 0
    baseline_transitions: int = 0
    stage0_transitions: int = 0
    behavioral_differences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "fixture_label": self.fixture_label,
            "category": self.category,
            "baseline_completed": self.baseline_completed,
            "stage0_completed": self.stage0_completed,
            "baseline_wall_clock": self.baseline_wall_clock,
            "stage0_wall_clock": self.stage0_wall_clock,
            "baseline_invocations": self.baseline_invocations,
            "stage0_invocations": self.stage0_invocations,
            "baseline_transitions": self.baseline_transitions,
            "stage0_transitions": self.stage0_transitions,
            "baseline_observations": self.baseline_observations,
            "stage0_observations": self.stage0_observations,
            "behavioral_differences": self.behavioral_differences,
        }


@dataclass
class DeclinedCandidateComparison:
    """Comparison of baseline vs. Stage 0 handling of declined candidates."""

    fixture_id: str
    baseline_observations: list[str] = field(default_factory=list)
    stage0_observations: list[str] = field(default_factory=list)
    stage0_disposition: str = ""
    stage0_evidence_statuses: list[str] = field(default_factory=list)
    comparison_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "baseline_observations": self.baseline_observations,
            "stage0_observations": self.stage0_observations,
            "stage0_disposition": self.stage0_disposition,
            "stage0_evidence_statuses": self.stage0_evidence_statuses,
            "comparison_note": self.comparison_note,
        }


@dataclass
class ComparisonReport:
    """Full comparison report between baseline and Stage 0."""

    eval_version: str = "1.0-comparison"
    baseline_timestamp: str = ""
    stage0_timestamp: str = ""
    comparison_timestamp: str = ""

    scope_notes: list[str] = field(default_factory=list)
    metric_comparisons: list[MetricComparison] = field(default_factory=list)
    fixture_comparisons: list[FixtureComparison] = field(default_factory=list)
    declined_comparisons: list[DeclinedCandidateComparison] = field(
        default_factory=list
    )
    stage0_resource_characteristics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_version": self.eval_version,
            "baseline_timestamp": self.baseline_timestamp,
            "stage0_timestamp": self.stage0_timestamp,
            "comparison_timestamp": self.comparison_timestamp,
            "scope_and_limitations": self.scope_notes,
            "metric_comparisons": [m.to_dict() for m in self.metric_comparisons],
            "fixture_comparisons": [f.to_dict() for f in self.fixture_comparisons],
            "declined_candidate_comparisons": [
                d.to_dict() for d in self.declined_comparisons
            ],
            "stage0_resource_characteristics": (self.stage0_resource_characteristics),
        }

    def write_json(self, path: Path) -> Path:
        """Write the comparison report as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def write_markdown(self, path: Path) -> Path:
        """Write a human-readable markdown comparison report."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []

        lines.append("# DDE Evaluation Comparison Report — Baseline vs. Stage 0")
        lines.append("")
        lines.append(f"**Evaluation version**: {self.eval_version}")
        lines.append(f"**Baseline run**: {self.baseline_timestamp}")
        lines.append(f"**Stage 0 run**: {self.stage0_timestamp}")
        lines.append(f"**Comparison generated**: {self.comparison_timestamp}")
        lines.append("")

        # ---- Scope and limitations ----
        lines.append("## Scope and Limitations")
        lines.append("")
        for note in self.scope_notes:
            lines.append(f"- {note}")
        lines.append("")

        # ---- Aggregate metrics comparison ----
        lines.append("## Aggregate Metrics Comparison")
        lines.append("")
        lines.append("| Metric | Baseline | Stage 0 | Note |")
        lines.append("|--------|----------|---------|------|")
        for mc in self.metric_comparisons:
            b_display = _format_metric_cell(
                mc.baseline_value,
                mc.baseline_denominator,
                mc.baseline_rate,
                mc.baseline_note,
            )
            s_display = _format_metric_cell(
                mc.stage0_value,
                mc.stage0_denominator,
                mc.stage0_rate,
                mc.stage0_note,
            )
            lines.append(
                f"| {mc.metric_name} | {b_display} | "
                f"{s_display} | {mc.comparison_note} |"
            )
        lines.append("")

        # ---- Per-fixture comparison ----
        lines.append("## Per-Fixture Comparison")
        lines.append("")
        for fc in self.fixture_comparisons:
            lines.append(f"### {fc.fixture_id}: {fc.fixture_label}")
            lines.append("")
            lines.append(f"- **Category**: {fc.category}")
            lines.append(
                f"- **Completed**: baseline={fc.baseline_completed}, "
                f"Stage 0={fc.stage0_completed}"
            )
            lines.append(
                f"- **Wall clock**: baseline {fc.baseline_wall_clock}s, "
                f"Stage 0 {fc.stage0_wall_clock}s"
            )
            lines.append(
                f"- **Invocations**: baseline {fc.baseline_invocations}, "
                f"Stage 0 {fc.stage0_invocations}"
            )
            lines.append(
                f"- **State transitions**: "
                f"baseline {fc.baseline_transitions}, "
                f"Stage 0 {fc.stage0_transitions}"
            )

            if fc.behavioral_differences:
                lines.append("")
                lines.append("**Behavioral differences**:")
                for diff in fc.behavioral_differences:
                    lines.append(f"- {diff}")

            if fc.stage0_observations:
                lines.append("")
                lines.append("**Stage 0 observations**:")
                for obs in fc.stage0_observations:
                    lines.append(f"- {obs}")

            lines.append("")

        # ---- Declined candidate comparison ----
        lines.append("## Declined Candidate Follow-up")
        lines.append("")
        lines.append(
            "The following fixtures represent concepts likely to be "
            "declined in a real workflow.  This comparison checks whether "
            "Stage 0's handling of these candidates differs from the "
            "baseline's, exposing potential selection bias."
        )
        lines.append("")
        for dc in self.declined_comparisons:
            lines.append(f"### {dc.fixture_id}")
            lines.append("")
            lines.append(f"**Stage 0 disposition**: {dc.stage0_disposition}")
            if dc.stage0_evidence_statuses:
                lines.append(
                    "**Evidence statuses**: " + ", ".join(dc.stage0_evidence_statuses)
                )
            lines.append("")
            lines.append(f"**Comparison**: {dc.comparison_note}")
            lines.append("")

        # ---- Resource budget ----
        lines.append("## Resource Budget — Stage 0 Characteristics")
        lines.append("")
        lines.append(
            "Stage 0 dispatches workstreams per concept, subject to "
            "budget controls.  The following characteristics were "
            "observed during this comparison run — recorded as "
            "versioned policy per #82 acceptance criteria, not as "
            "assumed constants."
        )
        lines.append("")
        if self.stage0_resource_characteristics:
            lines.append("| Characteristic | Value |")
            lines.append("|----------------|-------|")
            for key, val in self.stage0_resource_characteristics.items():
                lines.append(f"| {key} | {val} |")
        lines.append("")

        # ---- Regression criteria ----
        lines.append("## Regression Criteria")
        lines.append("")
        lines.append(
            "Stage 0 must not regress the baseline properties established in Phase 1:"
        )
        lines.append("")
        for mc in self.metric_comparisons:
            if mc.comparison_note and "regression" in (mc.comparison_note.lower()):
                lines.append(f"- **{mc.metric_name}**: {mc.comparison_note}")
        lines.append("- All evaluation fixtures complete without unexpected errors")
        lines.append("- Stage 0 workstreams invoke real CLI commands, not mocks")
        lines.append("")

        # ---- Provenance ----
        lines.append("## Provenance")
        lines.append("")
        lines.append(
            "All fixtures are synthetic, clearly labeled as such in "
            "their definitions.  No real program data or patient data "
            "is used."
        )
        lines.append(
            "Fixture provenance is documented in "
            "`applications/DDE/eval/fixtures/definitions.py`."
        )
        lines.append(
            "Baseline data is from "
            "`applications/DDE/eval/baseline/baseline-report.json` "
            "(Phase 1 output, frozen — not regenerated or modified)."
        )
        lines.append("")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _format_metric_cell(
    value: Any,
    denominator: Any,
    rate: Any,
    note: str,
) -> str:
    """Format a metric value for a markdown table cell."""
    if note:
        return f"{value} — {note}"
    if rate is not None and rate != "N/A" and denominator is not None:
        return f"{value}/{denominator} ({rate})"
    return str(value)


# ---------------------------------------------------------------------------
# Report loading
# ---------------------------------------------------------------------------


def load_baseline_report(path: Path) -> dict[str, Any]:
    """Load the frozen baseline report JSON.

    This reads the Phase 1 baseline output without modifying it.
    """
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Comparison generation
# ---------------------------------------------------------------------------


def _compare_metric(
    name: str,
    baseline_summary: dict[str, Any],
    stage0_summary: dict[str, Any],
) -> MetricComparison:
    """Compare a single metric between baseline and Stage 0 summaries."""
    b = baseline_summary.get(name, {})
    s = stage0_summary.get(name, {})

    if isinstance(b, dict):
        b_val = b.get("value", "N/A")
        b_denom = b.get("denominator")
        b_rate = b.get("rate")
        b_note = b.get("note", "")
    else:
        b_val, b_denom, b_rate, b_note = b, None, None, ""

    if isinstance(s, dict):
        s_val = s.get("value", "N/A")
        s_denom = s.get("denominator")
        s_rate = s.get("rate")
        s_note = s.get("note", "")
    else:
        s_val, s_denom, s_rate, s_note = s, None, None, ""

    comparison_note = _build_comparison_note(name, b_val, s_val)

    return MetricComparison(
        metric_name=name,
        baseline_value=b_val,
        stage0_value=s_val,
        baseline_denominator=b_denom,
        stage0_denominator=s_denom,
        baseline_rate=b_rate,
        stage0_rate=s_rate,
        baseline_note=b_note,
        stage0_note=s_note,
        comparison_note=comparison_note,
    )


def _build_comparison_note(
    name: str,
    b_val: Any,
    s_val: Any,
) -> str:
    """Build a human-readable comparison note for a metric."""
    if b_val == "N/A" and s_val == "N/A":
        return (
            "Both N/A — requires LLM agent workflow execution, "
            "not measured by this control-plane harness"
        )
    if isinstance(b_val, (int, float)) and isinstance(s_val, (int, float)):
        if b_val == s_val:
            return "No change"
        delta = s_val - b_val
        note = f"Delta: {delta:+g}"
        if name == "fixtures_completed" and s_val < b_val:
            note += " — REGRESSION: fewer fixtures completed"
        return note
    return ""


def generate_comparison(
    baseline_data: dict[str, Any],
    stage0_report: BaselineReport,
) -> ComparisonReport:
    """Generate a comparison report from baseline data and Stage 0 results.

    Parameters
    ----------
    baseline_data:
        The frozen baseline report loaded via ``load_baseline_report()``.
    stage0_report:
        The Stage 0 report produced by ``run_all_fixtures_stage0()``.

    Returns
    -------
    ComparisonReport
        Complete comparison with metric deltas, per-fixture analysis,
        declined-candidate follow-up, and scope documentation.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stage0_summary = stage0_report.summary_metrics()
    baseline_summary = baseline_data.get("summary", {})

    report = ComparisonReport(
        baseline_timestamp=baseline_data.get("run_timestamp", "unknown"),
        stage0_timestamp=stage0_report.run_timestamp,
        comparison_timestamp=now,
    )

    # ---- Scope notes ----
    report.scope_notes = [
        (
            "This comparison covers baseline (pre-Stage 0) vs. Stage 0 "
            "bounded triage workflows only."
        ),
        (
            "Evidence reuse (#77) is P2 and explicitly out of scope for "
            "this tracker phase — it has not been implemented and is not "
            "measured in this comparison."
        ),
        (
            "#82's acceptance criteria requests comparison with 'bounded "
            "Stage 0 and evidence reuse.'  Only the bounded Stage 0 "
            "portion is covered here.  The evidence-reuse comparison "
            "will be addressed when #77 is implemented in a future "
            "tracker phase."
        ),
        (
            "This harness exercises the control-plane and CLI layers.  "
            "The four LLM-dependent metrics (unsupported_claims_accepted, "
            "mistaken_rejections, decision_reversals, "
            "expensive_work_avoided) remain N/A in both workflows "
            "because this harness does not include a live LLM agent in "
            "the loop.  These metrics require full workflow execution "
            "with scientific review, which this evaluation harness does "
            "not provide."
        ),
        (
            "Stage 0 workstreams invoke real CLI commands "
            "(dde manufacturing assess-stage0, etc.) — no mocked results."
        ),
    ]

    # ---- Metric comparisons ----
    metric_names = [
        "fixtures_completed",
        "cli_success_rate",
        "cli_failure_rate",
        "total_state_transitions",
        "total_relay_codes_fired",
        "total_artifacts_produced",
        "total_wall_clock_seconds",
        "repeated_operations",
        "unsupported_claims_accepted",
        "mistaken_rejections",
        "decision_reversals",
        "expensive_work_avoided",
    ]
    for name in metric_names:
        report.metric_comparisons.append(
            _compare_metric(name, baseline_summary, stage0_summary)
        )

    # ---- Per-fixture comparisons ----
    baseline_fixtures = {
        r["fixture_id"]: r for r in baseline_data.get("fixture_results", [])
    }
    for stage0_result in stage0_report.fixture_results:
        fid = stage0_result.fixture_id
        bl = baseline_fixtures.get(fid, {})

        fc = FixtureComparison(
            fixture_id=fid,
            fixture_label=stage0_result.fixture_label,
            category=stage0_result.scenario_category,
            baseline_completed=bl.get("completed", False),
            stage0_completed=stage0_result.completed,
            baseline_observations=bl.get("observations", []),
            stage0_observations=stage0_result.observations,
            baseline_wall_clock=bl.get("wall_clock_seconds", 0),
            stage0_wall_clock=stage0_result.wall_clock_seconds,
            baseline_invocations=bl.get("invocation_count", 0),
            stage0_invocations=stage0_result.invocation_count,
            baseline_transitions=bl.get("transition_count", 0),
            stage0_transitions=stage0_result.transition_count,
        )

        # Identify behavioral differences.
        if fc.baseline_completed != fc.stage0_completed:
            fc.behavioral_differences.append(
                f"Completion status changed: "
                f"baseline={fc.baseline_completed}, "
                f"Stage 0={fc.stage0_completed}"
            )

        # Stage 0 adds triage-specific behaviour.
        triage_obs = [
            o
            for o in stage0_result.observations
            if o.startswith("Stage 0 disposition:") or o.startswith("[manufacturing]")
        ]
        if triage_obs:
            fc.behavioral_differences.append(
                "Stage 0 adds triage evaluation with manufacturing "
                "feasibility assessment (not present in baseline)"
            )

        # Note structural differences in approach.
        if bl.get("invocation_count", 0) > 0 and (stage0_result.invocation_count > 0):
            fc.behavioral_differences.append(
                "Different CLI command paths: baseline uses "
                "control-plane commands (hypothesis adopt, validate "
                "check); Stage 0 uses triage workstream commands "
                "(manufacturing assess-stage0)"
            )

        report.fixture_comparisons.append(fc)

    # ---- Declined candidate comparisons ----
    for declined_id in DECLINED_CANDIDATE_SAMPLE:
        bl = baseline_fixtures.get(declined_id, {})
        stage0_match = None
        for sr in stage0_report.fixture_results:
            if sr.fixture_id == declined_id:
                stage0_match = sr
                break

        dc = DeclinedCandidateComparison(
            fixture_id=declined_id,
            baseline_observations=bl.get("observations", []),
        )

        if stage0_match:
            dc.stage0_observations = stage0_match.observations

            # Extract disposition and evidence statuses.
            for obs in stage0_match.observations:
                if obs.startswith("Stage 0 disposition:"):
                    dc.stage0_disposition = obs.replace("Stage 0 disposition: ", "")
                if "evidence_status=" in obs:
                    status = obs.split("evidence_status=")[1].split(",")[0]
                    dc.stage0_evidence_statuses.append(status)

            dc.comparison_note = _build_declined_note(
                declined_id,
                dc.stage0_disposition,
            )
        else:
            dc.comparison_note = f"No Stage 0 result found for {declined_id}."

        report.declined_comparisons.append(dc)

    # ---- Resource budget characteristics ----
    report.stage0_resource_characteristics = {
        "total_wall_clock_seconds": stage0_report.total_wall_clock,
        "total_workstream_invocations": stage0_report.total_invocations,
        "total_state_transitions": stage0_report.total_transitions,
        "workstreams_per_concept": (
            "1 (manufacturing) — differentiation and structure screening "
            "require additional configuration not present in the "
            "evaluation fixtures"
        ),
        "budget_controls_available": (
            "max_wall_clock_seconds, max_concepts, "
            "max_workstream_invocations (all unbounded in this run)"
        ),
    }

    return report


def _build_declined_note(
    fixture_id: str,
    disposition: str,
) -> str:
    """Build comparison note for a declined candidate."""
    if fixture_id == "EVAL-001":
        return (
            "Baseline: work order reaches submitted state; validation "
            "fails on deliverables_exist because no genomics artifacts "
            "exist.  Stage 0: concept is evaluated through triage "
            f"(disposition: {disposition}).  Stage 0 evaluates the "
            "concept's manufacturing feasibility rather than checking "
            "for pre-existing artifacts — a structurally different "
            "assessment path that does not auto-terminate the concept "
            "for lacking genetic evidence."
        )
    if fixture_id == "EVAL-002":
        return (
            "Baseline: pocket analysis artifact has unfavorable metrics "
            "(druggability score 0.12); fpocket.single_conformation relay "
            "fires.  Stage 0: concept is evaluated through triage "
            f"(disposition: {disposition}).  Stage 0 assesses "
            "manufacturing feasibility independently of the pocket "
            "druggability findings — the unfavorable pocket score does "
            "not auto-terminate the concept, consistent with the "
            "no-automatic-veto design principle."
        )
    return f"Stage 0 disposition: {disposition}"
