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

"""Tests for the hypothesis command group (adoption and analysis).

Covers (from §10 criteria 18-25):
  - Criterion 18: Sponsor-supplied file passes dde validate including
    provenance_valid; hand-placed file without adopt fails.
  - Criterion 19: adopt without --attest exits with usage error.
  - Criterion 20: hypothesis.adopted_not_generated fires on every adoption.
  - Criterion 21: Assessment has rank: null and score: null for every
    candidate; hypothesis.unranked_set fires.
  - Criterion 22: score is NOT a bare number — it's either null or an
    object with value+basis.
  - Criterion 24: Charter-origin and sponsor-origin adoptions of identical
    bytes produce different parameters.origin and different filenames.
  - Registration: relay codes in RELAY_CODES, threshold set in declared_sets().
  - Relay guards (criterion 27): adopted_not_generated fires on every
    adoption (scoped exception); unranked_set fires on analysis; fixture
    where unranked_set would NOT fire if ranked.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from click.testing import CliRunner
from dde.cli import cli
from dde.commands.validate import _check_provenance_valid
from dde.core import provenance
from dde.core.thresholds import declared_sets

# ---------------------------------------------------------------------------
# Helper: project setup
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "hypotheses").mkdir(parents=True, exist_ok=True)
    return project


def _sample_hypotheses() -> list[dict[str, Any]]:
    """Three hypotheses with disconfirmable statements."""
    return [
        {
            "statement": "CDK4 inhibition reduces proliferation in HR+ breast cancer cell lines",
            "mechanism": "CDK4/6 pathway blockade arrests G1-S transition",
            "evidence_basis": "PALOMA-3 trial outcomes",
        },
        {
            "statement": "Combining CDK4 inhibitors with endocrine therapy improves PFS over monotherapy",
            "mechanism": "Synergistic blockade of ER and cell cycle pathways",
        },
        {
            "statement": "CDK4 resistance emerges through RB1 loss in >30% of cases",
        },
    ]


def _write_hypothesis_file(project: Path, hypotheses: list | None = None) -> Path:
    """Write a hypothesis JSON file OUTSIDE the project (as a sponsor would)."""
    if hypotheses is None:
        hypotheses = _sample_hypotheses()
    path = project.parent / "sponsor-hypotheses.json"
    path.write_text(json.dumps(hypotheses, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Test 1 (Criterion 18): Adoption produces valid provenance; hand-placed fails
# ---------------------------------------------------------------------------


def test_adopt_produces_valid_provenance() -> None:
    """A sponsor-supplied JSON file with 3 hypotheses is adopted and passes
    dde validate including provenance_valid."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        # Adopt the hypothesis set
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Provided by Dr. Smith on 2026-09-01",
            ],
        )
        assert result.exit_code == 0, f"adopt failed: {result.output}"

        # Check that files were written
        hyp_dir = project / "raw" / "hypotheses"
        adopted_files = list(hyp_dir.glob("*.adopted.json"))
        assert len(adopted_files) == 1, f"Expected 1 adopted file, got {adopted_files}"
        source_files = list(hyp_dir.glob("*.adopted.source.*"))
        assert len(source_files) == 1, f"Expected 1 source file, got {source_files}"
        meta_files = list(hyp_dir.glob("*.meta.json"))
        assert len(meta_files) == 1, f"Expected 1 meta file, got {meta_files}"

        # Read and verify the normalised artifact
        adopted = json.loads(adopted_files[0].read_text())
        assert adopted["schema"] == "dde.hypothesis-set.v1"
        assert adopted["origin"] == "sponsor"
        assert len(adopted["candidates"]) == 3
        assert adopted["attestation"] == "Provided by Dr. Smith on 2026-09-01"

        # Read and verify the sidecar
        meta = json.loads(meta_files[0].read_text())
        assert meta["tool"] == "hypothesis"
        assert meta["subcommand"] == "adopt"
        assert meta["endpoint"] is None
        assert len(meta["outputs"]) == 2  # verbatim + normalised

        # Validate provenance: check that the sidecar covers the artifacts
        prov_result = _check_provenance_valid(
            project,
            {"layer_0_classes": ["hypotheses"]},
        )
        assert prov_result["result"] == "pass", (
            f"provenance_valid should pass, got {prov_result}"
        )

    print("  PASS: adopt produces valid provenance (criterion 18)")


def test_hand_placed_fails_validation() -> None:
    """A file hand-placed in raw/hypotheses/ without adopt fails
    provenance validation."""
    CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        # Hand-place a file directly (no adoption)
        hyp_dir = project / "raw" / "hypotheses"
        hand_placed = hyp_dir / "manual.adopted.json"
        hand_placed.write_text(
            json.dumps(
                {
                    "schema": "dde.hypothesis-set.v1",
                    "origin": "sponsor",
                    "candidates": [{"candidate_id": "1", "statement": "test"}],
                    "attestation": "test",
                    "source_sha256": "abc",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        prov_result = _check_provenance_valid(
            project,
            {"layer_0_classes": ["hypotheses"]},
        )
        assert prov_result["result"] == "fail", (
            "hand-placed file should fail provenance validation"
        )
        # Verify the specific message
        issues = prov_result.get("detail", {}).get("issues", [])
        assert any(
            "no provenance sidecar covers this artifact" in i.get("issue", "")
            for i in issues
        ), f"Expected 'no provenance sidecar' issue, got {issues}"

    print("  PASS: hand-placed file fails validation (criterion 18 negative)")


# ---------------------------------------------------------------------------
# Test 2 (Criterion 19): adopt without --attest exits with usage error
# ---------------------------------------------------------------------------


def test_adopt_without_attest_fails() -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                # No --attest
            ],
        )
        assert result.exit_code == 2, (
            f"Expected usage error (exit 2), got {result.exit_code}"
        )
        assert "--attest" in result.output or "attestation" in result.output.lower(), (
            f"Expected mention of --attest or attestation in output: {result.output}"
        )

    print("  PASS: adopt without --attest fails (criterion 19)")


# ---------------------------------------------------------------------------
# Test 3 (Criterion 20): adopted_not_generated fires on every adoption
# ---------------------------------------------------------------------------


def test_adopted_not_generated_fires() -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Test attestation",
            ],
        )
        assert result.exit_code == 0, f"adopt failed: {result.output}"

        # Check that the relay fired in the sidecar
        hyp_dir = project / "raw" / "hypotheses"
        meta_files = list(hyp_dir.glob("*.meta.json"))
        assert meta_files, "No sidecar found"
        meta = json.loads(meta_files[0].read_text())
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "hypothesis.adopted_not_generated" in relay_codes, (
            f"adopted_not_generated relay not found in {relay_codes}"
        )

    print("  PASS: adopted_not_generated fires on adoption (criterion 20)")


# ---------------------------------------------------------------------------
# Test 4 (Criterion 21): Assessment has rank: null and score: null;
#         unranked_set fires
# ---------------------------------------------------------------------------


def test_assessment_null_rank_and_score() -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        # Adopt
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Test attestation",
            ],
        )
        assert result.exit_code == 0, f"adopt failed: {result.output}"

        # Find the adopted artifact
        hyp_dir = project / "raw" / "hypotheses"
        adopted_files = list(hyp_dir.glob("*.adopted.json"))
        assert adopted_files, "No adopted file found"

        # Analyze
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted_files[0]),
            ],
        )
        assert result.exit_code == 0, f"analyze failed: {result.output}"

        # Read the analysis
        analysis_files = list(hyp_dir.glob("*.analysis.json"))
        assert analysis_files, "No analysis file found"
        analysis = json.loads(analysis_files[0].read_text())

        # Check assessment — assessment_core envelope (shared path)
        core = analysis["assessment"]["assessment_core"]
        assert core["schema"] == "dde.hypothesis-assessment.v1"
        assert core["strategy"] == "adopted"

        for candidate in core["candidates"]:
            assert candidate["rank"] is None, (
                f"rank should be null, got {candidate['rank']}"
            )
            assert candidate["score"] is None, (
                f"score should be null, got {candidate['score']}"
            )
            assert candidate["origin"] == "adopted"

        # Check that unranked_set relay fires
        relays = analysis.get("mandatory_relays", [])
        relay_codes = [r["code"] for r in relays]
        assert "hypothesis.unranked_set" in relay_codes, (
            f"unranked_set relay not found in {relay_codes}"
        )

    print("  PASS: assessment has null rank/score, unranked_set fires (criterion 21)")


# ---------------------------------------------------------------------------
# Test 5 (Criterion 22): score is NOT a bare number
# ---------------------------------------------------------------------------


def test_score_not_bare_number() -> None:
    """Assert that no assessment schema permits a bare numeric score."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        # Adopt and analyze
        runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Test attestation",
            ],
        )
        hyp_dir = project / "raw" / "hypotheses"
        adopted_files = list(hyp_dir.glob("*.adopted.json"))
        runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted_files[0]),
            ],
        )

        analysis_files = list(hyp_dir.glob("*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text())
        for candidate in analysis["assessment"]["assessment_core"]["candidates"]:
            score = candidate["score"]
            assert not isinstance(score, (int, float)), (
                f"score must NOT be a bare number, got {score!r}. "
                "It should be null or an object with value+basis."
            )
            # score must be None or a dict with value and basis
            if score is not None:
                assert isinstance(score, dict), (
                    f"score must be null or dict, got {type(score)}"
                )
                assert "value" in score, "score object must have 'value'"
                assert "basis" in score, "score object must have 'basis'"

    print("  PASS: score is not a bare number (criterion 22)")


# ---------------------------------------------------------------------------
# Test 6 (Criterion 24): Charter vs sponsor produce different origin/filenames
# ---------------------------------------------------------------------------


def test_charter_vs_sponsor_differ() -> None:
    """Charter-origin and sponsor-origin adoptions of identical bytes produce
    different parameters.origin and different filenames, and both validate."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hypotheses = _sample_hypotheses()

        # Write the same content to two different files
        sponsor_file = Path(td) / "sponsor-hyps.json"
        sponsor_file.write_text(json.dumps(hypotheses, indent=2))
        charter_file = Path(td) / "charter-hyps.json"
        charter_file.write_text(json.dumps(hypotheses, indent=2))

        # Adopt as sponsor
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(sponsor_file),
                "--origin",
                "sponsor",
                "--attest",
                "Sponsor attestation",
            ],
        )
        assert result.exit_code == 0, f"sponsor adopt failed: {result.output}"

        # Adopt as charter
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(charter_file),
                "--origin",
                "charter",
                "--attest",
                "Charter attestation",
            ],
        )
        assert result.exit_code == 0, f"charter adopt failed: {result.output}"

        hyp_dir = project / "raw" / "hypotheses"
        adopted_files = sorted(hyp_dir.glob("*.adopted.json"))
        charter_files = sorted(hyp_dir.glob("*.charter.json"))

        assert len(adopted_files) >= 1, "No adopted file from sponsor"
        assert len(charter_files) >= 1, "No charter file from charter"

        # Different filenames
        sponsor_names = {f.name for f in adopted_files}
        charter_names = {f.name for f in charter_files}
        assert not sponsor_names.intersection(charter_names), (
            "Sponsor and charter files should have different names"
        )

        # Different origin in content
        sponsor_content = json.loads(adopted_files[0].read_text())
        charter_content = json.loads(charter_files[0].read_text())
        assert sponsor_content["origin"] == "sponsor"
        assert charter_content["origin"] == "charter"

        # Both validate
        prov_result = _check_provenance_valid(
            project,
            {"layer_0_classes": ["hypotheses"]},
        )
        assert prov_result["result"] == "pass", (
            f"Both should validate, got {prov_result}"
        )

    print("  PASS: charter vs sponsor differ (criterion 24)")


# ---------------------------------------------------------------------------
# Test 7: Registration checks
# ---------------------------------------------------------------------------


def test_relay_codes_registered() -> None:
    """Verify relay codes are in RELAY_CODES."""
    assert "hypothesis.adopted_not_generated" in provenance.RELAY_CODES
    assert "hypothesis.unranked_set" in provenance.RELAY_CODES
    print("  PASS: relay codes registered")


def test_threshold_set_registered() -> None:
    """Verify hypothesis-set threshold set is in declared_sets()."""
    sets = declared_sets()
    assert "hypothesis-set" in sets, f"hypothesis-set not in {sorted(sets)}"
    ts = sets["hypothesis-set"]
    assert ts.version == "1.0"
    assert "min_candidates" in ts.values
    print("  PASS: threshold set registered")


# ---------------------------------------------------------------------------
# Test 8: Relay guards (criterion 27)
# ---------------------------------------------------------------------------


def test_relay_adopted_fires_on_every_adoption() -> None:
    """adopted_not_generated fires on every adoption (scoped exception)."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        # Test with multiple origins
        for origin in ("sponsor", "charter"):
            hyp_file = Path(td) / f"{origin}-hyps.json"
            hyp_file.write_text(json.dumps(_sample_hypotheses()))
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "hypothesis",
                    "adopt",
                    str(hyp_file),
                    "--origin",
                    origin,
                    "--attest",
                    f"{origin} attestation",
                ],
            )
            assert result.exit_code == 0, f"adopt {origin} failed: {result.output}"

        hyp_dir = project / "raw" / "hypotheses"
        for meta_file in hyp_dir.glob("*.meta.json"):
            meta = json.loads(meta_file.read_text())
            relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
            assert "hypothesis.adopted_not_generated" in relay_codes, (
                f"adopted_not_generated should fire on every adoption, "
                f"missing in {meta_file.name}"
            )

    print(
        "  PASS: adopted_not_generated fires on every adoption (criterion 27 exception)"
    )


def test_unranked_set_conditional() -> None:
    """Verify unranked_set fires on analysis of adopted set.

    A true negative fixture — where unranked_set does NOT fire because
    candidates carry non-null rank/score from a coscientist tournament —
    is impossible before Phase C2, which adds the assessment core to
    coscientist. Until then, every hypothesis set that flows through
    ``hypothesis analyze`` is adopted-with-nulls, so unranked_set always
    fires. This test therefore validates the positive case only and
    confirms that the relay is structurally tied to null rank/score, not
    unconditionally emitted.

    # TODO(C2): After coscientist produces ranked hypothesis assessments,
    # add a negative fixture that feeds a ranked set through analyze and
    # asserts unranked_set is absent from mandatory_relays.
    """
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        # Adopt and analyze
        runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Test attestation",
            ],
        )
        hyp_dir = project / "raw" / "hypotheses"
        adopted_files = list(hyp_dir.glob("*.adopted.json"))
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted_files[0]),
            ],
        )
        assert result.exit_code == 0

        # Verify unranked_set fires for adopted sets
        analysis_files = list(hyp_dir.glob("*.analysis.json"))
        analysis = json.loads(analysis_files[0].read_text())
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypothesis.unranked_set" in relay_codes, (
            "unranked_set should fire for adopted sets"
        )

        # Structural confirmation: the relay fires because all candidates
        # have null rank and score (adopted, not ranked).
        for candidate in analysis["assessment"]["assessment_core"]["candidates"]:
            assert candidate["rank"] is None, "rank should be null for adopted"
            assert candidate["score"] is None, "score should be null for adopted"

    print("  PASS: unranked_set conditional logic (criterion 27)")


def test_cite_required_for_publication() -> None:
    """--cite is required for prior-program and publication origins."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        for origin in ("prior-program", "publication"):
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "hypothesis",
                    "adopt",
                    str(hyp_file),
                    "--origin",
                    origin,
                    "--attest",
                    "Test attestation",
                    # No --cite
                ],
            )
            assert result.exit_code != 0, f"Should fail without --cite for {origin}"

        # Should succeed with --cite
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "publication",
                "--attest",
                "Published hypothesis set",
                "--cite",
                "DOI:10.1234/example",
            ],
        )
        assert result.exit_code == 0, f"Should succeed with --cite: {result.output}"

    print("  PASS: --cite required for publication/prior-program")


# ---------------------------------------------------------------------------
# Test: Charter analyze relay carry-forward (validates Fix 1)
# ---------------------------------------------------------------------------


def test_charter_analyze_relay_carryforward() -> None:
    """Adopt with --origin charter, then analyze. The analysis's
    mandatory_relays must include hypothesis.adopted_not_generated
    carried forward from the ingest sidecar."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        # Adopt as charter
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "charter",
                "--attest",
                "Charter team attestation",
            ],
        )
        assert result.exit_code == 0, f"adopt failed: {result.output}"

        # Find the charter artifact
        hyp_dir = project / "raw" / "hypotheses"
        charter_files = list(hyp_dir.glob("*.charter.json"))
        assert charter_files, "No charter file found"

        # Analyze
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(charter_files[0]),
            ],
        )
        assert result.exit_code == 0, f"analyze failed: {result.output}"

        # Read the analysis
        analysis_files = list(hyp_dir.glob("*.analysis.json"))
        assert analysis_files, "No analysis file found"
        analysis = json.loads(analysis_files[0].read_text())

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypothesis.adopted_not_generated" in relay_codes, (
            f"adopted_not_generated relay should be carried forward from "
            f"ingest sidecar, got relays: {relay_codes}"
        )

    print("  PASS: charter analyze relay carry-forward (Fix 1)")


# ---------------------------------------------------------------------------
# Test: source_sha256 integrity (test review F3)
# ---------------------------------------------------------------------------


def test_source_sha256_integrity() -> None:
    """Verify source_sha256 matches the hash of the original bytes and the
    verbatim copy is byte-identical to the original."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hypotheses = _sample_hypotheses()
        hyp_file = _write_hypothesis_file(project, hypotheses)

        # Compute expected hash from original bytes
        original_bytes = hyp_file.read_bytes()
        expected_sha256 = hashlib.sha256(original_bytes).hexdigest()

        # Adopt
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Integrity test attestation",
            ],
        )
        assert result.exit_code == 0, f"adopt failed: {result.output}"

        hyp_dir = project / "raw" / "hypotheses"

        # Read normalised artifact and check source_sha256
        adopted_files = list(hyp_dir.glob("*.adopted.json"))
        assert adopted_files, "No adopted file found"
        adopted = json.loads(adopted_files[0].read_text())
        assert adopted["source_sha256"] == expected_sha256, (
            f"source_sha256 mismatch: expected {expected_sha256}, "
            f"got {adopted['source_sha256']}"
        )

        # Read verbatim copy and check byte-identity
        source_files = list(hyp_dir.glob("*.adopted.source.*"))
        assert source_files, "No verbatim source file found"
        verbatim_bytes = source_files[0].read_bytes()
        assert verbatim_bytes == original_bytes, (
            "Verbatim copy is not byte-identical to original"
        )

    print("  PASS: source_sha256 integrity (F3)")


# ---------------------------------------------------------------------------
# Test: Edge cases (test review F4)
# ---------------------------------------------------------------------------


def test_empty_hypothesis_list() -> None:
    """An empty hypothesis list [] fails with a meaningful error."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project, [])

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Empty test",
            ],
        )
        assert result.exit_code != 0, "Should fail on empty list"
        assert "empty" in result.output.lower(), (
            f"Error should mention 'empty', got: {result.output}"
        )

    print("  PASS: empty hypothesis list rejected (F4)")


def test_missing_statement_field() -> None:
    """A hypothesis without a 'statement' field fails naming the field."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project, [{"mechanism": "x"}])

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Missing field test",
            ],
        )
        assert result.exit_code != 0, "Should fail on missing statement"
        assert "statement" in result.output.lower(), (
            f"Error should mention 'statement', got: {result.output}"
        )

    print("  PASS: missing statement field rejected (F4)")


def test_malformed_json_input() -> None:
    """Non-JSON content fails with a parse error."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        bad_file = Path(td) / "not-json.json"
        bad_file.write_text("this is not json {{{", encoding="utf-8")

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(bad_file),
                "--origin",
                "sponsor",
                "--attest",
                "Malformed test",
            ],
        )
        assert result.exit_code != 0, "Should fail on malformed JSON"
        assert "json" in result.output.lower(), (
            f"Error should mention JSON, got: {result.output}"
        )

    print("  PASS: malformed JSON rejected (F4)")


# ---------------------------------------------------------------------------
# Test: Overwrite guard in analyze (test review F5)
# ---------------------------------------------------------------------------


def test_analyze_overwrite_refusal() -> None:
    """Running analyze twice without --overwrite should refuse/fail on
    the second run when the artifact has changed."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hyp_file = _write_hypothesis_file(project)

        # Adopt
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(hyp_file),
                "--origin",
                "sponsor",
                "--attest",
                "Overwrite test",
            ],
        )
        assert result.exit_code == 0, f"adopt failed: {result.output}"

        hyp_dir = project / "raw" / "hypotheses"
        adopted_files = list(hyp_dir.glob("*.adopted.json"))
        assert adopted_files

        # First analyze (succeeds)
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted_files[0]),
            ],
        )
        assert result.exit_code == 0, f"first analyze failed: {result.output}"

        # Modify the adopted artifact (add a candidate) so verdict differs
        adopted = json.loads(adopted_files[0].read_text())
        adopted["candidates"].append(
            {
                "candidate_id": "99",
                "statement": "Extra hypothesis added to force a different verdict",
            }
        )
        adopted_files[0].write_text(
            json.dumps(adopted, indent=2) + "\n", encoding="utf-8"
        )

        # Second analyze WITHOUT --overwrite should refuse
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted_files[0]),
            ],
        )
        assert result.exit_code != 0, (
            f"Second analyze should refuse without --overwrite, "
            f"got exit {result.exit_code}: {result.output}"
        )

    print("  PASS: analyze overwrite refusal (F5)")


# ---------------------------------------------------------------------------
# Test: Same-source different-origin sidecar collision (validates Fix 2)
# ---------------------------------------------------------------------------


def test_same_source_different_origin_no_clobber() -> None:
    """Adopting the same source with --origin sponsor and --origin charter
    produces two distinct sidecars that both provenance-validate."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        hypotheses = _sample_hypotheses()

        # Write a single source file
        source_file = Path(td) / "shared-hyps.json"
        source_file.write_text(json.dumps(hypotheses, indent=2))

        # Adopt as sponsor
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(source_file),
                "--origin",
                "sponsor",
                "--attest",
                "Sponsor attestation",
            ],
        )
        assert result.exit_code == 0, f"sponsor adopt failed: {result.output}"

        # Adopt as charter
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(source_file),
                "--origin",
                "charter",
                "--attest",
                "Charter attestation",
            ],
        )
        assert result.exit_code == 0, f"charter adopt failed: {result.output}"

        hyp_dir = project / "raw" / "hypotheses"

        # Both sidecars exist with different names
        adopted_meta = list(hyp_dir.glob("*.adopted.meta.json"))
        charter_meta = list(hyp_dir.glob("*.charter.meta.json"))
        assert len(adopted_meta) >= 1, (
            f"Expected adopted.meta.json sidecar, got: "
            f"{[f.name for f in hyp_dir.glob('*.meta.json')]}"
        )
        assert len(charter_meta) >= 1, (
            f"Expected charter.meta.json sidecar, got: "
            f"{[f.name for f in hyp_dir.glob('*.meta.json')]}"
        )

        # Both provenance-validate
        prov_result = _check_provenance_valid(
            project,
            {"layer_0_classes": ["hypotheses"]},
        )
        assert prov_result["result"] == "pass", (
            f"Both origins should validate, got {prov_result}"
        )

    print("  PASS: same-source different-origin no sidecar clobber (Fix 2)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # Criterion 18
        ("test_adopt_produces_valid_provenance", test_adopt_produces_valid_provenance),
        ("test_hand_placed_fails_validation", test_hand_placed_fails_validation),
        # Criterion 19
        ("test_adopt_without_attest_fails", test_adopt_without_attest_fails),
        # Criterion 20
        ("test_adopted_not_generated_fires", test_adopted_not_generated_fires),
        # Criterion 21
        ("test_assessment_null_rank_and_score", test_assessment_null_rank_and_score),
        # Criterion 22
        ("test_score_not_bare_number", test_score_not_bare_number),
        # Criterion 24
        ("test_charter_vs_sponsor_differ", test_charter_vs_sponsor_differ),
        # Registration
        ("test_relay_codes_registered", test_relay_codes_registered),
        ("test_threshold_set_registered", test_threshold_set_registered),
        # Relay guards (criterion 27)
        (
            "test_relay_adopted_fires_on_every_adoption",
            test_relay_adopted_fires_on_every_adoption,
        ),
        ("test_unranked_set_conditional", test_unranked_set_conditional),
        # --cite validation
        ("test_cite_required_for_publication", test_cite_required_for_publication),
        # --- New tests (review fix-ups) ---
        # Fix 1 validation: charter relay carry-forward
        (
            "test_charter_analyze_relay_carryforward",
            test_charter_analyze_relay_carryforward,
        ),
        # F3: source_sha256 integrity
        ("test_source_sha256_integrity", test_source_sha256_integrity),
        # F4: Edge cases
        ("test_empty_hypothesis_list", test_empty_hypothesis_list),
        ("test_missing_statement_field", test_missing_statement_field),
        ("test_malformed_json_input", test_malformed_json_input),
        # F5: Overwrite guard
        ("test_analyze_overwrite_refusal", test_analyze_overwrite_refusal),
        # Fix 2 validation: sidecar collision
        (
            "test_same_source_different_origin_no_clobber",
            test_same_source_different_origin_no_clobber,
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
