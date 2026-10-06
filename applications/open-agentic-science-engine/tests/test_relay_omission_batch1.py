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

"""Tests for silent relay omission fixes (batch 1).

Covers:
  #180 — allen.brain_region_expression_only fires even with empty datasets
  #184 — conservation.low_coverage fires when scored positions < canonical length
  #186 — compreg analyze raises when no registry response files exist
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


# ---------------------------------------------------------------------------
# Issue #180 — allen.brain_region_expression_only with empty datasets
# ---------------------------------------------------------------------------


def _make_allen_artifact(
    project: Path,
    query: str = "BRCA1",
    *,
    datasets: list[dict[str, Any]] | None = None,
    genes: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a minimal allen search artifact JSON."""
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()[:80]
    slug = slug or "allen-search"

    artifact: dict[str, Any] = {
        "schema": "dde.allen-search.v1",
        "query": {"gene": query, "organism": "Homo sapiens"},
        "genes": genes if genes is not None else [],
        "datasets": datasets if datasets is not None else [],
    }

    tx_dir = project / "raw" / "transcriptomics"
    tx_dir.mkdir(parents=True, exist_ok=True)
    path = tx_dir / f"{slug}.allen.artifact.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def test_allen_relay_fires_with_empty_datasets() -> None:
    """#180: allen.brain_region_expression_only MUST be present even when
    datasets is empty — the relay is a mandatory qualifier that prevents
    false negative inferences about peripheral tissue expression.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_allen_artifact(project, "BRCA1", datasets=[], genes=[])

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "allen", "analyze", "BRCA1"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Read the analysis file
        tx_dir = project / "raw" / "transcriptomics"
        analysis_files = list(tx_dir.glob("*.allen.analysis.json"))
        assert analysis_files, f"No analysis file found in {tx_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "allen.brain_region_expression_only" in relay_codes, (
            f"allen.brain_region_expression_only should fire even with empty "
            f"datasets; got relay codes: {relay_codes}"
        )
    print("  PASS: allen relay fires with empty datasets")


def test_allen_relay_fires_with_nonempty_datasets() -> None:
    """Sanity check: the relay also fires when datasets are present."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_allen_artifact(
            project,
            "BRCA1",
            datasets=[{"id": "ds1", "products": ["Mouse Brain"], "failed": False}],
            genes=[{"organism_name": "Homo sapiens"}],
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "allen", "analyze", "BRCA1"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        tx_dir = project / "raw" / "transcriptomics"
        analysis_files = list(tx_dir.glob("*.allen.analysis.json"))
        assert analysis_files, f"No analysis file found in {tx_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "allen.brain_region_expression_only" in relay_codes, (
            f"allen.brain_region_expression_only should fire with datasets; "
            f"got relay codes: {relay_codes}"
        )
    print("  PASS: allen relay fires with nonempty datasets")


# ---------------------------------------------------------------------------
# Issue #184 — conservation.low_coverage fires when coverage < 1.0
# ---------------------------------------------------------------------------


def _make_conservation_record(
    project: Path,
    query_name: str = "TEST_PROTEIN",
    *,
    positions: list[int] | None = None,
    canonical_length: int | None = None,
) -> Path:
    """Write a conservation.json with sparse positions.

    If positions is None, defaults to [10, 20, ..., 100] — 10 scored
    positions out of a 100-residue sequence.
    """
    if positions is None:
        positions = list(range(10, 101, 10))  # [10, 20, 30, ..., 100]

    residues = []
    for pos in positions:
        residues.append(
            {
                "position": pos,
                "amino_acid": "A",
                "score": 0.5,
                "grade": 5,
            }
        )

    record: dict[str, Any] = {
        "tool": "conservation",
        "subcommand": "compute",
        "query_name": query_name,
        "msa_file": "test.fasta",
        "n_sequences": 50,
        "n_positions": len(residues),
        "model": "JTT",
        "method": "bayesian",
        "residues": residues,
    }
    if canonical_length is not None:
        record["canonical_length"] = canonical_length

    genomics_dir = project / "raw" / "genomics"
    genomics_dir.mkdir(parents=True, exist_ok=True)
    path = genomics_dir / f"{query_name}.conservation.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def test_conservation_low_coverage_relay_fires() -> None:
    """#184: conservation.low_coverage MUST fire when only 10 out of 100
    positions are scored (coverage = 10%).
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        # 10 scored positions at [10, 20, ..., 100] with canonical_length=100
        _make_conservation_record(
            project,
            "TEST_PROTEIN",
            positions=list(range(10, 101, 10)),
            canonical_length=100,
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "conservation", "analyze", "TEST_PROTEIN"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Read the analysis file
        genomics_dir = project / "raw" / "genomics"
        analysis_files = list(genomics_dir.glob("*.conservation.analysis.json"))
        assert analysis_files, f"No analysis file found in {genomics_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        # Verify coverage is < 1.0
        coverage = analysis["metrics"]["coverage"]
        assert coverage < 1.0, (
            f"Coverage should be < 1.0 (expected ~0.1); got {coverage}"
        )
        assert abs(coverage - 0.1) < 0.01, (
            f"Coverage should be ~0.1 (10/100); got {coverage}"
        )

        # Verify low_coverage relay fires
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "conservation.low_coverage" in relay_codes, (
            f"conservation.low_coverage should fire for 10% coverage; "
            f"got relay codes: {relay_codes}"
        )
    print("  PASS: conservation low_coverage relay fires")


def test_conservation_coverage_without_canonical_length_field() -> None:
    """#184: When canonical_length is absent from an older record, coverage
    should be derived from max(position) rather than len(residues), so the
    low_coverage relay still fires.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        # No canonical_length in the record — the fix should derive it
        # from max(res["position"]) = 100, not len(residues) = 10
        _make_conservation_record(
            project,
            "TEST_PROTEIN",
            positions=list(range(10, 101, 10)),
            canonical_length=None,  # absent from record
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "conservation", "analyze", "TEST_PROTEIN"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        genomics_dir = project / "raw" / "genomics"
        analysis_files = list(genomics_dir.glob("*.conservation.analysis.json"))
        assert analysis_files, f"No analysis file found in {genomics_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        coverage = analysis["metrics"]["coverage"]
        assert coverage < 1.0, (
            f"Coverage should be < 1.0 even without canonical_length field; "
            f"got {coverage}"
        )

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "conservation.low_coverage" in relay_codes, (
            f"conservation.low_coverage should fire for sparse positions "
            f"even without canonical_length in record; got: {relay_codes}"
        )
    print("  PASS: conservation coverage works without canonical_length field")


# ---------------------------------------------------------------------------
# Issue #186 — compreg analyze raises when no registry response files exist
# ---------------------------------------------------------------------------


def _make_compreg_meta(project: Path, identifier: str = "aspirin") -> Path:
    """Write a minimal compreg resolve sidecar (meta.json) without any
    registry response files.
    """
    from dde.commands.compreg import _classify, _slug

    kind, value = _classify(identifier)
    slug = _slug(kind, value)

    meta: dict[str, Any] = {
        "tool": "compreg",
        "subcommand": "resolve",
        "identifier": identifier,
        "mandatory_relays": [],
    }

    compounds_dir = project / "raw" / "compounds"
    compounds_dir.mkdir(parents=True, exist_ok=True)
    path = compounds_dir / f"{slug}.meta.json"
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


def test_compreg_raises_when_no_registry_files() -> None:
    """#186: compreg analyze MUST raise an error when ALL registry response
    files are missing, not silently report 'not_found'.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_compreg_meta(project, "aspirin")
        # Deliberately do NOT create .registry-pubchem.json or
        # .registry-chembl.json files.

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "compreg", "analyze", "aspirin"],
            catch_exceptions=True,
        )

        # The command should fail — either a non-zero exit code or an
        # exception.  Before the fix, this would succeed with exit 0 and
        # outcome "not_found".
        assert result.exit_code != 0, (
            f"Expected non-zero exit (error for missing registry files), "
            f"but got exit {result.exit_code}\n{result.output}"
        )

        # Verify the error message mentions registry response files
        output = result.output or ""
        exception_text = str(result.exception) if result.exception else ""
        combined = output + exception_text
        assert (
            "no registry response files" in combined.lower()
            or "resolve" in combined.lower()
        ), (
            f"Error should mention missing registry files or suggest resolve; "
            f"got: {combined}"
        )
    print("  PASS: compreg raises when no registry files")


def test_compreg_succeeds_with_registry_files() -> None:
    """Sanity check: compreg analyze succeeds when registry files exist."""
    from click.testing import CliRunner
    from dde.cli import cli
    from dde.commands.compreg import _classify, _slug

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_compreg_meta(project, "aspirin")

        # Create a PubChem registry response
        kind, value = _classify("aspirin")
        slug = _slug(kind, value)
        compounds_dir = project / "raw" / "compounds"
        pubchem_response: dict[str, Any] = {
            "PropertyTable": {
                "Properties": [
                    {
                        "CID": 2244,
                        "MolecularFormula": "C9H8O4",
                        "MolecularWeight": "180.16",
                        "IUPACName": "2-acetyloxybenzoic acid",
                        "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                    }
                ]
            }
        }
        pubchem_path = compounds_dir / f"{slug}.registry-pubchem.json"
        pubchem_path.write_text(
            json.dumps(pubchem_response, indent=2) + "\n", encoding="utf-8"
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "compreg", "analyze", "aspirin"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
    print("  PASS: compreg succeeds with registry files")


# ---------------------------------------------------------------------------
# Runner (for manual execution outside pytest)
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        (
            "test_allen_relay_fires_with_empty_datasets",
            test_allen_relay_fires_with_empty_datasets,
        ),
        (
            "test_allen_relay_fires_with_nonempty_datasets",
            test_allen_relay_fires_with_nonempty_datasets,
        ),
        (
            "test_conservation_low_coverage_relay_fires",
            test_conservation_low_coverage_relay_fires,
        ),
        (
            "test_conservation_coverage_without_canonical_length_field",
            test_conservation_coverage_without_canonical_length_field,
        ),
        (
            "test_compreg_raises_when_no_registry_files",
            test_compreg_raises_when_no_registry_files,
        ),
        (
            "test_compreg_succeeds_with_registry_files",
            test_compreg_succeeds_with_registry_files,
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
