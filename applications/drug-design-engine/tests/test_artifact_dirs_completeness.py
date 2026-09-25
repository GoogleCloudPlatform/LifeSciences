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

"""Systemic guard: every ARTIFACT_CLASS in dde/commands/ must be registered.

This is the test that prevents a fourth recurrence of issues #83, #85,
and #131.  Each time, a command module declared an ARTIFACT_CLASS that
was not present in ARTIFACT_DIRS, causing _find_layer0_artifacts() to
return empty and citation checking to raise 'unknown artifact class'.

The guard works by scanning every *.py file in dde/commands/ at test
time, extracting ARTIFACT_CLASS assignments via the AST, and asserting
each value is resolvable through normalize_artifact_class + ARTIFACT_DIRS.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Ensure the tools package is importable.
_TOOLS_ROOT = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from dde.core.context import ARTIFACT_DIRS, normalize_artifact_class

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMMANDS_DIR = _TOOLS_ROOT / "dde" / "commands"


def _discover_artifact_classes() -> list[tuple[str, str]]:
    """Parse every command module and return [(filename, artifact_class), ...]."""
    results: list[tuple[str, str]] = []
    for py_file in sorted(_COMMANDS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "ARTIFACT_CLASS"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                results.append((py_file.name, node.value.value))
    return results


# ---------------------------------------------------------------------------
# Guard test — the one that prevents a fourth recurrence
# ---------------------------------------------------------------------------


class TestArtifactDirsCompleteness:
    """Every ARTIFACT_CLASS declared in a command module must be registered."""

    @pytest.fixture(scope="class")
    def discovered(self) -> list[tuple[str, str]]:
        return _discover_artifact_classes()

    def test_at_least_one_class_discovered(
        self, discovered: list[tuple[str, str]]
    ) -> None:
        """Sanity check: the scanner must find at least one declaration."""
        assert discovered, (
            "No ARTIFACT_CLASS declarations found in dde/commands/ — "
            "the scanner is broken, not the registry"
        )

    def test_all_artifact_classes_registered(
        self, discovered: list[tuple[str, str]]
    ) -> None:
        """Every ARTIFACT_CLASS value must resolve via ARTIFACT_DIRS.

        This is the systemic guard for issues #83, #85, #131.  A failure
        here means a command module declares an artifact class that
        _find_layer0_artifacts() cannot look up, causing citation
        checking to silently return empty or raise 'unknown artifact
        class'.

        Fix: add the missing class to ARTIFACT_DIRS in
        dde/core/context.py.
        """
        unregistered: list[tuple[str, str]] = []
        for filename, cls in discovered:
            normalized = normalize_artifact_class(cls)
            if normalized not in ARTIFACT_DIRS:
                unregistered.append((filename, cls))

        assert not unregistered, (
            "ARTIFACT_CLASS value(s) not registered in ARTIFACT_DIRS "
            "(this is the bug from #83/#85/#131):\n"
            + "\n".join(
                f"  {filename}: ARTIFACT_CLASS = {cls!r}"
                for filename, cls in unregistered
            )
            + "\n\nFix: add the missing class(es) to ARTIFACT_DIRS in "
            "dde/core/context.py"
        )


# ---------------------------------------------------------------------------
# normalize_artifact_class tests
# ---------------------------------------------------------------------------


class TestNormalizeArtifactClass:
    """normalize_artifact_class strips the dde. prefix correctly."""

    def test_strips_dde_prefix(self) -> None:
        assert normalize_artifact_class("dde.genetics") == "genetics"

    def test_strips_dde_prefix_compounds(self) -> None:
        assert normalize_artifact_class("dde.compounds") == "compounds"

    def test_no_prefix_unchanged(self) -> None:
        assert normalize_artifact_class("genomics") == "genomics"

    def test_known_aliases_resolve(self) -> None:
        """All known aliases (with dde. prefix) must resolve."""
        for cls in ARTIFACT_DIRS:
            prefixed = f"dde.{cls}"
            normalized = normalize_artifact_class(prefixed)
            assert normalized in ARTIFACT_DIRS, (
                f"normalize_artifact_class({prefixed!r}) = {normalized!r} "
                f"which is not in ARTIFACT_DIRS"
            )


# ---------------------------------------------------------------------------
# Unknown class produces distinct finding (not empty result)
# ---------------------------------------------------------------------------


class TestUnknownClassFinding:
    """An unknown artifact class must return a distinct finding, not empty."""

    def test_is_known_artifact_class_true_for_registered(self) -> None:
        from dde.commands.validate import _is_known_artifact_class

        assert _is_known_artifact_class("genomics") is True
        assert _is_known_artifact_class("compounds") is True
        assert _is_known_artifact_class("dde.genomics") is True

    def test_is_known_artifact_class_false_for_unknown(self) -> None:
        from dde.commands.validate import _is_known_artifact_class

        assert _is_known_artifact_class("nonexistent_class_xyz") is False

    def test_find_layer0_returns_empty_for_unknown(self) -> None:
        """_find_layer0_artifacts returns [] for unknown classes.

        Callers must use _is_known_artifact_class to distinguish
        'unknown class' (toolchain bug) from 'no artifacts found'
        (real scientific finding).
        """
        import tempfile

        from dde.commands.validate import _find_layer0_artifacts

        with tempfile.TemporaryDirectory() as tmpdir:
            result = _find_layer0_artifacts(Path(tmpdir), "nonexistent_class_xyz")
            assert result == []

    def test_check_deliverables_exist_unknown_class_finding(self) -> None:
        """deliverables_exist must return kind=unknown_artifact_class for
        unregistered classes, not kind=COMPLETENESS with 'no artifacts found'.
        """
        import tempfile

        from dde.commands.validate import _check_deliverables_exist

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            deliverables = {
                "layer_0_classes": ["nonexistent_class_xyz"],
            }
            result = _check_deliverables_exist(root, deliverables)
            assert result["result"] == "fail"
            assert result["kind"] == "unknown_artifact_class", (
                f"Expected kind='unknown_artifact_class', got {result['kind']!r}. "
                "Unknown classes must produce a distinct finding, not be "
                "confused with 'no artifacts found'."
            )
            detail = result["detail"]
            assert "unknown_artifact_classes" in detail
            assert any(
                "nonexistent_class_xyz" in entry["class"]
                for entry in detail["unknown_artifact_classes"]
            )
