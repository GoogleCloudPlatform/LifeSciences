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

"""Tests for GWAS composite score separation and disease filter.

Covers:
  1. genetic_association_score appears separately in output
  2. Relay fires when composite passes but genetic doesn't
  3. --disease-filter matching
  4. --disease-filter no match
  5. Relay code registration
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

from dde.core import provenance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "genomics").mkdir(parents=True, exist_ok=True)
    return project


def _write_gwas_artifact(
    project: Path,
    gene: str,
    associations: list[dict[str, Any]],
    source: str = "opentargets",
) -> Path:
    """Write a canned dde.gwas.v1 artifact for the analyze command."""
    slug = gene.lower()
    artifact_path = project / "raw" / "genomics" / f"{slug}.gwas-{source}.artifact.json"

    top_diseases: list[str] = []
    seen: set[str] = set()
    for a in associations[:5]:
        name = a.get("disease_name", "")
        if name and name not in seen:
            top_diseases.append(name)
            seen.add(name)

    artifact = {
        "schema": "dde.gwas.v1",
        "query": {"gene": gene.upper(), "source": source},
        "summary": {
            "n_associations": len(associations),
            "top_diseases": top_diseases,
        },
        "associations": associations,
    }
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact_path


# ---------------------------------------------------------------------------
# 1. genetic_association_score appears separately in output
# ---------------------------------------------------------------------------


def test_genetic_association_score_in_output() -> None:
    """Analyze output includes genetic_association_score per association."""
    from click.testing import CliRunner
    from dde.cli import cli

    associations = [
        {
            "source_db": "opentargets",
            "disease_id": "EFO_0000249",
            "disease_name": "Alzheimer disease",
            "score": 0.42,
            "evidence_count": 3,
            "datatype_scores": {
                "genetic_association": 0.31,
                "literature": 0.08,
                "known_drug": 0.05,
            },
        },
        {
            "source_db": "opentargets",
            "disease_id": "EFO_0000311",
            "disease_name": "cancer",
            "score": 0.15,
            "evidence_count": 2,
            "datatype_scores": {
                "genetic_association": 0.12,
                "literature": 0.03,
            },
        },
    ]

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_gwas_artifact(project, "APOE", associations)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "gwas",
                "analyze",
                "APOE",
                "--source",
                "opentargets",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check analysis artifact
        genomics_dir = project / "raw" / "genomics"
        analysis_files = list(genomics_dir.glob("*.analysis.json"))
        assert analysis_files, "No analysis file found"

        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
        assessment = analysis["assessment"]

        # score_details should contain genetic_association_score
        assert "score_details" in assessment, (
            f"No score_details in assessment: {list(assessment.keys())}"
        )
        for detail in assessment["score_details"]:
            assert "genetic_association_score" in detail, (
                f"No genetic_association_score in detail: {detail}"
            )
            assert "overall_score" in detail
            assert "datatype_scores" in detail

        # Check that the first one has the expected value
        first = assessment["score_details"][0]
        assert first["genetic_association_score"] == 0.31
        assert first["overall_score"] == 0.42

        # Check output contains composite breakdown
        assert "composite:" in result.output

    print("  PASS: genetic_association_score appears in output")


# ---------------------------------------------------------------------------
# 2. Relay fires when composite passes but genetic doesn't
# ---------------------------------------------------------------------------


def test_composite_not_genetic_relay() -> None:
    """opentargets.composite_not_genetic fires when composite passes, genetic fails."""
    from click.testing import CliRunner
    from dde.cli import cli

    # score=0.42 >= 0.1 (default threshold) but genetic_association=0.05 < 0.1
    associations = [
        {
            "source_db": "opentargets",
            "disease_id": "EFO_0000249",
            "disease_name": "Alzheimer disease",
            "score": 0.42,
            "evidence_count": 3,
            "datatype_scores": {
                "genetic_association": 0.05,
                "literature": 0.30,
                "known_drug": 0.10,
            },
        },
    ]

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_gwas_artifact(project, "TESTGENE", associations)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "gwas",
                "analyze",
                "TESTGENE",
                "--source",
                "opentargets",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check the relay was fired
        genomics_dir = project / "raw" / "genomics"
        analysis_files = list(genomics_dir.glob("*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "opentargets.composite_not_genetic" in relay_codes, (
            f"opentargets.composite_not_genetic not fired; relays: {relay_codes}"
        )

        # Also check the output mentions the relay
        assert "opentargets.composite_not_genetic" in result.output

    print("  PASS: composite_not_genetic relay fires correctly")


# ---------------------------------------------------------------------------
# 3. Relay does NOT fire when genetic also passes
# ---------------------------------------------------------------------------


def test_no_relay_when_genetic_passes() -> None:
    """No composite_not_genetic relay when genetic_association also passes."""
    from click.testing import CliRunner
    from dde.cli import cli

    # Both overall and genetic_association >= threshold
    associations = [
        {
            "source_db": "opentargets",
            "disease_id": "EFO_0000249",
            "disease_name": "Alzheimer disease",
            "score": 0.42,
            "evidence_count": 3,
            "datatype_scores": {
                "genetic_association": 0.31,
                "literature": 0.08,
            },
        },
    ]

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_gwas_artifact(project, "GOODGENE", associations)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "gwas",
                "analyze",
                "GOODGENE",
                "--source",
                "opentargets",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        genomics_dir = project / "raw" / "genomics"
        analysis_files = list(genomics_dir.glob("*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "opentargets.composite_not_genetic" not in relay_codes, (
            "composite_not_genetic should NOT fire when genetic passes"
        )

    print("  PASS: no relay when genetic also passes")


# ---------------------------------------------------------------------------
# 4. --disease-filter matching
# ---------------------------------------------------------------------------


def test_disease_filter_match() -> None:
    """--disease-filter finds matching disease and reports score."""
    from click.testing import CliRunner
    from dde.cli import cli

    associations = [
        {
            "source_db": "opentargets",
            "disease_id": "EFO_0000249",
            "disease_name": "Alzheimer disease",
            "score": 0.42,
            "evidence_count": 3,
            "datatype_scores": {"genetic_association": 0.31},
        },
        {
            "source_db": "opentargets",
            "disease_id": "EFO_0003885",
            "disease_name": "atopic dermatitis",
            "score": 0.28,
            "evidence_count": 2,
            "datatype_scores": {"genetic_association": 0.20},
        },
    ]

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_gwas_artifact(project, "OSMR", associations)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "gwas",
                "analyze",
                "OSMR",
                "--source",
                "opentargets",
                "--disease-filter",
                "atopic",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
        assert "MATCH" in result.output
        assert "atopic dermatitis" in result.output

        # Check assessment
        genomics_dir = project / "raw" / "genomics"
        analysis_files = list(genomics_dir.glob("*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
        assessment = analysis["assessment"]

        assert assessment["disease_filter_match"] is True
        assert len(assessment["disease_filter_details"]) == 1
        assert (
            assessment["disease_filter_details"][0]["disease_name"]
            == "atopic dermatitis"
        )
        assert assessment["disease_filter_details"][0]["score"] == 0.28

    print("  PASS: --disease-filter match works")


# ---------------------------------------------------------------------------
# 5. --disease-filter no match
# ---------------------------------------------------------------------------


def test_disease_filter_no_match() -> None:
    """--disease-filter reports NO MATCH when disease not in associations."""
    from click.testing import CliRunner
    from dde.cli import cli

    associations = [
        {
            "source_db": "opentargets",
            "disease_id": "EFO_0000249",
            "disease_name": "Alzheimer disease",
            "score": 0.42,
            "evidence_count": 3,
            "datatype_scores": {"genetic_association": 0.31},
        },
    ]

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_gwas_artifact(project, "APOE", associations)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "gwas",
                "analyze",
                "APOE",
                "--source",
                "opentargets",
                "--disease-filter",
                "psoriasis",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
        assert "NO MATCH" in result.output

        genomics_dir = project / "raw" / "genomics"
        analysis_files = list(genomics_dir.glob("*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
        assessment = analysis["assessment"]

        assert assessment["disease_filter_match"] is False
        assert len(assessment["disease_filter_details"]) == 0

    print("  PASS: --disease-filter no match works")


# ---------------------------------------------------------------------------
# 6. Relay code registered in RELAY_CODES
# ---------------------------------------------------------------------------


def test_relay_code_registered() -> None:
    """opentargets.composite_not_genetic is in provenance.RELAY_CODES."""
    assert "opentargets.composite_not_genetic" in provenance.RELAY_CODES, (
        "opentargets.composite_not_genetic not registered in RELAY_CODES"
    )
    print("  PASS: opentargets.composite_not_genetic registered in RELAY_CODES")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        (
            "test_genetic_association_score_in_output",
            test_genetic_association_score_in_output,
        ),
        ("test_composite_not_genetic_relay", test_composite_not_genetic_relay),
        ("test_no_relay_when_genetic_passes", test_no_relay_when_genetic_passes),
        ("test_disease_filter_match", test_disease_filter_match),
        ("test_disease_filter_no_match", test_disease_filter_no_match),
        ("test_relay_code_registered", test_relay_code_registered),
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
