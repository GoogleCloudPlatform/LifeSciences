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

"""Tests for analysis source field normalization (#129).

Covers:
- Bare filename is normalized to project-relative path
- Absolute path is made relative to project root
- Path escaping project root raises ArtifactError
- Citation check reports attempted resolution path in error message
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dde.core.errors import ArtifactError
from dde.core.provenance import _normalize_source


class TestNormalizeSourceBareFilename(unittest.TestCase):
    """A bare filename is resolved via ARTIFACT_DIRS to a project-relative path."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        # Create a standard artifact directory with a file in it.
        assay_dir = self.project_root / "raw" / "assays"
        assay_dir.mkdir(parents=True)
        (assay_dir / "compound.selectivity.json").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_bare_filename_normalized(self) -> None:
        """A bare filename found in ARTIFACT_DIRS is stored with its relative path."""
        with self._patch_project_root():
            result = _normalize_source("compound.selectivity.json")
        self.assertEqual(result, "raw/assays/compound.selectivity.json")

    def test_bare_filename_as_path_object(self) -> None:
        """A bare filename passed as a Path object is also normalized."""
        with self._patch_project_root():
            result = _normalize_source(Path("compound.selectivity.json"))
        self.assertEqual(result, "raw/assays/compound.selectivity.json")

    def test_bare_filename_not_found_returned_as_is(self) -> None:
        """A bare filename not found in any artifact dir is returned unchanged."""
        with self._patch_project_root():
            result = _normalize_source("nonexistent.json")
        self.assertEqual(result, "nonexistent.json")

    def test_non_path_string_returned_as_is(self) -> None:
        """A non-path reference (accession, query) passes through unchanged."""
        with self._patch_project_root():
            result = _normalize_source("BRCA1 pain neuropathic")
        self.assertEqual(result, "BRCA1 pain neuropathic")

    def _patch_project_root(self):
        """Patch _get_project_root to return our temp project root."""
        return patch(
            "dde.core.provenance._get_project_root",
            return_value=self.project_root.resolve(),
        )


class TestNormalizeSourceAbsolutePath(unittest.TestCase):
    """An absolute path is converted to a project-relative string."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        tox_dir = self.project_root / "raw" / "tox"
        tox_dir.mkdir(parents=True)
        (tox_dir / "genotox.json").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_absolute_path_made_relative(self) -> None:
        """An absolute path under the project root is stored as relative."""
        abs_path = self.project_root / "raw" / "tox" / "genotox.json"
        with self._patch_project_root():
            result = _normalize_source(str(abs_path))
        self.assertEqual(result, "raw/tox/genotox.json")

    def test_absolute_path_object_made_relative(self) -> None:
        """A Path object with an absolute path is stored as relative."""
        abs_path = self.project_root / "raw" / "tox" / "genotox.json"
        with self._patch_project_root():
            result = _normalize_source(abs_path)
        self.assertEqual(result, "raw/tox/genotox.json")

    def _patch_project_root(self):
        return patch(
            "dde.core.provenance._get_project_root",
            return_value=self.project_root.resolve(),
        )


class TestNormalizeSourceEscapesProjectRoot(unittest.TestCase):
    """Paths that escape the project root raise ArtifactError."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name) / "project"
        self.project_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_absolute_path_outside_project_raises(self) -> None:
        """An absolute path outside the project root raises ArtifactError."""
        outside = "/etc/passwd"
        with self._patch_project_root():
            with self.assertRaises(ArtifactError):
                _normalize_source(outside)

    def test_relative_traversal_raises(self) -> None:
        """A relative path with '..' that escapes the project raises ArtifactError."""
        with self._patch_project_root():
            with self.assertRaises(ArtifactError):
                _normalize_source("../../etc/passwd")

    def _patch_project_root(self):
        return patch(
            "dde.core.provenance._get_project_root",
            return_value=self.project_root.resolve(),
        )


class TestNormalizeSourceNoProjectRoot(unittest.TestCase):
    """When no project root is available, source passes through unchanged."""

    def test_returns_as_is_when_no_project(self) -> None:
        with patch("dde.core.provenance._get_project_root", return_value=None):
            result = _normalize_source("compound.selectivity.json")
        self.assertEqual(result, "compound.selectivity.json")


class TestNormalizeSourceAlreadyRelative(unittest.TestCase):
    """A project-relative path with directory components passes through."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        assay_dir = self.project_root / "raw" / "assays"
        assay_dir.mkdir(parents=True)
        (assay_dir / "compound.selectivity.json").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_relative_path_preserved(self) -> None:
        """An already-relative path is returned unchanged."""
        with self._patch_project_root():
            result = _normalize_source("raw/assays/compound.selectivity.json")
        self.assertEqual(result, "raw/assays/compound.selectivity.json")

    def _patch_project_root(self):
        return patch(
            "dde.core.provenance._get_project_root",
            return_value=self.project_root.resolve(),
        )


class TestCitationCheckErrorMessage(unittest.TestCase):
    """The citation check error message includes the attempted resolution path."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        # Create artifact dirs but no actual source file.
        assay_dir = self.project_root / "raw" / "assays"
        assay_dir.mkdir(parents=True)
        # Create an analysis file that cites a bare filename (the bug).
        analysis = {
            "source": "compound.selectivity.json",
            "threshold_set": "test",
        }
        (assay_dir / "compound.analysis.json").write_text(
            json.dumps(analysis), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_error_includes_resolution_path(self) -> None:
        """When a source path fails to resolve, the error includes the attempted path."""
        from dde.commands.validate import _check_analysis_citations

        deliverables = {"layer_0_classes": ["assays"]}
        result = _check_analysis_citations(self.project_root, deliverables)
        self.assertEqual(result["status"], "fail")
        issues = result["detail"]["issues"]
        # Find the issue about the unresolved source.
        source_issues = [
            i for i in issues if "compound.selectivity.json" in i.get("issue", "")
        ]
        self.assertTrue(len(source_issues) > 0, "Expected an issue about the source")
        issue_text = source_issues[0]["issue"]
        # The error should mention the attempted resolution path.
        self.assertIn("resolved to", issue_text)
        self.assertIn("file not found", issue_text)
        self.assertIn("project-relative path", issue_text)


if __name__ == "__main__":
    unittest.main()
