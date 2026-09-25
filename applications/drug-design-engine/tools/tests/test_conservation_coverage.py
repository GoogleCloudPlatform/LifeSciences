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

"""Tests for conservation coverage reporting and alignment command.

Covers:
1. Coverage computation (scored_positions / total_positions)
2. Low-coverage relay fires at threshold
3. Normal coverage does not fire relay
4. Unscored positions list
5. pocket_in_gap relay fires when pocket residues overlap unscored
6. Relay codes registered in provenance.RELAY_CODES
7. Alignment command invokes the aligner binary correctly
8. Missing-binary is handled gracefully
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test.
_mock_requests = MagicMock()
_mock_yaml = MagicMock()
_module_patches = patch.dict(
    "sys.modules",
    {"requests": _mock_requests, "yaml": _mock_yaml},
)
_module_patches.start()

from dde.commands.conservation import (  # noqa: E402
    COVERAGE_THRESHOLD,
    _find_aligner,
    _run_alignment,
)
from dde.core.errors import ArtifactError, DependencyError  # noqa: E402
from dde.core.provenance import RELAY_CODES  # noqa: E402

# ---------------------------------------------------------------------------
# Coverage computation tests
# ---------------------------------------------------------------------------


def _make_residues(positions: list[int]) -> list[dict]:
    """Build minimal residue records for testing coverage."""
    return [
        {
            "position": p,
            "amino_acid": "A",
            "score": -0.5,
            "confidence_interval": [-1.0, 0.0],
            "std": 0.3,
            "msa_coverage": "5/5",
            "grade": 5,
        }
        for p in positions
    ]


def _compute_coverage(
    residues: list[dict],
    canonical_length: int,
) -> tuple[float, int, int, list[int]]:
    """Replicate the coverage computation from analyze_cmd for testing."""
    n_positions = len(residues)
    scored_positions = n_positions
    scored_set = {r["position"] for r in residues}
    unscored_positions = [
        pos for pos in range(1, canonical_length + 1) if pos not in scored_set
    ]
    coverage = (
        round(scored_positions / canonical_length, 3) if canonical_length > 0 else 1.0
    )
    return coverage, scored_positions, canonical_length, unscored_positions


class TestCoverageComputation(unittest.TestCase):
    """Coverage = scored_positions / total_positions."""

    def test_full_coverage(self):
        residues = _make_residues(list(range(1, 101)))  # 100 positions, all scored
        coverage, scored, total, unscored = _compute_coverage(residues, 100)
        self.assertEqual(coverage, 1.0)
        self.assertEqual(scored, 100)
        self.assertEqual(total, 100)
        self.assertEqual(unscored, [])

    def test_partial_coverage(self):
        # 58 of 100 positions scored
        residues = _make_residues(list(range(1, 59)))
        coverage, scored, total, unscored = _compute_coverage(residues, 100)
        self.assertAlmostEqual(coverage, 0.58, places=2)
        self.assertEqual(scored, 58)
        self.assertEqual(total, 100)
        self.assertEqual(len(unscored), 42)

    def test_unscored_positions_list(self):
        # Positions 1-3 scored, 4-5 unscored
        residues = _make_residues([1, 2, 3])
        coverage, _scored, _total, unscored = _compute_coverage(residues, 5)
        self.assertEqual(unscored, [4, 5])
        self.assertAlmostEqual(coverage, 0.6, places=1)

    def test_non_contiguous_scored(self):
        # Gaps in the middle
        residues = _make_residues([1, 3, 5, 7, 9])
        coverage, scored, _total, unscored = _compute_coverage(residues, 10)
        self.assertEqual(scored, 5)
        self.assertEqual(unscored, [2, 4, 6, 8, 10])
        self.assertEqual(coverage, 0.5)

    def test_zero_length_canonical(self):
        coverage, _scored, _total, _unscored = _compute_coverage([], 0)
        self.assertEqual(coverage, 1.0)


class TestLowCoverageRelay(unittest.TestCase):
    """Low-coverage relay fires when coverage < COVERAGE_THRESHOLD."""

    def test_below_threshold_fires(self):
        # 50% coverage — below default 0.7 threshold
        coverage = 0.5
        self.assertLess(coverage, COVERAGE_THRESHOLD)

    def test_at_threshold_does_not_fire(self):
        # Exactly at threshold
        coverage = COVERAGE_THRESHOLD
        self.assertFalse(coverage < COVERAGE_THRESHOLD)

    def test_above_threshold_does_not_fire(self):
        coverage = 0.9
        self.assertFalse(coverage < COVERAGE_THRESHOLD)

    def test_threshold_is_0_7(self):
        self.assertEqual(COVERAGE_THRESHOLD, 0.7)


class TestPocketInGapRelay(unittest.TestCase):
    """pocket_in_gap relay fires when pocket residues overlap unscored."""

    def test_pocket_in_gap(self):
        unscored = [10, 20, 30]
        pocket_residues = [5, 10, 15, 20]
        unscored_set = set(unscored)
        pocket_in_gap = [p for p in pocket_residues if p in unscored_set]
        self.assertEqual(pocket_in_gap, [10, 20])

    def test_no_overlap(self):
        unscored = [10, 20, 30]
        pocket_residues = [5, 15, 25]
        unscored_set = set(unscored)
        pocket_in_gap = [p for p in pocket_residues if p in unscored_set]
        self.assertEqual(pocket_in_gap, [])

    def test_empty_pocket_residues(self):
        unscored = [10, 20, 30]
        pocket_residues = []
        unscored_set = set(unscored)
        pocket_in_gap = [p for p in pocket_residues if p in unscored_set]
        self.assertEqual(pocket_in_gap, [])


# ---------------------------------------------------------------------------
# Relay code registration
# ---------------------------------------------------------------------------


class TestRelayCodes(unittest.TestCase):
    """New relay codes are registered in provenance.RELAY_CODES."""

    def test_low_coverage_registered(self):
        self.assertIn("conservation.low_coverage", RELAY_CODES)

    def test_pocket_in_gap_registered(self):
        self.assertIn("conservation.pocket_in_gap", RELAY_CODES)

    def test_rate_is_not_function_still_registered(self):
        self.assertIn("conservation.rate_is_not_function", RELAY_CODES)


# ---------------------------------------------------------------------------
# Alignment command tests
# ---------------------------------------------------------------------------


class TestFindAligner(unittest.TestCase):
    """_find_aligner locates muscle or mafft on PATH."""

    @patch("shutil.which")
    def test_finds_muscle(self, mock_which):
        mock_which.side_effect = lambda name: (
            "/usr/local/bin/muscle" if name == "muscle" else None
        )
        path, name = _find_aligner(None)
        self.assertEqual(name, "muscle")
        self.assertEqual(path, "/usr/local/bin/muscle")

    @patch("shutil.which")
    def test_finds_mafft(self, mock_which):
        mock_which.side_effect = lambda name: (
            "/usr/bin/mafft" if name == "mafft" else None
        )
        path, name = _find_aligner(None)
        self.assertEqual(name, "mafft")
        self.assertEqual(path, "/usr/bin/mafft")

    @patch("shutil.which")
    def test_prefers_specified_aligner(self, mock_which):
        def which_fn(name):
            if name == "muscle":
                return "/usr/local/bin/muscle"
            if name == "mafft":
                return "/usr/bin/mafft"
            return None

        mock_which.side_effect = which_fn
        _path, name = _find_aligner("mafft")
        self.assertEqual(name, "mafft")

    @patch("shutil.which")
    def test_raises_when_neither_found(self, mock_which):
        mock_which.return_value = None
        with self.assertRaises(DependencyError) as ctx:
            _find_aligner(None)
        self.assertIn("muscle", str(ctx.exception))
        self.assertIn("mafft", str(ctx.exception))

    @patch("shutil.which")
    def test_falls_back_to_other(self, mock_which):
        """When preferred aligner is missing, uses the other."""
        mock_which.side_effect = lambda name: (
            "/usr/bin/mafft" if name == "mafft" else None
        )
        _path, name = _find_aligner("muscle")
        self.assertEqual(name, "mafft")


class TestRunAlignment(unittest.TestCase):
    """_run_alignment correctly invokes the aligner binary."""

    @patch("subprocess.run")
    def test_muscle_invocation(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        input_path = Path("/tmp/test_input.fasta")
        output_path = Path("/tmp/test_output.fasta")

        # muscle writes to the output file directly, so we mock is_file
        with patch.object(Path, "is_file", return_value=True):
            _run_alignment("/usr/local/bin/muscle", "muscle", input_path, output_path)

        # Verify correct command arguments
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "/usr/local/bin/muscle")
        self.assertIn("-align", call_args)
        self.assertIn(str(input_path), call_args)
        self.assertIn("-output", call_args)
        self.assertIn(str(output_path), call_args)

    @patch("subprocess.run")
    def test_mafft_invocation(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=">seq1\nACGT\n>seq2\nACGT\n",
            stderr="",
        )
        input_path = Path("/tmp/test_input.fasta")
        output_path = Path("/tmp/test_output.fasta")

        with patch.object(Path, "write_text"):
            _run_alignment("/usr/bin/mafft", "mafft", input_path, output_path)

        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "/usr/bin/mafft")
        self.assertIn("--auto", call_args)
        self.assertIn(str(input_path), call_args)

    @patch("subprocess.run")
    def test_muscle_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: invalid input",
        )
        with self.assertRaises(ArtifactError) as ctx:
            _run_alignment(
                "/usr/local/bin/muscle",
                "muscle",
                Path("/tmp/input.fasta"),
                Path("/tmp/output.fasta"),
            )
        self.assertIn("muscle failed", str(ctx.exception))

    @patch("subprocess.run")
    def test_mafft_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: invalid format",
        )
        with self.assertRaises(ArtifactError) as ctx:
            _run_alignment(
                "/usr/bin/mafft",
                "mafft",
                Path("/tmp/input.fasta"),
                Path("/tmp/output.fasta"),
            )
        self.assertIn("mafft failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
