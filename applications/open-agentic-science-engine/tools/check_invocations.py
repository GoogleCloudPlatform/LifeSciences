#!/usr/bin/env python3
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

"""Check every `dde …` command written in the repo against the CLI that exists.

A document that describes code is a cache of that code. This is the
cache-invalidation check: it reads the fenced code blocks and inline code spans
in `docs/`, `skills/`, `templates/` and `README.md`, and asks the tree click
actually built whether each command and option resolves.

Deliberately ignorant of phases, thresholds, relays and layers. An instrument
aimed at the rule it is testing tends to confirm what its author expected; this
one knows nothing about the rules it will catch violations of, which is why it
can catch them. Candidates come from code blocks and code spans only — a
sentence mentioning a command is not an instruction to run one.

Placeholders (`dde <tool> analyze`) are skipped: they are not claims about
the surface. Everything else either resolves or is reported.

**This gate cannot run in a plain container, and that is permanent.** The
other three checkers here can: plan-review took click out of
check_threshold_names by parsing the RELAY_CODES literal instead of
importing the module that owns it. That move is not available to this
one and must not be attempted. Its subject *is* the tree click builds,
so re-deriving that tree from source would make this checker a second
cache of the very thing it exists to invalidate — the fault named in the
first line of this docstring. Exit 2 without the venv is the honest
answer here, not a gap to close.

Usage:  PYTHONPATH=tools python3 tools/check_invocations.py
Exits 1 if anything is wrong, so it can gate a commit. Exit 2 means it
could not run at all.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

# Exit 2 means "could not check", distinct from 1 "checked and found
# something". Without the split, an absent venv makes this gate red for
# a reason that has nothing to do with its subject, and a gate that goes
# red for unrelated reasons is one people learn to wave through — at
# which point it protects nothing. Coverage reporting stops a vacuous
# pass; this stops a vacuous failure. Credit to template-builder, who
# hit it first in check_artifact_paths.py.
try:
    import click

    from dde.cli import cli
except Exception as exc:  # pragma: no cover - environment, not logic
    # Not ImportError. A missing dependency raises that; a half-installed
    # one raises whatever the broken module raises on the way up —
    # plan-review's click stub produced AttributeError from common.py.
    # Narrowing to ImportError sends exactly that case out as exit 1,
    # "checked and found a problem", which is the conflation exit 2
    # exists to prevent.
    sys.stderr.write(
        f"cannot check: {exc}\n"
        "This checker imports the CLI it validates against, so it needs the "
        "tools environment. Run `source <tools-home>/env.sh` first, or "
        "`tools/install.sh` if the venv is absent.\n"
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("docs", "skills", "templates", "README.md")

#: Command groups the design documents specify but the CLI does not yet
#: provide. They are written down deliberately — the orchestration guidance
#: is normative for commands that do not exist, and the ROC template tells its
#: agent in as many words that they are missing and must not be faked.
#:
#: The list cannot rot, because an entry that becomes implemented is reported
#: as an error in its own right. An allowlist nobody is forced to revisit is
#: how a stale exemption survives a rewrite.
PLANNED_BUT_UNIMPLEMENTED: set[str] = set()

# A placeholder is not a claim about the surface. The ellipsis appears
# in both spellings — three dots and U+2026 — and matching only the
# ASCII one reported prose as a broken invocation, which is how a
# checker that sits at one problem becomes a checker nobody reads.
_PLACEHOLDER = re.compile(r"^[<{$]|\.\.\.|…")


def candidates(text: str) -> list[tuple[int, str]]:
    """(lineno, command) for each dde command in a code block or code span.

    Three contexts, not two. Fenced blocks and inline spans are the
    obvious ones; the third is a line whose first token is `dde`
    outside both, which is how an indent-style code block is written.
    Restricting to the first two silently skipped four real invocations
    — two of them in `skills/artifact-conventions`, a skill agents read
    and copy from — and reported a clean 76 while doing it. plan-review
    hit the mirror of this in `tool_groups_in`: theirs matched prose and
    inflated the count, mine matched too little and understated it. A
    count is only reassuring if you know what it declined to look at.

    Prose is kept out by the requirement that `dde` be the *first*
    token on the line, which "all dde project artifacts" fails, plus
    the placeholder filter downstream.
    """
    found: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        stripped = line.strip().lstrip("$").strip()
        if in_fence:
            if stripped.startswith("dde "):
                found.append((lineno, stripped))
            continue
        spans = re.findall(r"`([^`]+)`", line)
        for span in spans:
            span = span.strip().lstrip("$").strip()
            if span.startswith("dde "):
                found.append((lineno, span))
        if not spans and stripped.startswith("dde "):
            found.append((lineno, stripped))
    return found


def resolve(tokens: list[str]):
    """Walk the built tree. Returns ((command, remaining_tokens), None) or (None, reason)."""
    node: click.Command = cli
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-") or not isinstance(node, click.Group):
            break
        nxt = node.get_command(None, token)
        if nxt is None:
            if _PLACEHOLDER.match(token):
                return None, None
            if node is cli and token in PLANNED_BUT_UNIMPLEMENTED:
                return None, None
            return None, f"unknown subcommand {token!r} under {node.name or 'dde'}"
        node, index = nxt, index + 1
    if node is cli or isinstance(node, click.Group):
        return None, None  # a bare group: a help reference, not a call
    return (node, tokens[index:]), None


def option_names(command: click.Command) -> set[str]:
    names: set[str] = set()
    for param in command.params:
        names.update(getattr(param, "opts", []) or [])
        names.update(getattr(param, "secondary_opts", []) or [])
    return names


def main() -> int:
    problems: list[tuple[str, int, str, str]] = []
    checked = 0

    for target in TARGETS:
        base = ROOT / target
        files = [base] if base.is_file() else sorted(base.rglob("*.md"))
        for path in files:
            for lineno, text in candidates(path.read_text(encoding="utf-8")):
                command_text = text.split("#")[0].split("|")[0].split("&&")[0].strip()
                try:
                    tokens = shlex.split(command_text)[1:]
                except ValueError:
                    continue
                if not tokens:
                    continue
                checked += 1
                resolved, reason = resolve(tokens)
                rel = str(path.relative_to(ROOT))
                if reason:
                    problems.append((rel, lineno, command_text, reason))
                    continue
                if resolved is None:
                    continue
                command, rest = resolved
                known = option_names(command)
                for token in rest:
                    if not token.startswith("-") or token == "--":
                        continue
                    flag = token.split("=")[0]
                    if flag not in known:
                        problems.append(
                            (
                                rel,
                                lineno,
                                command_text,
                                f"`{command.name}` has no option {flag}",
                            )
                        )

    # The allowlist checks itself: an entry that now exists is a stale
    # exemption, and a stale exemption is how a checker stops checking.
    landed = [name for name in PLANNED_BUT_UNIMPLEMENTED if cli.get_command(None, name)]
    for name in landed:
        problems.append(
            (
                "tools/check_invocations.py",
                0,
                f"dde {name}",
                f"allowlisted as unimplemented but `dde {name}` now exists — "
                "remove the entry so its invocations are checked",
            )
        )

    print(f"checked {checked} dde invocation(s) across {', '.join(TARGETS)}")
    print(
        f"allowlisted as not yet implemented: "
        f"{', '.join(sorted(PLANNED_BUT_UNIMPLEMENTED))}"
    )

    # Zero coverage is a failure, not a clean run. A renamed TARGETS entry, a
    # regex that stops matching the fence style someone switched to, or a
    # partial checkout all yield "0 problems" from an instrument that read
    # nothing — and well-formed output over an empty subject is exactly what
    # gets believed. Four instances of this shape landed in one day across
    # three people's work, so the rule is now: report coverage, and refuse to
    # report success without any.
    if checked == 0:
        print(
            "FAIL: no dde invocations found at all — the checker inspected "
            "nothing, which is not the same as finding nothing"
        )
        return 1
    for rel, lineno, text, reason in problems:
        print(f"  {rel}:{lineno}: {reason}")
        print(f"      {text}")
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
