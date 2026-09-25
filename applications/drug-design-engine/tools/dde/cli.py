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

"""dde CLI entry point.

Routing lives in skill descriptions, not in this help text. Subcommands
are grouped by tool; each tool's phases are separate invocations.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import click

from .commands.admet import admet
from .commands.allen import allen
from .commands.alphafold import alphafold
from .commands.alphagenome import alphagenome
from .commands.analog import analog
from .commands.artifact import artifact
from .commands.assay import assay
from .commands.cbioportal import cbioportal
from .commands.cellxgene import cellxgene
from .commands.cite import cite
from .commands.compound import compound
from .commands.compreg import compreg
from .commands.conservation import conservation
from .commands.coscientist import coscientist
from .commands.dice import dice
from .commands.differentiation import differentiation
from .commands.disco import disco
from .commands.disignatlas import disignatlas
from .commands.docking import docking
from .commands.doctor import doctor
from .commands.dossier import dossier
from .commands.env import env
from .commands.expression import expression
from .commands.faers import faers
from .commands.genetics import genetics
from .commands.geo import geo
from .commands.gtex import gtex
from .commands.gwas import gwas
from .commands.homology import homology
from .commands.hypex import hypex
from .commands.hypothesis import hypothesis
from .commands.litref import litref
from .commands.manufacturing import manufacturing
from .commands.mmp import mmp
from .commands.mpo import mpo
from .commands.patent import patent
from .commands.pathway import pathway
from .commands.phenotype import phenotype
from .commands.pk import pk
from .commands.pocket import pocket
from .commands.ppi import ppi
from .commands.preprint import preprint
from .commands.program import program
from .commands.pubchem import pubchem
from .commands.pubmed import pubmed
from .commands.pubmed_bq import pubmed_bq
from .commands.retro import retro
from .commands.run import run
from .commands.schema import schema
from .commands.scp import scp
from .commands.screen import screen
from .commands.selectivity import selectivity
from .commands.similar import similar
from .commands.site import site
from .commands.spatialdb import spatialdb
from .commands.structure import structure
from .commands.structure_screening import structure_screen
from .commands.tools_cmd import tools_group
from .commands.tox import tox
from .commands.triage import triage
from .commands.trials import trials
from .commands.validate import validate
from .commands.workorder import workorder
from .common import AppState, DDEGroup, enforce_phase_two
from .core import provenance
from .core.context import init_project
from .core.env import CLI_VERSION
from .core.toolchain import check_integrity


@click.group(cls=DDEGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(CLI_VERSION, prog_name="dde")
@click.option(
    "--project",
    "project_override",
    default=None,
    help="Program directory to write artifacts into. Overrides $DDE_PROJECT.",
)
@click.pass_context
def cli(ctx: click.Context, project_override: str | None) -> None:
    """dde — execution surface for dde agent tooling.

    Artifacts land under the program directory, resolved from
    $DDE_PROJECT or a .dde/ marker — never from the current
    working directory.
    """
    ctx.obj = AppState(project_override=project_override)

    # One-time dirty-source warning (#127). Suppressible for legitimate
    # development via DDE_NO_DIRTY_WARNING=1.
    if not os.environ.get("DDE_NO_DIRTY_WARNING"):
        tc = check_integrity()
        if tc.modified:
            n = len(tc.modified_files)
            file_word = "file" if n == 1 else "files"
            print(
                f"Warning: DDE source has uncommitted modifications"
                f" ({n} {file_word}). Run 'dde doctor' for details.",
                file=sys.stderr,
            )


@cli.command()
@click.argument("directory", type=click.Path(file_okay=False))
def init(directory: str) -> None:
    """Create a program directory with full artifact layer structure."""
    root = init_project(directory)
    click.echo(f"Initialised dde program at {root}")
    click.echo(f"  export DDE_PROJECT={root}")


# ---------------------------------------------------------------------------
# Relay emission enumeration — derived mechanically, not hand-maintained
# ---------------------------------------------------------------------------


def _collect_sources(
    group: click.Group,
    prefix: list[str],
    out: list[tuple[str, str, Any]],
    *,
    _seen: set[int] | None = None,
) -> None:
    """Walk the command tree collecting (CLI path, callback source, callback).

    For each leaf command, gets the Python source of its callback via
    ``inspect.getsource``.  Phase-two guards injected by
    ``enforce_phase_two`` are unwrapped so the search runs over the
    original command function, not the wrapper in ``common.py``.

    The callback reference is retained so callers can resolve its module
    for the module-helper search (see ``_enumerate_emission_sites``).

    Commands registered under multiple names (aliases) are visited only
    once; subsequent names pointing to the same ``click.Command`` object
    are skipped so relay-emission counts are not inflated.
    """
    import inspect

    if _seen is None:
        _seen = set()

    for name, cmd in sorted(group.commands.items()):
        cmd_id = id(cmd)
        if cmd_id in _seen:
            continue
        _seen.add(cmd_id)
        path = [*prefix, name]
        if isinstance(cmd, click.Group):
            _collect_sources(cmd, path, out, _seen=_seen)
            continue
        callback = cmd.callback
        if callback is None:
            continue
        # Unwrap the phase-two guard injected by enforce_phase_two.
        # The original callback is captured as a keyword-only default
        # argument on the wrapper (see common.py).
        if getattr(callback, "_phase_two_guarded", False):
            original = (callback.__kwdefaults__ or {}).get("_original")
            if original is not None:
                callback = original
        try:
            text = inspect.getsource(callback)
        except (OSError, TypeError):
            continue
        out.append((" ".join(path), text, callback))


def _module_helpers(mod: Any) -> dict[str, str]:
    """Return ``{name: source}`` for top-level helper functions in *mod*.

    A "helper" is any regular function defined in *mod* (same
    ``__module__``) that is **not** a Click command callback.  Click
    decorators rebind the decorated name to a ``Command`` object, so
    ``inspect.isfunction`` already excludes them.

    Functions imported from other modules are filtered by comparing
    ``__module__`` to the module's own ``__name__``.
    """
    import inspect

    mod_name = mod.__name__
    helpers: dict[str, str] = {}
    for fname, obj in inspect.getmembers(mod, inspect.isfunction):
        if getattr(obj, "__module__", None) != mod_name:
            continue
        try:
            helpers[fname] = inspect.getsource(obj)
        except (OSError, TypeError):
            continue
    return helpers


def _enumerate_emission_sites() -> dict[str, list[str]]:
    """Which subcommand(s) can emit each registered relay code.

    **Pass 1 — direct match (high confidence).**  Walks the registered
    Click command tree and, for each leaf command, searches its
    callback's source for literal occurrences of each ``RELAY_CODES``
    key.  Both ``provenance.relay("code", ...)`` and
    ``sidecar.warn(..., code="code")`` are caught because the search
    matches the code string itself, not a particular calling convention.
    Nested closures (e.g. ``genetics.py``'s ``add_relay``) are covered
    here because they live inside the command function body.

    **Pass 2 — module-helper search.**  For codes still unmatched after
    pass 1, searches module-level helper functions in each command's
    source file.  A code is attributed to a command when:

    1. The code literal appears in a top-level helper in the same module.
    2. The command's own source transitively references that helper by
       name (``helper_name(`` appears in the command source or in the
       source of another helper the command already reaches).

    This catches the ``compound.py`` pattern where ``_build_sidecar``
    is a standalone function called by multiple phase-1 commands,
    without over-attributing: only commands that actually call the
    helper (directly or transitively) are listed.

    Returns ``{code: ["group subcommand", ...]}``.  Every registered
    code appears; those with no emission site found get an empty list.
    """
    import inspect

    sources: list[tuple[str, str, Any]] = []
    _collect_sources(cli, [], sources)

    result: dict[str, list[str]] = {code: [] for code in provenance.RELAY_CODES}

    # --- Pass 1: direct match (existing high-confidence path) ---
    for code in provenance.RELAY_CODES:
        for cli_path, text, _cb in sources:
            if f'"{code}"' in text or f"'{code}'" in text:
                result[code].append(cli_path)

    # --- Pass 2: module-helper search for still-unmatched codes ---
    orphaned = [code for code, sites in result.items() if not sites]
    if not orphaned:
        return result

    # Cache helpers per module so each file is inspected at most once.
    helper_cache: dict[str, dict[str, str]] = {}
    for _cli_path, _src, callback in sources:
        mod = inspect.getmodule(callback)
        if mod is not None and mod.__name__ not in helper_cache:
            helper_cache[mod.__name__] = _module_helpers(mod)

    for code in orphaned:
        for cli_path, cmd_source, callback in sources:
            mod = inspect.getmodule(callback)
            if mod is None:
                continue
            helpers = helper_cache.get(mod.__name__, {})
            if not helpers:
                continue

            # Which helpers in this module contain the relay code?
            code_helpers = {
                hname
                for hname, hsource in helpers.items()
                if f'"{code}"' in hsource or f"'{code}'" in hsource
            }
            if not code_helpers:
                continue

            # Transitive reachability: start with helpers the command
            # calls directly, then expand through helper-to-helper calls.
            reachable: set[str] = {
                hname for hname in helpers if f"{hname}(" in cmd_source
            }
            changed = True
            while changed:
                changed = False
                for rname in list(reachable):
                    rsource = helpers.get(rname, "")
                    for hname in helpers:
                        if hname not in reachable and f"{hname}(" in rsource:
                            reachable.add(hname)
                            changed = True

            # Attribute the code only if a code-bearing helper is reachable.
            if code_helpers & reachable:
                result[code].append(cli_path)

    return result


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable record.")
def relays(as_json: bool) -> None:
    """List mandatory-relay codes and their emission sites.

    A relay code marks a warning that must reach the Layer 1 finding
    unchanged, because a report omitting it is wrong rather than merely
    incomplete. Skills name these codes in their interpretation
    contract; reviewers enumerate `mandatory_relays` in an
    `.analysis.json` and confirm each was addressed.

    Each code's emission site is derived mechanically by searching
    command source for literal code strings — not from a hand-maintained
    list.
    """
    emissions = _enumerate_emission_sites()
    n_codes = len(provenance.RELAY_CODES)
    n_sites = sum(len(sites) for sites in emissions.values())
    orphaned = sorted(code for code, sites in emissions.items() if not sites)

    if as_json:
        records: dict[str, dict] = {}
        for code in sorted(provenance.RELAY_CODES):
            records[code] = {
                "message": provenance.RELAY_CODES[code],
                "emitted_by": sorted(emissions.get(code, [])),
            }
        click.echo(json.dumps(records, indent=2))
        return

    for code, meaning in sorted(provenance.RELAY_CODES.items()):
        sites = emissions.get(code, [])
        click.echo(f"{code}")
        if sites:
            click.echo(f"    emitted by: {', '.join(sorted(sites))}")
        else:
            click.echo("    emitted by: (none found — check registration)")
        click.echo(f"    {meaning}")

    click.echo()
    click.echo(f"{n_codes} codes, {n_sites} emission site(s) found")
    if orphaned:
        click.echo(
            f"  {len(orphaned)} code(s) with no emission site: " + ", ".join(orphaned)
        )


cli.add_command(admet)
cli.add_command(allen)
cli.add_command(artifact)
cli.add_command(cite)
cli.add_command(analog)
cli.add_command(assay)
cli.add_command(cbioportal)
cli.add_command(cellxgene)
cli.add_command(compound)
cli.add_command(compreg)
cli.add_command(conservation)
cli.add_command(doctor)
cli.add_command(env)
cli.add_command(coscientist)
cli.add_command(hypex)
cli.add_command(hypothesis)
cli.add_command(alphafold)
cli.add_command(alphagenome)
cli.add_command(expression)
cli.add_command(faers)
cli.add_command(geo)
cli.add_command(gtex)
cli.add_command(litref)
cli.add_command(preprint)
cli.add_command(pubmed)
cli.add_command(pubmed_bq)
cli.add_command(genetics)
cli.add_command(gwas)
cli.add_command(retro)
cli.add_command(phenotype)
cli.add_command(pathway)
cli.add_command(homology)
cli.add_command(pk)
cli.add_command(pocket)
cli.add_command(ppi)
cli.add_command(pubchem)
cli.add_command(workorder)
cli.add_command(workorder, name="wo")
cli.add_command(run)
cli.add_command(validate)
cli.add_command(program)
cli.add_command(site)
cli.add_command(docking)
cli.add_command(dossier)
cli.add_command(dice)
cli.add_command(selectivity)
cli.add_command(disco)
cli.add_command(disignatlas)
cli.add_command(manufacturing)
cli.add_command(mmp)
cli.add_command(mpo)
cli.add_command(scp)
cli.add_command(screen)
cli.add_command(structure)
cli.add_command(structure_screen)
cli.add_command(spatialdb)
cli.add_command(schema)
cli.add_command(similar)
cli.add_command(tox)
cli.add_command(differentiation)
cli.add_command(patent)
cli.add_command(trials)
cli.add_command(triage)
cli.add_command(tools_group)

# Must follow every add_command: the walk guards what is registered at
# the time it runs, so a command added after this line would be missed.
enforce_phase_two(cli)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
