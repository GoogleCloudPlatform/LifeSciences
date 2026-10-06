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

"""Tests for compound phase-1 artifact overwrite protection (#96).

Covers:
- New file: written normally
- Existing identical file: skipped with notice
- Existing different file: Refusal raised with SHA-256 details
- Existing different file with --overwrite: written
- Overwrite on identical file: written (flag bypasses skip)
- Refusal message includes the full artifact path
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from dde.commands.compound import _safe_write_artifact
from dde.core.errors import Refusal


class TestSafeWriteArtifactNew(unittest.TestCase):
    """A new file is written normally regardless of --overwrite."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_writes_new_file(self):
        path = self.tmp / "test.json"
        content = '{"key": "value"}\n'
        result = _safe_write_artifact(path, content, overwrite=False)
        self.assertTrue(result)
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_writes_new_file_with_overwrite(self):
        path = self.tmp / "test.json"
        content = '{"key": "value"}\n'
        result = _safe_write_artifact(path, content, overwrite=True)
        self.assertTrue(result)
        self.assertEqual(path.read_text(encoding="utf-8"), content)


class TestSafeWriteArtifactIdentical(unittest.TestCase):
    """An existing file with identical content is skipped."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_skips_identical_content(self):
        path = self.tmp / "test.json"
        content = '{"key": "value"}\n'
        path.write_text(content, encoding="utf-8")
        result = _safe_write_artifact(path, content, overwrite=False)
        self.assertFalse(result)
        # File is unchanged.
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_overwrite_replaces_identical(self):
        """--overwrite writes even when content is identical."""
        path = self.tmp / "test.json"
        content = '{"key": "value"}\n'
        path.write_text(content, encoding="utf-8")
        result = _safe_write_artifact(path, content, overwrite=True)
        self.assertTrue(result)


class TestSafeWriteArtifactDifferent(unittest.TestCase):
    """An existing file with different content is refused or overwritten."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_refuses_different_content(self):
        path = self.tmp / "test.json"
        old_content = '{"key": "old_value"}\n'
        new_content = '{"key": "new_value"}\n'
        path.write_text(old_content, encoding="utf-8")

        with self.assertRaises(Refusal) as ctx:
            _safe_write_artifact(path, new_content, overwrite=False)

        exc = ctx.exception
        self.assertIn("already exists with different content", exc.message)
        # SHA-256 hashes must appear in the detail.
        old_hash = hashlib.sha256(old_content.encode("utf-8")).hexdigest()
        new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        self.assertIn(old_hash, exc.detail)
        self.assertIn(new_hash, exc.detail)
        # Remedy must mention --overwrite and --out.
        self.assertIn("--overwrite", exc.remedy)
        self.assertIn("--out", exc.remedy)

    def test_overwrite_replaces_different_content(self):
        path = self.tmp / "test.json"
        old_content = '{"key": "old_value"}\n'
        new_content = '{"key": "new_value"}\n'
        path.write_text(old_content, encoding="utf-8")

        result = _safe_write_artifact(path, new_content, overwrite=True)
        self.assertTrue(result)
        self.assertEqual(path.read_text(encoding="utf-8"), new_content)

    def test_refusal_message_includes_path(self):
        path = self.tmp / "mol.descriptors.json"
        path.write_text("old", encoding="utf-8")
        with self.assertRaises(Refusal) as ctx:
            _safe_write_artifact(path, "new", overwrite=False)
        self.assertIn(str(path), ctx.exception.message)

    def test_refuses_with_exit_code_9(self):
        """Refusal carries exit code 9 (not a retry-worthy failure)."""
        path = self.tmp / "test.json"
        path.write_text("old", encoding="utf-8")
        with self.assertRaises(Refusal) as ctx:
            _safe_write_artifact(path, "new", overwrite=False)
        self.assertEqual(ctx.exception.exit_code, 9)

    def test_original_file_preserved_on_refusal(self):
        """The original file must not be modified when the write is refused."""
        path = self.tmp / "test.json"
        old_content = '{"key": "old_value"}\n'
        path.write_text(old_content, encoding="utf-8")
        with self.assertRaises(Refusal):
            _safe_write_artifact(path, '{"key": "new"}', overwrite=False)
        self.assertEqual(path.read_text(encoding="utf-8"), old_content)


if __name__ == "__main__":
    unittest.main()
