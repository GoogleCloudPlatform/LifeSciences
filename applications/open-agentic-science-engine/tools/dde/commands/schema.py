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

"""`dde schema` — schema introspection and template generation.

Subcommands:

  list       List all known schema IDs.
  show       Print a readable field table for a schema.
  template   Generate a skeleton JSON document for a schema.

These commands are generic — they work for all groups, discovering
schemas from the registry rather than being hand-coded per group.
"""

from __future__ import annotations

import json

import click

from ..core.schema_registry import get_schema, list_schemas, make_template, show_table


@click.group()
def schema() -> None:
    """Schema introspection and template generation."""


@schema.command("list")
def list_cmd() -> None:
    """List all known schema IDs.

    Prints one schema ID per line, sorted alphabetically.
    """
    ids = list_schemas()
    if not ids:
        click.echo("No schemas registered.")
        return

    for schema_id in ids:
        s = get_schema(schema_id)
        if s is not None:
            click.echo(f"{schema_id}  —  {s.description}")
        else:
            click.echo(schema_id)


@schema.command("show")
@click.argument("schema_id")
@click.option(
    "--json", "as_json", is_flag=True, help="Emit machine-readable JSON schema dump."
)
def show_cmd(schema_id: str, as_json: bool) -> None:
    """Print a readable field table for a schema.

    Accepts schema IDs like ``dde.pk-study.v1`` or ``dde.tox-repeat-dose.v1``.
    With ``--json``, emits a machine-readable JSON representation.
    """
    s = get_schema(schema_id)
    if s is None:
        known = list_schemas()
        import difflib

        matches = difflib.get_close_matches(schema_id, known, n=3, cutoff=0.4)
        msg = f"Unknown schema: {schema_id!r}"
        if matches:
            msg += f"\n  Did you mean: {', '.join(matches)}"
        msg += "\n  Run 'dde schema list' to see all known schemas."
        raise click.ClickException(msg)

    if as_json:
        # Machine-readable JSON dump
        record = {
            "schema_id": s.schema_id,
            "description": s.description,
            "fields": [
                {
                    "name": f.name,
                    "type": f.type,
                    "required": f.required,
                    **({"enum_values": list(f.enum_values)} if f.enum_values else {}),
                    **({"const": f.const} if f.const is not None else {}),
                    **({"description": f.description} if f.description else {}),
                }
                for f in s.fields
            ],
        }
        click.echo(json.dumps(record, indent=2))
    else:
        click.echo(show_table(schema_id))


@schema.command("template")
@click.argument("schema_id")
def template_cmd(schema_id: str) -> None:
    """Generate a skeleton JSON document for a schema.

    Produces valid JSON with all required fields present using
    placeholder values.  Enum fields use the first accepted value.
    Optional fields are included in a separate ``_optional`` block.

    The output can be edited and fed to ``dde <group> ingest``.
    """
    s = get_schema(schema_id)
    if s is None:
        known = list_schemas()
        import difflib

        matches = difflib.get_close_matches(schema_id, known, n=3, cutoff=0.4)
        msg = f"Unknown schema: {schema_id!r}"
        if matches:
            msg += f"\n  Did you mean: {', '.join(matches)}"
        msg += "\n  Run 'dde schema list' to see all known schemas."
        raise click.ClickException(msg)

    doc = make_template(schema_id)
    click.echo(json.dumps(doc, indent=2))
