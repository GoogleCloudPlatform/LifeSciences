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

"""Tests for the coscientist command group (#279).

Covers:
  - _extract_recommendation: extracts recommendation heading from markdown
  - _extract_recommendation: returns None on empty input
  - _extract_recommendation: returns None when no recommendation heading found
  - _extract_recommendation: handles various heading levels and phrasings
  - analyze surfaces recommendation in assessment dict
  - analyze handles missing recommendation (empty report)
  - analyze handles report with top_ideas_summary but no recommendation heading
  - CLI output shows recommendation preview when section is present
  - CLI output shows fallback message when summary exists but no heading
  - --json output includes recommendation in assessment
  - Analysis artifact contains recommendation field
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

from dde.commands.coscientist import _extract_recommendation

# ---------------------------------------------------------------------------
# Helper: build a normalised tournament artifact
# ---------------------------------------------------------------------------


def _make_tournament(
    *,
    top_ideas_summary: str = "",
    reviews_overview: str = "",
    n_ideas: int = 2,
    n_generated: int = 10,
) -> dict[str, Any]:
    """Build a minimal normalised tournament record for analyze tests."""
    ideas = []
    for i in range(1, n_ideas + 1):
        ideas.append(
            {
                "id": f"idea-{i}",
                "ranking": i,
                "title": f"Idea {i} title",
                "category": "therapeutic",
                "gene": f"GENE{i}",
                "elo_rating": 1500.0 - (i - 1) * 100,
                "attributes": {},
                "match": {
                    "total": 20,
                    "won": 15 - i,
                    "lost": 5 + i,
                    "win_rate": (15 - i) / 20,
                },
                "n_reviews": 3,
                "claims": [
                    {
                        "claim": f"Claim for idea {i}",
                        "verdict": "INACCURATE" if i > 1 else "ACCURATE",
                        "source_sentence": "Some sentence.",
                        "reasoning": "Some reasoning.",
                        "n_references": 2,
                    }
                ],
                "prose": {
                    "summary": f"Summary for idea {i}",
                    "description": f"Description for idea {i}",
                    "reviews_summary": "",
                    "verification_summary": "",
                },
            }
        )
    return {
        "schema": "dde.coscientist.v1",
        "source_file": "test-export.json",
        "tournament": {
            "title": "Test Tournament",
            "goal": "Find targets",
            "state": "COMPLETED",
            "stage": "ENDED",
            "session_id": "test-session-1",
            "created": "2026-01-01T00:00:00Z",
            "ended": "2026-01-02T00:00:00Z",
        },
        "stats": {
            "n_ideas_generated": n_generated,
            "n_categories": 3,
            "highest_elo": 1500.0,
            "input_tokens": 100000,
            "output_tokens": 50000,
        },
        "preferences": [],
        "ideas": ideas,
        "knowledge_base": {
            "summary": "",
            "n_references": 0,
            "n_learned_claims": 0,
            "connections_summary": "",
            "n_connections": 0,
        },
        "report": {
            "overview": "Tournament overview text.",
            "top_ideas_summary": top_ideas_summary,
            "reviews_overview": reviews_overview,
        },
        "_resolved_keys": {
            "ideas": "Ur",
            "report": "BVa",
        },
    }


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory for CliRunner tests."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "hypotheses").mkdir(parents=True, exist_ok=True)
    return project


def _write_tournament(
    project: Path,
    record: dict[str, Any],
    name: str = "cs-test-session-1",
) -> Path:
    """Write a normalised tournament artifact into the project."""
    hypo_dir = project / "raw" / "hypotheses"
    hypo_dir.mkdir(parents=True, exist_ok=True)
    path = hypo_dir / f"{name}.tournament.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Markdown fixtures
# ---------------------------------------------------------------------------

_MD_WITH_RECOMMENDATION = """\
## Top Ideas Overview

Here are the top-ranked ideas from the tournament.

## Recommendation and Best Next Steps

Based on the tournament results, we recommend prioritising GENE1 for
further validation. The following next steps are suggested:

1. Confirm target engagement in a cell-based assay.
2. Validate the ELO leader against an orthogonal dataset.

## Appendix

Additional details and supplementary information.
"""

_MD_WITH_RECOMMENDATION_H3 = """\
### Overview

Some overview content.

### Recommendation

We recommend pursuing GENE1 as the lead target. Next steps include
structural validation and selectivity profiling.

### References

1. Smith et al. 2025
"""

_MD_WITHOUT_RECOMMENDATION = """\
## Top Ideas Overview

Here are the top-ranked ideas from the tournament.

## Appendix

Additional details.
"""

_MD_LONG_RECOMMENDATION = (
    """\
## Overview

Overview content.

## Recommendation and Best Next Steps

"""
    + "This is a very detailed recommendation. " * 50
    + """

## Appendix

End.
"""
)


# ---------------------------------------------------------------------------
# 1. _extract_recommendation unit tests
# ---------------------------------------------------------------------------


def test_extract_recommendation_found() -> None:
    """Extracts recommendation section from markdown with ## heading."""
    result = _extract_recommendation(_MD_WITH_RECOMMENDATION)
    assert result is not None
    assert "Recommendation and Best Next Steps" in result
    assert "prioritising GENE1" in result
    assert "Appendix" not in result
    print("  PASS: extract_recommendation found")


def test_extract_recommendation_h3() -> None:
    """Extracts recommendation section from markdown with ### heading."""
    result = _extract_recommendation(_MD_WITH_RECOMMENDATION_H3)
    assert result is not None
    assert "Recommendation" in result
    assert "pursuing GENE1" in result
    assert "References" not in result
    print("  PASS: extract_recommendation h3")


def test_extract_recommendation_none_empty() -> None:
    """Returns None for empty input."""
    assert _extract_recommendation("") is None
    assert _extract_recommendation(None) is None
    print("  PASS: extract_recommendation None/empty")


def test_extract_recommendation_none_no_heading() -> None:
    """Returns None when no recommendation heading found."""
    result = _extract_recommendation(_MD_WITHOUT_RECOMMENDATION)
    assert result is None
    print("  PASS: extract_recommendation no heading")


def test_extract_recommendation_case_insensitive() -> None:
    """Heading match is case-insensitive."""
    md = "## RECOMMENDATION AND BEST NEXT STEPS\n\nDo this.\n"
    result = _extract_recommendation(md)
    assert result is not None
    assert "Do this." in result
    print("  PASS: extract_recommendation case insensitive")


def test_extract_recommendation_best_next_steps_only() -> None:
    """Matches 'Best Next Steps' heading alone."""
    md = "## Best Next Steps\n\nStep 1.\nStep 2.\n"
    result = _extract_recommendation(md)
    assert result is not None
    assert "Step 1." in result
    print("  PASS: extract_recommendation best next steps only")


# ---------------------------------------------------------------------------
# 2. CliRunner integration tests — analyze with recommendation
# ---------------------------------------------------------------------------


def test_analyze_recommendation_in_assessment() -> None:
    """analyze writes recommendation field into assessment in analysis artifact."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITH_RECOMMENDATION,
            reviews_overview="Some reviews overview text.",
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        assert analysis_path.is_file(), f"Analysis not written: {analysis_path}"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        rec = analysis["assessment"]["recommendation"]
        assert rec["section"] is not None
        assert "prioritising GENE1" in rec["section"]
        assert rec["reviews_overview_available"] is True
        assert rec["top_ideas_summary_available"] is True
    print("  PASS: analyze recommendation in assessment")


def test_analyze_recommendation_empty_report() -> None:
    """analyze handles empty report gracefully."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary="",
            reviews_overview="",
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        rec = analysis["assessment"]["recommendation"]
        assert rec["section"] is None
        assert rec["reviews_overview_available"] is False
        assert rec["top_ideas_summary_available"] is False
    print("  PASS: analyze recommendation empty report")


def test_analyze_no_recommendation_heading() -> None:
    """analyze handles top_ideas_summary with no recommendation heading."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITHOUT_RECOMMENDATION,
            reviews_overview="",
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        rec = analysis["assessment"]["recommendation"]
        assert rec["section"] is None
        assert rec["top_ideas_summary_available"] is True
    print("  PASS: analyze no recommendation heading")


def test_analyze_cli_shows_recommendation_preview() -> None:
    """CLI output shows recommendation preview when section is present."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITH_RECOMMENDATION,
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "--- Review Recommendation ---" in result.output
        assert (
            "Full recommendation available in the analysis artifact." in result.output
        )
    print("  PASS: analyze CLI shows recommendation preview")


def test_analyze_cli_fallback_message() -> None:
    """CLI output shows fallback message when summary exists but no heading."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITHOUT_RECOMMENDATION,
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "no structured recommendation section found" in result.output
    print("  PASS: analyze CLI fallback message")


def test_analyze_cli_no_recommendation_message_when_empty() -> None:
    """CLI output omits recommendation lines when report is empty."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary="",
            reviews_overview="",
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "--- Review Recommendation ---" not in result.output
        assert "no structured recommendation section found" not in result.output
    print("  PASS: analyze CLI no recommendation message when empty")


def test_analyze_json_includes_recommendation() -> None:
    """--json output includes recommendation in assessment."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITH_RECOMMENDATION,
            reviews_overview="Reviews overview.",
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "coscientist",
                "analyze",
                str(artifact_path),
                "--json",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        rec = payload["assessment"]["recommendation"]
        assert rec["section"] is not None
        assert "prioritising GENE1" in rec["section"]
        assert rec["reviews_overview_available"] is True
        assert rec["top_ideas_summary_available"] is True
    print("  PASS: analyze --json includes recommendation")


def test_analyze_long_recommendation_truncated_in_cli() -> None:
    """CLI preview truncates long recommendation to ~500 chars."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_LONG_RECOMMENDATION,
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        # The full recommendation is >500 chars, so CLI should show "..."
        # and the "Full recommendation available" message.
        assert (
            "Full recommendation available in the analysis artifact." in result.output
        )
    print("  PASS: analyze long recommendation truncated in CLI")


# ---------------------------------------------------------------------------
# 3. Assessment core tests — dde.hypothesis-assessment.v1
# ---------------------------------------------------------------------------


def test_assessment_core_present_in_analysis() -> None:
    """analyze output contains assessment_core key with correct schema."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITH_RECOMMENDATION,
            reviews_overview="Some reviews.",
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        core = analysis["assessment"]["assessment_core"]
        assert core["schema"] == "dde.hypothesis-assessment.v1"
        assert core["strategy"] == "co-scientist"
        assert "source_artifact" in core
        assert "source_sha256" in core
        assert isinstance(core["candidates"], list)
        assert len(core["candidates"]) == 2
        for candidate in core["candidates"]:
            assert candidate["origin"] == "generated"
    print("  PASS: assessment_core present in analysis")


def test_assessment_core_score_is_object() -> None:
    """Assessment core score is {value, basis} — never a bare number."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(n_ideas=3, n_generated=10)
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        for candidate in analysis["assessment"]["assessment_core"]["candidates"]:
            score = candidate["score"]
            assert isinstance(score, dict), (
                f"score must be a dict, got {type(score).__name__}: {score}"
            )
            assert "value" in score
            assert "basis" in score
            assert isinstance(score["value"], (int, float))
    print("  PASS: assessment_core score is object")


def test_assessment_core_strategy_is_coscientist() -> None:
    """Assessment core strategy is 'co-scientist'."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament()
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        assert analysis["assessment"]["assessment_core"]["strategy"] == "co-scientist"
    print("  PASS: assessment_core strategy is co-scientist")


def test_assessment_core_basis_is_coscientist_elo() -> None:
    """Assessment core basis is 'coscientist-elo@1.1'."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament()
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        for candidate in analysis["assessment"]["assessment_core"]["candidates"]:
            if candidate["score"] is not None:
                assert candidate["score"]["basis"] == "coscientist-elo@1.1"
    print("  PASS: assessment_core basis is coscientist-elo@1.1")


def test_assessment_core_backward_compatible() -> None:
    """Existing output keys are unchanged (backward compatibility)."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITH_RECOMMENDATION,
            reviews_overview="Some reviews.",
        )
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        # Existing top-level keys must still be present
        assert "source" in analysis
        assert "threshold_set" in analysis
        assert "thresholds_applied" in analysis
        assert "metrics" in analysis
        assert "assessment" in analysis

        # Existing assessment keys must still be present
        assessment = analysis["assessment"]
        assert "verdict" in assessment
        assert "leader" in assessment
        assert "elo_gap_to_runner_up" in assessment
        assert "leader_gap_is_decisive" in assessment
        assert "n_ideas_flagged" in assessment
        assert "ideas" in assessment
        assert "recommendation" in assessment

        # assessment_core is additive
        assert "assessment_core" in assessment
    print("  PASS: assessment_core backward compatible")


def test_assessment_core_null_rank_score() -> None:
    """Ideas without elo_rating / ranking produce rank: null, score: null."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(n_ideas=1, n_generated=5)
        # Strip elo_rating and ranking to simulate missing data
        idea = record["ideas"][0]
        del idea["elo_rating"]
        del idea["ranking"]
        artifact_path = _write_tournament(project, record)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "coscientist", "analyze", str(artifact_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        candidate = analysis["assessment"]["assessment_core"]["candidates"][0]
        assert candidate["rank"] is None, f"expected rank=null, got {candidate['rank']}"
        assert candidate["score"] is None, (
            f"expected score=null, got {candidate['score']}"
        )
    print("  PASS: assessment_core null rank/score for missing elo")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # _extract_recommendation unit tests
        ("test_extract_recommendation_found", test_extract_recommendation_found),
        ("test_extract_recommendation_h3", test_extract_recommendation_h3),
        (
            "test_extract_recommendation_none_empty",
            test_extract_recommendation_none_empty,
        ),
        (
            "test_extract_recommendation_none_no_heading",
            test_extract_recommendation_none_no_heading,
        ),
        (
            "test_extract_recommendation_case_insensitive",
            test_extract_recommendation_case_insensitive,
        ),
        (
            "test_extract_recommendation_best_next_steps_only",
            test_extract_recommendation_best_next_steps_only,
        ),
        # CliRunner integration tests — analyze with recommendation
        (
            "test_analyze_recommendation_in_assessment",
            test_analyze_recommendation_in_assessment,
        ),
        (
            "test_analyze_recommendation_empty_report",
            test_analyze_recommendation_empty_report,
        ),
        (
            "test_analyze_no_recommendation_heading",
            test_analyze_no_recommendation_heading,
        ),
        (
            "test_analyze_cli_shows_recommendation_preview",
            test_analyze_cli_shows_recommendation_preview,
        ),
        ("test_analyze_cli_fallback_message", test_analyze_cli_fallback_message),
        (
            "test_analyze_cli_no_recommendation_message_when_empty",
            test_analyze_cli_no_recommendation_message_when_empty,
        ),
        (
            "test_analyze_json_includes_recommendation",
            test_analyze_json_includes_recommendation,
        ),
        (
            "test_analyze_long_recommendation_truncated_in_cli",
            test_analyze_long_recommendation_truncated_in_cli,
        ),
        # Assessment core tests
        (
            "test_assessment_core_present_in_analysis",
            test_assessment_core_present_in_analysis,
        ),
        ("test_assessment_core_score_is_object", test_assessment_core_score_is_object),
        (
            "test_assessment_core_strategy_is_coscientist",
            test_assessment_core_strategy_is_coscientist,
        ),
        (
            "test_assessment_core_basis_is_coscientist_elo",
            test_assessment_core_basis_is_coscientist_elo,
        ),
        (
            "test_assessment_core_backward_compatible",
            test_assessment_core_backward_compatible,
        ),
        ("test_assessment_core_null_rank_score", test_assessment_core_null_rank_score),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} — {exc}")
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
