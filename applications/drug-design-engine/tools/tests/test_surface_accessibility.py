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

"""Tests for ``dde structure surface`` — surface accessibility analysis.

Covers:
- Shrake-Rupley SASA on known geometries (single atom, two touching atoms)
- Per-residue RSA computation with known maxASA values
- Exposure classification at RSA thresholds
- pLDDT extraction from B-factor column (PDB and CIF)
- Glycosylation site parsing from UniProt features (mock data)
- --near patch analysis
- Relay codes fire
- Threshold set declaration
"""

from __future__ import annotations

import math
import textwrap
import unittest
from typing import ClassVar

from dde.commands.structure import (
    MAX_ASA_TIEN,
    _compute_rsa,
    _compute_sasa,
    _format_residue_ranges,
    _generate_sphere_points,
    _parse_atoms_cif,
    _parse_atoms_pdb,
    _parse_glycosylation_sites,
    _parse_near_residues,
)
from dde.core import provenance, thresholds

# ---------------------------------------------------------------------------
# Minimal test fixtures
# ---------------------------------------------------------------------------

#: Single-atom PDB — SASA should be 4*pi*(r+probe)^2.
SINGLE_ATOM_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 85.30           C
    END
""")

#: Two atoms on chain A, close enough to occlude each other.
#: C atoms with vdW=1.70: touching distance is 2*1.70=3.40 A.
#: Placed 3.40 A apart along X-axis so they just touch.
TWO_TOUCHING_ATOMS_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 90.00           C
    ATOM      2  CA  ALA A   2       3.400   0.000   0.000  1.00 45.00           C
    END
""")

#: Multi-residue PDB with known B-factors for pLDDT extraction.
MULTI_RESIDUE_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 92.30           C
    ATOM      2  CB  ALA A   1       1.500   0.000   0.000  1.00 92.30           C
    ATOM      3  CA  GLY A   2      10.000   0.000   0.000  1.00 35.10           C
    ATOM      4  CA  LYS A   3      20.000   0.000   0.000  1.00 75.50           C
    ATOM      5  CB  LYS A   3      21.500   0.000   0.000  1.00 75.50           C
    END
""")

#: CIF with B-factor column.
CIF_WITH_BFACTOR = textwrap.dedent("""\
    data_test
    #
    loop_
    _atom_site.group_PDB
    _atom_site.id
    _atom_site.type_symbol
    _atom_site.label_atom_id
    _atom_site.label_comp_id
    _atom_site.label_asym_id
    _atom_site.label_seq_id
    _atom_site.Cartn_x
    _atom_site.Cartn_y
    _atom_site.Cartn_z
    _atom_site.B_iso_or_equiv
    _atom_site.auth_asym_id
    _atom_site.auth_seq_id
    ATOM 1 C CA ALA A 1 0.000 0.000 0.000 88.50 A 1
    ATOM 2 C CB ALA A 1 1.500 0.000 0.000 88.50 A 1
    ATOM 3 C CA GLY A 2 10.000 0.000 0.000 42.00 A 2
    #
""")

#: PDB with widely separated residues (all fully exposed).
EXPOSED_RESIDUES_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 95.00           C
    ATOM      2  CA  ALA A   2      50.000   0.000   0.000  1.00 95.00           C
    ATOM      3  CA  ALA A   3       0.000  50.000   0.000  1.00 95.00           C
    END
""")

#: Mock UniProt features for glycosylation site extraction.
MOCK_UNIPROT_FEATURES = [
    {
        "type": "Glycosylation",
        "location": {
            "start": {"value": 42},
            "end": {"value": 42},
        },
        "description": "N-linked (GlcNAc...) asparagine",
    },
    {
        "type": "Glycosylation",
        "location": {
            "start": {"value": 180},
            "end": {"value": 180},
        },
        "description": "O-linked (GalNAc...) serine",
    },
    {
        "type": "Transmembrane",
        "location": {
            "start": {"value": 50},
            "end": {"value": 70},
        },
        "description": "Helical",
    },
]


# ---------------------------------------------------------------------------
# Tests — Shrake-Rupley SASA
# ---------------------------------------------------------------------------


class TestSpherePointGeneration(unittest.TestCase):
    """Sphere points are approximately uniformly distributed on a unit sphere."""

    def test_correct_count(self):
        pts = _generate_sphere_points(100)
        self.assertEqual(pts.shape, (100, 3))

    def test_unit_sphere(self):
        pts = _generate_sphere_points(100)
        norms = (pts**2).sum(axis=1) ** 0.5
        for n in norms:
            self.assertAlmostEqual(n, 1.0, places=10)

    def test_single_point(self):
        pts = _generate_sphere_points(1)
        self.assertEqual(pts.shape, (1, 3))


class TestSASASingleAtom(unittest.TestCase):
    """A single isolated atom has SASA = 4*pi*(vdW + probe)^2."""

    def test_single_carbon_atom(self):
        atoms = _parse_atoms_pdb(SINGLE_ATOM_PDB)
        self.assertEqual(len(atoms), 1)

        sasa = _compute_sasa(atoms, probe_radius=1.4, n_points=500)
        self.assertEqual(len(sasa), 1)

        # Expected: 4*pi*(1.70 + 1.40)^2 = 4*pi*9.61 ≈ 120.76
        expected = 4.0 * math.pi * (1.70 + 1.40) ** 2
        # With 500 test points, single atom should give exact value
        # (no occlusion possible).
        self.assertAlmostEqual(sasa[0], expected, places=1)

    def test_bfactor_extracted(self):
        atoms = _parse_atoms_pdb(SINGLE_ATOM_PDB)
        self.assertAlmostEqual(atoms[0]["bfactor"], 85.30)


class TestSASATwoAtoms(unittest.TestCase):
    """Two touching atoms partially occlude each other."""

    def test_total_sasa_less_than_double_single(self):
        atoms = _parse_atoms_pdb(TWO_TOUCHING_ATOMS_PDB)
        self.assertEqual(len(atoms), 2)

        sasa = _compute_sasa(atoms, probe_radius=1.4, n_points=200)

        single_sasa = 4.0 * math.pi * (1.70 + 1.40) ** 2
        total_sasa = sum(sasa)
        # Total should be less than 2 * single (partial occlusion).
        self.assertLess(total_sasa, 2 * single_sasa)
        # But more than 1 * single (both atoms have some exposed surface).
        self.assertGreater(total_sasa, single_sasa)

    def test_symmetry(self):
        """Two identical atoms should have approximately equal SASA."""
        atoms = _parse_atoms_pdb(TWO_TOUCHING_ATOMS_PDB)
        sasa = _compute_sasa(atoms, probe_radius=1.4, n_points=200)
        # Should be approximately equal (both are C atoms at the same distance).
        self.assertAlmostEqual(sasa[0], sasa[1], delta=5.0)


class TestSASAEmpty(unittest.TestCase):
    """Empty atom list produces empty SASA."""

    def test_empty(self):
        self.assertEqual(_compute_sasa([]), [])


# ---------------------------------------------------------------------------
# Tests — RSA computation
# ---------------------------------------------------------------------------


class TestRSAComputation(unittest.TestCase):
    """Per-residue RSA is correctly computed from per-atom SASA."""

    def test_rsa_clamped_to_unit_interval(self):
        """RSA should be clamped to [0, 1]."""
        # Create an atom with huge SASA — RSA should still be <= 1.0.
        atoms = [
            {
                "chain": "A",
                "resnum": 1,
                "resname": "ALA",
                "x": 0,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 50,
            }
        ]
        # Give it a SASA larger than MAX_ASA for ALA (129.0).
        per_atom_sasa = [999.0]
        result = _compute_rsa(atoms, per_atom_sasa)
        self.assertEqual(len(result), 1)
        self.assertLessEqual(result[0]["rsa"], 1.0)

    def test_rsa_zero_when_no_sasa(self):
        atoms = [
            {
                "chain": "A",
                "resnum": 1,
                "resname": "ALA",
                "x": 0,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 50,
            }
        ]
        per_atom_sasa = [0.0]
        result = _compute_rsa(atoms, per_atom_sasa)
        self.assertEqual(result[0]["rsa"], 0.0)

    def test_rsa_known_value(self):
        """RSA = SASA / MAX_ASA for ALA = 129.0."""
        atoms = [
            {
                "chain": "A",
                "resnum": 1,
                "resname": "ALA",
                "x": 0,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 50,
            }
        ]
        per_atom_sasa = [64.5]  # Half of 129.0
        result = _compute_rsa(atoms, per_atom_sasa)
        self.assertAlmostEqual(result[0]["rsa"], 0.5, places=2)

    def test_multiple_atoms_same_residue(self):
        """SASA is summed across atoms in the same residue."""
        atoms = [
            {
                "chain": "A",
                "resnum": 1,
                "resname": "ALA",
                "x": 0,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 80,
            },
            {
                "chain": "A",
                "resnum": 1,
                "resname": "ALA",
                "x": 1,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 80,
            },
        ]
        per_atom_sasa = [30.0, 30.0]
        result = _compute_rsa(atoms, per_atom_sasa)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["sasa"], 60.0)

    def test_nonstandard_residue_rsa_is_none(self):
        """Non-standard residues get RSA = None."""
        atoms = [
            {
                "chain": "A",
                "resnum": 1,
                "resname": "UNK",
                "x": 0,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 50,
            }
        ]
        per_atom_sasa = [50.0]
        result = _compute_rsa(atoms, per_atom_sasa)
        self.assertIsNone(result[0]["rsa"])

    def test_plddt_extracted(self):
        """Mean pLDDT (B-factor) is correctly computed per residue."""
        atoms = [
            {
                "chain": "A",
                "resnum": 1,
                "resname": "ALA",
                "x": 0,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 80.0,
            },
            {
                "chain": "A",
                "resnum": 1,
                "resname": "ALA",
                "x": 1,
                "y": 0,
                "z": 0,
                "element": "C",
                "bfactor": 90.0,
            },
        ]
        per_atom_sasa = [30.0, 30.0]
        result = _compute_rsa(atoms, per_atom_sasa)
        self.assertAlmostEqual(result[0]["plddt"], 85.0)


# ---------------------------------------------------------------------------
# Tests — Exposure classification
# ---------------------------------------------------------------------------


class TestExposureClassification(unittest.TestCase):
    """RSA thresholds produce correct classifications."""

    def test_classification_thresholds(self):
        """Residues are classified by RSA thresholds."""
        ts = thresholds.load("surface")
        rsa_exposed = ts.get("rsa_exposed")  # 0.25
        rsa_highly = ts.get("rsa_highly_exposed")  # 0.50

        # Buried: RSA <= 0.25
        self.assertTrue(0.10 <= rsa_exposed)
        # Exposed: 0.25 < RSA <= 0.50
        self.assertTrue(rsa_highly > rsa_exposed)

    def test_exposed_residues_classified(self):
        """Widely separated atoms should produce exposed residues."""
        atoms = _parse_atoms_pdb(EXPOSED_RESIDUES_PDB)
        sasa = _compute_sasa(atoms, probe_radius=1.4, n_points=100)
        per_residue = _compute_rsa(atoms, sasa)

        # Each atom is a single CA far from others — should be fully exposed.
        # ALA's maxASA is 129.0; a single CA has SASA ≈ 120 A^2.
        # RSA ≈ 120/129 ≈ 0.93 → highly exposed.
        for res in per_residue:
            if res["rsa"] is not None:
                self.assertGreater(
                    res["rsa"], 0.5, f"Residue {res['resnum']} should be highly exposed"
                )


# ---------------------------------------------------------------------------
# Tests — B-factor / pLDDT extraction
# ---------------------------------------------------------------------------


class TestBFactorPDB(unittest.TestCase):
    """PDB B-factor extraction for pLDDT proxy."""

    def test_bfactor_values(self):
        atoms = _parse_atoms_pdb(MULTI_RESIDUE_PDB)
        bfactors = [(a["resnum"], a["bfactor"]) for a in atoms]
        # Residue 1: bfactor 92.30
        self.assertAlmostEqual(next(b for rn, b in bfactors if rn == 1), 92.30)
        # Residue 2 (GLY): bfactor 35.10
        self.assertAlmostEqual(next(b for rn, b in bfactors if rn == 2), 35.10)
        # Residue 3 (LYS): bfactor 75.50
        self.assertAlmostEqual(next(b for rn, b in bfactors if rn == 3), 75.50)

    def test_element_extracted(self):
        atoms = _parse_atoms_pdb(MULTI_RESIDUE_PDB)
        for a in atoms:
            self.assertEqual(a["element"], "C")


class TestBFactorCIF(unittest.TestCase):
    """CIF B-factor extraction for pLDDT proxy."""

    def test_bfactor_from_cif(self):
        atoms = _parse_atoms_cif(CIF_WITH_BFACTOR)
        self.assertTrue(len(atoms) > 0)

        # Check B-factor values.
        ala_atoms = [a for a in atoms if a["resname"] == "ALA"]
        self.assertTrue(len(ala_atoms) > 0)
        self.assertAlmostEqual(ala_atoms[0]["bfactor"], 88.50)

        gly_atoms = [a for a in atoms if a["resname"] == "GLY"]
        self.assertTrue(len(gly_atoms) > 0)
        self.assertAlmostEqual(gly_atoms[0]["bfactor"], 42.00)

    def test_element_from_cif(self):
        atoms = _parse_atoms_cif(CIF_WITH_BFACTOR)
        for a in atoms:
            self.assertEqual(a["element"], "C")


# ---------------------------------------------------------------------------
# Tests — Glycosylation site parsing
# ---------------------------------------------------------------------------


class TestGlycosylationParsing(unittest.TestCase):
    """Glycosylation sites are extracted from UniProt features."""

    def test_extracts_glycosylation_sites(self):
        sites = _parse_glycosylation_sites(MOCK_UNIPROT_FEATURES)
        self.assertEqual(len(sites), 2)

    def test_ignores_non_glycosylation(self):
        """Transmembrane features should not appear in glycosylation list."""
        sites = _parse_glycosylation_sites(MOCK_UNIPROT_FEATURES)
        types = [s["type"] for s in sites]
        self.assertNotIn("Transmembrane", types)

    def test_n_linked_type(self):
        sites = _parse_glycosylation_sites(MOCK_UNIPROT_FEATURES)
        n_linked = [s for s in sites if s["type"] == "N-linked"]
        self.assertEqual(len(n_linked), 1)
        self.assertEqual(n_linked[0]["position"], 42)

    def test_o_linked_type(self):
        sites = _parse_glycosylation_sites(MOCK_UNIPROT_FEATURES)
        o_linked = [s for s in sites if s["type"] == "O-linked"]
        self.assertEqual(len(o_linked), 1)
        self.assertEqual(o_linked[0]["position"], 180)

    def test_empty_features(self):
        sites = _parse_glycosylation_sites([])
        self.assertEqual(sites, [])


# ---------------------------------------------------------------------------
# Tests — --near patch parsing
# ---------------------------------------------------------------------------


class TestNearParsing(unittest.TestCase):
    """--near residue specification parsing."""

    def test_parses_chain_residue(self):
        result = _parse_near_residues("A:42,A:43,B:10")
        self.assertEqual(len(result), 3)
        self.assertIn(("A", 42), result)
        self.assertIn(("A", 43), result)
        self.assertIn(("B", 10), result)

    def test_single_residue(self):
        result = _parse_near_residues("A:42")
        self.assertEqual(result, [("A", 42)])

    def test_empty_string(self):
        result = _parse_near_residues("")
        self.assertEqual(result, [])

    def test_invalid_tokens_skipped(self):
        result = _parse_near_residues("A:42,invalid,B:10")
        self.assertEqual(len(result), 2)


class TestFormatResidueRanges(unittest.TestCase):
    """Residue range formatting."""

    def test_consecutive(self):
        self.assertEqual(_format_residue_ranges([1, 2, 3]), "1-3")

    def test_mixed(self):
        self.assertEqual(
            _format_residue_ranges([1, 2, 3, 7, 8, 15]),
            "1-3, 7-8, 15",
        )

    def test_single(self):
        self.assertEqual(_format_residue_ranges([42]), "42")

    def test_empty(self):
        self.assertEqual(_format_residue_ranges([]), "")


# ---------------------------------------------------------------------------
# Tests — Relay codes
# ---------------------------------------------------------------------------


class TestRelayCodeRegistration(unittest.TestCase):
    """Surface relay codes are registered in provenance.RELAY_CODES."""

    def test_sasa_static_snapshot_registered(self):
        self.assertIn("surface.sasa_is_static_snapshot", provenance.RELAY_CODES)

    def test_rsa_reference_values_registered(self):
        self.assertIn("surface.rsa_reference_values", provenance.RELAY_CODES)

    def test_relay_codes_fire(self):
        """Relay codes can be used in provenance.relay() without error."""
        rec = provenance.relay(
            "surface.sasa_is_static_snapshot",
            "test message",
        )
        self.assertEqual(rec["code"], "surface.sasa_is_static_snapshot")

        rec = provenance.relay(
            "surface.rsa_reference_values",
            "test message",
        )
        self.assertEqual(rec["code"], "surface.rsa_reference_values")


# ---------------------------------------------------------------------------
# Tests — Threshold set
# ---------------------------------------------------------------------------


class TestSurfaceThresholdSet(unittest.TestCase):
    """surface@1.0 threshold set is declared and resolvable."""

    def test_threshold_set_exists(self):
        ts = thresholds.load("surface")
        self.assertEqual(ts.name, "surface")
        self.assertEqual(ts.version, "1.0")

    def test_threshold_values(self):
        ts = thresholds.load("surface")
        self.assertAlmostEqual(ts.get("rsa_exposed"), 0.25)
        self.assertAlmostEqual(ts.get("rsa_highly_exposed"), 0.50)
        self.assertAlmostEqual(ts.get("plddt_disorder"), 50.0)
        self.assertEqual(ts.get("min_exposed_patch_residues"), 5)
        self.assertAlmostEqual(ts.get("glycosylation_proximity_angstrom"), 10.0)

    def test_no_unresolved(self):
        ts = thresholds.load("surface")
        self.assertEqual(ts.unresolved(), [])

    def test_provenance_is_set(self):
        ts = thresholds.load("surface")
        self.assertIn("Tien", ts.provenance)
        self.assertIn("Rost", ts.provenance)


# ---------------------------------------------------------------------------
# Tests — MAX_ASA reference table
# ---------------------------------------------------------------------------


class TestMaxASATable(unittest.TestCase):
    """Tien et al. 2013 maxASA table is complete for standard amino acids."""

    STANDARD_AA: ClassVar[list[str]] = [
        "ALA",
        "ARG",
        "ASN",
        "ASP",
        "CYS",
        "GLN",
        "GLU",
        "GLY",
        "HIS",
        "ILE",
        "LEU",
        "LYS",
        "MET",
        "PHE",
        "PRO",
        "SER",
        "THR",
        "TRP",
        "TYR",
        "VAL",
    ]

    def test_all_standard_aa_present(self):
        for aa in self.STANDARD_AA:
            self.assertIn(aa, MAX_ASA_TIEN, f"{aa} missing from MAX_ASA_TIEN")

    def test_all_values_positive(self):
        for aa, val in MAX_ASA_TIEN.items():
            self.assertGreater(val, 0, f"{aa} has non-positive maxASA")


if __name__ == "__main__":
    unittest.main()
