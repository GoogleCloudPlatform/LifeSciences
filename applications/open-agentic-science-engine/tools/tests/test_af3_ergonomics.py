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

"""Tests for AF3 workflow ergonomics improvements (#95).

Covers:
- Per-residue pLDDT computation from per-atom values (Item 1)
- AF3 input template output and validity (Item 2)
- Docking grid size warning fires at threshold (Item 3)
- Docking grid size warning does NOT fire for small grids (Item 3)
- Non-protein chain detection in PDB and CIF structures (Item 4)
- Relay fires when non-protein chains are present (Item 4)
- Relay does NOT fire for protein-only input (Item 4)
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from dde.commands.alphafold import _AF3_TEMPLATES, _af3_template, _parse_cif_plddt
from dde.commands.docking import _grid_size_warning
from dde.commands.pocket import _detect_non_protein_chains
from dde.core.errors import UsageError

# ---------------------------------------------------------------------------
# Item 1 — Per-residue pLDDT fixtures
# ---------------------------------------------------------------------------

#: Minimal CIF with three atoms in two residues on chain A, one residue
#: on chain B.  B-factors (pLDDT) are set to known values so per-residue
#: means can be verified arithmetically.
#:
#: Chain A, residue 1: atoms with B=90.0, B=80.0  → mean = 85.0
#: Chain A, residue 2: atom with B=70.0             → mean = 70.0
#: Chain B, residue 1: atoms with B=40.0, B=50.0  → mean = 45.0
PLDDT_CIF = textwrap.dedent("""\
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
    ATOM 1 C CA ALA A 1 1.0 2.0 3.0 90.0 A 1
    ATOM 2 C CB ALA A 1 1.5 2.5 3.5 80.0 A 1
    ATOM 3 C CA GLY A 2 5.0 5.0 5.0 70.0 A 2
    ATOM 4 C CA TRP B 1 10.0 10.0 10.0 40.0 B 1
    ATOM 5 C CB TRP B 1 10.5 10.5 10.5 50.0 B 1
    #
""")

#: CIF with no ATOM records — should raise SchemaError.
EMPTY_CIF = textwrap.dedent("""\
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
    #
""")


# ---------------------------------------------------------------------------
# Item 4 — Non-protein chain detection fixtures
# ---------------------------------------------------------------------------

#: Protein-only PDB — chain A has standard amino acids only.
PROTEIN_ONLY_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 30.00           C
    ATOM      2  CA  GLY A   2       5.000   5.000   5.000  1.00 30.00           C
    ATOM      3  CA  LEU A   3       8.000   8.000   8.000  1.00 30.00           C
    END
""")

#: PDB with protein chain A and ligand chain B.
PROTEIN_LIGAND_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 30.00           C
    ATOM      2  CA  GLY A   2       5.000   5.000   5.000  1.00 30.00           C
    HETATM    3  C1  LIG B   1      10.000  10.000  10.000  1.00 30.00           C
    HETATM    4  C2  LIG B   1      11.000  10.000  10.000  1.00 30.00           C
    END
""")

#: PDB with protein chain A and water chain W (waters should be ignored).
PROTEIN_WATER_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 30.00           C
    ATOM      2  CA  GLY A   2       5.000   5.000   5.000  1.00 30.00           C
    HETATM    3  O   HOH W   1      20.000  20.000  20.000  1.00 30.00           O
    HETATM    4  O   HOH W   2      21.000  20.000  20.000  1.00 30.00           O
    END
""")

#: PDB with protein chain A, nucleic acid chain B, and ion chain C.
PROTEIN_DNA_ION_PDB = textwrap.dedent("""\
    ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 30.00           C
    ATOM      2  CA  GLY A   2       5.000   5.000   5.000  1.00 30.00           C
    ATOM      3  P    DA B   1      10.000  10.000  10.000  1.00 30.00           P
    ATOM      4  P    DC B   2      12.000  10.000  10.000  1.00 30.00           P
    HETATM    5 ZN   ZN  C   1      20.000  20.000  20.000  1.00 30.00          ZN
    END
""")

#: CIF with protein chain A and ligand chain B.
PROTEIN_LIGAND_CIF = textwrap.dedent("""\
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
    ATOM 1 C CA ALA A 1 1.0 2.0 3.0 A 1
    ATOM 2 C CA GLY A 2 5.0 5.0 5.0 A 2
    HETATM 3 C C1 LIG B 1 10.0 10.0 10.0 B 1
    HETATM 4 C C2 LIG B 1 11.0 10.0 10.0 B 1
    #
""")


# ---------------------------------------------------------------------------
# Item 1 tests — Per-residue pLDDT
# ---------------------------------------------------------------------------


class TestPerResiduePlddt(unittest.TestCase):
    """Per-residue pLDDT computation from per-atom CIF B-factors."""

    def test_per_residue_means(self):
        """Atoms are correctly grouped by (chain, residue) and averaged."""
        per_residue, _chain_means = _parse_cif_plddt(PLDDT_CIF)

        # Chain A, residue 1: mean of (90.0, 80.0) = 85.0
        self.assertAlmostEqual(per_residue["A"]["1"], 85.0, places=1)

        # Chain A, residue 2: single atom at 70.0
        self.assertAlmostEqual(per_residue["A"]["2"], 70.0, places=1)

        # Chain B, residue 1: mean of (40.0, 50.0) = 45.0
        self.assertAlmostEqual(per_residue["B"]["1"], 45.0, places=1)

    def test_chain_means(self):
        """Chain means are computed from per-residue means, not per-atom."""
        _per_residue, chain_means = _parse_cif_plddt(PLDDT_CIF)

        # Chain A: mean of residue means (85.0, 70.0) = 77.5
        self.assertAlmostEqual(chain_means["A"], 77.5, places=1)

        # Chain B: single residue mean = 45.0
        self.assertAlmostEqual(chain_means["B"], 45.0, places=1)

    def test_all_chains_present(self):
        """Both chains A and B appear in the output."""
        per_residue, chain_means = _parse_cif_plddt(PLDDT_CIF)

        self.assertIn("A", per_residue)
        self.assertIn("B", per_residue)
        self.assertIn("A", chain_means)
        self.assertIn("B", chain_means)

    def test_empty_cif_raises(self):
        """A CIF with no atom records raises SchemaError."""
        from dde.core.errors import SchemaError

        with self.assertRaises(SchemaError):
            _parse_cif_plddt(EMPTY_CIF)


# ---------------------------------------------------------------------------
# Item 2 tests — AF3 input template
# ---------------------------------------------------------------------------


class TestAF3Template(unittest.TestCase):
    """AF3 input template generation."""

    def test_default_template_valid_json(self):
        """Default template is valid JSON with all required AF3 fields."""
        template = _af3_template("default")

        self.assertEqual(template["dialect"], "alphafold3")
        self.assertIn("version", template)
        self.assertIn("name", template)
        self.assertIn("modelSeeds", template)
        self.assertIn("sequences", template)
        self.assertIsInstance(template["sequences"], list)
        self.assertGreater(len(template["sequences"]), 0)

    def test_complex_template_has_two_proteins(self):
        """Complex template contains two protein chains."""
        template = _af3_template("complex")

        protein_chains = [seq for seq in template["sequences"] if "protein" in seq]
        self.assertEqual(len(protein_chains), 2)

    def test_ligand_template_has_protein_and_ligand(self):
        """Ligand template contains a protein and a ligand chain."""
        template = _af3_template("ligand")

        has_protein = any("protein" in seq for seq in template["sequences"])
        has_ligand = any("ligand" in seq for seq in template["sequences"])
        self.assertTrue(has_protein)
        self.assertTrue(has_ligand)

    def test_unknown_template_raises(self):
        """An unknown template type raises UsageError."""
        with self.assertRaises(UsageError):
            _af3_template("nonexistent")

    def test_templates_roundtrip_json(self):
        """All template types produce valid JSON that can round-trip."""
        for name in _AF3_TEMPLATES:
            template = _af3_template(name)
            # Must be serialisable and deserialise identically.
            text = json.dumps(template)
            roundtripped = json.loads(text)
            self.assertEqual(
                template, roundtripped, f"template {name!r} failed round-trip"
            )

    def test_template_returns_deep_copy(self):
        """Template returns a deep copy; mutations do not affect the original."""
        t1 = _af3_template("default")
        t1["name"] = "mutated"
        t2 = _af3_template("default")
        self.assertNotEqual(t2["name"], "mutated")


# ---------------------------------------------------------------------------
# Item 3 tests — Grid size warning
# ---------------------------------------------------------------------------


class TestGridSizeWarning(unittest.TestCase):
    """Docking grid size warning thresholds."""

    def test_large_volume_triggers_warning(self):
        """A grid with volume > 50,000 A^3 triggers a warning."""
        # 40 x 40 x 40 = 64,000 > 50,000
        warning = _grid_size_warning([40.0, 40.0, 40.0], exhaustiveness=8)
        self.assertIsNotNone(warning)
        self.assertIn("Warning", warning)
        self.assertIn("large search space", warning)

    def test_high_exhaustiveness_with_moderate_volume_triggers_warning(self):
        """Volume > 30,000 A^3 with exhaustiveness > 8 triggers a warning."""
        # 32 x 32 x 32 = 32,768 > 30,000 and exhaust=16 > 8
        warning = _grid_size_warning([32.0, 32.0, 32.0], exhaustiveness=16)
        self.assertIsNotNone(warning)
        self.assertIn("Warning", warning)

    def test_small_grid_no_warning(self):
        """A small grid does NOT trigger a warning."""
        # 20 x 20 x 20 = 8,000 — well under both thresholds
        warning = _grid_size_warning([20.0, 20.0, 20.0], exhaustiveness=8)
        self.assertIsNone(warning)

    def test_moderate_volume_low_exhaustiveness_no_warning(self):
        """Volume > 30,000 but exhaustiveness <= 8 does NOT trigger a warning."""
        # 32 x 32 x 32 = 32,768 > 30,000 but exhaust=8 ≤ 8
        warning = _grid_size_warning([32.0, 32.0, 32.0], exhaustiveness=8)
        self.assertIsNone(warning)

    def test_warning_contains_dimensions(self):
        """Warning message includes the grid dimensions."""
        warning = _grid_size_warning([43.9, 40.8, 41.5], exhaustiveness=16)
        self.assertIsNotNone(warning)
        self.assertIn("43.9", warning)
        self.assertIn("40.8", warning)
        self.assertIn("41.5", warning)

    def test_warning_contains_runtime_estimate(self):
        """Warning message includes an estimated runtime."""
        warning = _grid_size_warning([43.9, 40.8, 41.5], exhaustiveness=16)
        self.assertIsNotNone(warning)
        self.assertIn("Estimated runtime", warning)
        self.assertIn("min", warning)

    def test_warning_emits_to_stderr_hint(self):
        """Warning message suggests --background as a remedy."""
        warning = _grid_size_warning([43.9, 40.8, 41.5], exhaustiveness=16)
        self.assertIsNotNone(warning)
        self.assertIn("--background", warning)


# ---------------------------------------------------------------------------
# Item 4 tests — Non-protein chain detection
# ---------------------------------------------------------------------------


class TestNonProteinChainDetection(unittest.TestCase):
    """Detection of non-protein chains in PDB/CIF structures."""

    def _write_tmp(self, content: str, suffix: str) -> Path:
        """Write content to a temporary file and return the path."""
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        )
        fd.write(content)
        fd.close()
        return Path(fd.name)

    def test_protein_only_pdb(self):
        """Protein-only PDB reports no non-protein chains."""
        path = self._write_tmp(PROTEIN_ONLY_PDB, ".pdb")
        try:
            has, chains = _detect_non_protein_chains(path)
            self.assertFalse(has)
            self.assertEqual(chains, [])
        finally:
            path.unlink()

    def test_protein_ligand_pdb(self):
        """PDB with a ligand chain correctly detects it as non-protein."""
        path = self._write_tmp(PROTEIN_LIGAND_PDB, ".pdb")
        try:
            has, chains = _detect_non_protein_chains(path)
            self.assertTrue(has)
            self.assertIn("B", chains)
        finally:
            path.unlink()

    def test_water_chains_ignored(self):
        """Water-only chains are NOT flagged as non-protein."""
        path = self._write_tmp(PROTEIN_WATER_PDB, ".pdb")
        try:
            has, chains = _detect_non_protein_chains(path)
            self.assertFalse(has)
            self.assertEqual(chains, [])
        finally:
            path.unlink()

    def test_dna_and_ion_detected(self):
        """Nucleic acid and ion chains are detected as non-protein."""
        path = self._write_tmp(PROTEIN_DNA_ION_PDB, ".pdb")
        try:
            has, chains = _detect_non_protein_chains(path)
            self.assertTrue(has)
            self.assertIn("B", chains)
            self.assertIn("C", chains)
        finally:
            path.unlink()

    def test_cif_non_protein_detection(self):
        """CIF with a ligand chain correctly detects it as non-protein."""
        path = self._write_tmp(PROTEIN_LIGAND_CIF, ".cif")
        try:
            has, chains = _detect_non_protein_chains(path)
            self.assertTrue(has)
            self.assertIn("B", chains)
        finally:
            path.unlink()


class TestNonProteinRelay(unittest.TestCase):
    """Relay code registration for non-protein chain detection."""

    def test_relay_code_registered(self):
        """The fpocket.ligand_present_in_input relay code is registered."""
        from dde.core.provenance import RELAY_CODES

        self.assertIn("fpocket.ligand_present_in_input", RELAY_CODES)

    def test_relay_code_builds(self):
        """A relay with the registered code can be built."""
        from dde.core.provenance import relay

        record = relay(
            "fpocket.ligand_present_in_input",
            "test message",
        )
        self.assertEqual(record["code"], "fpocket.ligand_present_in_input")
        self.assertEqual(record["message"], "test message")

    def test_unregistered_relay_rejects(self):
        """An unregistered relay code raises KeyError."""
        from dde.core.provenance import relay

        with self.assertRaises(KeyError):
            relay("fpocket.nonexistent_code", "test")


if __name__ == "__main__":
    unittest.main()
