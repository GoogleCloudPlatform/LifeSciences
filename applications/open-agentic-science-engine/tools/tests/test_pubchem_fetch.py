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

"""Tests for ``dde pubchem fetch`` — CID-to-compound data.

Issue #90: No CID-to-SMILES path inside the DDE tool surface.

Asserts:
1. Property endpoint parsing produces correct artifact fields.
2. N/A SMILES triggers fallback to the full record endpoint.
3. Artifact follows ``dde.pubchem-compound.v1`` schema shape.
4. Sidecar written with tool="pubchem", subcommand="fetch".
5. Multiple CIDs each produce their own artifact + sidecar.
6. Invalid CID handling (404).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test.
_mock_requests = MagicMock()
_mock_yaml = MagicMock()
_module_patches = patch.dict(
    "sys.modules",
    {"requests": _mock_requests, "yaml": _mock_yaml},
)
_module_patches.start()

import unittest  # noqa: E402

from dde.commands.pubchem import (  # noqa: E402
    _build_compound_artifact,
    _extract_smiles_from_full_record,
    _needs_fallback,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _property_response(
    cid: int = 2244,
    canonical: str = "CC(=O)OC1=CC=CC=C1C(=O)O",
    isomeric: str = "CC(=O)OC1=CC=CC=C1C(=O)O",
    inchikey: str = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
    formula: str = "C9H8O4",
    weight: float = 180.16,
) -> dict[str, Any]:
    """Build a PubChem property-endpoint JSON response."""
    return {
        "PropertyTable": {
            "Properties": [
                {
                    "CID": cid,
                    "CanonicalSMILES": canonical,
                    "IsomericSMILES": isomeric,
                    "InChIKey": inchikey,
                    "MolecularFormula": formula,
                    "MolecularWeight": weight,
                }
            ]
        }
    }


def _na_property_response(cid: int = 99999) -> dict[str, Any]:
    """Property response where SMILES are N/A (the known PubChem quirk)."""
    return {
        "PropertyTable": {
            "Properties": [
                {
                    "CID": cid,
                    "CanonicalSMILES": "N/A",
                    "IsomericSMILES": "N/A",
                    "InChIKey": "ABCDEFGHIJ-UHFFFAOYSA-N",
                    "MolecularFormula": "C10H10",
                    "MolecularWeight": 130.19,
                }
            ]
        }
    }


def _full_record_response(
    cid: int = 99999,
    canonical: str = "C1=CC=CC=C1",
    isomeric: str = "C1=CC=CC=C1",
) -> dict[str, Any]:
    """Build a PubChem full-record JSON response with URN-based SMILES."""
    return {
        "PC_Compounds": [
            {
                "id": {"id": {"cid": cid}},
                "props": [
                    {
                        "urn": {
                            "label": "SMILES",
                            "name": "Canonical",
                        },
                        "value": {"sval": canonical},
                    },
                    {
                        "urn": {
                            "label": "SMILES",
                            "name": "Isomeric",
                        },
                        "value": {"sval": isomeric},
                    },
                    {
                        "urn": {
                            "label": "InChIKey",
                            "name": "Standard",
                        },
                        "value": {"sval": "ABCDEFGHIJ-UHFFFAOYSA-N"},
                    },
                ],
            }
        ]
    }


def _make_http_response(payload: dict[str, Any], status: int = 200) -> MagicMock:
    """Create a mock HTTP response object."""
    resp = MagicMock()
    resp.status_code = status
    body = json.dumps(payload).encode("utf-8")
    resp.content = body
    resp.text = json.dumps(payload)
    return resp


def _run_fetch(
    cids: list[int],
    http_side_effect: list[MagicMock],
    slug_override: str | None = None,
) -> tuple[int, str, dict[str, dict[str, Any]]]:
    """Run ``dde pubchem fetch`` via CliRunner.

    Returns ``(exit_code, output, files)`` where *files* is a dict
    mapping filename → parsed JSON for every file under ``raw/compounds/``.
    This reads inside the temp-dir context so the data survives cleanup.
    """
    from click.testing import CliRunner

    from dde.cli import cli

    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "test-project"
        project.mkdir()
        (project / ".dde").mkdir()
        compounds_dir = project / "raw" / "compounds"
        compounds_dir.mkdir(parents=True)

        args = ["--project", str(project), "pubchem", "fetch"]
        if slug_override:
            args.extend(["--name", slug_override])
        args.extend([str(c) for c in cids])

        with patch("dde.core.http.request", side_effect=http_side_effect):
            result = runner.invoke(cli, args)

        # Read all produced files before the temp dir is cleaned up.
        files: dict[str, dict[str, Any]] = {}
        if compounds_dir.is_dir():
            for f in sorted(compounds_dir.iterdir()):
                if f.suffix == ".json":
                    files[f.name] = json.loads(f.read_text(encoding="utf-8"))

        return result.exit_code, result.output, files


# ---------------------------------------------------------------------------
# Pure-function tests (no CLI runner needed)
# ---------------------------------------------------------------------------


class TestNeedsFallback(unittest.TestCase):
    """_needs_fallback correctly detects N/A and empty SMILES."""

    def test_normal_smiles_no_fallback(self):
        props = {
            "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
            "IsomericSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
        }
        self.assertFalse(_needs_fallback(props))

    def test_canonical_na_needs_fallback(self):
        props = {"CanonicalSMILES": "N/A", "IsomericSMILES": "CC"}
        self.assertTrue(_needs_fallback(props))

    def test_isomeric_na_needs_fallback(self):
        props = {"CanonicalSMILES": "CC", "IsomericSMILES": "N/A"}
        self.assertTrue(_needs_fallback(props))

    def test_both_na_needs_fallback(self):
        props = {"CanonicalSMILES": "N/A", "IsomericSMILES": "N/A"}
        self.assertTrue(_needs_fallback(props))

    def test_empty_canonical_needs_fallback(self):
        props = {"CanonicalSMILES": "", "IsomericSMILES": "CC"}
        self.assertTrue(_needs_fallback(props))

    def test_missing_key_needs_fallback(self):
        props = {"IsomericSMILES": "CC"}
        self.assertTrue(_needs_fallback(props))


class TestExtractSmilesFromFullRecord(unittest.TestCase):
    """_extract_smiles_from_full_record parses the URN structure."""

    def test_both_smiles_extracted(self):
        payload = _full_record_response(
            canonical="C1=CC=CC=C1",
            isomeric="[C@@H]1CC1",
        )
        result = _extract_smiles_from_full_record(payload)
        self.assertEqual(result["canonical"], "C1=CC=CC=C1")
        self.assertEqual(result["isomeric"], "[C@@H]1CC1")

    def test_empty_compounds_returns_empty(self):
        result = _extract_smiles_from_full_record({"PC_Compounds": []})
        self.assertEqual(result, {})

    def test_no_compounds_key_returns_empty(self):
        result = _extract_smiles_from_full_record({})
        self.assertEqual(result, {})

    def test_non_smiles_props_ignored(self):
        payload = {
            "PC_Compounds": [
                {
                    "props": [
                        {
                            "urn": {"label": "InChI", "name": "Standard"},
                            "value": {"sval": "InChI=1S/C6H6/c1-2-4-6-5-3-1/h1-6H"},
                        },
                        {
                            "urn": {"label": "SMILES", "name": "Canonical"},
                            "value": {"sval": "c1ccccc1"},
                        },
                    ]
                }
            ]
        }
        result = _extract_smiles_from_full_record(payload)
        self.assertEqual(result["canonical"], "c1ccccc1")
        self.assertNotIn("isomeric", result)


class TestBuildCompoundArtifact(unittest.TestCase):
    """_build_compound_artifact produces the correct schema shape."""

    def test_schema_field(self):
        art = _build_compound_artifact(
            cid=2244,
            canonical_smiles="CC",
            isomeric_smiles="CC",
            inchikey="ABC-DEF",
            molecular_formula="C2H6",
            molecular_weight=30.07,
            source="property",
        )
        self.assertEqual(art["schema"], "dde.pubchem-compound.v1")

    def test_all_fields_present(self):
        art = _build_compound_artifact(
            cid=2244,
            canonical_smiles="CC",
            isomeric_smiles="CC",
            inchikey="ABC-DEF",
            molecular_formula="C2H6",
            molecular_weight=30.07,
            source="property",
        )
        for key in (
            "schema",
            "cid",
            "canonical_smiles",
            "isomeric_smiles",
            "inchikey",
            "molecular_formula",
            "molecular_weight",
            "source",
            "retrieved",
        ):
            self.assertIn(key, art, f"missing key: {key}")

    def test_cid_is_int(self):
        art = _build_compound_artifact(
            cid=2244,
            canonical_smiles="CC",
            isomeric_smiles="CC",
            inchikey="ABC",
            molecular_formula="C2H6",
            molecular_weight=30.07,
            source="property",
        )
        self.assertIsInstance(art["cid"], int)

    def test_source_values(self):
        for src in ("property", "full_record"):
            art = _build_compound_artifact(
                cid=1,
                canonical_smiles="C",
                isomeric_smiles="C",
                inchikey="X",
                molecular_formula="CH4",
                molecular_weight=16.04,
                source=src,
            )
            self.assertEqual(art["source"], src)

    def test_retrieved_is_iso_timestamp(self):
        art = _build_compound_artifact(
            cid=1,
            canonical_smiles="C",
            isomeric_smiles="C",
            inchikey="X",
            molecular_formula="CH4",
            molecular_weight=16.04,
            source="property",
        )
        self.assertRegex(art["retrieved"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# CLI integration tests (CliRunner with mocked HTTP)
# ---------------------------------------------------------------------------


class TestFetchPropertyEndpoint(unittest.TestCase):
    """Normal property endpoint response produces a correct artifact."""

    def test_artifact_written(self):
        resp = _make_http_response(_property_response(cid=2244))
        exit_code, output, files = _run_fetch([2244], [resp])
        self.assertEqual(exit_code, 0, f"exit {exit_code}:\n{output}")

        artifact = files.get("2244.pubchem-compound.artifact.json")
        self.assertIsNotNone(artifact, "artifact file not written")
        self.assertEqual(artifact["schema"], "dde.pubchem-compound.v1")
        self.assertEqual(artifact["cid"], 2244)
        self.assertEqual(artifact["canonical_smiles"], "CC(=O)OC1=CC=CC=C1C(=O)O")
        self.assertEqual(artifact["inchikey"], "BSYNRYMUTXBXSQ-UHFFFAOYSA-N")
        self.assertEqual(artifact["molecular_formula"], "C9H8O4")
        self.assertAlmostEqual(artifact["molecular_weight"], 180.16)
        self.assertEqual(artifact["source"], "property")

    def test_sidecar_written(self):
        resp = _make_http_response(_property_response(cid=2244))
        exit_code, _, files = _run_fetch([2244], [resp])
        self.assertEqual(exit_code, 0)

        meta = files.get("2244.pubchem-compound.meta.json")
        self.assertIsNotNone(meta, "sidecar not written")
        self.assertEqual(meta["tool"], "pubchem")
        self.assertEqual(meta["subcommand"], "fetch")
        self.assertIn("cid", meta["parameters"])
        self.assertEqual(meta["parameters"]["cid"], 2244)


class TestFetchNAFallback(unittest.TestCase):
    """N/A SMILES in property response triggers full-record fallback."""

    def test_fallback_to_full_record(self):
        prop_resp = _make_http_response(_na_property_response(cid=99999))
        full_resp = _make_http_response(
            _full_record_response(
                cid=99999,
                canonical="C1=CC=CC=C1",
                isomeric="C1=CC=CC=C1",
            )
        )
        exit_code, output, files = _run_fetch([99999], [prop_resp, full_resp])
        self.assertEqual(exit_code, 0, f"exit {exit_code}:\n{output}")

        artifact = files.get("99999.pubchem-compound.artifact.json")
        self.assertIsNotNone(artifact, "artifact not written")
        self.assertEqual(artifact["source"], "full_record")
        self.assertEqual(artifact["canonical_smiles"], "C1=CC=CC=C1")

    def test_fallback_sidecar_records_both_endpoints(self):
        prop_resp = _make_http_response(_na_property_response(cid=99999))
        full_resp = _make_http_response(
            _full_record_response(cid=99999, canonical="C1=CC=CC=C1"),
        )
        exit_code, _, files = _run_fetch([99999], [prop_resp, full_resp])
        self.assertEqual(exit_code, 0)

        meta = files.get("99999.pubchem-compound.meta.json")
        self.assertIsNotNone(meta, "sidecar not written")
        # Endpoint field should contain both URLs
        self.assertIn("/property/", meta["endpoint"])
        self.assertIn("/JSON", meta["endpoint"])
        self.assertEqual(meta["source"], "full_record")


class TestFetchMultipleCIDs(unittest.TestCase):
    """Multiple CIDs each produce their own artifact + sidecar pair."""

    def test_two_cids(self):
        resp1 = _make_http_response(_property_response(cid=2244))
        resp2 = _make_http_response(
            _property_response(
                cid=5090,
                canonical="CC12CCC3C(C1CCC2O)CCC4=CC(=O)CCC34C",
                isomeric="CC12CCC3C(C1CCC2O)CCC4=CC(=O)CCC34C",
                inchikey="VOXZDWNPVJITMN-ZBRFXRBCSA-N",
                formula="C19H28O2",
                weight=288.42,
            ),
        )
        exit_code, output, files = _run_fetch([2244, 5090], [resp1, resp2])
        self.assertEqual(exit_code, 0, f"exit {exit_code}:\n{output}")

        self.assertIn("2244.pubchem-compound.artifact.json", files)
        self.assertIn("5090.pubchem-compound.artifact.json", files)
        self.assertIn("2244.pubchem-compound.meta.json", files)
        self.assertIn("5090.pubchem-compound.meta.json", files)


class TestFetchInvalidCID(unittest.TestCase):
    """404 from PubChem writes a not-found artifact."""

    def test_not_found_artifact(self):
        resp = _make_http_response({}, status=404)
        exit_code, output, files = _run_fetch([999999999], [resp])
        self.assertEqual(exit_code, 0, f"exit {exit_code}:\n{output}")

        artifact = files.get("999999999.pubchem-compound.artifact.json")
        self.assertIsNotNone(artifact, "not-found artifact not written")
        self.assertTrue(artifact.get("_not_found"))
        self.assertEqual(artifact["schema"], "dde.pubchem-compound.v1")
        self.assertEqual(artifact["cid"], 999999999)

    def test_not_found_sidecar_has_note(self):
        resp = _make_http_response({}, status=404)
        exit_code, _, files = _run_fetch([999999999], [resp])
        self.assertEqual(exit_code, 0)

        meta = files.get("999999999.pubchem-compound.meta.json")
        self.assertIsNotNone(meta, "not-found sidecar not written")
        self.assertTrue(meta.get("not_found"))


class TestFetchSlugOverride(unittest.TestCase):
    """--name option overrides the output filename slug."""

    def test_custom_slug(self):
        resp = _make_http_response(_property_response(cid=2244))
        exit_code, output, files = _run_fetch(
            [2244],
            [resp],
            slug_override="aspirin",
        )
        self.assertEqual(exit_code, 0, f"exit {exit_code}:\n{output}")

        self.assertIn(
            "aspirin.pubchem-compound.artifact.json",
            files,
            "custom-slug artifact not written",
        )
        self.assertIn(
            "aspirin.pubchem-compound.meta.json",
            files,
            "custom-slug sidecar not written",
        )


class TestArtifactSchemaShape(unittest.TestCase):
    """The artifact JSON strictly follows the dde.pubchem-compound.v1 schema."""

    def test_exact_keys(self):
        resp = _make_http_response(_property_response(cid=2244))
        _, _, files = _run_fetch([2244], [resp])

        artifact = files.get("2244.pubchem-compound.artifact.json")
        self.assertIsNotNone(artifact)
        expected_keys = {
            "schema",
            "cid",
            "canonical_smiles",
            "isomeric_smiles",
            "inchikey",
            "molecular_formula",
            "molecular_weight",
            "source",
            "retrieved",
        }
        self.assertEqual(set(artifact.keys()), expected_keys)

    def test_molecular_weight_is_number(self):
        resp = _make_http_response(_property_response(cid=2244, weight=180.16))
        _, _, files = _run_fetch([2244], [resp])

        artifact = files.get("2244.pubchem-compound.artifact.json")
        self.assertIsNotNone(artifact)
        self.assertIsInstance(artifact["molecular_weight"], (int, float))


if __name__ == "__main__":
    unittest.main()
