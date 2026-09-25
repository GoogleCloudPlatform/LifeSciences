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

"""Check every literal `raw/<name>/` path written in the repo against the
artifact classes the code actually declares.

The sibling of check_invocations.py, aimed at the other axis. That one asks
whether the commands in our prose resolve against the built click tree; this
one asks whether the *paths* resolve against ARTIFACT_DIRS in
`dde/core/context.py`. Both are cache-invalidation checks over
documentation, and the reason for two is that an instrument only finds what it
is pointed at: check_invocations.py passed `skills/artifact-conventions/SKILL.md`
clean while four invented directory names sat in its tree diagram, because it
was reading commands and they were paths.

Two kinds of finding, deliberately separated:

  * **error** — the name is not an artifact class at all. Nothing can ever
    write there. `raw/assay-data/` was one of these; the class is `assays`.
  * **note** — the name is a declared class that no command writes yet.
    `raw/docking/` is one of these. That is a roadmap item, not a defect, and
    conflating the two teaches people to ignore the output.

Coverage is reported as loudly as findings, and zero coverage is a failure
rather than a pass. Two checkers in this repo have already shipped reporting
"0 problems" while examining nothing — plan-review's threshold checker with
`sets_loaded_by_module()` returning `{}`, and the scientific-reviewer's own
evidence-integrity sweep, which compared an empty snapshot to an empty
snapshot and reported the evidence intact. Absence of a finding is only
evidence when something was actually inspected.

Usage:  PYTHONPATH=tools python3 tools/check_artifact_paths.py
Exits 1 if anything is wrong, so it can gate a commit.

**Do not read this tool's exit code through a pipe.** `check_artifact_paths.py
| tail -5` reports the status of `tail`, which is always 0, so a failing
checker looks like a passing one — a vacuous test of a test, and the same
defect this file guards against, one level up. Redirect to a file or to
/dev/null and read `$?` directly. Two people hit this on the day the file was
written, one of them its author.

Exit codes, and why there are three:

  * **0** — ran, found nothing wrong.
  * **1** — ran, found something wrong. A finding about the repo.
  * **2** — *could not run*. A finding about the checker's environment, which
    is not a finding about the repo at all.

Two of this repo's four checkers currently exit 1 by traceback when `click` is
absent, which is what happens with no `tools/.venv`. That collapses "I checked
and the repo is broken" into "I could not check" under one number, and the
person reading a suite of exit codes cannot tell them apart. It fails in the
loud direction, so nobody is misled into believing a false clean run — but a
gate that goes red for reasons unrelated to its subject is a gate people learn
to wave through, and then it is not protecting anything. Coverage guards keep
an instrument from reporting a vacuous pass; this keeps it from reporting a
vacuous *failure*.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

#: Reserved for "the checker could not run". Never returned for a finding.
CANNOT_RUN = 2

try:
    from dde.core.context import ARTIFACT_DIRS
except ImportError as exc:  # pragma: no cover - environment, not logic
    print(
        f"CANNOT RUN: {exc}\n"
        "  This is not a finding about the repository — the checker never "
        "inspected it.\n"
        "  Install the toolchain (cd tools && ./install.sh) and activate the "
        "venv, or run with\n"
        "  PYTHONPATH=tools from an interpreter that has the dependencies.",
        file=sys.stderr,
    )
    sys.exit(CANNOT_RUN)

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("docs", "skills", "templates", "README.md")
COMMANDS_DIR = ROOT / "tools" / "dde" / "commands"

#: Not an artifact class and never routed through `artifact_dir()`: it is the
#: `--out` override an auditor passes so its re-run does not land on the record
#: under audit. Named explicitly rather than matched by pattern, so that a
#: second such directory has to be argued for rather than inherited.
ALLOWED_NON_CLASSES = {
    "reanalysis": "auditor --out override (artifact-conventions)",
}

#: `raw/<name>` where <name> is a bare directory component. Placeholders are
#: excluded here rather than filtered later: `raw/<category>/` is a statement
#: about the shape of the layout, not a claim that a directory exists.
_RAW_PATH = re.compile(r"raw/([A-Za-z][A-Za-z0-9_-]*)/")


def declared_classes() -> dict[str, str]:
    """The artifact classes the code declares, name -> relative directory."""
    return dict(ARTIFACT_DIRS)


def written_classes() -> set[str]:
    """Classes some command actually writes, read from the command modules.

    Parsed rather than imported: a literal in the source is the claim we want
    to check, and importing would only tell us what the modules evaluate to.
    Covers both `ARTIFACT_CLASS = "x"` and inline `artifact_dir("x", ...)`.
    """
    found: set[str] = set()
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "ARTIFACT_CLASS"
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        found.add(node.value.value)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "artifact_dir"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
    return found


def candidates(text: str) -> list[tuple[int, str, str]]:
    """(lineno, class_name, line) for each literal raw/<name>/ in the text."""
    found: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _RAW_PATH.finditer(line):
            found.append((lineno, match.group(1), line.strip()))
    return found


def main() -> int:
    declared = declared_classes()
    written = written_classes()
    unwritten = sorted(set(declared) - written)

    problems: list[tuple[str, int, str, str]] = []
    notes: list[tuple[str, int, str, str]] = []
    checked = 0
    files_read = 0
    seen_classes: set[str] = set()

    for target in TARGETS:
        base = ROOT / target
        files = [base] if base.is_file() else sorted(base.rglob("*.md"))
        for path in files:
            files_read += 1
            rel = str(path.relative_to(ROOT))
            for lineno, name, line in candidates(path.read_text(encoding="utf-8")):
                checked += 1
                seen_classes.add(name)
                if name in ALLOWED_NON_CLASSES:
                    continue
                if name not in declared:
                    near = ", ".join(sorted(declared))
                    problems.append(
                        (
                            rel,
                            lineno,
                            line,
                            f"`raw/{name}/` is not an artifact class — "
                            f"nothing can write there. Declared: {near}",
                        )
                    )
                elif name not in written:
                    notes.append(
                        (
                            rel,
                            lineno,
                            line,
                            f"`raw/{name}/` is declared but no command writes it yet",
                        )
                    )

    # A class that stops being declared while prose still points at it is the
    # failure this checker exists for; a class that is declared and never
    # mentioned anywhere is the reverse-coverage gap that found three orphaned
    # skills today. Report it, but as a note: prose is not obliged to be
    # exhaustive.
    unmentioned = sorted(set(declared) - seen_classes)

    # Two kinds of number below, and only one of them is coverage
    # (plan-review, 0f50371). A total the instrument does not compute can
    # shrink when the instrument breaks, so it reports on the instrument: the
    # file count comes from a directory listing, and `len(declared)` from
    # ARTIFACT_DIRS. A total the instrument derives by parsing cannot — the
    # regex defines the population it then reports the size of, so a regex
    # that silently stops matching half the corpus reports a smaller number
    # with equal confidence and nothing looks wrong. check_invocations printed
    # "76 invocations" for weeks while four in indent-style blocks were
    # invisible to it; 76 was true and was never the denominator anyone read
    # it as. Label which is which, rather than printing them on one line where
    # the parser-defined one inherits the credibility of the counted one.
    print(
        f"corpus: {files_read} file(s) in {', '.join(TARGETS)} (from a directory listing)"
    )
    print(f"artifact classes declared in core/context.py: {len(declared)}")
    print(f"  written by some command: {', '.join(sorted(written)) or 'none'}")
    print(f"  declared, nothing writes yet: {', '.join(unwritten) or 'none'}")
    print(f"  allowlisted non-classes: {', '.join(sorted(ALLOWED_NON_CLASSES))}")
    if unmentioned:
        print(f"  declared but never mentioned in prose: {', '.join(unmentioned)}")
    print(
        f"found and checked {checked} literal raw/<class>/ path(s) — "
        "this total is defined by the regex, so it is a finding, not coverage"
    )

    # Zero coverage is the failure mode this checker was written knowing about.
    # A regex that stops matching, a TARGETS entry that gets renamed, or an
    # empty checkout all produce "0 problems" from an instrument that read
    # nothing. Refuse to report success without having inspected something.
    if checked == 0:
        print(
            "FAIL: no raw/<class>/ paths found at all — "
            "the checker inspected nothing, which is not the same as finding nothing"
        )
        return 1
    if not declared:
        print("FAIL: ARTIFACT_DIRS is empty — nothing to check against")
        return 1

    # These notes are a STANDING CONDITION, not a to-do list, and saying so is
    # the difference between a line that gets read and one that gets skipped.
    # They will appear on every run until a command writes each class, so a
    # reader who treats them as outstanding work learns within two runs to skip
    # the whole block — and the problems printed underneath go with it. Give
    # them the expiry condition for the same reason a prohibition gets one: a
    # true statement that never changes reads as noise unless it says what
    # would change it.
    if notes:
        print(
            f"\n{len(notes)} standing note(s) — prose points at "
            f"{', '.join(unwritten)}, which are declared in ARTIFACT_DIRS but "
            "have no writer yet. Expected, not a defect, and expected to "
            "persist until a command writes each class. These are not "
            "outstanding work:"
        )
    for rel, lineno, _line, reason in notes:
        print(f"  note {rel}:{lineno}: {reason}")
    if problems:
        print(f"\n{len(problems)} problem(s) — these are defects:")
    for rel, lineno, line, reason in problems:
        print(f"  {rel}:{lineno}: {reason}")
        print(f"      {line}")
    print(f"{len(problems)} problem(s), {len(notes)} note(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
