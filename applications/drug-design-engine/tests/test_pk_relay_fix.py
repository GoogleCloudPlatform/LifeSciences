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

"""Tests for pk relay anti-pattern fix (#146).

Covers:
  1. pk nca does NOT emit pk.nca_assumes_linearity relay (now a note)
  2. pk nca DOES include method_caveat in the sidecar
  3. pk ddi does NOT emit pk.ddi_static_model relay
  4. pk ddi DOES include method_caveat in the sidecar
  5. pk scale with n_species==1 DOES emit pk.single_species_scaling relay
  6. pk scale with n_species>1 does NOT emit pk.allometric_not_pbpk relay
  7. pk scale (any) DOES include method_caveat in the sidecar
  8. The three removed codes are NOT in RELAY_CODES
  9. pk.single_species_scaling IS still in RELAY_CODES
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

# ---------------------------------------------------------------------------
# Helpers: project and fixture setup
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory for CliRunner tests."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "pk").mkdir(parents=True, exist_ok=True)
    # Write a minimal thresholds file (empty — defaults will apply)
    return project


def _make_study(project: Path, study_id: str = "test-study") -> Path:
    """Write a minimal pk-study.v1 JSON for NCA testing."""
    study: dict[str, Any] = {
        "schema": "dde.pk-study.v1",
        "study_id": study_id,
        "species": "rat",
        "route": "iv",
        "dose_mg_kg": 10.0,
        "dose_units": "mg/kg",
        "time_units": "h",
        "concentration_units": "ng/mL",
        "time_points": [0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 24.0],
        "concentrations": [0.0, 1200.0, 950.0, 600.0, 350.0, 180.0, 55.0, 18.0, 3.0],
        "blq_value": None,
        "body_weight_kg": 0.25,
    }
    pk_dir = project / "raw" / "pk"
    path = pk_dir / f"{study_id}.pk-study.json"
    path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    return path


def _make_nca_result(
    project: Path,
    study_id: str = "test-study",
    species: str = "rat",
) -> Path:
    """Write a minimal pk-nca.v1 JSON for scaling tests."""
    nca: dict[str, Any] = {
        "tool": "pk",
        "subcommand": "nca",
        "schema": "dde.pk-nca.v1",
        "study_id": study_id,
        "species": species,
        "route": "iv",
        "parameters": {
            "cmax": 1200.0,
            "cmax_units": "ng/mL",
            "tmax": 0.25,
            "tmax_units": "h",
            "auc_0_t": 4500.0,
            "auc_0_inf": 4800.0,
            "auc_units": "ng/mL * h",
            "auc_extrapolation_pct": 6.25,
            "half_life": 3.5,
            "half_life_units": "h",
            "lambda_z": 0.198,
            "clearance": 0.0021,
            "clearance_units": "mg / (ng/mL * h)",
            "vd": 0.0106,
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
            "r_squared": 0.9985,
            "time_range": [1.0, 24.0],
        },
    }
    pk_dir = project / "raw" / "pk"
    path = pk_dir / f"{study_id}.pk-nca.json"
    path.write_text(json.dumps(nca, indent=2) + "\n", encoding="utf-8")
    return path


def _make_ddi_input(project: Path, compound_id: str = "test-compound") -> Path:
    """Write a minimal pk-ddi-input.v1 JSON for DDI testing."""
    ddi_input: dict[str, Any] = {
        "schema": "dde.pk-ddi-input.v1",
        "compound_id": compound_id,
        "cmax_unbound": 50.0,
        "cmax_units": "nM",
        "cyp_inhibition": [
            {
                "isoform": "CYP3A4",
                "ic50_or_ki": 500.0,
                "value_type": "ic50",
                "units": "nM",
            },
        ],
    }
    pk_dir = project / "raw" / "pk"
    path = pk_dir / f"{compound_id}.ddi-input.json"
    path.write_text(json.dumps(ddi_input, indent=2) + "\n", encoding="utf-8")
    return path


def _read_meta(artifact_path: Path, suffix: str) -> dict[str, Any]:
    """Read the .meta.json sidecar for a given artifact."""
    meta_name = artifact_path.name.replace(suffix, f"{suffix[:-5]}.meta.json")
    meta_path = artifact_path.parent / meta_name
    if not meta_path.exists():
        # Try the direct pattern
        meta_path = artifact_path.with_suffix("").with_suffix(".meta.json")
    assert meta_path.is_file(), f"Meta sidecar not found: {meta_path}"
    return json.loads(meta_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. pk nca does NOT emit pk.nca_assumes_linearity relay
# ---------------------------------------------------------------------------


def test_nca_no_linearity_relay() -> None:
    """pk nca does NOT emit pk.nca_assumes_linearity as a mandatory relay."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        study_path = _make_study(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pk", "nca", str(study_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Read meta sidecar
        meta_path = project / "raw" / "pk" / "test-study.pk-nca.meta.json"
        assert meta_path.is_file(), f"Meta not found: {meta_path}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "pk.nca_assumes_linearity" not in relay_codes, (
            f"pk.nca_assumes_linearity should NOT be a relay; got codes: {relay_codes}"
        )
    print("  PASS: nca no linearity relay")


# ---------------------------------------------------------------------------
# 2. pk nca DOES include method_caveat in the sidecar
# ---------------------------------------------------------------------------


def test_nca_has_method_caveat() -> None:
    """pk nca DOES include method_caveat as a sidecar note."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        study_path = _make_study(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pk", "nca", str(study_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        meta_path = project / "raw" / "pk" / "test-study.pk-nca.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert "method_caveat" in meta, (
            f"method_caveat should be present in sidecar; keys: {list(meta.keys())}"
        )
        assert "linear PK" in meta["method_caveat"], (
            f"method_caveat should mention linear PK; got: {meta['method_caveat']}"
        )
    print("  PASS: nca has method_caveat")


# ---------------------------------------------------------------------------
# 3. pk ddi does NOT emit pk.ddi_static_model relay
# ---------------------------------------------------------------------------


def test_ddi_no_static_model_relay() -> None:
    """pk ddi does NOT emit pk.ddi_static_model as a mandatory relay."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        ddi_path = _make_ddi_input(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pk", "ddi", str(ddi_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        meta_path = project / "raw" / "pk" / "test-compound.pk-ddi.meta.json"
        assert meta_path.is_file(), f"Meta not found: {meta_path}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "pk.ddi_static_model" not in relay_codes, (
            f"pk.ddi_static_model should NOT be a relay; got codes: {relay_codes}"
        )
    print("  PASS: ddi no static_model relay")


# ---------------------------------------------------------------------------
# 4. pk ddi DOES include method_caveat in the sidecar
# ---------------------------------------------------------------------------


def test_ddi_has_method_caveat() -> None:
    """pk ddi DOES include method_caveat as a sidecar note."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        ddi_path = _make_ddi_input(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pk", "ddi", str(ddi_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        meta_path = project / "raw" / "pk" / "test-compound.pk-ddi.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert "method_caveat" in meta, (
            f"method_caveat should be present in sidecar; keys: {list(meta.keys())}"
        )
        assert "worst-case" in meta["method_caveat"], (
            f"method_caveat should mention worst-case; got: {meta['method_caveat']}"
        )
    print("  PASS: ddi has method_caveat")


# ---------------------------------------------------------------------------
# 5. pk scale with n_species==1 DOES emit pk.single_species_scaling relay
# ---------------------------------------------------------------------------


def test_scale_single_species_relay_fires() -> None:
    """pk scale with n_species==1 DOES emit pk.single_species_scaling relay."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_study(project)  # needed for body_weight_kg lookup
        nca_path = _make_nca_result(project, species="rat")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pk", "scale", str(nca_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Find scaling meta sidecar
        pk_dir = project / "raw" / "pk"
        meta_files = list(pk_dir.glob("*.pk-scaling.meta.json"))
        assert meta_files, f"No scaling meta found in {pk_dir}"
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "pk.single_species_scaling" in relay_codes, (
            f"pk.single_species_scaling should be a relay for single species; "
            f"got codes: {relay_codes}"
        )
    print("  PASS: scale single species relay fires")


# ---------------------------------------------------------------------------
# 6. pk scale with n_species>1 does NOT emit pk.allometric_not_pbpk relay
# ---------------------------------------------------------------------------


def test_scale_multi_species_no_allometric_relay() -> None:
    """pk scale with n_species>1 does NOT emit pk.allometric_not_pbpk relay."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        # Create two NCA results from different species
        _make_study(project, study_id="rat-study")
        nca_rat = _make_nca_result(project, study_id="rat-study", species="rat")

        _make_study(project, study_id="dog-study")
        nca_dog = _make_nca_result(project, study_id="dog-study", species="dog")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "pk",
                "scale",
                str(nca_rat),
                str(nca_dog),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        pk_dir = project / "raw" / "pk"
        meta_files = list(pk_dir.glob("*.pk-scaling.meta.json"))
        assert meta_files, f"No scaling meta found in {pk_dir}"
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "pk.allometric_not_pbpk" not in relay_codes, (
            f"pk.allometric_not_pbpk should NOT be a relay; got codes: {relay_codes}"
        )
    print("  PASS: scale multi species no allometric relay")


# ---------------------------------------------------------------------------
# 7. pk scale (any) DOES include method_caveat in the sidecar
# ---------------------------------------------------------------------------


def test_scale_has_method_caveat() -> None:
    """pk scale DOES include method_caveat as a sidecar note (any n_species)."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_study(project)
        nca_path = _make_nca_result(project, species="rat")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pk", "scale", str(nca_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        pk_dir = project / "raw" / "pk"
        meta_files = list(pk_dir.glob("*.pk-scaling.meta.json"))
        assert meta_files, f"No scaling meta found in {pk_dir}"
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))

        assert "method_caveat" in meta, (
            f"method_caveat should be present in sidecar; keys: {list(meta.keys())}"
        )
        assert "allometric scaling" in meta["method_caveat"], (
            f"method_caveat should mention allometric scaling; "
            f"got: {meta['method_caveat']}"
        )
    print("  PASS: scale has method_caveat")


# ---------------------------------------------------------------------------
# 8. The three removed codes are NOT in RELAY_CODES
# ---------------------------------------------------------------------------


def test_removed_codes_not_in_relay_codes() -> None:
    """The three unconditional relay codes are removed from RELAY_CODES."""
    removed_codes = [
        "pk.nca_assumes_linearity",
        "pk.ddi_static_model",
        "pk.allometric_not_pbpk",
    ]
    for code in removed_codes:
        assert code not in RELAY_CODES, (
            f"{code!r} should have been removed from RELAY_CODES"
        )
    print("  PASS: removed codes not in RELAY_CODES")


# ---------------------------------------------------------------------------
# 9. pk.single_species_scaling IS still in RELAY_CODES
# ---------------------------------------------------------------------------


def test_single_species_scaling_still_registered() -> None:
    """pk.single_species_scaling is still registered in RELAY_CODES."""
    assert "pk.single_species_scaling" in RELAY_CODES, (
        "pk.single_species_scaling should remain in RELAY_CODES"
    )
    assert isinstance(RELAY_CODES["pk.single_species_scaling"], str)
    assert len(RELAY_CODES["pk.single_species_scaling"]) > 0
    print("  PASS: single_species_scaling still registered")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_nca_no_linearity_relay", test_nca_no_linearity_relay),
        ("test_nca_has_method_caveat", test_nca_has_method_caveat),
        ("test_ddi_no_static_model_relay", test_ddi_no_static_model_relay),
        ("test_ddi_has_method_caveat", test_ddi_has_method_caveat),
        (
            "test_scale_single_species_relay_fires",
            test_scale_single_species_relay_fires,
        ),
        (
            "test_scale_multi_species_no_allometric_relay",
            test_scale_multi_species_no_allometric_relay,
        ),
        ("test_scale_has_method_caveat", test_scale_has_method_caveat),
        (
            "test_removed_codes_not_in_relay_codes",
            test_removed_codes_not_in_relay_codes,
        ),
        (
            "test_single_species_scaling_still_registered",
            test_single_species_scaling_still_registered,
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
