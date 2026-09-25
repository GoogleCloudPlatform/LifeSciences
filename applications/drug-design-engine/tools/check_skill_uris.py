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

"""Check the skill URIs declared in `templates/*/scion-agent.yaml`, both directions.

A template grants capability by declaring skill URIs. Nothing checked them. The
grammar, the path mapping and the failure mode were all believed rather than
known, and every template in this repo encoded those beliefs.

They have now been established from the shipped `scion` binary rather than from
anyone's recollection. Recorded here because the next reader will otherwise
have to redo it:

  * `gh://owner/repo/skill-name[@ref][?token=SECRET_NAME]` is the accepted form.
    Also accepted: a full `https://github.com/owner/repo/tree/<ref>/<path>`
    URL, `skill://<registry>/<scope>/<name>@<version>`, `gcp-skill://…`,
    `scion-platform://<name>`, and a bare name.
  * `skills/` is **hardcoded**. `parseGHShorthand` concatenates the 7-byte
    literal `"skills/"` with the skill name, and the resolver requests
    `/repos/<owner>/<repo>/contents/skills/<name>?ref=<ref>`. The directory
    must contain `SKILL.md`. To use any other directory you must give the full
    GitHub URL form, which carries an explicit path.
  * **Resolution is remote.** It goes to the GitHub API against a ref, not to
    the local working tree. A skill that exists on disk but is not pushed does
    not exist as far as provisioning is concerned. This is the fact that most
    changes what is worth checking, and it is the reason for check 3.
  * A **required** skill that does not resolve is a hard error: `ProvisionAgent`
    returns `required skill %q could not be resolved: %s` and provisioning
    aborts. Only `optional: true` entries degrade quietly, to a `Debugf` line on
    the CLI's stderr that is suppressed unless `--debug` is set.

That last point refutes the belief this checker was commissioned to defend
against. Silent skill-load failure is real only for `optional: true`, and no
entry in this repo sets it. So check 4 exists to keep it that way: an
`optional: true` here would convert a loud provisioning failure into a silent
capability gap, which is a much worse trade than it looks.

Checks:

  1. Every declared URI parses under the verified grammar.
  2. Every URI naming *this* repo resolves to `skills/<name>/SKILL.md` on disk.
  3. That file is committed and present on the tracking ref. On disk is not
     enough, because resolution is remote.
  4. No entry is `optional: true`, and no template carries a field the platform
     guidance forbids.
  5. Reverse: every skill under `skills/` is declared by at least one template.
     This is the direction that matters. The forward pass asks a question the
     author of the declarations already wants to get right; the reverse pass
     asks one nobody is positioned to ask, and it is how three skills were found
     built, documented and unreachable.
  6. Currency: the pushed copy of a resolvable skill matches the working tree.

Check 6 exists because check 3 was read as answering a question it does not
ask. **Presence on the ref and currency of what is on the ref are different
questions.** A skill can be declared, resolvable and green while provisioning
loads text its author already replaced — the author committed and did not push,
or edited and did not commit. That case is worse than absence, which is the
case check 3 covers: absence fails loudly at provisioning, whereas staleness
provisions cleanly with the wrong content and reports nothing anywhere.

So the outcome for a declared skill is three-way, not two-way:

  * **resolvable and current** — the pushed copy matches this working tree.
  * **resolvable but superseded locally** — provisioning succeeds and loads the
    older text. Reported by name with the remedy; exit 0, because the person
    running this may not own the skill and may not be able to push it. Under
    `--strict` it is an error, which is the mode to use before provisioning an
    agent that depends on the skill.
  * **not on the ref at all** — provisioning fails. An error in both modes.

Check 6's own limit, stated because an instrument must state its coverage:
currency is judged against the *local* remote-tracking ref, which is only as
fresh as the last `git fetch`. A stale tracking ref can call a pushed change
unreported. It fails in the direction of silence, so run `git fetch` first.

Coverage is reported, not assumed. URIs naming a foreign repository cannot be
checked from here and are counted and named rather than passed over — an
instrument must state its coverage, not only its findings. For the same reason
the closing line says "pushed", not "current", when check 6 could not run.

Usage:  python3 tools/check_skill_uris.py [--strict]
Exits 1 if anything is wrong, so it can gate a commit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
SKILLS = ROOT / "skills"

#: Fields the platform guidance says never to author in a template. They are
#: legitimate schema fields that Scion itself writes into resolved agent
#: configs, so a parser will not reject them — which is exactly why a checker
#: has to. A convention cannot carry a guarantee.
FORBIDDEN_FIELDS = ("harness", "harness_config", "explicit_workspace", "config_dir")

#: From `parseGHShorthand` in the shipped binary.
_GH = re.compile(
    r"^gh://"
    r"(?P<owner>[A-Za-z0-9._-]+)/"
    r"(?P<repo>[A-Za-z0-9._-]+)/"
    r"(?P<name>[^/@?]+)"
    r"(?:@(?P<ref>[^?]+))?"
    r"(?:\?token=(?P<token>[A-Z][A-Z0-9_]*))?$"
)
_OTHER_SCHEMES = (
    "skill://",
    "gcp-skill://",
    "scion-platform://",
    "https://github.com/",
)

#: PyYAML is not installed in every agent's container, and a checker that only
#: some agents can run is not a gate. The template files are flat, so they are
#: read line-wise instead. Parse coverage is reported so that a format change
#: that defeats this reader shows up as zero rather than as success.
_URI = re.compile(r"^\s*-\s*uri:\s*[\"']?([^\"'\s]+)[\"']?")
_OPTIONAL = re.compile(r"^\s*optional:\s*(\S+)")
_TOPLEVEL = re.compile(r"^([a-z_]+):")


def this_repo() -> tuple[str, str] | None:
    """(owner, repo) from the git remote, or None if it cannot be determined."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"[:/]([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?$", url)
    return (match.group(1), match.group(2)) if match else None


def tracking_ref() -> str | None:
    for args in (["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],):
        try:
            out = subprocess.run(
                ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
        return out or None
    return None


def on_ref(ref: str, path: str) -> bool:
    """Is `path` present on `ref`? Resolution is remote, so disk is not enough."""
    try:
        out = subprocess.run(
            ["git", "ls-tree", "--name-only", ref, path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(out)


def differs_from_ref(ref: str, directory: str) -> str | None:
    """Why the working copy of `directory` differs from `ref`, or None.

    Two ways to be superseded, and only asking the first would leave a hole the
    size of the one this function was added to close. A tracked file edited or
    committed-but-unpushed shows in `git diff`; a *new* file in an existing
    skill directory shows in neither the diff nor `ls-tree`, because git does
    not track what it has never been told about.
    """
    try:
        diff = subprocess.run(
            ["git", "diff", "--quiet", ref, "--", directory],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", directory],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    reasons = []
    if diff.returncode == 1:
        reasons.append("tracked content differs")
    elif diff.returncode != 0:
        return None  # git could not answer; do not invent a verdict
    if untracked:
        shown = ", ".join(untracked[:3]) + ("…" if len(untracked) > 3 else "")
        reasons.append(f"{len(untracked)} untracked file(s): {shown}")
    return "; ".join(reasons) or None


def read_template(
    path: Path,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]]:
    """(uris, optional_flags, forbidden_fields), each as (lineno, value)."""
    uris: list[tuple[int, str]] = []
    optionals: list[tuple[int, str]] = []
    forbidden: list[tuple[int, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = _URI.match(line)
        if match:
            uris.append((lineno, match.group(1)))
            continue
        match = _OPTIONAL.match(line)
        if match:
            optionals.append((lineno, match.group(1)))
            continue
        match = _TOPLEVEL.match(line)
        if match and match.group(1) in FORBIDDEN_FIELDS:
            forbidden.append((lineno, match.group(1)))
    return uris, optionals, forbidden


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv
    for arg in argv:
        if arg != "--strict":
            print(
                f"unknown argument {arg!r}; usage: check_skill_uris.py [--strict]",
                file=sys.stderr,
            )
            return 2

    errors: list[str] = []
    notes: list[str] = []
    stale: list[str] = []

    templates = sorted(TEMPLATES.glob("*/scion-agent.yaml"))
    skills_on_disk = sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))

    # Nothing to compare against is not agreement. Each of these would leave the
    # tool finding no mismatches in an empty set and reporting success.
    if not templates:
        print(f"no templates found under {TEMPLATES} — check the path", file=sys.stderr)
        return 1
    if not skills_on_disk:
        print(f"no skills found under {SKILLS} — check the path", file=sys.stderr)
        return 1

    owner_repo = this_repo()
    ref = tracking_ref()
    if owner_repo is None:
        notes.append(
            "could not determine this repository from `git remote get-url origin`; "
            "no URI could be matched against it, so checks 2 and 3 did not run"
        )
    if ref is None:
        notes.append(
            "no tracking ref (`@{u}`); check 3 did not run, so a skill present on "
            "disk but never pushed would not be reported"
        )

    declared: set[str] = set()
    currency_checked: set[str] = set()
    checked = 0
    foreign: list[str] = []
    parsed_with_skills = 0

    for path in templates:
        rel = path.relative_to(ROOT)
        uris, optionals, forbidden = read_template(path)
        if uris:
            parsed_with_skills += 1

        for lineno, field in forbidden:
            errors.append(
                f"{rel}:{lineno}: declares `{field}`, which platform guidance says "
                f"never to author in a template; the parser accepts it, which is why "
                f"this is checked here"
            )

        for lineno, value in optionals:
            if value.lower() in ("true", "yes", "on"):
                errors.append(
                    f"{rel}:{lineno}: `optional: true` converts an unresolvable skill "
                    f"from a hard provisioning failure into a Debugf line nobody sees. "
                    f"If the capability is genuinely optional, say so here in a comment "
                    f"and add it to this checker's allowlist deliberately"
                )

        for lineno, uri in uris:
            checked += 1
            match = _GH.match(uri)
            if not match:
                if uri.startswith(_OTHER_SCHEMES):
                    foreign.append(f"{rel}:{lineno} {uri} (non-gh scheme)")
                    continue
                errors.append(
                    f"{rel}:{lineno}: {uri!r} does not parse. Expected "
                    f"gh://owner/repo/skill-name[@ref][?token=SECRET_NAME], a full "
                    f"https://github.com/owner/repo/tree/<ref>/<path> URL, or "
                    f"skill:// / scion-platform:// / a bare name"
                )
                continue

            name = match.group("name")
            if ".." in name or name in (".", ""):
                errors.append(f"{rel}:{lineno}: {uri!r} has an invalid skill name")
                continue

            if (
                owner_repo is None
                or (match.group("owner"), match.group("repo")) != owner_repo
            ):
                foreign.append(f"{rel}:{lineno} {uri}")
                continue

            declared.add(name)
            skill_path = f"skills/{name}/SKILL.md"
            if not (ROOT / skill_path).is_file():
                errors.append(
                    f"{rel}:{lineno}: {uri!r} resolves to {skill_path}, which does not "
                    f"exist. `skills/` is hardcoded in the resolver, so the skill "
                    f"directory name must match the URI's last segment exactly"
                )
                continue
            if not ref:
                continue
            if not on_ref(ref, skill_path):
                errors.append(
                    f"{rel}:{lineno}: {uri!r} exists on disk but is not on {ref}. "
                    f"Skill resolution is remote — it fetches from the GitHub API "
                    f"against a ref, not from this working tree — so provisioning "
                    f"would fail. Commit and push {skill_path}"
                )
                continue
            # Present on the ref. That is not the same as current on the ref.
            if name in currency_checked:
                continue  # two templates may declare the same skill
            currency_checked.add(name)
            why = differs_from_ref(ref, f"skills/{name}/")
            if why:
                stale.append(
                    f"skills/{name}/ is on {ref}, so this resolves and provisioning "
                    f"succeeds — but the pushed copy is not the current one ({why}). "
                    f"An agent provisioned now loads the superseded text, and nothing "
                    f"else in this repo reports that. Remedy: commit and push "
                    f"skills/{name}/, or `git fetch` if {ref} is behind"
                )

    undeclared = [s for s in skills_on_disk if s not in declared]
    for name in undeclared:
        errors.append(
            f"skills/{name}/SKILL.md is declared by no template. It is built, it is "
            f"documented, and no agent can load it. If that is deliberate, the skill "
            f"should not be here"
        )

    # Count the skills that are BOTH on disk and declared. `len(declared)` would
    # count names that resolve to nothing, and the two totals then happen to
    # agree whenever a template declares as many phantoms as it misses real
    # skills — a coverage line that reports a number it does not measure is the
    # defect this file exists to catch, one level up.
    reachable = len(set(declared) & set(skills_on_disk))
    print(
        f"{parsed_with_skills} of {len(templates)} template(s) yielded a skills block; "
        f"{checked} URI(s) checked; {reachable} of {len(skills_on_disk)} local "
        f"skill(s) reachable from a template; {len(currency_checked)} checked for "
        f"currency against {ref or 'no ref'}"
    )
    if stale:
        print(
            f"\n{len(stale)} skill(s) DECLARED AND PUSHED, BUT NOT CURRENT"
            f"{' (--strict: counted as errors)' if strict else ''}:"
        )
        for line in stale:
            print(f"  {line}")
    if foreign:
        print(
            f"\n{len(foreign)} URI(s) name another repository or scheme and cannot be "
            f"checked from here:"
        )
        for line in foreign:
            print(f"  {line}")
    for note in notes:
        print(f"\nCOVERAGE: {note}")

    if strict:
        errors.extend(stale)

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1

    # Say what was established and nothing wider. The earlier closing line read
    # as though it certified the content agents would load; it certified only
    # that something is there under that name. When check 6 could not run, the
    # gap between those two readings is exactly what this line must not paper
    # over — see the module docstring on presence versus currency.
    if stale:
        print(
            f"\nEvery declared URI resolves to a pushed skill, and every skill is "
            f"declared — but {len(stale)} pushed copy(ies) above are superseded "
            f"locally. Re-run with --strict before provisioning against them."
        )
    elif currency_checked and len(currency_checked) == len(
        declared & set(skills_on_disk)
    ):
        print(
            "\nEvery declared URI resolves to a pushed skill and the pushed copy "
            "matches this working tree; every skill is declared."
        )
    else:
        print(
            "\nEvery declared URI resolves to a pushed skill, and every skill is "
            "declared. NOT asserted: that the pushed copy is the current one."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
