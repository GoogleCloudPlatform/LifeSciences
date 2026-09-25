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

"""Regression tests for Silent Relay Omission fixes (Batch 2).

Covers:
  #191  docking analyze: upstream prepare relays restored via receptor_id lookup
  #198  litref analyze: missing response files raise ArtifactError, not NOT_FOUND
  #205  selectivity: string "false" for panel_complete does not suppress relay
  #216  tox margins: clinical PK sidecar relays forwarded alongside animal PK
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
# Helpers: project setup
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory for CliRunner tests."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    return project


def _write_json(path: Path, data: Any) -> Path:
    """Write JSON to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# #191  docking analyze: upstream prepare relays restored
# ---------------------------------------------------------------------------


def test_docking_analyze_finds_prepare_sidecar_by_receptor_id() -> None:
    """docking analyze looks up prepare sidecar by receptor_id, not by the
    composite {ligand}_{receptor_id} stem.  Upstream relays from the prepare
    sidecar must appear in the analysis output (#191).
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        dock_dir = project / "raw" / "docking"
        dock_dir.mkdir(parents=True, exist_ok=True)

        receptor_id = "6LU7"
        ligand = "compound-A"
        stem = f"{ligand}_{receptor_id}"

        # Write a docking_result.json with receptor_id field
        result_doc: dict[str, Any] = {
            "tool": "docking",
            "subcommand": "run",
            "receptor": f"{receptor_id}.receptor.pdbqt",
            "receptor_id": receptor_id,
            "ligand": f"{ligand}.pdbqt",
            "docking_mode": "rigid",
            "n_poses": 1,
            "exhaustiveness": 8,
            "poses": [
                {
                    "rank": 1,
                    "mode": 1,
                    "affinity_kcalmol": -9.2,
                    "rmsd_lb": 0.0,
                    "rmsd_ub": 0.0,
                }
            ],
            "best_score": -9.2,
        }
        _write_json(dock_dir / f"{stem}.docking_result.json", result_doc)

        # Write a prepare sidecar named by receptor_id (NOT by stem)
        # with a mandatory relay that should be forwarded.
        # Use a real registered relay code from provenance.RELAY_CODES.
        prepare_meta: dict[str, Any] = {
            "tool": "docking",
            "subcommand": "prepare",
            "mandatory_relays": [
                {
                    "code": "fpocket.conformation_dependent",
                    "message": "Druggability score depends on conformation",
                },
            ],
        }
        _write_json(dock_dir / f"{receptor_id}.prepare.meta.json", prepare_meta)

        # Also create a docking sidecar (named by composite stem)
        docking_meta: dict[str, Any] = {
            "tool": "docking",
            "subcommand": "run",
            "mandatory_relays": [
                {
                    "code": "docking.score_is_not_affinity",
                    "message": "Vina score is not affinity",
                },
            ],
        }
        _write_json(dock_dir / f"{stem}.docking.meta.json", docking_meta)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "docking",
                "analyze",
                str(dock_dir / f"{stem}.docking_result.json"),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Read the analysis output
        analysis_path = dock_dir / f"{stem}.docking.analysis.json"
        assert analysis_path.is_file(), f"Analysis not found: {analysis_path}"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]

        # The prepare sidecar relay must be present (looked up by receptor_id)
        assert "fpocket.conformation_dependent" in relay_codes, (
            f"fpocket.conformation_dependent relay from prepare sidecar should be "
            f"forwarded; got codes: {relay_codes}"
        )
        # The docking sidecar relay must also be present
        assert "docking.score_is_not_affinity" in relay_codes, (
            f"docking.score_is_not_affinity relay should also be present; "
            f"got codes: {relay_codes}"
        )
    print("  PASS: docking analyze finds prepare sidecar by receptor_id")


# ---------------------------------------------------------------------------
# #198  litref analyze: missing response files raise error
# ---------------------------------------------------------------------------


def test_litref_analyze_raises_on_missing_response_files() -> None:
    """litref analyze with a meta.json but no response files must raise
    ArtifactError, not silently report NOT_FOUND (#198).
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        lit_dir = project / "raw" / "literature"
        lit_dir.mkdir(parents=True, exist_ok=True)

        # Write a meta.json for a citation — but do NOT write any
        # response files (.trials.json or .literature.json)
        slug = "paloma-3"
        meta: dict[str, Any] = {
            "tool": "litref",
            "subcommand": "resolve",
            "parameters": {
                "identifier_kind": "name",
                "normalised": "PALOMA-3",
            },
            "mandatory_relays": [],
        }
        _write_json(lit_dir / f"{slug}.meta.json", meta)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "litref",
                "analyze",
                "PALOMA-3",
            ],
            catch_exceptions=True,  # Let the error propagate
        )

        # Must NOT exit 0 with "not_found" — that would be a silent omission.
        # Must raise ArtifactError (exit code 3).
        assert result.exit_code != 0, (
            f"Expected non-zero exit for missing response files, "
            f"got exit 0.\nOutput: {result.output}"
        )

        # Verify the error message mentions running resolve first
        assert "resolve" in (result.output or "").lower() or (
            result.exception and "resolve" in str(result.exception).lower()
        ), (
            f"Error should mention running 'dde litref resolve' first; "
            f"output: {result.output}, exception: {result.exception}"
        )
    print("  PASS: litref analyze raises on missing response files")


# ---------------------------------------------------------------------------
# #205  selectivity: string "false" must not suppress panel_incomplete relay
# ---------------------------------------------------------------------------


def test_selectivity_compare_rejects_string_panel_complete() -> None:
    """selectivity compare rejects panel_complete as a string, because the
    string "false" is truthy in Python and would suppress the relay (#205).
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        sel_dir = project / "raw" / "selectivity"
        sel_dir.mkdir(parents=True, exist_ok=True)

        # Write a panel file with panel_complete as string "false" — this
        # should trigger a validation error.
        panel: dict[str, Any] = {
            "schema": "dde.selectivity-panel.v1",
            "compound_id": "TEST-001",
            "panel_complete": "false",  # BUG: string, not boolean
            "primary_target": {
                "name": "CDK4",
                "activity_type": "IC50",
                "activity_value": 10.0,
                "activity_unit": "nM",
            },
            "off_targets": [
                {
                    "name": "CDK6",
                    "activity_type": "IC50",
                    "activity_value": 100.0,
                    "activity_unit": "nM",
                },
            ],
        }
        panel_path = _write_json(sel_dir / "bad-panel.json", panel)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "selectivity",
                "compare",
                str(panel_path),
            ],
            catch_exceptions=True,
        )

        # The compare step should reject the string panel_complete.
        assert result.exit_code != 0, (
            f"Expected validation error for string panel_complete, "
            f"got exit 0.\nOutput: {result.output}"
        )
        assert "panel_complete" in (result.output or ""), (
            f"Error should mention panel_complete; output: {result.output}"
        )
    print("  PASS: selectivity compare rejects string panel_complete")


def test_selectivity_analyze_fires_relay_when_panel_complete_not_true() -> None:
    """selectivity analyze fires panel_incomplete relay when panel_complete
    is not exactly True — e.g. when it's False or missing (#205).
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        sel_dir = project / "raw" / "selectivity"
        sel_dir.mkdir(parents=True, exist_ok=True)

        # Write a properly normalised selectivity artifact with
        # panel_complete = False (the relay SHOULD fire).
        record: dict[str, Any] = {
            "schema": "dde.selectivity-panel.v1",
            "source_file": "test.json",
            "compound_id": "TEST-001",
            "primary_target": {
                "name": "CDK4",
                "activity_type": "IC50",
                "activity_value": 10.0,
                "activity_unit": "nM",
            },
            "off_targets": [
                {
                    "name": "CDK6",
                    "activity_type": "IC50",
                    "activity_value": 100.0,
                    "activity_unit": "nM",
                },
            ],
            "panel_complete": False,
            "selectivity_ratios": [
                {
                    "off_target": "CDK6",
                    "off_target_value": 100.0,
                    "off_target_unit": "nM",
                    "primary_target": "CDK4",
                    "primary_value": 10.0,
                    "primary_unit": "nM",
                    "activity_type": "IC50",
                    "selectivity_ratio": 10.0,
                },
            ],
            "summary": {
                "n_off_targets": 1,
                "off_target_names": ["CDK6"],
                "activity_type": "IC50",
                "panel_complete": False,
            },
        }
        artifact_path = _write_json(
            sel_dir / "test-001-cdk4-selectivity.selectivity.json", record
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "selectivity",
                "analyze",
                str(artifact_path),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Find the analysis output
        analysis_files = list(sel_dir.glob("*.analysis.json"))
        assert analysis_files, f"No analysis file found in {sel_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "selectivity.panel_incomplete" in relay_codes, (
            f"selectivity.panel_incomplete relay should fire when "
            f"panel_complete is False; got codes: {relay_codes}"
        )
    print("  PASS: selectivity analyze fires relay when panel_complete is not True")


# ---------------------------------------------------------------------------
# #216  tox margins: clinical PK sidecar relays forwarded
# ---------------------------------------------------------------------------


def test_tox_margins_forwards_both_pk_sidecar_relays() -> None:
    """tox margins forwards mandatory_relays from BOTH animal PK and
    clinical PK sidecars into the output sidecar (#216).
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        tox_dir = project / "raw" / "tox"
        tox_dir.mkdir(parents=True, exist_ok=True)
        pk_dir = project / "raw" / "pk"
        pk_dir.mkdir(parents=True, exist_ok=True)

        study_id = "tox-study-1"

        # Write a tox repeat-dose artifact
        tox_doc: dict[str, Any] = {
            "schema": "dde.tox-repeat-dose.v1",
            "study_id": study_id,
            "species": "rat",
            "strain": "Wistar",
            "route": "oral",
            "duration_days": 28,
            "dose_levels": [
                {"dose_mg_kg": 10.0, "n_animals": 10},
                {"dose_mg_kg": 30.0, "n_animals": 10},
                {"dose_mg_kg": 100.0, "n_animals": 10},
            ],
            "noael_mg_kg": 30.0,
            "noael_basis": "hepatocellular hypertrophy at 100 mg/kg",
            "findings": [
                {
                    "dose_mg_kg": 100.0,
                    "organ": "liver",
                    "finding": "hepatocellular hypertrophy",
                    "severity": "minimal",
                    "incidence": "3/10",
                },
            ],
        }
        tox_path = _write_json(tox_dir / f"{study_id}.tox-repeat-dose.json", tox_doc)

        # Write animal PK NCA artifact with its sidecar
        animal_pk: dict[str, Any] = {
            "tool": "pk",
            "subcommand": "nca",
            "schema": "dde.pk-nca.v1",
            "study_id": "animal-pk-study",
            "species": "rat",
            "route": "oral",
            "parameters": {
                "cmax": 500.0,
                "cmax_units": "ng/mL",
                "tmax": 2.0,
                "tmax_units": "h",
                "auc_0_t": 3000.0,
                "auc_0_inf": 3200.0,
                "auc_units": "ng/mL * h",
                "auc_extrapolation_pct": 6.0,
                "half_life": 4.0,
                "half_life_units": "h",
                "lambda_z": 0.17,
                "clearance": 0.003,
                "clearance_units": "mg / (ng/mL * h)",
                "vd": 0.017,
                "vd_units": "mg / ng/mL",
                "bioavailability": "not determinable (single study)",
            },
            "blq_handling": {
                "method": "none (no BLQ values)",
                "blq_value": None,
                "n_blq_points": 0,
            },
            "terminal_phase": {
                "n_points": 5,
                "r_squared": 0.998,
                "time_range": [2.0, 24.0],
            },
        }
        animal_pk_path = _write_json(pk_dir / "animal-pk-study.pk-nca.json", animal_pk)
        # Animal PK sidecar with a distinctive relay (use a registered code)
        animal_pk_meta: dict[str, Any] = {
            "tool": "pk",
            "subcommand": "nca",
            "mandatory_relays": [
                {
                    "code": "pk.rule_of_exponents_uncorrected",
                    "message": "Animal PK allometric exponent outside simple range",
                },
            ],
        }
        _write_json(pk_dir / "animal-pk-study.pk-nca.meta.json", animal_pk_meta)

        # Write clinical PK NCA artifact with its sidecar
        clinical_pk: dict[str, Any] = {
            "tool": "pk",
            "subcommand": "nca",
            "schema": "dde.pk-nca.v1",
            "study_id": "clinical-pk-study",
            "species": "human",
            "route": "oral",
            "parameters": {
                "cmax": 200.0,
                "cmax_units": "ng/mL",
                "tmax": 3.0,
                "tmax_units": "h",
                "auc_0_t": 1500.0,
                "auc_0_inf": 1600.0,
                "auc_units": "ng/mL * h",
                "auc_extrapolation_pct": 5.0,
                "half_life": 6.0,
                "half_life_units": "h",
                "lambda_z": 0.12,
                "clearance": 0.001,
                "clearance_units": "mg / (ng/mL * h)",
                "vd": 0.009,
                "vd_units": "mg / ng/mL",
                "bioavailability": "not determinable (single study)",
            },
            "blq_handling": {
                "method": "none (no BLQ values)",
                "blq_value": None,
                "n_blq_points": 0,
            },
            "terminal_phase": {
                "n_points": 5,
                "r_squared": 0.997,
                "time_range": [3.0, 48.0],
            },
        }
        clinical_pk_path = _write_json(
            pk_dir / "clinical-pk-study.pk-nca.json", clinical_pk
        )
        # Clinical PK sidecar with a different distinctive relay (use a registered code)
        clinical_pk_meta: dict[str, Any] = {
            "tool": "pk",
            "subcommand": "nca",
            "mandatory_relays": [
                {
                    "code": "pk.single_species_scaling",
                    "message": "Clinical PK from single-species scaling only",
                },
            ],
        }
        _write_json(pk_dir / "clinical-pk-study.pk-nca.meta.json", clinical_pk_meta)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "tox",
                "margins",
                str(tox_path),
                str(animal_pk_path),
                "--clinical-pk",
                str(clinical_pk_path),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Find the margins sidecar
        meta_path = tox_dir / f"{study_id}.tox-margins.meta.json"
        assert meta_path.is_file(), f"Margins sidecar not found: {meta_path}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]

        # Animal PK relay must be present
        assert "pk.rule_of_exponents_uncorrected" in relay_codes, (
            f"pk.rule_of_exponents_uncorrected relay from animal PK sidecar "
            f"should be forwarded; got codes: {relay_codes}"
        )
        # Clinical PK relay must ALSO be present (the bug was that it was dropped)
        assert "pk.single_species_scaling" in relay_codes, (
            f"pk.single_species_scaling relay from clinical PK sidecar should "
            f"be forwarded; got codes: {relay_codes}"
        )
    print("  PASS: tox margins forwards both PK sidecar relays")
