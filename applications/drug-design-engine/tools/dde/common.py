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

"""Shared click plumbing: state, error rendering, common options."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from .core import thresholds as thresholds_mod
from .core.context import ProjectContext, resolve_project
from .core.errors import ArtifactError, DDEError
from .core.output import Emitter


@dataclass
class AppState:
    """Per-invocation state carried on the click context."""

    project_override: str | None = None
    _project: ProjectContext | None = None

    def project(self) -> ProjectContext:
        """Resolve the project root once per invocation."""
        if self._project is None:
            self._project = resolve_project(self.project_override)
        return self._project


pass_state = click.make_pass_decorator(AppState, ensure=True)


class DDEGroup(click.Group):
    """Group that renders DDEError cleanly and uses its exit code.

    Failures print a reason and exit non-zero. Nothing is synthesized on
    any failure path.
    """

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except DDEError as exc:
            click.echo(exc.render(), err=True)
            sys.exit(exc.exit_code)


#: The phase-2 subcommand name. The two-phase contract names this
#: command specifically (tool-design-guidance §3), so keying the guard on
#: the name is not a heuristic — it is the contract, and a phase-2
#: command called something else is already outside it.
ANALYZE = "analyze"


def is_phase_two(name: str) -> bool:
    """Is this subcommand name a phase-2 command?

    `analyze`, and also `analyze-<something>` — a tool with two kinds of
    phase-1 output needs two analysers, and `analyze-prediction` and
    `analyze-ism` are both squarely phase 2. Matching only the exact name
    left them unguarded, which is the same defect the guard was written
    to remove: a rule that holds for the commands someone remembered.
    """
    return (
        name == ANALYZE
        or name.startswith(f"{ANALYZE}-")
        or name == "assess"
        or name.startswith("assess-")
    )


#: Injected into every phase-2 command by the root walk, rather than
#: written on each one. An `--overwrite` a command author has to remember
#: to add is missing from exactly the command whose author did not think
#: about the second opinion.
OVERWRITE_OPTION = click.Option(
    ["--overwrite"],
    is_flag=True,
    help="Replace an existing analysis that disagrees with this one. "
    "Without it, a conflicting write is refused (exit 9).",
)

OVERWRITE_CROSS_WO_OPTION = click.Option(
    ["--overwrite-cross-wo"],
    is_flag=True,
    help="Allow overwriting an analysis attributed to a different work order. "
    "Without it, cross-work-order overwrite is refused even with --overwrite.",
)


def enforce_phase_two(group: click.Group) -> None:
    """Apply the phase-2 contract to every `analyze` command in the tree.

    Two properties, both structural, both applied here rather than at the
    call sites they constrain:

    * **Offline.** The shared HTTP client is latched for the rest of the
      invocation, so a lookup added to a phase-2 command raises instead of
      quietly making re-analysis depend on an endpoint.
    * **Non-destructive.** `--overwrite` is injected, and without it a
      write that would replace a *differing* analysis is refused. A second
      opinion has to be producible without destroying the first, and the
      directory scheme cannot promise that on its own: two reviewers on
      one date resolve to one path.

    Applied once to the root group, so a tool added next month inherits
    both without its author knowing they exist. A per-command decorator
    would need to be remembered by exactly the person who forgot the rule.

    Walks the registered tree rather than inspecting argv: `--out
    analyze` is a plausible argument and must not trip this, while
    `dde expression analyze` must.
    """
    from .core import http, provenance

    for name, command in group.commands.items():
        if isinstance(command, click.Group):
            enforce_phase_two(command)
            continue
        if not is_phase_two(name):
            continue
        original = command.callback
        if original is None or getattr(original, "_phase_two_guarded", False):
            continue

        if not any(p.name == "overwrite" for p in command.params):
            command.params.append(OVERWRITE_OPTION)
        if not any(p.name == "overwrite_cross_wo" for p in command.params):
            command.params.append(OVERWRITE_CROSS_WO_OPTION)

        def guarded(
            *args: Any, _original=original, _group=group.name, _name=name, **kwargs: Any
        ):
            # Injected by this wrapper, so consumed by it: the wrapped
            # callback never declared the parameter.
            provenance.allow_overwrite(bool(kwargs.pop("overwrite", False)))
            provenance.allow_overwrite_cross_wo(
                bool(kwargs.pop("overwrite_cross_wo", False))
            )
            http.forbid_network(
                f"`{_group} {_name}` is phase 2: it reads what phase 1 wrote "
                "and never queries an endpoint"
            )
            return _original(*args, **kwargs)

        guarded._phase_two_guarded = True  # type: ignore[attr-defined]
        command.callback = guarded


def output_options(func: Callable) -> Callable:
    """--json and --quiet, present on every subcommand (§6)."""
    func = click.option(
        "--json", "as_json", is_flag=True, help="Emit a machine-readable JSON record."
    )(func)
    func = click.option("--quiet", is_flag=True, help="Emit output paths only.")(func)
    return func


def out_option(func: Callable) -> Callable:
    """--out override for the artifact-class default directory (§4)."""
    return click.option(
        "--out",
        default=None,
        help="Override the default output directory (relative paths resolve "
        "against the project root, never CWD).",
    )(func)


def name_option(func: Callable) -> Callable:
    """--name: human-readable compound name alias for filenames and metadata.

    When provided, a slugified version of the name is used instead of the
    SMILES-derived slug for output filenames.  The original name is also
    recorded in the provenance sidecar under ``compound_name``.  When
    omitted, behavior is identical to the default (SMILES-derived slug).
    """
    return click.option(
        "--name",
        default=None,
        help="Human-readable compound name alias for filenames and metadata.",
    )(func)


def from_option(func: Callable) -> Callable:
    """--from: where phase 2 READS its inputs, decoupled from --out.

    `analyze` used one directory for both, which made an independent
    re-run impossible: re-running to check a verdict overwrote the very
    artifact being checked, and the reviewer then compared their output
    with itself. `--out` alone could not help, because redirecting the
    output also redirected the lookup and the input was no longer found.

    So a reviewer regenerates a verdict without touching the evidence:

        dde genetics analyze TP53 --out review/2026-08-18

    Defaults to the artifact-class directory, which is where phase 1
    writes. Nothing is searched for and nothing falls back — an input
    directory that does not hold the artifact is an error, not a cue to
    look somewhere else.
    """
    return click.option(
        "--from",
        "from_dir",
        default=None,
        help="Read phase-1 artifacts from here instead of the default "
        "directory (relative paths resolve against the project root).",
    )(func)


def beside_or_out(
    state: AppState, source: Path, filename: str, out: str | None
) -> Path:
    """Destination for an `analyze` that took its input as a path.

    Where the input is named explicitly there is nothing for `--from` to
    do, but the destructive half of the problem is still there: writing
    the verdict beside the artifact it read means a second opinion
    overwrites the first one, and the reviewer then compares their own
    output with itself.

    So the default stays beside the source — that is where phase 1 put
    it and where every existing skill looks — and `--out` moves only the
    verdict, never the lookup. The input is a path either way, so the
    two cannot be confused as they were on the identifier-based
    commands.
    """
    if out is None:
        return source.parent / filename
    override = Path(out)
    target = override if override.is_absolute() else state.project().root / override
    target.mkdir(parents=True, exist_ok=True)
    return target / filename


def emitter(as_json: bool, quiet: bool) -> Emitter:
    return Emitter(as_json=as_json, quiet=quiet)


def resolve_artifact(state: AppState, path: str, what: str = "artifact") -> Path:
    """Resolve an input artifact path against the project root, not CWD.

    Agents are unreliable about working directory (§4), so a relative
    path means "relative to the program directory". A bare path that
    exists in CWD but not under the project root is still an error —
    silently reading the wrong file is worse than failing.
    """
    candidate = Path(path)
    resolved = (
        candidate if candidate.is_absolute() else state.project().root / candidate
    )
    if not resolved.is_file():
        raise ArtifactError(
            f"{what} not found: {resolved}",
            detail=f"relative paths resolve against the project root "
            f"({state.project().root}), never the current directory",
            remedy="run the corresponding phase-1 subcommand first, or pass an absolute path",
        )
    return resolved


def load_thresholds(
    state: AppState, name: str, overrides: dict[str, Any] | None = None
) -> thresholds_mod.ThresholdSet:
    return thresholds_mod.load(name, state.project().root, overrides)
