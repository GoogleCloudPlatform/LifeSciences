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

"""Tests for dde dossier export (#141).

Covers:
  - Export with tox records populates 2.6.6
  - Export with pk records populates 2.6.4
  - Missing section shows NOT_AVAILABLE, not blank
  - Relay forwarding collects upstream relays
  - Source references resolve back to artifacts
  - JSON output structure
  - Markdown output is readable
  - ICH references are included
  - Empty project produces all-gap export (no crash)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.dossier import (
    ICH_GUIDANCE_REFERENCES,
    _build_export,
    _export_to_json,
    _export_to_markdown,
    _export_to_tsv,
)
from dde.core.provenance import RELAY_CODES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    return project


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON artifact to *path*, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_tox_repeat_dose(project: Path, name: str = "compound") -> Path:
    """Write a minimal tox-repeat-dose artifact."""
    data: dict[str, Any] = {
        "schema": "dde.tox-repeat-dose.v1",
        "species": "rat",
        "route": "oral gavage",
        "duration": "28 days",
        "dose": "10, 30, 100 mg/kg/day",
        "noael": "30 mg/kg/day",
        "loael": "100 mg/kg/day",
        "findings": "Hepatocellular hypertrophy at 100 mg/kg/day",
        "work_order_id": "WO-001",
    }
    path = project / "raw" / "tox" / f"{name}.tox-repeat-dose.json"
    _write_json(path, data)
    return path


def _make_tox_genotox(project: Path, name: str = "compound") -> Path:
    """Write a minimal tox-genotox artifact."""
    data: dict[str, Any] = {
        "schema": "dde.tox-genotox.v1",
        "test_system": "Ames test, S. typhimurium TA98/TA100",
        "result": "Negative",
        "conclusion": "Not mutagenic under test conditions",
        "work_order_id": "WO-001",
    }
    path = project / "raw" / "tox" / f"{name}.tox-genotox.json"
    _write_json(path, data)
    return path


def _make_pk_study(project: Path, name: str = "compound") -> Path:
    """Write a minimal pk-study artifact."""
    data: dict[str, Any] = {
        "schema": "dde.pk-study.v1",
        "species": "rat",
        "route": "iv",
        "dose_mg_kg": 10.0,
        "dose_units": "mg/kg",
        "clearance": "15.2 mL/min/kg",
        "half_life": "3.2 h",
        "vd": "1.2 L/kg",
        "work_order_id": "WO-001",
    }
    path = project / "raw" / "pk" / f"{name}.pk-study.json"
    _write_json(path, data)
    return path


def _make_pk_nca(project: Path, name: str = "compound") -> Path:
    """Write a minimal pk-nca artifact."""
    data: dict[str, Any] = {
        "schema": "dde.pk-nca.v1",
        "species": "rat",
        "route": "iv",
        "dose_mg_kg": 10.0,
        "dose_units": "mg/kg",
        "parameters": {"AUC_inf": 1234.5, "Cmax": 456.7, "Tmax": 0.5},
        "work_order_id": "WO-001",
    }
    path = project / "raw" / "pk" / f"{name}.pk-nca.json"
    _write_json(path, data)
    return path


def _make_assay(project: Path, name: str = "compound") -> Path:
    """Write a minimal assay artifact."""
    data: dict[str, Any] = {
        "schema": "dde.assay.v1",
        "study_type": "Primary pharmacodynamics",
        "target": "Target X",
        "species": "human",
        "assay_type": "Radioligand binding",
        "result": "IC50 = 15 nM",
        "conclusion": "Potent inhibitor",
        "work_order_id": "WO-001",
    }
    path = project / "raw" / "assays" / f"{name}.assay.json"
    _write_json(path, data)
    return path


def _make_sidecar_with_relay(
    artifact_path: Path,
    relay_code: str,
    relay_message: str,
) -> Path:
    """Write a .meta.json sidecar with a mandatory relay next to an artifact."""
    meta_data: dict[str, Any] = {
        "tool": "test",
        "subcommand": "test",
        "mandatory_relays": [
            {"code": relay_code, "message": relay_message},
        ],
    }
    # For a file like compound.pk-study.json, the sidecar is
    # compound.pk-study.meta.json.
    base = artifact_path.name.rsplit(".json", 1)[0]
    meta_path = artifact_path.parent / f"{base}.meta.json"
    _write_json(meta_path, meta_data)
    return meta_path


# ===========================================================================
# Tests
# ===========================================================================


def test_export_with_tox_records_populates_2_6_6() -> None:
    """Tox artifacts map to CTD 2.6.6 subsections."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _make_tox_repeat_dose(project)
        _make_tox_genotox(project)

        export = _build_export(project, "WO-001")

        tox_section = next(s for s in export["sections"] if s["ctd_section"] == "2.6.6")
        repeat_dose = next(
            ss
            for ss in tox_section["subsections"]
            if ss["name"] == "Repeat-Dose Toxicity"
        )
        genotox = next(
            ss for ss in tox_section["subsections"] if ss["name"] == "Genotoxicity"
        )

        assert repeat_dose["status"] == "POPULATED"
        assert len(repeat_dose["entries"]) == 1
        assert repeat_dose["entries"][0]["species"] == "rat"
        assert repeat_dose["entries"][0]["noael"] == "30 mg/kg/day"

        assert genotox["status"] == "POPULATED"
        assert len(genotox["entries"]) == 1
        assert genotox["entries"][0]["result"] == "Negative"

        print("  PASS: tox records populate 2.6.6")


def test_export_with_pk_records_populates_2_6_4() -> None:
    """PK artifacts map to CTD 2.6.4 subsections."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _make_pk_study(project)
        _make_pk_nca(project)

        export = _build_export(project, "WO-001")

        pk_section = next(s for s in export["sections"] if s["ctd_section"] == "2.6.4")
        absorption = next(
            ss for ss in pk_section["subsections"] if ss["name"] == "Absorption"
        )

        assert absorption["status"] == "POPULATED"
        # pk-study.json and pk-nca.json both match for Absorption
        assert len(absorption["entries"]) >= 1

        # Check that entries have source references.
        for entry in absorption["entries"]:
            assert "source" in entry
            assert "artifact_path" in entry["source"]

        print("  PASS: pk records populate 2.6.4")


def test_missing_section_shows_not_available() -> None:
    """Sections without artifacts show NOT_AVAILABLE, not blank or omitted."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        # Only create tox artifacts — pk and pharmacology should be NOT_AVAILABLE.
        _make_tox_repeat_dose(project)

        export = _build_export(project, "WO-001")

        pk_section = next(s for s in export["sections"] if s["ctd_section"] == "2.6.4")
        for ss in pk_section["subsections"]:
            assert ss["status"] == "NOT_AVAILABLE", (
                f"Expected NOT_AVAILABLE for {ss['name']}, got {ss['status']}"
            )
            assert ss["reason"] == "No source artifact found"
            assert ss["entries"] == []

        pharm_section = next(
            s for s in export["sections"] if s["ctd_section"] == "2.6.2"
        )
        for ss in pharm_section["subsections"]:
            assert ss["status"] == "NOT_AVAILABLE", (
                f"Expected NOT_AVAILABLE for {ss['name']}, got {ss['status']}"
            )

        print("  PASS: missing sections show NOT_AVAILABLE")


def test_relay_forwarding_collects_upstream_relays() -> None:
    """Relays from source artifact sidecars are collected in the export."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        pk_path = _make_pk_study(project)

        _make_sidecar_with_relay(
            pk_path,
            "pk.single_species_scaling",
            "Single-species scaling applied.",
        )

        export = _build_export(project, "WO-001")

        assert export["relay_count"] > 0
        relay_codes = [r["code"] for r in export["relays"]]
        assert "pk.single_species_scaling" in relay_codes

        # Each forwarded relay must include source_artifact.
        for r in export["relays"]:
            assert "source_artifact" in r
            assert "code" in r
            assert "message" in r

        print("  PASS: relay forwarding collects upstream relays")


def test_source_references_resolve_to_artifacts() -> None:
    """Every entry's source.artifact_path is a real file under the project."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _make_pk_study(project)
        _make_tox_repeat_dose(project)

        export = _build_export(project, "WO-001")

        for section in export["sections"]:
            for ss in section["subsections"]:
                for entry in ss.get("entries", []):
                    artifact_path = entry["source"]["artifact_path"]
                    full_path = project / artifact_path
                    assert full_path.is_file(), (
                        f"Source reference does not resolve: {artifact_path}"
                    )

        print("  PASS: source references resolve to artifacts")


def test_json_output_structure() -> None:
    """JSON output has all required top-level keys and correct schema."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _make_pk_study(project)

        export = _build_export(project, "WO-001")

        assert export["schema"] == "dde.dossier-export.v1"
        assert export["work_order_id"] == "WO-001"
        assert export["format"] == "ctd"
        assert "scope_caveat" in export
        assert isinstance(export["sections"], list)
        assert isinstance(export["relays"], list)
        assert isinstance(export["relay_count"], int)

        # Validate JSON round-trip.
        rendered = _export_to_json(export)
        parsed = json.loads(rendered)
        assert parsed["schema"] == export["schema"]

        print("  PASS: JSON output structure is correct")


def test_markdown_output_is_readable() -> None:
    """Markdown output contains headers, tables, and NOT_AVAILABLE markers."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _make_pk_study(project)
        _make_tox_repeat_dose(project)

        export = _build_export(project, "WO-001")
        md = _export_to_markdown(export)

        # Must contain CTD section headers.
        assert "## 2.6.2" in md
        assert "## 2.6.4" in md
        assert "## 2.6.6" in md

        # Must contain NOT_AVAILABLE for missing sections.
        assert "NOT AVAILABLE" in md

        # Must contain table headers (markdown table syntax).
        assert "| --- |" in md

        # Must be non-empty.
        assert len(md) > 100

        print("  PASS: markdown output is readable")


def test_tsv_output_is_tabular() -> None:
    """TSV output renders tab-separated columns."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _make_tox_repeat_dose(project)

        export = _build_export(project, "WO-001")
        tsv = _export_to_tsv(export)

        # Must contain section markers.
        assert "# 2.6.6" in tsv

        # Must contain NOT_AVAILABLE for missing sections.
        assert "NOT_AVAILABLE" in tsv

        # Must contain tab characters in data rows.
        data_lines = [
            line
            for line in tsv.splitlines()
            if line and not line.startswith("#") and not line.startswith("##")
        ]
        tab_lines = [line for line in data_lines if "\t" in line]
        assert len(tab_lines) > 0

        print("  PASS: TSV output is tabular")


def test_ich_references_are_included() -> None:
    """Each CTD section includes its ICH guidance references."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))

        export = _build_export(project, "WO-001")

        for section in export["sections"]:
            ctd = section["ctd_section"]
            refs = section.get("guidance_references", [])

            expected = ICH_GUIDANCE_REFERENCES.get(ctd, [])
            assert len(refs) == len(expected), (
                f"Section {ctd}: expected {len(expected)} refs, got {len(refs)}"
            )

            expected_codes = {r["code"] for r in expected}
            actual_codes = {r["code"] for r in refs}
            assert actual_codes == expected_codes, (
                f"Section {ctd}: expected codes {expected_codes}, got {actual_codes}"
            )

        # Verify specific mappings from the brief.
        section_2_6_2 = next(
            s for s in export["sections"] if s["ctd_section"] == "2.6.2"
        )
        ref_codes_2_6_2 = {r["code"] for r in section_2_6_2["guidance_references"]}
        assert "ICH S7A" in ref_codes_2_6_2
        assert "ICH S7B" in ref_codes_2_6_2

        section_2_6_4 = next(
            s for s in export["sections"] if s["ctd_section"] == "2.6.4"
        )
        ref_codes_2_6_4 = {r["code"] for r in section_2_6_4["guidance_references"]}
        assert "ICH S3A" in ref_codes_2_6_4

        section_2_6_6 = next(
            s for s in export["sections"] if s["ctd_section"] == "2.6.6"
        )
        ref_codes_2_6_6 = {r["code"] for r in section_2_6_6["guidance_references"]}
        assert "ICH S2(R1)" in ref_codes_2_6_6
        assert "ICH M3(R2)" in ref_codes_2_6_6
        assert "ICH S5(R3)" in ref_codes_2_6_6

        print("  PASS: ICH references are included")


def test_empty_project_produces_all_gap_export() -> None:
    """An empty project produces a valid export with all NOT_AVAILABLE sections."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))

        export = _build_export(project, "WO-001")

        assert export["schema"] == "dde.dossier-export.v1"
        assert export["relay_count"] == 0
        assert export["relays"] == []

        for section in export["sections"]:
            for ss in section["subsections"]:
                assert ss["status"] == "NOT_AVAILABLE", (
                    f"Expected NOT_AVAILABLE for {ss['name']}, got {ss['status']}"
                )
                assert ss["reason"] == "No source artifact found"
                assert ss["entries"] == []

        # Must not crash on any output format.
        _export_to_json(export)
        _export_to_tsv(export)
        _export_to_markdown(export)

        print("  PASS: empty project produces all-gap export (no crash)")


def test_relay_code_registered() -> None:
    """dossier.relays_forwarded is registered in RELAY_CODES."""
    assert "dossier.relays_forwarded" in RELAY_CODES
    print("  PASS: dossier.relays_forwarded is registered")


def test_multiple_artifacts_same_section() -> None:
    """Multiple artifacts of the same type all appear in the export."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        _make_tox_repeat_dose(project, name="compound-a")
        _make_tox_repeat_dose(project, name="compound-b")

        export = _build_export(project, "WO-001")

        tox_section = next(s for s in export["sections"] if s["ctd_section"] == "2.6.6")
        repeat_dose = next(
            ss
            for ss in tox_section["subsections"]
            if ss["name"] == "Repeat-Dose Toxicity"
        )

        assert repeat_dose["status"] == "POPULATED"
        assert len(repeat_dose["entries"]) == 2

        # Each entry must have a distinct source path.
        paths = {e["source"]["artifact_path"] for e in repeat_dose["entries"]}
        assert len(paths) == 2

        print("  PASS: multiple artifacts same section")


def test_must_propagate_relays_forwarded() -> None:
    """Must-propagate relay codes are forwarded when found in sidecars."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        assay_path = _make_assay(project)

        _make_sidecar_with_relay(
            assay_path,
            "selectivity.ratio_not_affinity",
            "IC50 ratios used, not Kd.",
        )

        export = _build_export(project, "WO-001")

        relay_codes = [r["code"] for r in export["relays"]]
        assert "selectivity.ratio_not_affinity" in relay_codes

        print("  PASS: must-propagate relays forwarded")


def test_prediction_not_measurement_relay_forwarded() -> None:
    """Any relay containing 'prediction_not_measurement' is forwarded."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        pk_path = _make_pk_study(project)

        _make_sidecar_with_relay(
            pk_path,
            "admet.prediction_not_measurement",
            "Predicted ADMET, not measured.",
        )

        export = _build_export(project, "WO-001")

        relay_codes = [r["code"] for r in export["relays"]]
        assert "admet.prediction_not_measurement" in relay_codes

        print("  PASS: prediction_not_measurement relay forwarded")


def test_fields_not_in_artifact_labelled_as_not_available() -> None:
    """Fields requested but absent from the artifact are labelled NOT AVAILABLE."""
    with tempfile.TemporaryDirectory() as tmp:
        project = _make_project(Path(tmp))
        # Create a minimal tox-repeat-dose with some fields missing.
        data: dict[str, Any] = {
            "schema": "dde.tox-repeat-dose.v1",
            "species": "rat",
            # deliberately omit route, duration, dose, noael, loael, findings
        }
        path = project / "raw" / "tox" / "sparse.tox-repeat-dose.json"
        _write_json(path, data)

        export = _build_export(project, "WO-001")

        tox_section = next(s for s in export["sections"] if s["ctd_section"] == "2.6.6")
        repeat_dose = next(
            ss
            for ss in tox_section["subsections"]
            if ss["name"] == "Repeat-Dose Toxicity"
        )
        assert repeat_dose["status"] == "POPULATED"
        entry = repeat_dose["entries"][0]
        assert entry["species"] == "rat"
        assert "NOT AVAILABLE" in str(entry.get("route", ""))

        print("  PASS: missing fields labelled NOT AVAILABLE")


# ---------------------------------------------------------------------------
# Run all tests when executed directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_export_with_tox_records_populates_2_6_6()
    test_export_with_pk_records_populates_2_6_4()
    test_missing_section_shows_not_available()
    test_relay_forwarding_collects_upstream_relays()
    test_source_references_resolve_to_artifacts()
    test_json_output_structure()
    test_markdown_output_is_readable()
    test_tsv_output_is_tabular()
    test_ich_references_are_included()
    test_empty_project_produces_all_gap_export()
    test_relay_code_registered()
    test_multiple_artifacts_same_section()
    test_must_propagate_relays_forwarded()
    test_prediction_not_measurement_relay_forwarded()
    test_fields_not_in_artifact_labelled_as_not_available()
    print("\nAll dossier export tests passed.")
