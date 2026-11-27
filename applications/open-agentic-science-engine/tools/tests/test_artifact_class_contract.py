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

"""Systemic checks for the built-in artifact-class contract."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from oase.cli import cli
from oase.commands.cbioportal import ARTIFACT_CLASS as CBIOPORTAL_ARTIFACT_CLASS
from oase.commands.faers import ARTIFACT_CLASS as FAERS_ARTIFACT_CLASS
from oase.core.context import ARTIFACT_DIRS, init_project, normalize_artifact_class

_COMMANDS_DIR = Path(__file__).resolve().parent.parent / "oase" / "commands"


def _artifact_dir_calls() -> list[tuple[str, int, str]]:
    """Return every statically resolvable artifact_dir class argument.

    Built-in output locations must use either a string literal or a module-level
    string constant.  Treating dynamic expressions as failures keeps this test
    comprehensive instead of silently skipping producer paths it cannot audit.
    """
    calls: list[tuple[str, int, str]] = []
    unresolved: list[str] = []

    for path in sorted(_COMMANDS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "artifact_dir"
                and node.args
            ):
                continue
            argument = node.args[0]
            artifact_class: str | None = None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                artifact_class = argument.value
            elif isinstance(argument, ast.Name):
                artifact_class = constants.get(argument.id)

            if artifact_class is None:
                unresolved.append(f"{path.name}:{node.lineno}: {ast.unparse(argument)}")
            else:
                calls.append((path.name, node.lineno, artifact_class))

    assert not unresolved, (
        "artifact_dir() calls must use statically auditable classes:\n  "
        + "\n  ".join(unresolved)
    )
    return calls


def _work_order(artifact_class: str) -> dict[str, Any]:
    return {
        "decision_question": "Can this artifact class be fulfilled?",
        "requested_role": "computational-biologist",
        "stage": "0",
        "cycle": 1,
        "context": {},
        "dependencies": [],
        "capabilities": ["database-search"],
        "deliverables": {
            "layer_0_classes": [artifact_class],
            "layer_1": [],
        },
        "acceptance_criteria": ["Persist the requested evidence"],
        "alert_policy": {},
        "priority": "high",
        "resource_class": "standard",
        "report_to": "science-program-lead",
    }


def _create_from_yaml(tmp_path: Path, artifact_class: str):
    project = init_project(tmp_path / "program")
    spec = project / "work-order.yaml"
    spec.write_text(
        yaml.safe_dump(_work_order(artifact_class), sort_keys=False),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        [
            "--project",
            str(project),
            "workorder",
            "create",
            "--from",
            str(spec),
            "--commit",
        ],
        env={"OASE_NO_DIRTY_WARNING": "1"},
    )
    return project, result


def test_every_builtin_artifact_dir_call_uses_a_registered_class() -> None:
    calls = _artifact_dir_calls()
    assert calls, "no built-in artifact_dir() calls found"

    unregistered = [
        f"{filename}:{line}: {artifact_class}"
        for filename, line, artifact_class in calls
        if normalize_artifact_class(artifact_class) not in ARTIFACT_DIRS
    ]
    assert not unregistered, (
        "built-in artifact_dir() calls use unregistered classes:\n  "
        + "\n  ".join(unregistered)
    )


@pytest.mark.parametrize("schema_label", ["cbioportal-search", "faers-search"])
def test_tool_schema_labels_are_rejected_during_yaml_ingestion(
    tmp_path: Path,
    schema_label: str,
) -> None:
    project, result = _create_from_yaml(tmp_path, schema_label)

    assert result.exit_code != 0
    assert "unknown artifact class" in result.output.lower()
    assert not list((project / ".oase/control/work-orders").glob("*.json"))


@pytest.mark.parametrize(
    "artifact_class",
    [CBIOPORTAL_ARTIFACT_CLASS, FAERS_ARTIFACT_CLASS],
)
def test_builtin_canonical_classes_are_accepted_from_yaml(
    tmp_path: Path,
    artifact_class: str,
) -> None:
    _, result = _create_from_yaml(tmp_path, artifact_class)
    assert result.exit_code == 0, result.output


def test_unknown_custom_class_remains_fail_closed(tmp_path: Path) -> None:
    project, result = _create_from_yaml(tmp_path, "custom-external-result")

    assert result.exit_code != 0
    assert "unknown artifact class" in result.output.lower()
    assert not list((project / ".oase/control/work-orders").glob("*.json"))
