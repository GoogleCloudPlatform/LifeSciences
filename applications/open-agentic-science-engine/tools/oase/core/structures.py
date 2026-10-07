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

"""Shared structure-format detection for PDB and mmCIF files.

Every structure-consuming tool in this CLI needs to answer "is this PDB
or mmCIF?" at least once before it can do anything useful.  Before this
module existed, each tool answered the question independently and
differently, and every new tool rediscovered the gap (see #19 and #33).

**Detection vs. handling are deliberately separate.**  What a tool does
once it knows the format depends on the tool, not on the format:

  pocket.py   Dispatch to ``_parse_residues_pdb()`` or
              ``_parse_residues_cif()`` — the tool must parse atom
              coordinates natively, so it needs a format-specific
              parser per format.

  docking.py  Pass the file to ``mk_prepare_receptor.py --read_with_prody``,
              which handles both PDB and mmCIF transparently via ProDy.
              The tool does not need to know the format for parsing, but
              may use ``detect_structure_format()`` for early validation
              (fail with a clear error before launching a subprocess that
              would produce an opaque one).

A future tool should check this module first: either reuse
``detect_structure_format()`` to dispatch format-specific logic, or
discover that the downstream tool already handles both formats natively
and skip format-specific logic entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .errors import ArtifactError

#: File extensions that signal PDB format (case-insensitive).
_PDB_EXTENSIONS = frozenset({".pdb", ".ent"})

#: File extensions that signal mmCIF format (case-insensitive).
_CIF_EXTENSIONS = frozenset({".cif", ".mmcif"})

#: Tokens that appear at the start of lines in mmCIF files but never in
#: legitimate PDB fixed-format files.
_CIF_TOKENS = ("data_", "loop_", "_atom_site.", "_entry.id", "_cell.", "_audit.")

#: Record types that appear at column 0 in PDB fixed-format files.
_PDB_RECORD_TYPES = (
    "ATOM  ",
    "HETATM",
    "HEADER",
    "REMARK",
    "CRYST1",
    "SEQRES",
    "END   ",
)

#: How many bytes of the file head to read for content sniffing.  200 kB
#: is enough to reach the coordinate block in any structure this project
#: handles, without reading multi-GB files entirely.
_SNIFF_BYTES = 200_000


def detect_structure_format(path: Path) -> Literal["pdb", "cif"]:
    """Determine whether *path* is a PDB or mmCIF structure file.

    Uses a two-stage strategy:

    1. **Extension hint** — ``.pdb``/``.ent`` → PDB, ``.cif``/``.mmcif`` → CIF.
    2. **Content sniff** — reads the first ~200 kB of the file and looks
       for characteristic tokens.  mmCIF files use ``data_``, ``loop_``,
       and ``_atom_site.*`` block/loop syntax; PDB files use fixed-width
       ``ATOM``/``HETATM``/``HEADER`` records without CIF's syntax.

    If the extension gives a clear answer and the content does not
    contradict it, the extension wins (fast path).  If the extension is
    absent or unrecognised, content alone decides.  If the content
    contradicts the extension, content wins (a mislabelled file should
    not silently misparse — see ``pocket.py``'s ``_is_experimental()``,
    which reads file content rather than trusting a name).

    Raises :class:`ArtifactError` if the file matches neither pattern
    or cannot be read.
    """
    if not path.is_file():
        raise ArtifactError(
            f"structure file not found: {path}",
            remedy="check that the path points to a PDB or mmCIF coordinate file",
        )

    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:_SNIFF_BYTES]
    except OSError as exc:
        raise ArtifactError(
            f"could not read structure file: {path}",
            detail=str(exc),
        ) from exc

    content_format = _sniff_content(head)
    ext_format = _classify_extension(path)

    # Content signal is authoritative when present.
    if content_format is not None:
        return content_format

    # No content signal — fall back to extension.
    if ext_format is not None:
        return ext_format

    raise ArtifactError(
        f"cannot determine structure format of {path.name}",
        detail="file does not contain recognisable PDB or mmCIF content "
        "and the extension is not .pdb, .ent, .cif, or .mmcif",
        remedy="pass a PDB or mmCIF coordinate file",
    )


def _classify_extension(path: Path) -> Literal["pdb", "cif"] | None:
    """Return format from the file extension, or ``None`` if unrecognised."""
    ext = path.suffix.lower()
    if ext in _PDB_EXTENSIONS:
        return "pdb"
    if ext in _CIF_EXTENSIONS:
        return "cif"
    return None


def _sniff_content(head: str) -> Literal["pdb", "cif"] | None:
    """Classify a file's format from its content.

    Returns ``"cif"`` if any line starts with a characteristic mmCIF
    token, ``"pdb"`` if any line starts with a characteristic PDB record
    type (and no CIF tokens were seen), or ``None`` if neither signal is
    found.
    """
    has_cif = False
    has_pdb = False

    for line in head.splitlines():
        if not line or line.isspace():
            continue
        # Check CIF tokens — these are unambiguous; no PDB file contains
        # lines starting with ``data_`` or ``_atom_site.``.
        for token in _CIF_TOKENS:
            if line.startswith(token):
                has_cif = True
                break
        # Check PDB record types.  These are fixed-width 6-character
        # identifiers at column 0.  We test the first 6 characters.
        if line[:6] in _PDB_RECORD_TYPES:
            has_pdb = True

    # CIF tokens are more specific and never appear in PDB files, so
    # they win if both signals are present (which can happen in a CIF
    # file that also contains ATOM records — mmCIF does use ``ATOM``
    # as a record type in its ``_atom_site`` loop).
    if has_cif:
        return "cif"
    if has_pdb:
        return "pdb"
    return None
