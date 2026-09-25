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

"""`dde screen` -- virtual screening of compound libraries.

Batch docking of a multi-molecule library against a prepared receptor
with ranked output.  Phase 1 only -- no thresholds are applied, no
judgment is made.  The pipeline records what it computed and what it
could not.

Phase 1 (``screen run``):
  Parse SDF library -> validate -> (optional pre-filter) -> 3D prep ->
  PDBQT conversion -> dock -> record.  Rank by best docking score.

Phase 2 (``screen analyze``):
  Apply ``docking-scores`` thresholds to classify hits by binding
  strength -- reusable offline without re-docking.  (Not in scope for
  Phase 1.)
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
)
from ..core import provenance
from ..core.errors import DependencyError, Refusal
from ..core.output import Emitter
from ..core.pipeline import (
    name_slug,
    prepare_3d,
    prepare_ligand_pdbqt,
    run_docking,
    validate_smiles,
)

ARTIFACT_CLASS = "screening"
TOOL = "screen"


def _guard_input_output_alias(input_path: Path, target_dir: Path, label: str) -> None:
    """Raise :class:`Refusal` if *input_path* lives inside *target_dir*.

    Prevents the screening pipeline from clobbering its own inputs when
    the input file resides inside (or *is*) the output directory.
    """
    if input_path.resolve() == target_dir.resolve() or (
        input_path.resolve().is_relative_to(target_dir.resolve())
    ):
        raise Refusal(
            f"input {label} path aliases output directory — would clobber input",
            remedy="use --out to write output to a different directory",
        )


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
            detail="virtual screening requires RDKit for SMILES validation, "
            "descriptor computation, and 3D coordinate generation",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi)",
        ) from e


# ---------------------------------------------------------------------------
# SDF input parsing (screen-specific)
# ---------------------------------------------------------------------------


def _iter_library(
    library_path: Path,
    Chem: Any,
) -> Iterator[tuple[str, Any | None]]:
    """Yield (name, mol) pairs from a multi-molecule SDF file.

    Returns molecule name from the ``_Name`` property, or generates a
    positional identifier (``compound_001``, ...) if absent.
    Yields ``(name, None)`` for unparseable entries (caller records and
    continues).
    """
    with open(library_path, "rb") as fh:
        supplier = Chem.ForwardSDMolSupplier(fh, removeHs=False)
        for idx, mol in enumerate(supplier):
            if mol is None:
                yield (f"compound_{idx + 1:03d}", None)
                continue
            try:
                mol_name = mol.GetProp("_Name")
            except KeyError:
                mol_name = ""
            if not mol_name or not mol_name.strip():
                mol_name = f"compound_{idx + 1:03d}"
            yield (mol_name.strip(), mol)


def _has_3d_coords(mol: Any) -> bool:
    """True if the molecule already has 3D coordinates.

    Detection: check if any atom has a non-zero Z coordinate.
    """
    try:
        conf = mol.GetConformer()
    except ValueError:
        return False
    return any(abs(conf.GetAtomPosition(i).z) > 0.01 for i in range(mol.GetNumAtoms()))


# ---------------------------------------------------------------------------
# Pre-filter
# ---------------------------------------------------------------------------


def _check_prefilter(
    mol: Any,
    Descriptors: Any,
    rdMolDescriptors: Any,
    thresholds: dict[str, Any],
) -> tuple[bool, str | None]:
    """Check descriptor-based pre-filter thresholds.

    Returns ``(passes, reason)``.  When ``passes`` is False, ``reason``
    describes which threshold was exceeded.
    """
    mw = round(Descriptors.MolWt(mol), 2)
    if mw > thresholds["max_mw"]:
        return False, f"mw={mw} > max_mw={thresholds['max_mw']}"

    logp = round(Descriptors.MolLogP(mol), 2)
    if logp > thresholds["max_logp"]:
        return False, f"logp={logp} > max_logp={thresholds['max_logp']}"

    hbd = Descriptors.NumHDonors(mol)
    if hbd > thresholds["max_hbd"]:
        return False, f"hbd={hbd} > max_hbd={thresholds['max_hbd']}"

    hba = Descriptors.NumHAcceptors(mol)
    if hba > thresholds["max_hba"]:
        return False, f"hba={hba} > max_hba={thresholds['max_hba']}"

    tpsa = round(Descriptors.TPSA(mol), 2)
    if tpsa > thresholds["max_tpsa"]:
        return False, f"tpsa={tpsa} > max_tpsa={thresholds['max_tpsa']}"

    rotatable = rdMolDescriptors.CalcNumRotatableBonds(mol)
    if rotatable > thresholds["max_rotatable_bonds"]:
        return False, (
            f"rotatable_bonds={rotatable} > "
            f"max_rotatable_bonds={thresholds['max_rotatable_bonds']}"
        )

    return True, None


# ---------------------------------------------------------------------------
# Screen name derivation
# ---------------------------------------------------------------------------


def _derive_screen_name(library_path: Path, target_dir: Path) -> str:
    """Derive a screen name from the library filename.

    The screen name is metadata stored inside the results JSON; it does
    not influence the output filename (always ``screen-results.json``),
    so collision avoidance on the name is unnecessary.
    """
    return f"{library_path.stem}-screen"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def screen() -> None:
    """Virtual screening of compound libraries."""


# ---------------------------------------------------------------------------
# Phase 1 -- run
# ---------------------------------------------------------------------------


@screen.command("run")
@click.argument("library", type=click.Path(exists=True))
@click.option(
    "--receptor",
    required=True,
    type=click.Path(exists=True),
    help="Prepared receptor PDBQT (from `docking prepare`).",
)
@click.option(
    "--gridbox",
    required=True,
    type=click.Path(exists=True),
    help="Grid box JSON (from `docking prepare`).",
)
@click.option(
    "--pre-filter/--no-pre-filter",
    default=False,
    help="Apply descriptor-based pre-filter before docking.",
)
@click.option(
    "--exhaustiveness",
    default=8,
    type=int,
    show_default=True,
    help="Vina search exhaustiveness.",
)
@click.option(
    "--n-poses",
    default=9,
    type=int,
    show_default=True,
    help="Max poses to generate per compound.",
)
@click.option(
    "--top-n",
    default=None,
    type=int,
    help="Display top N compounds in summary (all are recorded).",
)
@click.option(
    "--name",
    "screen_name",
    default=None,
    help="Screen name for output directory/filenames.",
)
@out_option
@output_options
@pass_state
def run_cmd(
    state: AppState,
    library: str,
    receptor: str,
    gridbox: str,
    pre_filter: bool,
    exhaustiveness: int,
    n_poses: int,
    top_n: int | None,
    screen_name: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Screen a compound library against a prepared receptor.

    Takes an SDF file of compounds and docks each against the receptor,
    producing ranked results with provenance.

    Pipeline failures for individual compounds are recorded and
    processing continues with the next compound.

    \b
    Outputs under raw/screening/:
      screen-results.json           -- batch results record
      screen-results.meta.json      -- provenance sidecar
      {name}.poses.pdbqt            -- per-compound pose files (docked only)
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    Chem, AllChem, Descriptors, rdMolDescriptors = _require_rdkit()

    # --- fail-fast dependency checks ---
    if not shutil.which("mk_prepare_ligand.py"):
        raise DependencyError(
            "mk_prepare_ligand.py is not on PATH",
            detail="virtual screening requires Meeko's mk_prepare_ligand.py "
            "for ligand PDBQT conversion",
            remedy="install meeko into the tools environment (pip install meeko>=0.5)",
        )
    if not shutil.which("vina"):
        raise DependencyError(
            "vina is not on PATH",
            detail="virtual screening requires the AutoDock Vina binary",
            remedy="re-provision with `tools/install.sh --binaries-only`",
        )

    # --- resolve inputs ---
    library_path = Path(library)
    if not library_path.is_absolute():
        library_path = state.project().root / library_path
    receptor_path = Path(receptor)
    if not receptor_path.is_absolute():
        receptor_path = state.project().root / receptor_path
    gridbox_path = Path(gridbox)
    if not gridbox_path.is_absolute():
        gridbox_path = state.project().root / gridbox_path

    # --- guard against input/output path aliasing ---
    for input_path, label in [
        (library_path, "library"),
        (receptor_path, "receptor"),
        (gridbox_path, "gridbox"),
    ]:
        _guard_input_output_alias(input_path, target_dir, label)

    # --- screen name ---
    if screen_name is None:
        screen_name = _derive_screen_name(library_path, target_dir)

    # --- RDKit version for provenance ---
    from rdkit import rdBase

    rdkit_version = rdBase.rdkitVersion

    # --- load pre-filter thresholds if requested ---
    prefilter_thresholds: dict[str, Any] | None = None
    prefilter_threshold_set = None
    if pre_filter:
        prefilter_threshold_set = load_thresholds(state, "screening-prefilter")
        prefilter_thresholds = prefilter_threshold_set.applied()

    # --- iterate library and process each compound ---
    click.echo(
        f"Screening compounds from {library_path.name} against {receptor_path.name}",
        err=True,
    )

    results: list[dict[str, Any]] = []
    counts = {
        "input": 0,
        "parse_failed": 0,
        "validated": 0,
        "prefiltered": 0,
        "prep_failed": 0,
        "dock_failed": 0,
        "docked": 0,
    }

    for compound_name, mol in _iter_library(library_path, Chem):
        counts["input"] += 1
        i = counts["input"]
        identifier = name_slug(compound_name, compound_name)

        # --- Progress reporting (every 50 compounds) ---
        if i % 50 == 0:
            click.echo(
                f"  {i} compounds processed "
                f"({counts['docked']} docked, "
                f"{counts['parse_failed'] + counts['prep_failed'] + counts['dock_failed']} failed)",
                err=True,
            )

        compound_result: dict[str, Any] = {
            "name": compound_name,
            "smiles": None,
            "best_score": None,
            "n_poses": None,
            "pipeline_status": None,
            "poses_file": None,
            "rank": None,
        }

        # --- Parse failure ---
        if mol is None:
            compound_result["pipeline_status"] = "parse_failed"
            counts["parse_failed"] += 1
            click.echo(
                f"  warning: {compound_name}: SDF parse failed; skipping",
                err=True,
            )
            results.append(compound_result)
            continue

        # --- Validate: get canonical SMILES ---
        try:
            smiles = Chem.MolToSmiles(mol)
            compound_result["smiles"] = smiles
        except Exception:
            compound_result["pipeline_status"] = "parse_failed"
            counts["parse_failed"] += 1
            click.echo(
                f"  warning: {compound_name}: SMILES conversion failed; skipping",
                err=True,
            )
            results.append(compound_result)
            continue

        counts["validated"] += 1

        # --- Pre-filter (if enabled) ---
        if pre_filter and prefilter_thresholds is not None:
            passes, reason = _check_prefilter(
                mol,
                Descriptors,
                rdMolDescriptors,
                prefilter_thresholds,
            )
            if not passes:
                compound_result["pipeline_status"] = "prefiltered"
                compound_result["prefilter_reason"] = reason
                counts["prefiltered"] += 1
                results.append(compound_result)
                continue

        # --- 3D preparation ---
        has_3d = _has_3d_coords(mol)
        try:
            if has_3d:
                # SDF already has 3D coordinates -- use them directly
                mol_h = Chem.AddHs(mol, addCoords=True)
            else:
                # Need to validate SMILES and generate 3D
                validation = validate_smiles(smiles, Chem)
                if validation is None:
                    compound_result["pipeline_status"] = "prep_failed"
                    counts["prep_failed"] += 1
                    click.echo(
                        f"  warning: {compound_name}: SMILES validation failed "
                        "during 3D prep; skipping",
                        err=True,
                    )
                    results.append(compound_result)
                    continue

                val_mol, _canonical = validation
                prep_result = prepare_3d(val_mol, smiles, Chem, AllChem)
                if prep_result is None:
                    compound_result["pipeline_status"] = "prep_failed"
                    counts["prep_failed"] += 1
                    click.echo(
                        f"  warning: {compound_name}: 3D embedding failed; skipping",
                        err=True,
                    )
                    results.append(compound_result)
                    continue
                mol_h = prep_result[0]
        except Exception as exc:
            compound_result["pipeline_status"] = "prep_failed"
            counts["prep_failed"] += 1
            click.echo(
                f"  warning: {compound_name}: 3D prep error: {exc}; skipping",
                err=True,
            )
            results.append(compound_result)
            continue

        # --- Write SDF and convert to PDBQT ---
        with tempfile.TemporaryDirectory(prefix="dde-screen-") as tmpdir:
            sdf_path = Path(tmpdir) / f"{identifier}.3d.sdf"
            writer = Chem.SDWriter(str(sdf_path))
            try:
                writer.write(mol_h)
            finally:
                writer.close()

            # Save generated 3D SDF if we generated it (not from SDF)
            if not has_3d:
                dest_sdf = target_dir / f"{identifier}.3d.sdf"
                shutil.copy2(sdf_path, dest_sdf)

            ligand_pdbqt = Path(tmpdir) / f"{identifier}.pdbqt"
            if not prepare_ligand_pdbqt(sdf_path, ligand_pdbqt):
                compound_result["pipeline_status"] = "prep_failed"
                counts["prep_failed"] += 1
                click.echo(
                    f"  warning: {compound_name}: PDBQT conversion failed; skipping",
                    err=True,
                )
                results.append(compound_result)
                continue

            # --- Dock ---
            dock_result = run_docking(
                ligand_pdbqt,
                receptor_path,
                gridbox_path,
                exhaustiveness=exhaustiveness,
                n_poses=n_poses,
            )

            if dock_result is None:
                compound_result["pipeline_status"] = "dock_failed"
                counts["dock_failed"] += 1
                click.echo(
                    f"  warning: {compound_name}: docking failed; skipping",
                    err=True,
                )
                results.append(compound_result)
                continue

            # --- Success: record result ---
            compound_result["pipeline_status"] = "docked"
            compound_result["best_score"] = dock_result["best_score"]
            compound_result["n_poses"] = dock_result["n_poses"]
            counts["docked"] += 1

            # Write poses to target directory
            poses_filename = f"{identifier}.poses.pdbqt"
            poses_dest = target_dir / poses_filename
            poses_dest.write_text(dock_result["poses_text"], encoding="utf-8")
            compound_result["poses_file"] = poses_filename

        results.append(compound_result)

    # --- Rank by best_score ascending (more negative = better) ---
    docked = [r for r in results if r["pipeline_status"] == "docked"]
    docked.sort(key=lambda r: r["best_score"])
    for rank_idx, r in enumerate(docked, start=1):
        r["rank"] = rank_idx

    # Build sorted results: ranked compounds first, then failures
    ranked_results = sorted(
        [r for r in results if r["rank"] is not None],
        key=lambda r: r["rank"],
    )
    unranked_results = [r for r in results if r["rank"] is None]
    sorted_results = ranked_results + unranked_results

    # --- Build output record ---
    prefilter_record: dict[str, Any] = {"enabled": pre_filter}
    if pre_filter and prefilter_threshold_set is not None:
        prefilter_record["threshold_set"] = prefilter_threshold_set.tag
        prefilter_record["thresholds_applied"] = prefilter_thresholds

    screen_record: dict[str, Any] = {
        "tool": TOOL,
        "subcommand": "run",
        "library": library_path.name,
        "library_format": "sdf",
        "receptor": receptor_path.name,
        "gridbox": gridbox_path.name,
        "screen_name": screen_name,
        "exhaustiveness": exhaustiveness,
        "n_poses": n_poses,
        "prefilter": prefilter_record,
        "counts": counts,
        "results": sorted_results,
    }

    results_filename = "screen-results.json"
    record_path = target_dir / results_filename
    record_path.write_text(
        json.dumps(screen_record, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    # --- Provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="run",
        endpoint=None,
        parameters={
            "library": library_path.name,
            "receptor": receptor_path.name,
            "gridbox": gridbox_path.name,
            "screen_name": screen_name,
            "exhaustiveness": exhaustiveness,
            "n_poses": n_poses,
            "pre_filter": pre_filter,
        },
    )
    sidecar.note("rdkit_version", rdkit_version)
    sidecar.note("library_sha256", provenance.sha256_file(library_path))
    sidecar.note("receptor_path", str(receptor_path))
    sidecar.note("receptor_sha256", provenance.sha256_file(receptor_path))
    sidecar.note("gridbox_path", str(gridbox_path))
    sidecar.note("counts", counts)

    # --- Conditional relay: prefilter_excludes_not_rejects ---
    if pre_filter and counts["prefiltered"] > 0:
        sidecar.warn(
            f"{counts['prefiltered']} compound(s) excluded by descriptor "
            f"pre-filter (threshold set: "
            f"{prefilter_threshold_set.tag if prefilter_threshold_set else 'screening-prefilter'}). "
            "Excluded compounds were not docked, not proven inactive.",
            code="screening.prefilter_excludes_not_rejects",
        )

    sidecar.add_output(record_path)
    meta_path = sidecar.write(target_dir / "screen-results.meta.json")

    # --- CLI output: ranked summary ---
    emit.line(f"Screen complete: {counts['docked']}/{counts['input']} compounds docked")
    if counts["prefiltered"] > 0:
        emit.line(f"  Pre-filtered: {counts['prefiltered']}")
    if counts["parse_failed"] > 0:
        emit.line(f"  Parse failed: {counts['parse_failed']}")
    if counts["prep_failed"] > 0:
        emit.line(f"  Prep failed: {counts['prep_failed']}")
    if counts["dock_failed"] > 0:
        emit.line(f"  Dock failed: {counts['dock_failed']}")

    emit.line("")

    # Show top-N (or all if top_n is None) ranked compounds
    display_results = ranked_results
    if top_n is not None and top_n > 0:
        display_results = ranked_results[:top_n]

    for r in display_results:
        score_str = f"{r['best_score']:.1f}" if r["best_score"] is not None else "N/A"
        emit.line(
            f"  #{r['rank']}  {r['name']}  score={score_str}  poses={r['n_poses']}"
        )

    if top_n is not None and len(ranked_results) > top_n:
        emit.line(
            f"  ... and {len(ranked_results) - top_n} more (all in {results_filename})"
        )

    if sidecar.relays:
        for record in sidecar.relays:
            emit.line(f"relay {record['code']}: {record['message']}")

    emit.data("screen_name", screen_name)
    emit.data("counts", counts)
    if sidecar.relays:
        emit.data("mandatory_relays", sidecar.relays)
    emit.path(record_path, role="results")
    emit.path(meta_path, role="sidecar")
    emit.flush()
