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

"""`dde docking` — molecular docking with AutoDock Vina.

Three-phase layout:

  prepare   Convert a receptor structure (PDB or mmCIF) to PDBQT via
            mk_prepare_receptor.py (Meeko) and derive a grid
            box from an fpocket pocket record.  This is a citable Layer 0
            artifact in its own right: the receptor file, grid box, and
            provenance sidecar are inputs to `run`, and a different grid
            box is a different experiment.

  run       Execute Vina with the prepared receptor, a ligand PDBQT or
            SDF, and the grid box.  Writes poses and per-ligand result
            JSON; judges nothing.

  analyze   Read stored docking scores, apply the ``docking-scores``
            threshold set, and produce an analysis record with relays.
            Phase 2: offline, applies thresholds.

The three-phase split (rather than compound.py's single-file approach)
exists because `prepare` produces a reusable receptor file: one receptor
may be docked against many ligands, and re-preparing it each time is
both wasteful and a provenance hazard (the same receptor prepared twice
may differ if Meeko changes).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    from_option,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import provenance
from ..core.errors import ArtifactError, DependencyError, UsageError
from ..core.output import Emitter
from ..core.paths import confine_path, sanitize_slug
from ..core.structures import detect_structure_format

ARTIFACT_CLASS = "docking"

#: Standard amino acid three-letter codes recognised by Meeko/ProDy.
#: Used by ``_strip_non_protein`` to filter input structures.
_STANDARD_AMINO_ACIDS: frozenset[str] = frozenset(
    {
        "ALA",
        "ARG",
        "ASN",
        "ASP",
        "CYS",
        "GLN",
        "GLU",
        "GLY",
        "HIS",
        "ILE",
        "LEU",
        "LYS",
        "MET",
        "PHE",
        "PRO",
        "SER",
        "THR",
        "TRP",
        "TYR",
        "VAL",
        # Common variants that ProDy / Meeko treat as protein
        "MSE",  # selenomethionine
    }
)


# ---------------------------------------------------------------------------
# Lazy dependency checks
# ---------------------------------------------------------------------------


def _require_mk_prepare_ligand() -> str:
    """Check that mk_prepare_ligand.py is on PATH, raising DependencyError if absent."""
    path = shutil.which("mk_prepare_ligand.py")
    if path:
        return path
    raise DependencyError(
        "mk_prepare_ligand.py is not on PATH",
        detail="ligand PDBQT preparation requires Meeko's mk_prepare_ligand.py script",
        remedy="install meeko into the tools environment (pip install meeko>=0.5)",
    )


def _require_vina() -> str:
    """Check that the Vina binary is on PATH, raising DependencyError if absent."""
    path = shutil.which("vina")
    if path:
        return path
    raise DependencyError(
        "vina is not on PATH",
        detail="molecular docking cannot run without the AutoDock Vina binary",
        remedy="re-provision with `tools/install.sh --binaries-only`",
    )


def _require_mk_prepare_receptor() -> str:
    """Check that mk_prepare_receptor.py is on PATH, raising DependencyError if absent."""
    path = shutil.which("mk_prepare_receptor.py")
    if path:
        return path
    raise DependencyError(
        "mk_prepare_receptor.py is not on PATH",
        detail="receptor PDBQT preparation requires Meeko's mk_prepare_receptor.py script",
        remedy="install meeko into the tools environment (pip install meeko>=0.5)",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_pocket_record(pocket_path: Path) -> dict[str, Any]:
    """Read and validate a pocket record JSON file."""
    return provenance.read_json(pocket_path, "pocket record")


def _find_pocket_by_rank(pockets: list[dict[str, Any]], rank: int) -> dict[str, Any]:
    """Find a pocket entry by rank, raising UsageError if absent."""
    for entry in pockets:
        if entry.get("rank") == rank:
            return entry
    available = sorted(p.get("rank", "?") for p in pockets)
    raise UsageError(
        f"no pocket with rank {rank} in the pocket record",
        detail=f"available ranks: {available}",
        remedy=f"pass --pocket-rank N where N is one of {available}",
    )


def _locate_pocket_atoms(fpocket_output_dir: Path, rank: int) -> Path:
    """Locate the pocket atom file in the fpocket output tree.

    fpocket writes ``pocketN_atm.pdb`` for PDB input and
    ``pocketN_atm.cif`` for CIF input.
    """
    pockets_dir = fpocket_output_dir / "pockets"
    for ext in (".pdb", ".cif"):
        candidate = pockets_dir / f"pocket{rank}_atm{ext}"
        if candidate.is_file():
            return candidate
    raise ArtifactError(
        f"pocket atom file not found for pocket {rank} in {fpocket_output_dir}",
        detail=f"looked for pockets/pocket{rank}_atm.pdb and "
        f"pockets/pocket{rank}_atm.cif",
        remedy="check that the fpocket output directory is intact and "
        "that the pocket record points to the correct directory",
    )


def _compute_grid_box(
    pocket_atm_path: Path, padding: float
) -> tuple[list[float], list[float]]:
    """Compute grid box center and size from pocket atom coordinates.

    Center is the centroid of all atom coordinates in the pocket atom
    file.  Size is the extent of the coordinates plus padding on each
    side.

    The 10 A padding is common Vina practice; Eberhardt et al.,
    J Chem Inf Model 2021;61:3891-3898 (AutoDock Vina 1.2.x) recommend
    a box that encompasses the binding site with sufficient margin for
    ligand placement.

    Returns ``(center, size)`` where each is ``[x, y, z]``.
    """
    text = pocket_atm_path.read_text(encoding="utf-8", errors="replace")
    coords: list[tuple[float, float, float]] = []

    if pocket_atm_path.suffix.lower() == ".cif":
        # Parse mmCIF ATOM/HETATM records — coordinates are in
        # Cartn_x/y/z columns; we locate them from the _atom_site header.
        lines = text.splitlines()
        columns: list[str] = []
        data_start = 0
        in_atom_site = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("_atom_site."):
                in_atom_site = True
                columns.append(stripped.split(".")[1])
            elif in_atom_site:
                data_start = i
                break

        col_x = columns.index("Cartn_x") if "Cartn_x" in columns else None
        col_y = columns.index("Cartn_y") if "Cartn_y" in columns else None
        col_z = columns.index("Cartn_z") if "Cartn_z" in columns else None

        if col_x is not None and col_y is not None and col_z is not None:
            for line in lines[data_start:]:
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                fields = line.split()
                try:
                    coords.append(
                        (
                            float(fields[col_x]),
                            float(fields[col_y]),
                            float(fields[col_z]),
                        )
                    )
                except (ValueError, IndexError):
                    continue
    else:
        # Parse PDB-format fixed columns: x=30:38, y=38:46, z=46:54
        for line in text.splitlines():
            if not line.startswith(("ATOM", "HETATM")):
                continue
            try:
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
                coords.append((x, y, z))
            except (ValueError, IndexError):
                continue

    if not coords:
        raise ArtifactError(
            f"no atom coordinates found in {pocket_atm_path.name}",
            detail="the pocket atom file appears empty or unparseable",
            remedy="check that the fpocket output is intact",
        )

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    zs = [c[2] for c in coords]

    center = [
        round(sum(xs) / len(xs), 3),
        round(sum(ys) / len(ys), 3),
        round(sum(zs) / len(zs), 3),
    ]
    size = [
        round((max(xs) - min(xs)) + 2 * padding, 3),
        round((max(ys) - min(ys)) + 2 * padding, 3),
        round((max(zs) - min(zs)) + 2 * padding, 3),
    ]

    return center, size


def _strip_non_protein(
    structure_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Remove non-protein chains and heteroatoms from a structure file.

    Supports both PDB and mmCIF formats.  Keeps only ``ATOM`` records
    whose residue name is a standard amino acid (see
    ``_STANDARD_AMINO_ACIDS``).  For PDB files, ``TER`` and ``END``
    records are preserved.  For mmCIF files, header lines, loop
    definitions, and protein ``ATOM`` records are preserved.

    Returns a dict with provenance information::

        {
            "protein_only": True,
            "removed_chains": ["B"],
            "removed_residue_types": ["LIG", "HOH"],
            "removed_atom_count": 42,
        }
    """
    text = structure_path.read_text(encoding="utf-8", errors="replace")
    fmt = structure_path.suffix.lower()

    kept_lines: list[str] = []
    removed_chains: set[str] = set()
    removed_residue_types: set[str] = set()
    removed_atom_count = 0

    if fmt in (".cif", ".mmcif"):
        # mmCIF: keep everything except non-protein _atom_site rows.
        # Identify the column indices for group_PDB, label_comp_id,
        # and label_asym_id from the _atom_site loop header.
        lines = text.splitlines(keepends=True)
        columns: list[str] = []
        in_atom_site = False
        data_start = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("_atom_site."):
                in_atom_site = True
                columns.append(stripped.split(".")[1])
            elif in_atom_site:
                data_start = i
                break

        col_comp = (
            columns.index("label_comp_id") if "label_comp_id" in columns else None
        )
        col_chain = (
            columns.index("label_asym_id") if "label_asym_id" in columns else None
        )
        col_group = columns.index("group_PDB") if "group_PDB" in columns else None

        # Copy lines up to the data block unchanged
        kept_lines.extend(lines[:data_start])

        for line in lines[data_start:]:
            if not line.startswith(("ATOM", "HETATM")):
                kept_lines.append(line)
                continue

            fields = line.split()
            is_protein = True

            if col_comp is not None:
                try:
                    res_name = fields[col_comp]
                except IndexError:
                    res_name = ""
                if res_name not in _STANDARD_AMINO_ACIDS:
                    is_protein = False

            if col_group is not None:
                try:
                    group = fields[col_group]
                except IndexError:
                    group = ""
                if group == "HETATM":
                    is_protein = False

            if is_protein:
                kept_lines.append(line)
            else:
                removed_atom_count += 1
                if col_chain is not None:
                    try:
                        removed_chains.add(fields[col_chain])
                    except IndexError:
                        pass
                if col_comp is not None:
                    try:
                        removed_residue_types.add(fields[col_comp])
                    except IndexError:
                        pass
    else:
        # PDB format: fixed-column layout.
        for line in text.splitlines(keepends=True):
            if not line.startswith(("ATOM  ", "HETATM")):
                kept_lines.append(line)
                continue

            res_name = line[17:20].strip()
            chain = line[21:22].strip()

            if line.startswith("HETATM") or res_name not in _STANDARD_AMINO_ACIDS:
                removed_atom_count += 1
                if chain:
                    removed_chains.add(chain)
                if res_name:
                    removed_residue_types.add(res_name)
            else:
                kept_lines.append(line)

    output_path.write_text("".join(kept_lines), encoding="utf-8")

    return {
        "protein_only": True,
        "removed_chains": sorted(removed_chains),
        "removed_residue_types": sorted(removed_residue_types),
        "removed_atom_count": removed_atom_count,
    }


def _resolve_highest_occupancy_altloc(structure_path: Path) -> str:
    """Determine the altloc label with the highest mean occupancy.

    Scans ATOM/HETATM records for alternate conformations (altloc
    indicator in PDB column 16, or ``label_alt_id`` in mmCIF).
    Returns the label (e.g. ``"A"`` or ``"B"``) whose atoms have
    the highest average occupancy value.  Falls back to ``"A"`` if
    no altlocs are found.

    PDB format: altloc is column 16 (0-indexed), occupancy is
    columns 54-60.
    """
    text = structure_path.read_text(encoding="utf-8", errors="replace")
    fmt = structure_path.suffix.lower()

    # Collect occupancy values per altloc label.
    altloc_occupancies: dict[str, list[float]] = {}

    if fmt in (".cif", ".mmcif"):
        lines = text.splitlines()
        columns: list[str] = []
        data_start = 0
        in_atom_site = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("_atom_site."):
                in_atom_site = True
                columns.append(stripped.split(".")[1])
            elif in_atom_site:
                data_start = i
                break

        col_alt = columns.index("label_alt_id") if "label_alt_id" in columns else None
        col_occ = columns.index("occupancy") if "occupancy" in columns else None

        if col_alt is not None and col_occ is not None:
            for line in lines[data_start:]:
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                fields = line.split()
                try:
                    alt_id = fields[col_alt]
                    occ = float(fields[col_occ])
                except (IndexError, ValueError):
                    continue
                if alt_id and alt_id != ".":
                    altloc_occupancies.setdefault(alt_id, []).append(occ)
    else:
        # PDB format: altloc indicator is column 16, occupancy is 54:60.
        for line in text.splitlines():
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            if len(line) < 60:
                continue
            alt = line[16:17].strip()
            if not alt:
                continue
            try:
                occ = float(line[54:60])
            except (ValueError, IndexError):
                continue
            altloc_occupancies.setdefault(alt, []).append(occ)

    if not altloc_occupancies:
        return "A"

    # Return the label with the highest mean occupancy.
    best_label = "A"
    best_mean = -1.0
    for label, occs in sorted(altloc_occupancies.items()):
        mean_occ = sum(occs) / len(occs)
        if mean_occ > best_mean:
            best_mean = mean_occ
            best_label = label

    return best_label


def _convert_receptor_to_pdbqt(
    structure_path: Path,
    output_path: Path,
    altloc: str = "A",
) -> None:
    """Convert a receptor structure to PDBQT format using mk_prepare_receptor.py.

    Invokes meeko's mk_prepare_receptor.py as a subprocess — the
    documented modern entry point for receptor PDBQT conversion.  Uses
    ``--read_with_prody`` (the ProDy-based reader) which transparently
    handles both PDB and mmCIF formats through a single code path.
    This is important because AlphaFold DB structures arrive as CIF,
    not PDB.

    ``altloc`` selects which alternate conformation to use when the
    structure contains altlocs:

    - ``"A"`` (default): select altloc A (conventionally highest occupancy)
    - ``"B"``: select altloc B
    - ``"highest"``: select the altloc with the highest occupancy value

    Without ``--default_altloc``, Meeko crashes on structures with
    alternate conformations (#133).
    """
    script = _require_mk_prepare_receptor()

    # Map the altloc option to Meeko's --default_altloc flag.
    # "highest" is not directly supported by Meeko; we resolve it
    # to the actual altloc label by scanning occupancy values.
    effective_altloc = altloc
    if altloc == "highest":
        effective_altloc = _resolve_highest_occupancy_altloc(structure_path)

    completed = subprocess.run(
        [
            script,
            "--read_with_prody",
            str(structure_path),
            "--default_altloc",
            effective_altloc,
            "-p",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        raw_detail = (completed.stderr or completed.stdout or "no output").strip()[:500]
        # Detect non-protein residue failures and suggest --protein-only.
        _non_protein_hints = (
            "unknown residue",
            "unrecognized residue",
            "non-standard residue",
            "UNK",
            "UNL",
        )
        if any(hint.lower() in raw_detail.lower() for hint in _non_protein_hints):
            remedy = (
                "structure contains non-protein chains; pass --protein-only "
                "to strip them before preparation"
            )
        else:
            remedy = (
                "check that the file is a valid PDB or mmCIF structure "
                "with protein atoms"
            )
        raise ArtifactError(
            f"mk_prepare_receptor.py failed for {structure_path.name}",
            detail=raw_detail,
            remedy=remedy,
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ArtifactError(
            f"mk_prepare_receptor.py produced no output for {structure_path.name}",
            detail=(completed.stderr or completed.stdout or "no output").strip()[:500],
            remedy="check the structure for unusual residues or missing atoms",
        )


def _meeko_version() -> str | None:
    """Return the installed Meeko version, or None if unavailable."""
    try:
        import meeko

        return getattr(meeko, "__version__", None)
    except ImportError:
        return None


def _vina_version(vina_path: str) -> str | None:
    """Return the Vina version string, or None if it cannot be obtained."""
    try:
        result = subprocess.run(
            [vina_path, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        version = result.stdout.strip() or result.stderr.strip()
        return version or None
    except OSError:
        return None


def _convert_ligand_to_pdbqt(sdf_path: Path, output_path: Path) -> None:
    """Convert an SDF/MOL ligand to PDBQT format using mk_prepare_ligand.py.

    Invokes meeko's mk_prepare_ligand.py as a subprocess — the
    documented entry point for ligand PDBQT conversion.  This matches
    the subprocess convention used for receptor preparation, fpocket,
    and Vina, and is robust to meeko's internal API changes.

    The ``analyze`` subcommand never calls this function.
    """
    script = _require_mk_prepare_ligand()

    completed = subprocess.run(
        [script, "-i", str(sdf_path), "-o", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        raise ArtifactError(
            f"mk_prepare_ligand.py failed for {sdf_path.name}",
            detail=(completed.stderr or completed.stdout or "no output").strip()[:500],
            remedy="check that the file is a valid SDF/MOL file with "
            "parseable molecular structure and explicit hydrogens",
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ArtifactError(
            f"mk_prepare_ligand.py produced no output for {sdf_path.name}",
            detail=(completed.stderr or completed.stdout or "no output").strip()[:500],
            remedy="check that the molecule has valid chemistry and "
            "supported atom types",
        )


def _parse_flexible_residues(spec: str) -> list[dict[str, str]]:
    """Parse a comma-separated flexible residue specification.

    Accepts specs like ``A:ARG455,A:GLU526,A:TYR829``.  Each token must
    be ``<chain>:<resname><resnum>``.

    Returns a list of dicts with keys ``chain``, ``res_name``, ``res_num``.
    """
    residues: list[dict[str, str]] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise UsageError(
                f"invalid flexible residue spec: {token!r}",
                detail="expected format <chain>:<resname><resnum> (e.g. A:ARG455)",
                remedy="use --flexible-residues 'A:ARG455,A:GLU526'",
            )
        chain, residue_part = token.split(":", 1)
        # Split residue_part into name (alpha) and number (digits at end)
        i = len(residue_part)
        while i > 0 and residue_part[i - 1].isdigit():
            i -= 1
        if i == 0 or i == len(residue_part):
            raise UsageError(
                f"invalid flexible residue spec: {token!r}",
                detail="residue part must contain both a name and a number "
                f"(got {residue_part!r})",
                remedy="use format <chain>:<resname><resnum> (e.g. A:ARG455)",
            )
        res_name = residue_part[:i]
        res_num = residue_part[i:]
        residues.append(
            {
                "chain": chain,
                "res_name": res_name,
                "res_num": res_num,
            }
        )
    if not residues:
        raise UsageError(
            "empty flexible residue specification",
            remedy="provide at least one residue, e.g. --flexible-residues 'A:ARG455'",
        )
    return residues


def _split_flexible_receptor(
    receptor_path: Path,
    residue_specs: list[dict[str, str]],
    rigid_output: Path,
    flex_output: Path,
) -> None:
    """Split a receptor PDBQT into rigid and flexible parts.

    Extracts atoms matching the specified residues into the flexible
    PDBQT file (wrapped in ``BEGIN_RES``/``END_RES`` blocks for Vina),
    and writes all remaining lines to the rigid PDBQT file.

    Vina 1.2+ accepts ``BEGIN_RES``/``END_RES`` delimiters in the
    flexible residue file.  This avoids a dependency on
    ``prepare_flexreceptor4.py`` from ADFRSuite/MGLTools while
    producing output that Vina can consume directly.
    """
    text = receptor_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    # Build a set of (chain, res_name, res_num) for fast lookup
    flex_keys: set[tuple[str, str, str]] = set()
    for spec in residue_specs:
        flex_keys.add((spec["chain"], spec["res_name"], spec["res_num"]))

    # Partition lines
    rigid_lines: list[str] = []
    # Group flexible atoms by residue for BEGIN_RES/END_RES blocks
    flex_atoms: dict[tuple[str, str, str], list[str]] = {}
    for key in flex_keys:
        flex_atoms[key] = []

    matched_keys: set[tuple[str, str, str]] = set()

    for line in lines:
        if line.startswith(("ATOM", "HETATM")):
            # PDB column layout: chain=21, res_name=17:20, res_num=22:26
            chain = line[21:22].strip()
            res_name = line[17:20].strip()
            res_num = line[22:26].strip()
            key = (chain, res_name, res_num)
            if key in flex_keys:
                flex_atoms[key].append(line)
                matched_keys.add(key)
            else:
                rigid_lines.append(line)
        else:
            rigid_lines.append(line)

    # Validate that all specified residues were found
    missing = flex_keys - matched_keys
    if missing:
        missing_strs = [f"{c}:{n}{r}" for c, n, r in sorted(missing)]
        raise ArtifactError(
            f"flexible residue(s) not found in receptor: {', '.join(missing_strs)}",
            detail="the specified residues do not exist in the receptor PDBQT file",
            remedy="check chain IDs, residue names, and residue numbers "
            "against the receptor structure",
        )

    # Write rigid receptor
    rigid_output.write_text("".join(rigid_lines), encoding="utf-8")

    # Write flexible residues with BEGIN_RES/END_RES blocks
    flex_lines: list[str] = []
    for key in sorted(flex_atoms.keys()):
        chain, res_name, res_num = key
        atoms = flex_atoms[key]
        flex_lines.append(f"BEGIN_RES {res_name} {chain} {res_num}\n")
        for atom_line in atoms:
            if not atom_line.endswith("\n"):
                atom_line += "\n"
            flex_lines.append(atom_line)
        flex_lines.append(f"END_RES {res_name} {chain} {res_num}\n")

    flex_output.write_text("".join(flex_lines), encoding="utf-8")


def _grid_size_warning(size: list[float], exhaustiveness: int) -> str | None:
    """Return a warning string if the docking search space is large.

    Thresholds chosen from pilot experience (AD Target Discovery):
    - volume > 50,000 A^3 at any exhaustiveness
    - volume > 30,000 A^3 when exhaustiveness > 8

    Runtime estimate calibrated from the pilot: a 43.9 x 40.8 x 41.5 A
    grid at exhaustiveness=16 took ~15-20 min on one Vina core.
    """
    volume = size[0] * size[1] * size[2]
    large = volume > 50_000 or (exhaustiveness > 8 and volume > 30_000)
    if not large:
        return None

    # Calibration reference from the pilot run.
    ref_vol = 43.9 * 40.8 * 41.5  # ~74,427 A^3
    ref_exhaust = 16
    ref_time = 17.5  # midpoint of 15-20 min
    estimated_mins = ref_time * (volume / ref_vol) * (exhaustiveness / ref_exhaust)

    return (
        f"Warning: large search space ({size[0]:.1f} x {size[1]:.1f} x "
        f"{size[2]:.1f} A, exhaustiveness={exhaustiveness}).\n"
        f"Estimated runtime: ~{estimated_mins:.0f} min. Consider "
        f"--background or reducing exhaustiveness."
    )


def _parse_vina_poses(pdbqt_text: str) -> list[dict[str, Any]]:
    """Parse REMARK VINA RESULT lines from Vina output PDBQT.

    Each Vina result line contains: affinity (kcal/mol), rmsd_lb, rmsd_ub.
    Poses are numbered sequentially as written by Vina (best first).
    """
    poses: list[dict[str, Any]] = []
    for line in pdbqt_text.splitlines():
        if line.startswith("REMARK VINA RESULT:"):
            parts = line[len("REMARK VINA RESULT:") :].split()
            if len(parts) >= 3:
                poses.append(
                    {
                        "rank": len(poses) + 1,
                        "affinity_kcalmol": float(parts[0]),
                        "rmsd_lb": float(parts[1]),
                        "rmsd_ub": float(parts[2]),
                    }
                )
    return poses


def _parse_pdbqt_atoms(text: str) -> list[dict[str, Any]]:
    """Parse ATOM/HETATM records from PDBQT text.

    Returns a list of dicts with chain, res_name, res_num, x, y, z.
    PDBQT uses standard PDB column layout for coordinates (cols 30-54).
    """
    atoms: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except (ValueError, IndexError):
            continue
        chain = line[21:22].strip() or ""
        res_name = line[17:20].strip()
        try:
            res_num = int(line[22:26].strip())
        except ValueError:
            res_num = 0
        atoms.append(
            {
                "chain": chain,
                "res_name": res_name,
                "res_num": res_num,
                "x": x,
                "y": y,
                "z": z,
            }
        )
    return atoms


def _split_pdbqt_models(text: str) -> list[str]:
    """Split a multi-model PDBQT into individual model blocks.

    Models are delimited by MODEL/ENDMDL lines.  If no MODEL/ENDMDL
    delimiters are present, the entire text is treated as one model.
    """
    models: list[str] = []
    current_lines: list[str] = []
    in_model = False

    for line in text.splitlines():
        if line.startswith("MODEL"):
            in_model = True
            current_lines = []
        elif line.startswith("ENDMDL"):
            if current_lines:
                models.append("\n".join(current_lines))
            current_lines = []
            in_model = False
        elif in_model:
            current_lines.append(line)

    if not models:
        models.append(text)

    return models


def _compute_contacts(
    pose_atoms: list[dict[str, Any]],
    receptor_atoms: list[dict[str, Any]],
    cutoff: float,
) -> list[dict[str, Any]]:
    """Find receptor residues within cutoff of any pose atom.

    A residue is "in contact" if any of its atoms is within the cutoff
    distance of any pose atom.  Returns contacted residues sorted by
    minimum distance ascending.
    """
    cutoff_sq = cutoff * cutoff

    # Group receptor atoms by residue identity
    residue_atoms: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for atom in receptor_atoms:
        key = (atom["chain"], atom["res_name"], atom["res_num"])
        residue_atoms.setdefault(key, []).append(atom)

    contacts: list[dict[str, Any]] = []
    for (chain, res_name, res_num), atoms in residue_atoms.items():
        min_dist_sq = float("inf")
        for ra in atoms:
            rx, ry, rz = ra["x"], ra["y"], ra["z"]
            for pa in pose_atoms:
                dx = pa["x"] - rx
                dy = pa["y"] - ry
                dz = pa["z"] - rz
                d_sq = dx * dx + dy * dy + dz * dz
                if d_sq < min_dist_sq:
                    min_dist_sq = d_sq
        if min_dist_sq <= cutoff_sq:
            contacts.append(
                {
                    "chain": chain,
                    "res_name": res_name,
                    "res_num": res_num,
                    "min_distance": round(min_dist_sq**0.5, 3),
                }
            )

    contacts.sort(key=lambda c: c["min_distance"])
    return contacts


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def docking() -> None:
    """Molecular docking (AutoDock Vina)."""


# ---------------------------------------------------------------------------
# Phase 1 — prepare
# ---------------------------------------------------------------------------


@docking.command("prepare")
@click.argument("structure", type=click.Path())
@click.argument("pocket_record", type=click.Path())
@click.option(
    "--pocket-rank",
    default=1,
    type=int,
    show_default=True,
    help="Which pocket from the record to use for grid box derivation "
    "(by fpocket rank; default: rank 1, the top-scoring pocket).",
)
@click.option(
    "--protein-only/--keep-all",
    default=False,
    show_default=True,
    help="Strip non-protein chains and heteroatoms before receptor preparation. "
    "Records removed chains in the sidecar. Recommended for AF3 complex outputs.",
)
@click.option(
    "--altloc",
    default="A",
    type=click.Choice(["A", "B", "highest"], case_sensitive=False),
    show_default=True,
    help="Which alternate conformation to select when the structure "
    "contains altlocs. 'A' (default) selects altloc A (conventionally "
    "highest occupancy); 'B' selects altloc B; 'highest' selects the "
    "altloc with the highest occupancy value. Without this, Meeko "
    "crashes on structures with alternate conformations.",
)
@out_option
@output_options
@pass_state
def prepare_cmd(
    state: AppState,
    structure: str,
    pocket_record: str,
    pocket_rank: int,
    protein_only: bool,
    altloc: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Prepare a receptor for docking: structure to PDBQT, derive grid box.

    Reads a structure file (PDB or mmCIF; the receptor) and a pocket
    record from ``dde pocket run``.  Converts the receptor to PDBQT
    format via mk_prepare_receptor.py (Meeko, using ProDy for format-
    agnostic parsing) and derives grid box parameters (center and size)
    from the pocket's atom coordinates with 10 A padding (common Vina
    practice; Eberhardt et al., J Chem Inf Model 2021).

    Outputs are written under ``raw/docking/``:

    \b
      {stem}.receptor.pdbqt    — prepared receptor
      {stem}.gridbox.json      — grid box parameters
      {stem}.prepare.meta.json — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- resolve inputs ---
    structure_path = resolve_artifact(state, structure, "structure")
    pocket_record_path = resolve_artifact(state, pocket_record, "pocket record")

    stem = structure_path.stem

    # --- early format validation ---
    # mk_prepare_receptor.py --read_with_prody handles both PDB and CIF
    # transparently, so the detected format is not used for dispatch.
    # However, validating early gives a clear ArtifactError for files
    # that are neither format, rather than letting mk_prepare_receptor.py
    # produce an opaque subprocess error.
    detect_structure_format(structure_path)

    # --- read pocket record and locate pocket ---
    pocket_doc = _read_pocket_record(pocket_record_path)
    pockets = pocket_doc.get("pockets") or []
    if not pockets:
        raise ArtifactError(
            f"pocket record {pocket_record_path.name} contains no pockets",
            remedy="run `dde pocket run` on the structure first",
        )

    pocket_entry = _find_pocket_by_rank(pockets, pocket_rank)

    # --- locate the fpocket output directory and pocket atom file ---
    fpocket_output_dir_name = pocket_doc.get("fpocket_output_dir")
    if not fpocket_output_dir_name:
        raise ArtifactError(
            "pocket record does not contain fpocket_output_dir",
            remedy="the pocket record may be from an older version; "
            "re-run `dde pocket run`",
        )

    # The fpocket output dir is stored relative to the pocket record's
    # parent directory (they sit beside each other in raw/structures/).
    fpocket_output_dir = pocket_record_path.parent / fpocket_output_dir_name
    if not fpocket_output_dir.is_dir():
        raise ArtifactError(
            f"fpocket output directory not found: {fpocket_output_dir}",
            detail=f"the pocket record references {fpocket_output_dir_name!r}",
            remedy="check that the fpocket output tree has not been moved or deleted",
        )

    pocket_atm_path = _locate_pocket_atoms(fpocket_output_dir, pocket_rank)

    # --- derive grid box ---
    # 10 A padding on each side beyond the pocket extent — common Vina
    # practice.  Eberhardt et al., J Chem Inf Model 2021;61:3891-3898
    # (AutoDock Vina 1.2.x) recommend a box that encompasses the binding
    # site with sufficient margin.
    padding = 10.0
    center, size = _compute_grid_box(pocket_atm_path, padding)

    # --- optionally strip non-protein content ---
    strip_info: dict[str, Any] | None = None
    effective_structure = structure_path
    _strip_tmpdir: str | None = None
    if protein_only:
        _strip_tmpdir = tempfile.mkdtemp(prefix="dde-strip-")
        stripped_path = Path(_strip_tmpdir) / structure_path.name
        strip_info = _strip_non_protein(structure_path, stripped_path)
        effective_structure = stripped_path

    # --- convert receptor to PDBQT via mk_prepare_receptor.py ---
    receptor_path = target_dir / f"{stem}.receptor.pdbqt"
    try:
        _convert_receptor_to_pdbqt(effective_structure, receptor_path, altloc=altloc)
    finally:
        if _strip_tmpdir:
            shutil.rmtree(_strip_tmpdir, ignore_errors=True)

    gridbox = {
        "center": center,
        "size": size,
        "pocket_rank": pocket_rank,
        "pocket_source": str(pocket_record_path),
        "padding_angstrom": padding,
    }
    gridbox_path = target_dir / f"{stem}.gridbox.json"
    gridbox_path.write_text(json.dumps(gridbox, indent=2) + "\n", encoding="utf-8")

    # --- provenance sidecar ---
    # DISTINCT filename from what `run` will use ({stem}.docking.meta.json)
    # to avoid the compound.py round-1 sidecar collision bug.
    prepare_params: dict[str, Any] = {
        "structure": structure_path.name,
        "pocket_record": pocket_record_path.name,
        "pocket_rank": pocket_rank,
        "altloc": altloc,
    }
    if protein_only:
        prepare_params["protein_only"] = True
    sidecar = provenance.Sidecar(
        tool="docking",
        subcommand="prepare",
        endpoint=None,
        parameters=prepare_params,
    )
    sidecar.note("structure_sha256", provenance.sha256_file(structure_path))
    sidecar.note("pocket_record_path", str(pocket_record_path))
    sidecar.note("pocket_rank", pocket_rank)
    sidecar.note("pocket_druggability_score", pocket_entry.get("druggability_score"))
    sidecar.note("grid_box", gridbox)

    if strip_info is not None:
        sidecar.note("protein_only", strip_info["protein_only"])
        sidecar.note("removed_chains", strip_info["removed_chains"])
        sidecar.note("removed_residue_types", strip_info["removed_residue_types"])
        sidecar.note("removed_atom_count", strip_info["removed_atom_count"])

    meeko_ver = _meeko_version()
    if meeko_ver:
        sidecar.note("meeko_version", meeko_ver)

    sidecar.add_output(receptor_path)
    sidecar.add_output(gridbox_path)

    # --- propagate upstream relays from pocket sidecars ---
    # Follow the same pattern as compound.py analyze_cmd and this file's
    # analyze_cmd: read mandatory_relays from the pocket record's sidecar
    # and analysis, and carry them forward so downstream analyze can
    # surface upstream relays mechanically.
    pocket_stem = pocket_record_path.name.replace(".pockets.json", "")
    seen_codes: set[str] = set()
    for candidate_name in (
        f"{pocket_stem}.pockets.meta.json",
        f"{pocket_stem}.pocket.analysis.json",
    ):
        candidate = pocket_record_path.parent / candidate_name
        if candidate.is_file():
            upstream = provenance.read_json(candidate, candidate_name)
            for r in upstream.get("mandatory_relays", []) or []:
                if r["code"] not in seen_codes:
                    sidecar.relays.append(r)
                    seen_codes.add(r["code"])

    meta_path = sidecar.write(target_dir / f"{stem}.prepare.meta.json")

    emit.path(receptor_path, role="receptor")
    emit.path(gridbox_path, role="gridbox")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — run
# ---------------------------------------------------------------------------


@docking.command("run")
@click.argument("receptor", type=click.Path())
@click.argument("gridbox", type=click.Path())
@click.argument("ligands", nargs=-1, required=True, type=click.Path())
@click.option(
    "--exhaustiveness",
    default=8,
    type=int,
    show_default=True,
    help="Vina exhaustiveness parameter.",
)
@click.option(
    "--n-poses",
    default=9,
    type=int,
    show_default=True,
    help="Number of poses to generate per ligand.",
)
@click.option(
    "--flexible-residues",
    default=None,
    help="Comma-separated residue specs for flexible sidechains "
    "(e.g., 'A:ARG455,A:GLU526,A:TYR829').",
)
@out_option
@output_options
@pass_state
def run_cmd(
    state: AppState,
    receptor: str,
    gridbox: str,
    ligands: tuple[str, ...],
    exhaustiveness: int,
    n_poses: int,
    flexible_residues: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Execute AutoDock Vina against a prepared receptor and ligand(s).

    Takes the receptor PDBQT and grid box JSON from ``docking prepare``
    and one or more ligand files (PDBQT or SDF).  SDF ligands are
    converted to PDBQT via mk_prepare_ligand.py (Meeko) before docking.

    When ``--flexible-residues`` is provided, the receptor is split into
    rigid and flexible parts and Vina runs in flexible-receptor mode.
    Flexible sidechains are allowed to move during docking, which can
    improve pose accuracy for binding sites with conformationally
    important residues.

    Outputs per ligand under ``raw/docking/``, where ``receptor_id``
    is derived from the receptor filename (structure stem):

    \b
      {ligand}_{receptor_id}.docking_result.json — per-ligand scores and poses
      {ligand}_{receptor_id}.poses.pdbqt         — Vina output poses
      {ligand}_{receptor_id}.docking.meta.json   — provenance sidecar

    Including the receptor identifier prevents output collisions when
    the same ligand is docked against multiple receptors (#205).
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    vina_path = _require_vina()
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- resolve inputs ---
    receptor_path = resolve_artifact(state, receptor, "receptor PDBQT")
    gridbox_path = resolve_artifact(state, gridbox, "gridbox JSON")

    gridbox_doc = provenance.read_json(gridbox_path, "gridbox")
    center = gridbox_doc["center"]
    size = gridbox_doc["size"]

    # --- grid size warning (to stderr so JSON output is not corrupted) ---
    grid_warning = _grid_size_warning(size, exhaustiveness)
    if grid_warning:
        click.echo(grid_warning, err=True)

    vina_ver = _vina_version(vina_path)

    # --- flexible residue handling ---
    flex_specs: list[dict[str, str]] | None = None
    if flexible_residues:
        flex_specs = _parse_flexible_residues(flexible_residues)

    docking_mode = "flexible" if flex_specs else "rigid"

    # --- split receptor once for flexible docking (before ligand loop) ---
    # The receptor split is ligand-independent; hoisting it above the loop
    # avoids redundant PDBQT splitting when docking multiple ligands.
    _flex_tmpdir: str | None = None
    if flex_specs:
        _flex_tmpdir = tempfile.mkdtemp(prefix="dde-dock-flex-")
        rigid_receptor = Path(_flex_tmpdir) / "rigid_receptor.pdbqt"
        flex_pdbqt: Path | None = Path(_flex_tmpdir) / "flexible_residues.pdbqt"
        _split_flexible_receptor(
            receptor_path,
            flex_specs,
            rigid_receptor,
            flex_pdbqt,
        )
        effective_receptor = rigid_receptor
    else:
        effective_receptor = receptor_path
        flex_pdbqt = None

    # --- derive receptor identifier for output naming ---
    # The receptor file is named {structure_stem}.receptor.pdbqt by
    # `docking prepare`.  Strip the `.receptor` suffix to get a clean,
    # deterministic, human-readable identifier.  This prevents output
    # collisions when the same ligand is docked against two different
    # receptors into the same directory (#205).
    receptor_name = receptor_path.stem  # e.g. "6LU7.receptor"
    if receptor_name.endswith(".receptor"):
        receptor_id = receptor_name[: -len(".receptor")]
    else:
        receptor_id = receptor_name

    for ligand_arg in ligands:
        ligand_path = resolve_artifact(state, ligand_arg, "ligand")
        ligand_stem = ligand_path.stem

        # --- convert SDF to PDBQT if necessary ---
        with tempfile.TemporaryDirectory(prefix="dde-dock-") as tmpdir:
            if ligand_path.suffix.lower() in (".sdf", ".mol"):
                ligand_pdbqt = Path(tmpdir) / f"{ligand_stem}.pdbqt"
                _convert_ligand_to_pdbqt(ligand_path, ligand_pdbqt)
            else:
                ligand_pdbqt = ligand_path

            # --- run Vina ---
            # Include receptor_id in output filename to prevent collision
            # when the same ligand is docked against multiple receptors (#205).
            poses_path = target_dir / f"{ligand_stem}_{receptor_id}.poses.pdbqt"
            cmd = [
                vina_path,
                "--receptor",
                str(effective_receptor),
                "--ligand",
                str(ligand_pdbqt),
                "--center_x",
                str(center[0]),
                "--center_y",
                str(center[1]),
                "--center_z",
                str(center[2]),
                "--size_x",
                str(size[0]),
                "--size_y",
                str(size[1]),
                "--size_z",
                str(size[2]),
                "--exhaustiveness",
                str(exhaustiveness),
                "--num_modes",
                str(n_poses),
                "--out",
                str(poses_path),
            ]
            if flex_pdbqt is not None:
                cmd.extend(["--flex", str(flex_pdbqt)])

            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )

        if completed.returncode != 0:
            raise ArtifactError(
                f"Vina failed for ligand {ligand_path.name}",
                detail=(completed.stderr or completed.stdout or "no output").strip()[
                    :500
                ],
                remedy="check that the receptor PDBQT, ligand, and grid box "
                "parameters are valid",
            )

        if not poses_path.is_file():
            raise ArtifactError(
                f"Vina produced no output for ligand {ligand_path.name}",
                detail="the output poses file was not written",
                remedy="check Vina logs for errors",
            )

        # --- parse poses ---
        poses_text = poses_path.read_text(encoding="utf-8")
        poses = _parse_vina_poses(poses_text)

        if not poses:
            raise ArtifactError(
                f"Vina produced no poses for ligand {ligand_path.name}",
                detail="the output PDBQT contains no REMARK VINA RESULT lines",
                remedy="increase exhaustiveness or check ligand/receptor compatibility",
            )

        best_score = min(p["affinity_kcalmol"] for p in poses)

        # --- write per-ligand result JSON ---
        result_record: dict[str, Any] = {
            "tool": "docking",
            "subcommand": "run",
            "receptor": receptor_path.name,
            "receptor_id": receptor_id,
            "ligand": ligand_path.name,
            "docking_mode": docking_mode,
            "n_poses": len(poses),
            "exhaustiveness": exhaustiveness,
            "poses": poses,
            "best_score": best_score,
        }
        if flex_specs:
            result_record["flexible_residues"] = flexible_residues
        result_path = target_dir / f"{ligand_stem}_{receptor_id}.docking_result.json"
        result_path.write_text(
            json.dumps(result_record, indent=2) + "\n", encoding="utf-8"
        )

        # --- provenance sidecar ---
        # DISTINCT from prepare's {stem}.prepare.meta.json
        sidecar_params: dict[str, Any] = {
            "receptor": receptor_path.name,
            "receptor_id": receptor_id,
            "ligand": ligand_path.name,
            "exhaustiveness": exhaustiveness,
            "n_poses": n_poses,
        }
        if flexible_residues:
            sidecar_params["flexible_residues"] = flexible_residues

        sidecar = provenance.Sidecar(
            tool="docking",
            subcommand="run",
            endpoint=None,
            parameters=sidecar_params,
        )
        sidecar.note("receptor_sha256", provenance.sha256_file(receptor_path))
        sidecar.note("ligand_sha256", provenance.sha256_file(ligand_path))
        sidecar.note("receptor_path", str(receptor_path))
        sidecar.note("ligand_path", str(ligand_path))
        sidecar.note("gridbox_path", str(gridbox_path))
        sidecar.note("docking_mode", docking_mode)
        if flex_specs:
            sidecar.note("flexible_residues", flexible_residues)
        sidecar.note("vina_binary", vina_path)
        if vina_ver:
            sidecar.note("vina_version", vina_ver)

        meeko_ver = _meeko_version()
        if meeko_ver:
            sidecar.note("meeko_version", meeko_ver)

        sidecar.add_output(result_path)
        sidecar.add_output(poses_path)
        meta_path = sidecar.write(
            target_dir / f"{ligand_stem}_{receptor_id}.docking.meta.json"
        )

        emit.path(result_path, role="result")
        emit.path(poses_path, role="poses")
        emit.path(meta_path, role="sidecar")

    # Clean up receptor split temp directory (created before the loop)
    if _flex_tmpdir:
        shutil.rmtree(_flex_tmpdir, ignore_errors=True)

    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2 — analyze
# ---------------------------------------------------------------------------


@docking.command("analyze")
@click.argument("paths", nargs=-1, required=True, type=click.Path())
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    paths: tuple[str, ...],
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply binding-energy thresholds to stored docking scores.

    Reads one or more ``.docking_result.json`` files written by
    ``docking run``, applies the ``docking-scores`` threshold set, and
    writes a ``.docking.analysis.json`` per input with a verdict and
    mandatory relays.  This command never queries an endpoint — the
    phase-2 latch enforces that automatically.

    Accepts multiple paths and glob patterns::

    \b
      dde docking analyze result1.docking_result.json result2.docking_result.json
      dde docking analyze raw/docking/*.docking_result.json
    """
    import glob as globmod

    emit = Emitter(as_json=as_json, quiet=quiet)

    # --- expand glob patterns ---
    expanded_paths: list[str] = []
    for p in paths:
        # If the shell didn't expand globs (e.g. quoted argument),
        # expand them here.
        matches = sorted(globmod.glob(p))
        if matches:
            expanded_paths.extend(matches)
        else:
            # No glob match — pass through so resolve_artifact
            # produces the standard "not found" error.
            expanded_paths.append(p)

    if not expanded_paths:
        raise UsageError(
            "no paths provided",
            remedy="pass one or more .docking_result.json paths",
        )

    # --- load thresholds once (shared across all inputs) ---
    thresholds = load_thresholds(state, "docking-scores")

    strong_threshold = thresholds.get("strong_binding_energy")
    moderate_threshold = thresholds.get("moderate_binding_energy")

    # weak_binding_energy may be UNRESOLVED — handle gracefully per the
    # unresolved threshold pattern (consistent with genetics.py and
    # expression.py): check unresolved() rather than catching .get(),
    # and only fetch a value we will actually use.
    weak_resolved = "weak_binding_energy" not in thresholds.unresolved()
    weak_threshold = thresholds.get("weak_binding_energy") if weak_resolved else None

    for path in expanded_paths:
        source = resolve_artifact(state, path, "docking result")
        result_doc = provenance.read_json(source, "docking result")
        stem = source.name.replace(".docking_result.json", "")

        # Where to look for phase-1 sidecars: --from directory, or beside
        # the result file.
        from_dir_path = (
            state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
            if from_dir
            else source.parent
        )

        # --- classify every pose by score band ---
        raw_poses = result_doc.get("poses", [])
        classified_poses: list[dict[str, Any]] = []

        for pose in raw_poses:
            score = pose["affinity_kcalmol"]
            # Vina scores are negative kcal/mol: more negative = stronger.
            # score <= threshold is the test (e.g. -8.5 <= -8.0 → strong).
            if score <= strong_threshold:
                band = "strong"
            elif score <= moderate_threshold:
                band = "moderate"
            elif weak_resolved and score <= weak_threshold:
                band = "weak"
            elif weak_resolved:
                # Score is above the weak threshold — no meaningful binding
                band = "no-binding"
            else:
                # weak_binding_energy is UNRESOLVED — score is above moderate
                # but cannot be split into weak/no-binding.  Label the band
                # descriptively rather than "unclassified" (which implies a
                # tool failure rather than a missing threshold).
                band = "above-moderate"

            classified_poses.append(
                {
                    "rank": pose["rank"],
                    "affinity_kcalmol": score,
                    "rmsd_lb": pose.get("rmsd_lb"),
                    "rmsd_ub": pose.get("rmsd_ub"),
                    "band": band,
                }
            )

        best_score = result_doc.get("best_score")
        if best_score is None and classified_poses:
            best_score = min(p["affinity_kcalmol"] for p in classified_poses)

        # --- determine verdict ---
        has_strong = any(p["band"] == "strong" for p in classified_poses)
        has_moderate = any(p["band"] == "moderate" for p in classified_poses)

        if has_strong:
            verdict = "strong-binders-found"
            statement = (
                f"At least one pose scores {best_score:.1f} kcal/mol, in the "
                f"strong predicted binding band "
                f"(≤ {strong_threshold} kcal/mol)."
            )
        elif has_moderate:
            best_moderate = min(
                p["affinity_kcalmol"]
                for p in classified_poses
                if p["band"] == "moderate"
            )
            verdict = "moderate-binders-found"
            statement = (
                f"Best pose scores {best_moderate:.1f} kcal/mol, in the "
                f"moderate predicted binding band "
                f"(≤ {moderate_threshold} kcal/mol)."
            )
        else:
            verdict = "no-significant-binding"
            score_str = f"{best_score:.1f}" if best_score is not None else "N/A"
            statement = (
                f"All {len(classified_poses)} pose(s) score above "
                f"{moderate_threshold} kcal/mol (best: {score_str} kcal/mol); "
                f"no significant predicted binding detected."
            )

        advisories: list[str] = []
        if not weak_resolved:
            advisories.append(
                "The weak_binding_energy threshold is unresolved (no cited "
                "source defines this cutoff). Poses scoring above the moderate "
                f"threshold (> {moderate_threshold} kcal/mol) are classified as "
                "'above-moderate' rather than split into weak/no-binding bands. "
                "All other classifications remain fully functional."
            )

        # --- collect upstream relays from all phase-1 sidecars ---
        relays: list[dict[str, str]] = []
        seen_codes: set[str] = set()

        # The prepare sidecar is named {receptor_id}.prepare.meta.json
        # (keyed on the structure stem), NOT {ligand}_{receptor_id}.  Use
        # the receptor_id stored in the docking result to look it up
        # correctly.  The docking sidecar IS named {stem}.docking.meta.json
        # (same composite stem as the result file).
        receptor_id = result_doc.get("receptor_id", "")
        safe_receptor_id = sanitize_slug(receptor_id) if receptor_id else ""
        safe_stem = sanitize_slug(stem) if stem else ""
        sidecar_stems = {
            "prepare": safe_receptor_id or safe_stem,
            "docking": safe_stem,
        }
        for suffix in ("prepare", "docking"):
            lookup_stem = sidecar_stems[suffix]
            meta_candidate = from_dir_path / f"{lookup_stem}.{suffix}.meta.json"
            if (
                confine_path(from_dir_path, Path(f"{lookup_stem}.{suffix}.meta.json"))
                is None
            ):
                raise ArtifactError(
                    f"sidecar path escapes base directory: {meta_candidate}",
                    remedy="check receptor_id in the docking result document",
                )
            if meta_candidate.is_file():
                meta = provenance.read_json(meta_candidate, f"{suffix} sidecar")
                for r in meta.get("mandatory_relays", []) or []:
                    if r["code"] not in seen_codes:
                        relays.append(r)
                        seen_codes.add(r["code"])
            elif suffix == "prepare":
                import logging

                logging.getLogger("dde.docking").warning(
                    "prepare sidecar not found at %s — upstream relays "
                    "(pocket quality alerts, conformation dependence, "
                    "peptide occlusion) will be missing from the analysis",
                    meta_candidate,
                )

        # --- conditional relay: score_is_not_affinity ---
        # Fires when any pose is in the strong or moderate band — the over-
        # readable result is available and may be carried as an affinity.
        if has_strong or has_moderate:
            code = "docking.score_is_not_affinity"
            if code not in seen_codes:
                ligand_name = result_doc.get("ligand", stem)
                relays.append(
                    provenance.relay(
                        code,
                        f"Best Vina score {best_score:.1f} kcal/mol for "
                        f"{ligand_name}; this is a computed interaction "
                        "energy, not a measured affinity.",
                    )
                )
                seen_codes.add(code)

        # --- build analysis record ---
        # Carry docking_mode from Phase 1 artifact into analysis output
        # for provenance completeness (rigid vs flexible).
        docking_mode = result_doc.get("docking_mode", "rigid")

        metrics: dict[str, Any] = {
            "ligand": result_doc.get("ligand"),
            "receptor": result_doc.get("receptor"),
            "docking_mode": docking_mode,
            "n_poses": len(classified_poses),
            "best_score": best_score,
            "poses": classified_poses,
        }
        assessment: dict[str, Any] = {
            "verdict": verdict,
            "statement": statement,
            "advisories": advisories,
        }

        analysis_path = beside_or_out(
            state, source, f"{stem}.docking.analysis.json", out
        )
        provenance.write_analysis(
            analysis_path,
            source=str(source),
            threshold_set=thresholds.tag,
            thresholds_applied=thresholds.applied(),
            threshold_sources=thresholds.sources(),
            threshold_provenance=thresholds.provenance,
            metrics=metrics,
            assessment=assessment,
            unresolved=thresholds.unresolved() or None,
            mandatory_relays=relays,
            suppress_warnings=as_json,
        )

        emit.data("assessment", assessment)
        emit.data("metrics", metrics)
        emit.data("mandatory_relays", relays)
        emit.line(f"{stem}  [threshold_set {thresholds.tag}]")
        emit.line(f"{verdict}: {statement}")
        for note in advisories:
            emit.line(f"  - {note}")
        for record in relays:
            emit.line(f"relay {record['code']}: {record['message']}")
        emit.path(analysis_path, role="analysis")

    emit.flush()


# ---------------------------------------------------------------------------
# contacts
# ---------------------------------------------------------------------------


@docking.command("contacts")
@click.argument("poses_pdbqt", type=click.Path())
@click.argument("receptor_pdbqt", type=click.Path())
@click.option(
    "--cutoff",
    default=4.0,
    type=float,
    show_default=True,
    help="Distance cutoff in Angstroms for contact definition.",
)
@click.option(
    "--min-distance",
    default=2.0,
    type=float,
    show_default=True,
    help="Contacts below this distance (Angstroms) are flagged as "
    "potential steric clashes (rigid-receptor docking artifacts).",
)
@out_option
@output_options
@pass_state
def contacts_cmd(
    state: AppState,
    poses_pdbqt: str,
    receptor_pdbqt: str,
    cutoff: float,
    min_distance: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Report per-pose residue contacts from docking output.

    Reads a multi-model PDBQT from ``docking run`` (poses) and the
    receptor PDBQT from ``docking prepare``, then identifies receptor
    residues within the distance cutoff of each pose.

    Contacts closer than ``--min-distance`` are flagged as potential
    steric clashes (rigid-receptor docking artifacts).

    Outputs under ``raw/docking/``:

    \b
      {stem}.contacts.json       — contact analysis results
      {stem}.contacts.meta.json  — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- resolve inputs ---
    poses_path = resolve_artifact(state, poses_pdbqt, "poses PDBQT")
    receptor_path = resolve_artifact(state, receptor_pdbqt, "receptor PDBQT")

    # Derive stem from poses filename, consistent with other subcommands.
    name = poses_path.name
    stem = (
        name[: -len(".poses.pdbqt")]
        if name.endswith(".poses.pdbqt")
        else poses_path.stem
    )

    # --- parse receptor ---
    receptor_text = receptor_path.read_text(encoding="utf-8", errors="replace")
    receptor_atoms = _parse_pdbqt_atoms(receptor_text)
    if not receptor_atoms:
        raise ArtifactError(
            f"no atom coordinates found in receptor {receptor_path.name}",
            detail="the receptor PDBQT appears empty or unparseable",
            remedy="check that the file is a valid PDBQT from `dde docking prepare`",
        )

    # --- parse poses and compute contacts ---
    poses_text = poses_path.read_text(encoding="utf-8", errors="replace")
    models = _split_pdbqt_models(poses_text)

    per_pose_contacts: list[dict[str, Any]] = []
    steric_clashes: list[dict[str, Any]] = []
    for i, model_text in enumerate(models, start=1):
        pose_atoms = _parse_pdbqt_atoms(model_text)
        contacts = _compute_contacts(pose_atoms, receptor_atoms, cutoff)
        # Flag contacts below min_distance as potential steric clashes
        for contact in contacts:
            if contact["min_distance"] < min_distance:
                contact["steric_clash_warning"] = True
                steric_clashes.append(
                    {
                        "residue": f"{contact['res_name']}{contact['res_num']}",
                        "distance": contact["min_distance"],
                        "pose": i,
                    }
                )
        per_pose_contacts.append(
            {
                "pose": i,
                "rank": i,  # rank from docking output; pose 1 = best affinity
                "n_contacts": len(contacts),
                "residues": contacts,
            }
        )

    # --- emit steric clash warning to stderr ---
    if steric_clashes:
        clash_details = ", ".join(
            f"{c['residue']} {c['distance']:.2f} A (pose {c['pose']})"
            for c in steric_clashes
        )
        click.echo(
            f"warning: {len(steric_clashes)} contact(s) below "
            f"{min_distance} A (potential steric clashes): {clash_details}",
            err=True,
        )

    # --- write contacts JSON artifact ---
    contacts_record: dict[str, Any] = {
        "tool": "docking",
        "subcommand": "contacts",
        "poses_file": poses_path.name,
        "receptor_file": receptor_path.name,
        "cutoff_angstrom": cutoff,
        "min_distance_threshold": min_distance,
        "n_poses": len(per_pose_contacts),
        "contacts": per_pose_contacts,
        "steric_clashes": steric_clashes,
    }
    contacts_path = target_dir / f"{stem}.contacts.json"
    contacts_path.write_text(
        json.dumps(contacts_record, indent=2) + "\n", encoding="utf-8"
    )

    # --- provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="docking",
        subcommand="contacts",
        endpoint=None,
        parameters={
            "poses": poses_path.name,
            "receptor": receptor_path.name,
            "cutoff_angstrom": cutoff,
            "min_distance_threshold": min_distance,
        },
    )
    sidecar.note("poses_sha256", provenance.sha256_file(poses_path))
    sidecar.note("receptor_sha256", provenance.sha256_file(receptor_path))
    sidecar.note("poses_path", str(poses_path))
    sidecar.note("receptor_path", str(receptor_path))
    sidecar.note("n_poses", len(per_pose_contacts))

    # --- propagate upstream relays from input sidecars ---
    # Look for .meta.json sidecars adjacent to the input files,
    # following the relay propagation pattern from analyze_cmd.
    for input_path, suffixes in (
        (poses_path, ("docking", "prepare")),
        (receptor_path, ("prepare",)),
    ):
        base = input_path.name
        for ext in (".poses.pdbqt", ".receptor.pdbqt", ".pdbqt"):
            if base.endswith(ext):
                base = base[: -len(ext)]
                break
        for suffix in suffixes:
            meta_candidate = input_path.parent / f"{base}.{suffix}.meta.json"
            if meta_candidate.is_file():
                meta = provenance.read_json(meta_candidate, f"{suffix} sidecar")
                for r in meta.get("mandatory_relays", []) or []:
                    if not any(
                        existing["code"] == r["code"] for existing in sidecar.relays
                    ):
                        sidecar.relays.append(r)

    sidecar.warn(
        "Contact distances are geometric proximity in a static docked "
        "pose; no hydrogen-bond geometry, electrostatic complementarity, "
        "or reactive-orientation analysis was performed.",
        code="docking.contact_is_not_binding_event",
    )

    sidecar.add_output(contacts_path)
    meta_path = sidecar.write(target_dir / f"{stem}.contacts.meta.json")

    # --- emit contacts summary per pose ---
    for pose_result in per_pose_contacts:
        emit.line(f"pose {pose_result['pose']}: {pose_result['n_contacts']} contact(s)")
    if steric_clashes:
        emit.line(
            f"steric clashes: {len(steric_clashes)} contact(s) below {min_distance} A"
        )
    if sidecar.relays:
        for record in sidecar.relays:
            emit.line(f"relay {record['code']}: {record['message']}")
    emit.data("contacts", per_pose_contacts)
    if steric_clashes:
        emit.data("steric_clashes", steric_clashes)
    if sidecar.relays:
        emit.data("mandatory_relays", sidecar.relays)
    emit.path(contacts_path, role="contacts")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# contacts-matrix
# ---------------------------------------------------------------------------


def _compute_contacts_for_file(
    poses_path: Path,
    receptor_atoms: list[dict[str, Any]],
    cutoff: float,
) -> list[dict[str, Any]]:
    """Compute contacts for all poses in a single PDBQT file.

    Returns a flat list of contact dicts (chain, res_name, res_num,
    min_distance) across all poses, keeping only the minimum distance
    per residue across all poses in the file.
    """
    poses_text = poses_path.read_text(encoding="utf-8", errors="replace")
    models = _split_pdbqt_models(poses_text)

    # Aggregate contacts across poses: keep minimum distance per residue
    residue_min: dict[tuple[str, str, int], float] = {}
    for model_text in models:
        pose_atoms = _parse_pdbqt_atoms(model_text)
        contacts = _compute_contacts(pose_atoms, receptor_atoms, cutoff)
        for contact in contacts:
            key = (contact["chain"], contact["res_name"], contact["res_num"])
            dist = contact["min_distance"]
            if key not in residue_min or dist < residue_min[key]:
                residue_min[key] = dist

    result: list[dict[str, Any]] = []
    for (chain, res_name, res_num), min_dist in sorted(
        residue_min.items(), key=lambda x: x[1]
    ):
        result.append(
            {
                "chain": chain,
                "res_name": res_name,
                "res_num": res_num,
                "min_distance": min_dist,
            }
        )
    return result


@docking.command("contacts-matrix")
@click.argument("poses_pdbqt", nargs=-1, required=True, type=click.Path())
@click.option(
    "--receptor",
    required=True,
    type=click.Path(),
    help="Receptor PDBQT file (shared across all pose files).",
)
@click.option(
    "--cutoff",
    default=4.0,
    type=float,
    show_default=True,
    help="Distance cutoff in Angstroms for contact definition.",
)
@out_option
@output_options
@pass_state
def contacts_matrix_cmd(
    state: AppState,
    poses_pdbqt: tuple[str, ...],
    receptor: str,
    cutoff: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Cross-compound contact frequency matrix from multiple pose files.

    Takes multiple pose PDBQT files (one per compound) and a shared
    receptor PDBQT.  For each pose file, runs the same contact analysis
    as ``docking contacts``, then builds a cross-compound matrix: rows
    are residues, columns are compounds, values are minimum distances
    (null if no contact within cutoff).

    Consensus contacts — residues contacted by all or most compounds —
    are identified and reported.

    Outputs under ``raw/docking/``:

    \b
      matrix.contacts-matrix.json       — cross-compound contact matrix
      matrix.contacts-matrix.meta.json  — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- resolve receptor ---
    receptor_path = resolve_artifact(state, receptor, "receptor PDBQT")
    receptor_text = receptor_path.read_text(encoding="utf-8", errors="replace")
    receptor_atoms = _parse_pdbqt_atoms(receptor_text)
    if not receptor_atoms:
        raise ArtifactError(
            f"no atom coordinates found in receptor {receptor_path.name}",
            detail="the receptor PDBQT appears empty or unparseable",
            remedy="check that the file is a valid PDBQT from `dde docking prepare`",
        )

    # --- process each compound's pose file ---
    compound_names: list[str] = []
    # residue_key → {compound_name → min_distance}
    all_residues: dict[tuple[str, str, int], dict[str, float]] = {}

    sidecar = provenance.Sidecar(
        tool="docking",
        subcommand="contacts-matrix",
        endpoint=None,
        parameters={
            "receptor": receptor_path.name,
            "n_compounds": len(poses_pdbqt),
            "cutoff_angstrom": cutoff,
        },
    )
    sidecar.note("receptor_sha256", provenance.sha256_file(receptor_path))
    sidecar.note("receptor_path", str(receptor_path))

    n_succeeded = 0
    n_failed = 0
    failed_compounds: list[dict[str, Any]] = []

    for poses_arg in poses_pdbqt:
        poses_path = resolve_artifact(state, poses_arg, "poses PDBQT")
        name = poses_path.name
        compound_stem = (
            name[: -len(".poses.pdbqt")]
            if name.endswith(".poses.pdbqt")
            else poses_path.stem
        )
        compound_names.append(compound_stem)

        try:
            contacts = _compute_contacts_for_file(poses_path, receptor_atoms, cutoff)

            for contact in contacts:
                key = (contact["chain"], contact["res_name"], contact["res_num"])
                all_residues.setdefault(key, {})[compound_stem] = contact[
                    "min_distance"
                ]

            sidecar.note(
                f"poses_sha256_{compound_stem}",
                provenance.sha256_file(poses_path),
            )
            n_succeeded += 1
            emit.line(f"{compound_stem}: {len(contacts)} residue(s) within {cutoff} A")
        except Exception as exc:
            n_failed += 1
            failed_compounds.append(
                {
                    "compound": compound_stem,
                    "error": str(exc),
                }
            )
            emit.line(f"{compound_stem}: error — {str(exc)[:100]}")

    # --- build matrix ---
    # Sort residues by chain + res_num for stable output
    sorted_residues = sorted(all_residues.keys(), key=lambda k: (k[0], k[2]))

    matrix_rows: list[dict[str, Any]] = []
    for chain, res_name, res_num in sorted_residues:
        distances = all_residues[(chain, res_name, res_num)]
        row: dict[str, Any] = {
            "chain": chain,
            "res_name": res_name,
            "res_num": res_num,
            "residue_label": f"{chain}:{res_name}{res_num}"
            if chain
            else f"{res_name}{res_num}",
            "distances": {},
            "n_compounds": len(distances),
        }
        for compound in compound_names:
            row["distances"][compound] = distances.get(compound)
        matrix_rows.append(row)

    # --- identify consensus contacts ---
    n_compounds = len(compound_names)
    consensus_contacts: list[dict[str, Any]] = []
    for row in matrix_rows:
        if row["n_compounds"] == n_compounds:
            consensus_contacts.append(
                {
                    "residue_label": row["residue_label"],
                    "chain": row["chain"],
                    "res_name": row["res_name"],
                    "res_num": row["res_num"],
                    "contacted_by_all": True,
                    "distances": row["distances"],
                }
            )

    # Also identify near-consensus: contacted by majority (> 50%)
    majority_threshold = n_compounds / 2.0
    near_consensus: list[dict[str, Any]] = []
    for row in matrix_rows:
        if row["n_compounds"] > majority_threshold and row["n_compounds"] < n_compounds:
            near_consensus.append(
                {
                    "residue_label": row["residue_label"],
                    "chain": row["chain"],
                    "res_name": row["res_name"],
                    "res_num": row["res_num"],
                    "n_compounds": row["n_compounds"],
                    "fraction": round(row["n_compounds"] / n_compounds, 3),
                    "distances": row["distances"],
                }
            )

    # --- write matrix artifact ---
    matrix_record: dict[str, Any] = {
        "tool": "docking",
        "subcommand": "contacts-matrix",
        "receptor": receptor_path.name,
        "compounds": compound_names,
        "n_compounds": n_compounds,
        "n_succeeded": n_succeeded,
        "n_failed": n_failed,
        "cutoff_angstrom": cutoff,
        "n_residues": len(matrix_rows),
        "matrix": matrix_rows,
        "consensus_contacts": consensus_contacts,
        "near_consensus_contacts": near_consensus,
        "failed_compounds": failed_compounds,
    }

    matrix_path = target_dir / "matrix.contacts-matrix.json"
    matrix_path.write_text(json.dumps(matrix_record, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(matrix_path)

    # --- propagate upstream relays from input sidecars ---
    seen_codes: set[str] = set()
    for poses_arg in poses_pdbqt:
        poses_path = resolve_artifact(state, poses_arg, "poses PDBQT")
        base = poses_path.name
        for ext in (".poses.pdbqt", ".pdbqt"):
            if base.endswith(ext):
                base = base[: -len(ext)]
                break
        for suffix in ("docking", "prepare"):
            meta_candidate = poses_path.parent / f"{base}.{suffix}.meta.json"
            if meta_candidate.is_file():
                meta = provenance.read_json(meta_candidate, f"{suffix} sidecar")
                for r in meta.get("mandatory_relays", []) or []:
                    if r["code"] not in seen_codes:
                        sidecar.relays.append(r)
                        seen_codes.add(r["code"])

    # --- propagate upstream relays from receptor sidecar ---
    receptor_base = receptor_path.name
    for ext in (".receptor.pdbqt", ".pdbqt"):
        if receptor_base.endswith(ext):
            receptor_base = receptor_base[: -len(ext)]
            break
    receptor_meta = receptor_path.parent / f"{receptor_base}.prepare.meta.json"
    if receptor_meta.is_file():
        meta = provenance.read_json(receptor_meta, "prepare sidecar")
        for r in meta.get("mandatory_relays", []) or []:
            if r["code"] not in seen_codes:
                sidecar.relays.append(r)
                seen_codes.add(r["code"])

    meta_path = sidecar.write(target_dir / "matrix.contacts-matrix.meta.json")

    # --- emit summary ---
    emit.line(f"matrix: {len(matrix_rows)} residue(s) across {n_compounds} compound(s)")
    if consensus_contacts:
        labels = ", ".join(c["residue_label"] for c in consensus_contacts[:5])
        suffix = (
            f" (+{len(consensus_contacts) - 5} more)"
            if len(consensus_contacts) > 5
            else ""
        )
        emit.line(f"consensus ({len(consensus_contacts)}): {labels}{suffix}")
    else:
        emit.line("no consensus contacts (no residue contacted by all compounds)")

    emit.data("n_compounds", n_compounds)
    emit.data("n_residues", len(matrix_rows))
    emit.data("n_consensus", len(consensus_contacts))
    emit.data("consensus_contacts", consensus_contacts)
    if sidecar.relays:
        emit.data("mandatory_relays", sidecar.relays)
    emit.path(matrix_path, role="contacts-matrix")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# validate-pose
# ---------------------------------------------------------------------------

VALIDATE_POSE_SCHEMA = "dde.docking-pose-validation.v1"


def _parse_heavy_atoms_pdb(text: str) -> list[tuple[float, float, float]]:
    """Extract heavy-atom coordinates from PDB-format text."""
    coords: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except (ValueError, IndexError):
            continue
        # Exclude hydrogens
        atom_name = line[12:16].strip() if len(line) > 16 else ""
        element = line[76:78].strip() if len(line) >= 78 else ""
        if not element:
            element = atom_name.lstrip("0123456789")[:1]
        if element in ("H", "D"):
            continue
        coords.append((x, y, z))
    return coords


def _parse_heavy_atoms_sdf(text: str) -> list[tuple[float, float, float]]:
    """Extract heavy-atom coordinates from an SDF/MOL file.

    SDF V2000 format: after the header (3 lines) + counts line, each
    atom block line has format:
      x(10.4) y(10.4) z(10.4) symbol(3) ...
    """
    lines = text.splitlines()
    if len(lines) < 4:
        return []

    # Counts line (line index 3) — first two fields are atom_count, bond_count
    counts_line = lines[3]
    try:
        parts = counts_line.split()
        n_atoms = int(parts[0])
    except (ValueError, IndexError):
        return []

    coords: list[tuple[float, float, float]] = []
    for i in range(4, min(4 + n_atoms, len(lines))):
        line = lines[i]
        try:
            x = float(line[0:10])
            y = float(line[10:20])
            z = float(line[20:30])
            symbol = line[31:34].strip()
        except (ValueError, IndexError):
            continue
        if symbol in ("H", "D"):
            continue
        coords.append((x, y, z))
    return coords


def _parse_heavy_atoms_mol2(text: str) -> list[tuple[float, float, float]]:
    """Extract heavy-atom coordinates from a MOL2 file.

    MOL2 atom records appear in the @<TRIPOS>ATOM block.  Each line:
      atom_id atom_name x y z atom_type [subst_id subst_name charge]
    """
    coords: list[tuple[float, float, float]] = []
    in_atom_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("@<TRIPOS>ATOM"):
            in_atom_block = True
            continue
        if stripped.startswith("@<TRIPOS>") and in_atom_block:
            break
        if not in_atom_block:
            continue
        parts = stripped.split()
        if len(parts) < 6:
            continue
        try:
            x = float(parts[2])
            y = float(parts[3])
            z = float(parts[4])
        except ValueError:
            continue
        # atom_type is e.g. "C.3", "N.am", "H" — extract element
        atom_type = parts[5]
        element = atom_type.split(".")[0]
        if element in ("H", "D"):
            continue
        coords.append((x, y, z))
    return coords


def _parse_ligand_coords(
    path: Path,
) -> list[tuple[float, float, float]]:
    """Parse heavy-atom coordinates from a ligand file.

    Supports PDB, SDF/MOL, MOL2, and PDBQT formats.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()

    if suffix in (".sdf", ".mol"):
        coords = _parse_heavy_atoms_sdf(text)
    elif suffix == ".mol2":
        coords = _parse_heavy_atoms_mol2(text)
    else:
        # PDB or PDBQT
        coords = _parse_heavy_atoms_pdb(text)

    if not coords:
        raise ArtifactError(
            f"no heavy-atom coordinates found in {path.name}",
            detail=f"format detected from extension: {suffix}",
            remedy="check that the file is a valid ligand coordinate file "
            "(PDB, SDF, MOL2, or PDBQT) with heavy atoms",
        )
    return coords


def _compute_ligand_rmsd(
    coords_a: list[tuple[float, float, float]],
    coords_b: list[tuple[float, float, float]],
) -> float:
    """Compute heavy-atom RMSD between two sets of coordinates.

    Assumes 1:1 atom correspondence by order (same molecule, same atom
    ordering).  This is standard for docking pose validation where the
    docked pose and reference ligand have identical atom ordering.

    Raises ArtifactError if atom counts differ.
    """
    import numpy as np

    if len(coords_a) != len(coords_b):
        raise ArtifactError(
            f"atom count mismatch: docked pose has {len(coords_a)} heavy atoms, "
            f"reference has {len(coords_b)}",
            detail="pose validation requires identical atom ordering between "
            "the docked pose and reference ligand",
            remedy="ensure both files describe the same molecule with the "
            "same atom order",
        )

    a = np.array(coords_a)
    b = np.array(coords_b)
    diff = a - b
    rmsd = float(np.sqrt(np.mean(np.sum(diff * diff, axis=1))))
    return rmsd


@docking.command("validate-pose")
@click.argument("docked_pose", type=click.Path())
@click.argument("reference_ligand", type=click.Path())
@click.option(
    "--threshold",
    default=2.0,
    type=float,
    show_default=True,
    help="RMSD threshold in Angstroms for pass/fail.",
)
@click.option(
    "--warn-only",
    is_flag=True,
    default=False,
    help="Fire an advisory relay instead of blocking when RMSD exceeds threshold.",
)
@out_option
@output_options
@pass_state
def validate_pose_cmd(
    state: AppState,
    docked_pose: str,
    reference_ligand: str,
    threshold: float,
    warn_only: bool,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Validate a docked pose against a reference ligand placement.

    Computes heavy-atom RMSD between a docked pose and a reference
    ligand coordinate file.  Reports pass/fail against the threshold
    and emits appropriate relays.

    Accepts PDB, SDF/MOL, MOL2, and PDBQT ligand formats.

    \b
    Outputs:
      {stem}.pose-validation.artifact.json — validation record
      {stem}.pose-validation.meta.json     — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    # --- resolve inputs ---
    pose_path = resolve_artifact(state, docked_pose, "docked pose")
    ref_path = resolve_artifact(state, reference_ligand, "reference ligand")

    # --- parse coordinates ---
    pose_coords = _parse_ligand_coords(pose_path)
    ref_coords = _parse_ligand_coords(ref_path)

    # --- compute RMSD ---
    rmsd = _compute_ligand_rmsd(pose_coords, ref_coords)
    n_atoms = len(pose_coords)
    passed = rmsd <= threshold

    # --- output paths ---
    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    stem = f"{pose_path.stem}_vs_{ref_path.stem}"

    # --- build artifact record ---
    artifact_record: dict[str, Any] = {
        "schema": VALIDATE_POSE_SCHEMA,
        "rmsd": round(rmsd, 4),
        "threshold": threshold,
        "pass": passed,
        "n_atoms": n_atoms,
        "reference_file": ref_path.name,
        "pose_file": pose_path.name,
        "warn_only": warn_only,
    }

    artifact_path = target_dir / f"{stem}.pose-validation.artifact.json"
    artifact_path.write_text(
        json.dumps(artifact_record, indent=2) + "\n", encoding="utf-8"
    )

    # --- provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="docking",
        subcommand="validate-pose",
        endpoint=None,
        parameters={
            "docked_pose": pose_path.name,
            "reference_ligand": ref_path.name,
            "threshold": threshold,
            "warn_only": warn_only,
        },
    )
    sidecar.note("pose_sha256", provenance.sha256_file(pose_path))
    sidecar.note("ref_sha256", provenance.sha256_file(ref_path))
    sidecar.note("rmsd", round(rmsd, 4))
    sidecar.note("pass", passed)
    sidecar.note("n_atoms", n_atoms)
    sidecar.add_output(artifact_path)

    # --- relays ---
    if not passed:
        if warn_only:
            sidecar.warn(
                f"Pose-reproduction RMSD ({rmsd:.2f} A) exceeds threshold "
                f"({threshold:.1f} A) for {pose_path.name} vs "
                f"{ref_path.name}. Running with --warn-only; this does not "
                "block but the docking protocol lacks validated pose control.",
                code="docking.no_pose_control",
            )
        else:
            sidecar.warn(
                f"Docked pose {pose_path.name} deviates {rmsd:.2f} A from "
                f"reference {ref_path.name} (threshold: {threshold:.1f} A). "
                "The docking protocol did not reproduce the known binding mode.",
                code="docking.pose_reproduction_failed",
            )

    meta_path = sidecar.write(target_dir / f"{stem}.pose-validation.meta.json")

    # --- output ---
    verdict = "PASS" if passed else "FAIL"
    emit.line(f"Pose validation: {pose_path.name} vs {ref_path.name}")
    emit.line(f"  RMSD: {rmsd:.4f} A ({n_atoms} heavy atoms)")
    emit.line(f"  Threshold: {threshold:.1f} A")
    emit.line(f"  Verdict: {verdict}")
    if warn_only and not passed:
        emit.line("  (--warn-only: advisory, not blocking)")

    emit.data("schema", VALIDATE_POSE_SCHEMA)
    emit.data("rmsd", round(rmsd, 4))
    emit.data("threshold", threshold)
    emit.data("pass", passed)
    emit.data("n_atoms", n_atoms)
    if sidecar.relays:
        emit.data("mandatory_relays", sidecar.relays)
    emit.path(artifact_path, role="pose-validation")
    emit.path(meta_path, role="sidecar")
    emit.flush()
