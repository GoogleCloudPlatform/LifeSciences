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

"""Shared pipeline helpers for multi-step docking workflows.

Extracted from ``commands/analog.py`` for reuse by ``commands/screen.py``
(and any future workflow command that chains validate-prepare-dock).

These are pipeline step implementations (validate-then-fragment,
embed-then-optimize, convert-then-check, dock-then-parse), not general
chemistry utilities.  ``pipeline.py`` is a more accurate name for what
they are than ``chem.py`` would be.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def slug(smiles: str) -> str:
    """Derive a deterministic, filesystem-safe slug from canonical SMILES."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", smiles).strip("-").lower()
    if not s or len(s) < 3:
        s = "mol-" + hashlib.sha256(smiles.encode()).hexdigest()[:16]
    return s[:80]


def name_slug(name: str | None, smiles: str) -> str:
    """Return a filesystem-safe identifier from a name or SMILES."""
    if name:
        s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
        if s and len(s) >= 2:
            return s[:80]
    return slug(smiles)


def validate_smiles(smiles: str, Chem: Any) -> tuple[Any, str] | None:
    """Validate a SMILES string, returning (mol, canonical) or None on failure.

    Handles multi-fragment SMILES by keeping the largest fragment,
    following the compound.py convention.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    frags = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    if len(frags) > 1:
        frags_sorted = sorted(
            [(f, f.GetNumHeavyAtoms()) for f in frags],
            key=lambda x: x[1],
            reverse=True,
        )
        mol = frags_sorted[0][0]

    canonical = Chem.MolToSmiles(mol)
    return mol, canonical


def prepare_3d(
    mol: Any,
    canonical: str,
    Chem: Any,
    AllChem: Any,
) -> tuple[Any, str, bool] | None:
    """Generate 3D coordinates using ETKDGv3 + MMFF/UFF.

    Returns ``(mol_h, force_field, converged)`` or None on failure.
    Follows the compound.py prepare-3d pattern exactly.
    """
    mol_h = Chem.AddHs(mol)

    params = AllChem.ETKDGv3()
    embed_result = AllChem.EmbedMolecule(mol_h, params)
    if embed_result == -1:
        return None

    force_field = "MMFF"
    converged = False

    try:
        ff_result = AllChem.MMFFOptimizeMolecule(mol_h)
        if ff_result == -1:
            raise RuntimeError("MMFF parameterization failed")
        converged = ff_result == 0
    except RuntimeError:
        force_field = "UFF"
        try:
            ff_result = AllChem.UFFOptimizeMolecule(mol_h)
            converged = ff_result == 0
        except (ValueError, RuntimeError):
            return None

    return mol_h, force_field, converged


def prepare_ligand_pdbqt(sdf_path: Path, output_path: Path) -> bool:
    """Convert SDF to PDBQT via mk_prepare_ligand.py, if available.

    Returns True on success, False if the tool is unavailable or fails.
    """
    script = shutil.which("mk_prepare_ligand.py")
    if not script:
        return False

    try:
        completed = subprocess.run(
            [script, "-i", str(sdf_path), "-o", str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            completed.returncode == 0
            and output_path.is_file()
            and output_path.stat().st_size > 0
        )
    except OSError:
        return False


def run_docking(
    ligand_pdbqt: Path,
    receptor_path: Path,
    gridbox_path: Path,
    *,
    exhaustiveness: int = 8,
    n_poses: int = 9,
) -> dict[str, Any] | None:
    """Invoke AutoDock Vina with explicit receptor and grid box paths.

    Returns a dict with docking results (``best_score``, ``n_poses``,
    ``poses``, ``poses_text``), or None on failure.

    Unlike the original ``analog.py`` helper, the grid box path is
    accepted explicitly — ``screen`` receives it from ``--gridbox``
    while ``analog`` computes it from the receptor naming convention.
    """
    vina_path = shutil.which("vina")
    if not vina_path:
        return None

    if not gridbox_path.is_file():
        return None

    try:
        gridbox_doc = json.loads(gridbox_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    center = gridbox_doc.get("center")
    size = gridbox_doc.get("size")
    if not center or not size:
        return None

    with tempfile.NamedTemporaryFile(
        suffix=".pdbqt",
        prefix="dde-dock-",
        delete=False,
    ) as poses_file:
        poses_path = Path(poses_file.name)

    try:
        cmd = [
            vina_path,
            "--receptor",
            str(receptor_path),
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
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if completed.returncode != 0 or not poses_path.is_file():
            return None

        # Parse poses for scores
        poses_text = poses_path.read_text(encoding="utf-8")
        poses: list[dict[str, Any]] = []
        for line in poses_text.splitlines():
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

        if not poses:
            return None

        best_score = min(p["affinity_kcalmol"] for p in poses)
        return {
            "best_score": best_score,
            "n_poses": len(poses),
            "poses": poses,
            "poses_text": poses_text,
        }
    finally:
        poses_path.unlink(missing_ok=True)
