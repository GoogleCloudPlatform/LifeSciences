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

"""CLI option conformance tests (#134).

Asserts that semantically identical options appear consistently across
command groups:

1. Phase-1 artifact producers must have --overwrite.
2. Commands that write artifacts must have --out.
3. Specific fixes verified:
   - --overwrite is on admet predict
   - compound analyze works with --name only (no SMILES)
   - docking analyze accepts multiple paths (nargs=-1)
"""

from __future__ import annotations

import os
import unittest

import click

# Suppress dirty-source warnings during test import.
os.environ["DDE_NO_DIRTY_WARNING"] = "1"

from dde.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_command(group: click.Group, *names: str) -> click.Command | None:
    """Walk the command tree to find a nested subcommand by name chain."""
    current: click.Command | click.Group = group
    for name in names:
        if not isinstance(current, click.Group):
            return None
        current = current.commands.get(name)  # type: ignore[assignment]
        if current is None:
            return None
    return current


def _param_names(cmd: click.Command) -> set[str]:
    """Return the set of parameter names (long option stems) on *cmd*.

    For options like ``--overwrite``, the name is ``overwrite``.
    For arguments like ``SMILES``, the name is ``smiles``.
    """
    return {p.name for p in cmd.params if p.name}


def _has_option(cmd: click.Command, option_name: str) -> bool:
    """Return True if *cmd* has an option whose name matches *option_name*."""
    return option_name in _param_names(cmd)


# ---------------------------------------------------------------------------
# Phase-1 artifact producers that must have --overwrite
# ---------------------------------------------------------------------------

#: Phase-1 commands that write artifacts and must carry --overwrite.
#: This list is the source of truth for the conformance assertion.
_PHASE1_PRODUCERS: list[tuple[str, ...]] = [
    ("compound", "validate"),
    ("compound", "descriptors"),
    ("compound", "alerts"),
    ("compound", "sa-score"),
    ("compound", "profile"),
    ("admet", "predict"),
]

#: All artifact-writing commands must carry --out.
_ARTIFACT_WRITERS: list[tuple[str, ...]] = [
    ("compound", "validate"),
    ("compound", "descriptors"),
    ("compound", "alerts"),
    ("compound", "sa-score"),
    ("compound", "profile"),
    ("admet", "predict"),
    ("admet", "predict-batch"),
    ("admet", "analyze"),
    ("compound", "analyze"),
    ("docking", "prepare"),
    ("docking", "run"),
    ("docking", "analyze"),
    ("docking", "contacts"),
    ("docking", "contacts-matrix"),
]


# ---------------------------------------------------------------------------
# Conformance: --overwrite on phase-1 producers
# ---------------------------------------------------------------------------


class TestOverwriteConformance(unittest.TestCase):
    """Every phase-1 artifact producer must have --overwrite."""

    def test_phase1_producers_have_overwrite(self):
        for path in _PHASE1_PRODUCERS:
            with self.subTest(command=path):
                cmd = _find_command(cli, *path)
                self.assertIsNotNone(cmd, f"command {' '.join(path)} not found")
                self.assertTrue(
                    _has_option(cmd, "overwrite"),
                    f"{' '.join(path)} is missing --overwrite",
                )


# ---------------------------------------------------------------------------
# Conformance: --out on artifact writers
# ---------------------------------------------------------------------------


class TestOutConformance(unittest.TestCase):
    """Every artifact-writing command must have --out."""

    def test_artifact_writers_have_out(self):
        for path in _ARTIFACT_WRITERS:
            with self.subTest(command=path):
                cmd = _find_command(cli, *path)
                self.assertIsNotNone(cmd, f"command {' '.join(path)} not found")
                self.assertTrue(
                    _has_option(cmd, "out"),
                    f"{' '.join(path)} is missing --out",
                )


# ---------------------------------------------------------------------------
# Fix 1: --overwrite on admet predict
# ---------------------------------------------------------------------------


class TestAdmetPredictOverwrite(unittest.TestCase):
    """admet predict must have --overwrite (#134 fix 1)."""

    def test_admet_predict_has_overwrite(self):
        cmd = _find_command(cli, "admet", "predict")
        self.assertIsNotNone(cmd, "admet predict not found")
        self.assertTrue(
            _has_option(cmd, "overwrite"),
            "admet predict is missing --overwrite",
        )


# ---------------------------------------------------------------------------
# Fix 2: compound analyze accepts --name without SMILES
# ---------------------------------------------------------------------------


class TestCompoundAnalyzeSmiles(unittest.TestCase):
    """compound analyze must accept --name without a positional SMILES."""

    def test_smiles_is_optional(self):
        cmd = _find_command(cli, "compound", "analyze")
        self.assertIsNotNone(cmd, "compound analyze not found")
        smiles_param = None
        for p in cmd.params:
            if p.name == "smiles":
                smiles_param = p
                break
        self.assertIsNotNone(smiles_param, "compound analyze has no smiles param")
        self.assertFalse(
            smiles_param.required,
            "compound analyze SMILES should not be required (--name can substitute)",
        )

    def test_smiles_positional_still_accepted(self):
        """SMILES can still be passed as a positional argument."""
        cmd = _find_command(cli, "compound", "analyze")
        self.assertIsNotNone(cmd, "compound analyze not found")
        smiles_param = None
        for p in cmd.params:
            if p.name == "smiles":
                smiles_param = p
                break
        self.assertIsNotNone(smiles_param)
        self.assertIsInstance(
            smiles_param,
            click.Argument,
            "smiles should still be a positional argument",
        )


# ---------------------------------------------------------------------------
# Fix 3: docking analyze accepts multiple paths
# ---------------------------------------------------------------------------


class TestDockingAnalyzeVariadic(unittest.TestCase):
    """docking analyze must accept multiple paths (nargs=-1)."""

    def test_paths_is_variadic(self):
        cmd = _find_command(cli, "docking", "analyze")
        self.assertIsNotNone(cmd, "docking analyze not found")
        paths_param = None
        for p in cmd.params:
            if p.name == "paths":
                paths_param = p
                break
        self.assertIsNotNone(
            paths_param,
            "docking analyze should have a 'paths' parameter (variadic)",
        )
        self.assertEqual(
            paths_param.nargs,
            -1,
            "docking analyze paths should accept variable number of args",
        )


if __name__ == "__main__":
    unittest.main()
