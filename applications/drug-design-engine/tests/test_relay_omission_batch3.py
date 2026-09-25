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

"""Regression tests for Silent Relay Omission fixes (Batch 3).

Covers:
  #197 — hypex.citation_manifest_absent fires when citation manifest is corrupt
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    return project


def _write_json(path: Path, data: Any) -> Path:
    """Write JSON to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _make_hypex_run_dir(
    base: Path,
    *,
    hypothesis_ids: list[str] | None = None,
    corrupt_citation_manifest_ids: list[str] | None = None,
    valid_citation_manifest_ids: list[str] | None = None,
) -> Path:
    """Create a minimal hypex run directory with hypotheses and optional
    citation manifests.

    Args:
        base: Parent directory for the run.
        hypothesis_ids: IDs for hypotheses to create. Defaults to ["H-0001"].
        corrupt_citation_manifest_ids: Hypothesis IDs for which to write
            corrupt (unparseable) citation manifest files.
        valid_citation_manifest_ids: Hypothesis IDs for which to write
            valid citation manifest files.
    """
    if hypothesis_ids is None:
        hypothesis_ids = ["H-0001"]
    if corrupt_citation_manifest_ids is None:
        corrupt_citation_manifest_ids = []
    if valid_citation_manifest_ids is None:
        valid_citation_manifest_ids = []

    run_dir = base / "hypex-run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create hypotheses
    hyp_dir = run_dir / "hypotheses"
    hyp_dir.mkdir(exist_ok=True)
    for h_id in hypothesis_ids:
        _write_json(
            hyp_dir / f"{h_id}.json",
            {
                "id": h_id,
                "title": f"Test hypothesis {h_id}",
                "statement": "A test statement.",
                "mechanism": "A test mechanism.",
                "status": "active",
                "lineage": {
                    "parents": [],
                    "operator": "null",
                },
            },
        )

    # Create empty but required directories
    (run_dir / "matches").mkdir(exist_ok=True)
    (run_dir / "reviews").mkdir(exist_ok=True)

    # Create citation manifests
    citations_dir = run_dir / "citations"
    citations_dir.mkdir(exist_ok=True)

    for h_id in corrupt_citation_manifest_ids:
        manifest_path = citations_dir / f"{h_id}.json"
        # Write corrupt (unparseable) JSON
        manifest_path.write_text("{invalid json content", encoding="utf-8")

    for h_id in valid_citation_manifest_ids:
        _write_json(
            citations_dir / f"{h_id}.json",
            {
                "summary": {
                    "total": 5,
                    "verified": 3,
                    "phantom": 1,
                    "suspect": 0,
                    "unverified": 1,
                },
            },
        )

    return run_dir


# ---------------------------------------------------------------------------
# Issue #197 — hypex.citation_manifest_absent with corrupt manifest
# ---------------------------------------------------------------------------


def test_manifest_present_false_when_citation_manifest_corrupt() -> None:
    """#197: When a citation manifest file exists but is corrupt (invalid
    JSON), manifest_present must be False in the ingested record so that
    the hypex.citation_manifest_absent relay fires.

    Before the fix, manifest_present was set to True BEFORE parsing the
    file, so corrupt manifests silently suppressed the relay.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        run_dir = _make_hypex_run_dir(
            Path(td),
            hypothesis_ids=["H-0001"],
            corrupt_citation_manifest_ids=["H-0001"],
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypex",
                "ingest",
                str(run_dir),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Find the ingested artifact
        hypex_dir = project / "raw" / "hypotheses"
        artifact_files = list(hypex_dir.glob("*.hypex.json"))
        assert artifact_files, f"No hypex artifact found in {hypex_dir}"
        record = json.loads(artifact_files[0].read_text(encoding="utf-8"))

        # Verify manifest_present is False for the hypothesis with the
        # corrupt citation manifest
        hyp_list = record.get("hypotheses", [])
        assert hyp_list, "No hypotheses in the ingested record"
        h = hyp_list[0]
        citations = h.get("citations", {})
        assert citations.get("manifest_present") is False, (
            f"manifest_present should be False when citation manifest is "
            f"corrupt; got: {citations}"
        )
    print("  PASS: manifest_present is False when citation manifest is corrupt")


def test_relay_fires_when_citation_manifest_corrupt() -> None:
    """#197: hypex.citation_manifest_absent relay MUST fire when the
    citation manifest exists but is corrupt/unreadable.

    This tests the full pipeline: ingest then analyze, verifying that the
    mandatory relay appears in the analysis output.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        run_dir = _make_hypex_run_dir(
            Path(td),
            hypothesis_ids=["H-0001"],
            corrupt_citation_manifest_ids=["H-0001"],
        )

        runner = CliRunner()

        # Step 1: Ingest
        ingest_result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypex",
                "ingest",
                str(run_dir),
            ],
            catch_exceptions=False,
        )
        assert ingest_result.exit_code == 0, (
            f"Ingest failed: exit {ingest_result.exit_code}\n{ingest_result.output}"
        )

        # Find the ingested artifact for analyze
        hypex_dir = project / "raw" / "hypotheses"
        artifact_files = list(hypex_dir.glob("*.hypex.json"))
        assert artifact_files, f"No hypex artifact found in {hypex_dir}"
        artifact_path = artifact_files[0]

        # Step 2: Analyze
        analyze_result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypex",
                "analyze",
                str(artifact_path),
            ],
            catch_exceptions=False,
        )
        assert analyze_result.exit_code == 0, (
            f"Analyze failed: exit {analyze_result.exit_code}\n{analyze_result.output}"
        )

        # Find the analysis file
        analysis_files = list(hypex_dir.glob("*.analysis.json"))
        assert analysis_files, f"No analysis file found in {hypex_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.citation_manifest_absent" in relay_codes, (
            f"hypex.citation_manifest_absent should fire when citation "
            f"manifest is corrupt; got relay codes: {relay_codes}"
        )
    print("  PASS: relay fires when citation manifest is corrupt")


def test_manifest_present_true_when_citation_manifest_valid() -> None:
    """Sanity check: manifest_present is True when citation manifest is
    valid, and the hypex.citation_manifest_absent relay does NOT fire.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        run_dir = _make_hypex_run_dir(
            Path(td),
            hypothesis_ids=["H-0001"],
            valid_citation_manifest_ids=["H-0001"],
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypex",
                "ingest",
                str(run_dir),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        hypex_dir = project / "raw" / "hypotheses"
        artifact_files = list(hypex_dir.glob("*.hypex.json"))
        assert artifact_files, f"No hypex artifact found in {hypex_dir}"
        record = json.loads(artifact_files[0].read_text(encoding="utf-8"))

        hyp_list = record.get("hypotheses", [])
        assert hyp_list, "No hypotheses in the ingested record"
        h = hyp_list[0]
        citations = h.get("citations", {})
        assert citations.get("manifest_present") is True, (
            f"manifest_present should be True when citation manifest is "
            f"valid; got: {citations}"
        )
    print("  PASS: manifest_present is True when citation manifest is valid")


# ---------------------------------------------------------------------------
# Runner (for manual execution outside pytest)
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        (
            "test_manifest_present_false_when_citation_manifest_corrupt",
            test_manifest_present_false_when_citation_manifest_corrupt,
        ),
        (
            "test_relay_fires_when_citation_manifest_corrupt",
            test_relay_fires_when_citation_manifest_corrupt,
        ),
        (
            "test_manifest_present_true_when_citation_manifest_valid",
            test_manifest_present_true_when_citation_manifest_valid,
        ),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} -- {exc}")
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
