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

"""`dde artifact` — artifact management commands.

Provides ``dde artifact register`` for assigning provenance sidecars to
files produced outside the DDE tool surface (e.g. files fetched from
external APIs).  The sidecar is machine-generated with
``type: "registration"`` so it is clearly distinguishable from
production sidecars written by DDE tools.

Provides ``dde artifact classes`` for discovering every registered
artifact class, its target directory, and which command group produces
it.  Added as the systemic discoverability fix for issue #131.

This is NOT a science tool and does NOT modify the registered file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    DDEGroup,
    emitter,
    output_options,
    pass_state,
)
from ..core.context import ARTIFACT_DIRS
from ..core.errors import ArtifactError, Refusal
from ..core.paths import is_safe_to_open
from ..core.provenance import Sidecar


@click.group(cls=DDEGroup)
def artifact() -> None:
    """Artifact management commands."""


@artifact.command("register")
@click.argument("paths", nargs=-1, required=True)
@click.option(
    "--source",
    required=True,
    help="Source description (e.g. 'EBI ClustalO via WebFetch').",
)
@click.option(
    "--description", "description", default=None, help="Optional longer description."
)
@output_options
@pass_state
def register_cmd(
    state: AppState,
    paths: tuple[str, ...],
    source: str,
    description: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Register external files with provenance sidecars.

    Writes a ``.meta.json`` sidecar with ``type: "registration"`` for
    each given path.  Refuses to overwrite an existing sidecar.

    The sidecar records the file's SHA-256 digest, size, the provided
    source description, and the current ``$DDE_WORK_ORDER_ID`` and
    ``$SCION_AGENT_SLUG`` (if set).
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    for raw_path in paths:
        file_path = Path(raw_path)
        # Resolve against project root for relative paths.
        if not file_path.is_absolute():
            file_path = project.root / file_path
        file_path = file_path.resolve()

        # Verify file exists.
        if not file_path.is_file():
            raise ArtifactError(
                f"file not found: {raw_path}",
                detail=f"resolved to {file_path}",
                remedy="check the path and try again",
            )

        # Refuse paths outside the project root.
        if not file_path.is_relative_to(project.root.resolve()):
            raise Refusal(
                f"path is outside the project root: {raw_path}",
                detail=f"project root is {project.root}",
                remedy="provide a path within the project directory",
            )

        # Determine the sidecar path.
        sidecar_path = file_path.parent / f"{file_path.name}.meta.json"

        # Refuse to write through a symlink.
        if not is_safe_to_open(sidecar_path):
            raise Refusal(
                f"sidecar path is a symlink: {sidecar_path.name}",
                detail="writing through symlinks is not allowed",
                remedy="remove the symlink and try again",
            )

        # Refuse to overwrite an existing sidecar.
        if sidecar_path.exists():
            raise Refusal(
                f"sidecar already exists: {sidecar_path.name}",
                detail=f"a .meta.json already covers {file_path.name}",
                remedy="remove the existing sidecar first if re-registration is intended",
            )

        # Build the sidecar.
        sc = Sidecar(tool="external-registration", subcommand="register")
        sc.add_output(file_path)
        sc.note("type", "registration")
        sc.note("source", source)
        if description is not None:
            sc.note("description", description)
        sc.note("registered_by", os.environ.get("SCION_AGENT_SLUG") or "unattributed")

        sc.write(sidecar_path)

        rel = str(sidecar_path.relative_to(project.root))
        if as_json:
            emit.data("sidecar", rel)
            emit.data("registered", str(file_path.relative_to(project.root)))
        else:
            emit.line(f"Registered: {rel}")
        emit.path(sidecar_path, role="sidecar")

    emit.flush()


# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------


def _build_class_producer_map() -> dict[str, list[str]]:
    """Map each artifact class to the command module(s) that declare it.

    Scans all ``*.py`` files in ``dde/commands/`` for
    ``ARTIFACT_CLASS = "..."`` declarations.  Returns
    ``{artifact_class: [module_name, ...]}``.
    """
    import ast

    commands_dir = Path(__file__).parent
    result: dict[str, list[str]] = {}
    for py_file in sorted(commands_dir.glob("*.py")):
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
                cls = node.value.value
                result.setdefault(cls, []).append(py_file.stem)
    return result


@artifact.command("classes")
@click.option(
    "--json", "as_json", is_flag=True, help="Emit machine-readable JSON array."
)
@click.option("--quiet", is_flag=True, help="Print class names only, one per line.")
def classes_cmd(as_json: bool, quiet: bool) -> None:
    """List every registered artifact class, its directory, and producer(s).

    Gives agents and specialists a way to discover the valid artifact
    classes without reading source.  Added for issue #131.
    """
    producer_map = _build_class_producer_map()

    rows: list[dict[str, Any]] = []
    for cls in sorted(ARTIFACT_DIRS):
        rows.append(
            {
                "class": cls,
                "directory": ARTIFACT_DIRS[cls],
                "producers": sorted(producer_map.get(cls, [])),
            }
        )

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if quiet:
        for row in rows:
            click.echo(row["class"])
        return

    # Human-readable table.
    cls_w = max(len(r["class"]) for r in rows)
    dir_w = max(len(r["directory"]) for r in rows)
    hdr_cls = "CLASS".ljust(cls_w)
    hdr_dir = "DIRECTORY".ljust(dir_w)
    click.echo(f"  {hdr_cls}  {hdr_dir}  PRODUCERS")
    click.echo(f"  {'─' * cls_w}  {'─' * dir_w}  {'─' * 30}")
    for row in rows:
        c = row["class"].ljust(cls_w)
        d = row["directory"].ljust(dir_w)
        p = ", ".join(row["producers"]) if row["producers"] else "-"
        click.echo(f"  {c}  {d}  {p}")
    click.echo()
    click.echo(f"  {len(rows)} artifact class(es) registered.")
