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

"""Tests for the similar command group (#260).

Covers:
  - search with valid SMILES (mock PubChem async + ChEMBL responses)
  - search with invalid SMILES (Refusal, exit 9)
  - substructure with valid SMILES (mock responses)
  - PubChem async polling (mock ListKey -> poll -> result)
  - PubChem timeout handling
  - ChEMBL pagination
  - analyze exact-match verdict
  - analyze known-compound-found verdict
  - analyze novel verdict
  - Relay guards: tanimoto_is_2d_only fires only when hits present,
    database_coverage_limited fires always
  - --source pubchem only, chembl only, both
  - CliRunner integration tests for all three subcommands
  - --json, --quiet flags
  - --from, --out on analyze
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

from dde.commands.similar import (
    SCHEMA,
    _build_artifact,
    _classify_results,
    _merge_hits,
    _poll_pubchem_listkey,
    _slug,
    _validate_smiles,
)
from dde.core import provenance
from dde.core.errors import ArtifactError, Refusal

# ---------------------------------------------------------------------------
# Helper: canned API responses
# ---------------------------------------------------------------------------


def _pubchem_listkey_waiting(listkey: str = "12345") -> dict[str, Any]:
    """PubChem async waiting response."""
    return {"Waiting": {"ListKey": listkey}}


def _pubchem_listkey_result(cids: list[int]) -> dict[str, Any]:
    """PubChem async result with CID list."""
    return {"IdentifierList": {"CID": cids}}


def _pubchem_properties(cids: list[int]) -> dict[str, Any]:
    """PubChem compound property response."""
    props = []
    for cid in cids:
        props.append(
            {
                "CID": cid,
                "CanonicalSMILES": f"SMILES-{cid}",
                "IUPACName": f"compound-{cid}",
                "MolecularWeight": 180.0 + cid,
                "InChIKey": f"INCHIKEY-{cid}",
            }
        )
    return {"PropertyTable": {"Properties": props}}


def _chembl_similarity_response(
    molecules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """ChEMBL similarity search response."""
    if molecules is None:
        molecules = [
            {
                "molecule_chembl_id": "CHEMBL25",
                "pref_name": "ASPIRIN",
                "similarity": 95,
                "molecule_structures": {
                    "canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O",
                    "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                },
            },
            {
                "molecule_chembl_id": "CHEMBL1697753",
                "pref_name": "DIFLUNISAL",
                "similarity": 80,
                "molecule_structures": {
                    "canonical_smiles": "OC(=O)c1cc(-c2ccc(F)cc2F)ccc1O",
                    "standard_inchi_key": "HUPFGBZLZUBKPF-UHFFFAOYSA-N",
                },
            },
        ]
    return {
        "molecules": molecules,
        "page_meta": {"next": None},
    }


def _chembl_substructure_response(
    molecules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """ChEMBL substructure search response."""
    if molecules is None:
        molecules = [
            {
                "molecule_chembl_id": "CHEMBL25",
                "pref_name": "ASPIRIN",
                "molecule_structures": {
                    "canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O",
                    "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                },
            },
        ]
    return {
        "molecules": molecules,
        "page_meta": {"next": None},
    }


# ---------------------------------------------------------------------------
# 1. SMILES validation tests
# ---------------------------------------------------------------------------


def test_validate_smiles_valid() -> None:
    """Valid SMILES returns canonical form."""
    with mock.patch("dde.commands.similar._require_rdkit") as mock_rdkit:
        mock_chem = mock.MagicMock()
        mock_mol = mock.MagicMock()
        mock_chem.MolFromSmiles.return_value = mock_mol
        mock_chem.MolToSmiles.return_value = "CC(=O)Oc1ccccc1C(=O)O"
        mock_rdkit.return_value = mock_chem

        result = _validate_smiles("CC(=O)Oc1ccccc1C(O)=O")
        assert result == "CC(=O)Oc1ccccc1C(=O)O"
    print("  PASS: validate_smiles valid")


def test_validate_smiles_invalid() -> None:
    """Invalid SMILES raises Refusal (exit 9)."""
    with mock.patch("dde.commands.similar._require_rdkit") as mock_rdkit:
        mock_chem = mock.MagicMock()
        mock_chem.MolFromSmiles.return_value = None
        mock_rdkit.return_value = mock_chem

        try:
            _validate_smiles("INVALID_SMILES")
            assert False, "Should have raised Refusal"
        except Refusal as exc:
            assert exc.exit_code == 9
            assert "unparseable SMILES" in exc.message
    print("  PASS: validate_smiles invalid -> Refusal (exit 9)")


# ---------------------------------------------------------------------------
# 2. Slug tests
# ---------------------------------------------------------------------------


def test_slug_simple() -> None:
    """Slug from simple SMILES."""
    slug = _slug("CCO")
    assert slug == "cco"
    print("  PASS: slug simple")


def test_slug_complex() -> None:
    """Slug from complex SMILES with special chars."""
    slug = _slug("CC(=O)Oc1ccccc1C(=O)O")
    assert slug  # non-empty
    assert "/" not in slug
    assert "=" not in slug
    assert len(slug) <= 80
    print("  PASS: slug complex")


def test_slug_short_smiles() -> None:
    """Very short/special SMILES gets hash-based slug."""
    slug = _slug("[H]")
    assert slug.startswith("mol-")
    print("  PASS: slug short/special SMILES")


# ---------------------------------------------------------------------------
# 3. PubChem async polling tests
# ---------------------------------------------------------------------------


def test_poll_pubchem_listkey_immediate() -> None:
    """Polling returns immediately when result is ready."""
    with (
        mock.patch("dde.commands.similar.http.get_json") as mock_get,
        mock.patch("dde.commands.similar.time.sleep"),
    ):
        mock_get.return_value = _pubchem_listkey_result([2244, 3672])
        result = _poll_pubchem_listkey("test-key")
        assert result == [2244, 3672]
    print("  PASS: poll PubChem listkey immediate")


def test_poll_pubchem_listkey_waiting() -> None:
    """Polling waits then returns result."""
    with (
        mock.patch("dde.commands.similar.http.get_json") as mock_get,
        mock.patch("dde.commands.similar.time.sleep"),
    ):
        mock_get.side_effect = [
            {"Waiting": {"ListKey": "test-key"}},
            {"Waiting": {"ListKey": "test-key"}},
            _pubchem_listkey_result([2244]),
        ]
        result = _poll_pubchem_listkey("test-key", timeout=60)
        assert result == [2244]
    print("  PASS: poll PubChem listkey with waiting")


def test_poll_pubchem_listkey_timeout() -> None:
    """Polling times out."""
    with (
        mock.patch("dde.commands.similar.http.get_json") as mock_get,
        mock.patch("dde.commands.similar.time.sleep"),
        mock.patch("dde.commands.similar.time.monotonic") as mock_time,
    ):
        # First call: t=0, second call: t=200 (past timeout)
        mock_time.side_effect = [0, 0, 200]
        mock_get.return_value = {"Waiting": {"ListKey": "test-key"}}

        try:
            _poll_pubchem_listkey("test-key", timeout=120)
            assert False, "Should have raised ArtifactError"
        except ArtifactError as exc:
            assert "timed out" in exc.message
    print("  PASS: poll PubChem listkey timeout")


def test_poll_pubchem_listkey_fault() -> None:
    """Polling raises on fault response."""
    with (
        mock.patch("dde.commands.similar.http.get_json") as mock_get,
        mock.patch("dde.commands.similar.time.sleep"),
    ):
        mock_get.return_value = {"Fault": {"Code": "PUGREST.ServerBusy"}}

        try:
            _poll_pubchem_listkey("test-key")
            assert False, "Should have raised ArtifactError"
        except ArtifactError as exc:
            assert "failed" in exc.message
    print("  PASS: poll PubChem listkey fault")


# ---------------------------------------------------------------------------
# 4. Merge hits tests
# ---------------------------------------------------------------------------


def test_merge_hits_dedup() -> None:
    """Merge deduplicates by InChIKey, preferring exact scores.

    PubChem similarity hits have tanimoto set to the threshold as a
    lower bound (tanimoto_is_lower_bound=True).  When a compound
    appears in both PubChem and ChEMBL, the ChEMBL entry with an
    exact score should be kept.
    """
    hits = [
        # PubChem hit: lower-bound score (server-filtered at threshold 0.85)
        {
            "inchikey": "KEY-1",
            "tanimoto": 0.85,
            "tanimoto_is_lower_bound": True,
            "source_db": "pubchem",
        },
        # ChEMBL hit: exact score for same compound
        {"inchikey": "KEY-1", "tanimoto": 0.95, "source_db": "chembl"},
        # Unique PubChem hit
        {
            "inchikey": "KEY-2",
            "tanimoto": 0.85,
            "tanimoto_is_lower_bound": True,
            "source_db": "pubchem",
        },
    ]
    merged = _merge_hits(hits, 10)
    assert len(merged) == 2
    # KEY-1 should prefer ChEMBL's exact score over PubChem's lower bound.
    key1 = next(h for h in merged if h["inchikey"] == "KEY-1")
    assert key1["source_db"] == "chembl"
    assert key1["tanimoto"] == 0.95
    assert "tanimoto_is_lower_bound" not in key1 or not key1["tanimoto_is_lower_bound"]
    # Sorted: 0.95 first, 0.85 second
    assert merged[0]["tanimoto"] == 0.95
    print("  PASS: merge hits dedup prefers exact ChEMBL score")


def test_merge_hits_cap() -> None:
    """Merge caps at max_results."""
    hits = [{"inchikey": f"KEY-{i}", "tanimoto": 0.9 - i * 0.01} for i in range(50)]
    merged = _merge_hits(hits, 5)
    assert len(merged) == 5
    print("  PASS: merge hits cap")


def test_merge_hits_sort_none_tanimoto() -> None:
    """Hits with None tanimoto sort last."""
    hits = [
        {"inchikey": "KEY-1", "tanimoto": None},
        {"inchikey": "KEY-2", "tanimoto": 0.85},
        {"inchikey": "KEY-3", "tanimoto": 0.90},
    ]
    merged = _merge_hits(hits, 10)
    assert merged[0]["tanimoto"] == 0.90
    assert merged[1]["tanimoto"] == 0.85
    assert merged[2]["tanimoto"] is None
    print("  PASS: merge hits sort None tanimoto last")


# ---------------------------------------------------------------------------
# 5. Build artifact tests
# ---------------------------------------------------------------------------


def test_build_artifact_schema() -> None:
    """Built artifact has correct schema and required fields."""
    hits = [
        {
            "source_db": "pubchem",
            "cid": 2244,
            "canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O",
            "iupac_name": "aspirin",
            "tanimoto": 1.0,
            "molecular_weight": 180.16,
        },
    ]
    artifact = _build_artifact(
        "CC(=O)Oc1ccccc1C(=O)O",
        "similarity",
        "pubchem",
        0.85,
        20,
        hits,
    )
    assert artifact["schema"] == SCHEMA
    assert artifact["query"]["smiles"] == "CC(=O)Oc1ccccc1C(=O)O"
    assert artifact["query"]["search_type"] == "similarity"
    assert artifact["query"]["threshold"] == 0.85
    assert artifact["summary"]["n_hits"] == 1
    assert artifact["summary"]["closest_match"]["tanimoto"] == 1.0
    assert len(artifact["hits"]) == 1
    print("  PASS: build_artifact schema and structure")


def test_build_artifact_no_hits() -> None:
    """Artifact with no hits has null closest_match."""
    artifact = _build_artifact(
        "CCO",
        "similarity",
        "both",
        0.85,
        20,
        [],
    )
    assert artifact["summary"]["n_hits"] == 0
    assert artifact["summary"]["closest_match"] is None
    print("  PASS: build_artifact no hits")


def test_build_artifact_substructure_no_threshold() -> None:
    """Substructure artifact has no threshold in query."""
    artifact = _build_artifact(
        "c1ccccc1",
        "substructure",
        "both",
        None,
        20,
        [],
    )
    assert "threshold" not in artifact["query"]
    print("  PASS: build_artifact substructure no threshold")


# ---------------------------------------------------------------------------
# 6. Classification tests
# ---------------------------------------------------------------------------


def _make_similar_artifact(
    hits: list[dict[str, Any]] | None = None,
    smiles: str = "CCO",
    search_type: str = "similarity",
    source: str = "both",
    threshold: float = 0.85,
) -> dict[str, Any]:
    """Build a minimal similarity artifact for testing classify logic."""
    if hits is None:
        hits = []
    return _build_artifact(smiles, search_type, source, threshold, 20, hits)


def test_classify_exact_match() -> None:
    """Tanimoto 1.0 -> exact-match verdict."""
    artifact = _make_similar_artifact(
        hits=[
            {"tanimoto": 1.0, "source_db": "pubchem", "inchikey": "KEY-1"},
        ]
    )
    verdict, _relays = _classify_results(artifact, 0.85, 1.0)
    assert verdict == "exact-match", f"Expected exact-match, got {verdict}"
    print("  PASS: classify exact-match")


def test_classify_known_compound_found() -> None:
    """Hit above threshold but below 1.0 -> known-compound-found."""
    artifact = _make_similar_artifact(
        hits=[
            {"tanimoto": 0.92, "source_db": "pubchem", "inchikey": "KEY-1"},
        ]
    )
    verdict, _relays = _classify_results(artifact, 0.85, 1.0)
    assert verdict == "known-compound-found", (
        f"Expected known-compound-found, got {verdict}"
    )
    print("  PASS: classify known-compound-found")


def test_classify_novel() -> None:
    """No hits above threshold -> novel."""
    artifact = _make_similar_artifact(
        hits=[
            {"tanimoto": 0.60, "source_db": "pubchem", "inchikey": "KEY-1"},
        ]
    )
    verdict, _relays = _classify_results(artifact, 0.85, 1.0)
    assert verdict == "novel", f"Expected novel, got {verdict}"
    print("  PASS: classify novel")


def test_classify_novel_no_hits() -> None:
    """No hits at all -> novel."""
    artifact = _make_similar_artifact(hits=[])
    verdict, _relays = _classify_results(artifact, 0.85, 1.0)
    assert verdict == "novel", f"Expected novel, got {verdict}"
    print("  PASS: classify novel (no hits)")


def test_classify_novel_none_tanimoto() -> None:
    """Hits with None tanimoto (substructure) -> novel."""
    artifact = _make_similar_artifact(
        hits=[
            {"tanimoto": None, "source_db": "pubchem", "inchikey": "KEY-1"},
        ]
    )
    verdict, _relays = _classify_results(artifact, 0.85, 1.0)
    assert verdict == "novel", f"Expected novel, got {verdict}"
    print("  PASS: classify novel (None tanimoto)")


def test_classify_pubchem_lower_bound() -> None:
    """PubChem hits with lower-bound tanimoto -> known-compound-found.

    PubChem server-filters at the user's threshold, so hits with
    tanimoto set to threshold (lower bound) should classify as
    known-compound-found, not novel.
    """
    artifact = _make_similar_artifact(
        hits=[
            {
                "tanimoto": 0.85,
                "tanimoto_is_lower_bound": True,
                "source_db": "pubchem",
                "inchikey": "KEY-1",
            },
        ]
    )
    verdict, _relays = _classify_results(artifact, 0.85, 1.0)
    assert verdict == "known-compound-found", (
        f"Expected known-compound-found for server-filtered PubChem hit, got {verdict}"
    )
    print("  PASS: classify PubChem lower-bound -> known-compound-found")


# ---------------------------------------------------------------------------
# 7. Relay guard tests
# ---------------------------------------------------------------------------


def test_relay_tanimoto_fires_with_hits() -> None:
    """tanimoto_is_2d_only fires ONLY when at least one hit is returned."""
    artifact = _make_similar_artifact(
        hits=[
            {"tanimoto": 0.95, "source_db": "pubchem", "inchikey": "KEY-1"},
        ]
    )
    _verdict, relays = _classify_results(artifact, 0.85, 1.0)
    codes = {r["code"] for r in relays}
    assert "similar.tanimoto_is_2d_only" in codes, (
        "tanimoto_is_2d_only should fire when hits present"
    )
    print("  PASS: relay tanimoto_is_2d_only fires with hits")


def test_relay_tanimoto_silent_no_hits() -> None:
    """tanimoto_is_2d_only does NOT fire when no hits."""
    artifact = _make_similar_artifact(hits=[])
    _verdict, relays = _classify_results(artifact, 0.85, 1.0)
    codes = {r["code"] for r in relays}
    assert "similar.tanimoto_is_2d_only" not in codes, (
        "tanimoto_is_2d_only should NOT fire when no hits"
    )
    print("  PASS: relay tanimoto_is_2d_only silent with no hits (GUARDED)")


def test_relay_coverage_always_fires() -> None:
    """database_coverage_limited fires unconditionally."""
    # With hits:
    artifact1 = _make_similar_artifact(
        hits=[
            {"tanimoto": 0.95, "source_db": "pubchem", "inchikey": "KEY-1"},
        ]
    )
    _, relays1 = _classify_results(artifact1, 0.85, 1.0)
    codes1 = {r["code"] for r in relays1}
    assert "similar.database_coverage_limited" in codes1, (
        "database_coverage_limited should fire with hits"
    )

    # Without hits:
    artifact2 = _make_similar_artifact(hits=[])
    _, relays2 = _classify_results(artifact2, 0.85, 1.0)
    codes2 = {r["code"] for r in relays2}
    assert "similar.database_coverage_limited" in codes2, (
        "database_coverage_limited should fire without hits (UNCONDITIONAL)"
    )
    print("  PASS: relay database_coverage_limited fires unconditionally")


# ---------------------------------------------------------------------------
# 8. Relay code registration
# ---------------------------------------------------------------------------


def test_relay_codes_registered() -> None:
    """Both new relay codes are registered in RELAY_CODES."""
    assert "similar.tanimoto_is_2d_only" in provenance.RELAY_CODES, (
        "similar.tanimoto_is_2d_only not registered"
    )
    assert "similar.database_coverage_limited" in provenance.RELAY_CODES, (
        "similar.database_coverage_limited not registered"
    )
    print("  PASS: relay codes registered in RELAY_CODES")


# ---------------------------------------------------------------------------
# 9. Threshold set registration
# ---------------------------------------------------------------------------


def test_threshold_set_registered() -> None:
    """similar-search threshold set is declared with expected values."""
    from dde.core.thresholds import UNRESOLVED, declared_sets

    sets = declared_sets()
    assert "similar-search" in sets, (
        f"similar-search not in declared sets: {sorted(sets.keys())}"
    )
    ts = sets["similar-search"]
    assert ts.version == "1.0"
    assert ts.values["tanimoto_similarity_cutoff"] == 0.85
    assert ts.values["exact_match_cutoff"] == 1.0
    assert ts.values["novelty_threshold"] is UNRESOLVED
    print("  PASS: threshold set similar-search registered")


# ---------------------------------------------------------------------------
# 10. ChEMBL pagination test
# ---------------------------------------------------------------------------


def test_chembl_pagination() -> None:
    """ChEMBL pagination follows next URL."""
    from dde.commands.similar import _chembl_paginate

    page1 = {
        "molecules": [
            {"molecule_chembl_id": "CHEMBL1", "molecule_structures": {}},
            {"molecule_chembl_id": "CHEMBL2", "molecule_structures": {}},
        ],
        "page_meta": {"next": "/chembl/api/data/similarity/page2.json"},
    }
    page2 = {
        "molecules": [
            {"molecule_chembl_id": "CHEMBL3", "molecule_structures": {}},
        ],
        "page_meta": {"next": None},
    }

    with mock.patch("dde.commands.similar.http.get_json") as mock_get:
        mock_get.side_effect = [page1, page2]
        result = _chembl_paginate("https://example.com/search.json", 10)
        assert len(result) == 3
        assert result[0]["molecule_chembl_id"] == "CHEMBL1"
        assert result[2]["molecule_chembl_id"] == "CHEMBL3"
    print("  PASS: ChEMBL pagination follows next URL")


def test_chembl_pagination_cap() -> None:
    """ChEMBL pagination stops at max_results."""
    from dde.commands.similar import _chembl_paginate

    page1 = {
        "molecules": [
            {"molecule_chembl_id": f"CHEMBL{i}", "molecule_structures": {}}
            for i in range(10)
        ],
        "page_meta": {"next": "/chembl/api/data/similarity/page2.json"},
    }

    with mock.patch("dde.commands.similar.http.get_json") as mock_get:
        mock_get.return_value = page1
        result = _chembl_paginate("https://example.com/search.json", 5)
        assert len(result) == 5
    print("  PASS: ChEMBL pagination caps at max_results")


# ---------------------------------------------------------------------------
# 11. Source filtering tests
# ---------------------------------------------------------------------------


def test_source_pubchem_only() -> None:
    """--source pubchem queries only PubChem.

    PubChem similarity hits have tanimoto set to the query threshold
    as a guaranteed lower bound.
    """
    from dde.commands.similar import _pubchem_similarity

    with mock.patch("dde.commands.similar.http.get_json") as mock_get:
        mock_get.side_effect = [
            _pubchem_listkey_waiting("key1"),
            _pubchem_listkey_result([2244]),
            _pubchem_properties([2244]),
        ]
        with mock.patch("dde.commands.similar.time.sleep"):
            hits = _pubchem_similarity("CCO", 0.85, 20)
    assert len(hits) == 1
    assert hits[0]["source_db"] == "pubchem"
    assert hits[0]["tanimoto"] == 0.85  # server-filtered lower bound
    assert hits[0]["tanimoto_is_lower_bound"] is True
    print("  PASS: source pubchem only (lower-bound tanimoto)")


def test_source_chembl_only() -> None:
    """--source chembl queries only ChEMBL."""
    from dde.commands.similar import _chembl_similarity

    with mock.patch("dde.commands.similar.http.get_json") as mock_get:
        mock_get.return_value = _chembl_similarity_response()
        hits = _chembl_similarity("CCO", 0.85, 20)
    assert len(hits) == 2
    assert all(h["source_db"] == "chembl" for h in hits)
    assert hits[0]["tanimoto"] == 0.95  # 95/100
    assert hits[1]["tanimoto"] == 0.80  # 80/100
    print("  PASS: source chembl only")


# ---------------------------------------------------------------------------
# 12. CliRunner integration tests
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory for CliRunner tests."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "compounds").mkdir(parents=True, exist_ok=True)
    return project


def test_cli_search_valid_smiles() -> None:
    """CliRunner: search with valid SMILES writes artifact, exit 0."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with (
            mock.patch("dde.commands.similar._validate_smiles") as mock_validate,
            mock.patch("dde.commands.similar._pubchem_similarity") as mock_pc,
            mock.patch("dde.commands.similar._chembl_similarity") as mock_ch,
        ):
            mock_validate.return_value = "CCO"
            mock_pc.return_value = [
                {
                    "source_db": "pubchem",
                    "cid": 702,
                    "canonical_smiles": "CCO",
                    "iupac_name": "ethanol",
                    "tanimoto": 0.85,
                    "tanimoto_is_lower_bound": True,
                    "molecular_weight": 46.07,
                    "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
                },
            ]
            mock_ch.return_value = [
                {
                    "source_db": "chembl",
                    "chembl_id": "CHEMBL545",
                    "canonical_smiles": "CCO",
                    "pref_name": "ETHANOL",
                    "tanimoto": 1.0,
                    "molecular_weight": None,
                    "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
                },
            ]

            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "search", "CCO"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        artifact_path = project / "raw" / "compounds" / "cco.similar-both.json"
        assert artifact_path.is_file(), f"Artifact not found: {artifact_path}"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["schema"] == SCHEMA
        assert artifact["query"]["search_type"] == "similarity"
    print("  PASS: CLI search valid SMILES")


def test_cli_search_invalid_smiles() -> None:
    """CliRunner: search with invalid SMILES exits 9."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.similar._require_rdkit") as mock_rdkit:
            mock_chem = mock.MagicMock()
            mock_chem.MolFromSmiles.return_value = None
            mock_rdkit.return_value = mock_chem

            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "search", "INVALID"],
            )

        assert result.exit_code == 9, (
            f"Expected exit 9, got {result.exit_code}\n{result.output}"
        )
    print("  PASS: CLI search invalid SMILES -> exit 9")


def test_cli_search_source_pubchem() -> None:
    """CliRunner: --source pubchem queries only PubChem."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with (
            mock.patch("dde.commands.similar._validate_smiles") as mock_validate,
            mock.patch("dde.commands.similar._pubchem_similarity") as mock_pc,
            mock.patch("dde.commands.similar._chembl_similarity") as mock_ch,
        ):
            mock_validate.return_value = "CCO"
            mock_pc.return_value = [
                {
                    "source_db": "pubchem",
                    "cid": 702,
                    "canonical_smiles": "CCO",
                    "iupac_name": "ethanol",
                    "tanimoto": 0.85,
                    "tanimoto_is_lower_bound": True,
                    "molecular_weight": 46.07,
                    "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
                },
            ]

            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "similar",
                    "search",
                    "CCO",
                    "--source",
                    "pubchem",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        # ChEMBL should not have been called.
        mock_ch.assert_not_called()
        artifact_path = project / "raw" / "compounds" / "cco.similar-pubchem.json"
        assert artifact_path.is_file()
    print("  PASS: CLI search --source pubchem")


def test_cli_search_source_chembl() -> None:
    """CliRunner: --source chembl queries only ChEMBL."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with (
            mock.patch("dde.commands.similar._validate_smiles") as mock_validate,
            mock.patch("dde.commands.similar._pubchem_similarity") as mock_pc,
            mock.patch("dde.commands.similar._chembl_similarity") as mock_ch,
        ):
            mock_validate.return_value = "CCO"
            mock_ch.return_value = [
                {
                    "source_db": "chembl",
                    "chembl_id": "CHEMBL545",
                    "canonical_smiles": "CCO",
                    "pref_name": "ETHANOL",
                    "tanimoto": 1.0,
                    "molecular_weight": None,
                    "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
                },
            ]

            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "similar",
                    "search",
                    "CCO",
                    "--source",
                    "chembl",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        mock_pc.assert_not_called()
        artifact_path = project / "raw" / "compounds" / "cco.similar-chembl.json"
        assert artifact_path.is_file()
    print("  PASS: CLI search --source chembl")


def test_cli_substructure_valid() -> None:
    """CliRunner: substructure with valid SMILES writes artifact, exit 0."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with (
            mock.patch("dde.commands.similar._validate_smiles") as mock_validate,
            mock.patch("dde.commands.similar._pubchem_substructure") as mock_pc,
            mock.patch("dde.commands.similar._chembl_substructure") as mock_ch,
        ):
            mock_validate.return_value = "c1ccccc1"
            mock_pc.return_value = [
                {
                    "source_db": "pubchem",
                    "cid": 241,
                    "canonical_smiles": "c1ccccc1",
                    "iupac_name": "benzene",
                    "tanimoto": None,
                    "molecular_weight": 78.11,
                    "inchikey": "UHOVQNZJYSORNB-UHFFFAOYSA-N",
                },
            ]
            mock_ch.return_value = []

            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "substructure", "c1ccccc1"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        artifact_path = project / "raw" / "compounds" / "c1ccccc1.substruct-both.json"
        assert artifact_path.is_file()
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["query"]["search_type"] == "substructure"
    print("  PASS: CLI substructure valid")


def test_cli_search_json_flag() -> None:
    """CliRunner: --json flag produces valid JSON output."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with (
            mock.patch("dde.commands.similar._validate_smiles") as mock_validate,
            mock.patch("dde.commands.similar._pubchem_similarity") as mock_pc,
            mock.patch("dde.commands.similar._chembl_similarity") as mock_ch,
        ):
            mock_validate.return_value = "CCO"
            mock_pc.return_value = []
            mock_ch.return_value = []

            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "search", "CCO", "--json"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "smiles" in payload
    print("  PASS: CLI search --json")


def test_cli_search_quiet_flag() -> None:
    """CliRunner: --quiet flag suppresses human-readable output."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with (
            mock.patch("dde.commands.similar._validate_smiles") as mock_validate,
            mock.patch("dde.commands.similar._pubchem_similarity") as mock_pc,
            mock.patch("dde.commands.similar._chembl_similarity") as mock_ch,
        ):
            mock_validate.return_value = "CCO"
            mock_pc.return_value = []
            mock_ch.return_value = []

            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "search", "CCO", "--quiet"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "similarity search" not in result.output
    print("  PASS: CLI search --quiet")


# ---------------------------------------------------------------------------
# 13. Analyze CliRunner tests
# ---------------------------------------------------------------------------


def _write_search_artifact(
    project: Path,
    smiles: str = "CCO",
    source: str = "both",
    hits: list[dict[str, Any]] | None = None,
    search_type: str = "similarity",
) -> None:
    """Write a canned search artifact and sidecar for analyze tests."""
    compounds_dir = project / "raw" / "compounds"
    compounds_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(smiles)

    if hits is None:
        hits = [
            {
                "source_db": "pubchem",
                "cid": 702,
                "canonical_smiles": "CCO",
                "iupac_name": "ethanol",
                "tanimoto": 1.0,
                "molecular_weight": 46.07,
                "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            },
        ]

    artifact = _build_artifact(smiles, search_type, source, 0.85, 20, hits)
    if search_type == "similarity":
        artifact_name = f"{slug}.similar-{source}.json"
    else:
        artifact_name = f"{slug}.substruct-{source}.json"

    artifact_path = compounds_dir / artifact_name
    artifact_path.write_text(
        json.dumps(artifact, indent=2) + "\n",
        encoding="utf-8",
    )

    sidecar = {
        "tool": "similar",
        "subcommand": "search" if search_type == "similarity" else "substructure",
        "endpoint": "https://example.com",
        "parameters": {"input_smiles": smiles, "canonical_smiles": smiles},
        "outputs": [str(artifact_path)],
        "mandatory_relays": [],
    }
    meta_name = artifact_name.replace(".json", ".meta.json")
    meta_path = compounds_dir / meta_name
    meta_path.write_text(
        json.dumps(sidecar, indent=2) + "\n",
        encoding="utf-8",
    )


def test_cli_analyze_exact_match() -> None:
    """CliRunner: analyze produces exact-match verdict."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_search_artifact(
            project,
            "CCO",
            hits=[
                {
                    "source_db": "pubchem",
                    "cid": 702,
                    "canonical_smiles": "CCO",
                    "iupac_name": "ethanol",
                    "tanimoto": 1.0,
                    "molecular_weight": 46.07,
                    "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
                },
            ],
        )

        runner = CliRunner()
        with mock.patch("dde.commands.similar._validate_smiles") as mock_validate:
            mock_validate.return_value = "CCO"
            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "analyze", "CCO"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )
        assert "EXACT-MATCH" in result.output
        analysis_path = project / "raw" / "compounds" / "cco.similar.analysis.json"
        assert analysis_path.is_file()
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        assert analysis["assessment"]["outcome"] == "exact-match"
    print("  PASS: CLI analyze exact-match")


def test_cli_analyze_known_compound() -> None:
    """CliRunner: analyze produces known-compound-found verdict."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_search_artifact(
            project,
            "CCO",
            hits=[
                {
                    "source_db": "pubchem",
                    "cid": 702,
                    "canonical_smiles": "CCCO",
                    "iupac_name": "propanol",
                    "tanimoto": 0.90,
                    "molecular_weight": 60.10,
                    "inchikey": "BDERNNFJNOPAEC-UHFFFAOYSA-N",
                },
            ],
        )

        runner = CliRunner()
        with mock.patch("dde.commands.similar._validate_smiles") as mock_validate:
            mock_validate.return_value = "CCO"
            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "analyze", "CCO"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "KNOWN-COMPOUND-FOUND" in result.output
    print("  PASS: CLI analyze known-compound-found")


def test_cli_analyze_novel() -> None:
    """CliRunner: analyze produces novel verdict."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_search_artifact(
            project,
            "CCO",
            hits=[
                {
                    "source_db": "pubchem",
                    "cid": 999,
                    "canonical_smiles": "C1CCCCC1",
                    "iupac_name": "cyclohexane",
                    "tanimoto": 0.30,
                    "molecular_weight": 84.16,
                    "inchikey": "XDTMQSROBMDMFD-UHFFFAOYSA-N",
                },
            ],
        )

        runner = CliRunner()
        with mock.patch("dde.commands.similar._validate_smiles") as mock_validate:
            mock_validate.return_value = "CCO"
            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "analyze", "CCO"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "NOVEL" in result.output
    print("  PASS: CLI analyze novel")


def test_cli_analyze_json_flag() -> None:
    """CliRunner: --json flag on analyze produces valid JSON."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_search_artifact(project, "CCO")

        runner = CliRunner()
        with mock.patch("dde.commands.similar._validate_smiles") as mock_validate:
            mock_validate.return_value = "CCO"
            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "analyze", "CCO", "--json"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "assessment" in payload
    print("  PASS: CLI analyze --json")


def test_cli_analyze_quiet_flag() -> None:
    """CliRunner: --quiet flag on analyze suppresses verbose output."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_search_artifact(project, "CCO")

        runner = CliRunner()
        with mock.patch("dde.commands.similar._validate_smiles") as mock_validate:
            mock_validate.return_value = "CCO"
            result = runner.invoke(
                cli,
                ["--project", str(project), "similar", "analyze", "CCO", "--quiet"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "EXACT-MATCH" not in result.output
    print("  PASS: CLI analyze --quiet")


def test_cli_analyze_from_flag() -> None:
    """CliRunner: --from flag reads artifacts from a different directory."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        alt_dir = project / "alt-input"
        alt_dir.mkdir(parents=True, exist_ok=True)

        slug = _slug("CCO")
        hits = [
            {
                "source_db": "pubchem",
                "cid": 702,
                "canonical_smiles": "CCO",
                "iupac_name": "ethanol",
                "tanimoto": 1.0,
                "molecular_weight": 46.07,
                "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            },
        ]
        artifact = _build_artifact("CCO", "similarity", "both", 0.85, 20, hits)
        artifact_path = alt_dir / f"{slug}.similar-both.json"
        artifact_path.write_text(
            json.dumps(artifact, indent=2) + "\n",
            encoding="utf-8",
        )
        sidecar = {
            "tool": "similar",
            "subcommand": "search",
            "endpoint": "https://example.com",
            "parameters": {"input_smiles": "CCO"},
            "outputs": [],
            "mandatory_relays": [],
        }
        meta_path = alt_dir / f"{slug}.similar-both.meta.json"
        meta_path.write_text(
            json.dumps(sidecar, indent=2) + "\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        with mock.patch("dde.commands.similar._validate_smiles") as mock_validate:
            mock_validate.return_value = "CCO"
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "similar",
                    "analyze",
                    "CCO",
                    "--from",
                    str(alt_dir),
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "EXACT-MATCH" in result.output
    print("  PASS: CLI analyze --from")


def test_cli_analyze_out_flag() -> None:
    """CliRunner: --out flag writes analysis to a different directory."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _write_search_artifact(project, "CCO")

        alt_out = project / "alt-output"
        runner = CliRunner()
        with mock.patch("dde.commands.similar._validate_smiles") as mock_validate:
            mock_validate.return_value = "CCO"
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "similar",
                    "analyze",
                    "CCO",
                    "--out",
                    str(alt_out),
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        analysis_path = alt_out / "cco.similar.analysis.json"
        assert analysis_path.is_file(), (
            f"Analysis not written to --out directory: {alt_out}"
        )
    print("  PASS: CLI analyze --out")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # SMILES validation
        ("test_validate_smiles_valid", test_validate_smiles_valid),
        ("test_validate_smiles_invalid", test_validate_smiles_invalid),
        # Slug
        ("test_slug_simple", test_slug_simple),
        ("test_slug_complex", test_slug_complex),
        ("test_slug_short_smiles", test_slug_short_smiles),
        # PubChem async polling
        ("test_poll_pubchem_listkey_immediate", test_poll_pubchem_listkey_immediate),
        ("test_poll_pubchem_listkey_waiting", test_poll_pubchem_listkey_waiting),
        ("test_poll_pubchem_listkey_timeout", test_poll_pubchem_listkey_timeout),
        ("test_poll_pubchem_listkey_fault", test_poll_pubchem_listkey_fault),
        # Merge hits
        ("test_merge_hits_dedup", test_merge_hits_dedup),
        ("test_merge_hits_cap", test_merge_hits_cap),
        ("test_merge_hits_sort_none_tanimoto", test_merge_hits_sort_none_tanimoto),
        # Build artifact
        ("test_build_artifact_schema", test_build_artifact_schema),
        ("test_build_artifact_no_hits", test_build_artifact_no_hits),
        (
            "test_build_artifact_substructure_no_threshold",
            test_build_artifact_substructure_no_threshold,
        ),
        # Classification
        ("test_classify_exact_match", test_classify_exact_match),
        ("test_classify_known_compound_found", test_classify_known_compound_found),
        ("test_classify_novel", test_classify_novel),
        ("test_classify_novel_no_hits", test_classify_novel_no_hits),
        ("test_classify_novel_none_tanimoto", test_classify_novel_none_tanimoto),
        ("test_classify_pubchem_lower_bound", test_classify_pubchem_lower_bound),
        # Relay guards
        ("test_relay_tanimoto_fires_with_hits", test_relay_tanimoto_fires_with_hits),
        ("test_relay_tanimoto_silent_no_hits", test_relay_tanimoto_silent_no_hits),
        ("test_relay_coverage_always_fires", test_relay_coverage_always_fires),
        # Registration
        ("test_relay_codes_registered", test_relay_codes_registered),
        ("test_threshold_set_registered", test_threshold_set_registered),
        # ChEMBL pagination
        ("test_chembl_pagination", test_chembl_pagination),
        ("test_chembl_pagination_cap", test_chembl_pagination_cap),
        # Source filtering
        ("test_source_pubchem_only", test_source_pubchem_only),
        ("test_source_chembl_only", test_source_chembl_only),
        # CliRunner integration — search
        ("test_cli_search_valid_smiles", test_cli_search_valid_smiles),
        ("test_cli_search_invalid_smiles", test_cli_search_invalid_smiles),
        ("test_cli_search_source_pubchem", test_cli_search_source_pubchem),
        ("test_cli_search_source_chembl", test_cli_search_source_chembl),
        ("test_cli_substructure_valid", test_cli_substructure_valid),
        ("test_cli_search_json_flag", test_cli_search_json_flag),
        ("test_cli_search_quiet_flag", test_cli_search_quiet_flag),
        # CliRunner integration — analyze
        ("test_cli_analyze_exact_match", test_cli_analyze_exact_match),
        ("test_cli_analyze_known_compound", test_cli_analyze_known_compound),
        ("test_cli_analyze_novel", test_cli_analyze_novel),
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
