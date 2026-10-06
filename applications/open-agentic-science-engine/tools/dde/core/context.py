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

"""Project root resolution and artifact directory layout.

Agents are unreliable about working directory, so the CLI never trusts
CWD for output placement. See docs/tool-design-guidance.md §4.

Resolution order:
  1. $DDE_PROJECT
  2. walk up from CWD looking for a `.dde/` marker directory
  3. fail loudly — never silently write into CWD

Additionally, the dde repo itself is refused as a project root. In
development /workspace *is* the dde repo, and writing raw/ into it
would pollute the repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ProjectRootError, SchemaError
from .paths import is_safe_to_open

# Marker placed at the root of the dde *source repo*. Its presence
# means "this directory is the toolchain, not a drug program".
REPO_MARKER = ".dde-repo"

# Marker directory identifying a dde *program* directory.
PROJECT_MARKER = ".dde"

# One directory per artifact class (§4). Keys are artifact classes used
# by subcommands; values are paths relative to the project root.
ARTIFACT_DIRS: dict[str, str] = {
    "admet": "raw/admet",
    "analogs": "raw/analogs",
    "assays": "raw/assays",
    "bioactivity": "raw/bioactivity",
    "compound": "raw/compounds",
    "compounds": "raw/compounds",
    "descriptors": "raw/descriptors",
    "docking": "raw/docking",
    "expression": "raw/expression",
    "genetics": "raw/genomics",
    "genomics": "raw/genomics",
    "gtex": "raw/gtex",
    "hypotheses": "raw/hypotheses",
    "ip": "raw/ip",
    "literature": "raw/literature",
    "manufacturing": "raw/manufacturing",
    "mmp": "raw/mmp",
    "mpo": "raw/mpo",
    "pipeline": "raw/pipeline",
    "pathway": "raw/genomics",
    "pk": "raw/pk",
    "pocket": "raw/pocket",
    "regulatory": "raw/regulatory",
    "retrosynthesis": "raw/retrosynthesis",
    "safety": "raw/safety",
    "sar": "raw/sar",
    "screening": "raw/screening",
    "single-cell": "raw/single-cell",
    "structures": "raw/structures",
    "tox": "raw/tox",
    "transcriptomics": "raw/transcriptomics",
}


def normalize_artifact_class(artifact_class: str) -> str:
    """Strip a leading ``dde.`` prefix from an artifact class name.

    Work orders use prefixed names (e.g. ``dde.genetics``); ARTIFACT_DIRS
    uses unprefixed names (``genetics``).  This normalizer is the single
    place that mapping lives — every lookup site must call it first.
    """
    if artifact_class.startswith("dde."):
        return artifact_class[4:]
    return artifact_class


def resolve_artifact_subdir(artifact_class: str) -> str:
    """Look up the relative directory for *artifact_class* in ARTIFACT_DIRS.

    Applies ``dde.*`` prefix normalization before lookup.  Raises
    :class:`SchemaError` if the (normalized) class is not registered —
    an unknown class must never silently return ``None``.
    """
    normalized = normalize_artifact_class(artifact_class)
    subdir = ARTIFACT_DIRS.get(normalized)
    if subdir is None:
        raise SchemaError(
            f"unknown artifact class: {artifact_class!r}",
            detail=f"known classes: {', '.join(sorted(ARTIFACT_DIRS))}",
            remedy="this is an internal registration bug — add the class to ARTIFACT_DIRS in context.py",
        )
    return subdir


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project root plus how we found it."""

    root: Path
    source: str  # "DDE_PROJECT" | ".dde walk-up"

    def artifact_dir(
        self, artifact_class: str, override: str | os.PathLike | None = None
    ) -> Path:
        """Return (and create) the output directory for an artifact class.

        ``dde.*`` prefix is normalized before lookup — work orders use
        prefixed names, ARTIFACT_DIRS uses unprefixed names.

        `override` corresponds to a subcommand's --out flag. A relative
        override resolves against the project root, never against CWD.
        """
        if override is not None:
            path = Path(override)
            target = path if path.is_absolute() else self.root / path
        else:
            rel = resolve_artifact_subdir(artifact_class)
            target = self.root / rel
        target.mkdir(parents=True, exist_ok=True)
        return target

    def relative(self, path: Path) -> str:
        """Render a path relative to the project root when possible."""
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except ValueError:
            return str(path)


def _looks_like_dde_repo(path: Path) -> bool:
    """Heuristic backstop for the explicit repo marker."""
    if (path / REPO_MARKER).exists():
        return True
    return (
        (path / "docs" / "tool-design-guidance.md").is_file()
        and (path / "tools").is_dir()
        and not (path / PROJECT_MARKER).is_dir()
    )


def resolve_project(explicit: str | os.PathLike | None = None) -> ProjectContext:
    """Resolve the dde project root, or raise ProjectRootError."""
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        source = "--project"
        if not root.is_dir():
            raise ProjectRootError(
                f"--project path does not exist: {root}",
                remedy="create the directory, or point --project at an existing program directory",
            )
        return _validate(root, source)

    env = os.environ.get("DDE_PROJECT")
    if env:
        root = Path(env).expanduser().resolve()
        if not root.is_dir():
            raise ProjectRootError(
                f"DDE_PROJECT points at a non-existent directory: {root}",
                remedy="create it, or unset DDE_PROJECT to use .dde/ discovery",
            )
        return _validate(root, "DDE_PROJECT")

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / PROJECT_MARKER).is_dir():
            return _validate(candidate, f"{PROJECT_MARKER}/ walk-up")

    raise ProjectRootError(
        "could not resolve the dde project root",
        detail=f"no {PROJECT_MARKER}/ directory found in {here} or any parent, "
        "and DDE_PROJECT is not set",
        remedy=(
            "set DDE_PROJECT to your program directory, or run "
            "`dde init <dir>` to create one"
        ),
    )


def _validate(root: Path, source: str) -> ProjectContext:
    """Refuse the dde source repo; refuse unwritable roots."""
    if _looks_like_dde_repo(root):
        raise ProjectRootError(
            f"refusing to use the dde source repo as a project root: {root}",
            detail=(
                "writing raw/ here would pollute the repository. This is the "
                "development case called out in tool-design-guidance.md §4."
            ),
            remedy=(
                "set DDE_PROJECT to the program directory, e.g.\n"
                "           export DDE_PROJECT=/workspace/program-hr-mbc\n"
                "  or create a new one:\n"
                "           dde init /workspace/my-program\n"
                "           export DDE_PROJECT=/workspace/my-program"
            ),
        )
    if not os.access(root, os.W_OK):
        raise ProjectRootError(
            f"project root is not writable: {root}",
            detail=f"resolved from {source}",
        )
    return ProjectContext(root=root, source=source)


def _write_if_missing(path: Path, content: str) -> None:
    """Write *content* to *path* only if the file does not already exist."""
    if path.is_dir():
        raise ProjectRootError(f"expected a file but found a directory: {path}")
    if not is_safe_to_open(path):
        raise ProjectRootError(
            f"refusing to write through symlink: {path}",
            detail="symlink exploitation guard",
        )
    if not path.exists():
        path.write_text(content, encoding="utf-8")


# Findings sub-disciplines mirroring the canonical layout (dde-plan.md §findings).
FINDINGS_SUBDIRS: list[str] = [
    "structural-biology",
    "computational-biology",
    "medicinal-chemistry",
    "computational-chemistry",
    "admet-dmpk",
    "experimental-biology",
    "regulatory",
]

# Program-state skeleton files. Values are minimal Markdown headers
# explaining each file's purpose.
PROGRAM_STATE_FILES: dict[str, str] = {
    "active-series.md": "# Active Series\n\nTrack active chemical series under investigation.\n",
    "liability-tracker.md": "# Liability Tracker\n\nRecord identified liabilities and their mitigation status.\n",
    "decision-log.md": "# Decision Log\n\nChronological record of key program decisions.\n",
    "open-questions.md": "# Open Questions\n\nOutstanding questions requiring resolution.\n",
}

# Gate stage directories created under gates/ (dde-plan.md §gates).
GATE_STAGES: list[str] = [
    "stage1-intervention-validation",
    "stage2-starting-matter-declaration",
    "stage3-candidate-nomination",
    "stage4-human-readiness-package",
]


def init_project(path: str | os.PathLike) -> Path:
    """Create a program directory with the full artifact layer structure.

    The layout is idempotent: directories use ``exist_ok=True`` and
    skeleton files are only written when they do not already exist.
    """
    root = Path(path).expanduser().resolve()
    if _looks_like_dde_repo(root):
        raise ProjectRootError(
            f"refusing to initialise a program inside the dde source repo: {root}",
            remedy="choose a directory outside the repo",
        )
    (root / PROJECT_MARKER).mkdir(parents=True, exist_ok=True)

    # --- Layer 0: raw artifact directories ---
    for rel in ARTIFACT_DIRS.values():
        (root / rel).mkdir(parents=True, exist_ok=True)

    # --- Findings sub-disciplines (#50) ---
    for subdir in FINDINGS_SUBDIRS:
        (root / "findings" / subdir).mkdir(parents=True, exist_ok=True)

    # --- Program state files (#50) ---
    (root / "program-state").mkdir(parents=True, exist_ok=True)
    for filename, header in PROGRAM_STATE_FILES.items():
        _write_if_missing(root / "program-state" / filename, header)

    # --- Gate stage directories (#50) ---
    for stage in GATE_STAGES:
        (root / "gates" / stage).mkdir(parents=True, exist_ok=True)

    # --- Executive summary (#50) ---
    (root / "executive").mkdir(parents=True, exist_ok=True)
    _write_if_missing(
        root / "executive" / "program-summary.md",
        "# Program Summary\n\nHigh-level program status and executive overview.\n",
    )

    # --- .dde config skeleton (#50) ---
    _write_if_missing(
        root / PROJECT_MARKER / "thresholds.yaml",
        "# Threshold configuration for automated quality gates.\n"
        "# Define per-artifact-class acceptance thresholds here.\n",
    )
    _write_if_missing(
        root / PROJECT_MARKER / "program.yaml",
        "# Program-level configuration.\n"
        "# Define target, program metadata, and global settings here.\n",
    )

    # Control plane directories (issue #22).
    from .controlstore import ensure_control_dirs

    ensure_control_dirs(root)

    return root
