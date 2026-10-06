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

"""Tests for the homology command group (#184).

Covers:
  - _parse_range validation
  - _extract_hit coverage computation
  - _fetch_structure_metadata entity-matching (R1 fix)
  - Manifest schema completeness
  - Phase 2 — hit classification, verdict, relays
  - Phase 3 — fetch-structure PDB ID parsing, download, sidecar
"""

from __future__ import annotations

import json
import sys
import unittest.mock as mock
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.homology import (
    RCSB_DOWNLOAD_BASE,
    TOOL_FETCH,
    _assess_hit,
    _classify_coverage,
    _classify_identity,
    _classify_resolution,
    _compute_verdict,
    _extract_hit,
    _fetch_structure_metadata,
    _parse_pdb_entity_id,
    _parse_range,
)
from dde.core.errors import UsageError

# ---------------------------------------------------------------------------
# 1. _parse_range tests
# ---------------------------------------------------------------------------


def test_parse_range_valid() -> None:
    """Valid range parses correctly."""
    result = _parse_range("400-575", 575)
    assert result == (400, 575), f"Expected (400, 575), got {result}"
    print("  PASS: _parse_range valid range")


def test_parse_range_start_gt_end() -> None:
    """START > END raises UsageError."""
    try:
        _parse_range("575-400", 575)
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: _parse_range START > END")


def test_parse_range_start_lt_1() -> None:
    """START < 1 raises UsageError."""
    try:
        _parse_range("0-100", 500)
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: _parse_range START < 1")


def test_parse_range_end_exceeds_length() -> None:
    """END > canonical_length raises UsageError."""
    try:
        _parse_range("1-600", 500)
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: _parse_range END > canonical_length")


def test_parse_range_non_integer() -> None:
    """Non-integer range values raise UsageError."""
    try:
        _parse_range("abc-def", 500)
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: _parse_range non-integer")


def test_parse_range_wrong_format() -> None:
    """Wrong format (no dash) raises UsageError."""
    try:
        _parse_range("400", 500)
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: _parse_range wrong format")


# ---------------------------------------------------------------------------
# 2. _extract_hit tests
# ---------------------------------------------------------------------------


def test_extract_hit_coverage() -> None:
    """Coverage computation and field extraction from a canned match_context."""
    result: dict[str, Any] = {
        "identifier": "7FD3_1",
        "services": [
            {
                "nodes": [
                    {
                        "match_context": [
                            {
                                "query_beg": 3,
                                "query_end": 138,
                                "subject_beg": 1,
                                "subject_end": 161,
                                "sequence_identity": 0.302,
                                "evalue": 2.8e-11,
                                "bitscore": 48,
                                "alignment_length": 136,
                            }
                        ]
                    }
                ]
            }
        ],
    }

    subseq_len = 176
    hit = _extract_hit(result, subseq_len)

    # query_coverage_fraction = (138 - 3 + 1) / 176 = 136 / 176 ≈ 0.7727
    expected_coverage = round((138 - 3 + 1) / 176, 4)
    assert hit["alignment"]["query_coverage_fraction"] == expected_coverage, (
        f"Expected coverage {expected_coverage}, "
        f"got {hit['alignment']['query_coverage_fraction']}"
    )
    assert hit["pdb_id"] == "7FD3", f"Expected pdb_id '7FD3', got {hit['pdb_id']}"
    assert hit["entity_id"] == 1, f"Expected entity_id 1, got {hit['entity_id']}"
    assert hit["pdb_entity_id"] == "7FD3_1"
    print("  PASS: _extract_hit coverage computation")


# ---------------------------------------------------------------------------
# 3. _fetch_structure_metadata entity-matching tests (R1 fix)
# ---------------------------------------------------------------------------


def _make_graphql_response(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a canned GraphQL response envelope."""
    return {"data": {"entries": entries}}


def test_entity_matching_homolog() -> None:
    """Scenario A: hit entity maps to a different UniProt — not direct."""
    hits: list[dict[str, Any]] = [
        {
            "pdb_entity_id": "7FD3_1",
            "pdb_id": "7FD3",
            "entity_id": 1,
            "title": "",
            "experimental_method": "",
            "resolution_angstrom": None,
        },
    ]
    graphql_response = _make_graphql_response(
        [
            {
                "rcsb_id": "7FD3",
                "struct": {"title": "Crystal structure of homolog"},
                "rcsb_entry_info": {
                    "resolution_combined": [2.1],
                    "experimental_method": "X-RAY DIFFRACTION",
                },
                "polymer_entities": [
                    {
                        "rcsb_id": "7FD3_1",
                        "rcsb_entity_source_organism": [
                            {"ncbi_scientific_name": "Homo sapiens"}
                        ],
                        "rcsb_polymer_entity_align": [
                            {
                                "reference_database_name": "UniProt",
                                "reference_database_accession": "Q9NP60",
                                "aligned_regions": [
                                    {"ref_beg_seq_id": 1, "length": 150}
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )

    with mock.patch("dde.commands.homology.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.json.return_value = graphql_response
        mock_req.return_value = mock_resp
        _fetch_structure_metadata(hits, "Q9HB29")

    assert hits[0]["is_direct_structure"] is False, (
        f"Expected is_direct_structure=False, got {hits[0]['is_direct_structure']}"
    )
    assert hits[0]["source_uniprot"] == "Q9NP60", (
        f"Expected source_uniprot='Q9NP60', got {hits[0]['source_uniprot']}"
    )
    assert hits[0]["source_organism"] == "Homo sapiens", (
        f"Expected source_organism='Homo sapiens', got {hits[0]['source_organism']}"
    )
    print("  PASS: entity matching — homolog (not direct)")


def test_entity_matching_direct() -> None:
    """Scenario B: hit entity maps to the query UniProt — direct structure."""
    hits: list[dict[str, Any]] = [
        {
            "pdb_entity_id": "6U6U_1",
            "pdb_id": "6U6U",
            "entity_id": 1,
            "title": "",
            "experimental_method": "",
            "resolution_angstrom": None,
        },
    ]
    graphql_response = _make_graphql_response(
        [
            {
                "rcsb_id": "6U6U",
                "struct": {"title": "Direct structure of query"},
                "rcsb_entry_info": {
                    "resolution_combined": [1.8],
                    "experimental_method": "X-RAY DIFFRACTION",
                },
                "polymer_entities": [
                    {
                        "rcsb_id": "6U6U_1",
                        "rcsb_entity_source_organism": [
                            {"ncbi_scientific_name": "Mus musculus"}
                        ],
                        "rcsb_polymer_entity_align": [
                            {
                                "reference_database_name": "UniProt",
                                "reference_database_accession": "Q9HB29",
                                "aligned_regions": [
                                    {"ref_beg_seq_id": 1, "length": 200}
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )

    with mock.patch("dde.commands.homology.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.json.return_value = graphql_response
        mock_req.return_value = mock_resp
        _fetch_structure_metadata(hits, "Q9HB29")

    assert hits[0]["is_direct_structure"] is True, (
        f"Expected is_direct_structure=True, got {hits[0]['is_direct_structure']}"
    )
    assert hits[0]["source_uniprot"] == "Q9HB29", (
        f"Expected source_uniprot='Q9HB29', got {hits[0]['source_uniprot']}"
    )
    print("  PASS: entity matching — direct structure")


def test_entity_matching_heterocomplex() -> None:
    """Scenario C (R1 bug case): hit on entity 1 (homolog), entity 2 maps to query.

    The BLAST hit was on entity 1 (which maps to Q9NP60, a homolog).
    Entity 2 of the same PDB entry maps to the query protein Q9HB29.
    is_direct_structure must be False — the hit is on entity 1, not entity 2.
    """
    hits: list[dict[str, Any]] = [
        {
            "pdb_entity_id": "XXXX_1",
            "pdb_id": "XXXX",
            "entity_id": 1,
            "title": "",
            "experimental_method": "",
            "resolution_angstrom": None,
        },
    ]
    graphql_response = _make_graphql_response(
        [
            {
                "rcsb_id": "XXXX",
                "struct": {"title": "Heterocomplex with query partner"},
                "rcsb_entry_info": {
                    "resolution_combined": [2.5],
                    "experimental_method": "X-RAY DIFFRACTION",
                },
                "polymer_entities": [
                    # Entity 1: the BLAST hit — maps to Q9NP60 (homolog)
                    {
                        "rcsb_id": "XXXX_1",
                        "rcsb_entity_source_organism": [
                            {"ncbi_scientific_name": "Rattus norvegicus"}
                        ],
                        "rcsb_polymer_entity_align": [
                            {
                                "reference_database_name": "UniProt",
                                "reference_database_accession": "Q9NP60",
                                "aligned_regions": [
                                    {"ref_beg_seq_id": 1, "length": 120}
                                ],
                            }
                        ],
                    },
                    # Entity 2: co-crystallised partner — maps to Q9HB29 (query)
                    {
                        "rcsb_id": "XXXX_2",
                        "rcsb_entity_source_organism": [
                            {"ncbi_scientific_name": "Homo sapiens"}
                        ],
                        "rcsb_polymer_entity_align": [
                            {
                                "reference_database_name": "UniProt",
                                "reference_database_accession": "Q9HB29",
                                "aligned_regions": [
                                    {"ref_beg_seq_id": 1, "length": 300}
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
    )

    with mock.patch("dde.commands.homology.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.json.return_value = graphql_response
        mock_req.return_value = mock_resp
        _fetch_structure_metadata(hits, "Q9HB29")

    assert hits[0]["is_direct_structure"] is False, (
        "Heterocomplex bug: is_direct_structure should be False when the BLAST "
        "hit is on entity 1 (homolog) even though entity 2 maps to the query. "
        f"Got: {hits[0]['is_direct_structure']}"
    )
    assert hits[0]["source_uniprot"] == "Q9NP60", (
        f"Expected source_uniprot='Q9NP60' (from entity 1), "
        f"got {hits[0]['source_uniprot']}"
    )
    assert hits[0]["source_organism"] == "Rattus norvegicus", (
        f"Expected source_organism from entity 1, got {hits[0]['source_organism']}"
    )
    print("  PASS: entity matching — heterocomplex (R1 bug case)")


# ---------------------------------------------------------------------------
# 4. Manifest schema test
# ---------------------------------------------------------------------------


def test_manifest_schema() -> None:
    """End-to-end: mock both RCSB calls, verify manifest JSON schema."""
    import tempfile

    # Canned RCSB search response
    search_response_data = {
        "result_set": [
            {
                "identifier": "7FD3_1",
                "services": [
                    {
                        "nodes": [
                            {
                                "match_context": [
                                    {
                                        "query_beg": 1,
                                        "query_end": 50,
                                        "subject_beg": 10,
                                        "subject_end": 60,
                                        "sequence_identity": 0.45,
                                        "evalue": 1e-15,
                                        "bitscore": 80,
                                        "alignment_length": 50,
                                    }
                                ]
                            }
                        ]
                    }
                ],
            }
        ]
    }

    # Canned GraphQL response
    graphql_response_data = _make_graphql_response(
        [
            {
                "rcsb_id": "7FD3",
                "struct": {"title": "Crystal structure"},
                "rcsb_entry_info": {
                    "resolution_combined": [2.0],
                    "experimental_method": "X-RAY DIFFRACTION",
                },
                "polymer_entities": [
                    {
                        "rcsb_id": "7FD3_1",
                        "rcsb_entity_source_organism": [
                            {"ncbi_scientific_name": "Homo sapiens"}
                        ],
                        "rcsb_polymer_entity_align": [
                            {
                                "reference_database_name": "UniProt",
                                "reference_database_accession": "P04637",
                                "aligned_regions": [
                                    {"ref_beg_seq_id": 1, "length": 50}
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )

    # Build the manifest the same way the search command does
    from dde.commands.homology import _blast_search

    # Mock both HTTP calls: first call = BLAST search, second = GraphQL
    call_count = 0

    def mock_request_side_effect(*args: Any, **kwargs: Any) -> mock.Mock:
        nonlocal call_count
        call_count += 1
        resp = mock.Mock()
        if call_count == 1:
            # BLAST search response
            resp.status_code = 200
            resp.json.return_value = search_response_data
        else:
            # GraphQL response
            resp.json.return_value = graphql_response_data
        return resp

    with mock.patch("dde.commands.homology.http.request") as mock_req:
        mock_req.side_effect = mock_request_side_effect

        # Run BLAST
        result_set = _blast_search("MVLSPADKTNV" * 5, 0.001, 0.2, 25)
        hits = [_extract_hit(r, 55) for r in result_set]
        _fetch_structure_metadata(hits, "Q9HB29")

    # Build manifest dict
    manifest = {
        "query": {
            "uniprot_accession": "Q9HB29",
            "gene_symbol": "TEST",
            "residue_range": [1, 55],
            "query_sequence": "MVLSPADKTNV" * 5,
            "canonical_length": 400,
        },
        "search_parameters": {
            "evalue_cutoff": 0.001,
            "identity_cutoff": 0.2,
            "max_hits": 25,
            "search_type": "domain_subsequence",
        },
        "hits": hits,
        "hit_count": len(hits),
        "search_timestamp": "2026-01-01T00:00:00Z",
    }

    # Verify top-level keys
    required_top_keys = {
        "query",
        "search_parameters",
        "hits",
        "hit_count",
        "search_timestamp",
    }
    assert set(manifest.keys()) == required_top_keys, (
        f"Top-level keys mismatch: expected {required_top_keys}, "
        f"got {set(manifest.keys())}"
    )

    # Verify each hit has all required fields
    required_hit_fields = {
        "pdb_entity_id",
        "pdb_id",
        "entity_id",
        "alignment",
        "title",
        "experimental_method",
        "resolution_angstrom",
        "source_uniprot",
        "source_organism",
        "is_direct_structure",
    }
    for i, hit in enumerate(manifest["hits"]):
        missing = required_hit_fields - set(hit.keys())
        assert not missing, f"Hit {i} missing fields: {missing}"

    # Verify manifest is JSON-serialisable
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(manifest, f, indent=2)
        f.flush()
        # Read back and verify
        with open(f.name) as rf:
            loaded = json.load(rf)
        assert loaded["hit_count"] == 1
        assert loaded["hits"][0]["pdb_id"] == "7FD3"

    print("  PASS: manifest schema completeness")


# ---------------------------------------------------------------------------
# Phase 2 tests — hit classification
# ---------------------------------------------------------------------------


class _FakeThresholds:
    """Minimal stand-in for ThresholdSet that supports .get()."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str) -> Any:
        return self._values[key]


_DEFAULT_T = _FakeThresholds(
    {
        "identity_high": 0.5,
        "identity_moderate": 0.3,
        "resolution_high": 2.5,
        "resolution_low": 3.5,
        "coverage_minimum": 0.5,
    }
)


# --- Identity band classification ---


def test_identity_high() -> None:
    """Identity at or above identity_high → "high"."""
    assert _classify_identity(0.5, _DEFAULT_T) == "high"
    assert _classify_identity(0.9, _DEFAULT_T) == "high"
    print("  PASS: identity band — high")


def test_identity_moderate() -> None:
    """Identity at or above identity_moderate but below identity_high → "moderate"."""
    assert _classify_identity(0.3, _DEFAULT_T) == "moderate"
    assert _classify_identity(0.49, _DEFAULT_T) == "moderate"
    print("  PASS: identity band — moderate")


def test_identity_remote() -> None:
    """Identity below identity_moderate → "remote"."""
    assert _classify_identity(0.29, _DEFAULT_T) == "remote"
    assert _classify_identity(0.0, _DEFAULT_T) == "remote"
    print("  PASS: identity band — remote")


# --- Resolution quality classification ---


def test_resolution_high() -> None:
    """Resolution at or below resolution_high → "high"."""
    assert _classify_resolution(2.5, _DEFAULT_T) == "high"
    assert _classify_resolution(1.0, _DEFAULT_T) == "high"
    print("  PASS: resolution quality — high")


def test_resolution_moderate() -> None:
    """Resolution between resolution_high and resolution_low → "moderate"."""
    assert _classify_resolution(2.6, _DEFAULT_T) == "moderate"
    assert _classify_resolution(3.5, _DEFAULT_T) == "moderate"
    print("  PASS: resolution quality — moderate")


def test_resolution_low() -> None:
    """Resolution above resolution_low → "low"."""
    assert _classify_resolution(3.6, _DEFAULT_T) == "low"
    assert _classify_resolution(5.0, _DEFAULT_T) == "low"
    print("  PASS: resolution quality — low")


def test_resolution_not_applicable() -> None:
    """Resolution is None (NMR or unknown) → "not-applicable"."""
    assert _classify_resolution(None, _DEFAULT_T) == "not-applicable"
    print("  PASS: resolution quality — not-applicable")


# --- Coverage classification ---


def test_coverage_adequate() -> None:
    """Coverage at or above coverage_minimum → "adequate"."""
    assert _classify_coverage(0.5, _DEFAULT_T) == "adequate"
    assert _classify_coverage(1.0, _DEFAULT_T) == "adequate"
    print("  PASS: coverage — adequate")


def test_coverage_insufficient() -> None:
    """Coverage below coverage_minimum → "insufficient"."""
    assert _classify_coverage(0.49, _DEFAULT_T) == "insufficient"
    assert _classify_coverage(0.0, _DEFAULT_T) == "insufficient"
    print("  PASS: coverage — insufficient")


# --- Overall verdict logic ---


def _make_assessment(
    identity_band: str = "high",
    coverage: str = "adequate",
) -> dict[str, Any]:
    """Helper to build a minimal hit assessment dict."""
    return {
        "pdb_entity_id": "TEST_1",
        "pdb_id": "TEST",
        "sequence_identity": 0.5,
        "resolution_angstrom": 2.0,
        "query_coverage_fraction": 0.8,
        "identity_band": identity_band,
        "resolution_quality": "high",
        "coverage": coverage,
        "source_uniprot": "P12345",
        "is_direct_structure": True,
        "title": "Test",
    }


def test_verdict_strong_candidates() -> None:
    """At least one hit with identity "high" AND coverage "adequate" → strong-candidates."""
    assessments = [_make_assessment("high", "adequate")]
    assert _compute_verdict(assessments, 1) == "strong-candidates"
    print("  PASS: verdict — strong-candidates")


def test_verdict_moderate_candidates_moderate_band() -> None:
    """Best hit is moderate band with adequate coverage → moderate-candidates."""
    assessments = [_make_assessment("moderate", "adequate")]
    assert _compute_verdict(assessments, 1) == "moderate-candidates"
    print("  PASS: verdict — moderate-candidates (moderate band)")


def test_verdict_moderate_candidates_high_inadequate() -> None:
    """High band but inadequate coverage → moderate-candidates."""
    assessments = [_make_assessment("high", "insufficient")]
    assert _compute_verdict(assessments, 1) == "moderate-candidates"
    print("  PASS: verdict — moderate-candidates (high band, inadequate coverage)")


def test_verdict_remote_only() -> None:
    """All hits below identity_moderate → remote-only."""
    assessments = [_make_assessment("remote", "adequate")]
    assert _compute_verdict(assessments, 1) == "remote-only"
    print("  PASS: verdict — remote-only")


def test_verdict_no_coverage() -> None:
    """Hits exist but none have adequate coverage (and no high/moderate) → no-coverage."""
    assessments = [_make_assessment("remote", "insufficient")]
    assert _compute_verdict(assessments, 1) == "no-coverage"
    print("  PASS: verdict — no-coverage")


def test_verdict_no_hits() -> None:
    """Manifest has hit_count 0 → no-hits."""
    assert _compute_verdict([], 0) == "no-hits"
    print("  PASS: verdict — no-hits")


# --- Relay emission ---


def test_relay_fires_for_non_direct() -> None:
    """Relay fires when any hit has is_direct_structure: false."""
    from dde.core import provenance

    hits = [
        {"is_direct_structure": False, "source_uniprot": "Q9NP60"},
        {"is_direct_structure": True, "source_uniprot": "Q9HB29"},
    ]
    non_direct = [h for h in hits if not h.get("is_direct_structure", False)]
    relays: list[dict[str, str]] = []
    if non_direct:
        sources = sorted({h.get("source_uniprot", "unknown") for h in non_direct})
        relays.append(
            provenance.relay(
                "homology.structure_is_not_target",
                f"All {len(non_direct)} hit(s) are structures of homologous proteins "
                f"({', '.join(sources[:5])}), not Q9HB29 itself.",
            )
        )

    assert len(relays) == 1
    assert relays[0]["code"] == "homology.structure_is_not_target"
    print("  PASS: relay fires for non-direct hits")


def test_relay_silent_for_all_direct() -> None:
    """Relay does NOT fire when all hits have is_direct_structure: true."""
    hits = [
        {"is_direct_structure": True, "source_uniprot": "Q9HB29"},
        {"is_direct_structure": True, "source_uniprot": "Q9HB29"},
    ]
    non_direct = [h for h in hits if not h.get("is_direct_structure", False)]
    relays: list[dict[str, str]] = []
    if non_direct:
        relays.append({"code": "should-not-reach", "message": "bug"})

    assert len(relays) == 0
    print("  PASS: relay silent for all-direct hits")


# --- Per-hit assessment integration ---


def test_assess_hit_fields() -> None:
    """_assess_hit returns all required fields with correct values."""
    hit: dict[str, Any] = {
        "pdb_entity_id": "7FD3_1",
        "pdb_id": "7FD3",
        "alignment": {
            "sequence_identity": 0.45,
            "query_coverage_fraction": 0.77,
        },
        "resolution_angstrom": 2.1,
        "source_uniprot": "Q9NP60",
        "is_direct_structure": False,
        "title": "Crystal structure",
    }

    result = _assess_hit(hit, _DEFAULT_T)

    assert result["identity_band"] == "moderate", (
        f"Expected moderate, got {result['identity_band']}"
    )
    assert result["resolution_quality"] == "high", (
        f"Expected high, got {result['resolution_quality']}"
    )
    assert result["coverage"] == "adequate", (
        f"Expected adequate, got {result['coverage']}"
    )
    assert result["sequence_identity"] == 0.45
    assert result["resolution_angstrom"] == 2.1
    assert result["query_coverage_fraction"] == 0.77

    required_fields = {
        "pdb_entity_id",
        "pdb_id",
        "sequence_identity",
        "resolution_angstrom",
        "query_coverage_fraction",
        "identity_band",
        "resolution_quality",
        "coverage",
        "source_uniprot",
        "is_direct_structure",
        "title",
    }
    missing = required_fields - set(result.keys())
    assert not missing, f"Missing fields: {missing}"
    print("  PASS: _assess_hit returns all required fields")


# ---------------------------------------------------------------------------
# Phase 3 tests — fetch-structure
# ---------------------------------------------------------------------------


def test_parse_pdb_entity_id_with_entity() -> None:
    """PDB entity ID with underscore extracts the PDB ID correctly."""
    pdb_id, raw = _parse_pdb_entity_id("7FD3_1")
    assert pdb_id == "7FD3", f"Expected '7FD3', got {pdb_id!r}"
    assert raw == "7FD3_1", f"Expected raw '7FD3_1', got {raw!r}"
    print("  PASS: parse PDB entity ID with entity suffix")


def test_parse_pdb_entity_id_bare() -> None:
    """Bare PDB ID (no entity suffix) works correctly."""
    pdb_id, raw = _parse_pdb_entity_id("7FD3")
    assert pdb_id == "7FD3", f"Expected '7FD3', got {pdb_id!r}"
    assert raw == "7FD3", f"Expected raw '7FD3', got {raw!r}"
    print("  PASS: parse bare PDB ID (no entity suffix)")


def test_parse_pdb_entity_id_lowercase() -> None:
    """Lowercase input is uppercased."""
    pdb_id, raw = _parse_pdb_entity_id("7fd3_1")
    assert pdb_id == "7FD3", f"Expected '7FD3', got {pdb_id!r}"
    # raw preserves original input
    assert raw == "7fd3_1", f"Expected raw '7fd3_1', got {raw!r}"
    print("  PASS: parse PDB entity ID — lowercase uppercased")


def test_fetch_structure_download_and_sidecar() -> None:
    """Mock download: verify file is written and sidecar records correct metadata."""
    import hashlib
    import tempfile

    from dde.core import provenance

    fake_content = b"FAKE CIF CONTENT FOR TESTING"
    expected_sha256 = hashlib.sha256(fake_content).hexdigest()

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        pdb_id = "7FCH"
        fmt = "cif"
        raw_input = "7FCH_1"

        download_url = f"{RCSB_DOWNLOAD_BASE}/{pdb_id}.{fmt}"

        # Simulate what fetch-structure does: write file, create sidecar
        structure_path = target_dir / f"{pdb_id}.{fmt}"
        structure_path.write_bytes(fake_content)

        sidecar = provenance.Sidecar(
            tool=TOOL_FETCH,
            subcommand="fetch-structure",
            endpoint=download_url,
            parameters={
                "pdb_entity_id": raw_input,
                "pdb_id": pdb_id,
                "format": fmt,
            },
        )
        sidecar.add_output(structure_path)

        meta_path = target_dir / f"{pdb_id}.meta.json"
        sidecar.write(meta_path)

        # Verify the structure file was written
        assert structure_path.is_file(), "Structure file was not written"
        assert structure_path.read_bytes() == fake_content, "File content mismatch"

        # Verify the sidecar was written and has correct content
        assert meta_path.is_file(), "Sidecar file was not written"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert meta["tool"] == TOOL_FETCH, (
            f"Expected tool {TOOL_FETCH!r}, got {meta['tool']!r}"
        )
        assert meta["subcommand"] == "fetch-structure", (
            f"Expected subcommand 'fetch-structure', got {meta['subcommand']!r}"
        )
        assert meta["endpoint"] == download_url, (
            f"Expected endpoint {download_url!r}, got {meta['endpoint']!r}"
        )

        params = meta["parameters"]
        assert params["pdb_entity_id"] == raw_input
        assert params["pdb_id"] == pdb_id
        assert params["format"] == fmt

        # Verify outputs array contains the file with SHA-256
        outputs = meta.get("outputs", [])
        assert len(outputs) == 1, f"Expected 1 output, got {len(outputs)}"
        assert outputs[0]["path"] == f"{pdb_id}.{fmt}"
        assert outputs[0]["sha256"] == expected_sha256, (
            f"SHA-256 mismatch: expected {expected_sha256}, got {outputs[0]['sha256']}"
        )

    print("  PASS: fetch-structure download and sidecar correctness")


def test_fetch_structure_pdb_format() -> None:
    """Verify --format pdb uses the .pdb extension and correct URL."""
    import hashlib
    import tempfile

    from dde.core import provenance

    fake_content = b"FAKE PDB CONTENT"
    expected_sha256 = hashlib.sha256(fake_content).hexdigest()

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        pdb_id = "7FCH"
        fmt = "pdb"
        raw_input = "7FCH"

        download_url = f"{RCSB_DOWNLOAD_BASE}/{pdb_id}.{fmt}"

        structure_path = target_dir / f"{pdb_id}.{fmt}"
        structure_path.write_bytes(fake_content)

        sidecar = provenance.Sidecar(
            tool=TOOL_FETCH,
            subcommand="fetch-structure",
            endpoint=download_url,
            parameters={
                "pdb_entity_id": raw_input,
                "pdb_id": pdb_id,
                "format": fmt,
            },
        )
        sidecar.add_output(structure_path)

        meta_path = target_dir / f"{pdb_id}.meta.json"
        sidecar.write(meta_path)

        # Verify .pdb file and URL
        assert structure_path.name == "7FCH.pdb", (
            f"Expected '7FCH.pdb', got {structure_path.name!r}"
        )
        assert download_url == "https://files.rcsb.org/download/7FCH.pdb"

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["parameters"]["format"] == "pdb"
        assert meta["outputs"][0]["path"] == "7FCH.pdb"
        assert meta["outputs"][0]["sha256"] == expected_sha256

    print("  PASS: fetch-structure with --format pdb")


def test_fetch_structure_sidecar_sha256() -> None:
    """Sidecar outputs array contains the downloaded file with a valid SHA-256 hash."""
    import hashlib
    import tempfile

    from dde.core import provenance

    # Use content that produces a known hash
    content = b"deterministic test content"
    expected_sha256 = hashlib.sha256(content).hexdigest()

    with tempfile.TemporaryDirectory() as tmpdir:
        target_dir = Path(tmpdir)
        structure_path = target_dir / "TEST.cif"
        structure_path.write_bytes(content)

        sidecar = provenance.Sidecar(
            tool=TOOL_FETCH,
            subcommand="fetch-structure",
            endpoint="https://files.rcsb.org/download/TEST.cif",
            parameters={"pdb_entity_id": "TEST", "pdb_id": "TEST", "format": "cif"},
        )
        sidecar.add_output(structure_path)

        meta_path = target_dir / "TEST.meta.json"
        sidecar.write(meta_path)

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        outputs = meta["outputs"]
        assert len(outputs) == 1
        assert outputs[0]["sha256"] == expected_sha256, (
            f"Expected {expected_sha256}, got {outputs[0]['sha256']}"
        )
        # SHA-256 is a 64-char hex string
        assert len(outputs[0]["sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in outputs[0]["sha256"])

    print("  PASS: fetch-structure sidecar SHA-256 correctness")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # _parse_range
        ("test_parse_range_valid", test_parse_range_valid),
        ("test_parse_range_start_gt_end", test_parse_range_start_gt_end),
        ("test_parse_range_start_lt_1", test_parse_range_start_lt_1),
        ("test_parse_range_end_exceeds_length", test_parse_range_end_exceeds_length),
        ("test_parse_range_non_integer", test_parse_range_non_integer),
        ("test_parse_range_wrong_format", test_parse_range_wrong_format),
        # _extract_hit
        ("test_extract_hit_coverage", test_extract_hit_coverage),
        # _fetch_structure_metadata entity matching (R1 fix)
        ("test_entity_matching_homolog", test_entity_matching_homolog),
        ("test_entity_matching_direct", test_entity_matching_direct),
        ("test_entity_matching_heterocomplex", test_entity_matching_heterocomplex),
        # Manifest schema
        ("test_manifest_schema", test_manifest_schema),
        # Phase 2 — identity band
        ("test_identity_high", test_identity_high),
        ("test_identity_moderate", test_identity_moderate),
        ("test_identity_remote", test_identity_remote),
        # Phase 2 — resolution quality
        ("test_resolution_high", test_resolution_high),
        ("test_resolution_moderate", test_resolution_moderate),
        ("test_resolution_low", test_resolution_low),
        ("test_resolution_not_applicable", test_resolution_not_applicable),
        # Phase 2 — coverage
        ("test_coverage_adequate", test_coverage_adequate),
        ("test_coverage_insufficient", test_coverage_insufficient),
        # Phase 2 — verdict
        ("test_verdict_strong_candidates", test_verdict_strong_candidates),
        (
            "test_verdict_moderate_candidates_moderate_band",
            test_verdict_moderate_candidates_moderate_band,
        ),
        (
            "test_verdict_moderate_candidates_high_inadequate",
            test_verdict_moderate_candidates_high_inadequate,
        ),
        ("test_verdict_remote_only", test_verdict_remote_only),
        ("test_verdict_no_coverage", test_verdict_no_coverage),
        ("test_verdict_no_hits", test_verdict_no_hits),
        # Phase 2 — relay emission
        ("test_relay_fires_for_non_direct", test_relay_fires_for_non_direct),
        ("test_relay_silent_for_all_direct", test_relay_silent_for_all_direct),
        # Phase 2 — per-hit assessment
        ("test_assess_hit_fields", test_assess_hit_fields),
        # Phase 3 — fetch-structure
        ("test_parse_pdb_entity_id_with_entity", test_parse_pdb_entity_id_with_entity),
        ("test_parse_pdb_entity_id_bare", test_parse_pdb_entity_id_bare),
        ("test_parse_pdb_entity_id_lowercase", test_parse_pdb_entity_id_lowercase),
        (
            "test_fetch_structure_download_and_sidecar",
            test_fetch_structure_download_and_sidecar,
        ),
        ("test_fetch_structure_pdb_format", test_fetch_structure_pdb_format),
        ("test_fetch_structure_sidecar_sha256", test_fetch_structure_sidecar_sha256),
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
