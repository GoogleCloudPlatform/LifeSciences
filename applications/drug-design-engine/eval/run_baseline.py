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

"""Run the DDE evaluation baseline and produce reports.

Usage (from applications/DDE/):

    PYTHONPATH=tools python3 -m eval.run_baseline

Or directly:

    PYTHONPATH=tools python3 eval/run_baseline.py

Outputs:
    eval/baseline/baseline-report.json   — full machine-readable report
    eval/baseline/baseline-report.md     — human-readable summary
    eval/baseline/run-manifest.json      — reproduction manifest
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the tools package is importable from DDE root.
_DDE_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _DDE_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
# Ensure the DDE root is importable (for eval package).
if str(_DDE_ROOT) not in sys.path:
    sys.path.insert(0, str(_DDE_ROOT))

from eval.fixtures.definitions import (  # noqa: E402
    ALL_FIXTURES,
    DECLINED_CANDIDATE_SAMPLE,
)
from eval.harness import run_all_fixtures  # noqa: E402


def main() -> int:
    """Run the baseline evaluation and write reports."""
    baseline_dir = _DDE_ROOT / "eval" / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    # Run all fixtures
    report = run_all_fixtures()

    # Write JSON report
    json_path = report.write_json(baseline_dir / "baseline-report.json")
    print(f"\nJSON report written to: {json_path}")

    # Write markdown report
    md_path = report.write_markdown(baseline_dir / "baseline-report.md")
    print(f"Markdown report written to: {md_path}")

    # Write run manifest for reproducibility
    manifest = {
        "eval_version": report.eval_version,
        "run_timestamp": report.run_timestamp,
        "python_version": sys.version,
        "platform": sys.platform,
        "fixture_count": len(ALL_FIXTURES),
        "fixture_ids": [f.fixture_id for f in ALL_FIXTURES],
        "fixture_categories": [f.category for f in ALL_FIXTURES],
        "declined_candidate_sample": DECLINED_CANDIDATE_SAMPLE,
        "completed": report.completed_fixtures,
        "total": report.total_fixtures,
        "command": "PYTHONPATH=tools python3 -m eval.run_baseline",
        "working_directory": str(_DDE_ROOT),
        "outputs": [
            str(json_path.relative_to(_DDE_ROOT)),
            str(md_path.relative_to(_DDE_ROOT)),
        ],
    }
    manifest_path = baseline_dir / "run-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Run manifest written to: {manifest_path}")

    if report.completed_fixtures < report.total_fixtures:
        failed = report.total_fixtures - report.completed_fixtures
        print(f"\nWARNING: {failed} fixture(s) did not complete.")
        return 1

    print("\nBaseline evaluation complete. All fixtures passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
