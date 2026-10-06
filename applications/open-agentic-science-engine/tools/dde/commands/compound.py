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

"""`dde compound` — compound property profiling with RDKit.

Phase 1 (no judgment):
  validate     Parse a SMILES string, canonicalize, write parse record.
  descriptors  Compute molecular descriptors (MW, LogP, TPSA, etc.).
  alerts       Run PAINS, Brenk, and aggregator structural filters.
  sa-score     Compute synthetic accessibility score (Ertl & Schuffenhauer).
  prepare-3d   Generate 3D coordinates (ETKDGv3 + MMFF/UFF optimization).

Phase 2 (offline, applies thresholds):
  analyze      Read stored descriptors, alerts, and SA-score, apply
               drug-likeness criteria, produce an analysis record with relays.

Multi-fragment SMILES (salts, mixtures) follow the "strip counterions,
alert on parent" convention.  The convention is documented in the sidecar
— the parent is not chosen silently.  An unparseable SMILES is a refusal
(exit 9), not a failure, because a different input string is the remedy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    from_option,
    load_thresholds,
    name_option,
    out_option,
    output_options,
    pass_state,
)
from ..core import provenance
from ..core.errors import ArtifactError, DependencyError, Refusal
from ..core.output import Emitter, warn

ARTIFACT_CLASS = "compounds"

# Common aggregator SMARTS patterns — these structures are known to cause
# non-specific assay interference through colloidal aggregation.  No single
# curated aggregator catalog exists in RDKit's FilterCatalog; these are
# drawn from Shoichet lab publications on colloidal aggregation
# (McGovern et al., J Med Chem 2002; Feng et al., Nat Chem Biol 2005).
_AGGREGATOR_PATTERNS: list[tuple[str, str]] = [
    ("catechol", "[OH]c1ccccc1[OH]"),
    ("quinone", "O=C1C=CC(=O)C=C1"),
]


# ---------------------------------------------------------------------------
# RDKit lazy import
# ---------------------------------------------------------------------------


def _require_rdkit():
    """Lazy-import RDKit, raising DependencyError if absent."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, rdMolDescriptors
        from rdkit.Chem.FilterCatalog import (
            FilterCatalog,
            FilterCatalogParams,
        )

        return Chem, Descriptors, rdMolDescriptors, FilterCatalog, FilterCatalogParams
    except ImportError as e:
        raise DependencyError(
            "RDKit is not installed",
            detail="compound validation, descriptors and structural alerts require RDKit",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi)",
        ) from e


def _require_rdkit_3d():
    """Lazy-import RDKit with AllChem for 3D embedding, raising DependencyError if absent."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        return Chem, AllChem
    except ImportError as e:
        raise DependencyError(
            "RDKit is not installed",
            detail="3D coordinate generation requires RDKit with AllChem",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi)",
        ) from e


def _require_rdkit_sa_score():
    """Lazy-import the RDKit Contrib SA_Score module, raising DependencyError if absent."""
    try:
        import sys

        from rdkit import RDConfig

        contrib_sa = os.path.join(RDConfig.RDContribDir, "SA_Score")
        if contrib_sa not in sys.path:
            sys.path.insert(0, contrib_sa)

        import sascorer

        return sascorer
    except (ImportError, AttributeError) as e:
        raise DependencyError(
            "RDKit SA_Score module is not available",
            detail="SA-score computation requires the SA_Score Contrib module "
            "bundled with RDKit (rdkit/Contrib/SA_Score/sascorer.py)",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi); "
            "the SA_Score module is included in the Contrib directory",
        ) from e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(smiles: str) -> str:
    """Derive a deterministic, filesystem-safe slug from canonical SMILES.

    Special characters common in SMILES (parentheses, equals signs, slashes,
    brackets) are replaced with hyphens.  If the result is too short to be
    useful (e.g. a SMILES made entirely of special characters), a truncated
    SHA-256 of the SMILES is used instead.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", smiles).strip("-").lower()
    if not slug or len(slug) < 3:
        slug = "mol-" + hashlib.sha256(smiles.encode()).hexdigest()[:16]
    return slug[:80]


def _parse_smiles(smiles_input: str) -> tuple[Any, str, dict[str, Any], list[str]]:
    """Parse a SMILES string, handling multi-fragment inputs.

    Returns ``(mol, canonical_smiles, fragment_notes, stripped_fragments)``.
    Raises :class:`Refusal` on unparseable input — a different input
    string is the remedy, not a retry.
    """
    Chem = _require_rdkit()[0]

    mol = Chem.MolFromSmiles(smiles_input)
    if mol is None:
        raise Refusal(
            f"unparseable SMILES: {smiles_input!r}",
            detail="RDKit could not interpret this as a valid molecular structure",
            remedy="check the SMILES syntax; common issues include unclosed "
            "brackets, invalid atom symbols, and unbalanced parentheses",
        )

    notes: dict[str, Any] = {}
    fragments_stripped: list[str] = []

    frags = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    if len(frags) > 1:
        # Strip counterions: keep the largest fragment by heavy atom count.
        frags_sorted = sorted(
            [(f, f.GetNumHeavyAtoms()) for f in frags],
            key=lambda x: x[1],
            reverse=True,
        )
        parent = frags_sorted[0][0]
        fragments_stripped = [Chem.MolToSmiles(f) for f, _ in frags_sorted[1:]]
        notes = {
            "multi_fragment_input": True,
            "original_smiles": smiles_input,
            "fragments_stripped": fragments_stripped,
            "convention": (
                "Counterions stripped; parent molecule selected as the largest "
                "fragment by heavy atom count.  The stripped fragments are "
                "listed here — this is documented, not silent."
            ),
        }
        mol = parent

    canonical = Chem.MolToSmiles(mol)
    return mol, canonical, notes, fragments_stripped


def _build_sidecar(
    subcommand: str,
    smiles_input: str,
    canonical: str,
    fragment_notes: dict[str, Any],
) -> provenance.Sidecar:
    """Create a provenance sidecar for a phase-1 compound subcommand."""
    sidecar = provenance.Sidecar(
        tool="compound",
        subcommand=subcommand,
        endpoint=None,
        parameters={"input_smiles": smiles_input, "canonical_smiles": canonical},
    )
    if fragment_notes:
        for key, value in fragment_notes.items():
            sidecar.note(key, value)
        stripped = ", ".join(fragment_notes["fragments_stripped"])
        sidecar.warn(
            f"Multi-fragment input: counterions stripped ({stripped}).  "
            "Parent molecule selected as largest fragment by heavy atom count.",
            code="compound.fragment_stripped",
        )
    return sidecar


def _overwrite_option(func):
    """--overwrite: bypass overwrite protection for phase-1 artifacts."""
    return click.option(
        "--overwrite",
        is_flag=True,
        help="Replace existing artifacts that differ from the new output. "
        "Without it, a conflicting write is refused (exit 9).",
    )(func)


def _guard_sdf_no_clobber(path: Path) -> None:
    """Raise :class:`Refusal` if *path* already exists.

    Used by ``prepare-3d`` where the output is written via RDKit's
    ``SDWriter`` and :func:`_safe_write_artifact` (which expects text
    content for SHA-256 comparison) does not apply.
    """
    if path.exists():
        raise Refusal(
            f"artifact already exists: {path}",
            remedy="use --out to write to a different location",
        )


def _safe_write_artifact(path: Path, content: str, *, overwrite: bool) -> bool:
    """Write artifact content with overwrite protection.

    Returns True if the file was written (new file or overwrite mode).
    Returns False if skipped because identical content already exists.
    Raises :class:`Refusal` if the file exists with different content
    and *overwrite* is False.
    """
    path = Path(path)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return True
    if overwrite:
        path.write_text(content, encoding="utf-8")
        return True

    existing_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    if existing_hash == new_hash:
        warn(f"artifact already exists with identical content, skipping: {path.name}")
        return False

    raise Refusal(
        f"artifact {path} already exists with different content",
        detail=f"existing SHA-256: {existing_hash}, new SHA-256: {new_hash}",
        remedy="use --overwrite to replace, or use --out to write to a "
        "different location",
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def compound() -> None:
    """Compound property profiling (RDKit)."""


# ---------------------------------------------------------------------------
# Phase 1 — validate
# ---------------------------------------------------------------------------


@compound.command("validate")
@click.argument("smiles")
@out_option
@name_option
@_overwrite_option
@output_options
@pass_state
def validate_cmd(
    state: AppState,
    smiles: str,
    out: str | None,
    name: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Parse and canonicalize a SMILES string.

    Rejects unparseable input with exit 9 (Refusal).  For multi-fragment
    SMILES (salts, mixtures), counterions are stripped and the parent
    molecule is selected by heavy atom count; the sidecar documents what
    was removed.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    mol, canonical, fragment_notes, _ = _parse_smiles(smiles)
    slug = _slug(name) if name else _slug(canonical)

    sidecar = _build_sidecar("validate", smiles, canonical, fragment_notes)
    if name:
        sidecar.note("compound_name", name)

    record: dict[str, Any] = {
        "tool": "compound",
        "subcommand": "validate",
        "input_smiles": smiles,
        "canonical_smiles": canonical,
        "parse_status": "valid",
        "n_atoms": mol.GetNumAtoms(),
        "n_heavy_atoms": mol.GetNumHeavyAtoms(),
    }
    if fragment_notes:
        record["multi_fragment"] = fragment_notes

    record_path = target_dir / f"{slug}.validate.json"
    content = json.dumps(record, indent=2) + "\n"
    _safe_write_artifact(record_path, content, overwrite=overwrite)
    meta_path = target_dir / f"{slug}.validate.meta.json"
    sidecar.add_output(record_path)
    meta_path = sidecar.write(meta_path)

    emit.data("canonical_smiles", canonical)
    emit.data("parse_status", "valid")
    emit.path(record_path, role="validate")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — descriptors
# ---------------------------------------------------------------------------


@compound.command("descriptors")
@click.argument("smiles")
@out_option
@name_option
@_overwrite_option
@output_options
@pass_state
def descriptors_cmd(
    state: AppState,
    smiles: str,
    out: str | None,
    name: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Compute RDKit molecular descriptors for a SMILES string.

    MW, LogP, TPSA, HBA, HBD, rotatable bonds, aromatic rings, and heavy
    atom count.  Phase 1: the descriptors are recorded, not judged.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    _Chem, Descriptors, rdMolDescriptors, _, _ = _require_rdkit()
    mol, canonical, fragment_notes, _ = _parse_smiles(smiles)
    slug = _slug(name) if name else _slug(canonical)

    sidecar = _build_sidecar("descriptors", smiles, canonical, fragment_notes)
    if name:
        sidecar.note("compound_name", name)

    descriptors = {
        "molecular_weight": round(Descriptors.MolWt(mol), 2),
        "logp": round(Descriptors.MolLogP(mol), 2),
        "tpsa": round(Descriptors.TPSA(mol), 2),
        "hba": Descriptors.NumHAcceptors(mol),
        "hbd": Descriptors.NumHDonors(mol),
        "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "aromatic_rings": Descriptors.NumAromaticRings(mol),
        "heavy_atom_count": mol.GetNumHeavyAtoms(),
    }

    record = {
        "tool": "compound",
        "subcommand": "descriptors",
        "canonical_smiles": canonical,
        "descriptors": descriptors,
    }

    record_path = target_dir / f"{slug}.descriptors.json"
    content = json.dumps(record, indent=2) + "\n"
    _safe_write_artifact(record_path, content, overwrite=overwrite)
    meta_path = target_dir / f"{slug}.descriptors.meta.json"
    sidecar.add_output(record_path)
    meta_path = sidecar.write(meta_path)

    emit.data("canonical_smiles", canonical)
    emit.data("descriptors", descriptors)
    emit.path(record_path, role="descriptors")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — alerts
# ---------------------------------------------------------------------------


@compound.command("alerts")
@click.argument("smiles")
@out_option
@name_option
@_overwrite_option
@output_options
@pass_state
def alerts_cmd(
    state: AppState,
    smiles: str,
    out: str | None,
    name: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Run PAINS, Brenk, and aggregator substructure filters.

    Phase 1: alert hits are recorded with pattern names and sources.  No
    judgment is made about the compound — that belongs to ``analyze``.
    A clean result (no hits) is a first-class outcome, not the absence of
    one.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    Chem, _, _, FilterCatalog, FilterCatalogParams = _require_rdkit()
    mol, canonical, fragment_notes, _ = _parse_smiles(smiles)
    slug = _slug(name) if name else _slug(canonical)

    sidecar = _build_sidecar("alerts", smiles, canonical, fragment_notes)
    if name:
        sidecar.note("compound_name", name)

    # --- PAINS filters (built-in catalog) ---
    pains_params = FilterCatalogParams()
    pains_params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
    pains_catalog = FilterCatalog(pains_params)

    pains_hits: list[dict[str, str]] = []
    for entry in pains_catalog.GetMatches(mol):
        pains_hits.append(
            {
                "pattern_name": entry.GetDescription(),
                "source": "PAINS",
            }
        )

    # --- Brenk filters (built-in catalog) ---
    brenk_params = FilterCatalogParams()
    brenk_params.AddCatalog(FilterCatalogParams.FilterCatalogs.BRENK)
    brenk_catalog = FilterCatalog(brenk_params)

    brenk_hits: list[dict[str, str]] = []
    for entry in brenk_catalog.GetMatches(mol):
        brenk_hits.append(
            {
                "pattern_name": entry.GetDescription(),
                "source": "Brenk",
            }
        )

    # --- Aggregator patterns (custom SMARTS; no built-in catalog) ---
    aggregator_hits: list[dict[str, str]] = []
    for agg_name, smarts in _AGGREGATOR_PATTERNS:
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is not None and mol.HasSubstructMatch(pattern):
            aggregator_hits.append(
                {
                    "pattern_name": agg_name,
                    "smarts": smarts,
                    "source": "aggregator",
                }
            )

    all_hits = pains_hits + brenk_hits + aggregator_hits

    record: dict[str, Any] = {
        "tool": "compound",
        "subcommand": "alerts",
        "canonical_smiles": canonical,
        "n_alerts": len(all_hits),
        "pains_hits": pains_hits,
        "brenk_hits": brenk_hits,
        "aggregator_hits": aggregator_hits,
        "clean": len(all_hits) == 0,
    }

    record_path = target_dir / f"{slug}.alerts.json"
    content = json.dumps(record, indent=2) + "\n"
    _safe_write_artifact(record_path, content, overwrite=overwrite)
    meta_path = target_dir / f"{slug}.alerts.meta.json"
    sidecar.add_output(record_path)
    meta_path = sidecar.write(meta_path)

    emit.data("canonical_smiles", canonical)
    emit.data("n_alerts", len(all_hits))
    emit.data("clean", len(all_hits) == 0)
    if pains_hits:
        emit.data("pains_hits", pains_hits)
    if brenk_hits:
        emit.data("brenk_hits", brenk_hits)
    if aggregator_hits:
        emit.data("aggregator_hits", aggregator_hits)
    emit.path(record_path, role="alerts")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — prepare-3d
# ---------------------------------------------------------------------------


_FORCE_FIELDS = ("MMFF", "UFF")


@compound.command("prepare-3d")
@click.argument("smiles")
@out_option
@name_option
@click.option(
    "--force-field",
    type=click.Choice(_FORCE_FIELDS, case_sensitive=False),
    default=None,
    help="Force field for geometry optimization (default: MMFF, falling back to UFF).",
)
@output_options
@pass_state
def prepare_3d_cmd(
    state: AppState,
    smiles: str,
    out: str | None,
    name: str | None,
    force_field: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Generate 3D coordinates for a SMILES string.

    Adds hydrogens, embeds the molecule using ETKDGv3, and optimizes
    geometry with MMFF (default) or UFF.  Writes an SDF file with 3D
    coordinates suitable for docking workflows.  Phase 1: the 3D
    structure is produced, not judged.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    Chem, AllChem = _require_rdkit_3d()
    mol, canonical, fragment_notes, _ = _parse_smiles(smiles)
    slug = _slug(name) if name else _slug(canonical)

    sidecar = _build_sidecar("prepare-3d", smiles, canonical, fragment_notes)
    if name:
        sidecar.note("compound_name", name)

    # Whether the user explicitly requested a force field or we are using
    # the default with automatic fallback.
    explicit_ff = force_field is not None
    selected_ff = (force_field or "MMFF").upper()

    # Record RDKit version for provenance.
    from rdkit import rdBase

    rdkit_version = rdBase.rdkitVersion

    # Add hydrogens — required for realistic 3D geometry.
    mol_h = Chem.AddHs(mol)

    # Generate 3D coordinates using ETKDGv3.
    params = AllChem.ETKDGv3()
    embed_result = AllChem.EmbedMolecule(mol_h, params)
    if embed_result == -1:
        raise ArtifactError(
            f"3D embedding failed for {canonical!r}",
            detail="AllChem.EmbedMolecule returned -1; the molecule may be "
            "too constrained or too large for coordinate generation",
            remedy="check the SMILES for unusual ring systems or strained "
            "geometries; try a simpler analog first",
        )

    # Optimize geometry with the selected force field.
    converged = False
    if selected_ff == "MMFF":
        try:
            ff_result = AllChem.MMFFOptimizeMolecule(mol_h)
            if ff_result == -1:
                raise RuntimeError("MMFF parameterization failed")
            converged = ff_result == 0
        except RuntimeError as e:
            if explicit_ff:
                raise ArtifactError(
                    f"MMFF force field cannot parameterize {canonical!r}",
                    detail="MMFF parameterization failed and --force-field MMFF "
                    "was explicitly requested",
                    remedy="try --force-field UFF, which covers a broader "
                    "range of atom types",
                ) from e
            # Automatic fallback to UFF.
            selected_ff = "UFF"
            sidecar.warn(
                "MMFF parameterization failed; fell back to UFF force field.",
            )

    if selected_ff == "UFF":
        try:
            ff_result = AllChem.UFFOptimizeMolecule(mol_h)
            converged = ff_result == 0
        except (ValueError, RuntimeError) as e:
            raise ArtifactError(
                f"UFF force field cannot parameterize {canonical!r}",
                detail="UFF optimization failed; the molecule may contain "
                "atom types not covered by the Universal Force Field",
                remedy="verify the SMILES encodes a valid organic molecule; "
                "exotic metals or unusual valences may not be parameterizable",
            ) from e

    # Write SDF with 3D coordinates.
    sdf_path = target_dir / f"{slug}.3d.sdf"
    _guard_sdf_no_clobber(sdf_path)
    writer = Chem.SDWriter(str(sdf_path))
    try:
        writer.write(mol_h)
    finally:
        writer.close()

    # Build provenance sidecar with 3D-specific metadata.
    sidecar.note("rdkit_version", rdkit_version)
    sidecar.note("embedding_method", "ETKDGv3")
    sidecar.note("force_field", selected_ff)
    sidecar.note("hydrogens_added", True)
    sidecar.note("optimization_converged", converged)

    sidecar.add_output(sdf_path)
    meta_path = sidecar.write(target_dir / f"{slug}.prepare-3d.meta.json")

    emit.data("canonical_smiles", canonical)
    emit.data("force_field", selected_ff)
    emit.data("optimization_converged", converged)
    emit.path(sdf_path, role="sdf")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — sa-score
# ---------------------------------------------------------------------------


@compound.command("sa-score")
@click.argument("smiles")
@out_option
@name_option
@_overwrite_option
@output_options
@pass_state
def sa_score_cmd(
    state: AppState,
    smiles: str,
    out: str | None,
    name: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Compute the synthetic accessibility score for a SMILES string.

    The SA-score (Ertl & Schuffenhauer 2009) rates how easy a molecule
    is to synthesize on a 1-10 scale (1 = easy, 10 = hard).  Phase 1:
    the score is recorded, not judged.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    _require_rdkit()
    sascorer = _require_rdkit_sa_score()
    mol, canonical, fragment_notes, _ = _parse_smiles(smiles)
    slug = _slug(name) if name else _slug(canonical)

    import rdkit

    sidecar = _build_sidecar("sa-score", smiles, canonical, fragment_notes)
    if name:
        sidecar.note("compound_name", name)
    sidecar.note("rdkit_version", rdkit.__version__)
    sidecar.note(
        "sa_score_source", "rdkit.Contrib.SA_Score.sascorer (Ertl & Schuffenhauer 2009)"
    )

    score = round(sascorer.calculateScore(mol), 2)

    record: dict[str, Any] = {
        "tool": "compound",
        "subcommand": "sa-score",
        "canonical_smiles": canonical,
        "sa_score": score,
        "rdkit_version": rdkit.__version__,
    }

    record_path = target_dir / f"{slug}.sa-score.json"
    content = json.dumps(record, indent=2) + "\n"
    _safe_write_artifact(record_path, content, overwrite=overwrite)
    meta_path = target_dir / f"{slug}.sa-score.meta.json"
    sidecar.add_output(record_path)
    meta_path = sidecar.write(meta_path)

    emit.data("canonical_smiles", canonical)
    emit.data("sa_score", score)
    emit.path(record_path, role="sa-score")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — profile (batch: validate + descriptors + alerts + sa-score)
# ---------------------------------------------------------------------------


@compound.command("profile")
@click.argument("smiles")
@name_option
@out_option
@_overwrite_option
@output_options
@pass_state
def profile_cmd(
    state: AppState,
    smiles: str,
    name: str | None,
    out: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Run all phase-1 steps: validate, descriptors, alerts, sa-score.

    This is a convenience wrapper that preserves the same individual
    artifacts and sidecars as running each command separately.  Stops
    on the first failure (if validate refuses, descriptors is not run).
    """
    ctx = click.get_current_context()
    steps = [
        ("validate", validate_cmd),
        ("descriptors", descriptors_cmd),
        ("alerts", alerts_cmd),
        ("sa-score", sa_score_cmd),
    ]

    for step_name, cmd_func in steps:
        if not quiet and not as_json:
            click.echo(f"--- {step_name} ---")
        try:
            ctx.invoke(
                cmd_func,
                smiles=smiles,
                name=name,
                out=out,
                overwrite=overwrite,
                as_json=as_json,
                quiet=quiet,
            )
        except Exception:
            if not quiet and not as_json:
                click.echo(f"--- {step_name} FAILED ---")
            raise


# ---------------------------------------------------------------------------
# Phase 2 — analyze
# ---------------------------------------------------------------------------

#: Descriptor-to-threshold mappings for Lipinski Rule of Five.
_LIPINSKI_CHECKS: list[tuple[str, str, str, str]] = [
    ("MW", "molecular_weight", "max_mw", "Lipinski et al., Adv Drug Deliv Rev 2001"),
    ("LogP", "logp", "max_logp", "Lipinski et al., Adv Drug Deliv Rev 2001"),
    ("HBD", "hbd", "max_hbd", "Lipinski et al., Adv Drug Deliv Rev 2001"),
    ("HBA", "hba", "max_hba", "Lipinski et al., Adv Drug Deliv Rev 2001"),
]

#: Descriptor-to-threshold mappings for Veber criteria.
_VEBER_CHECKS: list[tuple[str, str, str, str]] = [
    (
        "rotatable_bonds",
        "rotatable_bonds",
        "max_rotatable_bonds",
        "Veber et al., J Med Chem 2002",
    ),
    ("TPSA", "tpsa", "max_tpsa", "Veber et al., J Med Chem 2002"),
]


@compound.command("analyze")
@click.argument("smiles", required=False, default=None)
@from_option
@out_option
@name_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    smiles: str | None,
    from_dir: str | None,
    out: str | None,
    name: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply drug-likeness criteria to stored descriptors, alerts, and SA-score.

    Reads the ``.descriptors.json`` and ``.alerts.json`` written by the
    phase-1 subcommands, applies named threshold sets, and writes an
    ``.analysis.json`` with a verdict plus mandatory relays.  Optionally
    reads ``.sa-score.json`` when present (existing pipelines without
    SA-score are not affected).  This command never queries an endpoint —
    the phase-2 latch enforces that automatically.

    When ``--name`` is provided, the SMILES positional argument may be
    omitted — the slug is derived from the name and the canonical SMILES
    is read from the stored descriptors record.
    """
    if smiles is None and name is None:
        raise Refusal(
            "either SMILES or --name must be provided",
            detail="compound analyze needs at least one identifier to locate "
            "stored phase-1 artifacts",
            remedy="pass a SMILES string as a positional argument, or use "
            "--name to identify the compound by its registered name",
        )

    emit = Emitter(as_json=as_json, quiet=quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Derive slug from the name (if provided) or SMILES string directly
    # (no RDKit needed).  The user should pass the canonical SMILES
    # (printed by phase 1) when --name is not used.
    slug = _slug(name) if name else _slug(smiles)

    # --- locate stored phase-1 artifacts ---
    desc_path = source_dir / f"{slug}.descriptors.json"
    if not desc_path.is_file():
        raise ArtifactError(
            f"no descriptors found for {smiles!r} under {source_dir}",
            detail=f"tried slug {slug!r}; if you passed a non-canonical SMILES, "
            "pass the canonical form printed by `dde compound validate`",
            remedy="run `dde compound descriptors` first, then pass the "
            "canonical SMILES it printed",
        )
    desc_doc = provenance.read_json(desc_path, "descriptors record")
    canonical = desc_doc.get("canonical_smiles") or smiles or name

    alerts_path = source_dir / f"{slug}.alerts.json"
    if not alerts_path.is_file():
        raise ArtifactError(
            f"no alerts found for {canonical!r} under {source_dir}",
            remedy=f"run `dde compound alerts {smiles!r}` first",
        )
    alerts_doc = provenance.read_json(alerts_path, "alerts record")

    # SA-score is optional — existing pipelines must not break if absent.
    sa_score_path = source_dir / f"{slug}.sa-score.json"
    sa_score_doc: dict[str, Any] | None = None
    if sa_score_path.is_file():
        sa_score_doc = provenance.read_json(sa_score_path, "SA-score record")

    # --- load thresholds ---
    desc_thresholds = load_thresholds(state, "compound-descriptors")
    alert_thresholds = load_thresholds(state, "compound-alerts")
    sa_thresholds = (
        load_thresholds(state, "compound-sa-score") if sa_score_doc else None
    )

    # --- collect upstream relays from all phase-1 sidecars ---
    relays: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for suffix in ("validate", "descriptors", "alerts", "sa-score"):
        meta_candidate = source_dir / f"{slug}.{suffix}.meta.json"
        if meta_candidate.is_file():
            meta = provenance.read_json(meta_candidate, f"{suffix} sidecar")
            for r in meta.get("mandatory_relays", []) or []:
                if r["code"] not in seen_codes:
                    relays.append(r)
                    seen_codes.add(r["code"])

    descriptors = desc_doc.get("descriptors", {})

    # --- check Lipinski Rule of Five ---
    lipinski_violations: list[dict[str, Any]] = []
    for rule_name, desc_key, thresh_key, source in _LIPINSKI_CHECKS:
        value = descriptors.get(desc_key)
        limit = desc_thresholds.get(thresh_key)
        if value is not None and value > limit:
            lipinski_violations.append(
                {
                    "rule": rule_name,
                    "value": value,
                    "limit": limit,
                    "source": source,
                }
            )

    # --- check Veber criteria ---
    veber_violations: list[dict[str, Any]] = []
    for rule_name, desc_key, thresh_key, source in _VEBER_CHECKS:
        value = descriptors.get(desc_key)
        limit = desc_thresholds.get(thresh_key)
        if value is not None and value > limit:
            veber_violations.append(
                {
                    "rule": rule_name,
                    "value": value,
                    "limit": limit,
                    "source": source,
                }
            )

    # --- check alert thresholds ---
    pains_hits = alerts_doc.get("pains_hits", [])
    brenk_hits = alerts_doc.get("brenk_hits", [])
    aggregator_hits = alerts_doc.get("aggregator_hits", [])

    alert_violations: list[dict[str, Any]] = []
    if len(pains_hits) > alert_thresholds.get("max_pains_hits"):
        alert_violations.append(
            {
                "type": "PAINS",
                "count": len(pains_hits),
                "limit": alert_thresholds.get("max_pains_hits"),
            }
        )
    if len(brenk_hits) > alert_thresholds.get("max_brenk_hits"):
        alert_violations.append(
            {
                "type": "Brenk",
                "count": len(brenk_hits),
                "limit": alert_thresholds.get("max_brenk_hits"),
            }
        )
    if len(aggregator_hits) > alert_thresholds.get("max_aggregator_hits"):
        alert_violations.append(
            {
                "type": "aggregator",
                "count": len(aggregator_hits),
                "limit": alert_thresholds.get("max_aggregator_hits"),
            }
        )

    # --- check SA-score threshold (when available) ---
    sa_score_violation: dict[str, Any] | None = None
    if sa_score_doc is not None and sa_thresholds is not None:
        sa_value = sa_score_doc.get("sa_score")
        sa_limit = sa_thresholds.get("max_sa_score")
        if sa_value is not None and sa_value > sa_limit:
            sa_score_violation = {
                "rule": "SA-score",
                "value": sa_value,
                "limit": sa_limit,
                "source": "Ertl & Schuffenhauer, J Cheminformatics 2009",
            }

    # --- determine verdict ---
    # Lipinski Rule of Five: oral bioavailability is unlikely if 2+
    # violations. One violation is marginal, not failing.
    passes_lipinski = len(lipinski_violations) <= 1
    passes_veber = len(veber_violations) == 0
    passes_sa_score = sa_score_violation is None
    passes_rules = passes_lipinski and passes_veber and passes_sa_score
    alerts_flagged = len(alert_violations) > 0

    if passes_rules and not alerts_flagged:
        verdict = "drug-like"
        statement = (
            f"{canonical} passes all drug-likeness criteria and has no "
            "structural alerts above threshold."
        )
    elif passes_rules and alerts_flagged:
        verdict = "drug-like-with-alerts"
        alert_names = ", ".join(v["type"] for v in alert_violations)
        statement = (
            f"{canonical} passes drug-likeness rules but has structural "
            f"alerts: {alert_names}."
        )
    elif not passes_rules and not alerts_flagged:
        verdict = "rule-violations"
        all_v = lipinski_violations + veber_violations
        if sa_score_violation:
            all_v = [*all_v, sa_score_violation]
        rule_names = ", ".join(v["rule"] for v in all_v)
        statement = (
            f"{canonical} violates {len(all_v)} drug-likeness rule(s): {rule_names}."
        )
    else:
        verdict = "rule-violations-and-alerts"
        all_v = lipinski_violations + veber_violations
        if sa_score_violation:
            all_v = [*all_v, sa_score_violation]
        rule_names = ", ".join(v["rule"] for v in all_v)
        alert_names = ", ".join(v["type"] for v in alert_violations)
        statement = (
            f"{canonical} violates {len(all_v)} rule(s) ({rule_names}) and "
            f"has structural alerts ({alert_names})."
        )

    advisories: list[str] = []
    if len(lipinski_violations) == 1:
        v = lipinski_violations[0]
        advisories.append(
            f"One Lipinski violation ({v['rule']} = {v['value']}, "
            f"limit {v['limit']}).  The Rule of Five allows one violation — "
            "this compound is marginal by that criterion."
        )

    # --- conditional relay: alerts_not_toxicology ---
    # Fires only when alerts are clean, per the relay code registration.
    alerts_clean = alerts_doc.get(
        "clean", len(pains_hits) + len(brenk_hits) + len(aggregator_hits) == 0
    )
    if alerts_clean:
        relays.append(
            provenance.relay(
                "compound.alerts_not_toxicology",
                f"{canonical} has no PAINS/Brenk/aggregator hits, but "
                "absence of these alerts is not evidence of safety — these "
                "filters detect known interference patterns, not toxicity "
                "mechanisms.",
            )
        )

    # --- conditional relay: sa_score_is_estimate ---
    # Fires when SA-score is below threshold (passing result is where
    # the caveat matters most), following the alerts_not_toxicology pattern.
    if sa_score_doc is not None and passes_sa_score:
        relays.append(
            provenance.relay(
                "compound.sa_score_is_estimate",
                f"{canonical} SA-score ({sa_score_doc.get('sa_score')}) is "
                "below the accessibility threshold, but a low SA-score does "
                "not confirm synthetic feasibility — it reflects fragment "
                "frequency, not a synthesis route.",
            )
        )

    metrics: dict[str, Any] = {
        "canonical_smiles": canonical,
        "descriptors": descriptors,
        "n_lipinski_violations": len(lipinski_violations),
        "n_veber_violations": len(veber_violations),
        "n_alert_violations": len(alert_violations),
        "lipinski_violations": lipinski_violations,
        "veber_violations": veber_violations,
        "alert_violations": alert_violations,
        "pains_hits": len(pains_hits),
        "brenk_hits": len(brenk_hits),
        "aggregator_hits": len(aggregator_hits),
    }
    if sa_score_doc is not None:
        metrics["sa_score"] = sa_score_doc.get("sa_score")
        metrics["sa_score_violation"] = sa_score_violation
    assessment: dict[str, Any] = {
        "verdict": verdict,
        "statement": statement,
        "advisories": advisories,
    }

    combined_tag = f"{desc_thresholds.tag}+{alert_thresholds.tag}"
    thresholds_applied = {
        **desc_thresholds.applied(),
        **alert_thresholds.applied(),
    }
    threshold_sources = {
        **desc_thresholds.sources(),
        **alert_thresholds.sources(),
    }
    threshold_provenance = (
        f"{desc_thresholds.provenance}; {alert_thresholds.provenance}"
    )
    unresolved = desc_thresholds.unresolved() + alert_thresholds.unresolved()

    if sa_thresholds is not None:
        combined_tag = f"{combined_tag}+{sa_thresholds.tag}"
        thresholds_applied.update(sa_thresholds.applied())
        threshold_sources.update(sa_thresholds.sources())
        threshold_provenance = f"{threshold_provenance}; {sa_thresholds.provenance}"
        unresolved = unresolved + sa_thresholds.unresolved()

    analysis_path = target_dir / f"{slug}.analysis.json"
    provenance.write_analysis(
        analysis_path,
        source=state.project().relative(desc_path),
        threshold_set=combined_tag,
        thresholds_applied=thresholds_applied,
        metrics=metrics,
        assessment=assessment,
        threshold_sources=threshold_sources,
        threshold_provenance=threshold_provenance,
        unresolved=unresolved,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.line(f"{canonical}  [threshold_set {combined_tag}]")
    emit.line(f"{verdict}: {statement}")
    for note in advisories:
        emit.line(f"  - {note}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
