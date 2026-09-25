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

"""Regression tests for ARTIFACT_DIRS registration and lookup.

Issue #83: ARTIFACT_CLASS values declared in command modules must be
    registered in ARTIFACT_DIRS — a missing entry causes 100% failure
    on the default path.

Issue #85: Work orders use ``dde.*``-prefixed class names.  The lookup
    must normalize the prefix, and an unknown class must raise rather
    than silently returning None.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dde.core.context import (
    ARTIFACT_DIRS,
    ProjectContext,
    normalize_artifact_class,
    resolve_artifact_subdir,
)
from dde.core.errors import SchemaError

# ---------------------------------------------------------------------------
# Locate the commands directory relative to this test file.
# ---------------------------------------------------------------------------

_COMMANDS_DIR = Path(__file__).resolve().parent.parent / "dde" / "commands"


def _collect_artifact_classes() -> list[tuple[str, str]]:
    """Return ``[(filename, artifact_class), ...]`` from all command modules."""
    results: list[tuple[str, str]] = []
    for py_file in sorted(_COMMANDS_DIR.glob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        for m in re.finditer(r'^ARTIFACT_CLASS\s*=\s*["\']([^"\']+)["\']', text, re.M):
            results.append((py_file.name, m.group(1)))
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAllArtifactClassesRegistered:
    """Every ARTIFACT_CLASS declared in a command module must exist in
    ARTIFACT_DIRS.  This is the "never again" guard for issue #83.
    """

    @pytest.fixture()
    def declared_classes(self) -> list[tuple[str, str]]:
        return _collect_artifact_classes()

    def test_commands_dir_exists(self) -> None:
        assert _COMMANDS_DIR.is_dir(), f"commands dir not found: {_COMMANDS_DIR}"

    def test_at_least_one_class_found(
        self, declared_classes: list[tuple[str, str]]
    ) -> None:
        assert len(declared_classes) > 0, "no ARTIFACT_CLASS declarations found"

    def test_all_classes_registered(
        self, declared_classes: list[tuple[str, str]]
    ) -> None:
        unregistered: list[str] = []
        for filename, cls in declared_classes:
            if cls not in ARTIFACT_DIRS:
                unregistered.append(f"{filename}: {cls}")
        assert not unregistered, (
            "ARTIFACT_CLASS values not registered in ARTIFACT_DIRS:\n"
            + "\n".join(f"  {u}" for u in unregistered)
        )


class TestDdePrefixNormalization:
    """``dde.*`` prefixed class names must resolve the same as unprefixed."""

    def test_normalize_strips_prefix(self) -> None:
        assert normalize_artifact_class("dde.genetics") == "genetics"

    def test_normalize_leaves_unprefixed(self) -> None:
        assert normalize_artifact_class("genetics") == "genetics"

    def test_normalize_only_strips_leading_dde(self) -> None:
        # "xdde.genetics" should NOT be stripped — only leading "dde."
        assert normalize_artifact_class("xdde.genetics") == "xdde.genetics"

    def test_resolve_subdir_with_prefix(self) -> None:
        """``resolve_artifact_subdir("dde.genetics")`` returns the same
        path as ``resolve_artifact_subdir("genetics")``."""
        assert resolve_artifact_subdir("dde.genetics") == resolve_artifact_subdir(
            "genetics"
        )

    def test_artifact_dir_with_prefix(self, tmp_path: Path) -> None:
        """``artifact_dir("dde.genetics")`` returns the same path as
        ``artifact_dir("genetics")``."""
        ctx = ProjectContext(root=tmp_path, source="test")
        prefixed = ctx.artifact_dir("dde.genetics")
        unprefixed = ctx.artifact_dir("genetics")
        assert prefixed == unprefixed

    def test_all_known_classes_resolve_with_prefix(self) -> None:
        """Every entry in ARTIFACT_DIRS resolves when prefixed with ``dde.``."""
        for cls in ARTIFACT_DIRS:
            prefixed = f"dde.{cls}"
            assert resolve_artifact_subdir(prefixed) == ARTIFACT_DIRS[cls], (
                f"dde.{cls} did not resolve to {ARTIFACT_DIRS[cls]}"
            )


class TestUnknownClassRaises:
    """An unknown artifact class must raise SchemaError, not return None."""

    def test_resolve_subdir_raises(self) -> None:
        with pytest.raises(SchemaError, match="unknown artifact class"):
            resolve_artifact_subdir("nonexistent")

    def test_resolve_subdir_raises_with_prefix(self) -> None:
        with pytest.raises(SchemaError, match="unknown artifact class"):
            resolve_artifact_subdir("dde.nonexistent")

    def test_artifact_dir_raises(self, tmp_path: Path) -> None:
        ctx = ProjectContext(root=tmp_path, source="test")
        with pytest.raises(SchemaError, match="unknown artifact class"):
            ctx.artifact_dir("nonexistent")

    def test_artifact_dir_raises_with_prefix(self, tmp_path: Path) -> None:
        ctx = ProjectContext(root=tmp_path, source="test")
        with pytest.raises(SchemaError, match="unknown artifact class"):
            ctx.artifact_dir("dde.nonexistent")
