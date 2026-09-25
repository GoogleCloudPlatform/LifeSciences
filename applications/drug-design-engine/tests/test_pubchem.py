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

"""Tests for the pubchem command group (#259).

Covers:
  - annotate with valid CID (mock HTTP responses)
  - annotate with unknown CID (404 -> not-found artifact)
  - analyze-annotation known-drug verdict (max_phase >= 1)
  - analyze-annotation known-compound verdict (annotations, no drug data)
  - analyze-annotation unknown verdict (minimal annotation)
  - Relay guards: relays only fire on known-drug/known-compound, NOT unknown
  - ChEMBL enrichment fallback (404 on ChEMBL -> PubChem-only works)
  - CliRunner integration tests for annotate and analyze-annotation (R2 fix)
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.pubchem import (
    SCHEMA,
    _build_artifact,
    _classify_annotation,
    _extract_chembl_data,
    _extract_classification,
    _extract_inchikey,
    _extract_synonyms,
    _slug,
)
from dde.core import provenance

# ---------------------------------------------------------------------------
# Helper: canned API responses
# ---------------------------------------------------------------------------


def _pubchem_synonyms_response(cid: int, synonyms: list[str]) -> dict[str, Any]:
    """Build a canned PubChem synonyms response."""
    return {
        "InformationList": {
            "Information": [
                {"CID": cid, "Synonym": synonyms},
            ]
        }
    }


def _pubchem_synonyms_not_found(cid: int) -> dict[str, Any]:
    return {"_not_found": True, "cid": cid, "http_status": 404}


def _pubchem_classification_response(
    cid: int,
    classification: list[str],
    actions: list[str],
) -> dict[str, Any]:
    """Build a canned PubChem classification response."""
    nodes_class = [{"Information": {"Name": c}} for c in classification]
    nodes_action = [{"Information": {"Name": a}} for a in actions]
    hierarchies = []
    if classification:
        hierarchies.append(
            {
                "SourceName": "ChEBI Ontology",
                "Node": nodes_class,
            }
        )
    if actions:
        hierarchies.append(
            {
                "SourceName": "MeSH Pharmacological Actions",
                "Node": nodes_action,
            }
        )
    return {"Hierarchies": {"Hierarchy": hierarchies}}


def _pubchem_inchikey_response(cid: int, inchikey: str) -> dict[str, Any]:
    return {
        "PropertyTable": {
            "Properties": [{"CID": cid, "InChIKey": inchikey}],
        }
    }


def _chembl_molecule_response(
    chembl_id: str,
    max_phase: int,
    molecule_type: str,
) -> dict[str, Any]:
    return {
        "molecule_chembl_id": chembl_id,
        "max_phase": max_phase,
        "molecule_type": molecule_type,
        "molecule_structures": {"canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O"},
        "molecule_properties": {"full_molformula": "C9H8O4"},
    }


def _chembl_mechanism_response(
    chembl_id: str,
    target_name: str,
    action_type: str,
    target_chembl_id: str,
) -> dict[str, Any]:
    return {
        "mechanisms": [
            {
                "molecule_chembl_id": chembl_id,
                "target_name": target_name,
                "target_chembl_id": target_chembl_id,
                "action_type": action_type,
            }
        ]
    }


def _not_found_payload() -> dict[str, Any]:
    return {"_not_found": True, "http_status": 404}


# ---------------------------------------------------------------------------
# 1. _extract_synonyms tests
# ---------------------------------------------------------------------------


def test_extract_synonyms_valid() -> None:
    """Extract synonyms from a valid PubChem response."""
    payload = _pubchem_synonyms_response(2244, ["aspirin", "ASA", "Bayer"])
    result = _extract_synonyms(payload)
    assert result == ["aspirin", "ASA", "Bayer"], f"Got {result}"
    print("  PASS: extract_synonyms valid")


def test_extract_synonyms_not_found() -> None:
    """Not-found response returns empty list."""
    payload = _pubchem_synonyms_not_found(99999)
    result = _extract_synonyms(payload)
    assert result == [], f"Expected [], got {result}"
    print("  PASS: extract_synonyms not-found")


def test_extract_synonyms_bounded() -> None:
    """Synonyms are bounded to MAX_SYNONYMS (20)."""
    from dde.commands.pubchem import MAX_SYNONYMS

    payload = _pubchem_synonyms_response(2244, [f"syn-{i}" for i in range(50)])
    result = _extract_synonyms(payload)
    assert len(result) == MAX_SYNONYMS, f"Expected {MAX_SYNONYMS}, got {len(result)}"
    print("  PASS: extract_synonyms bounded")


# ---------------------------------------------------------------------------
# 2. _extract_classification tests
# ---------------------------------------------------------------------------


def test_extract_classification_valid() -> None:
    """Extract classification and actions from a valid response."""
    payload = _pubchem_classification_response(
        2244,
        ["Anti-Inflammatory Agents, Non-Steroidal"],
        ["Cyclooxygenase Inhibitors"],
    )
    classification, actions = _extract_classification(payload)
    assert "Anti-Inflammatory Agents, Non-Steroidal" in classification, (
        f"classification={classification}"
    )
    assert "Cyclooxygenase Inhibitors" in actions, f"actions={actions}"
    print("  PASS: extract_classification valid")


def test_extract_classification_not_found() -> None:
    """Not-found response returns empty lists."""
    classification, actions = _extract_classification(_not_found_payload())
    assert classification == [] and actions == [], (
        f"Expected empty, got cls={classification}, act={actions}"
    )
    print("  PASS: extract_classification not-found")


# ---------------------------------------------------------------------------
# 3. _extract_inchikey tests
# ---------------------------------------------------------------------------


def test_extract_inchikey_valid() -> None:
    """Extract InChIKey from property response."""
    payload = _pubchem_inchikey_response(2244, "BSYNRYMUTXBXSQ-UHFFFAOYSA-N")
    result = _extract_inchikey(payload)
    assert result == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", f"Got {result}"
    print("  PASS: extract_inchikey valid")


def test_extract_inchikey_not_found() -> None:
    """Not-found returns None."""
    result = _extract_inchikey(_not_found_payload())
    assert result is None, f"Expected None, got {result}"
    print("  PASS: extract_inchikey not-found")


# ---------------------------------------------------------------------------
# 4. _extract_chembl_data tests
# ---------------------------------------------------------------------------


def test_extract_chembl_data_valid() -> None:
    """Extract drug status and MoA from ChEMBL responses."""
    mol = _chembl_molecule_response("CHEMBL25", 4, "Small molecule")
    moa = _chembl_mechanism_response(
        "CHEMBL25",
        "Cyclooxygenase-2",
        "INHIBITOR",
        "CHEMBL2094253",
    )
    result = _extract_chembl_data(mol, moa)
    assert result["chembl_id"] == "CHEMBL25"
    assert result["max_phase"] == 4
    assert result["molecule_type"] == "Small molecule"
    assert len(result["mechanisms"]) == 1
    assert result["mechanisms"][0]["target_name"] == "Cyclooxygenase-2"
    assert result["mechanisms"][0]["action_type"] == "INHIBITOR"
    print("  PASS: extract_chembl_data valid")


def test_extract_chembl_data_not_found() -> None:
    """ChEMBL not-found returns empty data (PubChem-only is valid)."""
    result = _extract_chembl_data(_not_found_payload(), _not_found_payload())
    assert result["chembl_id"] is None
    assert result["max_phase"] is None
    assert result["mechanisms"] == []
    print("  PASS: extract_chembl_data not-found (ChEMBL fallback)")


# ---------------------------------------------------------------------------
# 5. _build_artifact tests
# ---------------------------------------------------------------------------


def test_build_artifact_schema() -> None:
    """Built artifact has correct schema and required fields."""
    chembl_data = {
        "chembl_id": "CHEMBL25",
        "max_phase": 4,
        "molecule_type": "Small molecule",
        "mechanisms": [
            {
                "target_name": "Cyclooxygenase-2",
                "target_chembl_id": "CHEMBL2094253",
                "action_type": "INHIBITOR",
                "source": "chembl",
            }
        ],
    }
    artifact = _build_artifact(
        2244,
        ["aspirin", "ASA"],
        ["Anti-Inflammatory Agents"],
        ["Cyclooxygenase Inhibitors"],
        chembl_data,
    )
    assert artifact["schema"] == SCHEMA
    assert artifact["query"]["cid"] == 2244
    assert artifact["query"]["chembl_id"] == "CHEMBL25"
    assert "synonyms" in artifact
    assert "pharmacology" in artifact
    assert "drug_status" in artifact
    assert "mechanisms" in artifact
    assert artifact["drug_status"]["max_phase"] == 4
    print("  PASS: build_artifact schema and structure")


def test_build_artifact_no_chembl() -> None:
    """Artifact without ChEMBL data has null drug_status fields."""
    chembl_data = {
        "chembl_id": None,
        "max_phase": None,
        "molecule_type": None,
        "mechanisms": [],
    }
    artifact = _build_artifact(12345, ["some-compound"], [], [], chembl_data)
    assert artifact["drug_status"]["max_phase"] is None
    assert artifact["drug_status"]["source"] is None
    assert artifact["mechanisms"] == []
    print("  PASS: build_artifact no ChEMBL data")


# ---------------------------------------------------------------------------
# 6. _slug tests
# ---------------------------------------------------------------------------


def test_slug() -> None:
    """Slug is deterministic for a CID."""
    assert _slug(2244) == "cid-2244"
    assert _slug(0) == "cid-0"
    assert _slug(99999999) == "cid-99999999"
    print("  PASS: slug generation")


# ---------------------------------------------------------------------------
# 7. Analyze verdict classification tests
# ---------------------------------------------------------------------------


def _make_annotation_artifact(
    cid: int = 2244,
    synonyms: list[str] | None = None,
    classification: list[str] | None = None,
    actions: list[str] | None = None,
    max_phase: int | None = None,
    chembl_id: str | None = None,
    mechanisms: list[dict] | None = None,
    not_found: bool = False,
) -> dict[str, Any]:
    """Build a minimal annotation artifact for testing analyze logic."""
    if not_found:
        return {"schema": SCHEMA, "_not_found": True, "query": {"cid": cid}}
    return {
        "schema": SCHEMA,
        "query": {"cid": cid, "chembl_id": chembl_id},
        "synonyms": {
            "count": len(synonyms) if synonyms else 0,
            "top": synonyms or [],
            "trade_names": [],
        },
        "pharmacology": {
            "classification": classification or [],
            "actions": actions or [],
        },
        "drug_status": {
            "max_phase": max_phase,
            "molecule_type": "Small molecule" if chembl_id else None,
            "source": "chembl" if chembl_id else None,
        },
        "mechanisms": mechanisms or [],
    }


def _classify(annotation: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Convenience wrapper: call the real _classify_annotation with the CID
    extracted from the annotation's query field."""
    cid = annotation.get("query", {}).get("cid", 0)
    return _classify_annotation(annotation, cid)


def test_analyze_known_drug() -> None:
    """max_phase >= 1 -> known-drug verdict."""
    annotation = _make_annotation_artifact(
        cid=2244,
        synonyms=["aspirin", "ASA"],
        classification=["Anti-Inflammatory Agents"],
        max_phase=4,
        chembl_id="CHEMBL25",
        mechanisms=[
            {"target_name": "COX-2", "action_type": "INHIBITOR", "source": "chembl"}
        ],
    )
    verdict, _relays = _classify(annotation)
    assert verdict == "known-drug", f"Expected known-drug, got {verdict}"
    print("  PASS: analyze verdict — known-drug")


def test_analyze_known_compound() -> None:
    """Has annotations but no drug data -> known-compound verdict."""
    annotation = _make_annotation_artifact(
        cid=12345,
        synonyms=["some-compound", "alt-name"],
        classification=["Organic Compounds"],
        max_phase=None,
        chembl_id=None,
    )
    verdict, _relays = _classify(annotation)
    assert verdict == "known-compound", f"Expected known-compound, got {verdict}"
    print("  PASS: analyze verdict — known-compound")


def test_analyze_unknown() -> None:
    """Minimal annotation (no synonyms, no classification) -> unknown."""
    annotation = _make_annotation_artifact(
        cid=99999,
        synonyms=[],
        classification=[],
        actions=[],
        max_phase=None,
        chembl_id=None,
    )
    verdict, _relays = _classify(annotation)
    assert verdict == "unknown", f"Expected unknown, got {verdict}"
    print("  PASS: analyze verdict — unknown")


def test_analyze_known_drug_max_phase_1() -> None:
    """max_phase == 1 (just entered clinical trials) -> still known-drug."""
    annotation = _make_annotation_artifact(
        cid=54321,
        synonyms=["experimental-compound"],
        max_phase=1,
        chembl_id="CHEMBL9999",
    )
    verdict, _relays = _classify(annotation)
    assert verdict == "known-drug", f"Expected known-drug, got {verdict}"
    print("  PASS: analyze verdict — known-drug (max_phase=1)")


def test_analyze_max_phase_0_is_not_drug() -> None:
    """max_phase == 0 -> NOT known-drug (compound with ChEMBL record but no clinical data)."""
    annotation = _make_annotation_artifact(
        cid=54322,
        synonyms=["preclinical-compound"],
        max_phase=0,
        chembl_id="CHEMBL8888",
    )
    verdict, _relays = _classify(annotation)
    assert verdict == "known-compound", f"Expected known-compound, got {verdict}"
    print("  PASS: analyze verdict — max_phase=0 is known-compound, not known-drug")


# ---------------------------------------------------------------------------
# 8. Relay guard tests
# ---------------------------------------------------------------------------


def test_relay_fires_known_drug() -> None:
    """Both relays fire on known-drug verdict."""
    annotation = _make_annotation_artifact(
        cid=2244,
        synonyms=["aspirin"],
        max_phase=4,
        chembl_id="CHEMBL25",
    )
    _verdict, relays = _classify(annotation)
    codes = {r["code"] for r in relays}
    assert "pubchem.annotation_is_not_validation" in codes, (
        "annotation_is_not_validation should fire on known-drug"
    )
    assert "pubchem.drug_status_is_development_history" in codes, (
        "drug_status_is_development_history should fire on known-drug"
    )
    print("  PASS: relay fires — known-drug (both relays)")


def test_relay_fires_known_compound() -> None:
    """Only annotation_is_not_validation fires on known-compound (no drug data)."""
    annotation = _make_annotation_artifact(
        cid=12345,
        synonyms=["some-compound"],
        max_phase=None,
        chembl_id=None,
    )
    _verdict, relays = _classify(annotation)
    codes = {r["code"] for r in relays}
    assert "pubchem.annotation_is_not_validation" in codes, (
        "annotation_is_not_validation should fire on known-compound"
    )
    assert "pubchem.drug_status_is_development_history" not in codes, (
        "drug_status relay should NOT fire without drug data"
    )
    print("  PASS: relay fires — known-compound (annotation only)")


def test_relay_silent_on_unknown() -> None:
    """No relays fire on unknown verdict."""
    annotation = _make_annotation_artifact(
        cid=99999,
        synonyms=[],
        classification=[],
        max_phase=None,
        chembl_id=None,
    )
    verdict, relays = _classify(annotation)
    assert verdict == "unknown", f"Expected unknown, got {verdict}"
    assert len(relays) == 0, (
        f"Expected no relays on unknown verdict, got {len(relays)}: "
        f"{[r['code'] for r in relays]}"
    )
    print("  PASS: relay silent — unknown verdict (GUARDED)")


# ---------------------------------------------------------------------------
# 9. Relay code registration test
# ---------------------------------------------------------------------------


def test_relay_codes_registered() -> None:
    """Both new relay codes are registered in RELAY_CODES."""
    assert "pubchem.annotation_is_not_validation" in provenance.RELAY_CODES, (
        "pubchem.annotation_is_not_validation not registered"
    )
    assert "pubchem.drug_status_is_development_history" in provenance.RELAY_CODES, (
        "pubchem.drug_status_is_development_history not registered"
    )
    print("  PASS: relay codes registered in RELAY_CODES")


# ---------------------------------------------------------------------------
# 10. Threshold set registration test
# ---------------------------------------------------------------------------


def test_threshold_set_registered() -> None:
    """pubchem-annotation threshold set is declared."""
    from dde.core.thresholds import declared_sets

    sets = declared_sets()
    assert "pubchem-annotation" in sets, (
        f"pubchem-annotation not in declared sets: {sorted(sets.keys())}"
    )
    ts = sets["pubchem-annotation"]
    assert ts.version == "1.0"
    assert ts.values == {}  # empty set — no numeric cutoffs
    print("  PASS: threshold set pubchem-annotation registered")


# ---------------------------------------------------------------------------
# 11. ChEMBL enrichment fallback test
# ---------------------------------------------------------------------------


def test_chembl_fallback_pubchem_only() -> None:
    """When ChEMBL returns 404, PubChem-only annotation produces valid artifact."""
    # Simulate PubChem-only data (ChEMBL 404)
    chembl_data = _extract_chembl_data(_not_found_payload(), _not_found_payload())
    artifact = _build_artifact(
        12345,
        ["some-compound", "alt-name"],
        ["Organic Compounds"],
        [],
        chembl_data,
    )
    assert artifact["schema"] == SCHEMA
    assert artifact["query"]["chembl_id"] is None
    assert artifact["drug_status"]["max_phase"] is None
    assert artifact["drug_status"]["source"] is None
    assert artifact["mechanisms"] == []
    # Verdict should be known-compound (has synonyms), not an error
    verdict, _relays = _classify(artifact)
    assert verdict == "known-compound", f"Expected known-compound, got {verdict}"
    print("  PASS: ChEMBL fallback — PubChem-only annotation valid")


# ---------------------------------------------------------------------------
# 12. Not-found artifact test
# ---------------------------------------------------------------------------


def test_not_found_artifact() -> None:
    """Not-found artifact has correct schema and _not_found flag."""
    annotation = _make_annotation_artifact(cid=99999, not_found=True)
    assert annotation["_not_found"] is True
    assert annotation["schema"] == SCHEMA
    assert annotation["query"]["cid"] == 99999
    print("  PASS: not-found artifact structure")


# ---------------------------------------------------------------------------
# 13. CliRunner integration tests (R2 fix)
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory for CliRunner tests."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    # Create the compounds artifact directory.
    (project / "raw" / "compounds").mkdir(parents=True, exist_ok=True)
    return project


def _mock_http_response(body: dict[str, Any], status_code: int = 200) -> mock.Mock:
    """Build a mock HTTP response."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.content = json.dumps(body).encode("utf-8")
    return resp


def _annotate_http_side_effect(
    cid: int = 2244,
    synonyms: list[str] | None = None,
    classification: list[str] | None = None,
    actions: list[str] | None = None,
    inchikey: str = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
    chembl_id: str = "CHEMBL25",
    max_phase: int = 4,
    not_found: bool = False,
) -> Any:
    """Build a side_effect function for http.request that serves canned responses
    for the annotate command's sequence of API calls."""
    if synonyms is None:
        synonyms = ["aspirin", "ASA", "Bayer"]
    if classification is None:
        classification = ["Anti-Inflammatory Agents"]
    if actions is None:
        actions = ["Cyclooxygenase Inhibitors"]

    syn_resp = _pubchem_synonyms_response(cid, synonyms)
    cls_resp = _pubchem_classification_response(cid, classification, actions)
    ik_resp = _pubchem_inchikey_response(cid, inchikey)
    mol_resp = _chembl_molecule_response(chembl_id, max_phase, "Small molecule")
    moa_resp = _chembl_mechanism_response(
        chembl_id,
        "Cyclooxygenase-2",
        "INHIBITOR",
        "CHEMBL2094253",
    )
    not_found_resp_body = {"_not_found": True, "cid": cid, "http_status": 404}

    call_count = [0]

    def side_effect(method: str, url: str, **kwargs: Any) -> mock.Mock:
        if not_found:
            # First call (synonyms) returns 404 — the rest should not be called.
            return _mock_http_response(not_found_resp_body, status_code=404)

        call_count[0] += 1
        n = call_count[0]
        if n == 1:  # synonyms
            return _mock_http_response(syn_resp)
        elif n == 2:  # classification
            return _mock_http_response(cls_resp)
        elif n == 3:  # inchikey
            return _mock_http_response(ik_resp)
        elif n == 4:  # chembl molecule
            return _mock_http_response(mol_resp)
        elif n == 5:  # chembl mechanism
            return _mock_http_response(moa_resp)
        else:
            raise AssertionError(f"Unexpected HTTP call #{n}: {method} {url}")

    return side_effect


def test_cli_annotate_valid_cid() -> None:
    """CliRunner: annotate with valid CID writes artifact and sidecar, exit 0."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubchem.http.request") as mock_req:
            mock_req.side_effect = _annotate_http_side_effect(cid=2244)
            result = runner.invoke(
                cli,
                ["--project", str(project), "pubchem", "annotate", "2244"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        # Check artifact was written.
        artifact_path = (
            project / "raw" / "compounds" / "cid-2244.pubchem-annotation.json"
        )
        assert artifact_path.is_file(), f"Artifact not found: {artifact_path}"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["schema"] == SCHEMA
        assert artifact["query"]["cid"] == 2244
        # Check sidecar was written.
        sidecar_path = (
            project / "raw" / "compounds" / "cid-2244.pubchem-annotation.meta.json"
        )
        assert sidecar_path.is_file(), f"Sidecar not found: {sidecar_path}"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar["tool"] == "pubchem"
        assert sidecar["subcommand"] == "annotate"
    print("  PASS: CLI annotate valid CID")


def test_cli_annotate_not_found_cid() -> None:
    """CliRunner: annotate with unknown CID (404) writes not-found artifact, exit 0."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubchem.http.request") as mock_req:
            mock_req.side_effect = _annotate_http_side_effect(cid=99999, not_found=True)
            result = runner.invoke(
                cli,
                ["--project", str(project), "pubchem", "annotate", "99999"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        artifact_path = (
            project / "raw" / "compounds" / "cid-99999.pubchem-annotation.json"
        )
        assert artifact_path.is_file(), "Not-found artifact not written"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["_not_found"] is True
        assert artifact["query"]["cid"] == 99999
    print("  PASS: CLI annotate not-found CID")


def test_cli_annotate_json_flag() -> None:
    """CliRunner: --json flag produces valid JSON output."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubchem.http.request") as mock_req:
            mock_req.side_effect = _annotate_http_side_effect(cid=2244)
            result = runner.invoke(
                cli,
                ["--project", str(project), "pubchem", "annotate", "2244", "--json"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        payload = json.loads(result.output)
        assert "cid" in payload, f"JSON output missing 'cid': {payload}"
    print("  PASS: CLI annotate --json")


def test_cli_annotate_quiet_flag() -> None:
    """CliRunner: --quiet flag suppresses human-readable output."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubchem.http.request") as mock_req:
            mock_req.side_effect = _annotate_http_side_effect(cid=2244)
            result = runner.invoke(
                cli,
                ["--project", str(project), "pubchem", "annotate", "2244", "--quiet"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        # Quiet should not include the verbose compound name line.
        assert "aspirin" not in result.output, (
            f"Quiet output should not contain compound info: {result.output}"
        )
    print("  PASS: CLI annotate --quiet")


def _write_annotation_and_sidecar(
    project: Path,
    cid: int,
    annotation: dict[str, Any],
) -> None:
    """Write a canned annotation artifact and sidecar to the project."""
    compounds_dir = project / "raw" / "compounds"
    compounds_dir.mkdir(parents=True, exist_ok=True)
    slug = f"cid-{cid}"

    artifact_path = compounds_dir / f"{slug}.pubchem-annotation.json"
    artifact_path.write_text(
        json.dumps(annotation, indent=2) + "\n",
        encoding="utf-8",
    )

    sidecar = {
        "tool": "pubchem",
        "subcommand": "annotate",
        "endpoint": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON",
        "parameters": {"cid": cid},
        "outputs": [str(artifact_path)],
        "mandatory_relays": [],
    }
    sidecar_path = compounds_dir / f"{slug}.pubchem-annotation.meta.json"
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2) + "\n",
        encoding="utf-8",
    )


def test_cli_analyze_known_drug() -> None:
    """CliRunner: analyze-annotation produces known-drug verdict."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        annotation = _make_annotation_artifact(
            cid=2244,
            synonyms=["aspirin", "ASA"],
            classification=["Anti-Inflammatory Agents"],
            max_phase=4,
            chembl_id="CHEMBL25",
            mechanisms=[
                {"target_name": "COX-2", "action_type": "INHIBITOR", "source": "chembl"}
            ],
        )
        _write_annotation_and_sidecar(project, 2244, annotation)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pubchem", "analyze-annotation", "2244"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        assert "KNOWN-DRUG" in result.output, (
            f"Expected 'KNOWN-DRUG' in output: {result.output}"
        )
        # Check analysis artifact was written.
        analysis_path = (
            project / "raw" / "compounds" / "cid-2244.pubchem-annotation.analysis.json"
        )
        assert analysis_path.is_file(), f"Analysis not found: {analysis_path}"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        assert analysis["assessment"]["outcome"] == "known-drug"
    print("  PASS: CLI analyze-annotation known-drug")


def test_cli_analyze_known_compound() -> None:
    """CliRunner: analyze-annotation produces known-compound verdict."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        annotation = _make_annotation_artifact(
            cid=12345,
            synonyms=["some-compound", "alt-name"],
            classification=["Organic Compounds"],
            max_phase=None,
            chembl_id=None,
        )
        _write_annotation_and_sidecar(project, 12345, annotation)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pubchem", "analyze-annotation", "12345"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        assert "KNOWN-COMPOUND" in result.output, (
            f"Expected 'KNOWN-COMPOUND' in output: {result.output}"
        )
    print("  PASS: CLI analyze-annotation known-compound")


def test_cli_analyze_unknown() -> None:
    """CliRunner: analyze-annotation produces unknown verdict."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        annotation = _make_annotation_artifact(
            cid=99999,
            synonyms=[],
            classification=[],
            actions=[],
            max_phase=None,
            chembl_id=None,
        )
        _write_annotation_and_sidecar(project, 99999, annotation)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "pubchem", "analyze-annotation", "99999"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        assert "UNKNOWN" in result.output, (
            f"Expected 'UNKNOWN' in output: {result.output}"
        )
    print("  PASS: CLI analyze-annotation unknown")


def test_cli_analyze_json_flag() -> None:
    """CliRunner: --json flag on analyze-annotation produces valid JSON."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        annotation = _make_annotation_artifact(
            cid=2244,
            synonyms=["aspirin"],
            max_phase=4,
            chembl_id="CHEMBL25",
        )
        _write_annotation_and_sidecar(project, 2244, annotation)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "pubchem",
                "analyze-annotation",
                "2244",
                "--json",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        payload = json.loads(result.output)
        assert "assessment" in payload, f"JSON output missing 'assessment': {payload}"
        assert payload["assessment"]["outcome"] == "known-drug"
    print("  PASS: CLI analyze-annotation --json")


def test_cli_analyze_quiet_flag() -> None:
    """CliRunner: --quiet flag on analyze-annotation suppresses verbose output."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        annotation = _make_annotation_artifact(
            cid=2244,
            synonyms=["aspirin"],
            max_phase=4,
            chembl_id="CHEMBL25",
        )
        _write_annotation_and_sidecar(project, 2244, annotation)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "pubchem",
                "analyze-annotation",
                "2244",
                "--quiet",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        # Quiet should not include the verdict summary line.
        assert "KNOWN-DRUG" not in result.output, (
            f"Quiet output should not contain verdict: {result.output}"
        )
    print("  PASS: CLI analyze-annotation --quiet")


def test_cli_analyze_from_flag() -> None:
    """CliRunner: --from flag reads artifacts from a different directory."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        # Write the annotation to a non-default directory.
        alt_dir = project / "alt-input"
        alt_dir.mkdir(parents=True, exist_ok=True)
        annotation = _make_annotation_artifact(
            cid=2244,
            synonyms=["aspirin"],
            max_phase=4,
            chembl_id="CHEMBL25",
        )
        slug = "cid-2244"
        (alt_dir / f"{slug}.pubchem-annotation.json").write_text(
            json.dumps(annotation, indent=2) + "\n",
            encoding="utf-8",
        )
        sidecar = {
            "tool": "pubchem",
            "subcommand": "annotate",
            "endpoint": "https://example.com",
            "parameters": {"cid": 2244},
            "outputs": [],
            "mandatory_relays": [],
        }
        (alt_dir / f"{slug}.pubchem-annotation.meta.json").write_text(
            json.dumps(sidecar, indent=2) + "\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "pubchem",
                "analyze-annotation",
                "2244",
                "--from",
                str(alt_dir),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        assert "KNOWN-DRUG" in result.output
    print("  PASS: CLI analyze-annotation --from")


def test_cli_analyze_out_flag() -> None:
    """CliRunner: --out flag writes analysis to a different directory."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        annotation = _make_annotation_artifact(
            cid=2244,
            synonyms=["aspirin"],
            max_phase=4,
            chembl_id="CHEMBL25",
        )
        _write_annotation_and_sidecar(project, 2244, annotation)

        alt_out = project / "alt-output"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "pubchem",
                "analyze-annotation",
                "2244",
                "--out",
                str(alt_out),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        analysis_path = alt_out / "cid-2244.pubchem-annotation.analysis.json"
        assert analysis_path.is_file(), (
            f"Analysis not written to --out directory: {alt_out}"
        )
    print("  PASS: CLI analyze-annotation --out")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # Extraction
        ("test_extract_synonyms_valid", test_extract_synonyms_valid),
        ("test_extract_synonyms_not_found", test_extract_synonyms_not_found),
        ("test_extract_synonyms_bounded", test_extract_synonyms_bounded),
        ("test_extract_classification_valid", test_extract_classification_valid),
        (
            "test_extract_classification_not_found",
            test_extract_classification_not_found,
        ),
        ("test_extract_inchikey_valid", test_extract_inchikey_valid),
        ("test_extract_inchikey_not_found", test_extract_inchikey_not_found),
        ("test_extract_chembl_data_valid", test_extract_chembl_data_valid),
        ("test_extract_chembl_data_not_found", test_extract_chembl_data_not_found),
        # Build artifact
        ("test_build_artifact_schema", test_build_artifact_schema),
        ("test_build_artifact_no_chembl", test_build_artifact_no_chembl),
        # Slug
        ("test_slug", test_slug),
        # Analyze verdicts (R1 fix — tests real _classify_annotation, not a copy)
        ("test_analyze_known_drug", test_analyze_known_drug),
        ("test_analyze_known_compound", test_analyze_known_compound),
        ("test_analyze_unknown", test_analyze_unknown),
        ("test_analyze_known_drug_max_phase_1", test_analyze_known_drug_max_phase_1),
        ("test_analyze_max_phase_0_is_not_drug", test_analyze_max_phase_0_is_not_drug),
        # Relay guards (R1 fix — tests real _classify_annotation)
        ("test_relay_fires_known_drug", test_relay_fires_known_drug),
        ("test_relay_fires_known_compound", test_relay_fires_known_compound),
        ("test_relay_silent_on_unknown", test_relay_silent_on_unknown),
        # Registration
        ("test_relay_codes_registered", test_relay_codes_registered),
        ("test_threshold_set_registered", test_threshold_set_registered),
        # ChEMBL fallback
        ("test_chembl_fallback_pubchem_only", test_chembl_fallback_pubchem_only),
        # Not-found
        ("test_not_found_artifact", test_not_found_artifact),
        # CliRunner integration tests (R2 fix)
        ("test_cli_annotate_valid_cid", test_cli_annotate_valid_cid),
        ("test_cli_annotate_not_found_cid", test_cli_annotate_not_found_cid),
        ("test_cli_annotate_json_flag", test_cli_annotate_json_flag),
        ("test_cli_annotate_quiet_flag", test_cli_annotate_quiet_flag),
        ("test_cli_analyze_known_drug", test_cli_analyze_known_drug),
        ("test_cli_analyze_known_compound", test_cli_analyze_known_compound),
        ("test_cli_analyze_unknown", test_cli_analyze_unknown),
        ("test_cli_analyze_json_flag", test_cli_analyze_json_flag),
        ("test_cli_analyze_quiet_flag", test_cli_analyze_quiet_flag),
        ("test_cli_analyze_from_flag", test_cli_analyze_from_flag),
        ("test_cli_analyze_out_flag", test_cli_analyze_out_flag),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} — {exc}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
