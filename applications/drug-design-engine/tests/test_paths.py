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

"""Tests for dde.core.paths — path confinement and slug sanitization.

Covers:
  - confine_path: traversal prevention, null bytes, symlink loops, edge cases
  - sanitize_slug: safe filenames, GENCODE-style dots, length limits, empties
  - is_safe_to_open: symlink guards
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.paths import confine_path, is_safe_to_open, sanitize_slug

# ---------------------------------------------------------------------------
# confine_path
# ---------------------------------------------------------------------------


class TestConfinePath:
    """Tests for confine_path()."""

    def test_normal_path_within_base(self, tmp_path: Path) -> None:
        """A simple child path resolves inside the base directory."""
        child = tmp_path / "subdir" / "file.txt"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.touch()

        result = confine_path(tmp_path, Path("subdir/file.txt"))
        assert result is not None
        assert result == child.resolve()

    def test_traversal_escapes_base(self, tmp_path: Path) -> None:
        """A path containing ../ that escapes base_dir returns None."""
        result = confine_path(tmp_path, Path("../../etc/passwd"))
        assert result is None

    def test_absolute_path_outside_base(self, tmp_path: Path) -> None:
        """An absolute path outside base_dir returns None."""
        result = confine_path(tmp_path, Path("/etc/passwd"))
        assert result is None

    def test_resolves_to_base_dir_itself(self, tmp_path: Path) -> None:
        """A path that resolves to base_dir itself is allowed (edge case)."""
        result = confine_path(tmp_path, Path("."))
        assert result is not None
        assert result == tmp_path.resolve()

    def test_embedded_null_byte(self, tmp_path: Path) -> None:
        """A path with an embedded null byte returns None (ValueError)."""
        result = confine_path(tmp_path, Path("evil\x00file"))
        assert result is None

    def test_nonexistent_child_still_confined(self, tmp_path: Path) -> None:
        """A path that doesn't exist yet but is within base_dir succeeds."""
        result = confine_path(tmp_path, Path("future/child.txt"))
        assert result is not None
        assert result.is_relative_to(tmp_path.resolve())


# ---------------------------------------------------------------------------
# sanitize_slug
# ---------------------------------------------------------------------------


class TestSanitizeSlug:
    """Tests for sanitize_slug()."""

    def test_alphanumeric_unchanged(self) -> None:
        """A simple alphanumeric string passes through unchanged."""
        assert sanitize_slug("hello123") == "hello123"

    def test_path_separators_sanitized(self) -> None:
        """Path traversal characters are replaced with hyphens."""
        result = sanitize_slug("../../../etc/passwd")
        # Dots are kept (needed for GENCODE IDs), slashes become hyphens.
        # The result is harmless as a filename — no directory separators.
        assert "/" not in result
        assert "\\" not in result
        assert result == "..-..-..-etc-passwd"

    def test_gencode_id_preserved(self) -> None:
        """GENCODE-style IDs with dots are preserved."""
        assert sanitize_slug("ENSG00000141510.16") == "ENSG00000141510.16"

    def test_empty_after_sanitization_raises(self) -> None:
        """A string that becomes empty after sanitization raises ValueError."""
        with pytest.raises(ValueError, match="slug is empty"):
            sanitize_slug("///")

    def test_max_length_truncation(self) -> None:
        """A slug exceeding max_length is truncated."""
        long_text = "a" * 100
        result = sanitize_slug(long_text, max_length=20)
        assert len(result) <= 20

    def test_leading_trailing_special_chars_stripped(self) -> None:
        """Leading and trailing hyphens (from special chars) are stripped."""
        result = sanitize_slug("---hello---")
        assert result == "hello"

    def test_consecutive_special_chars_collapsed(self) -> None:
        """Consecutive special characters collapse to a single hyphen."""
        result = sanitize_slug("foo   bar!!!baz")
        assert result == "foo-bar-baz"

    def test_custom_max_length(self) -> None:
        """Custom max_length is respected."""
        result = sanitize_slug("abcdefghij", max_length=5)
        assert result == "abcde"

    def test_trailing_hyphen_after_truncation(self) -> None:
        """Trailing hyphens introduced by truncation are stripped."""
        # "abc-def" truncated at 4 → "abc-" → "abc"
        result = sanitize_slug("abc def", max_length=4)
        assert result == "abc"
        assert not result.endswith("-")

    def test_dot_only_raises(self) -> None:
        """A single dot is a filesystem-special name and must be rejected."""
        with pytest.raises(ValueError, match="filesystem-special"):
            sanitize_slug(".")

    def test_dotdot_raises(self) -> None:
        """Double-dot is a filesystem-special name and must be rejected."""
        with pytest.raises(ValueError, match="filesystem-special"):
            sanitize_slug("..")

    def test_disguised_dotdot_raises(self) -> None:
        """Input that collapses to '..' after sanitization must be rejected."""
        with pytest.raises(ValueError, match="filesystem-special"):
            sanitize_slug("-..-")


# ---------------------------------------------------------------------------
# is_safe_to_open
# ---------------------------------------------------------------------------


class TestIsSafeToOpen:
    """Tests for is_safe_to_open()."""

    def test_regular_file(self, tmp_path: Path) -> None:
        """A regular file is safe to open."""
        f = tmp_path / "regular.txt"
        f.touch()
        assert is_safe_to_open(f) is True

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlink is rejected by default."""
        target = tmp_path / "target.txt"
        target.touch()
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert is_safe_to_open(link) is False

    def test_symlink_allowed_when_flagged(self, tmp_path: Path) -> None:
        """A symlink is accepted when allow_symlinks=True."""
        target = tmp_path / "target.txt"
        target.touch()
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert is_safe_to_open(link, allow_symlinks=True) is True

    def test_nonexistent_path_is_safe(self, tmp_path: Path) -> None:
        """A non-existent path is considered safe (it's not a symlink)."""
        assert is_safe_to_open(tmp_path / "no-such-file") is True
