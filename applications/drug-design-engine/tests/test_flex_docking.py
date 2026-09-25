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

"""Tests for flexible-receptor docking mode (#226).

Covers:
  - _parse_flexible_residues validation and parsing
  - _split_flexible_receptor partitioning
  - Flexible residue sidecar metadata recording
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.docking import (
    _parse_flexible_residues,
    _split_flexible_receptor,
)
from dde.core.errors import ArtifactError, UsageError

# ---------------------------------------------------------------------------
# Sample PDBQT content for testing
# ---------------------------------------------------------------------------

SAMPLE_RECEPTOR_PDBQT = """\
REMARK  receptor prepared by mk_prepare_receptor.py
ATOM      1  N   ALA A  10       1.000   2.000   3.000  1.00  0.00    +0.000 N
ATOM      2  CA  ALA A  10       2.000   3.000   4.000  1.00  0.00    +0.000 C
ATOM      3  N   ARG A 455      10.000  20.000  30.000  1.00  0.00    +0.000 N
ATOM      4  CA  ARG A 455      11.000  21.000  31.000  1.00  0.00    +0.000 C
ATOM      5  CB  ARG A 455      12.000  22.000  32.000  1.00  0.00    +0.000 C
ATOM      6  N   GLU A 526      20.000  30.000  40.000  1.00  0.00    +0.000 N
ATOM      7  CA  GLU A 526      21.000  31.000  41.000  1.00  0.00    +0.000 C
ATOM      8  N   TYR B  10      50.000  60.000  70.000  1.00  0.00    +0.000 N
END
"""


# ---------------------------------------------------------------------------
# 1. _parse_flexible_residues tests
# ---------------------------------------------------------------------------


def test_parse_single_residue() -> None:
    """Single residue spec parses correctly."""
    result = _parse_flexible_residues("A:ARG455")
    assert len(result) == 1
    assert result[0] == {"chain": "A", "res_name": "ARG", "res_num": "455"}
    print("  PASS: single residue spec")


def test_parse_multiple_residues() -> None:
    """Multiple comma-separated residue specs parse correctly."""
    result = _parse_flexible_residues("A:ARG455,A:GLU526,A:TYR829")
    assert len(result) == 3
    assert result[0]["res_name"] == "ARG"
    assert result[1]["res_name"] == "GLU"
    assert result[2]["res_name"] == "TYR"
    print("  PASS: multiple residue specs")


def test_parse_residues_with_spaces() -> None:
    """Spaces around tokens are stripped."""
    result = _parse_flexible_residues(" A:ARG455 , B:GLU10 ")
    assert len(result) == 2
    assert result[0]["chain"] == "A"
    assert result[1]["chain"] == "B"
    print("  PASS: spaces stripped")


def test_parse_missing_colon_raises() -> None:
    """Residue spec without colon raises UsageError."""
    try:
        _parse_flexible_residues("ARG455")
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: missing colon raises UsageError")


def test_parse_no_number_raises() -> None:
    """Residue spec without number raises UsageError."""
    try:
        _parse_flexible_residues("A:ARG")
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: no residue number raises UsageError")


def test_parse_no_name_raises() -> None:
    """Residue spec without name raises UsageError."""
    try:
        _parse_flexible_residues("A:455")
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: no residue name raises UsageError")


def test_parse_empty_raises() -> None:
    """Empty spec raises UsageError."""
    try:
        _parse_flexible_residues("")
        assert False, "Expected UsageError"
    except UsageError:
        pass
    print("  PASS: empty spec raises UsageError")


# ---------------------------------------------------------------------------
# 2. _split_flexible_receptor tests
# ---------------------------------------------------------------------------


def test_split_single_residue() -> None:
    """Splitting extracts the specified residue into flex output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        receptor = Path(tmpdir) / "receptor.pdbqt"
        receptor.write_text(SAMPLE_RECEPTOR_PDBQT)

        rigid_out = Path(tmpdir) / "rigid.pdbqt"
        flex_out = Path(tmpdir) / "flex.pdbqt"

        specs = [{"chain": "A", "res_name": "ARG", "res_num": "455"}]
        _split_flexible_receptor(receptor, specs, rigid_out, flex_out)

        rigid_text = rigid_out.read_text()
        flex_text = flex_out.read_text()

        # ARG 455 atoms should NOT be in rigid
        assert "ARG A 455" not in rigid_text
        # ARG 455 atoms should be in flex
        assert "ARG A 455" in flex_text
        # Other residues should be in rigid
        assert "ALA A  10" in rigid_text
        assert "GLU A 526" in rigid_text
        # BEGIN_RES / END_RES markers
        assert "BEGIN_RES ARG A 455" in flex_text
        assert "END_RES ARG A 455" in flex_text

    print("  PASS: split single residue")


def test_split_multiple_residues() -> None:
    """Splitting extracts multiple specified residues."""
    with tempfile.TemporaryDirectory() as tmpdir:
        receptor = Path(tmpdir) / "receptor.pdbqt"
        receptor.write_text(SAMPLE_RECEPTOR_PDBQT)

        rigid_out = Path(tmpdir) / "rigid.pdbqt"
        flex_out = Path(tmpdir) / "flex.pdbqt"

        specs = [
            {"chain": "A", "res_name": "ARG", "res_num": "455"},
            {"chain": "A", "res_name": "GLU", "res_num": "526"},
        ]
        _split_flexible_receptor(receptor, specs, rigid_out, flex_out)

        rigid_text = rigid_out.read_text()
        flex_text = flex_out.read_text()

        # Flexible residues should NOT be in rigid
        assert "ARG A 455" not in rigid_text
        assert "GLU A 526" not in rigid_text
        # Flexible residues should be in flex
        assert "BEGIN_RES ARG A 455" in flex_text
        assert "BEGIN_RES GLU A 526" in flex_text
        # Non-flex residues should remain in rigid
        assert "ALA A  10" in rigid_text
        assert "TYR B  10" in rigid_text

    print("  PASS: split multiple residues")


def test_split_missing_residue_raises() -> None:
    """Specifying a residue not in the receptor raises ArtifactError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        receptor = Path(tmpdir) / "receptor.pdbqt"
        receptor.write_text(SAMPLE_RECEPTOR_PDBQT)

        rigid_out = Path(tmpdir) / "rigid.pdbqt"
        flex_out = Path(tmpdir) / "flex.pdbqt"

        specs = [{"chain": "A", "res_name": "LYS", "res_num": "999"}]
        try:
            _split_flexible_receptor(receptor, specs, rigid_out, flex_out)
            assert False, "Expected ArtifactError"
        except ArtifactError:
            pass

    print("  PASS: missing residue raises ArtifactError")


def test_split_preserves_non_atom_lines() -> None:
    """Non-ATOM/HETATM lines (REMARK, END) stay in rigid output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        receptor = Path(tmpdir) / "receptor.pdbqt"
        receptor.write_text(SAMPLE_RECEPTOR_PDBQT)

        rigid_out = Path(tmpdir) / "rigid.pdbqt"
        flex_out = Path(tmpdir) / "flex.pdbqt"

        specs = [{"chain": "A", "res_name": "ARG", "res_num": "455"}]
        _split_flexible_receptor(receptor, specs, rigid_out, flex_out)

        rigid_text = rigid_out.read_text()
        assert "REMARK" in rigid_text
        assert "END" in rigid_text

    print("  PASS: non-atom lines preserved in rigid")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    print("test_flex_docking.py")
    print("=" * 40)

    print("\n1. _parse_flexible_residues")
    test_parse_single_residue()
    test_parse_multiple_residues()
    test_parse_residues_with_spaces()
    test_parse_missing_colon_raises()
    test_parse_no_number_raises()
    test_parse_no_name_raises()
    test_parse_empty_raises()

    print("\n2. _split_flexible_receptor")
    test_split_single_residue()
    test_split_multiple_residues()
    test_split_missing_residue_raises()
    test_split_preserves_non_atom_lines()

    print("\nAll tests passed.")
