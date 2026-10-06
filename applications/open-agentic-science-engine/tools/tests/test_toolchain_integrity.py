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

"""Tests for toolchain integrity detection (#127).

Covers:
  - Sidecar includes cli_integrity field
  - Dirty detection sets cli_modified: true + note
  - Clean source does not set cli_modified
  - Non-git install returns "installed"
  - Doctor check reports modified files
  - CLI startup warning on dirty source
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from dde.commands.doctor import OK, WARN, Report, _check_toolchain_integrity
from dde.core import toolchain
from dde.core.provenance import Sidecar


class _ResetCacheMixin:
    """Clear the toolchain session cache before and after each test."""

    def setUp(self):
        super().setUp()
        toolchain.reset_cache()

    def tearDown(self):
        toolchain.reset_cache()
        super().tearDown()


# ---------------------------------------------------------------------------
# toolchain module unit tests
# ---------------------------------------------------------------------------


class TestCheckIntegrityClean(_ResetCacheMixin, unittest.TestCase):
    """check_integrity() when running from a clean git checkout."""

    @mock.patch("dde.core.toolchain._git_modified_files", return_value=[])
    @mock.patch("dde.core.toolchain._git_describe", return_value="v0.3.0")
    @mock.patch("dde.core.toolchain._find_git_root", return_value=Path("/fake/repo"))
    def test_clean(self, _root, _describe, _files):
        state = toolchain.check_integrity()
        self.assertEqual(state.integrity, "v0.3.0")
        self.assertFalse(state.modified)
        self.assertEqual(state.modified_files, [])

    @mock.patch("dde.core.toolchain._git_modified_files", return_value=[])
    @mock.patch("dde.core.toolchain._git_describe", return_value="v0.3.0-12-gabcdef1")
    @mock.patch("dde.core.toolchain._find_git_root", return_value=Path("/fake/repo"))
    def test_ahead_not_dirty(self, _root, _describe, _files):
        state = toolchain.check_integrity()
        self.assertEqual(state.integrity, "v0.3.0-12-gabcdef1")
        self.assertFalse(state.modified)
        self.assertEqual(state.modified_files, [])


class TestCheckIntegrityDirty(_ResetCacheMixin, unittest.TestCase):
    """check_integrity() when running from a dirty git checkout."""

    @mock.patch(
        "dde.core.toolchain._git_modified_files",
        return_value=["core/provenance.py", "commands/doctor.py"],
    )
    @mock.patch("dde.core.toolchain._git_describe", return_value="v0.3.0-dirty")
    @mock.patch("dde.core.toolchain._find_git_root", return_value=Path("/fake/repo"))
    def test_dirty(self, _root, _describe, _files):
        state = toolchain.check_integrity()
        self.assertEqual(state.integrity, "v0.3.0-dirty")
        self.assertTrue(state.modified)
        self.assertEqual(len(state.modified_files), 2)
        self.assertIn("core/provenance.py", state.modified_files)


class TestCheckIntegrityInstalled(_ResetCacheMixin, unittest.TestCase):
    """check_integrity() when running from an installed package (no .git)."""

    @mock.patch("dde.core.toolchain._find_git_root", return_value=None)
    def test_installed(self, _root):
        state = toolchain.check_integrity()
        self.assertEqual(state.integrity, "installed")
        self.assertFalse(state.modified)
        self.assertEqual(state.modified_files, [])


class TestCheckIntegrityUnknown(_ResetCacheMixin, unittest.TestCase):
    """check_integrity() when git is present but describe fails."""

    @mock.patch("dde.core.toolchain._git_modified_files", return_value=[])
    @mock.patch("dde.core.toolchain._git_describe", return_value="unknown")
    @mock.patch("dde.core.toolchain._find_git_root", return_value=Path("/fake/repo"))
    def test_unknown(self, _root, _describe, _files):
        state = toolchain.check_integrity()
        self.assertEqual(state.integrity, "unknown")
        self.assertFalse(state.modified)


class TestSessionCache(_ResetCacheMixin, unittest.TestCase):
    """check_integrity() caches its result for the session."""

    @mock.patch("dde.core.toolchain._git_modified_files", return_value=[])
    @mock.patch("dde.core.toolchain._git_describe", return_value="v0.3.0")
    @mock.patch("dde.core.toolchain._find_git_root", return_value=Path("/fake/repo"))
    def test_cached(self, mock_root, _describe, _files):
        first = toolchain.check_integrity()
        second = toolchain.check_integrity()
        self.assertIs(first, second)
        # _find_git_root should have been called only once
        mock_root.assert_called_once()


# ---------------------------------------------------------------------------
# Sidecar integration
# ---------------------------------------------------------------------------


class TestSidecarIntegrity(_ResetCacheMixin, unittest.TestCase):
    """Sidecar.to_dict() includes cli_integrity and cli_modified fields."""

    @mock.patch(
        "dde.core.provenance.check_integrity",
        return_value=toolchain.ToolchainState("v0.3.0", False, []),
    )
    def test_clean_sidecar(self, _mock):
        sc = Sidecar(tool="test", subcommand="fetch")
        d = sc.to_dict()
        self.assertEqual(d["cli_integrity"], "v0.3.0")
        self.assertNotIn("cli_modified", d)
        self.assertNotIn("cli_modified_note", d)

    @mock.patch(
        "dde.core.provenance.check_integrity",
        return_value=toolchain.ToolchainState(
            "v0.3.0-dirty", True, ["core/provenance.py"]
        ),
    )
    def test_dirty_sidecar(self, _mock):
        sc = Sidecar(tool="test", subcommand="fetch")
        d = sc.to_dict()
        self.assertEqual(d["cli_integrity"], "v0.3.0-dirty")
        self.assertTrue(d["cli_modified"])
        self.assertIn("uncommitted modifications", d["cli_modified_note"])

    @mock.patch(
        "dde.core.provenance.check_integrity",
        return_value=toolchain.ToolchainState("installed", False, []),
    )
    def test_installed_sidecar(self, _mock):
        sc = Sidecar(tool="test", subcommand="fetch")
        d = sc.to_dict()
        self.assertEqual(d["cli_integrity"], "installed")
        self.assertNotIn("cli_modified", d)


# ---------------------------------------------------------------------------
# Doctor check
# ---------------------------------------------------------------------------


class TestDoctorToolchainClean(_ResetCacheMixin, unittest.TestCase):
    """Doctor check reports OK on clean source."""

    @mock.patch(
        "dde.commands.doctor.check_integrity",
        return_value=toolchain.ToolchainState("v0.3.0", False, []),
    )
    def test_clean(self, _mock):
        report = Report()
        _check_toolchain_integrity(report)
        self.assertEqual(len(report.checks), 1)
        check = report.checks[0]
        self.assertEqual(check.name, "toolchain integrity")
        self.assertEqual(check.status, OK)
        self.assertIn("clean", check.detail)


class TestDoctorToolchainDirty(_ResetCacheMixin, unittest.TestCase):
    """Doctor check reports WARN with modified files on dirty source."""

    @mock.patch(
        "dde.commands.doctor.check_integrity",
        return_value=toolchain.ToolchainState(
            "v0.3.0-dirty",
            True,
            ["core/provenance.py", "commands/doctor.py", "cli.py"],
        ),
    )
    def test_dirty(self, _mock):
        report = Report()
        _check_toolchain_integrity(report)
        self.assertEqual(len(report.checks), 1)
        check = report.checks[0]
        self.assertEqual(check.name, "toolchain integrity")
        self.assertEqual(check.status, WARN)
        self.assertIn("uncommitted modifications", check.detail)
        self.assertIn("core/provenance.py", check.detail)
        self.assertIn("cli_modified", check.remedy)


class TestDoctorToolchainInstalled(_ResetCacheMixin, unittest.TestCase):
    """Doctor check reports OK for installed package."""

    @mock.patch(
        "dde.commands.doctor.check_integrity",
        return_value=toolchain.ToolchainState("installed", False, []),
    )
    def test_installed(self, _mock):
        report = Report()
        _check_toolchain_integrity(report)
        self.assertEqual(len(report.checks), 1)
        check = report.checks[0]
        self.assertEqual(check.name, "toolchain integrity")
        self.assertEqual(check.status, OK)
        self.assertIn("installed package", check.detail)


class TestDoctorToolchainUnknown(_ResetCacheMixin, unittest.TestCase):
    """Doctor check reports WARN when git is unavailable."""

    @mock.patch(
        "dde.commands.doctor.check_integrity",
        return_value=toolchain.ToolchainState("unknown", False, []),
    )
    def test_unknown(self, _mock):
        report = Report()
        _check_toolchain_integrity(report)
        self.assertEqual(len(report.checks), 1)
        check = report.checks[0]
        self.assertEqual(check.name, "toolchain integrity")
        self.assertEqual(check.status, WARN)
        self.assertIn("could not determine", check.detail)


# ---------------------------------------------------------------------------
# CLI startup warning
# ---------------------------------------------------------------------------


class TestCLIDirtyWarning(_ResetCacheMixin, unittest.TestCase):
    """CLI emits stderr warning on dirty source."""

    @mock.patch(
        "dde.cli.check_integrity",
        return_value=toolchain.ToolchainState(
            "v0.3.0-dirty", True, ["a.py", "b.py", "c.py"]
        ),
    )
    def test_warning_emitted(self, _mock):
        """The cli group callback prints a warning to stderr on dirty source."""
        from click.testing import CliRunner

        from dde.cli import cli

        runner = CliRunner(mix_stderr=False)
        env = {k: v for k, v in os.environ.items() if k != "DDE_NO_DIRTY_WARNING"}
        result = runner.invoke(cli, ["--version"], env=env)
        self.assertIn("uncommitted modifications", result.stderr)
        self.assertIn("3 files", result.stderr)

    @mock.patch(
        "dde.cli.check_integrity",
        return_value=toolchain.ToolchainState("v0.3.0-dirty", True, ["a.py"]),
    )
    def test_warning_suppressed(self, _mock):
        """DDE_NO_DIRTY_WARNING=1 suppresses the stderr warning."""
        from click.testing import CliRunner

        from dde.cli import cli

        runner = CliRunner(mix_stderr=False)
        env = dict(os.environ)
        env["DDE_NO_DIRTY_WARNING"] = "1"
        result = runner.invoke(cli, ["--version"], env=env)
        self.assertNotIn("uncommitted modifications", result.stderr)

    @mock.patch(
        "dde.cli.check_integrity",
        return_value=toolchain.ToolchainState("v0.3.0", False, []),
    )
    def test_no_warning_when_clean(self, _mock):
        """No warning when source is clean."""
        from click.testing import CliRunner

        from dde.cli import cli

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["--version"])
        self.assertNotIn("uncommitted modifications", result.stderr)


if __name__ == "__main__":
    unittest.main()
