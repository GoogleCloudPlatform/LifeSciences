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

"""Toolchain integrity detection — is the CLI running from modified source?

Answers a single question: does the installed `dde` package match its
declared `cli_version`?  When running from a git checkout, `git describe
--dirty --always` gives a precise answer; when running from an installed
package (no `.git` directory), the best we can say is "installed".

The result is cached for the session so the subprocess runs at most once
per process.  Every call site — sidecar writing, doctor, the CLI startup
warning — reads the same cached value.

Issue #127: specialists patched installed DDE source mid-run during the
pilot campaign. 5 files modified, 29 insertions, all sidecars still
claimed unmodified `cli_version: 0.3.0`.  This module makes the
condition visible instead of silent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple


class ToolchainState(NamedTuple):
    """Snapshot of the toolchain's source integrity."""

    #: Output of `git describe --dirty --always`, or "installed"/"unknown".
    integrity: str
    #: True when the git describe output contains "-dirty".
    modified: bool
    #: List of modified files (from `git diff --name-only`), empty when clean.
    modified_files: list[str]


# Session-level cache.  None means "not yet checked".
_cached: ToolchainState | None = None


def _package_root() -> Path:
    """Root of the dde package directory (the directory containing core/)."""
    return Path(__file__).resolve().parent.parent


def _find_git_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a `.git` directory."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _git_describe(git_root: Path) -> str:
    """Run `git describe --dirty --always` and return its output."""
    try:
        result = subprocess.run(
            ["git", "describe", "--dirty", "--always"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _git_modified_files(git_root: Path) -> list[str]:
    """Return list of modified files via `git diff --name-only HEAD`."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().splitlines() if f]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def check_integrity() -> ToolchainState:
    """Return the toolchain integrity state, caching for the session.

    Safe to call from any thread — worst case is a duplicate subprocess
    invocation, and the result is idempotent.
    """
    global _cached
    if _cached is not None:
        return _cached

    pkg_root = _package_root()
    git_root = _find_git_root(pkg_root)

    if git_root is None:
        _cached = ToolchainState(
            integrity="installed", modified=False, modified_files=[]
        )
        return _cached

    integrity = _git_describe(git_root)
    modified = "-dirty" in integrity
    modified_files = _git_modified_files(git_root) if modified else []

    _cached = ToolchainState(
        integrity=integrity,
        modified=modified,
        modified_files=modified_files,
    )
    return _cached


def reset_cache() -> None:
    """Clear the session cache.  Intended for tests only."""
    global _cached
    _cached = None
