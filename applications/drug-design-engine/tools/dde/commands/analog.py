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

"""`dde analog` — analog design/validation workflow.

Orchestrates the computational validation pipeline for proposed analog
compounds: validate → prepare-3d → dock → ADMET predict → rank.

This is a workflow command that chains existing dde tools rather than
reimplementing their logic.  Each analog is run through as many pipeline
steps as available, with graceful fallback when tools are missing.

Phase 1 only — no thresholds are applied, no judgment is made.  The
pipeline records what it computed and what it could not.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    out_option,
    output_options,
    pass_state,
)
from ..core import provenance
from ..core.errors import DependencyError
from ..core.output import Emitter
from ..core.pipeline import (
    name_slug as _name_slug,
)
from ..core.pipeline import (
    prepare_3d as _prepare_3d,
)
from ..core.pipeline import (
    prepare_ligand_pdbqt as _prepare_ligand_pdbqt,
)
from ..core.pipeline import (
    run_docking as _run_docking_impl,
)
from ..core.pipeline import (
    validate_smiles as _validate_smiles,
)

ARTIFACT_CLASS = "analogs"


# ---------------------------------------------------------------------------
# RDKit lazy import
# ---------------------------------------------------------------------------


def _require_rdkit():
    """Lazy-import RDKit, raising DependencyError if absent."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors

        return Chem, AllChem, Descriptors, rdMolDescriptors
    except ImportError as e:
        raise DependencyError(
            "RDKit is not installed",
            detail="analog evaluation requires RDKit for SMILES validation "
            "and 3D coordinate generation",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi)",
        ) from e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_docking(
    ligand_pdbqt: Path,
    receptor_path: Path,
    pocket_path: Path,
) -> dict[str, Any] | None:
    """Invoke AutoDock Vina, deriving gridbox from the receptor naming convention.

    Thin wrapper around ``core.pipeline.run_docking`` that computes the
    gridbox path the way ``analog evaluate`` always did.
    """
    gridbox_path = receptor_path.parent / (
        receptor_path.name.replace(".receptor.pdbqt", ".gridbox.json")
    )
    return _run_docking_impl(ligand_pdbqt, receptor_path, gridbox_path, n_poses=3)


def _predict_admet(
    mol: Any,
    canonical: str,
    Chem: Any,
    Descriptors: Any,
    rdMolDescriptors: Any,
) -> dict[str, Any]:
    """Run ADMET predictions on a molecule, following admet.py logic.

    Returns a dict of endpoint predictions.
    """
    logp = round(Descriptors.MolLogP(mol), 2)
    mw = round(Descriptors.MolWt(mol), 2)
    tpsa = round(Descriptors.TPSA(mol), 2)
    aromatic_rings = Descriptors.NumAromaticRings(mol)
    rotatable_bonds = rdMolDescriptors.CalcNumRotatableBonds(mol)

    # Metabolic stability (Gleeson 2008)
    if logp > 4 and aromatic_rings >= 3:
        metab_class = "high"
    elif 1 <= logp <= 3:
        metab_class = "low"
    else:
        metab_class = "moderate"

    # CYP inhibition risk
    basic_n_smarts = "[NH2,NH1,NH0;!$(N=*);!$(N#*)]"
    acidic_smarts = "[CX3](=O)[OX2H1]"
    basic_n_pattern = Chem.MolFromSmarts(basic_n_smarts)
    acidic_pattern = Chem.MolFromSmarts(acidic_smarts)
    has_basic_n = basic_n_pattern is not None and mol.HasSubstructMatch(basic_n_pattern)
    has_acidic = acidic_pattern is not None and mol.HasSubstructMatch(acidic_pattern)

    cyp_flags: list[str] = []
    if logp > 4 and has_basic_n:
        cyp_flags.append("CYP2D6")
    if mw > 400 and logp > 3:
        cyp_flags.append("CYP3A4")
    if logp > 2 and has_acidic:
        cyp_flags.append("CYP2C9")

    # Permeability (Egan 2000)
    if mw > 500:
        perm_class = "low"
    elif tpsa <= 132 and -1 <= logp <= 6:
        perm_class = "high"
    elif tpsa > 150:
        perm_class = "low"
    else:
        perm_class = "moderate"

    # hERG liability (Aronov 2005)
    herg_flagged = has_basic_n and logp > 3.7 and aromatic_rings >= 2

    # Solubility — ESOL (Delaney 2004)
    heavy_atoms = mol.GetNumHeavyAtoms()
    aromatic_atoms = sum(1 for atom in mol.GetAtoms() if atom.GetIsAromatic())
    aromatic_proportion = aromatic_atoms / heavy_atoms if heavy_atoms > 0 else 0.0
    predicted_logs = round(
        0.16
        - 0.63 * logp
        - 0.0062 * mw
        + 0.066 * rotatable_bonds
        - 0.74 * aromatic_proportion,
        3,
    )
    if predicted_logs > -1:
        sol_class = "high"
    elif predicted_logs >= -3:
        sol_class = "moderate"
    elif predicted_logs >= -5:
        sol_class = "low"
    else:
        sol_class = "insoluble"

    return {
        "molecular_descriptors": {
            "logp": logp,
            "mw": mw,
            "tpsa": tpsa,
            "aromatic_rings": aromatic_rings,
            "rotatable_bonds": rotatable_bonds,
        },
        "metabolic_stability": metab_class,
        "cyp_inhibition_flags": cyp_flags,
        "permeability": perm_class,
        "herg_flagged": herg_flagged,
        "solubility_class": sol_class,
        "solubility_logS": predicted_logs,
    }


def _compute_mpo_score(
    docking_score: float | None,
    admet: dict[str, Any],
    weights: dict[str, float],
) -> float:
    """Compute a multi-parameter optimization (MPO) score.

    Normalizes each component to [0, 1] and applies weights.
    Higher is better.
    """
    scores: dict[str, float] = {}

    # Docking score: more negative is better; normalize -12..0 → 1..0
    if docking_score is not None and "docking" in weights:
        norm = max(0.0, min(1.0, -docking_score / 12.0))
        scores["docking"] = norm * weights["docking"]

    # Solubility: high > moderate > low > insoluble → 1.0, 0.75, 0.5, 0.0
    sol_map = {"high": 1.0, "moderate": 0.75, "low": 0.5, "insoluble": 0.0}
    if "solubility" in weights:
        sol_val = sol_map.get(admet.get("solubility_class", ""), 0.5)
        scores["solubility"] = sol_val * weights["solubility"]

    # Permeability: high > moderate > low → 1.0, 0.5, 0.0
    perm_map = {"high": 1.0, "moderate": 0.5, "low": 0.0}
    if "permeability" in weights:
        perm_val = perm_map.get(admet.get("permeability", ""), 0.5)
        scores["permeability"] = perm_val * weights["permeability"]

    # Metabolic stability: low > moderate > high → 1.0, 0.5, 0.0
    metab_map = {"low": 1.0, "moderate": 0.5, "high": 0.0}
    if "metabolic_stability" in weights:
        metab_val = metab_map.get(admet.get("metabolic_stability", ""), 0.5)
        scores["metabolic_stability"] = metab_val * weights["metabolic_stability"]

    # hERG: not flagged → 1.0, flagged → 0.0
    if "herg" in weights:
        herg_val = 0.0 if admet.get("herg_flagged", False) else 1.0
        scores["herg"] = herg_val * weights["herg"]

    # CYP: no flags → 1.0, some flags → penalise proportionally
    if "cyp" in weights:
        n_cyp = len(admet.get("cyp_inhibition_flags", []))
        cyp_val = max(0.0, 1.0 - n_cyp / 3.0)
        scores["cyp"] = cyp_val * weights["cyp"]

    total_weight = sum(weights.get(k, 0) for k in scores)
    if total_weight == 0:
        return 0.0
    return round(sum(scores.values()) / total_weight, 4)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def analog() -> None:
    """Analog design and validation workflows."""


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


@analog.command("evaluate")
@click.argument("analogs_json", type=click.Path())
@click.option(
    "--receptor",
    type=click.Path(),
    default=None,
    help="Path to prepared receptor PDBQT (from `docking prepare`).",
)
@click.option(
    "--pocket",
    type=click.Path(),
    default=None,
    help="Path to pocket record JSON (from `pocket run`).",
)
@click.option(
    "--weights",
    type=click.Path(),
    default=None,
    help="MPO weights JSON file for ranking. "
    "Keys: docking, solubility, permeability, metabolic_stability, herg, cyp. "
    "If omitted, rank by docking score (if available) or ADMET profile.",
)
@out_option
@output_options
@pass_state
def evaluate_cmd(
    state: AppState,
    analogs_json: str,
    receptor: str | None,
    pocket: str | None,
    weights: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Evaluate proposed analogs through the computational validation pipeline.

    Takes a JSON file of analog SMILES and runs each through:
    validate → prepare-3d → dock → ADMET predict → rank.

    Pipeline failures for individual analogs are recorded and the
    evaluation continues with the next analog.  At minimum, validate +
    prepare-3d + ADMET should work; docking requires a receptor and
    pocket plus external tools.

    \b
    Outputs under raw/analogs/:
      analog-evaluation.json       — comprehensive results with ranking
      analog-evaluation.meta.json  — provenance sidecar
      {name}.3d.sdf                — per-analog 3D structures
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    Chem, AllChem, Descriptors, rdMolDescriptors = _require_rdkit()

    # --- read analogs input ---
    analogs_path = Path(analogs_json)
    if not analogs_path.is_absolute():
        analogs_path = state.project().root / analogs_path
    if not analogs_path.is_file():
        from ..core.errors import ArtifactError

        raise ArtifactError(
            f"analogs JSON not found: {analogs_path}",
            detail="relative paths resolve against the project root",
            remedy="provide a valid path to the analogs JSON file",
        )
    try:
        analogs_raw = json.loads(analogs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        from ..core.errors import ArtifactError

        raise ArtifactError(
            f"invalid JSON in {analogs_path.name}: {exc}",
            remedy="check the JSON syntax in the analogs file",
        ) from exc

    if not isinstance(analogs_raw, list):
        from ..core.errors import UsageError

        raise UsageError(
            "analogs JSON must be a list of objects",
            detail=f"got {type(analogs_raw).__name__}",
            remedy='expected format: [{"smiles": "...", "name": "...", "rationale": "..."}, ...]',
        )

    # --- validate each analog entry ---
    analogs: list[dict[str, Any]] = []
    for i, entry in enumerate(analogs_raw):
        if not isinstance(entry, dict):
            click.echo(
                f"warning: analog #{i}: expected object, got {type(entry).__name__}; skipping",
                err=True,
            )
            continue
        if "smiles" not in entry:
            click.echo(
                f"warning: analog #{i}: missing 'smiles' field; skipping",
                err=True,
            )
            continue
        analogs.append(entry)

    if not analogs:
        from ..core.errors import UsageError

        raise UsageError(
            "no valid analog entries found in the input file",
            remedy="each entry must be an object with at least a 'smiles' field",
        )

    # --- resolve optional inputs ---
    receptor_path: Path | None = None
    if receptor is not None:
        receptor_path = Path(receptor)
        if not receptor_path.is_absolute():
            receptor_path = state.project().root / receptor_path
        if not receptor_path.is_file():
            click.echo(
                f"warning: receptor not found at {receptor_path}; docking will be skipped",
                err=True,
            )
            receptor_path = None

    pocket_path: Path | None = None
    if pocket is not None:
        pocket_path = Path(pocket)
        if not pocket_path.is_absolute():
            pocket_path = state.project().root / pocket_path
        if not pocket_path.is_file():
            click.echo(
                f"warning: pocket record not found at {pocket_path}; docking will be skipped",
                err=True,
            )
            pocket_path = None

    mpo_weights: dict[str, float] | None = None
    if weights is not None:
        weights_path = Path(weights)
        if not weights_path.is_absolute():
            weights_path = state.project().root / weights_path
        if weights_path.is_file():
            try:
                mpo_weights = json.loads(weights_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                click.echo(
                    f"warning: invalid JSON in weights file {weights_path}; using default ranking",
                    err=True,
                )
        else:
            click.echo(
                f"warning: weights file not found at {weights_path}; using default ranking",
                err=True,
            )

    # --- RDKit version for provenance ---
    from rdkit import rdBase

    rdkit_version = rdBase.rdkitVersion

    # --- run the pipeline for each analog ---
    results: list[dict[str, Any]] = []
    pipeline_steps_available = ["validate", "prepare-3d", "admet-predict"]
    docking_available = (
        receptor_path is not None
        and pocket_path is not None
        and shutil.which("mk_prepare_ligand.py") is not None
        and shutil.which("vina") is not None
    )
    if docking_available:
        pipeline_steps_available.append("dock")

    for entry in analogs:
        smiles = entry["smiles"]
        name = entry.get("name")
        rationale = entry.get("rationale")
        identifier = _name_slug(name, smiles)

        analog_result: dict[str, Any] = {
            "name": name,
            "input_smiles": smiles,
            "rationale": rationale,
            "pipeline_status": {},
            "scores": {},
        }

        try:
            # Step 1: Validate SMILES
            validation = _validate_smiles(smiles, Chem)
            if validation is None:
                analog_result["pipeline_status"]["validate"] = "failed"
                analog_result["pipeline_status"]["failure_reason"] = (
                    f"unparseable SMILES: {smiles}"
                )
                click.echo(
                    f"warning: analog {name or identifier}: SMILES validation failed; skipping",
                    err=True,
                )
                results.append(analog_result)
                continue

            mol, canonical = validation
            analog_result["canonical_smiles"] = canonical
            analog_result["pipeline_status"]["validate"] = "passed"

            # Step 2: Prepare 3D
            prep_result = _prepare_3d(mol, canonical, Chem, AllChem)
            if prep_result is None:
                analog_result["pipeline_status"]["prepare-3d"] = "failed"
                click.echo(
                    f"warning: analog {name or identifier}: 3D embedding failed",
                    err=True,
                )
            else:
                mol_h, force_field, converged = prep_result
                analog_result["pipeline_status"]["prepare-3d"] = "passed"
                analog_result["scores"]["force_field"] = force_field
                analog_result["scores"]["optimization_converged"] = converged

                # Write SDF
                sdf_path = target_dir / f"{identifier}.3d.sdf"
                writer = Chem.SDWriter(str(sdf_path))
                try:
                    writer.write(mol_h)
                finally:
                    writer.close()
                analog_result["sdf_path"] = str(sdf_path)

                # Step 3: Docking (if tools available)
                if docking_available and receptor_path and pocket_path:
                    with tempfile.TemporaryDirectory(prefix="dde-analog-") as tmpdir:
                        ligand_pdbqt = Path(tmpdir) / f"{identifier}.pdbqt"
                        if _prepare_ligand_pdbqt(sdf_path, ligand_pdbqt):
                            dock_result = _run_docking(
                                ligand_pdbqt,
                                receptor_path,
                                pocket_path,
                            )
                            if dock_result is not None:
                                analog_result["pipeline_status"]["dock"] = "passed"
                                analog_result["scores"]["docking_score"] = dock_result[
                                    "best_score"
                                ]
                                analog_result["scores"]["docking_n_poses"] = (
                                    dock_result["n_poses"]
                                )
                            else:
                                analog_result["pipeline_status"]["dock"] = "failed"
                        else:
                            analog_result["pipeline_status"]["dock"] = "failed"
                            analog_result["pipeline_status"]["dock_detail"] = (
                                "ligand PDBQT conversion failed"
                            )
                elif receptor_path is not None or pocket_path is not None:
                    # Partially specified — note the gap
                    analog_result["pipeline_status"]["dock"] = "skipped"
                    analog_result["pipeline_status"]["dock_detail"] = (
                        "docking requires both --receptor and --pocket, plus "
                        "mk_prepare_ligand.py and vina on PATH"
                    )
                else:
                    analog_result["pipeline_status"]["dock"] = "skipped"

            # Step 4: ADMET predict
            try:
                admet_result = _predict_admet(
                    mol,
                    canonical,
                    Chem,
                    Descriptors,
                    rdMolDescriptors,
                )
                analog_result["pipeline_status"]["admet-predict"] = "passed"
                analog_result["scores"]["admet"] = admet_result
            except Exception as exc:
                analog_result["pipeline_status"]["admet-predict"] = "failed"
                analog_result["pipeline_status"]["admet_detail"] = str(exc)
                click.echo(
                    f"warning: analog {name or identifier}: ADMET prediction failed: {exc}",
                    err=True,
                )
        except Exception as exc:
            analog_result["pipeline_status"]["fatal"] = str(exc)
            click.echo(
                f"warning: analog {name or identifier}: unexpected error: {exc}",
                err=True,
            )

        results.append(analog_result)

    # --- Step 5: Rank ---
    # Separate successful results from failures for ranking
    rankable = [r for r in results if r["pipeline_status"].get("validate") == "passed"]

    if mpo_weights and rankable:
        # MPO ranking
        for r in rankable:
            docking_score = r["scores"].get("docking_score")
            admet_data = r["scores"].get("admet", {})
            r["scores"]["mpo_score"] = _compute_mpo_score(
                docking_score,
                admet_data,
                mpo_weights,
            )
        rankable.sort(key=lambda r: r["scores"].get("mpo_score", 0), reverse=True)
        ranking_method = "mpo"
    elif any(r["scores"].get("docking_score") is not None for r in rankable):
        # Rank by docking score (more negative is better)
        rankable.sort(
            key=lambda r: r["scores"].get("docking_score", 0),
        )
        ranking_method = "docking_score"
    else:
        # Rank by ADMET profile: prefer high solubility and permeability
        sol_rank = {"high": 0, "moderate": 1, "low": 2, "insoluble": 3}
        perm_rank = {"high": 0, "moderate": 1, "low": 2}
        rankable.sort(
            key=lambda r: (
                sol_rank.get(
                    r["scores"].get("admet", {}).get("solubility_class", ""),
                    2,
                ),
                perm_rank.get(
                    r["scores"].get("admet", {}).get("permeability", ""),
                    1,
                ),
            ),
        )
        ranking_method = "admet_profile"

    for rank_idx, r in enumerate(rankable, start=1):
        r["rank"] = rank_idx

    # Failed analogs get no rank
    for r in results:
        if "rank" not in r:
            r["rank"] = None

    # --- build output record ---
    evaluation_record: dict[str, Any] = {
        "tool": "analog",
        "subcommand": "evaluate",
        "n_analogs_input": len(analogs),
        "n_analogs_valid": len(rankable),
        "n_analogs_failed": len(analogs) - len(rankable),
        "ranking_method": ranking_method,
        "pipeline_steps_available": pipeline_steps_available,
        "results": results,
    }

    record_path = target_dir / "analog-evaluation.json"
    record_path.write_text(
        json.dumps(evaluation_record, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    # --- provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="analog",
        subcommand="evaluate",
        endpoint=None,
        parameters={
            "analogs_file": analogs_path.name,
            "n_analogs": len(analogs),
            "receptor": str(receptor_path) if receptor_path else None,
            "pocket": str(pocket_path) if pocket_path else None,
            "weights": str(weights) if weights else None,
        },
    )
    sidecar.note("rdkit_version", rdkit_version)
    sidecar.note("pipeline_steps_available", pipeline_steps_available)
    sidecar.note("ranking_method", ranking_method)
    sidecar.note("docking_available", docking_available)

    if not docking_available:
        missing_tools: list[str] = []
        if receptor_path is None:
            missing_tools.append("--receptor not provided")
        if pocket_path is None:
            missing_tools.append("--pocket not provided")
        if not shutil.which("mk_prepare_ligand.py"):
            missing_tools.append("mk_prepare_ligand.py not on PATH")
        if not shutil.which("vina"):
            missing_tools.append("vina not on PATH")
        sidecar.warn(
            f"Docking step was not available: {'; '.join(missing_tools)}. "
            "Ranking falls back to ADMET profile only."
        )

    sidecar.add_output(record_path)
    meta_path = sidecar.write(target_dir / "analog-evaluation.meta.json")

    # --- CLI output: ranked summary table ---
    emit.line(f"Analog evaluation: {len(rankable)}/{len(analogs)} analogs validated")
    emit.line(f"Ranking method: {ranking_method}")
    emit.line("")

    # Build a simple ranked table
    for r in sorted(results, key=lambda x: x.get("rank") or 999):
        name_str = r.get("name") or r.get("canonical_smiles", r["input_smiles"])
        rank_str = f"#{r['rank']}" if r.get("rank") is not None else "FAIL"
        parts = [f"{rank_str} {name_str}"]

        dock_score = r["scores"].get("docking_score")
        if dock_score is not None:
            parts.append(f"dock={dock_score:.1f}")

        admet_data = r["scores"].get("admet", {})
        if admet_data:
            flags: list[str] = []
            if admet_data.get("herg_flagged"):
                flags.append("hERG!")
            if admet_data.get("cyp_inhibition_flags"):
                flags.append(f"CYP:{','.join(admet_data['cyp_inhibition_flags'])}")
            sol = admet_data.get("solubility_class", "")
            if sol in ("low", "insoluble"):
                flags.append(f"sol={sol}")
            if flags:
                parts.append(" ".join(flags))

        mpo = r["scores"].get("mpo_score")
        if mpo is not None:
            parts.append(f"MPO={mpo:.3f}")

        # Note pipeline failures
        failed_steps = [
            step
            for step, status in r.get("pipeline_status", {}).items()
            if status == "failed"
            and not step.endswith("_detail")
            and not step.endswith("_reason")
        ]
        if failed_steps:
            parts.append(f"[failed: {','.join(failed_steps)}]")

        emit.line("  ".join(parts))

    emit.data("n_analogs_valid", len(rankable))
    emit.data("n_analogs_failed", len(analogs) - len(rankable))
    emit.data("ranking_method", ranking_method)
    emit.path(record_path, role="evaluation")
    emit.path(meta_path, role="sidecar")
    emit.flush()
