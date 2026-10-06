#!/usr/bin/env python3
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

"""Tests for GPCR topology annotation (#94 item 2).

Covers:
1. UniProt feature parsing for TM regions
2. Residue-to-TM mapping
3. Bundle-void advisory threshold
4. Gene symbol to accession resolution (mock)
5. Pocket with no TM residues (non-GPCR target)
6. Orthosteric candidate identification

Run with:
    PYTHONPATH=tools python3 tests/test_topology_annotation.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.structure import (
    map_pocket_to_topology,
    parse_tm_regions,
    parse_topo_domains,
    residue_to_tm,
    resolve_accession,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _check(name: str, fn: Any) -> None:
    global _PASS, _FAIL
    try:
        fn()
        _PASS += 1
        print(f"  PASS  {name}")
    except Exception:
        _FAIL += 1
        print(f"  FAIL  {name}")
        traceback.print_exc()
        print()


# ---------------------------------------------------------------------------
# Fixtures — mock UniProt features
# ---------------------------------------------------------------------------

# Typical Class A GPCR TM features (simplified, modeled on beta-2 adrenergic receptor)
GPCR_FEATURES: list[dict[str, Any]] = [
    {
        "type": "Transmembrane",
        "location": {"start": {"value": 34}, "end": {"value": 58}},
        "description": "Helical; Name=1",
    },
    {
        "type": "Transmembrane",
        "location": {"start": {"value": 71}, "end": {"value": 100}},
        "description": "Helical; Name=2",
    },
    {
        "type": "Transmembrane",
        "location": {"start": {"value": 107}, "end": {"value": 137}},
        "description": "Helical; Name=3",
    },
    {
        "type": "Transmembrane",
        "location": {"start": {"value": 151}, "end": {"value": 173}},
        "description": "Helical; Name=4",
    },
    {
        "type": "Transmembrane",
        "location": {"start": {"value": 197}, "end": {"value": 229}},
        "description": "Helical; Name=5",
    },
    {
        "type": "Transmembrane",
        "location": {"start": {"value": 241}, "end": {"value": 276}},
        "description": "Helical; Name=6",
    },
    {
        "type": "Transmembrane",
        "location": {"start": {"value": 286}, "end": {"value": 311}},
        "description": "Helical; Name=7",
    },
    # Topological domains interspersed
    {
        "type": "Topological domain",
        "location": {"start": {"value": 1}, "end": {"value": 33}},
        "description": "Extracellular",
    },
    {
        "type": "Topological domain",
        "location": {"start": {"value": 59}, "end": {"value": 70}},
        "description": "Cytoplasmic",
    },
    # Non-TM feature that should be ignored by parse_tm_regions
    {
        "type": "Chain",
        "location": {"start": {"value": 1}, "end": {"value": 413}},
        "description": "Beta-2 adrenergic receptor",
    },
]

# Features with no TM regions (e.g. a soluble protein)
SOLUBLE_FEATURES: list[dict[str, Any]] = [
    {
        "type": "Chain",
        "location": {"start": {"value": 1}, "end": {"value": 300}},
        "description": "Kinase domain",
    },
    {
        "type": "Domain",
        "location": {"start": {"value": 50}, "end": {"value": 200}},
        "description": "Protein kinase",
    },
]


def _make_pocket(
    rank: int,
    residues: list[dict[str, Any]],
    druggability_score: float = 0.72,
    volume: float = 800.0,
    n_alpha_spheres: int = 60,
) -> dict[str, Any]:
    """Build a mock pocket entry matching the fpocket record schema."""
    return {
        "rank": rank,
        "druggability_score": druggability_score,
        "volume": volume,
        "n_alpha_spheres": n_alpha_spheres,
        "residues": residues,
    }


def _make_residue(chain: str, resnum: int, resname: str = "ALA") -> dict[str, Any]:
    return {"chain": chain, "resnum": resnum, "resname": resname}


# ---------------------------------------------------------------------------
# Tests: UniProt feature parsing
# ---------------------------------------------------------------------------


def test_parse_tm_regions_gpcr():
    """TM regions are correctly extracted and numbered from GPCR features."""
    regions = parse_tm_regions(GPCR_FEATURES)
    assert len(regions) == 7, f"expected 7 TM regions, got {len(regions)}"
    # Check ordering (TM1 first, TM7 last)
    assert regions[0]["label"] == "TM1"
    assert regions[6]["label"] == "TM7"
    # Check boundaries
    assert regions[0]["start"] == 34
    assert regions[0]["end"] == 58
    assert regions[2]["label"] == "TM3"
    assert regions[2]["start"] == 107
    assert regions[2]["end"] == 137


def test_parse_tm_regions_soluble():
    """Soluble protein features produce no TM regions."""
    regions = parse_tm_regions(SOLUBLE_FEATURES)
    assert len(regions) == 0, f"expected 0 TM regions, got {len(regions)}"


def test_parse_tm_regions_missing_location():
    """Features with missing location values are skipped gracefully."""
    features = [
        {"type": "Transmembrane", "location": {"start": {"value": 10}, "end": {}}},
        {"type": "Transmembrane", "location": {"start": {}, "end": {"value": 50}}},
        {"type": "Transmembrane", "location": {}},
        {"type": "Transmembrane"},  # no location at all
    ]
    regions = parse_tm_regions(features)
    assert len(regions) == 0


def test_parse_topo_domains():
    """Topological domains (Extracellular, Cytoplasmic) are parsed."""
    domains = parse_topo_domains(GPCR_FEATURES)
    assert len(domains) == 2
    types = {d["description"] for d in domains}
    assert "Extracellular" in types
    assert "Cytoplasmic" in types


# ---------------------------------------------------------------------------
# Tests: Residue-to-TM mapping
# ---------------------------------------------------------------------------


def test_residue_to_tm_hit():
    """A residue inside a TM range maps to the correct label."""
    regions = parse_tm_regions(GPCR_FEATURES)
    # resnum 120 is inside TM3 (107-137)
    label = residue_to_tm(120, regions)
    assert label == "TM3", f"expected TM3, got {label}"


def test_residue_to_tm_boundary():
    """Residues at the exact boundary of a TM region are included."""
    regions = parse_tm_regions(GPCR_FEATURES)
    assert residue_to_tm(34, regions) == "TM1"  # start boundary
    assert residue_to_tm(58, regions) == "TM1"  # end boundary


def test_residue_to_tm_miss():
    """A residue outside all TM ranges returns None."""
    regions = parse_tm_regions(GPCR_FEATURES)
    # resnum 10 is before TM1 (34-58)
    label = residue_to_tm(10, regions)
    assert label is None, f"expected None, got {label}"


def test_residue_to_tm_between_tms():
    """A residue between two TM segments returns None."""
    regions = parse_tm_regions(GPCR_FEATURES)
    # resnum 65 is between TM1 (34-58) and TM2 (71-100)
    label = residue_to_tm(65, regions)
    assert label is None, f"expected None, got {label}"


# ---------------------------------------------------------------------------
# Tests: Pocket-to-topology mapping
# ---------------------------------------------------------------------------


def test_map_pocket_orthosteric_candidate():
    """A pocket with residues in TM3+TM6+TM7 is flagged as orthosteric candidate."""
    regions = parse_tm_regions(GPCR_FEATURES)
    # Create residues in TM3 (107-137), TM6 (241-276), TM7 (286-311)
    residues = [
        _make_residue("A", 120, "VAL"),  # TM3
        _make_residue("A", 125, "LEU"),  # TM3
        _make_residue("A", 250, "PHE"),  # TM6
        _make_residue("A", 260, "TRP"),  # TM6
        _make_residue("A", 295, "TYR"),  # TM7
        _make_residue("A", 300, "ASN"),  # TM7
    ]
    pocket = _make_pocket(3, residues, druggability_score=0.72)
    result = map_pocket_to_topology(pocket, regions)

    assert result["orthosteric_candidate"] is True
    assert result["n_tm_segments"] == 3
    assert result["n_tm_residues"] == 6
    assert not result["bundle_void_advisory"]


def test_map_pocket_non_orthosteric():
    """A pocket with TM1+TM2 only is not an orthosteric candidate."""
    regions = parse_tm_regions(GPCR_FEATURES)
    residues = [
        _make_residue("A", 40, "ALA"),  # TM1
        _make_residue("A", 80, "GLY"),  # TM2
    ]
    pocket = _make_pocket(1, residues, druggability_score=0.85)
    result = map_pocket_to_topology(pocket, regions)

    assert result["orthosteric_candidate"] is False
    assert result["n_tm_segments"] == 2


def test_map_pocket_no_tm_residues():
    """A pocket with no TM residues (non-GPCR target or extracellular pocket)."""
    regions = parse_tm_regions(GPCR_FEATURES)
    residues = [
        _make_residue("A", 10, "MET"),  # before TM1
        _make_residue("A", 15, "SER"),
        _make_residue("A", 20, "THR"),
    ]
    pocket = _make_pocket(5, residues, druggability_score=0.65)
    result = map_pocket_to_topology(pocket, regions)

    assert result["n_tm_segments"] == 0
    assert result["n_tm_residues"] == 0
    assert result["n_non_tm_residues"] == 3
    assert not result["orthosteric_candidate"]
    assert not result["bundle_void_advisory"]


# ---------------------------------------------------------------------------
# Tests: Bundle-void advisory
# ---------------------------------------------------------------------------


def test_bundle_void_many_segments_high_alpha():
    """Bundle-void fires when ≥5 TM segments AND >200 alpha spheres."""
    regions = parse_tm_regions(GPCR_FEATURES)
    # Residues spanning TM1 through TM5 (5 segments)
    residues = [
        _make_residue("A", 40),  # TM1
        _make_residue("A", 80),  # TM2
        _make_residue("A", 120),  # TM3
        _make_residue("A", 160),  # TM4
        _make_residue("A", 210),  # TM5
    ]
    pocket = _make_pocket(
        1,
        residues,
        druggability_score=0.99,
        n_alpha_spheres=294,
        volume=1200.0,
    )
    result = map_pocket_to_topology(pocket, regions)

    assert result["bundle_void_advisory"] is True
    assert "5 TM segments" in result["bundle_void_reason"]


def test_bundle_void_many_segments_high_volume():
    """Bundle-void fires when ≥5 TM segments AND volume >1500."""
    regions = parse_tm_regions(GPCR_FEATURES)
    residues = [
        _make_residue("A", 40),  # TM1
        _make_residue("A", 80),  # TM2
        _make_residue("A", 120),  # TM3
        _make_residue("A", 160),  # TM4
        _make_residue("A", 210),  # TM5
    ]
    pocket = _make_pocket(
        1,
        residues,
        druggability_score=0.99,
        n_alpha_spheres=50,
        volume=1800.0,
    )
    result = map_pocket_to_topology(pocket, regions)

    assert result["bundle_void_advisory"] is True


def test_bundle_void_not_enough_segments():
    """Bundle-void does NOT fire with <5 TM segments even if volume is large."""
    regions = parse_tm_regions(GPCR_FEATURES)
    residues = [
        _make_residue("A", 40),  # TM1
        _make_residue("A", 80),  # TM2
        _make_residue("A", 120),  # TM3
        _make_residue("A", 160),  # TM4
    ]
    pocket = _make_pocket(
        1,
        residues,
        druggability_score=0.99,
        n_alpha_spheres=500,
        volume=3000.0,
    )
    result = map_pocket_to_topology(pocket, regions)

    assert result["bundle_void_advisory"] is False


def test_bundle_void_enough_segments_low_metrics():
    """Bundle-void does NOT fire when ≥5 segments but alpha/volume both low."""
    regions = parse_tm_regions(GPCR_FEATURES)
    residues = [
        _make_residue("A", 40),  # TM1
        _make_residue("A", 80),  # TM2
        _make_residue("A", 120),  # TM3
        _make_residue("A", 160),  # TM4
        _make_residue("A", 210),  # TM5
    ]
    pocket = _make_pocket(
        1,
        residues,
        druggability_score=0.80,
        n_alpha_spheres=50,
        volume=500.0,
    )
    result = map_pocket_to_topology(pocket, regions)

    assert result["bundle_void_advisory"] is False


# ---------------------------------------------------------------------------
# Tests: Gene symbol resolution (mock)
# ---------------------------------------------------------------------------


def test_resolve_accession_direct():
    """A UniProt accession passes through without a network call."""
    # This should NOT make a network call — just pattern-match.
    _acc, _gene = (
        resolve_accession.__wrapped__(resolve_accession, "P07550")
        if hasattr(resolve_accession, "__wrapped__")
        else _test_resolve_accession_direct()
    )


def _test_resolve_accession_direct():
    """Direct accession recognized by regex."""
    from dde.commands.structure import _is_accession

    assert _is_accession("P07550") is True
    assert _is_accession("Q9UBS5") is True
    assert _is_accession("A0A1B2C3D4E5") is False  # too long
    assert _is_accession("ADRB2") is False  # gene symbol


def test_is_accession_patterns():
    """Various UniProt accession patterns are correctly identified."""
    from dde.commands.structure import _is_accession

    # Standard 6-char
    assert _is_accession("P12345") is True
    assert _is_accession("Q9UBS5") is True
    assert _is_accession("O15392") is True
    # 10-char (new-format)
    assert _is_accession("A0A1B2C3D4") is True
    # Gene symbols
    assert _is_accession("ADRB2") is False
    assert _is_accession("TP53") is False
    assert _is_accession("EGFR") is False
    # Empty / too short
    assert _is_accession("") is False
    assert _is_accession("P1") is False


# ---------------------------------------------------------------------------
# Tests: Non-GPCR target (soluble protein)
# ---------------------------------------------------------------------------


def test_soluble_protein_no_tm_mapping():
    """A soluble protein with no TM regions produces empty TM mapping for all pockets."""
    regions = parse_tm_regions(SOLUBLE_FEATURES)
    assert len(regions) == 0

    residues = [
        _make_residue("A", 60, "LYS"),
        _make_residue("A", 100, "ASP"),
        _make_residue("A", 150, "GLU"),
    ]
    pocket = _make_pocket(1, residues, druggability_score=0.90)
    result = map_pocket_to_topology(pocket, regions)

    assert result["n_tm_segments"] == 0
    assert result["n_tm_residues"] == 0
    assert result["n_non_tm_residues"] == 3
    assert not result["orthosteric_candidate"]
    assert not result["bundle_void_advisory"]
    assert len(result["tm_segments"]) == 0


# ---------------------------------------------------------------------------
# Tests: Seven-TM bundle void (all segments)
# ---------------------------------------------------------------------------


def test_seven_tm_bundle_void():
    """A pocket spanning all 7 TM segments with high volume triggers advisory."""
    regions = parse_tm_regions(GPCR_FEATURES)
    residues = [
        _make_residue("A", 40),  # TM1
        _make_residue("A", 45),  # TM1
        _make_residue("A", 80),  # TM2
        _make_residue("A", 85),  # TM2
        _make_residue("A", 120),  # TM3
        _make_residue("A", 125),  # TM3
        _make_residue("A", 160),  # TM4
        _make_residue("A", 165),  # TM4
        _make_residue("A", 210),  # TM5
        _make_residue("A", 215),  # TM5
        _make_residue("A", 250),  # TM6
        _make_residue("A", 255),  # TM6
        _make_residue("A", 295),  # TM7
        _make_residue("A", 300),  # TM7
    ]
    pocket = _make_pocket(
        1,
        residues,
        druggability_score=0.99,
        n_alpha_spheres=350,
        volume=2000.0,
    )
    result = map_pocket_to_topology(pocket, regions)

    assert result["bundle_void_advisory"] is True
    assert result["n_tm_segments"] == 7
    assert "7 TM segments" in result["bundle_void_reason"]
    # It still has TM3+TM6+TM7 but that's coincidental for a bundle void.
    assert result["orthosteric_candidate"] is True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("test_topology_annotation.py")
    print("=" * 60)

    _check("parse_tm_regions_gpcr", test_parse_tm_regions_gpcr)
    _check("parse_tm_regions_soluble", test_parse_tm_regions_soluble)
    _check("parse_tm_regions_missing_location", test_parse_tm_regions_missing_location)
    _check("parse_topo_domains", test_parse_topo_domains)

    _check("residue_to_tm_hit", test_residue_to_tm_hit)
    _check("residue_to_tm_boundary", test_residue_to_tm_boundary)
    _check("residue_to_tm_miss", test_residue_to_tm_miss)
    _check("residue_to_tm_between_tms", test_residue_to_tm_between_tms)

    _check("map_pocket_orthosteric_candidate", test_map_pocket_orthosteric_candidate)
    _check("map_pocket_non_orthosteric", test_map_pocket_non_orthosteric)
    _check("map_pocket_no_tm_residues", test_map_pocket_no_tm_residues)

    _check(
        "bundle_void_many_segments_high_alpha",
        test_bundle_void_many_segments_high_alpha,
    )
    _check(
        "bundle_void_many_segments_high_volume",
        test_bundle_void_many_segments_high_volume,
    )
    _check("bundle_void_not_enough_segments", test_bundle_void_not_enough_segments)
    _check(
        "bundle_void_enough_segments_low_metrics",
        test_bundle_void_enough_segments_low_metrics,
    )

    _check("resolve_accession_direct", _test_resolve_accession_direct)
    _check("is_accession_patterns", test_is_accession_patterns)

    _check("soluble_protein_no_tm_mapping", test_soluble_protein_no_tm_mapping)
    _check("seven_tm_bundle_void", test_seven_tm_bundle_void)

    print()
    print(f"{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)
