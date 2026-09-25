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

"""What the environment is, written as one file you can read.

`ENV_VERSION` goes into every provenance sidecar, so it partitions a
program's artifacts: results produced under two different values are not
comparable without an argument (tool-design-guidance §2, Rule 4). A
partition is only bearable if it can be *explained* — "these two numbers
came from different environments" is useless unless someone can say what
differed.

So the stamp is not a hash of something ephemeral. It is the hash of
`env-manifest.txt`, a plain-text document listing the interpreter, every
installed package, and every file in `bin/` with its own digest. The
document contains no timestamp and no path, so the same environment
produces the same bytes on any day in any container; and because each
stamped manifest is archived under its own hash, the difference between
two `env_version` values is a diff anyone can run months later.

The previous stamp hashed `pip freeze` alone. Under that rule, adding a
compiled binary to `bin/` — a change that alters what the tools *do* —
produced no change in the value that claims to identify the environment.
An identifier that misses the change you made is worse than no
identifier, because artifacts then carry a false claim of comparability.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import platform
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from .paths import confine_path, sanitize_slug

#: The document whose hash is ENV_VERSION.
MANIFEST = "env-manifest.txt"
#: The stamp itself, read by core.env.env_version().
STAMP = "ENV_VERSION"
#: Append-only log of stamps, oldest first.
HISTORY = "ENV_HISTORY"
#: pip-installable subset of the manifest, for humans and for pip.
LOCK = "requirements.lock"
#: Every manifest ever stamped, keyed by its own hash.
ARCHIVE = "manifests"
#: The commit the provisioning tree was on when the stamp was written.
#: Deliberately *beside* the manifest rather than inside it — see
#: `source_commit`.
SOURCE = "ENV_SOURCE"
#: Snapshot of host-requirements.txt, written beside the stamp so
#: `env plan` can report when host prerequisites change.  Not part of
#: the manifest: host requirements affect the cost of bootstrapping,
#: not what the tools compute (#18).
HOST_REQUIREMENTS = "HOST_REQUIREMENTS"
#: Source-tree filename for the declared host requirements.
HOST_REQUIREMENTS_SOURCE = "host-requirements.txt"

_HEADER = (
    "# dde environment manifest",
    "# The sha256 of this file is ENV_VERSION, the value stamped into every",
    "# artifact sidecar. It holds no timestamp and no absolute path, so the",
    "# same environment hashes the same on any day in any container.",
)


def _packages() -> list[str]:
    """`name==version` for every distribution the running interpreter sees.

    Read from importlib.metadata rather than by shelling out to `pip
    freeze`, so the manifest is produced by the same process that hashes
    it. Two producers of one canonical document is one producer too
    many.
    """
    found: dict[str, str] = {}
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name:
            continue
        found[name.lower()] = f"{name}=={dist.version}"
    return [found[key] for key in sorted(found)]


def _binaries(bin_dir: Path) -> list[str]:
    """`name  sha256  size` for every file in bin/, sorted by name.

    Every file, not every executable: a data file sitting beside a
    binary changes what that binary does, and the bit that says whether
    it is executable is not the interesting part.
    """
    if not bin_dir.is_dir():
        return []
    rows: list[str] = []
    for path in sorted(bin_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{path.name}\t{digest}\t{path.stat().st_size}")
    return rows


def _git(*args: str, cwd: Path) -> str | None:
    """Run a read-only git command, or None if it cannot be answered.

    Never raises. A missing git, a directory that is not a repository,
    and a command that failed are all the same answer here — "cannot be
    determined" — and the callers must distinguish that from "no", which
    is why this returns None rather than "".
    """
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def source_tree() -> Path:
    """The checkout this code is running from."""
    # dde/core/envstamp.py → dde/core → dde → tools
    return Path(__file__).resolve().parent.parent.parent


def source_commit(tree: Path | None = None) -> dict[str, object]:
    """Which commit the provisioning tree is on, and whether others can get it.

    **This is not part of the manifest, and must not be.** ENV_VERSION
    identifies the environment: the interpreter, the packages, the
    binaries. Folding the source commit into it would make every
    documentation commit bump the value and partition the program's
    artifacts over a change that alters nothing an artifact depends on.
    That is the exact mirror of the defect this module was written to
    fix — an identifier that fires on what does not matter is as useless
    as one that misses what does, and it is worse in practice, because
    people learn to ignore it.

    The commit is recorded *beside* the stamp instead, because it
    answers a different question: not "what is this environment?" but
    "can anyone else obtain the thing that built it?"

    `on_origin` is the one that matters. The shared volume is immediate
    and the repository is eventual, so a stamp written from an unpushed
    tree describes an environment built from something nobody else can
    fetch. Artifacts carrying it are not wrong, but they are not
    re-derivable, which is the property the two-phase contract exists to
    provide.

    `dirty` and `dirty_inputs` are not the same question, and only the
    second is worth waking anyone for. Five agents share this working
    tree, so at any moment somebody has an uncommitted file in it, and
    warning on that would light permanently for changes the reader did
    not make and cannot fix. Whether a checker script is committed has
    no bearing on whether this environment can be rebuilt. Whether
    `install.sh` is committed has every bearing on it.

    Every field is None when it cannot be determined. None is not False:
    a caller that treats "no repository here" as "not on origin" reports
    a failure it did not observe.
    """
    tree = tree or source_tree()
    head = _git("rev-parse", "HEAD", cwd=tree)
    if head is None:
        return {
            "commit": None,
            "dirty": None,
            "dirty_inputs": None,
            "on_origin": None,
            "remote_ref": None,
        }

    status = _git("status", "--porcelain", "--", ".", cwd=tree)
    dirty = None if status is None else bool(status)

    inputs = _git("status", "--porcelain", "--", *PROVISIONING_INPUTS, cwd=tree)
    dirty_inputs = (
        None
        if inputs is None
        else [line[3:].strip() for line in inputs.splitlines() if line.strip()]
    )

    # Reachability is checked against the remote-tracking refs this
    # container already has. That is a weaker claim than asking the
    # remote, and the weakness is stated rather than hidden: a stale
    # tracking ref can report a pushed commit as unreachable. The failure
    # direction is the safe one — it complains about a commit that is
    # fine, rather than passing one that nobody can fetch.
    #
    # All refs under refs/remotes/origin/ are walked, not just
    # origin/main — the provisioning tree may live on any branch (e.g.
    # origin/DDE), and restricting the check to main produces a false
    # failure on a working environment (#45).
    on_origin: bool | None = None
    remote_ref: str | None = None
    refs_output = _git(
        "for-each-ref", "--format=%(refname)", "refs/remotes/origin/", cwd=tree
    )
    if refs_output is not None:
        refs = [r.strip() for r in refs_output.splitlines() if r.strip()]
        if refs:
            on_origin = False
            for ref in refs:
                # Translate full refname to the short form git commands
                # expect (e.g. "refs/remotes/origin/DDE" → "origin/DDE").
                short_ref = ref.replace("refs/remotes/", "", 1)
                try:
                    probe = subprocess.run(
                        ["git", "merge-base", "--is-ancestor", head, short_ref],
                        cwd=str(tree),
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue
                if probe.returncode == 0:
                    on_origin = True
                    remote_ref = short_ref
                    break
        # else: on_origin stays None — no refs to check against

    return {
        "commit": head,
        "dirty": dirty,
        "dirty_inputs": dirty_inputs,
        "on_origin": on_origin,
        "remote_ref": remote_ref,
    }


#: Files whose content determines what a provisioning run produces. A
#: change to any of them between the stamped commit and the running tree
#: means the environment on the volume was built by different
#: instructions than the ones in front of you.
PROVISIONING_INPUTS = (
    "install.sh",
    "requirements.txt",
    "requirements-hypex.txt",
    "requirements-science.txt",
    "vendor/hypex",
)


def provisioning_drift(stamped: str, tree: Path | None = None) -> list[str] | None:
    """Which provisioning inputs changed since `stamped` built this environment.

    Reachability answers "can anyone fetch the commit this was built
    from". It does not answer "is that still how we build it", and those
    diverge the moment someone edits `install.sh` without re-provisioning
    — at which point the volume holds binaries built by instructions
    nobody is reading any more. Existence and currency are different
    questions; the second is the one that provisions cleanly with the
    wrong contents.

    Deliberately narrow. Every commit moves HEAD, and warning on that
    would fire constantly and be ignored within a day. Only a change to
    a file that determines what provisioning *produces* is worth a
    reader's attention.

    Returns [] for no drift, a list of paths for drift, and None when the
    question cannot be answered — an unknown commit, no repository, a
    shallow clone. None is not [].
    """
    tree = tree or source_tree()
    if _git("cat-file", "-e", f"{stamped}^{{commit}}", cwd=tree) is None:
        return None
    changed = _git(
        "diff", "--name-only", stamped, "HEAD", "--", *PROVISIONING_INPUTS, cwd=tree
    )
    if changed is None:
        return None
    return [line.strip() for line in changed.splitlines() if line.strip()]


def read_source(home: Path) -> dict[str, object] | None:
    """The source record written beside the stamp, if there is one."""
    path = home / SOURCE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_manifest(home: Path) -> str:
    """The canonical description of the environment rooted at `home`."""
    lines: list[str] = [*_HEADER, "", "[interpreter]"]
    lines.append(f"python\t{platform.python_version()}\t{platform.machine()}")
    lines += ["", "[packages]", *_packages()]
    lines += ["", "[binaries]", *_binaries(home / "bin")]
    return "\n".join(lines) + "\n"


def counts(manifest: str) -> dict[str, int]:
    """How many packages and binaries a manifest describes.

    Parsed by section header rather than by guessing from line shape:
    a package line and a binary line are both tab-or-`==` separated
    text, and counting them by pattern is how a summary starts lying
    after someone adds a third section.
    """
    section = ""
    tally = {"packages": 0, "binaries": 0}
    for line in manifest.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if section in tally:
            tally[section] += 1
    return tally


def version_of(manifest: str) -> str:
    return "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def short(version: str | None) -> str:
    if not version:
        return "(none)"
    body = version.split(":", 1)[-1]
    return f"{version.split(':', 1)[0]}:{body[:12]}…" if len(body) > 12 else version


@dataclass(frozen=True)
class EnvState:
    """The stamp on disk, the manifest that produced it, and the live one."""

    home: Path
    stamped: str | None
    recorded: str | None  # manifest text as stamped
    live: str  # manifest text as the environment is right now

    @property
    def live_version(self) -> str:
        return version_of(self.live)

    @property
    def recorded_version(self) -> str | None:
        return version_of(self.recorded) if self.recorded is not None else None

    @property
    def provisioned(self) -> bool:
        return self.stamped is not None

    @property
    def drifted(self) -> bool:
        """Does the environment differ from what its own stamp describes?

        A drifted environment is the failure this module exists to make
        visible: artifacts are being written with an `env_version` that
        no longer describes the tools that produced them, which is a
        false provenance record rather than a missing one.
        """
        return self.provisioned and self.stamped != self.live_version

    @property
    def stamp_stale(self) -> bool:
        """ENV_VERSION disagrees with the manifest archived beside it."""
        return (
            self.provisioned
            and self.recorded_version is not None
            and self.stamped != self.recorded_version
        )

    def changes(self) -> list[str]:
        """Unified diff from the recorded manifest to the live one."""
        if self.recorded is None:
            return []
        return diff_manifests(self.recorded, self.live)


def read_state(home: Path) -> EnvState:
    stamp_path = home / STAMP
    manifest_path = home / MANIFEST
    stamped = None
    if stamp_path.is_file():
        stamped = stamp_path.read_text(encoding="utf-8").strip() or None
    recorded = None
    if manifest_path.is_file():
        recorded = manifest_path.read_text(encoding="utf-8")
    return EnvState(
        home=home, stamped=stamped, recorded=recorded, live=build_manifest(home)
    )


def diff_manifests(before: str, after: str) -> list[str]:
    """Changed lines only, comments and context dropped."""
    out: list[str] = []
    for line in difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=0
    ):
        if line.startswith(("---", "+++", "@@")):
            continue
        body = line[1:].strip()
        if not body or body.startswith("#") or body.startswith("["):
            continue
        out.append(f"{line[0]} {body}")
    return out


_HEX = re.compile(r"\b[0-9a-f]{64}\b")


def display(changes: list[str]) -> list[str]:
    """Diff lines with digests shortened, for reading rather than hashing.

    The manifest keeps full digests because that is what is hashed. A
    64-character hex string in a doctor line is not information to a
    reader; it is what makes them stop reading doctor lines.
    """
    return [_HEX.sub(lambda m: m.group(0)[:12] + "…", line) for line in changes]


def summarize(changes: list[str]) -> str:
    """One line naming what moved, for the history log."""
    if not changes:
        return "no change"
    added = [c[2:] for c in changes if c.startswith("+")]
    removed = [c[2:] for c in changes if c.startswith("-")]
    names = sorted({entry.split("\t")[0].split("==")[0] for entry in added + removed})
    head = ", ".join(names[:6])
    if len(names) > 6:
        head += f", +{len(names) - 6} more"
    return f"{len(added)} added / {len(removed)} removed: {head}"


# ---- host requirements ---------------------------------------------------


def parse_host_requirements(text: str) -> list[tuple[str, str, str]]:
    """Parse host-requirements.txt into ``(category, name, reason)`` tuples.

    Blank lines and comment lines (starting with ``#``) are skipped.
    Each data line is ``category  name  reason`` separated by whitespace.
    """
    entries: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 2)
        if len(parts) >= 2:
            entries.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return entries


def read_host_requirements_source(tree: Path | None = None) -> str | None:
    """Read the declared host-requirements file from the source tree."""
    tree = tree or source_tree()
    path = tree / HOST_REQUIREMENTS_SOURCE
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def read_host_requirements_stamped(home: Path) -> str | None:
    """Read the host-requirements snapshot written at stamp time."""
    path = home / HOST_REQUIREMENTS
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def diff_host_requirements(before: str | None, after: str | None) -> dict[str, object]:
    """Compare two host-requirements texts.

    Returns a dict with ``added``, ``removed``, ``changed``, and
    ``before_count`` / ``after_count``.  When *before* is ``None``
    (no prior snapshot), everything in *after* is reported as added.
    """
    before_entries = (
        set()
        if before is None
        else {(cat, name) for cat, name, _ in parse_host_requirements(before)}
    )
    after_entries = (
        set()
        if after is None
        else {(cat, name) for cat, name, _ in parse_host_requirements(after)}
    )
    added = sorted(after_entries - before_entries)
    removed = sorted(before_entries - after_entries)
    return {
        "added": [f"{cat}  {name}" for cat, name in added],
        "removed": [f"{cat}  {name}" for cat, name in removed],
        "changed": bool(added or removed),
        "before_count": len(before_entries),
        "after_count": len(after_entries),
    }


def stamp(home: Path, note: str | None = None) -> dict[str, object]:
    """Write the manifest, the stamp, the lockfile, the archive and history.

    Returns a record of what changed, so the caller can print it rather
    than reporting success for an operation whose interesting content is
    the difference.
    """
    home.mkdir(parents=True, exist_ok=True)
    state = read_state(home)
    manifest = state.live
    version = version_of(manifest)
    changes = state.changes()

    (home / MANIFEST).write_text(manifest, encoding="utf-8")
    (home / STAMP).write_text(version + "\n", encoding="utf-8")

    packages = [line for line in manifest.splitlines() if "==" in line]
    (home / LOCK).write_text("\n".join(packages) + "\n", encoding="utf-8")

    archive = home / ARCHIVE
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"{version.replace(':', '-')}.txt").write_text(
        manifest, encoding="utf-8"
    )

    # Written on every stamp, including an unchanged one: the source
    # tree can move without the environment moving, and the question
    # "which commit provisioned what is on this volume now" has to stay
    # answerable in that case too.
    source = source_commit()
    source["stamped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source["env_version"] = version
    (home / SOURCE).write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")

    # Snapshot host requirements beside the stamp so env plan can
    # detect when bootstrap prerequisites change (#18).
    host_req_text = read_host_requirements_source()
    if host_req_text is not None:
        (home / HOST_REQUIREMENTS).write_text(host_req_text, encoding="utf-8")

    changed = version != state.stamped
    if changed:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if state.recorded is None:
            tally = counts(manifest)
            what = (
                f"initial stamp: {tally['packages']} package(s), "
                f"{tally['binaries']} file(s) in bin/"
            )
        else:
            what = summarize(changes)
        entry = f"{when}\t{version}\t{what}"
        if source.get("commit"):
            marker = str(source["commit"])[:12]
            if source.get("dirty"):
                marker += "+dirty"
            entry += f"\tsrc={marker}"
        if note:
            entry += f"\t{note}"
        with (home / HISTORY).open("a", encoding="utf-8") as handle:
            handle.write(entry + "\n")

    return {
        "env_version": version,
        "previous": state.stamped,
        "changed": changed,
        "changes": changes,
        "manifest": str(home / MANIFEST),
        "source": source,
    }


def archived(home: Path, version: str) -> str | None:
    """The manifest text for a stamped version, by full value or prefix."""
    archive = home / ARCHIVE
    if not archive.is_dir():
        return None
    wanted = sanitize_slug(version.replace(":", "-"))
    exact = confine_path(archive, Path(f"{wanted}.txt"))
    if exact is None:
        return None
    if exact.is_file():
        return exact.read_text(encoding="utf-8")
    matches = sorted(p for p in archive.glob("*.txt") if p.stem.startswith(wanted))
    if len(matches) == 1:
        return matches[0].read_text(encoding="utf-8")
    return None


def history(home: Path) -> list[tuple[str, str, str]]:
    """(timestamp, version, summary) oldest first."""
    path = home / HISTORY
    if not path.is_file():
        return []
    rows: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], "\t".join(parts[2:])))
    return rows
