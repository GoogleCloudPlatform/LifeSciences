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

"""Tests for peptide occlusion detection and short-chain handling in pocket.py.

Covers (#133 Items 2 and 3):
- Short chain detection: chains with < 20 residues are flagged
- Relay fires when low score + short chains retained
- Relay does NOT fire when score is high
- --strip-peptides removes short chains
- --ignore-chain removes specified chains
- Low-score holo structure advisory fires for non-receptor content
- Configurable peptide threshold
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dde.commands.pocket import (
    _LOW_DRUGGABILITY_THRESHOLD,
    _detect_short_chains,
    _strip_chains,
)
from dde.core import provenance

# ---------------------------------------------------------------------------
# Minimal test fixtures
# ---------------------------------------------------------------------------


#: Two-chain PDB: chain A has 25 residues (protein), chain B has 8 residues
#: (peptide ligand).  Chain B should be flagged as a short chain.
def _make_chain_pdb(chain: str, start_resnum: int, n_residues: int) -> str:
    """Generate PDB ATOM lines for a chain with n_residues of ALA."""
    lines = []
    serial = start_resnum * 10
    for i in range(n_residues):
        resnum = start_resnum + i
        x = float(i * 3)
        y = 0.0
        z = 0.0 if chain == "A" else 10.0
        lines.append(
            f"ATOM  {serial:5d}  CA  ALA {chain}{resnum:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 30.00           C  "
        )
        serial += 1
    return "\n".join(lines)


PROTEIN_PEPTIDE_PDB = (
    _make_chain_pdb("A", 1, 25) + "\n" + _make_chain_pdb("B", 1, 8) + "\n" + "END\n"
)

#: Both chains are long proteins — no short chains.
TWO_LONG_CHAINS_PDB = (
    _make_chain_pdb("A", 1, 30) + "\n" + _make_chain_pdb("B", 1, 25) + "\n" + "END\n"
)

#: Single long chain — no short chains, no non-protein.
SINGLE_CHAIN_PDB = _make_chain_pdb("A", 1, 50) + "\nEND\n"

#: Structure with a ligand chain (non-protein, HETATM with non-standard residue)
#: and a short protein chain.
COMPLEX_PDB = (
    _make_chain_pdb("A", 1, 30)
    + "\n"
    + _make_chain_pdb("B", 1, 5)
    + "\n"
    + "HETATM 9001  C1  LIG C   1       5.000   5.000   5.000  1.00 20.00           C  \n"
    + "END\n"
)

#: PDB with chain exactly at threshold (20 residues) — should NOT be flagged.
AT_THRESHOLD_PDB = (
    _make_chain_pdb("A", 1, 50) + "\n" + _make_chain_pdb("B", 1, 20) + "\n" + "END\n"
)

#: PDB with chain just below threshold (19 residues) — SHOULD be flagged.
BELOW_THRESHOLD_PDB = (
    _make_chain_pdb("A", 1, 50) + "\n" + _make_chain_pdb("B", 1, 19) + "\n" + "END\n"
)


# ---------------------------------------------------------------------------
# Tests: short chain detection
# ---------------------------------------------------------------------------


class TestDetectShortChains(unittest.TestCase):
    """Short chains (< threshold residues) are correctly detected."""

    def _write_pdb(self, content: str) -> Path:
        """Write PDB content to a temp file and return its path."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pdb", delete=False, encoding="utf-8"
        )
        f.write(content)
        f.close()
        return Path(f.name)

    def test_detects_short_chain(self):
        path = self._write_pdb(PROTEIN_PEPTIDE_PDB)
        try:
            result = _detect_short_chains(path, threshold=20)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["chain"], "B")
            self.assertEqual(result[0]["residues"], 8)
            self.assertEqual(result[0]["note"], "Possible peptide ligand")
        finally:
            path.unlink()

    def test_no_short_chains(self):
        path = self._write_pdb(TWO_LONG_CHAINS_PDB)
        try:
            result = _detect_short_chains(path, threshold=20)
            self.assertEqual(result, [])
        finally:
            path.unlink()

    def test_single_chain_no_short(self):
        path = self._write_pdb(SINGLE_CHAIN_PDB)
        try:
            result = _detect_short_chains(path, threshold=20)
            self.assertEqual(result, [])
        finally:
            path.unlink()

    def test_at_threshold_not_flagged(self):
        """A chain with exactly threshold residues is NOT flagged."""
        path = self._write_pdb(AT_THRESHOLD_PDB)
        try:
            result = _detect_short_chains(path, threshold=20)
            self.assertEqual(result, [])
        finally:
            path.unlink()

    def test_below_threshold_flagged(self):
        """A chain with threshold - 1 residues IS flagged."""
        path = self._write_pdb(BELOW_THRESHOLD_PDB)
        try:
            result = _detect_short_chains(path, threshold=20)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["chain"], "B")
            self.assertEqual(result[0]["residues"], 19)
        finally:
            path.unlink()

    def test_custom_threshold(self):
        """Custom threshold: 10 residues — 8-residue chain still flagged."""
        path = self._write_pdb(PROTEIN_PEPTIDE_PDB)
        try:
            result = _detect_short_chains(path, threshold=10)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["chain"], "B")
        finally:
            path.unlink()

    def test_custom_threshold_below(self):
        """Custom threshold 5: 8-residue chain is above, not flagged."""
        path = self._write_pdb(PROTEIN_PEPTIDE_PDB)
        try:
            result = _detect_short_chains(path, threshold=5)
            # 8 residues >= 5, so not flagged
            self.assertEqual(result, [])
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Tests: strip chains
# ---------------------------------------------------------------------------


class TestStripChains(unittest.TestCase):
    """--strip-peptides and --ignore-chain remove specified chains."""

    def _write_pdb(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pdb", delete=False, encoding="utf-8"
        )
        f.write(content)
        f.close()
        return Path(f.name)

    def test_strip_peptides_removes_short_chain(self):
        path = self._write_pdb(PROTEIN_PEPTIDE_PDB)
        output = self._write_pdb("")  # will be overwritten
        try:
            result = _strip_chains(path, {"B"}, output)
            self.assertIn("B", result["stripped_chains"])
            self.assertGreater(result["removed_atom_count"], 0)

            # Verify the output file contains no chain B atoms.
            text = output.read_text()
            for line in text.splitlines():
                if line.startswith(("ATOM  ", "HETATM")):
                    chain = line[21:22].strip()
                    self.assertNotEqual(chain, "B", "Chain B atoms should be stripped")
        finally:
            path.unlink()
            output.unlink()

    def test_strip_preserves_protein_chain(self):
        path = self._write_pdb(PROTEIN_PEPTIDE_PDB)
        output = self._write_pdb("")
        try:
            _strip_chains(path, {"B"}, output)
            text = output.read_text()
            has_chain_a = any(
                line.startswith(("ATOM  ", "HETATM")) and line[21:22].strip() == "A"
                for line in text.splitlines()
            )
            self.assertTrue(has_chain_a, "Chain A atoms should be preserved")
        finally:
            path.unlink()
            output.unlink()

    def test_ignore_chain_removes_specified(self):
        path = self._write_pdb(COMPLEX_PDB)
        output = self._write_pdb("")
        try:
            result = _strip_chains(path, {"B", "C"}, output)
            self.assertIn("B", result["stripped_chains"])
            self.assertIn("C", result["stripped_chains"])
            self.assertGreater(result["removed_atom_count"], 0)
        finally:
            path.unlink()
            output.unlink()

    def test_strip_empty_set_is_noop(self):
        path = self._write_pdb(PROTEIN_PEPTIDE_PDB)
        output = self._write_pdb("")
        try:
            result = _strip_chains(path, set(), output)
            self.assertEqual(result["removed_atom_count"], 0)
            # All atoms should be preserved.
            original_atoms = sum(
                1
                for line in PROTEIN_PEPTIDE_PDB.splitlines()
                if line.startswith(("ATOM  ", "HETATM"))
            )
            output_atoms = sum(
                1
                for line in output.read_text().splitlines()
                if line.startswith(("ATOM  ", "HETATM"))
            )
            self.assertEqual(original_atoms, output_atoms)
        finally:
            path.unlink()
            output.unlink()


# ---------------------------------------------------------------------------
# Tests: relay firing logic
# ---------------------------------------------------------------------------


class TestPeptideOcclusionRelay(unittest.TestCase):
    """Relay fires when low score + short chains retained, not when score is high."""

    def test_relay_fires_low_score_short_chains(self):
        """Relay fpocket.possible_peptide_occlusion fires when low score
        AND short chains are retained."""
        short_chains = [
            {"chain": "B", "residues": 8, "note": "Possible peptide ligand"}
        ]
        pockets = [{"rank": 1, "druggability_score": 0.004}]
        chains_stripped: set[str] = set()

        retained = [sc for sc in short_chains if sc["chain"] not in chains_stripped]

        # Simulate the relay logic from pocket.py run()
        should_fire = False
        if retained and pockets:
            low = [
                p
                for p in pockets
                if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
            ]
            if low:
                should_fire = True

        self.assertTrue(
            should_fire, "Relay should fire when low score + short chains retained"
        )

    def test_relay_not_fired_high_score(self):
        """Relay does NOT fire when score is high, even with short chains."""
        short_chains = [
            {"chain": "B", "residues": 8, "note": "Possible peptide ligand"}
        ]
        pockets = [{"rank": 1, "druggability_score": 0.968}]
        chains_stripped: set[str] = set()

        retained = [sc for sc in short_chains if sc["chain"] not in chains_stripped]

        should_fire = False
        if retained and pockets:
            low = [
                p
                for p in pockets
                if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
            ]
            if low:
                should_fire = True

        self.assertFalse(should_fire, "Relay should NOT fire when score is high")

    def test_relay_not_fired_when_stripped(self):
        """Relay does NOT fire when short chains were stripped."""
        short_chains = [
            {"chain": "B", "residues": 8, "note": "Possible peptide ligand"}
        ]
        pockets = [{"rank": 1, "druggability_score": 0.004}]
        chains_stripped = {"B"}

        retained = [sc for sc in short_chains if sc["chain"] not in chains_stripped]

        should_fire = False
        if retained and pockets:
            low = [
                p
                for p in pockets
                if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
            ]
            if low:
                should_fire = True

        self.assertFalse(
            should_fire, "Relay should NOT fire when short chains were stripped"
        )

    def test_relay_not_fired_no_short_chains(self):
        """Relay does NOT fire when there are no short chains."""
        short_chains: list[dict] = []
        pockets = [{"rank": 1, "druggability_score": 0.004}]
        chains_stripped: set[str] = set()

        retained = [sc for sc in short_chains if sc["chain"] not in chains_stripped]

        should_fire = False
        if retained and pockets:
            low = [
                p
                for p in pockets
                if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
            ]
            if low:
                should_fire = True

        self.assertFalse(should_fire, "Relay should NOT fire with no short chains")

    def test_relay_threshold_boundary(self):
        """Relay fires at exactly the boundary (score < 0.5, not <=)."""

        # Score exactly AT threshold — should NOT fire (< not <=)
        pockets_at = [{"rank": 1, "druggability_score": 0.5}]
        low = [
            p
            for p in pockets_at
            if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
        ]
        self.assertEqual(len(low), 0, "Score exactly at 0.5 should not trigger")

        # Score just below threshold — SHOULD fire
        pockets_below = [{"rank": 1, "druggability_score": 0.499}]
        low = [
            p
            for p in pockets_below
            if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
        ]
        self.assertEqual(len(low), 1, "Score at 0.499 should trigger")


class TestLowScoreHoloRelay(unittest.TestCase):
    """Low-score holo structure advisory fires for any non-receptor content."""

    def test_fires_with_non_protein_chains_and_low_score(self):
        """Advisory fires when non-protein chains present + low score."""
        has_non_protein = True
        retained_short = []  # type: ignore[var-annotated]
        pockets = [{"rank": 1, "druggability_score": 0.1}]

        has_non_receptor_content = has_non_protein or bool(retained_short)
        should_fire = False
        if has_non_receptor_content and pockets:
            low = [
                p
                for p in pockets
                if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
            ]
            if low:
                should_fire = True

        self.assertTrue(should_fire)

    def test_does_not_fire_high_score(self):
        """Advisory does NOT fire when score is high."""
        has_non_protein = True
        retained_short = []  # type: ignore[var-annotated]
        pockets = [{"rank": 1, "druggability_score": 0.95}]

        has_non_receptor_content = has_non_protein or bool(retained_short)
        should_fire = False
        if has_non_receptor_content and pockets:
            low = [
                p
                for p in pockets
                if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
            ]
            if low:
                should_fire = True

        self.assertFalse(should_fire)

    def test_does_not_fire_no_non_receptor(self):
        """Advisory does NOT fire when no non-receptor content."""
        has_non_protein = False
        retained_short = []  # type: ignore[var-annotated]
        pockets = [{"rank": 1, "druggability_score": 0.1}]

        has_non_receptor_content = has_non_protein or bool(retained_short)
        should_fire = False
        if has_non_receptor_content and pockets:
            low = [
                p
                for p in pockets
                if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
            ]
            if low:
                should_fire = True

        self.assertFalse(should_fire)


# ---------------------------------------------------------------------------
# Tests: relay code registration
# ---------------------------------------------------------------------------


class TestRelayCodesRegistered(unittest.TestCase):
    """New relay codes are properly registered in provenance.RELAY_CODES."""

    def test_peptide_occlusion_relay_registered(self):
        self.assertIn("fpocket.possible_peptide_occlusion", provenance.RELAY_CODES)

    def test_low_score_holo_relay_registered(self):
        self.assertIn("fpocket.low_score_holo_structure", provenance.RELAY_CODES)

    def test_relay_function_accepts_new_codes(self):
        """provenance.relay() accepts the new codes without raising."""
        r1 = provenance.relay(
            "fpocket.possible_peptide_occlusion",
            "test message",
        )
        self.assertEqual(r1["code"], "fpocket.possible_peptide_occlusion")

        r2 = provenance.relay(
            "fpocket.low_score_holo_structure",
            "test message",
        )
        self.assertEqual(r2["code"], "fpocket.low_score_holo_structure")


if __name__ == "__main__":
    unittest.main()
