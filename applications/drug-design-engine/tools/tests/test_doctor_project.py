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

"""Tests for _check_project() in dde doctor (#111).

Covers the three resolution paths:
  1. DDE_PROJECT env var → resolved from env
  2. .dde/ walk-up from CWD → auto-detected marker
  3. Neither found → failure with detail about what was tried
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dde.commands.doctor import FAIL, OK, Report, _check_project
from dde.common import AppState


class TestCheckProjectFromEnvVar(unittest.TestCase):
    """_check_project reports correctly when DDE_PROJECT is set."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "program"
        self.root.mkdir()
        # Project must be writable and not look like the dde repo
        (self.root / ".dde").mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_reports_ok_with_env_source(self):
        state = AppState()
        report = Report()
        with mock.patch.dict(os.environ, {"DDE_PROJECT": str(self.root)}):
            _check_project(report, state)
        self.assertEqual(len(report.checks), 1)
        check = report.checks[0]
        self.assertEqual(check.name, "project root")
        self.assertEqual(check.status, OK)
        self.assertIn("DDE_PROJECT", check.detail)
        self.assertIn(str(self.root), check.detail)


class TestCheckProjectFromWalkUp(unittest.TestCase):
    """_check_project reports correctly when .dde/ marker is found by walking up."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "program"
        self.root.mkdir()
        (self.root / ".dde").mkdir()
        # Create a child directory to CWD into
        self.child = self.root / "subdir"
        self.child.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_reports_ok_with_walkup_source(self):
        state = AppState()
        report = Report()
        # Remove DDE_PROJECT if set so walk-up is the only path
        env_cleared = {k: v for k, v in os.environ.items() if k != "DDE_PROJECT"}
        with mock.patch.dict(os.environ, env_cleared, clear=True):
            with mock.patch("dde.core.context.Path.cwd", return_value=self.child):
                _check_project(report, state)
        self.assertEqual(len(report.checks), 1)
        check = report.checks[0]
        self.assertEqual(check.name, "project root")
        self.assertEqual(check.status, OK)
        self.assertIn(".dde", check.detail)
        self.assertIn("walk", check.detail.lower())


class TestCheckProjectNotFound(unittest.TestCase):
    """_check_project reports failure with detail when no project root is found."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        # A directory with no .dde/ marker anywhere above it
        self.empty = Path(self._tmpdir.name) / "nowhere"
        self.empty.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_reports_fail_with_detail(self):
        state = AppState()
        report = Report()
        env_cleared = {k: v for k, v in os.environ.items() if k != "DDE_PROJECT"}
        with mock.patch.dict(os.environ, env_cleared, clear=True):
            with mock.patch("dde.core.context.Path.cwd", return_value=self.empty):
                _check_project(report, state)
        self.assertEqual(len(report.checks), 1)
        check = report.checks[0]
        self.assertEqual(check.name, "project root")
        self.assertEqual(check.status, FAIL)
        # The detail must mention what was tried — both .dde/ and DDE_PROJECT
        self.assertIn(".dde", check.detail)
        self.assertIn("DDE_PROJECT", check.detail)
        # Must have a remedy
        self.assertTrue(check.remedy)


if __name__ == "__main__":
    unittest.main()
