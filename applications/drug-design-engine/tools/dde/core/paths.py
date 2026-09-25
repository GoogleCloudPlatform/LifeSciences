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

"""Security helpers for path confinement and slug sanitization.

These utilities are the shared foundation for preventing path-traversal and
symlink-exploitation vulnerabilities across the DDE toolchain.  Every
command that builds a filesystem path from user-controlled input should
use ``confine_path`` and/or ``sanitize_slug`` rather than rolling its own
checks.

Functions
---------
confine_path
    Resolve a path and verify it stays within a base directory.
sanitize_slug
    Make a user-controlled string safe for use in filenames.
is_safe_to_open
    Guard against opening symlinks unless explicitly allowed.
"""

from __future__ import annotations

import re
from pathlib import Path


def confine_path(base_dir: Path, path: Path) -> Path | None:
    """Resolve and confine a path to *base_dir*.

    Returns the resolved path if it stays within *base_dir*, or ``None``
    if the path escapes or is invalid (embedded null byte, symlink loop).
    """
    try:
        resolved = (base_dir / path).resolve()
    except (ValueError, RuntimeError):
        # ValueError  - embedded null byte in the path string.
        # RuntimeError - symlink loop detected during resolution.
        return None
    if not resolved.is_relative_to(base_dir.resolve()):
        return None
    return resolved


def sanitize_slug(text: str, *, max_length: int = 80) -> str:
    """Make a user-controlled string safe for use in filenames.

    Keeps only ``[a-zA-Z0-9._-]``, replaces everything else with ``-``,
    collapses consecutive hyphens, strips leading/trailing hyphens, and
    truncates to *max_length*.

    Raises :class:`ValueError` if the result is empty or resolves to a
    filesystem-special name (``.`` or ``..``) after sanitization.
    """
    slug = re.sub(r"[^a-zA-Z0-9._-]", "-", text)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    if not slug:
        raise ValueError(f"slug is empty after sanitizing {text!r}")
    if slug in (".", ".."):
        raise ValueError(
            f"slug resolves to filesystem-special name {slug!r} "
            f"after sanitizing {text!r}"
        )
    return slug


def is_safe_to_open(path: Path, *, allow_symlinks: bool = False) -> bool:
    """Check whether *path* is safe to open for reading or writing.

    Returns ``False`` if the path is a symlink (unless *allow_symlinks*
    is ``True``).  Does **not** resolve or check confinement — use
    :func:`confine_path` for that.
    """
    if not allow_symlinks and path.is_symlink():
        return False
    return True
