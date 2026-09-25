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

"""Check that every registered relay code is emitted, every emitted code is
registered, and every code named in a skill relay table is registered.

A registered-but-never-emitted relay code is the worst kind of vacuous guard:
it appears as coverage in the registry *and* in the skill relay tables, and it
can never fire. Both of the places a reviewer would look to confirm the guard
exists would confirm it.

The prior manual audit used ``grep -v`` intended to exclude a *file* and which
excluded *lines* instead, silently dropping every ``provenance.relay(...)``-style
call. The fix that worked was to enumerate from the registry — the definitive
population — and search for each member by name. That is the method this gate
uses, via ``_enumerate_emission_sites()`` in ``cli.py``, and it is the reason
the gate exists: a careful grep did not work, and a gate is worth more than a
careful grep when the grep has already been wrong once.

Three checks:

  1. **Every code in RELAY_CODES has at least one emission site** outside
     ``provenance.py``. Uses ``_enumerate_emission_sites()`` from ``cli.py``
     — the same two-pass method (direct source match, then module-helper
     search) the ``dde relays`` command uses. Not reimplemented here.

  2. **Every code string emitted anywhere is registered.** Currently enforced
     at runtime by ``provenance.relay()``, but at runtime is too late — it
     fires during the expensive phase. Uses AST inspection of command source
     files to find string literals passed as relay codes. Two calling
     conventions are covered (see ``emitted_codes_from_ast``'s docstring
     for the coverage statement and what would evade it). This check cannot
     be derived from registry-enumeration data — that method searches for
     known strings and cannot discover unknown ones — so a second,
     independent scan is unavoidable. The AST approach was chosen over
     pattern-matching to avoid reintroducing the grep-fragility class this
     issue exists to prevent.

  3. **Every code named in a skill relay table is registered.** Parses
     markdown tables whose first-column header reads "Relay code" and
     extracts backtick-quoted code strings from subsequent data rows.

Usage:  PYTHONPATH=tools python3 tools/check_relay_codes.py

Exit codes, and the third one is not decoration:

  0  checked, nothing wrong
  1  checked, found a problem
  2  **could not run** — the checker never got as far as an opinion

Collapsing 2 into 1 would conflate "I checked and the repo is broken" with "I
could not check", and the two demand opposite responses: fix the repo, or fix
the environment. A gate that goes red for reasons unrelated to its subject is a
gate people learn to wave through — so it damages the checks that do work, not
only this one.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

#: Reserved for "the checker could not run". Never returned for a finding.
CANNOT_RUN = 2

#: Imported defensively, not because the import is optional, but because the
#: failure has to be reportable as its own outcome rather than as a traceback.
#: This checker imports the CLI to reuse ``_enumerate_emission_sites()`` for
#: check 1, which needs click and the full dde package. A broad except is
#: deliberate: a missing dependency raises ImportError, a half-installed one
#: raises whatever the broken module raises, and both mean the same thing
#: here — this checker never reached an opinion.
try:
    from dde.cli import _enumerate_emission_sites
    from dde.core import provenance
except Exception as exc:
    print(
        f"CANNOT RUN — {type(exc).__name__}: {exc}\n"
        "  This checker imports the CLI to reuse _enumerate_emission_sites() "
        "for check 1 (every registered code is emitted), and the provenance "
        "module to read RELAY_CODES. Both need click and the full dde "
        "package.\n"
        "  Remedy: run as `PYTHONPATH=tools python3 tools/check_relay_codes.py` "
        "from the repository root with the tools venv active.\n"
        "  Exit 2 rather than 1, because 1 would read as 'found a problem' "
        "and send someone to fix the repo instead of the environment.",
        file=sys.stderr,
    )
    sys.exit(CANNOT_RUN)

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
COMMANDS = ROOT / "tools" / "dde" / "commands"


def emitted_codes_from_ast() -> dict[str, list[tuple[str, int]]]:
    """All relay code strings emitted in command source, found by AST.

    Returns ``{code_string: [(relative_path, lineno), ...]}``.

    Two calling conventions are recognized.  A new one would evade this
    check — if one is added, extend this function and update this coverage
    statement:

      1. ``provenance.relay("code", ...)`` — a call to a function or method
         named ``relay`` whose first positional argument is a string literal
         (or a module-level constant assigned from one).
      2. ``sidecar.warn("msg", code="code")`` — a call to a function or
         method named ``warn`` with a keyword argument ``code`` whose value
         is a string literal (or a module-level constant).

    This is the one check that cannot be derived from registry-enumeration.
    ``_enumerate_emission_sites()`` searches for each *registered* code by
    name and can only find what it was told to look for; this check must
    find codes the registry does not know about, which is a different
    question that requires a different method.  The AST approach was chosen
    over regex/text matching because the prior audit's grep-based scan had
    exactly the fragility this issue was filed to prevent.
    """
    found: dict[str, list[tuple[str, int]]] = {}
    for path in sorted(COMMANDS.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        rel = str(path.relative_to(ROOT))
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue

        # Resolve module-level string constants so that
        #   RELAY = "tool.code_name"
        #   sidecar.warn(msg, code=RELAY)
        # is caught alongside the literal form.
        constants: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[target.id] = node.value.value

        def _resolve(
            node: ast.expr, constants: dict[str, str] = constants
        ) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name) and node.id in constants:
                return constants[node.id]
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            func_name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )

            code_value = None

            # Convention 1: relay("code", ...)
            if func_name == "relay" and node.args:
                code_value = _resolve(node.args[0])

            # Convention 2: warn(..., code="code")
            if func_name == "warn":
                for kw in node.keywords:
                    if kw.arg == "code":
                        code_value = _resolve(kw.value)
                        break

            if code_value is not None:
                found.setdefault(code_value, []).append(
                    (rel, getattr(node, "lineno", 0))
                )

    return found


def skill_relay_table_codes() -> dict[str, list[tuple[str, int]]]:
    """Relay codes named in skill relay tables.

    Returns ``{code_string: [(relative_path, lineno), ...]}``.

    A relay table is recognized by a markdown table row whose first data
    column header reads "Relay code" (case-insensitive).  Subsequent rows
    until the next non-table line are scanned for backtick-quoted strings
    in the first data column.  This is the same structure every skill in
    this repo uses: see ``pocket-druggability/SKILL.md``,
    ``target-genetic-evidence/SKILL.md``, etc.
    """
    found: dict[str, list[tuple[str, int]]] = {}
    skill_files = sorted(
        set(list(SKILLS.glob("*/SKILL.md")) + list(SKILLS.glob("*/*.md")))
    )

    for path in skill_files:
        rel = str(path.relative_to(ROOT))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        in_relay_table = False
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()

            # A non-table line ends any relay table being parsed.
            if not stripped.startswith("|"):
                in_relay_table = False
                continue

            cols = [c.strip() for c in stripped.split("|")]
            # cols[0] is '' (before first |), cols[-1] is '' or content
            if len(cols) < 3:
                continue
            first_col = cols[1]

            # Header row: first column reads "Relay code"
            if re.match(r"relay\s+code", first_col, re.IGNORECASE):
                in_relay_table = True
                continue

            # Separator row (dashes, optional alignment colons): skip but
            # stay in table.  Covers `:---`, `---:`, `:---:` as well as `---`.
            if all(
                c.replace("-", "").replace(":", "").replace(" ", "") == ""
                for c in cols[1:-1]
                if c  # skip empty segments from trailing |
            ):
                continue

            if in_relay_table:
                match = re.match(r"`([^`]+)`", first_col)
                if match:
                    code = match.group(1)
                    found.setdefault(code, []).append((rel, lineno))

    return found


def main() -> int:
    registered = set(provenance.RELAY_CODES)

    # An empty registry is not a neutral starting point. Every check below
    # would compare against nothing, find nothing wrong, and report success
    # — a vacuous pass, which is the defect this gate was built to catch.
    if not registered:
        print(
            "CANNOT RUN — RELAY_CODES is empty. Either the constant was "
            "moved or it holds no entries; the checker cannot function without "
            "a registry to check against.",
            file=sys.stderr,
        )
        return CANNOT_RUN

    errors: list[str] = []

    # --- Check 1: every registered code has at least one emission site ------
    emissions = _enumerate_emission_sites()
    n_sites = sum(len(sites) for sites in emissions.values())
    orphaned = sorted(code for code, sites in emissions.items() if not sites)

    for code in orphaned:
        errors.append(
            f"check 1: `{code}` is registered in RELAY_CODES but has no "
            f"emission site in any command. A registered code with no "
            f"emitter is a vacuous guard — it appears as coverage in the "
            f"registry and in skill relay tables, and it can never fire."
        )

    # --- Check 2: every emitted code is registered --------------------------
    emitted = emitted_codes_from_ast()
    unregistered_emitted = sorted(set(emitted) - registered)

    for code in unregistered_emitted:
        sites = emitted[code]
        locations = ", ".join(f"{p}:{ln}" for p, ln in sites)
        errors.append(
            f"check 2: `{code}` is emitted at {locations} but is not "
            f"registered in RELAY_CODES. Currently enforced at runtime by "
            f"provenance.relay(), but at runtime is too late — it fires "
            f"during the expensive phase. Add the code to RELAY_CODES or "
            f"remove the emission."
        )

    # --- Check 3: every code in a skill relay table is registered -----------
    skill_codes = skill_relay_table_codes()
    unregistered_in_skills = sorted(set(skill_codes) - registered)

    for code in unregistered_in_skills:
        sites = skill_codes[code]
        locations = ", ".join(f"{p}:{ln}" for p, ln in sites)
        errors.append(
            f"check 3: `{code}` is named in a skill relay table at "
            f"{locations} but is not registered in RELAY_CODES. A skill that "
            f"names an unregistered code sends a reviewer to check for "
            f"something that cannot appear."
        )

    # --- Coverage reporting -------------------------------------------------
    n_registered = len(registered)
    n_emitted_total = len(emitted)
    n_skill_codes_total = len(skill_codes)
    n_command_files = sum(
        1 for p in COMMANDS.glob("*.py") if not p.stem.startswith("_")
    )
    all_skill_files = sorted(
        set(list(SKILLS.glob("*/SKILL.md")) + list(SKILLS.glob("*/*.md")))
    )
    n_skill_files = len(all_skill_files)
    skills_with_tables = len({p for codes in skill_codes.values() for p, _ in codes})

    print(f"registered relay codes: {n_registered}")
    print(
        f"check 1: {n_registered - len(orphaned)} of {n_registered} "
        f"registered codes have emission sites ({n_sites} site(s) total), "
        f"found by _enumerate_emission_sites() (two-pass: direct match "
        f"+ module-helper search)"
    )
    print(
        f"check 2: {n_emitted_total} distinct code(s) found in "
        f"{n_command_files} command file(s) by AST inspection "
        f"(relay() first arg + warn() code= kwarg); "
        f"{len(unregistered_emitted)} unregistered"
    )
    print(
        f"check 3: {n_skill_codes_total} distinct code(s) found in relay "
        f"tables across {skills_with_tables} of {n_skill_files} skill "
        f"file(s); {len(unregistered_in_skills)} unregistered"
    )

    # --- Vacuity guards ----------------------------------------------------
    # Each of these would otherwise leave a check comparing its findings
    # against nothing and reporting success — a vacuous check, which is the
    # same defect as an integrity sweep that prints "intact" when pointed at
    # an empty directory.
    if n_command_files == 0:
        print(
            "FAIL: no command files found under commands/ — check 2 inspected "
            "nothing, which is not the same as finding nothing"
        )
        return 1
    if n_skill_files == 0:
        print(
            "FAIL: no skill files found under skills/ — check 3 inspected "
            "nothing, which is not the same as finding nothing"
        )
        return 1
    if n_emitted_total == 0:
        print(
            "FAIL: no relay emissions found in command source by AST — "
            "either no command emits a relay (unlikely given "
            f"{n_registered} registered codes) or the calling convention "
            "has changed and this checker's AST patterns do not cover it. "
            "See emitted_codes_from_ast()'s docstring for what it recognizes."
        )
        return 1

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(
        "\nEvery registered code is emitted, every emitted code is "
        "registered, and every skill relay table code is registered."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
