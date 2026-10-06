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

"""Environment identity — the value stamped into every artifact sidecar.

The tools environment is a mutable shared volume, so reproducibility
requires recording which version of it produced a result
(docs/tool-design-guidance.md §2, Rule 4).

`ENV_VERSION` is authoritative when the shared volume is provisioned.
When it is absent we are running against an unpinned developer
environment; we say so explicitly in the stamp rather than inventing a
hash that implies more rigour than exists.
"""

from __future__ import annotations

import hashlib
import os
import platform
import sys
from pathlib import Path

from .paths import is_safe_to_open

CLI_VERSION = "0.3.0"

DEFAULT_TOOLS_HOME = "/scion-volumes/tools"


def tools_home() -> Path:
    return Path(os.environ.get("DDE_TOOLS_HOME", DEFAULT_TOOLS_HOME))


def _repo_requirements() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "requirements.txt"
        if candidate.is_file():
            return candidate
    return None


def env_version() -> str:
    """Return the environment version string stamped into sidecars.

    Forms:
      "sha256:<hex>"          — provisioned shared volume, ENV_VERSION file
      "unpinned-dev:sha256:…" — developer env, hashed from requirements.txt
      "unpinned-dev:unknown"  — developer env, no requirements file found
    """
    marker = tools_home() / "ENV_VERSION"
    if marker.is_file() and is_safe_to_open(marker):
        value = marker.read_text(encoding="utf-8").strip()
        if value:
            return value

    req = _repo_requirements()
    if req is not None:
        digest = hashlib.sha256(req.read_bytes()).hexdigest()
        return f"unpinned-dev:sha256:{digest}"
    return "unpinned-dev:unknown"


def is_provisioned() -> bool:
    """True when running against a provisioned shared tools volume."""
    return (tools_home() / "ENV_VERSION").is_file()


def interpreter_tag() -> str:
    """Python minor version + architecture — the venv compatibility key."""
    return f"py{sys.version_info.major}.{sys.version_info.minor}-{platform.machine()}"


def env_warnings() -> list[str]:
    """Warnings about the environment that belong in every sidecar."""
    warnings: list[str] = []
    if not is_provisioned():
        warnings.append(
            "Environment is not provisioned from a shared tools volume "
            f"({tools_home()}/ENV_VERSION absent); env_version is a developer "
            "fallback and does not pin transitive dependencies."
        )
    return warnings
