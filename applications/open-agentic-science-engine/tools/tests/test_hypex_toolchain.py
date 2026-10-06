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

"""Doctor contract for DDE's provisioned Hypex toolchain."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from dde.commands.doctor import (
    _CAPABILITY_VALIDATED,
    _HELP_MARKERS,
    _PROVISIONED_BINARIES,
    CAPABILITY,
    OK,
    WARN,
    Report,
    _check_binaries,
    _check_hypothesis_strategies,
    get_capability_snapshot,
)

HYPEX_TOOLS = ("hypex", "elo", "prox")


class TestHypexDeclaration(unittest.TestCase):
    def test_complete_toolchain_is_provisioned(self):
        for name in HYPEX_TOOLS:
            self.assertIn(name, _PROVISIONED_BINARIES)
            self.assertEqual(len(_PROVISIONED_BINARIES[name]), 2)
            self.assertIn(name, _CAPABILITY_VALIDATED)

    def test_missing_tools_have_actionable_remedies(self):
        for name in HYPEX_TOOLS:
            remedy = _PROVISIONED_BINARIES[name][1]
            self.assertIn("install.sh", remedy)
            self.assertNotIn("not yet published", remedy)


class TestHypexDoctorChecks(unittest.TestCase):
    def test_missing_toolchain_is_capability_warning(self):
        report = Report()
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch(
                "dde.commands.doctor.env.tools_home",
                return_value=Path("/definitely/missing/dde-tools"),
            ),
        ):
            _check_binaries(report)
            _check_hypothesis_strategies(report)

        checks = {check.name: check for check in report.checks}
        for name in HYPEX_TOOLS:
            check = checks[f"binary {name}"]
            self.assertEqual(check.status, WARN)
            self.assertEqual(check.kind, CAPABILITY)
        strategy = checks["hypothesis strategy: hypex"]
        self.assertEqual(strategy.status, WARN)
        self.assertEqual(strategy.kind, CAPABILITY)
        self.assertIn("hypex, elo, prox", strategy.detail)

    def test_complete_runnable_toolchain_enables_strategy(self):
        report = Report()

        def run(command, **_kwargs):
            tool = Path(command[0]).name
            return subprocess.CompletedProcess(
                command,
                0,
                _HELP_MARKERS[tool],
                "",
            )

        with (
            mock.patch(
                "dde.commands.doctor.shutil.which",
                side_effect=lambda name: (
                    f"/tools/{name}" if name in HYPEX_TOOLS else None
                ),
            ),
            mock.patch("dde.commands.doctor.subprocess.run", side_effect=run),
        ):
            _check_hypothesis_strategies(report)

        strategy = next(
            check
            for check in report.checks
            if check.name == "hypothesis strategy: hypex"
        )
        self.assertEqual(strategy.status, OK)

    def test_non_runnable_prox_disables_strategy(self):
        report = Report()

        def run(command, **_kwargs):
            tool = Path(command[0]).name
            return subprocess.CompletedProcess(
                command,
                1 if tool == "prox" else 0,
                _HELP_MARKERS[tool],
                "error",
            )

        with (
            mock.patch(
                "dde.commands.doctor.shutil.which",
                side_effect=lambda name: (
                    f"/tools/{name}" if name in HYPEX_TOOLS else None
                ),
            ),
            mock.patch("dde.commands.doctor.subprocess.run", side_effect=run),
        ):
            _check_hypothesis_strategies(report)

        strategy = next(
            check
            for check in report.checks
            if check.name == "hypothesis strategy: hypex"
        )
        self.assertEqual(strategy.status, WARN)
        self.assertIn("prox", strategy.detail)

    def test_noop_prox_help_disables_strategy(self):
        report = Report()

        def run(command, **_kwargs):
            tool = Path(command[0]).name
            output = "" if tool == "prox" else _HELP_MARKERS[tool]
            return subprocess.CompletedProcess(command, 0, output, "")

        with (
            mock.patch(
                "dde.commands.doctor.shutil.which",
                side_effect=lambda name: (
                    f"/tools/{name}" if name in HYPEX_TOOLS else None
                ),
            ),
            mock.patch("dde.commands.doctor.subprocess.run", side_effect=run),
        ):
            _check_hypothesis_strategies(report)

        strategy = next(
            check
            for check in report.checks
            if check.name == "hypothesis strategy: hypex"
        )
        self.assertEqual(strategy.status, WARN)
        self.assertIn("prox", strategy.detail)


class TestHypexCapabilitySnapshot(unittest.TestCase):
    def test_missing_toolchain_is_unavailable_not_unreleased(self):
        with (
            mock.patch("dde.commands.doctor.shutil.which", return_value=None),
            mock.patch(
                "dde.commands.doctor.env.tools_home",
                return_value=Path("/definitely/missing/dde-tools"),
            ),
        ):
            snapshot = get_capability_snapshot()

        for name in HYPEX_TOOLS:
            self.assertEqual(snapshot[name], "unavailable")


if __name__ == "__main__":
    unittest.main()
