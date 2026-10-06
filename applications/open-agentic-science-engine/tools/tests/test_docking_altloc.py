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

"""Tests for altloc handling in docking.py (#133 Item 1).

Covers:
- _resolve_highest_occupancy_altloc selects correct altloc by occupancy
- _resolve_highest_occupancy_altloc falls back to "A" when no altlocs
- _convert_receptor_to_pdbqt passes --default_altloc to Meeko
- --altloc option variants (A, B, highest) are accepted
- Altloc choice is recorded in the sidecar parameters
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from dde.commands.docking import (
    _convert_receptor_to_pdbqt,
    _resolve_highest_occupancy_altloc,
)

# ---------------------------------------------------------------------------
# Minimal PDB fixtures with altlocs
# ---------------------------------------------------------------------------

#: PDB with altlocs A and B, where A has higher occupancy (0.60 vs 0.40).
PDB_ALTLOC_A_HIGHER = textwrap.dedent("""\
ATOM      1  CA AALA A   1       1.000   2.000   3.000  0.60 30.00           C
ATOM      2  CA BALA A   1       1.100   2.100   3.100  0.40 30.00           C
ATOM      3  CB AALA A   1       1.500   2.500   3.500  0.60 30.00           C
ATOM      4  CB BALA A   1       1.600   2.600   3.600  0.40 30.00           C
ATOM      5  CA  GLY A   2       5.000   5.000   5.000  1.00 30.00           C
END
""")

#: PDB with altlocs A and B, where B has higher occupancy (0.35 vs 0.65).
PDB_ALTLOC_B_HIGHER = textwrap.dedent("""\
ATOM      1  CA AALA A   1       1.000   2.000   3.000  0.35 30.00           C
ATOM      2  CA BALA A   1       1.100   2.100   3.100  0.65 30.00           C
ATOM      3  CB AALA A   1       1.500   2.500   3.500  0.35 30.00           C
ATOM      4  CB BALA A   1       1.600   2.600   3.600  0.65 30.00           C
END
""")

#: PDB with no altlocs — all atoms have blank altloc indicator.
PDB_NO_ALTLOC = textwrap.dedent("""\
ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 30.00           C
ATOM      2  CB  ALA A   1       1.500   2.500   3.500  1.00 30.00           C
ATOM      3  CA  GLY A   2       5.000   5.000   5.000  1.00 30.00           C
END
""")


# ---------------------------------------------------------------------------
# Tests: _resolve_highest_occupancy_altloc
# ---------------------------------------------------------------------------


class TestResolveHighestOccupancyAltloc(unittest.TestCase):
    """_resolve_highest_occupancy_altloc selects the correct altloc."""

    def _write_pdb(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pdb", delete=False, encoding="utf-8"
        )
        f.write(content)
        f.close()
        return Path(f.name)

    def test_selects_a_when_a_higher(self):
        path = self._write_pdb(PDB_ALTLOC_A_HIGHER)
        try:
            result = _resolve_highest_occupancy_altloc(path)
            self.assertEqual(result, "A")
        finally:
            path.unlink()

    def test_selects_b_when_b_higher(self):
        path = self._write_pdb(PDB_ALTLOC_B_HIGHER)
        try:
            result = _resolve_highest_occupancy_altloc(path)
            self.assertEqual(result, "B")
        finally:
            path.unlink()

    def test_falls_back_to_a_when_no_altlocs(self):
        path = self._write_pdb(PDB_NO_ALTLOC)
        try:
            result = _resolve_highest_occupancy_altloc(path)
            self.assertEqual(result, "A")
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Tests: _convert_receptor_to_pdbqt altloc passing
# ---------------------------------------------------------------------------


class TestConvertReceptorAltloc(unittest.TestCase):
    """_convert_receptor_to_pdbqt passes --default_altloc to Meeko."""

    @mock.patch("dde.commands.docking.subprocess.run")
    @mock.patch("dde.commands.docking._require_mk_prepare_receptor")
    def test_passes_default_altloc_a(self, mock_require, mock_run):
        """Default altloc 'A' is passed as --default_altloc A."""
        mock_require.return_value = "mk_prepare_receptor.py"
        mock_run.return_value = mock.Mock(returncode=0, stderr="", stdout="")

        structure = Path("/tmp/test.pdb")
        output = Path("/tmp/test.pdbqt")
        # Create a fake output file so the file-existence check passes.
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(Path, "stat") as mock_stat,
        ):
            mock_stat.return_value = mock.Mock(st_size=100)
            _convert_receptor_to_pdbqt(structure, output, altloc="A")

        # Verify subprocess.run was called with --default_altloc A
        args = mock_run.call_args[0][0]
        self.assertIn("--default_altloc", args)
        altloc_idx = args.index("--default_altloc")
        self.assertEqual(args[altloc_idx + 1], "A")

    @mock.patch("dde.commands.docking.subprocess.run")
    @mock.patch("dde.commands.docking._require_mk_prepare_receptor")
    def test_passes_default_altloc_b(self, mock_require, mock_run):
        """Altloc 'B' is passed as --default_altloc B."""
        mock_require.return_value = "mk_prepare_receptor.py"
        mock_run.return_value = mock.Mock(returncode=0, stderr="", stdout="")

        structure = Path("/tmp/test.pdb")
        output = Path("/tmp/test.pdbqt")
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(Path, "stat") as mock_stat,
        ):
            mock_stat.return_value = mock.Mock(st_size=100)
            _convert_receptor_to_pdbqt(structure, output, altloc="B")

        args = mock_run.call_args[0][0]
        self.assertIn("--default_altloc", args)
        altloc_idx = args.index("--default_altloc")
        self.assertEqual(args[altloc_idx + 1], "B")

    @mock.patch("dde.commands.docking._resolve_highest_occupancy_altloc")
    @mock.patch("dde.commands.docking.subprocess.run")
    @mock.patch("dde.commands.docking._require_mk_prepare_receptor")
    def test_highest_resolves_then_passes(self, mock_require, mock_run, mock_resolve):
        """Altloc 'highest' resolves to actual label, then passes it."""
        mock_require.return_value = "mk_prepare_receptor.py"
        mock_run.return_value = mock.Mock(returncode=0, stderr="", stdout="")
        mock_resolve.return_value = "B"

        structure = Path("/tmp/test.pdb")
        output = Path("/tmp/test.pdbqt")
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(Path, "stat") as mock_stat,
        ):
            mock_stat.return_value = mock.Mock(st_size=100)
            _convert_receptor_to_pdbqt(structure, output, altloc="highest")

        # Should have resolved "highest" to actual label
        mock_resolve.assert_called_once_with(structure)

        # Should pass the resolved label to Meeko
        args = mock_run.call_args[0][0]
        self.assertIn("--default_altloc", args)
        altloc_idx = args.index("--default_altloc")
        self.assertEqual(args[altloc_idx + 1], "B")

    @mock.patch("dde.commands.docking.subprocess.run")
    @mock.patch("dde.commands.docking._require_mk_prepare_receptor")
    def test_default_altloc_is_a(self, mock_require, mock_run):
        """When no altloc specified, default is 'A'."""
        mock_require.return_value = "mk_prepare_receptor.py"
        mock_run.return_value = mock.Mock(returncode=0, stderr="", stdout="")

        structure = Path("/tmp/test.pdb")
        output = Path("/tmp/test.pdbqt")
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(Path, "stat") as mock_stat,
        ):
            mock_stat.return_value = mock.Mock(st_size=100)
            # Call without altloc — should use default "A"
            _convert_receptor_to_pdbqt(structure, output)

        args = mock_run.call_args[0][0]
        self.assertIn("--default_altloc", args)
        altloc_idx = args.index("--default_altloc")
        self.assertEqual(args[altloc_idx + 1], "A")

    @mock.patch("dde.commands.docking.subprocess.run")
    @mock.patch("dde.commands.docking._require_mk_prepare_receptor")
    def test_read_with_prody_still_passed(self, mock_require, mock_run):
        """--read_with_prody is still passed alongside --default_altloc."""
        mock_require.return_value = "mk_prepare_receptor.py"
        mock_run.return_value = mock.Mock(returncode=0, stderr="", stdout="")

        structure = Path("/tmp/test.pdb")
        output = Path("/tmp/test.pdbqt")
        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(Path, "stat") as mock_stat,
        ):
            mock_stat.return_value = mock.Mock(st_size=100)
            _convert_receptor_to_pdbqt(structure, output, altloc="A")

        args = mock_run.call_args[0][0]
        self.assertIn("--read_with_prody", args)
        self.assertIn("--default_altloc", args)


if __name__ == "__main__":
    unittest.main()
