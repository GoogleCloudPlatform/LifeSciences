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

"""`dde doctor` — assert the environment before an agent trusts it.

Run at agent start, from every template. It converts a missing tool from
"the agent invents a plausible number" into "the agent reports a blocked
task" (docs/tool-design-guidance.md §2).

Credentials resolve through the CLI only. We test for presence, never
for value, and never print one.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

import click

from ..common import AppState, pass_state
from ..core import env, envstamp
from ..core.errors import DDEError
from ..core.thresholds import UNRESOLVED, declared_sets
from ..core.toolchain import check_integrity

OK = "ok"
INFO = "info"
WARN = "warn"
FAIL = "fail"

# A second axis, orthogonal to status. Status says what was observed;
# kind says what a warning MEANS for the reader's next command, which is
# the question they actually have. Eight skills currently tell an agent
# to "run dde doctor to confirm the environment" — a binary
# instruction against graded output, which leaves the reader to guess
# whether 14 warnings is a reason to stop. It is not, and the tool
# should say so rather than each skill carrying a copy of the answer.
CAPABILITY = "capability"  # a command you meant to run will refuse
CAVEAT = "caveat"  # you can run it; this changes how the result reads
HOUSEKEEPING = "housekeeping"  # the tooling lead's problem, not the reader's


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    remedy: str = ""
    #: What a WARN on this line means for the reader's next command. See
    #: KINDS. Ignored when the status is OK.
    kind: str = CAVEAT


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(
        self,
        name: str,
        status: str,
        detail: str = "",
        remedy: str = "",
        kind: str = CAVEAT,
    ) -> None:
        self.checks.append(Check(name, status, detail, remedy, kind))

    def warnings_of(self, kind: str) -> list[Check]:
        return [c for c in self.checks if c.status == WARN and c.kind == kind]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]


# --- individual checks -----------------------------------------------------


def _check_python(report: Report) -> None:
    report.add(
        "python",
        OK,
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} "
        f"({env.interpreter_tag()})",
    )


def _check_toolchain_integrity(report: Report) -> None:
    """Is the CLI running from unmodified source?

    Issue #127: specialists patched installed DDE source mid-run.
    Sidecars generated afterward claimed unmodified cli_version.
    This check makes the condition visible at doctor time.
    """
    tc = check_integrity()

    if tc.integrity == "installed":
        report.add(
            "toolchain integrity",
            OK,
            "running from installed package, source integrity not verifiable",
        )
        return

    if tc.integrity == "unknown":
        report.add(
            "toolchain integrity",
            WARN,
            "could not determine source state (git not available or failed)",
            "install git or run from a git checkout to enable integrity checking",
            kind=HOUSEKEEPING,
        )
        return

    if tc.modified:
        file_list = (
            ", ".join(tc.modified_files[:10]) if tc.modified_files else "(unknown)"
        )
        suffix = (
            f" … and {len(tc.modified_files) - 10} more"
            if len(tc.modified_files) > 10
            else ""
        )
        report.add(
            "toolchain integrity",
            WARN,
            f"DDE source has uncommitted modifications ({tc.integrity}): "
            f"{file_list}{suffix}",
            "artifacts produced now will carry cli_modified: true in their "
            "sidecar. Commit or stash the changes, or set DDE_NO_DIRTY_WARNING=1 "
            "if this is intentional development",
            kind=HOUSEKEEPING,
        )
        return

    report.add("toolchain integrity", OK, f"clean ({tc.integrity})")


def _check_environment(report: Report) -> None:
    if env.is_provisioned():
        report.add("tools environment", OK, f"{env.tools_home()} @ {env.env_version()}")
    else:
        report.add(
            "tools environment",
            WARN,
            f"not provisioned; {env.tools_home()}/ENV_VERSION absent",
            "artifacts will be stamped with an unpinned developer env_version; "
            "provision the shared volume before producing citable results",
        )


def _check_env_drift(report: Report) -> None:
    """Does the environment still match the stamp it is writing into sidecars?

    `_check_environment` reports that a stamp exists. Existing is not the
    property that matters: a stamp describing an environment that has
    since changed puts a false `env_version` on every artifact written
    under it, which is worse than the honest `unpinned-dev` fallback,
    because the false one is comparable-looking.

    Recomputed from the live interpreter and the live `bin/` rather than
    trusted, for the same reason the phase-2 latch is a latch and not a
    convention: an environment nobody can accidentally change is not the
    environment we have.
    """
    if not env.is_provisioned():
        return  # already reported as unprovisioned; one finding, not two
    state = envstamp.read_state(env.tools_home())
    if state.stamp_stale:
        report.add(
            "environment stamp",
            FAIL,
            f"ENV_VERSION ({envstamp.short(state.stamped)}) does not match the "
            f"manifest beside it ({envstamp.short(state.recorded_version)})",
            "run `dde env stamp` to rewrite both from the environment as it is",
        )
        return
    if not state.drifted:
        report.add(
            "environment stamp",
            OK,
            f"{envstamp.short(state.stamped)} matches the live environment",
        )
        return
    changes = state.changes()
    detail = "; ".join(envstamp.display(changes[:3])) or "manifest differs"
    report.add(
        "environment stamp",
        FAIL,
        f"environment has drifted from its stamp ({len(changes)} change(s)): {detail}",
        "artifacts written now record an env_version that misdescribes them; "
        "run `dde env plan` to see the difference and `dde env stamp` to "
        "record it, or restore the environment the stamp describes",
    )


def _report_provisioning_drift(report: Report, commit: object) -> None:
    """Has the way we provision changed since this environment was built?

    Silent on the common case by design. Every commit moves HEAD and
    almost none of them change what a provisioning run produces; a check
    that fired on all of them would be ignored inside a day, and then
    the true findings on this page go with it. So it speaks only when a
    file that determines the *output* of provisioning has moved.

    WARN rather than FAIL, for the same reason. The moment I edit
    `install.sh` this becomes true for every agent on the volume, and
    turning everyone's doctor red for a change they did not make and
    cannot fix is how a page stops being read.
    """
    if not commit:
        return
    changed = envstamp.provisioning_drift(str(commit))
    if not changed:
        return  # [] is current; None is unanswerable, and the caller already said so
    report.add(
        "provisioning currency",
        WARN,
        "changed since this environment was built: " + ", ".join(changed),
        "the volume holds an environment built by an older version of these "
        "files. Re-run `tools/install.sh` against this tree, or read the diff "
        "and confirm it does not affect what is installed",
        kind=HOUSEKEEPING,
    )


def _check_env_source(report: Report) -> None:
    """Can anyone else obtain the tree this environment was provisioned from?

    The shared volume is immediate and the repository is eventual, so
    those two are capable of disagreeing and nothing used to notice. An
    environment stamped from an unpushed tree is not wrong — it is
    *unreproducible*, which is the one property the two-phase contract
    exists to provide. That is the anti-fabrication rule applied to the
    environment rather than to a finding.

    Three outcomes, not two, and the third is the point. `on_origin` is
    None when there is no repository to ask, and None is not False: a
    specialist's container running from the volume alone cannot observe
    this, and a check that reports OK for having examined nothing is the
    defect three of us shipped today in three different tools.

    Reachability is only half. It asks whether the commit can be
    fetched, not whether it is still how we provision, and those two
    part company the moment someone edits `install.sh` without
    re-provisioning. That failure is worse than an unreachable commit,
    because an unreachable commit fails loudly here while a stale one
    passes every check on this page and hands the reader an environment
    built by instructions nobody is reading any more.
    """
    if not env.is_provisioned():
        return  # one finding, not two
    record = envstamp.read_source(env.tools_home())
    if record is None:
        report.add(
            "environment source",
            WARN,
            "the stamp records no provisioning commit (written before ENV_SOURCE existed)",
            "re-run `dde env stamp` from a checkout to record it; until then "
            "the tree that built this environment cannot be identified",
            kind=HOUSEKEEPING,
        )
        return

    commit = record.get("commit")
    short = str(commit)[:12] if commit else "unknown"
    if record.get("on_origin") is True:
        detail = f"provisioned from {short}, reachable on {record.get('remote_ref')}"
        unclean = record.get("dirty_inputs")
        if unclean:
            # WARN, not FAIL. An unreachable commit is unfetchable full
            # stop; an uncommitted install.sh only *might* have built
            # something a rebuild would not reproduce. Weaker evidence,
            # and a remedy the reader can act on.
            report.add(
                "environment source",
                WARN,
                detail
                + ", but these were uncommitted when stamped: "
                + ", ".join(unclean),
                "the commit is fetchable; the instructions that built this "
                "environment are not. Commit and push, then `dde env stamp`",
                kind=HOUSEKEEPING,
            )
            return
        if unclean is None and record.get("dirty"):
            # Pre-`dirty_inputs` record: the stamp knows the tree was
            # dirty but not which files, so the strong claim cannot be
            # made either way. Say which one this is.
            report.add(
                "environment source",
                WARN,
                detail + ", but the tree was dirty when stamped and the record "
                "predates per-file detail",
                "re-run `dde env stamp` to record whether the uncommitted "
                "files were provisioning inputs or unrelated work",
                kind=HOUSEKEEPING,
            )
            return
        report.add("environment source", OK, detail)
        _report_provisioning_drift(report, commit)
        return

    if record.get("on_origin") is False:
        report.add(
            "environment source",
            WARN,
            f"provisioned from {short}, which is not reachable on "
            f"{record.get('remote_ref')}",
            "the environment works, but artifacts carry a provenance commit "
            "nobody else can fetch. Push that commit (or `git fetch` if this "
            "container's view of origin is stale — the check reads the local "
            "remote-tracking ref). Use `dde doctor --strict` to treat this as "
            "a blocking failure",
            kind=HOUSEKEEPING,
        )
        return

    report.add(
        "environment source",
        WARN,
        f"provisioned from {short}; reachability not checked (no repository here)",
        "run this from a checkout of the tools tree to verify the commit is "
        "on origin — this container cannot answer it",
        kind=HOUSEKEEPING,
    )


def _check_project(report: Report, state: AppState) -> None:
    try:
        ctx = state.project()
    except DDEError as exc:
        detail = exc.message
        if exc.detail:
            detail = f"{detail} — {exc.detail}"
        report.add("project root", FAIL, detail, exc.remedy or "")
        return

    if ctx.source == "DDE_PROJECT":
        detail = f"{ctx.root} (resolved from DDE_PROJECT environment variable)"
    elif ".dde" in ctx.source:
        detail = f"{ctx.root} (auto-detected .dde/ marker by walking up from CWD)"
    else:
        detail = f"{ctx.root} (via {ctx.source})"
    report.add("project root", OK, detail)


#: Bound on the sidecar walk. A doctor run must stay fast enough that
#: templates keep calling it at agent start. If a project is larger than
#: this the cap is reported rather than silently applied — a partial scan
#: presented as a whole one is the failure this file exists to prevent.
_SIDECAR_SCAN_CAP = 2000


def _check_env_partition(report: Report, state: AppState) -> None:
    """Does this project's existing evidence come from one environment?

    `ENV_VERSION` is stamped into every sidecar precisely so that results
    produced before an environment change stay distinguishable from those
    produced after. That only helps if somebody can see the split, and
    nothing surfaced it: each sidecar was individually correct, and the
    partition existed only across files nobody compared.

    Adding one binary to `bin/` bumps the hash for the whole program
    (tool-design-guidance §2 Rule 4). The cost of adding a tool is not the
    build, it is the partition — so the partition is reported here, at
    agent start, rather than discovered when two findings disagree.
    """
    try:
        root = state.project().root
    except DDEError:
        return  # _check_project already reported it; do not report twice.

    raw = root / "raw"
    if not raw.is_dir():
        report.add("environment partition", OK, "no artifacts yet")
        return

    current = env.env_version()
    counts: dict[str, int] = {}
    unreadable: list[str] = []
    scanned = 0
    truncated = False

    for path in sorted(raw.rglob("*.meta.json")):
        if scanned >= _SIDECAR_SCAN_CAP:
            truncated = True
            break
        scanned += 1
        try:
            recorded = json.loads(path.read_text())["env_version"]
        except (OSError, ValueError, KeyError):
            unreadable.append(path.name)
            continue
        counts[recorded] = counts.get(recorded, 0) + 1

    if unreadable:
        report.add(
            "sidecar integrity",
            WARN,
            f"{len(unreadable)} sidecar(s) unreadable or missing env_version: "
            + ", ".join(sorted(unreadable)[:5])
            + (" …" if len(unreadable) > 5 else ""),
            "a sidecar that cannot be read cannot support a citation; re-run "
            "the fetch that should have written it rather than citing the "
            "artifact beside it",
            kind=HOUSEKEEPING,
        )

    if not counts:
        report.add("environment partition", OK, "no artifacts yet")
        return

    detail_counts = ", ".join(
        f"{version} ({n} artifact{'s' if n != 1 else ''})"
        f"{' — current' if version == current else ''}"
        for version, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    suffix = f"; scan capped at {_SIDECAR_SCAN_CAP} sidecars" if truncated else ""

    if list(counts) == [current]:
        report.add(
            "environment partition",
            OK,
            f"{sum(counts.values())} artifact(s), one environment{suffix}",
        )
        return

    report.add(
        "environment partition",
        WARN,
        f"this project spans {len(counts)} environment version(s): "
        f"{detail_counts}{suffix}",
        "results either side of an environment change are not directly "
        "comparable, and nothing here is corrupt — this is the stamp working. "
        "Never re-stamp an old sidecar. Where comparability matters, re-run "
        "`analyze` against the stored Layer 0 bytes, which regenerates the "
        "verdict under the current environment; a phase-1 payload cannot be "
        "re-dated and must be re-fetched to move it across the boundary",
        kind=HOUSEKEEPING,
    )


def _check_packages(report: Report) -> None:
    required = {
        "click": "CLI framework",
        "requests": "HTTP client for AFDB and other REST sources",
        # Both are required, not optional: AlphaGenome returns base64
        # zstd-compressed binary tensors, so without them there is no
        # path from a response to a number at all.
        "numpy": "decoding AlphaGenome tensors",
        "zstandard": "decompressing AlphaGenome tensor chunks",
    }
    optional = {
        "yaml": "program threshold overrides (.dde/thresholds.yaml)",
        "google.cloud.aiplatform": "AlphaFold 3 and AlphaGenome Vertex endpoints",
        "alphagenome": "AlphaGenome pip backend (ISM)",
        "rdkit.Chem": "compound validation, descriptors and structural alerts",
        "meeko": "PDBQT preparation for docking (mk_prepare_receptor.py, mk_prepare_ligand.py)",
    }
    for module, purpose in required.items():
        try:
            importlib.import_module(module)
            report.add(f"package {module}", OK, purpose)
        except ImportError:
            report.add(
                f"package {module}",
                FAIL,
                f"not importable — {purpose}",
                "install it into the tools environment",
            )
    for module, purpose in optional.items():
        try:
            importlib.import_module(module)
            report.add(f"package {module}", OK, purpose)
        except ImportError:
            report.add(
                f"package {module}",
                WARN,
                f"not installed — {purpose} unavailable",
                "install it if the corresponding subcommands are needed",
                kind=CAPABILITY,
            )


#: Binaries provisioned into the shared volume by install.sh, with what
#: goes missing when they do. Checked here as well as hashed into
#: ENV_VERSION: the hash tells you *which* fpocket produced a result,
#: this tells the agent whether there is one at all before it plans work
#: around a tool that is not there.
#:
#: Each entry is ``(purpose, remedy)``. Every declared tool is provisioned by
#: DDE; availability is an observed runtime state, not a release-plan flag.
_PROVISIONED_BINARIES = {
    "fpocket": (
        "pocket detection — `dde pocket run` cannot answer tractability",
        "re-provision with `tools/install.sh --binaries-only`",
    ),
    "vina": (
        "docking — structural-biologist and computational-chemist skills",
        "re-provision with `tools/install.sh --binaries-only`",
    ),
    "hypex": (
        "hypothesis-explorer datastore lifecycle — tournament management "
        "(init-run, add-hypothesis, add-match, validate)",
        "re-provision from pinned source with `tools/install.sh --binaries-only`",
    ),
    "elo": (
        "ELO rating engine — pairwise rankings and per-epoch standings "
        "for hypothesis tournaments",
        "re-provision from pinned source with `tools/install.sh --binaries-only`",
    ),
    "prox": (
        "proximity / similarity — hypothesis clustering for tournament "
        "pairing and merge recommendations",
        "re-run a full `tools/install.sh --update` to provision prox and its Python dependencies",
    ),
    "mk_prepare_receptor.py": (
        "receptor PDBQT preparation for docking (installed by meeko)",
        "install meeko and gemmi into the tools environment "
        "(pip install meeko>=0.5 gemmi>=0.7)",
    ),
    "mk_prepare_ligand.py": (
        "ligand PDBQT preparation for docking (installed by meeko)",
        "install meeko and gemmi into the tools environment "
        "(pip install meeko>=0.5 gemmi>=0.7)",
    ),
}

#: Meeko scripts that need capability validation beyond PATH presence.
#: A shim on PATH is a proxy for the capability it wraps: it is true
#: precisely when the underlying package may or may not work — the same
#: defect class as the inverted ``import venv`` check in
#: ``bootstrap-preflight.sh`` (tool-design-guidance.md §8). The shim
#: exists because pip installed it; whether the package it calls into
#: actually imports is a separate question the shim cannot answer.
_CAPABILITY_VALIDATED = {
    "hypex",
    "elo",
    "prox",
    "mk_prepare_receptor.py",
    "mk_prepare_ligand.py",
}

_HELP_MARKERS = {
    "hypex": "Usage:",
    "elo": "Usage:",
    "prox": "Commands:",
}


def _validate_script_capability(
    report: Report,
    binary: str,
    path: str,
    remedy: str,
) -> None:
    """Run ``<script> --help`` and report FAIL if it cannot execute.

    ``--help`` exercises the script's top-level imports (``from
    meeko.cli.… import main``) and exits 0 when the package is intact.
    If meeko or gemmi is missing the script dies with an ImportError
    before reaching the argument parser — that traceback is the real
    signal, not the presence of a file on PATH.
    """
    try:
        result = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        report.add(
            f"binary {binary}",
            WARN,
            f"on PATH at {path} but --help did not complete within 10 s",
            "the script may be hung; check it manually",
            kind=CAPABILITY,
        )
        return
    except OSError as exc:
        report.add(
            f"binary {binary}",
            FAIL,
            f"on PATH at {path} but cannot execute: {exc}",
            remedy,
        )
        return

    expected_help = _HELP_MARKERS.get(binary)
    if result.returncode == 0 and (
        expected_help is None or expected_help in result.stdout
    ):
        report.add(f"binary {binary}", OK, path)
        return

    # Extract the specific import error from the traceback when possible,
    # so the failure message names the missing package rather than just
    # saying "check failed".
    error_detail = ""
    for line in reversed(result.stderr.strip().splitlines()):
        if "ModuleNotFoundError" in line or "ImportError" in line:
            error_detail = line.strip()
            break
    if not error_detail and result.returncode == 0 and expected_help:
        error_detail = f"--help output did not contain {expected_help!r}"
    if not error_detail:
        # Fall back to the last non-empty line of stderr.
        for line in reversed(result.stderr.strip().splitlines()):
            stripped = line.strip()
            if stripped:
                error_detail = stripped[:200]
                break
    report.add(
        f"binary {binary}",
        FAIL,
        f"on PATH at {path} but cannot run: {error_detail}"
        if error_detail
        else f"on PATH at {path} but exited {result.returncode}",
        remedy,
    )


def _check_binaries(report: Report) -> None:
    for binary, purpose in {"gcloud": "GCP auth for Vertex endpoints"}.items():
        path = shutil.which(binary)
        if path:
            report.add(f"binary {binary}", OK, path)
        else:
            report.add(
                f"binary {binary}",
                WARN,
                f"not on PATH — {purpose}",
                "install the Google Cloud SDK if Vertex endpoints are needed",
                kind=CAPABILITY,
            )

    for binary, (purpose, remedy) in _PROVISIONED_BINARIES.items():
        path = shutil.which(binary)
        if path is None:
            candidate = env.tools_home() / "bin" / binary
            path = str(candidate) if candidate.is_file() else None
            if path:
                report.add(
                    f"binary {binary}",
                    WARN,
                    f"present at {path} but not on PATH",
                    f"source {env.tools_home()}/env.sh before invoking tools that shell out",
                    kind=CAPABILITY,
                )
                continue
        if not path:
            report.add(
                f"binary {binary}", WARN, f"not found — {purpose}", remedy, CAPABILITY
            )
        elif binary in _CAPABILITY_VALIDATED:
            _validate_script_capability(report, binary, path, remedy)
        else:
            report.add(f"binary {binary}", OK, path)


def _check_credentials(report: Report) -> None:
    """Check credentials. GCP: try ADC resolution. Others: env-var presence."""
    # --- ALPHAGENOME_API_KEY: env var, then ADC fallback ---
    if os.environ.get("ALPHAGENOME_API_KEY"):
        report.add("credential ALPHAGENOME_API_KEY", OK, "present (API key)")
    else:
        # No API key — try ADC resolution (same pattern as GCP credential
        # check below).  Vertex backend authenticates via ADC, so the key
        # is not required when ADC resolves.
        try:
            import google.auth  # lazy import

            google.auth.default()
            report.add(
                "credential ALPHAGENOME_API_KEY",
                OK,
                "not set, but GCP ADC resolves (Vertex backend)",
            )
        except ImportError:
            report.add(
                "credential ALPHAGENOME_API_KEY",
                WARN,
                "not set and google-auth not installed — neither API key "
                "nor ADC available",
                "set ALPHAGENOME_API_KEY for the pip backend, or install "
                "google-auth and configure ADC for the Vertex backend",
                kind=CAPABILITY,
            )
        except Exception:
            report.add(
                "credential ALPHAGENOME_API_KEY",
                WARN,
                "not set and ADC resolution failed — neither auth path available",
                "set ALPHAGENOME_API_KEY for the pip backend, or configure "
                "ADC for the Vertex backend (gcloud auth application-default "
                "login, workload identity, etc.)",
                kind=CAPABILITY,
            )

    # --- NCBI_API_KEY: env var presence → key sent on requests ---
    if os.environ.get("NCBI_API_KEY"):
        report.add(
            "credential NCBI_API_KEY",
            OK,
            "present — sent on E-utilities requests (QPS 10)",
        )
    else:
        report.add(
            "credential NCBI_API_KEY",
            WARN,
            "not set — NCBI E-utilities queries are limited to 3 QPS; "
            "with the key the limit rises to 10",
            "set NCBI_API_KEY (https://ncbiinsights.ncbi.nlm.nih.gov"
            "/2017/11/02/new-api-keys-for-the-e-utilities/)",
            kind=HOUSEKEEPING,
        )

    # --- GCP credentials: try ADC resolution ---
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        report.add(
            "credential GCP",
            OK,
            "GOOGLE_APPLICATION_CREDENTIALS set (explicit service-account key)",
        )
        return

    try:
        import google.auth  # lazy import

        credentials, project = google.auth.default()
        cred_type = type(credentials).__name__
        report.add(
            "credential GCP",
            OK,
            f"ADC resolved via {cred_type}"
            + (f" (project: {project})" if project else ""),
        )
    except ImportError:
        report.add(
            "credential GCP",
            WARN,
            "GOOGLE_APPLICATION_CREDENTIALS not set and google-auth "
            "not installed — cannot check ADC",
            "install google-auth or set GOOGLE_APPLICATION_CREDENTIALS",
            kind=CAPABILITY,
        )
    except Exception as exc:
        report.add(
            "credential GCP",
            WARN,
            f"GOOGLE_APPLICATION_CREDENTIALS not set and ADC resolution failed: {exc}",
            "set GOOGLE_APPLICATION_CREDENTIALS or configure ADC "
            "(gcloud auth application-default login, workload identity, etc.)",
            kind=CAPABILITY,
        )


def _check_thresholds(report: Report) -> None:
    for _name, tset in sorted(declared_sets().items()):
        unresolved = [k for k, v in tset.values.items() if v is UNRESOLVED]
        if unresolved:
            report.add(
                f"thresholds {tset.tag}",
                WARN,
                f"unresolved: {', '.join(unresolved)}",
                "analyses depending on these will refuse to produce a verdict "
                "until the values are established from a cited source",
                kind=CAPABILITY,
            )
        else:
            report.add(f"thresholds {tset.tag}", OK, f"{len(tset.values)} declared")


def _check_pacing(report: Report) -> None:
    """Report which HTTP pacing tier is active."""
    from ..core.http import _PACE_DIR, _PACE_TIER

    if _PACE_TIER == "shared":
        report.add(
            "HTTP pacing",
            OK,
            f"shared — cross-container coordination active ({_PACE_DIR})",
            kind=CAPABILITY,
        )
    elif _PACE_TIER == "local":
        report.add(
            "HTTP pacing",
            WARN,
            f"container-local only — multi-agent fan-out will multiply rates ({_PACE_DIR})",
            kind=CAVEAT,
        )
    else:
        report.add(
            "HTTP pacing",
            WARN,
            f"in-memory only — no cross-invocation coordination (attempted: {_PACE_DIR})",
            kind=CAVEAT,
        )


def _check_gwas_catalog(report: Report) -> None:
    """Probe the GWAS Catalog REST API base URL for reachability.

    A lightweight connectivity check — hits the API root, not a full gene
    query. Reports CAVEAT (not CAPABILITY) because the GWAS Catalog being
    down does not prevent other tools from working.

    Added for issue #47: the ``associations/search/findByGene`` endpoint
    was removed; this check surfaces whether the API itself is reachable,
    separate from whether the specific endpoint exists.
    """
    try:
        import requests as _requests
    except ImportError:
        report.add(
            "gwas catalog api",
            WARN,
            "requests package not installed — cannot probe endpoint",
            "install requests to enable GWAS Catalog connectivity checks",
            kind=CAVEAT,
        )
        return

    api_url = "https://www.ebi.ac.uk/gwas/rest/api"
    try:
        resp = _requests.get(api_url, timeout=10)
        if resp.status_code == 200:
            report.add(
                "gwas catalog api",
                WARN,
                f"{api_url} reachable, but associations/search/findByGene "
                "endpoint has been removed by EBI",
                "use --source opentargets or --source clinvar for gene-disease "
                "association queries; --source gwas-catalog will fail with a "
                "clear error message",
                kind=CAVEAT,
            )
        else:
            report.add(
                "gwas catalog api",
                WARN,
                f"{api_url} returned HTTP {resp.status_code}",
                "the GWAS Catalog REST API may be degraded; "
                "--source gwas-catalog queries will likely fail",
                kind=CAVEAT,
            )
    except _requests.ConnectionError:
        report.add(
            "gwas catalog api",
            WARN,
            f"{api_url} unreachable (connection error)",
            "the GWAS Catalog REST API is not reachable from this "
            "environment; --source gwas-catalog queries will fail",
            kind=CAVEAT,
        )
    except _requests.Timeout:
        report.add(
            "gwas catalog api",
            WARN,
            f"{api_url} timed out after 10 seconds",
            "the GWAS Catalog REST API is slow or unreachable; "
            "--source gwas-catalog queries may fail or hang",
            kind=CAVEAT,
        )
    except Exception as exc:
        report.add(
            "gwas catalog api",
            WARN,
            f"{api_url} probe failed: {type(exc).__name__}: {exc}",
            "unexpected error probing the GWAS Catalog REST API",
            kind=CAVEAT,
        )


def _check_disignatlas(report: Report) -> None:
    """Probe the DisigNAtlas landing page for reachability.

    A lightweight connectivity check — hits the landing page, not a full
    search query.  Reports CAVEAT (not CAPABILITY) because DisigNAtlas being
    down does not prevent other tools from working; GEO search may partially
    compensate.

    Added for issue #51: ``dde disignatlas search`` threw a raw
    ``ConnectionError`` traceback when the endpoint was unreachable;
    this check surfaces reachability at ``dde doctor`` time.
    """
    try:
        import requests as _requests
    except ImportError:
        report.add(
            "disignatlas endpoint",
            WARN,
            "requests package not installed — cannot probe endpoint",
            "install requests to enable DisigNAtlas connectivity checks",
            kind=CAVEAT,
        )
        return

    url = "https://www.inbirg.com/disignatlas/"
    try:
        resp = _requests.get(url, timeout=10)
        if resp.status_code == 200:
            report.add(
                "disignatlas endpoint",
                OK,
                f"{url} reachable (HTTP {resp.status_code})",
            )
        else:
            report.add(
                "disignatlas endpoint",
                WARN,
                f"{url} returned HTTP {resp.status_code}",
                "DisigNAtlas transcriptomics data is unavailable; "
                "GEO search may partially compensate",
                kind=CAVEAT,
            )
    except _requests.ConnectionError:
        report.add(
            "disignatlas endpoint",
            WARN,
            f"{url} unreachable (connection error)",
            "DisigNAtlas transcriptomics data is unavailable; "
            "GEO search may partially compensate",
            kind=CAVEAT,
        )
    except _requests.Timeout:
        report.add(
            "disignatlas endpoint",
            WARN,
            f"{url} timed out after 10 seconds",
            "DisigNAtlas transcriptomics data is unavailable; "
            "GEO search may partially compensate",
            kind=CAVEAT,
        )
    except _requests.RequestException as exc:
        report.add(
            "disignatlas endpoint",
            WARN,
            f"{url} probe failed: {type(exc).__name__}: {exc}",
            "DisigNAtlas transcriptomics data is unavailable; "
            "GEO search may partially compensate",
            kind=CAVEAT,
        )


def _check_askcos(report: Report) -> None:
    """Probe the ASKCOS retrosynthesis API for reachability.

    A lightweight connectivity check -- hits the frontend config endpoint,
    not a full retrosynthesis query. Reports CAVEAT (not CAPABILITY) because
    ASKCOS being down does not prevent other tools from working.
    """
    try:
        import requests as _requests
    except ImportError:
        report.add(
            "askcos api",
            WARN,
            "requests package not installed -- cannot probe endpoint",
            "install requests to enable ASKCOS connectivity checks",
            kind=CAVEAT,
        )
        return

    url = "https://askcos.mit.edu/api/frontend-config/get-all-config"
    try:
        resp = _requests.get(url, timeout=10)
        if resp.status_code == 200:
            report.add(
                "askcos api",
                OK,
                "askcos.mit.edu reachable (retrosynthesis endpoint available)",
            )
        else:
            report.add(
                "askcos api",
                WARN,
                f"askcos.mit.edu returned HTTP {resp.status_code}",
                "the ASKCOS retrosynthesis API may be degraded; "
                "`dde retro search` queries may fail",
                kind=CAVEAT,
            )
    except _requests.ConnectionError:
        report.add(
            "askcos api",
            WARN,
            "askcos.mit.edu unreachable (connection error)",
            "the ASKCOS retrosynthesis API is not reachable from this "
            "environment; `dde retro search` queries will fail",
            kind=CAVEAT,
        )
    except _requests.Timeout:
        report.add(
            "askcos api",
            WARN,
            "askcos.mit.edu timed out after 10 seconds",
            "the ASKCOS retrosynthesis API is slow or unreachable; "
            "`dde retro search` queries may fail or hang",
            kind=CAVEAT,
        )
    except Exception as exc:
        report.add(
            "askcos api",
            WARN,
            f"askcos.mit.edu probe failed: {type(exc).__name__}: {exc}",
            "unexpected error probing the ASKCOS retrosynthesis API",
            kind=CAVEAT,
        )


def _check_known_faults(report: Report) -> None:
    """Standing advisories the agent must know before planning work.

    Each carries a RETIRES WHEN test, because a standing fault that
    cannot say what would end it is indistinguishable from one nobody
    has re-checked in a year. But the tests are not equally available,
    and pretending otherwise is its own defect: a test only a privileged
    reader can run will be run by nobody, and the advisory outlives its
    fault exactly as if it had no test at all.

    So the two AlphaGenome tests, which need a key and a live endpoint,
    say whether the reader in front of them can run it. The tool knows —
    the credential either resolves here or it does not — and an
    unrunnable test that names its owner is an assigned check rather
    than a dead one. The HPA and gnomAD tests need only network; the AF3
    one is ours to build and says so.
    """
    key_here = bool(os.environ.get("ALPHAGENOME_API_KEY"))
    adc_here = False
    if not key_here:
        try:
            import google.auth  # lazy import

            google.auth.default()
            adc_here = True
            key_here = True
        except Exception:
            pass

    who = (
        "you can run this here — ALPHAGENOME_API_KEY resolves in this shell"
        if os.environ.get("ALPHAGENOME_API_KEY")
        else "you can run this here — GCP ADC resolves (Vertex backend)"
        if adc_here
        else "not runnable here: ALPHAGENOME_API_KEY is unset and ADC does not "
        "resolve, so this test belongs to whoever holds credentials"
    )
    report.add(
        "alphagenome quantiles",
        WARN,
        "quantile scores come back for some output types and not others — "
        "RNA_SEQ carries them, CAGE/ATAC/DNASE/PROCAP do not — so one variant "
        "can yield a significance-tested verdict on one assay and a "
        "magnitude-only one on another",
        "read quantile_scores_available per block, never once per run; a "
        "verdict ending _unconfirmed had no significance test behind it. "
        "RETIRES WHEN: the API returns quantiles for CAGE/ATAC/DNASE/PROCAP, "
        "which is observable — re-check by scoring one variant per output "
        f"type and looking for quantile_scores in each block ({who})",
    )
    report.add(
        "alphagenome requests",
        WARN,
        "the endpoint answers a malformed request with 502 rather than 400 — a "
        "missing organism, an unset interval strand, an interval that is not "
        "exactly 16384/131072/524288/1048576 bp, or an oversized default "
        "scorer set all look identical to a backend outage",
        "a persistent 502 is more likely a rejected request than a busy "
        "endpoint; check the request before waiting out a retry budget. "
        f"RETIRES WHEN: a deliberately malformed request comes back 400 ({who})",
    )
    report.add(
        "af3 concurrency",
        WARN,
        "the AF3 endpoint is single-flight; this CLI serialises callers only "
        "within one container",
        "parallel specialists in separate containers can still collide with 429s; "
        "a cross-container lease broker is not yet implemented. "
        "RETIRES WHEN: that broker ships, or the endpoint stops being "
        "single-flight — ours to build, not upstream's to fix",
    )
    report.add(
        "hpa silent column drop",
        WARN,
        "the Human Protein Atlas API drops unrecognised tissue column codes "
        "without an error, returning a narrower response instead of a 400",
        "`dde expression` validates all 50 columns and exits 3 if any is "
        "missing; if that fires, re-validate the tissue list — do not read the "
        "gap as absent expression. RETIRES WHEN: a request with one bogus "
        "column code comes back 400 instead of a narrower 200",
    )
    report.add(
        "hpa release pinning",
        WARN,
        "HPA exposes no release version through the API, in the body or the "
        "headers; it is scraped from /about/download",
        "provenance is anchored on the payload SHA-256 in the sidecar, not on "
        "the release label; cite the digest when the label is absent. "
        "RETIRES WHEN: HPA exposes a release identifier in the API response "
        "or its headers, at which point the scrape can be dropped",
    )
    report.add(
        "gnomad errors arrive as HTTP 200",
        WARN,
        "gnomAD reports both 'Gene not found' and 'Service overloaded' in a "
        "GraphQL errors array with a 200 status, so a throttle and a missing "
        "gene are indistinguishable by status code",
        "`dde genetics` retries the transient set and fails loudly on the "
        "rest; never record a throttled query as a gene with no constraint "
        "data. RETIRES WHEN: gnomAD returns a non-200 for either condition",
    )


def _check_hypothesis_strategies(report: Report) -> None:
    """Report availability of the four hypothesis entry strategies."""
    # Sponsor and charter are always available — they are adoption commands
    # built into the CLI itself.
    report.add(
        "hypothesis strategy: sponsor",
        OK,
        "always available (dde hypothesis adopt --origin sponsor)",
    )
    report.add(
        "hypothesis strategy: charter",
        OK,
        "always available (dde hypothesis adopt --origin charter)",
    )

    # Co-scientist requires an export to exist.  The command itself is
    # always present; the strategy is available whenever dde coscientist
    # is importable (which it always is in a standard installation).
    report.add(
        "hypothesis strategy: co-scientist",
        OK,
        "available (dde coscientist ingest — requires an export file)",
    )

    # Hypex requires the complete three-tool volume + templates + lease.
    # Derive provisioning status from _PROVISIONED_BINARIES so the binary
    # and strategy checks cannot disagree about whether DDE owns the tools.
    missing = []
    for tool in ("hypex", "elo", "prox"):
        path = shutil.which(tool)
        if path is None:
            candidate = env.tools_home() / "bin" / tool
            path = str(candidate) if candidate.is_file() else None
        if not path:
            missing.append(tool)
            continue
        try:
            result = subprocess.run(
                [path, "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            missing.append(tool)
        else:
            marker = _HELP_MARKERS[tool]
            if result.returncode != 0 or marker not in result.stdout:
                missing.append(tool)

    if not missing:
        report.add(
            "hypothesis strategy: hypex",
            OK,
            "available (hypex tools + DDE ingest/analyze; requires templates and a lease)",
        )
    else:
        report.add(
            "hypothesis strategy: hypex",
            WARN,
            "incomplete Hypex toolchain (missing or non-runnable: "
            + ", ".join(missing)
            + ") — tournament orchestration unavailable",
            "run a full `tools/install.sh --update` to provision "
            "hypex, elo, prox, and dependencies",
            kind=CAPABILITY,
        )


def _check_phase_two_contract(report: Report) -> None:
    """State how many phase-2 commands are under the contract, not just whether any broke it.

    Both halves of the phase-2 contract are structural, so both can be
    read off the built command tree: `analyze` never reaches the network
    (the root latch); its verdict can be written somewhere other than
    where it read (`--out`); and replacing a differing verdict in place
    takes `--overwrite`, so a second opinion cannot silently become the
    only opinion even when two runs resolve to one path.

    The check exists because the latch already passed every test while
    silently missing two commands: it keyed on the exact name `analyze`
    and said nothing about `analyze-prediction` or `analyze-ism`. An
    instrument must state its coverage, not only its findings — "N of N
    guarded" is a claim that can be checked and can be wrong, whereas "no
    violations found" is not a claim at all. Written as N rather than as
    today's count deliberately: a number cached in prose goes stale
    upward as commands land, and makes a correct run look like a
    regression rather than a healthy one.

    Walks the tree click actually built, for the same reason the relay
    check imports the registry instead of parsing the source: a checker
    that re-derives the thing it is checking is a second cache of it.
    """
    from ..cli import cli
    from ..common import is_phase_two

    guarded: list[str] = []
    unlatched: list[str] = []
    unredirectable: list[str] = []
    clobbering: list[str] = []

    def walk(group: click.Group, prefix: str = "") -> None:
        for name, command in group.commands.items():
            if isinstance(command, click.Group):
                walk(command, f"{prefix}{name} ")
                continue
            if not is_phase_two(name):
                continue
            label = f"{prefix}{name}"
            if getattr(command.callback, "_phase_two_guarded", False):
                guarded.append(label)
            else:
                unlatched.append(label)
            if not any(p.name == "out" for p in command.params):
                unredirectable.append(label)
            if not any(p.name == "overwrite" for p in command.params):
                clobbering.append(label)

    walk(cli)

    total = len(guarded) + len(unlatched)
    if unlatched or unredirectable or clobbering:
        problems = []
        if unlatched:
            problems.append(f"not offline-latched: {', '.join(sorted(unlatched))}")
        if unredirectable:
            problems.append(f"no --out: {', '.join(sorted(unredirectable))}")
        if clobbering:
            problems.append(f"no --overwrite guard: {', '.join(sorted(clobbering))}")
        report.add(
            "phase-2 contract",
            FAIL,
            f"{len(guarded)} of {total} phase-2 command(s) latched offline; "
            + "; ".join(problems),
            "a phase-2 command that can reach the network makes re-analysis "
            "depend on an endpoint, and one that can only write where it read "
            "destroys the artifact a reviewer is auditing — fix in "
            "dde/common.py (the root walk) or add --out to the command",
        )
        return

    report.add(
        "phase-2 contract",
        OK,
        f"{total} of {total} phase-2 command(s) latched offline, redirectable "
        f"and non-clobbering "
        f"({', '.join(sorted(guarded))})",
    )


# --- capability snapshot ---------------------------------------------------


#: Capabilities whose status is captured by get_capability_snapshot().
#: Maps a short name to a (check_function, detail_key) pair.  The check
#: functions are the same ones `doctor` runs; this list selects the
#: subset that represents *capabilities* (things that either work or
#: don't) as opposed to advisory caveats.
#:
#: The snapshot is written into sidecars and analysis records so that a
#: decision made when hypex was unavailable is structurally
#: distinguishable from one made with full tooling — no prose required.


def get_capability_snapshot() -> dict[str, str]:
    """Return a dict mapping capability names to status strings.

    Status is one of ``"available"``, ``"unavailable"``, or
    ``"degraded"``.  The snapshot is intentionally cheap — it re-uses
    the same check logic that ``dde doctor`` runs but does not probe
    remote endpoints (those are caveats, not capabilities).

    Designed to be called from provenance-writing code so the snapshot
    can be stamped into sidecars and analysis records.
    """
    snapshot: dict[str, str] = {}

    # --- Provisioned binaries ---
    for binary, (_purpose, _remedy) in _PROVISIONED_BINARIES.items():
        path = shutil.which(binary)
        if path is None:
            candidate = env.tools_home() / "bin" / binary
            if candidate.is_file():
                # Present but not on PATH — degraded (usable only via
                # explicit path, not by tools that shell out).
                snapshot[binary] = "degraded"
            else:
                snapshot[binary] = "unavailable"
        elif binary in _CAPABILITY_VALIDATED:
            # For capability-validated scripts, run the quick check.
            try:
                result = subprocess.run(
                    [path, "--help"],
                    capture_output=True,
                    timeout=10,
                )
                snapshot[binary] = (
                    "available" if result.returncode == 0 else "unavailable"
                )
            except (subprocess.TimeoutExpired, OSError):
                snapshot[binary] = "unavailable"
        else:
            snapshot[binary] = "available"

    # --- Optional packages that gate capabilities ---
    _optional_capability_packages = {
        "google.cloud.aiplatform": "vertex_ai",
        "alphagenome": "alphagenome_pip",
        "rdkit.Chem": "rdkit",
        "meeko": "meeko",
    }
    for module, cap_name in _optional_capability_packages.items():
        try:
            importlib.import_module(module)
            snapshot[cap_name] = "available"
        except ImportError:
            snapshot[cap_name] = "unavailable"

    # --- Credentials ---
    if os.environ.get("ALPHAGENOME_API_KEY"):
        snapshot["alphagenome_credential"] = "available"
    else:
        try:
            import google.auth

            google.auth.default()
            snapshot["alphagenome_credential"] = "available"
        except Exception:
            snapshot["alphagenome_credential"] = "unavailable"

    # --- GCP ---
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        snapshot["gcp_credential"] = "available"
    else:
        try:
            import google.auth

            google.auth.default()
            snapshot["gcp_credential"] = "available"
        except Exception:
            snapshot["gcp_credential"] = "unavailable"

    return snapshot


# --- command ---------------------------------------------------------------


def _verdict(report: Report, *, strict: bool = False) -> None:
    """Say what the reader should do, because the count does not.

    Eight skills tell an agent to "run dde doctor to confirm the
    environment". That is a binary instruction against graded output:
    this page prints fourteen warnings on a healthy install, none of
    which are a reason to stop, and an agent that treats the count as
    the answer either halts on nothing or learns to ignore the page —
    and the second is how a real FAIL gets waved through.

    So the tool answers the question rather than each skill carrying a
    copy of the answer. Copies go stale, and a stale copy of "which
    warnings matter" is worse than none.

    The three kinds are deliberately about the READER's next command,
    not about severity: what will refuse, what changes how a result
    reads, and what is somebody else's job.
    """
    capability = report.warnings_of(CAPABILITY)
    caveats = report.warnings_of(CAVEAT)
    housekeeping = report.warnings_of(HOUSEKEEPING)

    if report.failures:
        if strict:
            click.echo(
                "STOP (--strict). Fix the FAILED checks above before running "
                "anything; provenance issues are blocking under --strict."
            )
        else:
            click.echo(
                "STOP. Fix the FAILED checks above before running anything; "
                "results produced now would be unreproducible or wrong."
            )
        return

    # "Warnings are expected here" belongs in the tool, not in the eight
    # skills that point at it. I gave skills-lead a sentence ending "a
    # healthy install has about fourteen" and they patched it into all
    # eight — a cached count in prose, the exact defect template-builder
    # removed from this repo the same morning, failing in the direction
    # that makes a healthy system look broken. The tool knows its own
    # count and can say so without anyone caching it.
    click.echo(
        "PROCEED — nothing above stops work. Warnings are expected on a "
        "healthy install; judge by this verdict, not by how many there are. "
        "What they mean:"
    )
    if capability:
        click.echo(
            f"  {len(capability)} thing(s) you cannot run: "
            + ", ".join(c.name for c in capability)
        )
        click.echo(
            "    These refuse loudly if you invoke them. They do not affect "
            "any other command."
        )
    if caveats:
        click.echo(
            f"  {len(caveats)} standing fault(s) in upstream services — these "
            "change how you READ a result, not whether you can produce one."
        )
        click.echo(
            "    Read the remedy line for any tool you are about to use: "
            "`dde doctor --json`. Each names what would RETIRE it — a "
            "standing fault that cannot say what would end it is indis"
            "tinguishable from one nobody has re-checked in a year."
        )
    if housekeeping:
        click.echo(
            f"  {len(housekeeping)} environment issue(s) for the tooling lead: "
            + ", ".join(c.name for c in housekeeping)
        )
        click.echo(
            "    Report them; do not work around them. They do not invalidate "
            "a result you have already produced."
        )
        if not strict:
            click.echo(
                f"    ({len(housekeeping)} provenance issue(s) would fail "
                "under --strict mode.)"
            )
    if not (capability or caveats or housekeeping):
        click.echo("  none.")


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Emit the report as JSON.")
@click.option(
    "--strict",
    is_flag=True,
    help="Promote provenance (HOUSEKEEPING) warnings to failures. "
    "Use in CI or audit contexts where reproducibility is blocking.",
)
@pass_state
def doctor(state: AppState, as_json: bool, strict: bool) -> None:
    """Assert tools, credentials and environment version. Exits non-zero if broken.

    Project root is resolved in order: (1) DDE_PROJECT environment variable,
    (2) walk up from CWD looking for a .dde/ marker directory, (3) fail with
    guidance. Run `dde init <dir>` to create a project directory, or set
    DDE_PROJECT explicitly.
    """
    report = Report()
    _check_python(report)
    _check_toolchain_integrity(report)
    _check_environment(report)
    _check_env_drift(report)
    _check_env_source(report)
    _check_project(report, state)
    _check_env_partition(report, state)
    _check_packages(report)
    _check_binaries(report)
    _check_credentials(report)
    _check_thresholds(report)
    _check_pacing(report)
    _check_gwas_catalog(report)
    _check_disignatlas(report)
    _check_askcos(report)
    _check_hypothesis_strategies(report)
    _check_phase_two_contract(report)
    _check_known_faults(report)

    # --strict: promote HOUSEKEEPING warnings to FAIL so provenance issues
    # are blocking in CI/audit contexts.
    if strict:
        for check in report.checks:
            if check.status == WARN and check.kind == HOUSEKEEPING:
                check.status = FAIL

    if as_json:
        click.echo(
            json.dumps(
                {
                    "cli_version": env.CLI_VERSION,
                    "env_version": env.env_version(),
                    "ok": not report.failures,
                    "checks": [c.__dict__ for c in report.checks],
                    "capability_snapshot": get_capability_snapshot(),
                },
                indent=2,
            )
        )
    else:
        symbol = {OK: "ok  ", INFO: "info", WARN: "WARN", FAIL: "FAIL"}
        click.echo(f"dde {env.CLI_VERSION}  env {env.env_version()}")
        click.echo("")
        for check in report.checks:
            click.echo(f"  [{symbol[check.status]}] {check.name}: {check.detail}")
        click.echo("")
        if report.failures:
            click.echo(f"{len(report.failures)} check(s) FAILED:")
            for check in report.failures:
                click.echo(f"  - {check.name}: {check.remedy}")
        _verdict(report, strict=strict)

    sys.exit(1 if report.failures else 0)
