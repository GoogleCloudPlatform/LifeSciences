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

"""Tests for the hypex command group.

Covers (from the brief's test requirements):
  - Happy path: valid run dir → correct dde.hypex.v1 schema
  - Integrity violations: dangling match ref → fires hypex.integrity_violations
  - Aborted run: missing termination.json → termination.declared: false,
    fires hypex.run_aborted
  - Unconverged: budget_exhausted → unconverged verdict,
    fires hypex.run_not_converged
  - leader_gap_is_decisive returns null (not fallback to 50.0)
  - Relay code registration: all 9 codes in RELAY_CODES
  - Assessment core: score is {value, basis} not bare number
  - Pacing uncoordinated: missing/bad pacing.json → fires relay
  - Threshold set: hypex@1.0 is declared
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

from click.testing import CliRunner
from dde.cli import cli
from dde.core import provenance
from dde.core.thresholds import UNRESOLVED, declared_sets

# ---------------------------------------------------------------------------
# Helper: build minimal run directories
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "hypotheses").mkdir(parents=True, exist_ok=True)
    return project


def _make_hypothesis(
    h_id: str = "H-0001",
    title: str = "Test Hypothesis",
    status: str = "active",
    parents: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal valid hypothesis record."""
    return {
        "id": h_id,
        "title": title,
        "statement": f"Statement for {h_id}",
        "mechanism": f"Mechanism for {h_id}",
        "predictions": [f"Prediction for {h_id}"],
        "experiments": [
            {
                "design": "Test design",
                "readout": "Test readout",
                "est_difficulty": "low",
            }
        ],
        "evidence": [
            {
                "lit_id": "PMID:12345678",
                "role": "supports",
                "note": "Supporting evidence",
            }
        ],
        "focus_area": "test-area",
        "lineage": {
            "parents": parents or [],
            "operator": "null" if not parents else "combine",
        },
        "status": status,
        "created_by": "test-agent",
        "epoch": 0,
        "created_at": "2026-09-08T00:00:00Z",
    }


def _make_match(
    m_id: str = "M-0001",
    a: str = "H-0001",
    b: str = "H-0002",
    winner: str = "H-0001",
    epoch: int = 0,
) -> dict[str, Any]:
    """Build a minimal valid match record."""
    return {
        "id": m_id,
        "epoch": epoch,
        "a": a,
        "b": b,
        "format": "single-turn",
        "winner": winner,
        "margin": "decisive",
        "criterion_scores": {
            "novelty": {"a": 4, "b": 3},
            "plausibility": {"a": 4, "b": 3},
            "testability": {"a": 4, "b": 3},
        },
        "rationale": "Test rationale",
        "judge": "test-judge",
        "created_at": "2026-09-08T00:00:00Z",
    }


def _make_review(
    r_id: str = "H-0001.R-01",
    hypothesis_id: str = "H-0001",
    epoch: int = 0,
) -> dict[str, Any]:
    """Build a minimal valid review record."""
    return {
        "id": r_id,
        "hypothesis_id": hypothesis_id,
        "review_type": "initial",
        "scores": {
            "correctness": 4,
            "novelty": 3,
            "testability": 4,
            "safety": 5,
        },
        "verdict": "Promising hypothesis with strong testability.",
        "key_criticisms": ["Could benefit from more evidence"],
        "verified_citations": ["PMID:12345678"],
        "contradicting_evidence": [],
        "reviewer": "test-reviewer",
        "epoch": epoch,
    }


def _make_ratings(
    hypotheses: list[str],
    elos: list[float] | None = None,
    epoch: int = 0,
) -> dict[str, Any]:
    """Build a minimal ratings record."""
    if elos is None:
        elos = [1500.0 + (i * 50) for i in range(len(hypotheses))]
    ratings = {}
    for h_id, elo in zip(hypotheses, elos, strict=True):
        ratings[h_id] = {
            "elo": elo,
            "matches": 10,
            "wins": 5,
            "draws": 1,
        }
    return {
        "epoch": epoch,
        "base_rating": 1500.0,
        "ratings": ratings,
        "computed_from": "matches/ ledger @ M-0010",
    }


def _make_run_dir(
    base: Path,
    n_hypotheses: int = 3,
    *,
    include_termination: bool = True,
    termination_reason: str = "converged",
    include_ratings: bool = True,
    include_reviews: bool = True,
    include_matches: bool = True,
    include_pacing: bool = False,
    pacing_tier: str = "shared",
    include_quarantine: bool = False,
    dangling_match_ref: str | None = None,
    elos: list[float] | None = None,
) -> Path:
    """Create a minimal hypex run directory with all required subdirectories."""
    run_dir = base / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    for subdir in ("hypotheses", "matches", "reviews", "ratings", "meta", "quarantine"):
        (run_dir / subdir).mkdir(exist_ok=True)

    # Write hypotheses
    h_ids = [f"H-{i + 1:04d}" for i in range(n_hypotheses)]
    for h_id in h_ids:
        h = _make_hypothesis(h_id, title=f"Hypothesis {h_id}")
        (run_dir / "hypotheses" / f"{h_id}.json").write_text(
            json.dumps(h, indent=2), encoding="utf-8"
        )

    # Write matches
    if include_matches:
        for i in range(n_hypotheses - 1):
            a_id = h_ids[i]
            b_id = h_ids[i + 1]
            winner = dangling_match_ref if (dangling_match_ref and i == 0) else a_id
            m = _make_match(
                m_id=f"M-{i + 1:04d}",
                a=dangling_match_ref if (dangling_match_ref and i == 0) else a_id,
                b=b_id,
                winner=winner,
            )
            (run_dir / "matches" / f"M-{i + 1:04d}.json").write_text(
                json.dumps(m, indent=2), encoding="utf-8"
            )

    # Write reviews
    if include_reviews:
        for h_id in h_ids:
            r = _make_review(
                r_id=f"{h_id}.R-01",
                hypothesis_id=h_id,
            )
            (run_dir / "reviews" / f"{h_id}.R-01.json").write_text(
                json.dumps(r, indent=2), encoding="utf-8"
            )

    # Write ratings
    if include_ratings:
        if elos is None:
            elos = [1500.0 + (n_hypotheses - i) * 50 for i in range(n_hypotheses)]
        ratings = _make_ratings(h_ids, elos=elos)
        (run_dir / "ratings" / "epoch-0.json").write_text(
            json.dumps(ratings, indent=2), encoding="utf-8"
        )

    # Write termination
    if include_termination:
        term = {"reason": termination_reason, "epoch_reached": 0}
        (run_dir / "meta" / "termination.json").write_text(
            json.dumps(term, indent=2), encoding="utf-8"
        )

    # Write pacing
    if include_pacing:
        pacing = {"tier": pacing_tier, "paths": ["/shared/pace"]}
        (run_dir / "meta" / "pacing.json").write_text(
            json.dumps(pacing, indent=2), encoding="utf-8"
        )

    # Write quarantine
    if include_quarantine:
        q_h = _make_hypothesis("H-9999", title="Quarantined", status="quarantined")
        (run_dir / "quarantine" / "H-9999.json").write_text(
            json.dumps(q_h, indent=2), encoding="utf-8"
        )

    # Write run.yaml
    run_yaml = {
        "goal": "Test tournament goal",
        "constraints": [],
        "created_at": "2026-09-08T00:00:00Z",
        "tournament_strategy": "proximity-elo",
        "budgets": {
            "max_hypotheses": 50,
            "max_matches": 200,
            "max_epochs": 5,
        },
    }
    try:
        import yaml

        (run_dir / "run.yaml").write_text(yaml.dump(run_yaml), encoding="utf-8")
    except ImportError:
        # Write as JSON with .yaml extension — tests can still run
        (run_dir / "run.yaml").write_text(json.dumps(run_yaml), encoding="utf-8")

    return run_dir


def _run_ingest(runner: CliRunner, project: Path, run_dir: Path) -> Any:
    """Run dde hypex ingest and return the result."""
    return runner.invoke(
        cli,
        ["--project", str(project), "hypex", "ingest", str(run_dir)],
        catch_exceptions=False,
    )


def _run_analyze(runner: CliRunner, project: Path, artifact: str) -> Any:
    """Run dde hypex analyze and return the result."""
    return runner.invoke(
        cli,
        ["--project", str(project), "hypex", "analyze", artifact],
        catch_exceptions=False,
    )


# ---------------------------------------------------------------------------
# Tests: Relay Code Registration
# ---------------------------------------------------------------------------


def test_all_nine_relay_codes_registered():
    """All 9 hypex.* relay codes must be registered in RELAY_CODES."""
    expected_codes = [
        "hypex.citation_manifest_absent",
        "hypex.composite_ranking",
        "hypex.integrity_violations",
        "hypex.pacing_uncoordinated",
        "hypex.phantom_citations_present",
        "hypex.quarantined_excluded",
        "hypex.run_aborted",
        "hypex.run_not_converged",
        "hypex.unrated_hypotheses",
    ]
    for code in expected_codes:
        assert code in provenance.RELAY_CODES, (
            f"relay code {code!r} not registered in RELAY_CODES"
        )


def test_relay_codes_alphabetical():
    """hypex relay codes should be in alphabetical order."""
    hypex_codes = [k for k in provenance.RELAY_CODES if k.startswith("hypex.")]
    assert hypex_codes == sorted(hypex_codes), (
        f"hypex relay codes are not alphabetical: {hypex_codes}"
    )


# ---------------------------------------------------------------------------
# Tests: Threshold Set
# ---------------------------------------------------------------------------


def test_hypex_threshold_set_declared():
    """hypex@1.0 must be declared in the threshold registry."""
    sets = declared_sets()
    assert "hypex" in sets, "hypex threshold set not declared"
    ts = sets["hypex"]
    assert ts.version == "1.0"
    assert ts.tag == "hypex@1.0"


def test_hypex_threshold_resolved_values():
    """The three resolved thresholds must have specific values."""
    sets = declared_sets()
    ts = sets["hypex"]
    assert ts.values["min_matches"] == 5
    assert ts.values["min_win_rate"] == 0.5
    assert ts.values["max_phantom_citations"] == 0


def test_hypex_threshold_unresolved_values():
    """Three thresholds must be UNRESOLVED, not guessed."""
    sets = declared_sets()
    ts = sets["hypex"]
    assert ts.values["elo_decisive_gap"] is UNRESOLVED
    assert ts.values["max_suspect_citations"] is UNRESOLVED
    assert ts.values["min_safety_score"] is UNRESOLVED


# ---------------------------------------------------------------------------
# Tests: Ingest — Happy Path
# ---------------------------------------------------------------------------


def test_ingest_happy_path():
    """Valid run dir → correct dde.hypex.v1 schema output."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(base, n_hypotheses=3, include_pacing=True)

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0, f"ingest failed: {result.output}"

        # Check normalised artifact exists
        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        assert len(json_files) == 1, f"expected 1 hypex.json, got {len(json_files)}"

        record = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert record["schema"] == "dde.hypex.v1"
        assert record["hypex_run_id"] == "test-run"
        assert len(record["hypotheses"]) == 3
        assert record["observed"]["n_hypotheses"] == 3
        assert record["observed"]["n_matches"] == 2
        assert record["termination"]["declared"] is True
        assert record["termination"]["reason"] == "converged"

        # Check sidecar exists
        meta_files = list(hyp_dir.glob("hx-*.meta.json"))
        assert len(meta_files) == 1


def test_ingest_observed_counts_from_datastore():
    """observed counts must be derived from the datastore, not run.yaml.

    Criterion 10 analogue: observed.n_matches != declared_budgets.max_matches.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(base, n_hypotheses=3, include_pacing=True)

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        record = json.loads(json_files[0].read_text(encoding="utf-8"))

        # observed counts come from datastore
        assert record["observed"]["n_matches"] == 2  # from 3 hypotheses, 2 matches
        # declared budgets from run.yaml (carried but non-authoritative)
        assert record["declared_budgets"]["max_matches"] == 200

        # They must differ — proving §3.6 is honoured
        assert (
            record["observed"]["n_matches"] != record["declared_budgets"]["max_matches"]
        )


# ---------------------------------------------------------------------------
# Tests: Ingest — Integrity
# ---------------------------------------------------------------------------


def test_ingest_dangling_match_ref():
    """Dangling match reference → populates integrity.dangling_match_refs."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_pacing=True,
            dangling_match_ref="H-9999",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        record = json.loads(json_files[0].read_text(encoding="utf-8"))

        # Dangling ref from M-0001.a = H-9999
        assert len(record["integrity"]["dangling_match_refs"]) > 0
        assert any(
            "H-9999" in ref for ref in record["integrity"]["dangling_match_refs"]
        )


# ---------------------------------------------------------------------------
# Tests: Ingest — Aborted Run
# ---------------------------------------------------------------------------


def test_ingest_aborted_run():
    """Missing termination.json → termination.declared: false."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_termination=False,
            include_pacing=True,
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        record = json.loads(json_files[0].read_text(encoding="utf-8"))

        assert record["termination"]["declared"] is False
        assert record["termination"]["reason"] == "aborted"


# ---------------------------------------------------------------------------
# Tests: Analyze
# ---------------------------------------------------------------------------


def test_analyze_aborted_fires_run_aborted():
    """Aborted run analysis → fires hypex.run_aborted."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_termination=False,
            include_pacing=True,
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        # Check analysis file
        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        assert len(analysis_files) == 1
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relays = analysis.get("mandatory_relays", [])
        relay_codes = [r["code"] for r in relays]
        assert "hypex.run_aborted" in relay_codes


def test_analyze_unconverged_verdict():
    """Budget-exhausted → unconverged verdict + run_not_converged relay."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_pacing=True,
            termination_reason="budget_exhausted",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        assert analysis["assessment"]["verdict"] == "unconverged"
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.run_not_converged" in relay_codes


def test_analyze_leader_gap_is_decisive_null():
    """leader_gap_is_decisive must be null when elo_decisive_gap is UNRESOLVED.

    It must NOT fall back to 50.0.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_pacing=True,
            elos=[1600.0, 1500.0, 1400.0],
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        # With elo_decisive_gap UNRESOLVED, leader_gap_is_decisive MUST be null
        assert analysis["assessment"]["leader_gap_is_decisive"] is None


def test_analyze_integrity_violations_relay():
    """Dangling match ref → fires hypex.integrity_violations."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_pacing=True,
            dangling_match_ref="H-9999",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.integrity_violations" in relay_codes


def test_analyze_quarantined_excluded_relay():
    """Quarantined hypotheses → fires hypex.quarantined_excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
            include_quarantine=True,
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.quarantined_excluded" in relay_codes


def test_analyze_pacing_uncoordinated_missing():
    """Missing pacing.json → fires hypex.pacing_uncoordinated."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        # No pacing file
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=False,
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.pacing_uncoordinated" in relay_codes


def test_analyze_pacing_uncoordinated_bad_tier():
    """pacing.json with tier != "shared" → fires hypex.pacing_uncoordinated."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
            pacing_tier="local",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.pacing_uncoordinated" in relay_codes


# ---------------------------------------------------------------------------
# Tests: Assessment Core
# ---------------------------------------------------------------------------


def test_assessment_core_score_is_object():
    """score must be {value, basis} not a bare number (criterion 22)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(base, n_hypotheses=3, include_pacing=True)

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        core = analysis["assessment"]["assessment_core"]
        assert core["schema"] == "dde.hypothesis-assessment.v1"
        assert core["strategy"] == "hypex"

        for candidate in core["candidates"]:
            score = candidate["score"]
            if score is not None:
                assert isinstance(score, dict), (
                    f"score must be a dict, got {type(score).__name__}"
                )
                assert "value" in score, "score must have 'value' field"
                assert "basis" in score, "score must have 'basis' field"
                assert score["basis"] == "hypex-elo@1.0"
            # score: null is acceptable for unrated
            assert candidate["origin"] == "generated"


def test_assessment_core_unrated_score_null():
    """Unrated hypotheses must have score: null, not a number."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        # Include ratings only for first 2 of 3 hypotheses
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_ratings=False,
            include_pacing=True,
        )
        # Write ratings only for H-0001 and H-0002
        ratings = _make_ratings(["H-0001", "H-0002"], elos=[1600.0, 1500.0])
        (run_dir / "ratings" / "epoch-0.json").write_text(
            json.dumps(ratings, indent=2), encoding="utf-8"
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        core = analysis["assessment"]["assessment_core"]
        # Find H-0003 — it should have score: null
        h3 = next(c for c in core["candidates"] if c["candidate_id"] == "H-0003")
        assert h3["score"] is None, (
            f"unrated hypothesis should have null score, got {h3['score']}"
        )
        assert h3["rank"] is None, (
            f"unrated hypothesis should have null rank, got {h3['rank']}"
        )


# ---------------------------------------------------------------------------
# Tests: Analysis file structure
# ---------------------------------------------------------------------------


def test_analysis_has_threshold_set():
    """Analysis must record threshold_set: hypex@1.0."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(base, n_hypotheses=2, include_pacing=True)

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        assert analysis["threshold_set"] == "hypex@1.0"


def test_analysis_records_unresolved_thresholds():
    """Analysis must record which thresholds are UNRESOLVED."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(base, n_hypotheses=2, include_pacing=True)

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        unresolved = analysis.get("thresholds_unresolved", [])
        assert "elo_decisive_gap" in unresolved
        assert "max_suspect_citations" in unresolved
        assert "min_safety_score" in unresolved


# ---------------------------------------------------------------------------
# Tests: Schema gate on analyze
# ---------------------------------------------------------------------------


def test_analyze_rejects_wrong_schema():
    """analyze must reject artifacts with wrong schema."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        hyp_dir = project / "raw" / "hypotheses"

        # Write a fake artifact with wrong schema
        fake = hyp_dir / "hx-fake.hypex.json"
        fake.write_text(json.dumps({"schema": "dde.coscientist.v1"}, indent=2))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "hypex", "analyze", str(fake)],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Tests: Converged run with shared pacing does NOT fire pacing relay
# ---------------------------------------------------------------------------


def test_converged_shared_pacing_no_pacing_relay():
    """A converged run with shared pacing must NOT fire pacing_uncoordinated.

    Criterion 27: no relay fires unconditionally.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_pacing=True,
            pacing_tier="shared",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.pacing_uncoordinated" not in relay_codes


def test_converged_no_integrity_no_quarantine():
    """A clean converged run fires no defect relays.

    Criterion 27: verifies relays don't fire unconditionally.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_pacing=True,
            include_quarantine=False,
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        # Converged run with no integrity issues and no quarantine:
        assert "hypex.integrity_violations" not in relay_codes
        assert "hypex.quarantined_excluded" not in relay_codes
        assert "hypex.run_aborted" not in relay_codes


# ---------------------------------------------------------------------------
# Tests: Unrated hypotheses relay
# ---------------------------------------------------------------------------


def test_analyze_unrated_hypotheses_relay():
    """Unrated hypotheses → fires hypex.unrated_hypotheses."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=3,
            include_ratings=False,
            include_pacing=True,
        )
        # Only rate H-0001 and H-0002
        ratings = _make_ratings(["H-0001", "H-0002"], elos=[1600.0, 1500.0])
        (run_dir / "ratings" / "epoch-0.json").write_text(
            json.dumps(ratings, indent=2), encoding="utf-8"
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.unrated_hypotheses" in relay_codes


# ---------------------------------------------------------------------------
# Tests: Pacing persistence (Finding 1 — pacing survives run dir deletion)
# ---------------------------------------------------------------------------


def test_pacing_persisted_in_record():
    """Pacing data must be stored in the normalised record, not only on disk."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
            pacing_tier="shared",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        record = json.loads(json_files[0].read_text(encoding="utf-8"))

        assert "pacing" in record, "pacing must be persisted in the record"
        assert record["pacing"] is not None
        assert record["pacing"]["tier"] == "shared"


def test_pacing_relay_after_run_dir_deleted():
    """After deleting the run dir, analyze must NOT false-positive pacing_uncoordinated.

    This is the core regression for Finding 1: pacing is now read from
    the record, not from the filesystem.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
            pacing_tier="shared",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        # Delete the original run directory (simulates archival/cleanup)
        import shutil

        shutil.rmtree(run_dir)

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.pacing_uncoordinated" not in relay_codes, (
            "pacing_uncoordinated must NOT fire when pacing was properly "
            "coordinated — even after the run directory is deleted"
        )


# ---------------------------------------------------------------------------
# Tests: Composite ranking (Finding 6, criterion 14)
# ---------------------------------------------------------------------------


def test_composite_ranking_fires():
    """composite_preset in run.yaml + composite values → fires hypex.composite_ranking."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
        )

        # Patch run.yaml to include composite_preset
        run_yaml_path = run_dir / "run.yaml"
        try:
            import yaml

            run_yaml = yaml.safe_load(run_yaml_path.read_text(encoding="utf-8"))
        except ImportError:
            run_yaml = json.loads(run_yaml_path.read_text(encoding="utf-8"))
        run_yaml["composite_preset"] = "balanced-v2"
        try:
            import yaml

            run_yaml_path.write_text(yaml.dump(run_yaml), encoding="utf-8")
        except ImportError:
            run_yaml_path.write_text(json.dumps(run_yaml), encoding="utf-8")

        # Add composite values to hypotheses
        for f in (run_dir / "hypotheses").glob("*.json"):
            h = json.loads(f.read_text(encoding="utf-8"))
            h["composite"] = 0.85
            f.write_text(json.dumps(h, indent=2), encoding="utf-8")

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.composite_ranking" in relay_codes


def test_no_composite_no_relay():
    """No composite_preset and no composite values → does NOT fire composite_ranking."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.composite_ranking" not in relay_codes


# ---------------------------------------------------------------------------
# Tests: Roster (Finding 6, criterion 16)
# ---------------------------------------------------------------------------


def test_roster_ingested():
    """meta/roster.ndjson with 2 entries → record["roster"] has 2 entries."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
        )

        # Write roster.ndjson
        roster_path = run_dir / "meta" / "roster.ndjson"
        entries = [
            {"agent_id": "agent-1", "role": "hypothesis-generator"},
            {"agent_id": "agent-2", "role": "judge"},
        ]
        roster_path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        record = json.loads(json_files[0].read_text(encoding="utf-8"))

        assert len(record["roster"]) == 2
        agent_ids = [r["agent_id"] for r in record["roster"]]
        assert "agent-1" in agent_ids
        assert "agent-2" in agent_ids


# ---------------------------------------------------------------------------
# Tests: Archive verification (Finding 6)
# ---------------------------------------------------------------------------


def test_ingest_produces_archive():
    """Happy path ingest must produce a .tar.zst archive file."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(base, n_hypotheses=2, include_pacing=True)

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        archives = list(hyp_dir.glob("hx-*.run.tar.zst"))
        assert len(archives) == 1, f"expected 1 .tar.zst archive, got {len(archives)}"
        assert archives[0].stat().st_size > 0


# ---------------------------------------------------------------------------
# Tests: Negative relay — run_not_converged (Finding 6)
# ---------------------------------------------------------------------------


def test_converged_run_no_run_not_converged_relay():
    """A converged run must NOT fire hypex.run_not_converged."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = _make_project(base)
        run_dir = _make_run_dir(
            base,
            n_hypotheses=2,
            include_pacing=True,
            termination_reason="converged",
        )

        runner = CliRunner()
        result = _run_ingest(runner, project, run_dir)
        assert result.exit_code == 0

        hyp_dir = project / "raw" / "hypotheses"
        json_files = list(hyp_dir.glob("hx-*.hypex.json"))
        artifact = str(json_files[0])

        result = _run_analyze(runner, project, artifact)
        assert result.exit_code == 0

        analysis_files = list(hyp_dir.glob("hx-*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypex.run_not_converged" not in relay_codes


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
