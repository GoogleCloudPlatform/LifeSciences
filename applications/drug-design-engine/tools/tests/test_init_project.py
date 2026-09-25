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

"""Tests for init_project(), _write_if_missing(), and related constants (#50).

Covers:
- Fresh init creates all expected directories and skeleton files
- Re-init preserves existing file content (idempotency)
- _write_if_missing does not overwrite existing content
- _write_if_missing raises ProjectRootError when path is a directory
- Init into a dde source repo raises ProjectRootError
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dde.core.context import (
    ARTIFACT_DIRS,
    FINDINGS_SUBDIRS,
    GATE_STAGES,
    PROGRAM_STATE_FILES,
    _write_if_missing,
    init_project,
)
from dde.core.errors import ProjectRootError


class TestInitProjectFresh(unittest.TestCase):
    """Fresh init creates all expected directories and skeleton files."""

    def setUp(self):
        # Use a unique temp dir per test via unittest; pytest tmp_path is
        # also supported but we keep the file compatible with both runners.
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "program"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_creates_project_marker(self):
        init_project(self.root)
        self.assertTrue((self.root / ".dde").is_dir())

    def test_creates_raw_artifact_dirs(self):
        init_project(self.root)
        unique_rels = set(ARTIFACT_DIRS.values())
        for rel in unique_rels:
            self.assertTrue(
                (self.root / rel).is_dir(),
                f"expected raw artifact dir {rel!r} to exist",
            )

    def test_creates_findings_subdirs(self):
        init_project(self.root)
        for subdir in FINDINGS_SUBDIRS:
            self.assertTrue(
                (self.root / "findings" / subdir).is_dir(),
                f"expected findings/{subdir} to exist",
            )

    def test_creates_program_state_files(self):
        init_project(self.root)
        for filename, expected_content in PROGRAM_STATE_FILES.items():
            path = self.root / "program-state" / filename
            self.assertTrue(path.is_file(), f"expected {path} to be a file")
            self.assertEqual(path.read_text(encoding="utf-8"), expected_content)

    def test_creates_gate_stage_dirs(self):
        init_project(self.root)
        for stage in GATE_STAGES:
            self.assertTrue(
                (self.root / "gates" / stage).is_dir(),
                f"expected gates/{stage} to exist",
            )

    def test_creates_executive_summary(self):
        init_project(self.root)
        summary = self.root / "executive" / "program-summary.md"
        self.assertTrue(summary.is_file())
        self.assertIn("Program Summary", summary.read_text(encoding="utf-8"))

    def test_creates_dde_config_skeletons(self):
        init_project(self.root)
        thresholds = self.root / ".dde" / "thresholds.yaml"
        program = self.root / ".dde" / "program.yaml"
        self.assertTrue(thresholds.is_file())
        self.assertTrue(program.is_file())

    def test_returns_resolved_root(self):
        result = init_project(self.root)
        self.assertEqual(result, self.root.resolve())


class TestInitProjectIdempotent(unittest.TestCase):
    """Re-init preserves existing file content (idempotency)."""

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "program"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_reinit_preserves_existing_file_content(self):
        """Running init_project twice should not overwrite files written by
        the first run or by the user between runs."""
        init_project(self.root)

        # Simulate user edits to skeleton files.
        custom = "# My custom decision log\n\nImportant decision here.\n"
        decision_log = self.root / "program-state" / "decision-log.md"
        decision_log.write_text(custom, encoding="utf-8")

        custom_summary = "# Executive Overview\n\nCustomised.\n"
        summary = self.root / "executive" / "program-summary.md"
        summary.write_text(custom_summary, encoding="utf-8")

        # Re-init
        init_project(self.root)

        # User content must be preserved.
        self.assertEqual(decision_log.read_text(encoding="utf-8"), custom)
        self.assertEqual(summary.read_text(encoding="utf-8"), custom_summary)

    def test_reinit_still_creates_all_dirs(self):
        """Re-init must not skip directory creation."""
        init_project(self.root)
        init_project(self.root)

        for subdir in FINDINGS_SUBDIRS:
            self.assertTrue((self.root / "findings" / subdir).is_dir())
        for stage in GATE_STAGES:
            self.assertTrue((self.root / "gates" / stage).is_dir())


class TestWriteIfMissing(unittest.TestCase):
    """Unit tests for the _write_if_missing helper."""

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_writes_new_file(self):
        path = self.tmp / "new.txt"
        _write_if_missing(path, "hello")
        self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_does_not_overwrite_existing(self):
        path = self.tmp / "existing.txt"
        path.write_text("original", encoding="utf-8")
        _write_if_missing(path, "overwritten")
        self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_raises_on_directory_at_path(self):
        path = self.tmp / "oops"
        path.mkdir()
        with self.assertRaises(ProjectRootError) as ctx:
            _write_if_missing(path, "content")
        self.assertIn("expected a file but found a directory", str(ctx.exception))


class TestInitProjectRejectsRepo(unittest.TestCase):
    """Init into a dde source repo raises ProjectRootError."""

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmpdir.name) / "fake-repo"
        self.repo.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_explicit_repo_marker_rejected(self):
        (self.repo / ".dde-repo").touch()
        with self.assertRaises(ProjectRootError) as ctx:
            init_project(self.repo)
        self.assertIn("source repo", str(ctx.exception))

    def test_heuristic_repo_detection_rejected(self):
        """The heuristic backstop also rejects directories that look like
        the dde source repo (docs/tool-design-guidance.md + tools/)."""
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "tool-design-guidance.md").write_text(
            "", encoding="utf-8"
        )
        (self.repo / "tools").mkdir()
        with self.assertRaises(ProjectRootError) as ctx:
            init_project(self.repo)
        self.assertIn("source repo", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
