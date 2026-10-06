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

"""Tests for coscientist review_recommendation_available relay (#280).

Covers:
  - Relay fires when recommendation section is present
  - Relay absent when recommendation section is absent (no heading)
  - Relay absent when report is empty
  - Relay code matches registered code in RELAY_CODES
  - Relay is deduplicated (not duplicated if already in meta sidecar)
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

from dde.core.provenance import RELAY_CODES

RELAY_CODE = "coscientist.review_recommendation_available"


# ---------------------------------------------------------------------------
# Helper: build a normalised tournament artifact (mirrors test_coscientist.py)
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


def _write_meta_sidecar(
    artifact_path: Path,
    mandatory_relays: list[dict[str, str]] | None = None,
) -> Path:
    """Write a .meta.json sidecar beside the tournament artifact."""
    meta_path = artifact_path.with_name(
        artifact_path.name.replace(".tournament.json", ".meta.json")
    )
    meta = {
        "tool": "co-scientist",
        "subcommand": "ingest",
        "mandatory_relays": mandatory_relays or [],
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta_path


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

_MD_WITHOUT_RECOMMENDATION = """\
## Top Ideas Overview

Here are the top-ranked ideas from the tournament.

## Appendix

Additional details.
"""


# ---------------------------------------------------------------------------
# 1. Relay code is registered
# ---------------------------------------------------------------------------


def test_relay_code_registered() -> None:
    """The relay code is present in the RELAY_CODES registry."""
    assert RELAY_CODE in RELAY_CODES, (
        f"{RELAY_CODE!r} not found in RELAY_CODES; "
        f"registered codes: {sorted(RELAY_CODES)}"
    )
    # The description should be a non-empty string.
    assert isinstance(RELAY_CODES[RELAY_CODE], str)
    assert len(RELAY_CODES[RELAY_CODE]) > 0
    print("  PASS: relay code registered")


# ---------------------------------------------------------------------------
# 2. Relay fires when recommendation section is present
# ---------------------------------------------------------------------------


def test_relay_fires_when_recommendation_present() -> None:
    """analyze emits the relay when the recommendation section is present."""
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

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        assert analysis_path.is_file(), f"Analysis not written: {analysis_path}"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        relays = analysis.get("mandatory_relays", [])
        codes = [r["code"] for r in relays]
        assert RELAY_CODE in codes, (
            f"Expected {RELAY_CODE!r} in mandatory_relays, got codes: {codes}"
        )
    print("  PASS: relay fires when recommendation present")


# ---------------------------------------------------------------------------
# 3. Relay absent when recommendation section is absent (no heading)
# ---------------------------------------------------------------------------


def test_relay_absent_when_no_recommendation_heading() -> None:
    """analyze does NOT emit the relay when top_ideas_summary has no recommendation heading."""
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

        analysis_path = artifact_path.with_name(
            artifact_path.name.replace(".tournament.json", ".analysis.json")
        )
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        relays = analysis.get("mandatory_relays", [])
        codes = [r["code"] for r in relays]
        assert RELAY_CODE not in codes, (
            f"Relay {RELAY_CODE!r} should NOT fire when no recommendation heading; "
            f"got codes: {codes}"
        )
    print("  PASS: relay absent when no recommendation heading")


# ---------------------------------------------------------------------------
# 4. Relay absent when report is empty
# ---------------------------------------------------------------------------


def test_relay_absent_when_report_empty() -> None:
    """analyze does NOT emit the relay when the report is empty."""
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

        relays = analysis.get("mandatory_relays", [])
        codes = [r["code"] for r in relays]
        assert RELAY_CODE not in codes, (
            f"Relay {RELAY_CODE!r} should NOT fire on empty report; got codes: {codes}"
        )
    print("  PASS: relay absent when report empty")


# ---------------------------------------------------------------------------
# 5. Relay is deduplicated
# ---------------------------------------------------------------------------


def test_relay_deduplicated_with_meta_sidecar() -> None:
    """Relay is not duplicated if already present in the meta sidecar."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        record = _make_tournament(
            top_ideas_summary=_MD_WITH_RECOMMENDATION,
        )
        artifact_path = _write_tournament(project, record)

        # Write a meta sidecar that already carries the relay code.
        _write_meta_sidecar(
            artifact_path,
            mandatory_relays=[
                {
                    "code": RELAY_CODE,
                    "message": "Pre-existing relay from ingest.",
                }
            ],
        )

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
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        relays = analysis.get("mandatory_relays", [])
        codes = [r["code"] for r in relays]
        count = codes.count(RELAY_CODE)
        assert count == 1, (
            f"Expected exactly 1 instance of {RELAY_CODE!r}, got {count}; "
            f"relay codes: {codes}"
        )
    print("  PASS: relay deduplicated with meta sidecar")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_relay_code_registered", test_relay_code_registered),
        (
            "test_relay_fires_when_recommendation_present",
            test_relay_fires_when_recommendation_present,
        ),
        (
            "test_relay_absent_when_no_recommendation_heading",
            test_relay_absent_when_no_recommendation_heading,
        ),
        ("test_relay_absent_when_report_empty", test_relay_absent_when_report_empty),
        (
            "test_relay_deduplicated_with_meta_sidecar",
            test_relay_deduplicated_with_meta_sidecar,
        ),
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
