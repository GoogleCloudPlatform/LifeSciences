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

"""`dde env` — inspect, predict and write the environment stamp.

Iterating on the tools environment is cheap; iterating on it *blind* is
not, because every change partitions the artifacts written either side
of it (tool-design-guidance §2, Rule 4). These four subcommands exist so
the partition is visible before it happens, explainable after it
happens, and detectable when the environment has quietly stopped
matching its own stamp.

    dde env plan     what ENV_VERSION would become, and why
    dde env show     what it is now, and whether it still fits
    dde env stamp    write it, archiving the manifest and logging it
    dde env diff     what changed between two stamped versions

`plan` is the nimble half. An environment change you can price before
paying for it is a change you can schedule at a program boundary, which
is the whole of Rule 4's advice.
"""

from __future__ import annotations

import click

from ..common import emitter, output_options
from ..core import envstamp
from ..core.env import tools_home
from ..core.errors import ArtifactError
from ..core.paths import is_safe_to_open, sanitize_slug

#: Diff lines shown on the human path. The rest go to the JSON payload
#: and the archived manifests; a wall of package versions in an agent's
#: context is exactly what §6 exists to prevent.
_DIFF_PREVIEW = 8


@click.group()
def env() -> None:
    """Environment identity: the value stamped into every sidecar."""


def _emit_changes(e, changes: list[str]) -> None:
    for line in envstamp.display(changes[:_DIFF_PREVIEW]):
        e.line(f"  {line}")
    if len(changes) > _DIFF_PREVIEW:
        e.line(f"  … and {len(changes) - _DIFF_PREVIEW} more (use --json for all)")
    e.data("changes", changes)


def _emit_host_requirements(e, home) -> None:
    """Compare source-tree host requirements against the stamped snapshot.

    Silent when they match — no noise on the common path.  When they
    differ, one line names the shape of the change and the JSON payload
    carries the detail (#18).
    """
    current = envstamp.read_host_requirements_source()
    stamped = envstamp.read_host_requirements_stamped(home)

    if current is None:
        # No host-requirements.txt in the source tree — nothing to report.
        return

    if stamped is None:
        # First time, or an older stamp that pre-dates host-requirements
        # tracking.  Report the count, but don't alarm.
        parsed = envstamp.parse_host_requirements(current)
        e.data(
            "host_requirements",
            {
                "count": len(parsed),
                "stamped": False,
                "changed": False,
            },
        )
        return

    delta = envstamp.diff_host_requirements(stamped, current)
    e.data("host_requirements", delta)

    if delta["changed"]:
        added = len(delta["added"])
        removed = len(delta["removed"])
        parts = []
        if added:
            parts.append(f"{added} added")
        if removed:
            parts.append(f"{removed} removed")
        e.line(
            f"Host requirements changed ({', '.join(parts)}). "
            "This affects bootstrap, not the environment itself."
        )


@env.command()
@output_options
def show(as_json: bool, quiet: bool) -> None:
    """Report the current stamp and whether the environment still matches it."""
    home = tools_home()
    state = envstamp.read_state(home)
    e = emitter(as_json, quiet)

    e.data("tools_home", str(home))
    e.data("env_version", state.stamped)
    e.data("live_env_version", state.live_version)
    e.data("provisioned", state.provisioned)
    e.data("drifted", state.drifted)

    if not state.provisioned:
        e.line(f"No stamp at {home}/{envstamp.STAMP}.")
        e.line("Artifacts will record an unpinned developer env_version and say so.")
        e.flush()
        return

    e.line(f"env_version {envstamp.short(state.stamped)}   ({home})")
    tally = envstamp.counts(state.live)
    e.data("counts", tally)
    e.line(f"{tally['packages']} package(s), {tally['binaries']} file(s) in bin/")

    if state.stamp_stale:
        e.line("STAMP STALE: ENV_VERSION does not match the manifest beside it.")
    if state.drifted:
        e.line("DRIFTED: the environment no longer matches its own stamp.")
        e.line("Artifacts written now carry an env_version that misdescribes them.")
        _emit_changes(e, state.changes())
        e.line("Run `dde env stamp` to record the environment as it now is.")
    else:
        e.line("Environment matches its stamp.")

    # Host requirements status (#18).
    host_current = envstamp.read_host_requirements_source()
    if host_current is not None:
        host_parsed = envstamp.parse_host_requirements(host_current)
        host_stamped = envstamp.read_host_requirements_stamped(home)
        if host_stamped is not None:
            host_delta = envstamp.diff_host_requirements(host_stamped, host_current)
            e.data("host_requirements", host_delta)
            if host_delta["changed"]:
                added = len(host_delta["added"])
                removed = len(host_delta["removed"])
                parts = []
                if added:
                    parts.append(f"{added} added")
                if removed:
                    parts.append(f"{removed} removed")
                e.line(f"Host requirements changed since stamp ({', '.join(parts)}).")
            else:
                e.line(f"{len(host_parsed)} host requirement(s) — match stamp.")
        else:
            e.data(
                "host_requirements",
                {
                    "count": len(host_parsed),
                    "stamped": False,
                    "changed": False,
                },
            )
            e.line(
                f"{len(host_parsed)} host requirement(s) declared (no stamp snapshot)."
            )

    log = envstamp.history(home)
    if log:
        when, _version, summary = log[-1]
        e.line(f"last stamped {when}: {summary}")
    e.flush()


@env.command()
@output_options
def plan(as_json: bool, quiet: bool) -> None:
    """Show the ENV_VERSION the current environment would produce.

    Writes nothing. Run it before installing anything: the answer is
    whether the change you are about to make partitions the program's
    artifacts, and that is a scheduling decision, not a build detail.
    """
    home = tools_home()
    state = envstamp.read_state(home)
    e = emitter(as_json, quiet)

    e.data("env_version", state.stamped)
    e.data("would_be", state.live_version)
    e.data("would_change", state.stamped != state.live_version)

    if state.stamped is None:
        e.line(f"unstamped → {envstamp.short(state.live_version)}")
        e.line("First stamp: nothing is partitioned, because nothing was stamped.")
        _emit_host_requirements(e, home)
        e.flush()
        return

    if state.stamped == state.live_version:
        e.line(f"{envstamp.short(state.stamped)} — unchanged. Nothing to stamp.")
        _emit_host_requirements(e, home)
        e.flush()
        return

    e.line(f"{envstamp.short(state.stamped)} → {envstamp.short(state.live_version)}")
    e.line("Stamping this partitions the program: artifacts before and after")
    e.line("carry different env_version values and are not directly comparable.")
    if state.recorded is None:
        # No manifest beside the stamp: the old value was computed some
        # other way, so the difference is real but not itemisable. Say
        # that, rather than printing an empty diff under a warning about
        # a partition and leaving the reader to guess it means "nothing
        # changed".
        e.line("No manifest beside the current stamp, so the change cannot be")
        e.line("itemised — the existing value was produced by an earlier scheme.")
    else:
        _emit_changes(e, state.changes())
    _emit_host_requirements(e, home)
    e.flush()


@env.command()
@click.option(
    "--note", default=None, help="One line recorded in ENV_HISTORY beside the stamp."
)
@output_options
def stamp(note: str | None, as_json: bool, quiet: bool) -> None:
    """Write ENV_VERSION from the environment as it is right now.

    Also writes `env-manifest.txt` (the document actually hashed),
    `requirements.lock`, an archived copy under `manifests/`, and a line
    in `ENV_HISTORY`. The archive is what makes an old `env_version` in
    an old sidecar mean something a year from now.
    """
    home = tools_home()
    if not is_safe_to_open(home):
        raise ArtifactError(f"Refusing to stamp through symlink: {home}")
    result = envstamp.stamp(home, note=note)
    e = emitter(as_json, quiet)
    for key, value in result.items():
        e.data(key, value)
    e.path(result["manifest"], role="manifest")

    if result["changed"]:
        e.line(
            f"{envstamp.short(result['previous'])} → "
            f"{envstamp.short(str(result['env_version']))}"
        )
        _emit_changes(e, list(result["changes"]))
    else:
        e.line(
            f"{envstamp.short(str(result['env_version']))} — unchanged, nothing logged."
        )
    e.flush()


@env.command()
@click.argument("before")
@click.argument("after", default="current")
@output_options
def diff(before: str, after: str, as_json: bool, quiet: bool) -> None:
    """Explain the difference between two stamped environments.

    Takes full `sha256:…` values or unambiguous prefixes, as they appear
    in artifact sidecars; `current` means the live environment. This is
    how a partition gets settled: two results that disagree, produced
    under two env_versions, and a diff naming what actually differed.
    """
    home = tools_home()
    state = envstamp.read_state(home)

    def resolve(token: str) -> str:
        if token == "current":
            return state.live
        safe_token = sanitize_slug(token)
        text = envstamp.archived(home, safe_token)
        if text is None:
            raise ArtifactError(
                f"no archived manifest for {token!r}",
                detail=f"looked in {home / envstamp.ARCHIVE}",
                remedy="pass a full sha256:… value from a sidecar, an unambiguous "
                "prefix of one, or `current`. Environments stamped before "
                "manifests were archived cannot be reconstructed",
            )
        return text

    changes = envstamp.diff_manifests(resolve(before), resolve(after))
    e = emitter(as_json, quiet)
    e.data("before", before)
    e.data("after", after)
    e.data("changes", changes)
    if not changes:
        e.line(f"{before} and {after} describe the same environment.")
    else:
        e.line(f"{before} → {after}: {envstamp.summarize(changes)}")
        _emit_changes(e, changes)
    e.flush()
