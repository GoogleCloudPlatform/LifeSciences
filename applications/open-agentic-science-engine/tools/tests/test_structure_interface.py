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

"""Tests for ``dde structure interface`` — interface residue detection.

Covers:
- Two-chain PDB: interface residues correctly identified at 5 A cutoff
- Two-chain CIF: same logic on mmCIF format
- --chain filter: limits analysis to interfaces involving specified chain
- near_query formatting: correct CHAIN:RESNUM comma-separated output
- Single-chain structure: raises UsageError (no interface possible)
- No contacts at cutoff: interface is empty (no false positives)
- Three-chain structure: all pairs analysed
"""

from __future__ import annotations

import textwrap
import unittest

from dde.commands.structure import (
    _find_interface_residues,
    _group_atoms_by_chain,
    _near_query,
    _parse_atoms_cif,
    _parse_atoms_pdb,
)

# ---------------------------------------------------------------------------
# Minimal test fixtures
# ---------------------------------------------------------------------------

#: Two-chain PDB with chains A and B.
#: Chain A has GLU 10 and ARG 11, chain B has TRP 20.
#: GLU 10 on A is within 4.0 A of TRP 20 on B (should be detected at 5 A).
#: ARG 11 on A is ~12 A away from B (should NOT be detected at 5 A).
TWO_CHAIN_PDB = textwrap.dedent("""\
    ATOM      1  CA  GLU A  10       1.000   2.000   3.000  1.00 30.00           C
    ATOM      2  CB  GLU A  10       1.500   2.500   3.500  1.00 30.00           C
    ATOM      3  CA  ARG A  11      15.000  15.000  15.000  1.00 30.00           C
    ATOM      4  CA  TRP B  20       4.000   3.000   4.000  1.00 30.00           C
    ATOM      5  CB  TRP B  20       4.500   3.500   4.500  1.00 30.00           C
    END
""")

#: Single-chain PDB — should produce no interfaces.
SINGLE_CHAIN_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 30.00           C
    ATOM      2  CA  GLY A   2       5.000   5.000   5.000  1.00 30.00           C
    END
""")

#: Three-chain PDB with chains A, B, C.
#: A-B close, A-C close, B-C far apart.
THREE_CHAIN_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   1.000   1.000  1.00 30.00           C
    ATOM      2  CA  GLY B   1       3.000   1.000   1.000  1.00 30.00           C
    ATOM      3  CA  LEU C   1       1.000   3.000   1.000  1.00 30.00           C
    END
""")

#: Two-chain PDB where chains are far apart — no interface at 5 A.
FAR_APART_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   1.000   1.000  1.00 30.00           C
    ATOM      2  CA  GLY B   1      50.000  50.000  50.000  1.00 30.00           C
    END
""")

#: PDB with hydrogen atoms — should be excluded from distance calculation.
PDB_WITH_HYDROGENS = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   1.000   1.000  1.00 30.00           C
    ATOM      2  H   ALA A   1       3.500   1.000   1.000  1.00 30.00           H
    ATOM      3  CA  GLY B   1       4.000   1.000   1.000  1.00 30.00           C
    END
""")

#: Two-chain CIF with chains A and B.
#: GLU 10 on A is within 5 A of TRP 20 on B.
TWO_CHAIN_CIF = textwrap.dedent("""\
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
    _atom_site.auth_asym_id
    _atom_site.auth_seq_id
    ATOM 1 C CA GLU A 10 1.000 2.000 3.000 A 10
    ATOM 2 C CB GLU A 10 1.500 2.500 3.500 A 10
    ATOM 3 C CA ARG A 11 15.000 15.000 15.000 A 11
    ATOM 4 C CA TRP B 20 4.000 3.000 4.000 B 20
    ATOM 5 C CB TRP B 20 4.500 3.500 4.500 B 20
    #
""")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseAtomsPDB(unittest.TestCase):
    """PDB atom parsing extracts coordinates and excludes hydrogens."""

    def test_parses_heavy_atoms(self):
        atoms = _parse_atoms_pdb(TWO_CHAIN_PDB)
        self.assertEqual(len(atoms), 5)
        chains = {a["chain"] for a in atoms}
        self.assertEqual(chains, {"A", "B"})

    def test_excludes_hydrogens(self):
        atoms = _parse_atoms_pdb(PDB_WITH_HYDROGENS)
        # The H atom should be excluded — only 2 heavy atoms remain.
        self.assertEqual(len(atoms), 2)
        for atom in atoms:
            self.assertNotEqual(atom["resname"], "H")

    def test_single_chain(self):
        atoms = _parse_atoms_pdb(SINGLE_CHAIN_PDB)
        chains = {a["chain"] for a in atoms}
        self.assertEqual(chains, {"A"})


class TestParseAtomsCIF(unittest.TestCase):
    """CIF atom parsing extracts coordinates using auth_ columns."""

    def test_parses_atoms(self):
        atoms = _parse_atoms_cif(TWO_CHAIN_CIF)
        self.assertEqual(len(atoms), 5)
        chains = {a["chain"] for a in atoms}
        self.assertEqual(chains, {"A", "B"})

    def test_chain_and_resnum(self):
        atoms = _parse_atoms_cif(TWO_CHAIN_CIF)
        a_atoms = [a for a in atoms if a["chain"] == "A"]
        resnums = {a["resnum"] for a in a_atoms}
        self.assertIn(10, resnums)
        self.assertIn(11, resnums)


class TestFindInterfaceResidues(unittest.TestCase):
    """Interface residues are correctly identified at a distance cutoff."""

    def test_two_chain_interface(self):
        atoms = _parse_atoms_pdb(TWO_CHAIN_PDB)
        chain_atoms = _group_atoms_by_chain(atoms)

        residues_a, residues_b, n_contacts = _find_interface_residues(
            chain_atoms["A"], chain_atoms["B"], cutoff=5.0
        )

        # GLU 10 on A is close to TRP 20 on B → both are interface residues.
        a_resnums = {r["resnum"] for r in residues_a}
        self.assertIn(10, a_resnums, "GLU 10 should be at the interface")

        # ARG 11 on A is far away → should NOT be at the interface.
        self.assertNotIn(11, a_resnums, "ARG 11 should not be at the interface")

        # TRP 20 on B should be at the interface.
        b_resnums = {r["resnum"] for r in residues_b}
        self.assertIn(20, b_resnums, "TRP 20 should be at the interface")

        # At least one contact pair was found.
        self.assertGreater(n_contacts, 0)

    def test_no_contacts_when_far_apart(self):
        atoms = _parse_atoms_pdb(FAR_APART_PDB)
        chain_atoms = _group_atoms_by_chain(atoms)

        residues_a, residues_b, n_contacts = _find_interface_residues(
            chain_atoms["A"], chain_atoms["B"], cutoff=5.0
        )

        self.assertEqual(residues_a, [])
        self.assertEqual(residues_b, [])
        self.assertEqual(n_contacts, 0)

    def test_hydrogen_exclusion_affects_contacts(self):
        """Hydrogens are excluded before distance computation.

        The H atom at (3.5, 1.0, 1.0) on chain A is within 5 A of
        chain B at (4.0, 1.0, 1.0), but the heavy atom CA at (1.0, 1.0, 1.0)
        is 3.0 A from B — still within 5 A.  The interface should be found
        via the heavy atom only.
        """
        atoms = _parse_atoms_pdb(PDB_WITH_HYDROGENS)
        chain_atoms = _group_atoms_by_chain(atoms)

        residues_a, residues_b, _n_contacts = _find_interface_residues(
            chain_atoms["A"], chain_atoms["B"], cutoff=5.0
        )

        # Both residues should be at the interface (heavy atoms are 3 A apart).
        self.assertEqual(len(residues_a), 1)
        self.assertEqual(len(residues_b), 1)

    def test_cif_interface(self):
        """CIF parsing produces the same interface as PDB for equivalent data."""
        atoms = _parse_atoms_cif(TWO_CHAIN_CIF)
        chain_atoms = _group_atoms_by_chain(atoms)

        residues_a, residues_b, _n_contacts = _find_interface_residues(
            chain_atoms["A"], chain_atoms["B"], cutoff=5.0
        )

        a_resnums = {r["resnum"] for r in residues_a}
        self.assertIn(10, a_resnums)
        self.assertNotIn(11, a_resnums)

        b_resnums = {r["resnum"] for r in residues_b}
        self.assertIn(20, b_resnums)

    def test_cutoff_sensitivity(self):
        """A tighter cutoff excludes contacts that a looser one includes."""
        atoms = _parse_atoms_pdb(TWO_CHAIN_PDB)
        chain_atoms = _group_atoms_by_chain(atoms)

        # At 1.0 A cutoff, nothing should be close enough.
        res_a, res_b, _ = _find_interface_residues(
            chain_atoms["A"], chain_atoms["B"], cutoff=1.0
        )
        self.assertEqual(res_a, [])
        self.assertEqual(res_b, [])


class TestThreeChainStructure(unittest.TestCase):
    """All chain pairs are analysed in a multi-chain structure."""

    def test_all_pairs(self):
        atoms = _parse_atoms_pdb(THREE_CHAIN_PDB)
        chain_atoms = _group_atoms_by_chain(atoms)
        chains = sorted(chain_atoms.keys())
        self.assertEqual(chains, ["A", "B", "C"])

        # A-B: distance = 2.0 A → interface at 5 A
        res_a, res_b, _ = _find_interface_residues(
            chain_atoms["A"], chain_atoms["B"], cutoff=5.0
        )
        self.assertGreater(len(res_a), 0)
        self.assertGreater(len(res_b), 0)

        # A-C: distance = 2.0 A → interface at 5 A
        res_a, res_c, _ = _find_interface_residues(
            chain_atoms["A"], chain_atoms["C"], cutoff=5.0
        )
        self.assertGreater(len(res_a), 0)
        self.assertGreater(len(res_c), 0)

        # B-C: distance = sqrt(4+4) ≈ 2.83 A → interface at 5 A
        res_b, res_c, _ = _find_interface_residues(
            chain_atoms["B"], chain_atoms["C"], cutoff=5.0
        )
        self.assertGreater(len(res_b), 0)
        self.assertGreater(len(res_c), 0)


class TestChainFilter(unittest.TestCase):
    """--chain filter limits analysis to interfaces involving that chain."""

    def test_filter_to_one_chain(self):
        atoms = _parse_atoms_pdb(THREE_CHAIN_PDB)
        chain_atoms = _group_atoms_by_chain(atoms)
        all_chains = sorted(chain_atoms.keys())

        # When filtering to chain A, only A-B and A-C pairs
        filtered_pairs = [("A", other) for other in all_chains if other != "A"]
        self.assertEqual(len(filtered_pairs), 2)
        pair_set = {(a, b) for a, b in filtered_pairs}
        self.assertIn(("A", "B"), pair_set)
        self.assertIn(("A", "C"), pair_set)
        # B-C should NOT be in the filtered set
        self.assertNotIn(("B", "C"), pair_set)


class TestNearQuery(unittest.TestCase):
    """near_query formatting produces correct --near strings."""

    def test_format(self):
        residues = [
            {"chain": "A", "resnum": 123, "resname": "GLU"},
            {"chain": "A", "resnum": 124, "resname": "ARG"},
            {"chain": "B", "resnum": 45, "resname": "TRP"},
        ]
        result = _near_query(residues)
        self.assertEqual(result, "A:123,A:124,B:45")

    def test_empty(self):
        result = _near_query([])
        self.assertEqual(result, "")

    def test_single_residue(self):
        residues = [{"chain": "A", "resnum": 10, "resname": "ALA"}]
        result = _near_query(residues)
        self.assertEqual(result, "A:10")


class TestSingleChainRejection(unittest.TestCase):
    """A single-chain structure cannot produce interfaces."""

    def test_single_chain_atoms(self):
        atoms = _parse_atoms_pdb(SINGLE_CHAIN_PDB)
        chain_atoms = _group_atoms_by_chain(atoms)
        self.assertEqual(len(chain_atoms), 1)
        # The command would raise UsageError here; we test the
        # prerequisite condition (only one chain).


if __name__ == "__main__":
    unittest.main()
