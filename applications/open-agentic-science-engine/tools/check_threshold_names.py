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

"""Check that every threshold a skill cites is one the CLI declares, in the right set.

Skills cite thresholds by name and never by value (docs/tool-design-guidance.md
§7). That rule has had no instrument: a skill could cite `plddt_confidence`,
`expression_floor` or an AlphaFold threshold inside the genetics skill, and
nothing would notice. The prose would read correctly, and the name would resolve
to nothing at the moment a specialist went looking for it.

Two things are checked, and the second is the one worth having:

  1. A cited name is declared somewhere in `dde.core.thresholds`.
  2. A cited name is declared in a set the *citing skill's own tools* load.
     Citing a real threshold from another tool's set is the failure that reads
     best and helps least.

Written deliberately by someone who did not write the threshold sets. An
instrument aimed by the author of the thing it checks tends to confirm what its
author expected — see §3, "the instrument that finds a coverage gap is almost
never the one aimed at it".

Reverse coverage is reported too: a declared threshold that no skill cites is
not an error, but it is a number that decides something with no prose telling
anyone it exists.

Usage:  PYTHONPATH=tools python3 tools/check_threshold_names.py

Exit codes, and the third one is not decoration:

  0  checked, nothing wrong
  1  checked, found a problem
  2  **could not run** — the checker never got as far as an opinion

Collapsing 2 into 1 is the vacuous pass inverted. A missing `click` makes this
module fail at import, and a traceback exits 1, which reads in any gate as
*found a problem*. The two demand opposite responses: fix the repo, or fix the
environment. Worse, this direction fails towards a **false alarm**, and a gate
that cries wolf gets waved through — so it damages the checks that do work,
not only this one.
"""

from __future__ import annotations

import ast
import difflib
import re
import sys
from pathlib import Path

#: Imported defensively, not because the import is optional, but because the
#: failure has to be reportable as its own outcome rather than as a traceback.
#: The guard is deliberately broad: a *missing* dependency raises ImportError, a
#: half-installed one raises whatever the broken module raises at import, and
#: both mean the same thing here — this checker never reached an opinion.
try:
    from dde.core import thresholds as th
except Exception as exc:
    th = None  # type: ignore[assignment]
    _IMPORT_FAILURE: Exception | None = exc
else:
    _IMPORT_FAILURE = None

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
COMMANDS = ROOT / "tools" / "dde" / "commands"

#: A code span must look like this to be considered at all.
_SNAKE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")

#: Artifact record fields and CLI vocabulary. These are legitimately written in
#: skills and are not threshold citations. Kept explicit rather than inferred:
#: an inferred exclusion list would quietly grow to cover a real mistake.
#:
#: The list checks itself. An entry that is also a declared threshold name is
#: reported as an error, because it would silence the very citation this tool
#: exists to check — two entries here were exactly that on the first run. An
#: exemption nobody is forced to revisit is how a checker stops checking.
RECORD_FIELDS = {
    "mandatory_relays",
    "threshold_set",
    "thresholds_applied",
    "threshold_sources",
    "threshold_provenance",
    "env_version",
    "written_by",
    "resolved_by",
    "verdict_from",
    "not_found",
    "not_detected",
    "score_variant",
    "predict_interval",
    "interval_level",
    "exp_lof",
    "n_tracks_scored",
    "has_clash",
    "phantom_citations",
    "quantile_artifact",
}

#: How close a token must be to a declared name before we call it a typo
#: rather than an unrelated identifier.
_TYPO_RATIO = 0.82


def relay_codes_from_source() -> list[str]:
    """RELAY_CODES keys, read from provenance.py's source rather than imported.

    Importing them was the only thing that made this checker need click:
    `provenance` imports `output`, and `output` imports click, while
    `thresholds` is a leaf. So the gate could not run in any container without
    the shared venv — and a gate only some agents can run is not a gate.

    Reading the literal instead is not a workaround for a missing package. It
    is the correct dependency: this file wants a list of names, not the
    behaviour of the module that happens to hold them. Where a checker imports
    a module solely to read a constant, it inherits every dependency that
    module has, for none of the benefit.
    """
    source = (ROOT / "tools" / "dde" / "core" / "provenance.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0].id
        if target == "RELAY_CODES" and isinstance(node.value, ast.Dict):
            return [
                k.value
                for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
    return []


def declared() -> tuple[dict[str, set[str]], dict[str, str]]:
    """(set name -> threshold names), (threshold name -> set name)."""
    by_set: dict[str, set[str]] = {}
    owner: dict[str, str] = {}
    for name, ts in th.declared_sets().items():
        by_set[name] = set(ts.values)
        for key in ts.values:
            owner.setdefault(key, name)
    return by_set, owner


def sets_loaded_by_module(known_sets: set[str]) -> dict[str, set[str]]:
    """Command module stem -> threshold set names it loads.

    Parsed from the source rather than by running anything: `load()` needs a
    project root, and the question here is static.
    """
    out: dict[str, set[str]] = {}
    for path in sorted(COMMANDS.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        found: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        # Module-level `THRESHOLD_SET = "pocket"`. Without this the checker
        # silently fails to place any module that names its set in a
        # constant — which is the better style — and reports the SKILL as
        # unplaceable, sending the reader to look in the wrong file. A
        # checker that only understands one way of writing correct code
        # is a checker that dictates style while claiming to verify
        # meaning.
        constants: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not (
                isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            # `thresholds.load(...)` and the `load_thresholds(...)` wrapper in
            # common.py. Matching only the first found nothing at all, and the
            # tool reported success on a check that had never run.
            if name not in ("load", "load_thresholds") or not node.args:
                continue
            # The set name is not at a fixed position: `thresholds.load(name)`
            # but `load_thresholds(state, name, overrides)`. Take any string
            # argument that names a declared set rather than indexing, so a
            # future wrapper with a different signature keeps working.
            for arg in node.args:
                value = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    value = arg.value
                elif isinstance(arg, ast.Name):
                    value = constants.get(arg.id)
                if value in known_sets:
                    found.add(value)
        if found:
            out[path.stem] = found
    return out


def code_regions(text: str) -> str:
    """Only the fenced blocks and inline code spans, joined.

    An invocation is written as code. Scanning the whole document instead let
    English match: `all dde project artifacts` in a one-line description
    registered a tool group named `project`, which no command module provides.

    That direction of error is the dangerous one here. A phantom group cannot
    invent a threshold set — but a *real* word following `dde` in prose can
    credit a skill with a set its tools never load, and the placement check
    then passes a citation it was built to catch. A false positive in the
    entitlement pool is a false negative in the finding.

    Three code contexts, and the third was found by breaking the second. Fenced
    blocks and inline spans alone dropped `dde genetics analyze` from
    artifact-conventions, because that file uses a four-space indented block and
    no fence — so tightening the scan turned one false positive into two false
    negatives. A line whose *first* token is `dde` is a command in any
    plausible markdown, whatever surrounds it, so it is admitted directly.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or line.strip().startswith("dde "):
            out.append(line)
        else:
            out.extend(re.findall(r"`([^`]+)`", line))
    return "\n".join(out)


def tool_groups_in(text: str) -> set[str]:
    """Tool groups the skill actually invokes, from its `dde …` commands."""
    groups: set[str] = set()
    for match in re.finditer(r"\bdde\s+([a-z][a-z0-9-]*)", code_regions(text)):
        groups.add(match.group(1))
    return groups


def citations_in(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in _SNAKE.finditer(line):
            found.append((lineno, match.group(1)))
    return found


def main() -> int:
    if _IMPORT_FAILURE is not None:
        print(
            f"CANNOT RUN — this checker never reached an opinion about the repo.\n"
            f"  {type(_IMPORT_FAILURE).__name__}: {_IMPORT_FAILURE}\n"
            f"The threshold declarations are read by importing dde.core.thresholds, "
            f"which is a leaf module with no third-party dependency — so this failure "
            f"means the package itself is unreachable or broken, not that a tool is "
            f"missing. Remedy: run as `PYTHONPATH=tools python3 "
            f"tools/check_threshold_names.py` from the repository root. Exit 2 rather "
            f"than 1, because 1 would read as 'found a problem' and send someone to "
            f"fix the repo instead of the environment.",
            file=sys.stderr,
        )
        return 2

    by_set, owner = declared()
    module_sets = sets_loaded_by_module(set(by_set))
    relay_codes = relay_codes_from_source()
    relay_locals = {code.split(".", 1)[-1] for code in relay_codes}
    all_names = set(owner)

    # An empty exclusion set is not a neutral state. Every relay code would then
    # look like an undeclared threshold with a near-miss name, so the checker
    # would invent problems on a correct repo — the false-alarm direction, which
    # is the one that gets a gate ignored rather than the one that gets it
    # trusted. Fail as cannot-run, not as found-a-problem.
    if not relay_locals:
        print(
            "CANNOT RUN — no RELAY_CODES literal was found in "
            "tools/dde/core/provenance.py, so the exclusion set is empty and "
            "every relay code would be reported as a mistyped threshold. Either "
            "the constant moved or it is no longer a dict literal; point this "
            "reader at its new home.",
            file=sys.stderr,
        )
        return 2

    errors: list[str] = []

    # The exclusion list must not shadow a real threshold name.
    for name in sorted(RECORD_FIELDS & all_names):
        errors.append(
            f"tools/check_threshold_names.py: RECORD_FIELDS excludes `{name}`, "
            f"which is a declared threshold in set '{owner[name]}' — the "
            f"exclusion would silence a citation this tool exists to check"
        )

    checked = 0
    placed = 0
    no_tools: list[str] = []
    cited: set[str] = set()
    skill_files = sorted(SKILLS.glob("*/SKILL.md")) + sorted(SKILLS.glob("*/*.md"))
    skill_files = sorted(set(skill_files))

    # Nothing to compare against is not agreement. Each of these three would
    # otherwise leave the tool comparing citations against an empty pool,
    # finding no mismatches, and reporting success — a vacuous check, which is
    # the same defect as an integrity sweep that prints "intact" when pointed
    # at an empty directory. Wherever absence is used as evidence, ask what
    # else produces absence.
    if not skill_files:
        print("no skill files found — check the path", file=sys.stderr)
        return 1
    if not all_names:
        print(
            "no thresholds are declared at all — declared_sets() returned nothing, "
            "so every citation below would have been compared against an empty pool",
            file=sys.stderr,
        )
        return 1
    if not module_sets:
        print(
            "no command module was found to load any threshold set — the "
            "placement check cannot run, and the remaining check is the weaker one",
            file=sys.stderr,
        )
        return 1

    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        groups = tool_groups_in(text)
        # The sets this skill is entitled to cite from.
        entitled: set[str] = set()
        for group in groups:
            entitled |= module_sets.get(group, set())
        entitled_names = {n for s in entitled for n in by_set.get(s, ())}
        rel = path.relative_to(ROOT)

        # State coverage rather than assume it. A skill whose tool groups
        # resolve to no threshold set cannot have its citations placed, and
        # silently skipping it is how this tool would report success on a
        # check it never ran — which it did, on its first version.
        if entitled:
            placed += 1
        elif not groups:
            # No invocation at all. Distinct from "invokes a tool whose set
            # cannot be resolved", which is a defect; this is simply a prose
            # skill, and conflating the two is what made the shortfall
            # permanent and therefore unreadable.
            no_tools.append(rel.parent.name)
        unplaceable = sorted(g for g in groups if g not in module_sets)
        if citations_in(text) and not entitled:
            errors.append(
                f"{rel}: cites thresholds but no threshold set could be resolved "
                f"for its tool group(s) ({', '.join(unplaceable) or 'none found'}); "
                f"citations in this file are unchecked"
            )

        for lineno, token in citations_in(text):
            if token in RECORD_FIELDS or token in relay_locals:
                continue

            if token in all_names:
                checked += 1
                cited.add(token)
                if entitled and token not in entitled_names:
                    errors.append(
                        f"{rel}:{lineno}: cites `{token}`, declared in set "
                        f"'{owner[token]}', but this skill's tools "
                        f"({', '.join(sorted(groups)) or 'none'}) load "
                        f"{', '.join(sorted(entitled)) or 'no set'}"
                    )
                continue

            # Not a declared threshold. Only complain when it looks like one
            # that was mistyped — an unrelated identifier is not this tool's
            # business, and a checker that reports everything gets ignored.
            pool = entitled_names or all_names
            near = difflib.get_close_matches(token, pool, n=1, cutoff=_TYPO_RATIO)
            if near:
                errors.append(
                    f"{rel}:{lineno}: cites `{token}`, which is not declared; "
                    f"closest declared name is `{near[0]}`"
                )

    uncited = sorted(all_names - cited)

    # Two figures, and only the first is coverage. `len(skill_files)` comes from
    # a directory listing this parser cannot shrink, so a format change it
    # cannot read shows up as a smaller `placed`. `checked` has no independent
    # denominator — this parser defines the population it then reports on, so it
    # is a count of work done, not a proportion of work owed. Labelled rather
    # than dropped, because the number is useful and the misreading is not.
    # A gap that never closes stops being read. `8 of 9` was permanent and
    # unexplained, because one skill invokes no tool at all and never will — so
    # the shortfall carried no information and would train a reader to skip the
    # line that also reports the real gaps. Name the exempt files instead: the
    # number then moves only when something changes.
    print(
        f"{placed} of {len(skill_files)} skill file(s) placed against a declared "
        f"threshold set; {checked} citation(s) checked (no independent total: "
        f"citations not matched by this reader are not counted anywhere)"
    )
    if no_tools:
        print(
            f"{len(no_tools)} file(s) invoke no dde tool, so there is nothing to "
            f"place them against: {', '.join(no_tools)}"
        )
    print(f"{len(cited)} of {len(all_names)} declared thresholds are cited by a skill")
    if uncited:
        print(
            "\nDeclared but never cited (not an error; a number nobody was told about):"
        )
        for name in uncited:
            print(f"  {name}  [{owner[name]}]")

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1

    print("\nEvery cited threshold resolves, in a set its own skill's tools load.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
