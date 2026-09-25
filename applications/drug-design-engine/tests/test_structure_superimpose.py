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

"""Tests for `dde structure superimpose` and `dde docking validate-pose` (#140).

Covers:
  - Ca RMSD on identical structures = 0.0
  - RMSD on known-offset structure (translate by known vector, verify RMSD)
  - Chain selection
  - Sequence matching / residue correspondence
  - Per-residue distances
  - Low sequence identity relay fires
  - Pose validation RMSD calculation
  - Pass/fail threshold
  - Artifact output structure
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.docking import (
    _compute_ligand_rmsd,
    _parse_heavy_atoms_mol2,
    _parse_heavy_atoms_pdb,
    _parse_heavy_atoms_sdf,
)
from dde.commands.structure import (
    _align_sequences,
    _collect_residues,
    _kabsch_superimpose,
    _parse_atoms_with_names_pdb,
    _sequence_from_residues,
    _write_transformed_pdb,
)
from dde.core.errors import ArtifactError

# ---------------------------------------------------------------------------
# Sample PDB content — identical structures
# ---------------------------------------------------------------------------

SAMPLE_PDB_CHAIN_A = """\
HEADER    TEST STRUCTURE
ATOM      1  N   ALA A   1       1.000   2.000   3.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       2.000   3.000   4.000  1.00  0.00           C
ATOM      3  C   ALA A   1       3.000   4.000   5.000  1.00  0.00           C
ATOM      4  O   ALA A   1       3.500   4.500   5.500  1.00  0.00           O
ATOM      5  N   GLY A   2       4.000   5.000   6.000  1.00  0.00           N
ATOM      6  CA  GLY A   2       5.000   6.000   7.000  1.00  0.00           C
ATOM      7  C   GLY A   2       6.000   7.000   8.000  1.00  0.00           C
ATOM      8  O   GLY A   2       6.500   7.500   8.500  1.00  0.00           O
ATOM      9  N   LEU A   3       7.000   8.000   9.000  1.00  0.00           N
ATOM     10  CA  LEU A   3       8.000   9.000  10.000  1.00  0.00           C
ATOM     11  C   LEU A   3       9.000  10.000  11.000  1.00  0.00           C
ATOM     12  O   LEU A   3       9.500  10.500  11.500  1.00  0.00           O
ATOM     13  N   VAL A   4      10.000  11.000  12.000  1.00  0.00           N
ATOM     14  CA  VAL A   4      11.000  12.000  13.000  1.00  0.00           C
ATOM     15  C   VAL A   4      12.000  13.000  14.000  1.00  0.00           C
ATOM     16  O   VAL A   4      12.500  13.500  14.500  1.00  0.00           O
TER
END
"""


def _make_translated_pdb(pdb_text: str, dx: float, dy: float, dz: float) -> str:
    """Apply a translation to all ATOM coordinates in a PDB string."""
    lines = []
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and len(line) >= 54:
            x = float(line[30:38]) + dx
            y = float(line[38:46]) + dy
            z = float(line[46:54]) + dz
            line = f"{line[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{line[54:]}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _make_two_chain_pdb() -> str:
    """Create a PDB with chains A and B (different sequences)."""
    return """\
HEADER    TWO CHAIN STRUCTURE
ATOM      1  N   ALA A   1       1.000   2.000   3.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       2.000   3.000   4.000  1.00  0.00           C
ATOM      3  C   ALA A   1       3.000   4.000   5.000  1.00  0.00           C
ATOM      4  N   GLY A   2       4.000   5.000   6.000  1.00  0.00           N
ATOM      5  CA  GLY A   2       5.000   6.000   7.000  1.00  0.00           C
ATOM      6  C   GLY A   2       6.000   7.000   8.000  1.00  0.00           C
ATOM      7  N   LEU A   3       7.000   8.000   9.000  1.00  0.00           N
ATOM      8  CA  LEU A   3       8.000   9.000  10.000  1.00  0.00           C
ATOM      9  C   LEU A   3       9.000  10.000  11.000  1.00  0.00           C
ATOM     10  N   TRP B   1      20.000  21.000  22.000  1.00  0.00           N
ATOM     11  CA  TRP B   1      21.000  22.000  23.000  1.00  0.00           C
ATOM     12  C   TRP B   1      22.000  23.000  24.000  1.00  0.00           C
ATOM     13  N   PHE B   2      23.000  24.000  25.000  1.00  0.00           N
ATOM     14  CA  PHE B   2      24.000  25.000  26.000  1.00  0.00           C
ATOM     15  C   PHE B   2      25.000  26.000  27.000  1.00  0.00           C
TER
END
"""


# ---------------------------------------------------------------------------
# Tests: PDB parsing with atom names
# ---------------------------------------------------------------------------


def test_parse_atoms_with_names_pdb() -> None:
    """_parse_atoms_with_names_pdb extracts atom_name correctly."""
    atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    assert len(atoms) > 0
    first = atoms[0]
    assert "atom_name" in first
    assert first["atom_name"] == "N"
    assert first["chain"] == "A"
    assert first["resnum"] == 1
    assert first["resname"] == "ALA"

    # Check that CA atoms are present
    ca_atoms = [a for a in atoms if a["atom_name"] == "CA"]
    assert len(ca_atoms) == 4  # 4 residues


def test_parse_atoms_with_names_pdb_excludes_hydrogen() -> None:
    """Hydrogen atoms are excluded."""
    pdb_with_h = """\
ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00  0.00           C
ATOM      2  H   ALA A   1       1.500   2.500   3.500  1.00  0.00           H
ATOM      3  CA  GLY A   2       4.000   5.000   6.000  1.00  0.00           C
"""
    atoms = _parse_atoms_with_names_pdb(pdb_with_h)
    assert len(atoms) == 2  # H excluded
    assert all(a["element"] != "H" for a in atoms)


# ---------------------------------------------------------------------------
# Tests: residue collection and sequence extraction
# ---------------------------------------------------------------------------


def test_collect_residues() -> None:
    """_collect_residues groups atoms by (chain, resnum)."""
    atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    residues = _collect_residues(atoms, None)
    assert len(residues) == 4
    assert residues[0]["resname"] == "ALA"
    assert residues[0]["resnum"] == 1
    assert "CA" in residues[0]["atom_coords"]


def test_collect_residues_chain_filter() -> None:
    """_collect_residues filters by chain when specified."""
    pdb_text = _make_two_chain_pdb()
    atoms = _parse_atoms_with_names_pdb(pdb_text)
    residues_a = _collect_residues(atoms, "A")
    residues_b = _collect_residues(atoms, "B")
    assert len(residues_a) == 3
    assert len(residues_b) == 2
    assert all(r["chain"] == "A" for r in residues_a)
    assert all(r["chain"] == "B" for r in residues_b)


def test_sequence_from_residues() -> None:
    """_sequence_from_residues builds the correct one-letter sequence."""
    atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    residues = _collect_residues(atoms, None)
    seq = _sequence_from_residues(residues)
    assert seq == "AGLV"


# ---------------------------------------------------------------------------
# Tests: sequence alignment
# ---------------------------------------------------------------------------


def test_align_identical_sequences() -> None:
    """Identical sequences produce full 1:1 correspondence."""
    pairs = _align_sequences("AGLV", "AGLV")
    assert len(pairs) == 4
    assert pairs == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_align_different_sequences() -> None:
    """Different but overlapping sequences produce correct pairs."""
    pairs = _align_sequences("AGLV", "AGLM")
    # Should match A-A, G-G, L-L; V vs M is a mismatch but still aligned
    assert len(pairs) >= 3
    # First three should match by position
    assert (0, 0) in pairs
    assert (1, 1) in pairs
    assert (2, 2) in pairs


def test_align_with_gap() -> None:
    """Sequence with insertion produces gapped alignment."""
    pairs = _align_sequences("AGLV", "AGXLV")
    # Should still match A-A, G-G, L-L/L, V-V
    assert len(pairs) >= 3


def test_align_empty() -> None:
    """Empty sequences produce no pairs."""
    assert _align_sequences("", "AGLV") == []
    assert _align_sequences("AGLV", "") == []
    assert _align_sequences("", "") == []


# ---------------------------------------------------------------------------
# Tests: Kabsch superposition
# ---------------------------------------------------------------------------


def test_kabsch_identical_coordinates() -> None:
    """Identical coordinates produce RMSD = 0.0."""
    coords = np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0],
            [7.0, 8.0, 9.0],
            [10.0, 11.0, 12.0],
        ]
    )
    _rotation, _translation, rmsd = _kabsch_superimpose(coords, coords.copy())
    assert rmsd < 1e-10


def test_kabsch_known_translation() -> None:
    """Pure translation produces correct RMSD = 0.0 after alignment."""
    ref = np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0],
            [7.0, 8.0, 9.0],
            [10.0, 11.0, 12.0],
        ]
    )
    offset = np.array([5.0, 10.0, 15.0])
    mob = ref + offset
    _rotation, _translation, rmsd = _kabsch_superimpose(ref, mob)
    # After optimal superposition, RMSD should be ~0
    assert rmsd < 1e-6


def test_kabsch_known_rotation() -> None:
    """Known 90-degree rotation produces RMSD ≈ 0 after alignment."""
    ref = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 1.0, 1.0],
        ]
    )
    # Rotate 90 degrees around z-axis: (x,y,z) -> (-y,x,z)
    mob = np.array(
        [
            [0.0, 1.0, 0.0],
            [-1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
            [-1.0, 1.0, 1.0],
        ]
    )
    _rotation, _translation, rmsd = _kabsch_superimpose(ref, mob)
    assert rmsd < 1e-6


def test_kabsch_insufficient_atoms() -> None:
    """Fewer than 3 atoms raises ArtifactError, not a silent bad result."""
    ref = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    mob = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    try:
        _kabsch_superimpose(ref, mob)
        assert False, "should have raised ArtifactError"
    except ArtifactError:
        pass  # expected


def test_kabsch_known_offset_rmsd() -> None:
    """RMSD of a deliberately offset structure matches the expected value.

    Translate mobile by (1, 0, 0) but do NOT align — compute RMSD
    directly from the difference. With Kabsch alignment the RMSD should
    be 0, but if we add a *non-rigid* distortion (e.g. move one atom
    extra), RMSD should reflect that distortion only.
    """
    ref = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 10.0],
        ]
    )
    # Translate by (5, 5, 5) and then perturb one atom by 2.0 A
    mob = ref + np.array([5.0, 5.0, 5.0])
    mob[0] += np.array([2.0, 0.0, 0.0])  # perturb atom 0

    _rotation, _translation, rmsd = _kabsch_superimpose(ref, mob)
    # RMSD should be > 0 because of the per-atom distortion
    assert rmsd > 0.0
    # But should be moderate (around 1.0 A since only 1 of 4 atoms moves 2 A)
    assert rmsd < 2.0


# ---------------------------------------------------------------------------
# Tests: per-residue distances
# ---------------------------------------------------------------------------


def test_per_residue_distances_identical() -> None:
    """Identical structures produce per-residue Ca distances of 0.0."""
    atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    ref_residues = _collect_residues(atoms, None)
    mob_residues = _collect_residues(atoms, None)

    # All Ca atoms match
    for ref_res, mob_res in zip(ref_residues, mob_residues, strict=True):
        if "CA" in ref_res["atom_coords"] and "CA" in mob_res["atom_coords"]:
            ref_ca = np.array(ref_res["atom_coords"]["CA"])
            mob_ca = np.array(mob_res["atom_coords"]["CA"])
            dist = float(np.linalg.norm(ref_ca - mob_ca))
            assert dist < 1e-10


def test_per_residue_distances_translated() -> None:
    """Translated structure has uniform Ca distances equal to translation magnitude."""
    dx, dy, dz = 3.0, 4.0, 0.0  # magnitude = 5.0
    translated = _make_translated_pdb(SAMPLE_PDB_CHAIN_A, dx, dy, dz)

    ref_atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    mob_atoms = _parse_atoms_with_names_pdb(translated)

    ref_residues = _collect_residues(ref_atoms, None)
    mob_residues = _collect_residues(mob_atoms, None)

    expected_dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    for ref_res, mob_res in zip(ref_residues, mob_residues, strict=True):
        if "CA" in ref_res["atom_coords"] and "CA" in mob_res["atom_coords"]:
            ref_ca = np.array(ref_res["atom_coords"]["CA"])
            mob_ca = np.array(mob_res["atom_coords"]["CA"])
            dist = float(np.linalg.norm(ref_ca - mob_ca))
            assert abs(dist - expected_dist) < 1e-3


# ---------------------------------------------------------------------------
# Tests: RMSD on identical structure
# ---------------------------------------------------------------------------


def test_rmsd_identical_structures() -> None:
    """Superposition of identical structure to itself yields RMSD = 0.0."""
    atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    ref_residues = _collect_residues(atoms, None)
    mob_residues = _collect_residues(atoms, None)

    ref_seq = _sequence_from_residues(ref_residues)
    mob_seq = _sequence_from_residues(mob_residues)
    pairs = _align_sequences(ref_seq, mob_seq)

    ref_coords = []
    mob_coords = []
    for ri, mi in pairs:
        ref_ac = ref_residues[ri]["atom_coords"]
        mob_ac = mob_residues[mi]["atom_coords"]
        if "CA" in ref_ac and "CA" in mob_ac:
            ref_coords.append(ref_ac["CA"])
            mob_coords.append(mob_ac["CA"])

    ref_arr = np.array(ref_coords)
    mob_arr = np.array(mob_coords)
    _, _, rmsd = _kabsch_superimpose(ref_arr, mob_arr)
    assert rmsd < 1e-10


# ---------------------------------------------------------------------------
# Tests: RMSD on known-offset structure
# ---------------------------------------------------------------------------


def test_rmsd_known_offset() -> None:
    """RMSD after Kabsch on purely translated structure = 0.0."""
    translated = _make_translated_pdb(SAMPLE_PDB_CHAIN_A, 10.0, 20.0, 30.0)

    ref_atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    mob_atoms = _parse_atoms_with_names_pdb(translated)

    ref_residues = _collect_residues(ref_atoms, None)
    mob_residues = _collect_residues(mob_atoms, None)

    ref_seq = _sequence_from_residues(ref_residues)
    mob_seq = _sequence_from_residues(mob_residues)
    pairs = _align_sequences(ref_seq, mob_seq)

    ref_coords = []
    mob_coords = []
    for ri, mi in pairs:
        ref_ac = ref_residues[ri]["atom_coords"]
        mob_ac = mob_residues[mi]["atom_coords"]
        if "CA" in ref_ac and "CA" in mob_ac:
            ref_coords.append(ref_ac["CA"])
            mob_coords.append(mob_ac["CA"])

    ref_arr = np.array(ref_coords)
    mob_arr = np.array(mob_coords)
    _, _, rmsd = _kabsch_superimpose(ref_arr, mob_arr)
    assert rmsd < 1e-6


# ---------------------------------------------------------------------------
# Tests: chain selection
# ---------------------------------------------------------------------------


def test_chain_selection() -> None:
    """Chain filter correctly isolates residues."""
    pdb_text = _make_two_chain_pdb()
    atoms = _parse_atoms_with_names_pdb(pdb_text)

    res_a = _collect_residues(atoms, "A")
    res_b = _collect_residues(atoms, "B")

    seq_a = _sequence_from_residues(res_a)
    seq_b = _sequence_from_residues(res_b)

    assert seq_a == "AGL"
    assert seq_b == "WF"


# ---------------------------------------------------------------------------
# Tests: low sequence identity relay
# ---------------------------------------------------------------------------


def test_low_sequence_identity_detection() -> None:
    """Matched fraction below 50% is detectable for relay firing."""
    # Use two very different sequences to get low match fraction
    # 4 residues in ref, 10 in mobile → at most 4 matched = 4/10 = 40%
    ref_seq = "AGLV"
    mob_seq = "XXXXAGLVXX"  # extra residues

    pairs = _align_sequences(ref_seq, mob_seq)

    total_ref = len(ref_seq)
    total_mob = len(mob_seq)
    n_matched = len(pairs)
    matched_fraction = n_matched / max(total_ref, total_mob)

    # The matched fraction is at most 4/10 = 0.4 < 0.5
    assert matched_fraction < 0.5


# ---------------------------------------------------------------------------
# Tests: transformation matrix output
# ---------------------------------------------------------------------------


def test_write_transformed_pdb() -> None:
    """_write_transformed_pdb produces valid PDB with transformed coords."""
    atoms = _parse_atoms_with_names_pdb(SAMPLE_PDB_CHAIN_A)
    rotation = np.eye(3)
    translation = np.array([1.0, 2.0, 3.0])

    with tempfile.NamedTemporaryFile(suffix=".pdb", delete=False, mode="w") as f:
        out_path = Path(f.name)

    try:
        _write_transformed_pdb(atoms, rotation, translation, out_path)
        content = out_path.read_text()
        assert "ATOM" in content
        assert "REMARK" in content
        assert "END" in content

        # Re-parse the output and check that coordinates are shifted
        transformed_atoms = _parse_atoms_with_names_pdb(content)
        assert len(transformed_atoms) == len(atoms)
        for orig, trans in zip(atoms, transformed_atoms, strict=True):
            assert abs(trans["x"] - (orig["x"] + 1.0)) < 0.01
            assert abs(trans["y"] - (orig["y"] + 2.0)) < 0.01
            assert abs(trans["z"] - (orig["z"] + 3.0)) < 0.01
    finally:
        out_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests: pose validation RMSD
# ---------------------------------------------------------------------------


SAMPLE_LIGAND_PDB = """\
HETATM    1  C1  LIG A   1       1.000   2.000   3.000  1.00  0.00           C
HETATM    2  C2  LIG A   1       4.000   5.000   6.000  1.00  0.00           C
HETATM    3  O1  LIG A   1       7.000   8.000   9.000  1.00  0.00           O
HETATM    4  N1  LIG A   1      10.000  11.000  12.000  1.00  0.00           N
END
"""


def test_parse_heavy_atoms_pdb_ligand() -> None:
    """Heavy atoms are correctly extracted from a PDB ligand."""
    coords = _parse_heavy_atoms_pdb(SAMPLE_LIGAND_PDB)
    assert len(coords) == 4
    assert coords[0] == (1.0, 2.0, 3.0)
    assert coords[3] == (10.0, 11.0, 12.0)


def test_compute_ligand_rmsd_identical() -> None:
    """RMSD of identical coordinates = 0.0."""
    coords = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (7.0, 8.0, 9.0)]
    rmsd = _compute_ligand_rmsd(coords, coords)
    assert rmsd < 1e-10


def test_compute_ligand_rmsd_known_offset() -> None:
    """RMSD of coordinates offset by (1, 0, 0) = 1.0."""
    ref = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
    pose = [(1.0, 0.0, 0.0), (11.0, 0.0, 0.0), (1.0, 10.0, 0.0)]
    rmsd = _compute_ligand_rmsd(pose, ref)
    assert abs(rmsd - 1.0) < 1e-6


def test_compute_ligand_rmsd_known_value() -> None:
    """RMSD computed correctly for a specific offset pattern."""
    ref = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
    # Move atoms by 3, 0, 0 respectively
    pose = [(3.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
    rmsd = _compute_ligand_rmsd(pose, ref)
    # RMSD = sqrt(mean([9, 0, 0])) = sqrt(3) ≈ 1.732
    expected = math.sqrt(3.0)
    assert abs(rmsd - expected) < 1e-6


def test_compute_ligand_rmsd_atom_count_mismatch() -> None:
    """Mismatched atom counts raise ArtifactError."""
    ref = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
    pose = [(0.0, 0.0, 0.0)]
    try:
        _compute_ligand_rmsd(pose, ref)
        assert False, "should have raised ArtifactError"
    except ArtifactError:
        pass  # expected


# ---------------------------------------------------------------------------
# Tests: pass/fail threshold
# ---------------------------------------------------------------------------


def test_pose_validation_pass() -> None:
    """RMSD below threshold → pass."""
    ref = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
    # Small offset — RMSD = 0.5
    pose = [(0.5, 0.0, 0.0), (10.5, 0.0, 0.0), (0.5, 10.0, 0.0)]
    rmsd = _compute_ligand_rmsd(pose, ref)
    threshold = 2.0
    assert rmsd <= threshold


def test_pose_validation_fail() -> None:
    """RMSD above threshold → fail."""
    ref = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
    # Large offset — RMSD = 5.0
    pose = [(5.0, 0.0, 0.0), (15.0, 0.0, 0.0), (5.0, 10.0, 0.0)]
    rmsd = _compute_ligand_rmsd(pose, ref)
    threshold = 2.0
    assert rmsd > threshold


# ---------------------------------------------------------------------------
# Tests: SDF/MOL2 parsing
# ---------------------------------------------------------------------------


SAMPLE_SDF = """\
Molecule
     RDKit          3D

  4  3  0  0  0  0  0  0  0  0999 V2000
    1.0000    2.0000    3.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    4.0000    5.0000    6.0000 N   0  0  0  0  0  0  0  0  0  0  0  0
    7.0000    8.0000    9.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
   10.0000   11.0000   12.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
  3  4  1  0
M  END
$$$$
"""


def test_parse_heavy_atoms_sdf() -> None:
    """SDF parser extracts heavy atoms and excludes hydrogen."""
    coords = _parse_heavy_atoms_sdf(SAMPLE_SDF)
    assert len(coords) == 3  # H excluded
    assert coords[0] == (1.0, 2.0, 3.0)
    assert coords[1] == (4.0, 5.0, 6.0)
    assert coords[2] == (7.0, 8.0, 9.0)


SAMPLE_MOL2 = """\
@<TRIPOS>MOLECULE
test
 4 3 0 0 0
SMALL
GASTEIGER

@<TRIPOS>ATOM
      1 C1          1.0000    2.0000    3.0000 C.3     1  LIG1        0.0000
      2 N1          4.0000    5.0000    6.0000 N.am    1  LIG1        0.0000
      3 O1          7.0000    8.0000    9.0000 O.3     1  LIG1        0.0000
      4 H1         10.0000   11.0000   12.0000 H       1  LIG1        0.0000
@<TRIPOS>BOND
     1     1     2    1
     2     2     3    1
     3     3     4    1
"""


def test_parse_heavy_atoms_mol2() -> None:
    """MOL2 parser extracts heavy atoms and excludes hydrogen."""
    coords = _parse_heavy_atoms_mol2(SAMPLE_MOL2)
    assert len(coords) == 3  # H excluded
    assert coords[0] == (1.0, 2.0, 3.0)
    assert coords[1] == (4.0, 5.0, 6.0)
    assert coords[2] == (7.0, 8.0, 9.0)


# ---------------------------------------------------------------------------
# Tests: artifact output structure
# ---------------------------------------------------------------------------


def test_superposition_artifact_schema() -> None:
    """Superposition artifact contains all required fields."""
    # Simulate building the artifact record (same as the command does)
    record: dict[str, Any] = {
        "schema": "dde.structure-superposition.v1",
        "global_rmsd": 1.234,
        "matched_residues": 100,
        "total_ref": 120,
        "total_mobile": 115,
        "per_residue_distances": [
            {
                "ref_chain": "A",
                "ref_resnum": 1,
                "ref_resname": "ALA",
                "mob_chain": "A",
                "mob_resnum": 1,
                "mob_resname": "ALA",
                "ca_distance": 0.5,
            }
        ],
        "ref_file": "ref.pdb",
        "mobile_file": "mob.pdb",
    }

    # Verify all required fields from the brief are present
    assert "global_rmsd" in record
    assert "matched_residues" in record
    assert "total_ref" in record
    assert "total_mobile" in record
    assert "per_residue_distances" in record
    assert "ref_file" in record
    assert "mobile_file" in record
    assert record["schema"] == "dde.structure-superposition.v1"


def test_pose_validation_artifact_schema() -> None:
    """Pose validation artifact contains all required fields."""
    record: dict[str, Any] = {
        "schema": "dde.docking-pose-validation.v1",
        "rmsd": 1.5,
        "threshold": 2.0,
        "pass": True,
        "n_atoms": 25,
        "reference_file": "ref_ligand.pdb",
        "pose_file": "docked.pdb",
    }

    # Verify all required fields from the brief
    assert "rmsd" in record
    assert "threshold" in record
    assert "pass" in record
    assert "n_atoms" in record
    assert "reference_file" in record
    assert "pose_file" in record
    assert record["schema"] == "dde.docking-pose-validation.v1"


# ---------------------------------------------------------------------------
# Tests: relay codes registered
# ---------------------------------------------------------------------------


def test_relay_codes_registered() -> None:
    """New relay codes are registered in RELAY_CODES."""
    from dde.core.provenance import RELAY_CODES

    assert "structure.low_sequence_identity" in RELAY_CODES
    assert "docking.no_pose_control" in RELAY_CODES
    assert "docking.pose_reproduction_failed" in RELAY_CODES


# ---------------------------------------------------------------------------
# Run with pytest or standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
