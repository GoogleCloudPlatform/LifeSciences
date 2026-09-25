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

"""Run the DDE evaluation comparison — Stage 0 vs. baseline.

Usage (from applications/DDE/):

    PYTHONPATH=tools python3 -m eval.run_comparison

Or directly:

    PYTHONPATH=tools python3 eval/run_comparison.py

Outputs:
    eval/comparison/comparison-report.json  — full machine-readable report
    eval/comparison/comparison-report.md    — human-readable summary
    eval/comparison/stage0-report.json      — Stage 0 run results
    eval/comparison/run-manifest.json       — reproduction manifest

Prerequisites:
    The baseline must already exist at eval/baseline/baseline-report.json
    (produced by Phase 1 via ``python3 -m eval.run_baseline``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_DDE_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _DDE_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_DDE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DDE_ROOT))

from eval.comparison import generate_comparison, load_baseline_report  # noqa: E402
from eval.fixtures.definitions import (  # noqa: E402
    ALL_FIXTURES,
    DECLINED_CANDIDATE_SAMPLE,
)
from eval.stage0_harness import run_all_fixtures_stage0  # noqa: E402


def main() -> int:
    """Run the Stage 0 evaluation and produce comparison reports."""
    comparison_dir = _DDE_ROOT / "eval" / "comparison"
    comparison_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = _DDE_ROOT / "eval" / "baseline" / "baseline-report.json"
    if not baseline_path.exists():
        print(f"ERROR: Baseline report not found at {baseline_path}")
        print("Run the baseline first: PYTHONPATH=tools python3 -m eval.run_baseline")
        return 1

    # Load baseline (frozen Phase 1 output — not regenerated).
    baseline_data = load_baseline_report(baseline_path)
    print(f"Loaded baseline from {baseline_path}")
    print(f"  Baseline timestamp: {baseline_data.get('run_timestamp', 'unknown')}")

    # Run Stage 0 evaluation.
    stage0_report = run_all_fixtures_stage0()

    # Write Stage 0 report.
    stage0_json = stage0_report.write_json(comparison_dir / "stage0-report.json")
    print(f"\nStage 0 report written to: {stage0_json}")

    # Generate comparison.
    comparison = generate_comparison(baseline_data, stage0_report)

    # Write comparison reports.
    json_path = comparison.write_json(comparison_dir / "comparison-report.json")
    print(f"Comparison JSON written to: {json_path}")

    md_path = comparison.write_markdown(comparison_dir / "comparison-report.md")
    print(f"Comparison markdown written to: {md_path}")

    # Write run manifest for reproducibility.
    manifest = {
        "eval_version": comparison.eval_version,
        "comparison_timestamp": comparison.comparison_timestamp,
        "baseline_timestamp": comparison.baseline_timestamp,
        "stage0_timestamp": comparison.stage0_timestamp,
        "python_version": sys.version,
        "platform": sys.platform,
        "fixture_count": len(ALL_FIXTURES),
        "fixture_ids": [f.fixture_id for f in ALL_FIXTURES],
        "declined_candidate_sample": DECLINED_CANDIDATE_SAMPLE,
        "baseline_source": str(baseline_path.relative_to(_DDE_ROOT)),
        "stage0_completed": stage0_report.completed_fixtures,
        "stage0_total": stage0_report.total_fixtures,
        "scope_limitation": (
            "Evidence reuse (#77) is P2 and out of scope — only "
            "baseline vs. Stage 0 bounded triage is compared."
        ),
        "command": "PYTHONPATH=tools python3 -m eval.run_comparison",
        "working_directory": str(_DDE_ROOT),
        "outputs": [
            str(stage0_json.relative_to(_DDE_ROOT)),
            str(json_path.relative_to(_DDE_ROOT)),
            str(md_path.relative_to(_DDE_ROOT)),
        ],
    }
    manifest_path = comparison_dir / "run-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Run manifest written to: {manifest_path}")

    if stage0_report.completed_fixtures < stage0_report.total_fixtures:
        failed = stage0_report.total_fixtures - stage0_report.completed_fixtures
        print(f"\nWARNING: {failed} Stage 0 fixture(s) did not complete.")
        return 1

    print("\nComparison evaluation complete.  All fixtures passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
