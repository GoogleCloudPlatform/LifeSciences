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

"""`dde tools` — discover available command groups and their capabilities.

Answers the question "what can I run right now?" by introspecting the CLI
command tree.  Each registered command group is listed with its purpose
(from the module docstring), available subcommands, and artifact class
(when the module defines one).

This command exists because `dde --help` lists 55+ groups but does not
surface per-group purpose or subcommand inventory.  `dde doctor` checks
environment health but is organised by capability, not by command.
`dde tools list` fills the gap: a single command that inventories every
registered tool.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import click


def _tool_info(name: str, cmd: click.Group, ctx: click.Context) -> dict[str, Any]:
    """Build an info dict for a single command group."""
    purpose = (cmd.help or cmd.short_help or "").split("\n")[0].strip()
    subcommands = (
        sorted(cmd.list_commands(ctx)) if hasattr(cmd, "list_commands") else []
    )

    # Try to find ARTIFACT_CLASS from the module that defined this group.
    artifact_class: str | None = None
    callback = cmd.callback
    if callback is not None:
        mod_name = getattr(callback, "__module__", None)
        if mod_name:
            try:
                mod = importlib.import_module(mod_name)
                artifact_class = getattr(mod, "ARTIFACT_CLASS", None)
            except Exception:
                pass

    # Fallback: if the group itself has no callback (common for Click
    # groups built with @click.group without a body), inspect the parent
    # package.  Command modules in dde follow the pattern
    # dde.commands.<name>, so we can try importing that.
    if artifact_class is None:
        try:
            mod = importlib.import_module(f"dde.commands.{name}")
            artifact_class = getattr(mod, "ARTIFACT_CLASS", None)
        except Exception:
            pass

    return {
        "name": name,
        "purpose": purpose,
        "subcommands": subcommands,
        "artifact_class": artifact_class,
    }


# ---------------------------------------------------------------------------
# The ``tools`` group and its ``list`` subcommand
# ---------------------------------------------------------------------------


# Named ``tools_group`` to avoid shadowing the ``tools/`` directory on
# disk (the brief warns about this).
@click.group("tools")
def tools_group() -> None:
    """Discover available dde command groups and capabilities."""


@tools_group.command("list")
@click.option(
    "--json", "as_json", is_flag=True, help="Emit machine-readable JSON array."
)
@click.option("--quiet", is_flag=True, help="Print group names only, one per line.")
@click.option(
    "--domain",
    default=None,
    help="Filter groups whose purpose contains DOMAIN (case-insensitive).",
)
@click.pass_context
def list_tools(
    ctx: click.Context, as_json: bool, quiet: bool, domain: str | None
) -> None:
    """List every registered command group with purpose and subcommands."""
    # Walk up to the root CLI group.
    root = ctx
    while root.parent is not None:
        root = root.parent
    cli_group: click.Group = root.command  # type: ignore[assignment]

    rows: list[dict[str, Any]] = []
    for name in sorted(cli_group.list_commands(root)):
        cmd = cli_group.get_command(root, name)
        if cmd is None or not isinstance(cmd, click.Group):
            continue
        # Skip ourselves — ``tools`` is infrastructure, not a science tool.
        if name == "tools":
            continue
        info = _tool_info(name, cmd, root)
        if domain and domain.lower() not in info["purpose"].lower():
            continue
        rows.append(info)

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if quiet:
        for row in rows:
            click.echo(row["name"])
        return

    # Human-readable table.
    if not rows:
        click.echo("No matching tool groups found.")
        return

    # Compute column widths.
    name_w = max(len(r["name"]) for r in rows)
    class_w = max(len(r["artifact_class"] or "-") for r in rows)
    hdr_name = "NAME".ljust(name_w)
    hdr_class = "CLASS".ljust(class_w)
    click.echo(f"  {hdr_name}  {hdr_class}  PURPOSE")
    click.echo(f"  {'─' * name_w}  {'─' * class_w}  {'─' * 40}")
    for row in rows:
        n = row["name"].ljust(name_w)
        c = (row["artifact_class"] or "-").ljust(class_w)
        p = row["purpose"][:72]
        click.echo(f"  {n}  {c}  {p}")
    click.echo()
    click.echo(f"  {len(rows)} tool group(s) found.")
    click.echo("  Run `dde <group> --help` for subcommands and options.")
