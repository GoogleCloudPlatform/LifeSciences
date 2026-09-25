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

"""`dde admet` — ADMET endpoint prediction from molecular descriptors.

Phase 1 (no judgment):
  predict    Compute predicted ADMET endpoints from a SMILES string.
             Five endpoints: metabolic stability, CYP inhibition risk,
             permeability estimate, hERG liability, and solubility (ESOL).

Phase 2 (offline, applies thresholds):
  analyze    Read stored predictions, apply the `admet-endpoints` threshold
             set, produce an analysis record with verdict and relays.

All predictions are rule-based — molecular descriptors and SMARTS
pharmacophore patterns, not trained ML models. Every endpoint names its
source publication and the specific rules applied. A clean predicted
ADMET profile is not a substitute for in vitro ADMET studies.

Multi-fragment SMILES (salts, mixtures) follow the "strip counterions,
alert on parent" convention. The convention is documented in the sidecar
— the parent is not chosen silently. An unparseable SMILES is a refusal
(exit 9), not a failure, because a different input string is the remedy.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
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
from ..core.paths import is_safe_to_open

ARTIFACT_CLASS = "admet"


# ---------------------------------------------------------------------------
# RDKit lazy import
# ---------------------------------------------------------------------------


def _require_rdkit():
    """Lazy-import RDKit, raising DependencyError if absent."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, rdMolDescriptors

        return Chem, Descriptors, rdMolDescriptors
    except ImportError as e:
        raise DependencyError(
            "RDKit is not installed",
            detail="ADMET prediction requires RDKit for molecular descriptor computation",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi)",
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
    """Create a provenance sidecar for a phase-1 admet subcommand."""
    sidecar = provenance.Sidecar(
        tool="admet",
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


def _safe_write_artifact(path: Path, content: str, *, overwrite: bool) -> bool:
    """Write artifact content with overwrite protection.

    Returns True if the file was written (new file or overwrite mode).
    Returns False if skipped because identical content already exists.
    Raises :class:`Refusal` if the file exists with different content
    and *overwrite* is False.
    """
    path = Path(path)
    if not is_safe_to_open(path):
        raise Refusal(f"Refusing to write through symlink: {path}")
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
# SMARTS patterns for pharmacophore detection
# ---------------------------------------------------------------------------

# Basic nitrogen: protonatable at physiological pH.
# Matches primary, secondary, tertiary amines; excludes imines (N=*)
# and nitriles (N#*).
_BASIC_NITROGEN_SMARTS = "[NH2,NH1,NH0;!$(N=*);!$(N#*)]"

# Acidic group: carboxylic acid (for CYP2C9 risk).
_ACIDIC_GROUP_SMARTS = "[CX3](=O)[OX2H1]"


# ---------------------------------------------------------------------------
# Endpoint prediction functions
# ---------------------------------------------------------------------------


def _predict_metabolic_stability(
    mol: Any,
    logp: float,
    aromatic_rings: int,
) -> dict[str, Any]:
    """Predict metabolic stability class from LogP and aromatic ring count.

    Source: Gleeson 2008, "Generation of a Set of Simple, Interpretable
    ADMET Rules of Thumb", J Med Chem 2008;51:817-834.

    Gleeson's rules:
      - LogP > 4 AND aromatic ring count >= 3 → high metabolic liability
      - LogP 1-3 → low metabolic liability
      - Intermediate → moderate
    """
    if logp > 4 and aromatic_rings >= 3:
        stability_class = "high"
    elif 1 <= logp <= 3:
        stability_class = "low"
    else:
        stability_class = "moderate"

    return {
        "endpoint": "metabolic_stability",
        "predicted_class": stability_class,
        "contributing_descriptors": {
            "logp": logp,
            "aromatic_rings": aromatic_rings,
        },
        "rules_applied": (
            "Gleeson 2008: LogP > 4 AND aromatic rings >= 3 → high; "
            "LogP 1-3 → low; otherwise → moderate"
        ),
        "citation": (
            "Gleeson, 'Generation of a Set of Simple, Interpretable ADMET "
            "Rules of Thumb', J Med Chem 2008;51:817-834"
        ),
    }


def _predict_cyp_inhibition(
    mol: Any,
    Chem: Any,
    logp: float,
    mw: float,
) -> dict[str, Any]:
    """Predict CYP inhibition risk for CYP2D6, CYP3A4, CYP2C9.

    Sources:
      - Gleeson 2008 (J Med Chem 2008;51:817-834): lipophilic bases with
        LogP > 4 and basic nitrogen are CYP2D6 risk.
      - General CYP rules: CYP3A4 risk correlates with MW > 400 and
        LogP > 3 (larger, lipophilic molecules).
      - CYP2C9: acidic compounds with LogP > 2.
    """
    basic_n_pattern = Chem.MolFromSmarts(_BASIC_NITROGEN_SMARTS)
    acidic_pattern = Chem.MolFromSmarts(_ACIDIC_GROUP_SMARTS)

    has_basic_nitrogen = basic_n_pattern is not None and mol.HasSubstructMatch(
        basic_n_pattern
    )
    has_acidic_group = acidic_pattern is not None and mol.HasSubstructMatch(
        acidic_pattern
    )

    isoforms: dict[str, dict[str, Any]] = {}

    # CYP2D6: lipophilic bases — LogP > 4 and basic nitrogen
    cyp2d6_flagged = logp > 4 and has_basic_nitrogen
    cyp2d6_features = []
    if logp > 4:
        cyp2d6_features.append(f"LogP = {logp} (> 4)")
    if has_basic_nitrogen:
        cyp2d6_features.append("basic nitrogen detected")
    isoforms["CYP2D6"] = {
        "flagged": cyp2d6_flagged,
        "triggering_features": cyp2d6_features if cyp2d6_flagged else [],
        "citation": (
            "Gleeson, 'Generation of a Set of Simple, Interpretable ADMET "
            "Rules of Thumb', J Med Chem 2008;51:817-834"
        ),
    }

    # CYP3A4: larger lipophilic molecules — MW > 400 and LogP > 3
    cyp3a4_flagged = mw > 400 and logp > 3
    cyp3a4_features = []
    if mw > 400:
        cyp3a4_features.append(f"MW = {mw} (> 400)")
    if logp > 3:
        cyp3a4_features.append(f"LogP = {logp} (> 3)")
    isoforms["CYP3A4"] = {
        "flagged": cyp3a4_flagged,
        "triggering_features": cyp3a4_features if cyp3a4_flagged else [],
        "citation": (
            "General CYP3A4 risk rule: MW > 400 and LogP > 3 correlate "
            "with CYP3A4 inhibition risk"
        ),
    }

    # CYP2C9: acidic compounds with LogP > 2
    cyp2c9_flagged = logp > 2 and has_acidic_group
    cyp2c9_features = []
    if logp > 2:
        cyp2c9_features.append(f"LogP = {logp} (> 2)")
    if has_acidic_group:
        cyp2c9_features.append("carboxylic acid detected")
    isoforms["CYP2C9"] = {
        "flagged": cyp2c9_flagged,
        "triggering_features": cyp2c9_features if cyp2c9_flagged else [],
        "citation": (
            "General CYP2C9 risk rule: acidic compounds with LogP > 2 "
            "correlate with CYP2C9 inhibition risk"
        ),
    }

    return {
        "endpoint": "cyp_inhibition",
        "isoforms": isoforms,
        "any_flagged": any(iso["flagged"] for iso in isoforms.values()),
    }


def _predict_permeability(
    mol: Any,
    logp: float,
    tpsa: float,
    mw: float,
) -> dict[str, Any]:
    """Predict passive permeability using the Egan egg model.

    Source: Egan et al., "Prediction of Drug Absorption Using Multivariate
    Statistics", J Med Chem 2000;43:3867-3877.

    Egan's boundaries:
      - TPSA <= 132 AND LogP in [-1, 6] → high permeability
      - TPSA > 150 → low permeability
      - Otherwise → moderate
    Also: MW > 500 reduces permeability regardless.
    """
    if mw > 500:
        permeability_class = "low"
        mw_penalty = True
    elif tpsa <= 132 and -1 <= logp <= 6:
        permeability_class = "high"
        mw_penalty = False
    elif tpsa > 150:
        permeability_class = "low"
        mw_penalty = False
    else:
        permeability_class = "moderate"
        mw_penalty = False

    result: dict[str, Any] = {
        "endpoint": "permeability",
        "predicted_class": permeability_class,
        "contributing_descriptors": {
            "tpsa": tpsa,
            "logp": logp,
            "mw": mw,
        },
        "rules_applied": (
            "Egan 2000: TPSA <= 132 AND LogP in [-1, 6] → high; "
            "TPSA > 150 → low; MW > 500 → low (override); "
            "otherwise → moderate"
        ),
        "citation": (
            "Egan et al., 'Prediction of Drug Absorption Using Multivariate "
            "Statistics', J Med Chem 2000;43:3867-3877"
        ),
    }
    if mw_penalty:
        result["mw_penalty"] = (
            f"MW = {mw} (> 500): very high molecular weight reduces "
            "permeability regardless of TPSA and LogP"
        )
    return result


def _predict_herg(
    mol: Any,
    Chem: Any,
    logp: float,
    aromatic_rings: int,
) -> dict[str, Any]:
    """Predict hERG liability from pharmacophore features.

    Source: Aronov 2005, "Predictive in silico modeling for hERG channel
    blockers", Drug Discov Today 2005;10:149-155.

    Key features: basic nitrogen (protonatable at physiological pH)
    + LogP > 3.7 + two or more aromatic rings (hydrophobic mass).
    """
    basic_n_pattern = Chem.MolFromSmarts(_BASIC_NITROGEN_SMARTS)
    has_basic_nitrogen = basic_n_pattern is not None and mol.HasSubstructMatch(
        basic_n_pattern
    )

    triggering_features: list[str] = []
    if has_basic_nitrogen:
        triggering_features.append("basic nitrogen (protonatable at physiological pH)")
    if logp > 3.7:
        triggering_features.append(f"LogP = {logp} (> 3.7, hydrophobic)")
    if aromatic_rings >= 2:
        triggering_features.append(
            f"{aromatic_rings} aromatic ring(s) (>= 2, hydrophobic mass)"
        )

    flagged = has_basic_nitrogen and logp > 3.7 and aromatic_rings >= 2

    return {
        "endpoint": "herg_liability",
        "flagged": flagged,
        "triggering_features": triggering_features if flagged else [],
        "contributing_descriptors": {
            "has_basic_nitrogen": has_basic_nitrogen,
            "logp": logp,
            "aromatic_rings": aromatic_rings,
        },
        "limitations": (
            "Rule-based hERG prediction has a documented false-negative rate. "
            "Absence of this flag is not evidence of hERG safety. Many hERG "
            "blockers do not match this pharmacophore pattern, and this "
            "prediction cannot substitute for a measured IC50 or patch-clamp "
            "result."
        ),
        "rules_applied": (
            "Aronov 2005: basic nitrogen + LogP > 3.7 + >= 2 aromatic rings "
            "→ hERG liability flagged"
        ),
        "citation": (
            "Aronov, 'Predictive in silico modeling for hERG channel blockers', "
            "Drug Discov Today 2005;10:149-155"
        ),
    }


def _predict_solubility(
    mol: Any,
    Descriptors: Any,
    rdMolDescriptors: Any,
    logp: float,
    mw: float,
    rotatable_bonds: int,
) -> dict[str, Any]:
    """Predict aqueous solubility using the ESOL (Delaney) model.

    Source: Delaney 2004, "ESOL: Estimating Aqueous Solubility Directly
    from Molecular Structure", J Chem Inf Comput Sci 2004;44:1000-1009.

    Published equation:
      Log(S) = 0.16 - 0.63*cLogP - 0.0062*MW + 0.066*RB - 0.74*AP

    Where:
      cLogP = RDKit MolLogP
      MW = molecular weight
      RB = rotatable bonds
      AP = aromatic proportion (aromatic atoms / heavy atoms)
    """
    heavy_atoms = mol.GetNumHeavyAtoms()
    aromatic_atoms = sum(1 for atom in mol.GetAtoms() if atom.GetIsAromatic())
    aromatic_proportion = aromatic_atoms / heavy_atoms if heavy_atoms > 0 else 0.0

    # ESOL equation coefficients
    intercept = 0.16
    coeff_logp = -0.63
    coeff_mw = -0.0062
    coeff_rb = 0.066
    coeff_ap = -0.74

    predicted_logs = (
        intercept
        + coeff_logp * logp
        + coeff_mw * mw
        + coeff_rb * rotatable_bonds
        + coeff_ap * aromatic_proportion
    )
    predicted_logs = round(predicted_logs, 3)

    # Classification from LogS
    if predicted_logs > -1:
        solubility_class = "high"
    elif predicted_logs >= -3:
        solubility_class = "moderate"
    elif predicted_logs >= -5:
        solubility_class = "low"
    else:
        solubility_class = "insoluble"

    return {
        "endpoint": "solubility",
        "predicted_logS": predicted_logs,
        "predicted_class": solubility_class,
        "contributing_descriptors": {
            "cLogP": logp,
            "mw": mw,
            "rotatable_bonds": rotatable_bonds,
            "aromatic_proportion": round(aromatic_proportion, 4),
            "aromatic_atoms": aromatic_atoms,
            "heavy_atoms": heavy_atoms,
        },
        "equation_coefficients": {
            "intercept": intercept,
            "cLogP": coeff_logp,
            "MW": coeff_mw,
            "RB": coeff_rb,
            "AP": coeff_ap,
        },
        "classification_boundaries": {
            "high": "> -1",
            "moderate": "-1 to -3",
            "low": "-3 to -5",
            "insoluble": "< -5",
        },
        "citation": (
            "Delaney, 'ESOL: Estimating Aqueous Solubility Directly from "
            "Molecular Structure', J Chem Inf Comput Sci 2004;44:1000-1009"
        ),
    }


# ---------------------------------------------------------------------------
# Core prediction logic (shared by predict and predict-batch)
# ---------------------------------------------------------------------------


def _predict_single(smiles_input: str) -> dict[str, Any]:
    """Run all five ADMET endpoint predictions for a single SMILES.

    Returns the prediction record dict.  This is the shared logic called
    by both ``predict`` (single compound) and ``predict-batch`` (batch).
    Callers handle I/O, provenance, and emitter concerns.
    """
    Chem, Descriptors, rdMolDescriptors = _require_rdkit()
    mol, canonical, fragment_notes, _ = _parse_smiles(smiles_input)

    # --- compute shared descriptors ---
    logp = round(Descriptors.MolLogP(mol), 2)
    mw = round(Descriptors.MolWt(mol), 2)
    tpsa = round(Descriptors.TPSA(mol), 2)
    aromatic_rings = Descriptors.NumAromaticRings(mol)
    rotatable_bonds = rdMolDescriptors.CalcNumRotatableBonds(mol)

    # --- predict all five endpoints ---
    metabolic = _predict_metabolic_stability(mol, logp, aromatic_rings)
    cyp = _predict_cyp_inhibition(mol, Chem, logp, mw)
    permeability = _predict_permeability(mol, logp, tpsa, mw)
    herg = _predict_herg(mol, Chem, logp, aromatic_rings)
    solubility = _predict_solubility(
        mol,
        Descriptors,
        rdMolDescriptors,
        logp,
        mw,
        rotatable_bonds,
    )

    return {
        "tool": "admet",
        "subcommand": "predict",
        "canonical_smiles": canonical,
        "fragment_notes": fragment_notes,
        "molecular_descriptors": {
            "logp": logp,
            "mw": mw,
            "tpsa": tpsa,
            "aromatic_rings": aromatic_rings,
            "rotatable_bonds": rotatable_bonds,
        },
        "endpoints": {
            "metabolic_stability": metabolic,
            "cyp_inhibition": cyp,
            "permeability": permeability,
            "herg_liability": herg,
            "solubility": solubility,
        },
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def admet() -> None:
    """ADMET endpoint prediction (RDKit)."""


# ---------------------------------------------------------------------------
# Phase 1 — predict
# ---------------------------------------------------------------------------


@admet.command("predict")
@click.argument("smiles")
@out_option
@name_option
@_overwrite_option
@output_options
@pass_state
def predict_cmd(
    state: AppState,
    smiles: str,
    out: str | None,
    name: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Predict ADMET endpoints from a SMILES string.

    Five rule-based endpoints: metabolic stability, CYP inhibition risk,
    permeability, hERG liability, and solubility (ESOL model).  Phase 1:
    predictions are recorded with sources and contributing descriptors.
    No judgment is made — that belongs to ``analyze``.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    record = _predict_single(smiles)
    canonical = record["canonical_smiles"]
    fragment_notes = record.get("fragment_notes", {})
    slug = _slug(name) if name else _slug(canonical)

    sidecar = _build_sidecar("predict", smiles, canonical, fragment_notes)
    if name:
        sidecar.note("compound_name", name)

    record_path = target_dir / f"{slug}.predict.json"
    content = json.dumps(record, indent=2, allow_nan=False) + "\n"
    _safe_write_artifact(record_path, content, overwrite=overwrite)
    sidecar.add_output(record_path)
    meta_path = sidecar.write(target_dir / f"{slug}.predict.meta.json")

    emit.data("canonical_smiles", canonical)
    emit.data("endpoints", record["endpoints"])
    emit.path(record_path, role="predict")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — predict-batch
# ---------------------------------------------------------------------------


def _read_batch_input(input_path: Path) -> list[dict[str, str]]:
    """Read a CSV or JSON batch input file.

    Returns a list of dicts with ``smiles`` and optional ``name`` keys.
    CSV files must have a ``smiles`` column; JSON files must be a list of
    objects with a ``smiles`` key.  Uses stdlib ``csv`` — no pandas.
    """
    text = input_path.read_text(encoding="utf-8")
    suffix = input_path.suffix.lower()

    if suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise Refusal(
                f"JSON batch input must be a list of objects, got {type(data).__name__}",
                detail='expected [{"smiles": "...", ...}, ...]',
                remedy="wrap the data in a JSON array",
            )
        for i, entry in enumerate(data):
            if not isinstance(entry, dict) or "smiles" not in entry:
                raise Refusal(
                    f"entry {i} in JSON batch input is missing a 'smiles' key",
                    detail=f"got: {entry!r}"[:200],
                    remedy="each object must have a 'smiles' key",
                )
        return [{"smiles": e["smiles"], "name": e.get("name", "")} for e in data]

    # CSV (default for .csv or any non-JSON)
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "smiles" not in reader.fieldnames:
        raise Refusal(
            "CSV batch input must have a 'smiles' column",
            detail=f"columns found: {reader.fieldnames}",
            remedy="add a header row with at least 'smiles' as a column name",
        )
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({"smiles": row["smiles"], "name": row.get("name", "")})
    return rows


def _endpoint_pass_fail(endpoints: dict[str, Any]) -> dict[str, str]:
    """Derive simple pass/fail flags from predicted endpoints.

    ``pass`` means no liability was flagged for the endpoint;
    ``fail`` means the prediction flagged a concern.
    """
    flags: dict[str, str] = {}

    # metabolic stability: "high" liability → fail
    metab = endpoints.get("metabolic_stability", {})
    metab_class = metab.get("predicted_class", "")
    flags["metabolic_stability"] = "fail" if metab_class == "high" else "pass"

    # CYP inhibition: any isoform flagged → fail
    cyp = endpoints.get("cyp_inhibition", {})
    flags["cyp_inhibition"] = "fail" if cyp.get("any_flagged") else "pass"

    # permeability: "low" → fail
    perm = endpoints.get("permeability", {})
    perm_class = perm.get("predicted_class", "")
    flags["permeability"] = "fail" if perm_class == "low" else "pass"

    # hERG: flagged → fail
    herg = endpoints.get("herg_liability", {})
    flags["herg_liability"] = "fail" if herg.get("flagged") else "pass"

    # solubility: "insoluble" → fail
    sol = endpoints.get("solubility", {})
    sol_class = sol.get("predicted_class", "")
    flags["solubility"] = "fail" if sol_class == "insoluble" else "pass"

    return flags


@admet.command("predict-batch")
@click.argument("input_file", type=click.Path())
@out_option
@_overwrite_option
@output_options
@pass_state
def predict_batch_cmd(
    state: AppState,
    input_file: str,
    out: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Predict ADMET endpoints for a batch of compounds.

    Reads a CSV or JSON file containing SMILES strings and runs the same
    prediction logic as ``admet predict`` for each compound.  CSV files
    must have a ``smiles`` column (and an optional ``name`` column); JSON
    files must be a list of objects with a ``smiles`` key (and optional
    ``name``).

    If a compound fails, the error is recorded and processing continues
    with the next compound.

    Outputs under ``raw/admet/``:

    \b
      {slug}.predict.json          — per-compound prediction (one each)
      batch.admet-batch.json       — comparative summary table
      batch.admet-batch.meta.json  — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    input_path = Path(input_file)
    if not input_path.is_absolute():
        input_path = state.project().root / input_path
    if not input_path.is_file():
        raise ArtifactError(
            f"batch input file not found: {input_path}",
            remedy="provide a CSV or JSON file with SMILES strings",
        )

    entries = _read_batch_input(input_path)
    if not entries:
        raise Refusal(
            "batch input file contains no compounds",
            remedy="add at least one row/object with a 'smiles' key",
        )

    sidecar = provenance.Sidecar(
        tool="admet",
        subcommand="predict-batch",
        endpoint=None,
        parameters={
            "input_file": input_path.name,
            "n_compounds": len(entries),
        },
    )

    # --- propagate upstream relays from input sidecar ---
    seen_codes: set[str] = set()
    for meta_candidate in (
        input_path.with_suffix(".meta.json"),
        input_path.parent / f"{input_path.stem}.meta.json",
    ):
        if meta_candidate.is_file():
            upstream = provenance.read_json(meta_candidate, "input sidecar")
            for r in upstream.get("mandatory_relays", []) or []:
                if r["code"] not in seen_codes:
                    sidecar.relays.append(r)
                    seen_codes.add(r["code"])

    summary_rows: list[dict[str, Any]] = []
    n_succeeded = 0
    n_failed = 0

    for i, entry in enumerate(entries):
        smiles_input = entry["smiles"]
        name = entry.get("name", "")

        try:
            record = _predict_single(smiles_input)
            canonical = record["canonical_smiles"]
            slug = _slug(canonical)

            # Write individual prediction JSON
            record_path = target_dir / f"{slug}.predict.json"
            content = json.dumps(record, indent=2, allow_nan=False) + "\n"
            _safe_write_artifact(record_path, content, overwrite=overwrite)
            sidecar.add_output(record_path)

            # Per-compound provenance sidecar
            fragment_notes = record.get("fragment_notes", {})
            compound_sidecar = _build_sidecar(
                "predict", smiles_input, canonical, fragment_notes
            )
            if name:
                compound_sidecar.note("compound_name", name)
            compound_sidecar.add_output(record_path)
            compound_sidecar.write(target_dir / f"{slug}.predict.meta.json")

            # Build summary row
            flags = _endpoint_pass_fail(record["endpoints"])
            row: dict[str, Any] = {
                "index": i,
                "input_smiles": smiles_input,
                "canonical_smiles": canonical,
                "status": "ok",
                "endpoints": {},
            }
            if name:
                row["name"] = name

            # Add each endpoint's predicted class/value and pass/fail
            metab = record["endpoints"]["metabolic_stability"]
            row["endpoints"]["metabolic_stability"] = {
                "predicted_class": metab["predicted_class"],
                "pass_fail": flags["metabolic_stability"],
            }
            cyp = record["endpoints"]["cyp_inhibition"]
            row["endpoints"]["cyp_inhibition"] = {
                "any_flagged": cyp["any_flagged"],
                "pass_fail": flags["cyp_inhibition"],
            }
            perm = record["endpoints"]["permeability"]
            row["endpoints"]["permeability"] = {
                "predicted_class": perm["predicted_class"],
                "pass_fail": flags["permeability"],
            }
            herg = record["endpoints"]["herg_liability"]
            row["endpoints"]["herg_liability"] = {
                "flagged": herg["flagged"],
                "pass_fail": flags["herg_liability"],
            }
            sol = record["endpoints"]["solubility"]
            row["endpoints"]["solubility"] = {
                "predicted_class": sol["predicted_class"],
                "predicted_logS": sol["predicted_logS"],
                "pass_fail": flags["solubility"],
            }

            summary_rows.append(row)
            n_succeeded += 1
            emit.line(
                f"{canonical}: predicted ({name})"
                if name
                else f"{canonical}: predicted"
            )

        except Exception as exc:
            # Record error and continue with next compound
            error_row: dict[str, Any] = {
                "index": i,
                "input_smiles": smiles_input,
                "status": "error",
                "error": str(exc),
            }
            if name:
                error_row["name"] = name
            summary_rows.append(error_row)
            n_failed += 1
            emit.line(f"{smiles_input}: error — {str(exc)[:100]}")

    # --- write batch summary ---
    batch_record: dict[str, Any] = {
        "tool": "admet",
        "subcommand": "predict-batch",
        "input_file": input_path.name,
        "n_compounds": len(entries),
        "n_succeeded": n_succeeded,
        "n_failed": n_failed,
        "compounds": summary_rows,
    }

    batch_path = target_dir / "batch.admet-batch.json"
    batch_content = json.dumps(batch_record, indent=2, allow_nan=False) + "\n"
    _safe_write_artifact(batch_path, batch_content, overwrite=overwrite)
    sidecar.add_output(batch_path)
    meta_path = sidecar.write(target_dir / "batch.admet-batch.meta.json")

    emit.line(f"batch complete: {n_succeeded} succeeded, {n_failed} failed")
    emit.data("n_compounds", len(entries))
    emit.data("n_succeeded", n_succeeded)
    emit.data("n_failed", n_failed)
    emit.data("compounds", summary_rows)
    emit.path(batch_path, role="batch-summary")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — topical (Potts-Guy skin permeability)
# ---------------------------------------------------------------------------


def _compute_mw_from_smiles(smiles: str) -> float:
    """Compute molecular weight from SMILES using RDKit if available,
    otherwise raise DependencyError."""
    Chem, Descriptors, _ = _require_rdkit()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise Refusal(
            f"unparseable SMILES: {smiles!r}",
            detail="RDKit could not interpret this as a valid molecular structure",
            remedy="check the SMILES syntax",
        )
    return round(Descriptors.MolWt(mol), 2)


def _compute_logp_from_smiles(smiles: str) -> float:
    """Compute LogP from SMILES using RDKit."""
    Chem, Descriptors, _ = _require_rdkit()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise Refusal(
            f"unparseable SMILES: {smiles!r}",
            detail="RDKit could not interpret this as a valid molecular structure",
            remedy="check the SMILES syntax",
        )
    return round(Descriptors.MolLogP(mol), 2)


@admet.command("topical")
@click.argument("smiles")
@click.option(
    "--logp",
    type=float,
    default=None,
    help="LogP value. If not provided, computed from SMILES via RDKit.",
)
@click.option(
    "--solubility",
    type=float,
    default=None,
    help="Aqueous solubility Sw (mg/mL). Required for Jmax calculation.",
)
@out_option
@name_option
@_overwrite_option
@output_options
@pass_state
def topical_cmd(
    state: AppState,
    smiles: str,
    logp: float | None,
    solubility: float | None,
    out: str | None,
    name: str | None,
    overwrite: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Predict skin permeability using the Potts-Guy (1992) model.

    Computes log Kp (skin permeability coefficient), log Ksc/w (stratum
    corneum/water partition coefficient), and optionally Jmax (maximum
    flux) from a SMILES string.

    The Potts-Guy equation:
      log Kp = -2.72 + 0.71 * logP - 0.0061 * MW

    Where Kp is in cm/hr.  This is the gold-standard QSPR for skin
    permeability (Potts & Guy, Pharm Res 1992;9:663-669).

    \b
    Outputs:
      {slug}.admet-topical.json       -- topical permeability prediction
      {slug}.admet-topical.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- Resolve logP ---
    if logp is not None:
        logp_used = logp
        logp_source = "user-provided"
    else:
        try:
            logp_used = _compute_logp_from_smiles(smiles)
            logp_source = "RDKit MolLogP"
        except DependencyError as e:
            raise Refusal(
                "RDKit is not available and --logp was not provided",
                detail="logP is required for the Potts-Guy equation",
                remedy="install RDKit or provide --logp explicitly",
            ) from e

    # --- Compute MW from SMILES ---
    mw = _compute_mw_from_smiles(smiles)

    # --- Parse SMILES for canonical form ---
    _mol, canonical, fragment_notes, _ = _parse_smiles(smiles)
    slug = _slug(name) if name else _slug(canonical)

    # --- Potts-Guy log Kp ---
    # Potts & Guy, Pharm Res 1992;9:663-669
    log_kp = -2.72 + 0.71 * logp_used - 0.0061 * mw
    log_kp = round(log_kp, 4)

    # --- Log Ksc/w (stratum corneum/water partition) ---
    # Potts-Guy companion equation
    log_kscw = 0.71 * logp_used - 0.061
    log_kscw = round(log_kscw, 4)

    # --- Kp in cm/hr ---
    kp_cm_hr = 10**log_kp

    # --- Jmax (maximum flux) ---
    jmax = None
    solubility_used = solubility
    if solubility is not None:
        if solubility <= 0:
            raise Refusal(
                f"--solubility must be positive, got {solubility}",
                remedy="provide aqueous solubility in mg/mL as a positive number",
            )
        # Jmax = Kp * Sw, convert Sw from mg/mL to ug/mL (* 1000)
        # then Jmax in ug/cm2/hr
        jmax = kp_cm_hr * (solubility * 1000)  # ug/cm2/hr
        jmax = round(jmax, 6)

    # --- Permeability classification (Potts-Guy thresholds) ---
    if log_kp > -1.0:
        permeability_class = "high"
    elif log_kp < -3.0:
        permeability_class = "low"
    else:
        permeability_class = "moderate"

    # --- Build output record ---
    record: dict[str, Any] = {
        "tool": "admet",
        "subcommand": "topical",
        "schema": "dde.admet-topical.v1",
        "canonical_smiles": canonical,
        "fragment_notes": fragment_notes,
        "log_kp": log_kp,
        "log_kscw": log_kscw,
        "kp_cm_hr": round(kp_cm_hr, 8),
        "logP_used": logp_used,
        "logP_source": logp_source,
        "mw": mw,
        "permeability_class": permeability_class,
        "classification_thresholds": {
            "high": "log_kp > -1.0",
            "moderate": "-3.0 <= log_kp <= -1.0",
            "low": "log_kp < -3.0",
        },
        "model": {
            "name": "Potts-Guy",
            "equation": "log Kp = -2.72 + 0.71 * logP - 0.0061 * MW",
            "citation": (
                "Potts & Guy, 'Predicting Skin Permeability', Pharm Res 1992;9:663-669"
            ),
        },
    }

    if jmax is not None:
        record["jmax_ug_cm2_hr"] = jmax
        record["solubility_used"] = solubility_used
    else:
        record["jmax_ug_cm2_hr"] = None
        record["solubility_used"] = None

    record_path = target_dir / f"{slug}.admet-topical.json"
    content = json.dumps(record, indent=2, allow_nan=False) + "\n"
    _safe_write_artifact(record_path, content, overwrite=overwrite)

    # --- Provenance sidecar ---
    sidecar = _build_sidecar("topical", smiles, canonical, fragment_notes)
    if name:
        sidecar.note("compound_name", name)
    sidecar.note("log_kp", log_kp)
    sidecar.note("log_kscw", log_kscw)
    sidecar.note("permeability_class", permeability_class)
    sidecar.note("logP_used", logp_used)
    sidecar.note("logP_source", logp_source)
    sidecar.note("mw", mw)

    # Mandatory relay: prediction_not_measurement (#84 cross-cutting principle)
    sidecar.warn(
        "Skin permeability (log Kp) is a Potts-Guy QSPR estimate, not "
        "measured ex-vivo or in-vivo permeability.",
        code="admet.prediction_not_measurement",
    )

    sidecar.add_output(record_path)
    meta_path = sidecar.write(target_dir / f"{slug}.admet-topical.meta.json")

    # --- Emit summary ---
    emit.data("canonical_smiles", canonical)
    emit.data("log_kp", log_kp)
    emit.data("permeability_class", permeability_class)
    emit.path(record_path, role="topical")
    emit.path(meta_path, role="sidecar")
    emit.line(f"{canonical}")
    emit.line(f"log Kp = {log_kp} ({permeability_class} permeability)")
    emit.line(f"log Ksc/w = {log_kscw}")
    emit.line(f"MW = {mw}, logP = {logp_used} ({logp_source})")
    if jmax is not None:
        emit.line(f"Jmax = {jmax} ug/cm2/hr (Sw = {solubility_used} mg/mL)")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2 — analyze
# ---------------------------------------------------------------------------


@admet.command("analyze")
@click.argument("smiles")
@from_option
@out_option
@name_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    smiles: str,
    from_dir: str | None,
    out: str | None,
    name: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply ADMET thresholds to stored predictions.

    Reads the ``.predict.json`` written by ``admet predict``, applies the
    ``admet-endpoints`` threshold set, and writes an ``.analysis.json``
    with a verdict plus mandatory relays.  This command never queries an
    endpoint — the phase-2 latch enforces that automatically.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slug(name) if name else _slug(smiles)

    # --- locate stored phase-1 artifact ---
    predict_path = source_dir / f"{slug}.predict.json"
    if not predict_path.is_file():
        raise ArtifactError(
            f"no ADMET predictions found for {smiles!r} under {source_dir}",
            detail=f"tried slug {slug!r}; if you passed a non-canonical SMILES, "
            "pass the canonical form printed by `dde admet predict`",
            remedy="run `dde admet predict` first, then pass the "
            "canonical SMILES it printed",
        )
    predict_doc = provenance.read_json(predict_path, "ADMET prediction record")
    canonical = predict_doc.get("canonical_smiles", smiles)
    endpoints = predict_doc.get("endpoints", {})

    # --- load thresholds ---
    thresh = load_thresholds(state, "admet-endpoints")

    # --- collect upstream relays from phase-1 sidecars ---
    relays: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for suffix in ("predict",):
        meta_candidate = source_dir / f"{slug}.{suffix}.meta.json"
        if meta_candidate.is_file():
            meta = provenance.read_json(meta_candidate, f"{suffix} sidecar")
            for r in meta.get("mandatory_relays", []) or []:
                if r["code"] not in seen_codes:
                    relays.append(r)
                    seen_codes.add(r["code"])

    # --- evaluate each endpoint against thresholds ---
    endpoint_results: dict[str, dict[str, Any]] = {}
    liabilities: list[str] = []
    marginals: list[str] = []

    # 1. Metabolic stability — Gleeson 2008 rules via thresholds
    metab = endpoints.get("metabolic_stability", {})
    metab_descs = metab.get("contributing_descriptors", {})
    metab_logp = metab_descs.get("logp", 0)
    metab_aromatic = metab_descs.get("aromatic_rings", 0)

    if metab_logp > thresh.get("metabolic_logp_high") and metab_aromatic >= thresh.get(
        "metabolic_aromatic_rings_high"
    ):
        metab_class = "high"
    elif metab_logp <= thresh.get("metabolic_logp_low") and metab_logp >= 1:
        metab_class = "low"
    else:
        metab_class = "moderate"

    metab_result: dict[str, Any] = {
        "predicted_class": metab_class,
        "logp": metab_logp,
        "aromatic_rings": metab_aromatic,
    }
    if metab_class == "high":
        liabilities.append("metabolic_stability (high liability)")
        metab_result["status"] = "unacceptable"
    elif metab_class == "moderate":
        marginals.append("metabolic_stability (moderate)")
        metab_result["status"] = "marginal"
    else:
        metab_result["status"] = "acceptable"
    endpoint_results["metabolic_stability"] = metab_result

    # 2. CYP inhibition — multi-rule binary per isoform, no single threshold
    cyp = endpoints.get("cyp_inhibition", {})
    cyp_isoforms = cyp.get("isoforms", {})
    flagged_isoforms = [
        name for name, data in cyp_isoforms.items() if data.get("flagged")
    ]
    cyp_result: dict[str, Any] = {
        "flagged_isoforms": flagged_isoforms,
        "n_flagged": len(flagged_isoforms),
    }
    if len(flagged_isoforms) >= 2:
        liabilities.append(f"cyp_inhibition ({', '.join(flagged_isoforms)} flagged)")
        cyp_result["status"] = "unacceptable"
    elif len(flagged_isoforms) == 1:
        marginals.append(f"cyp_inhibition ({flagged_isoforms[0]} flagged)")
        cyp_result["status"] = "marginal"
    else:
        cyp_result["status"] = "acceptable"
    endpoint_results["cyp_inhibition"] = cyp_result

    # 3. Permeability — Egan 2000 egg model via thresholds
    perm = endpoints.get("permeability", {})
    perm_descs = perm.get("contributing_descriptors", {})
    perm_tpsa = perm_descs.get("tpsa", 0)
    perm_logp = perm_descs.get("logp", 0)
    perm_mw = perm_descs.get("mw", 0)

    if perm_mw > thresh.get("permeability_mw_max"):
        perm_class = "low"
    elif (
        perm_tpsa <= thresh.get("permeability_tpsa_high")
        and perm_logp >= thresh.get("permeability_logp_min")
        and perm_logp <= thresh.get("permeability_logp_max")
    ):
        perm_class = "high"
    elif perm_tpsa > thresh.get("permeability_tpsa_low"):
        perm_class = "low"
    else:
        perm_class = "moderate"

    perm_result: dict[str, Any] = {
        "predicted_class": perm_class,
        "tpsa": perm_tpsa,
        "logp": perm_logp,
        "mw": perm_mw,
    }
    if perm_class == "low":
        liabilities.append("permeability (low)")
        perm_result["status"] = "unacceptable"
    elif perm_class == "moderate":
        marginals.append("permeability (moderate)")
        perm_result["status"] = "marginal"
    else:
        perm_result["status"] = "acceptable"
    endpoint_results["permeability"] = perm_result

    # 4. hERG liability — Aronov 2005 pharmacophore via thresholds
    herg = endpoints.get("herg_liability", {})
    herg_descs = herg.get("contributing_descriptors", {})
    herg_basic_n = herg_descs.get("has_basic_nitrogen", False)
    herg_logp = herg_descs.get("logp", 0)
    herg_aromatic = herg_descs.get("aromatic_rings", 0)

    herg_flagged = (
        herg_basic_n
        and herg_logp > thresh.get("herg_logp_min")
        and herg_aromatic >= thresh.get("herg_min_aromatic_rings")
    )
    herg_features = herg.get("triggering_features", [])
    herg_result: dict[str, Any] = {
        "flagged": herg_flagged,
        "has_basic_nitrogen": herg_basic_n,
        "logp": herg_logp,
        "aromatic_rings": herg_aromatic,
    }
    if herg_flagged:
        liabilities.append("herg_liability (structural features flagged)")
        herg_result["status"] = "unacceptable"
        herg_result["triggering_features"] = herg_features
    else:
        herg_result["status"] = "acceptable"
    endpoint_results["herg_liability"] = herg_result

    # 5. Solubility — ESOL classification via thresholds
    sol = endpoints.get("solubility", {})
    sol_logs = sol.get("predicted_logS")

    if sol_logs is not None and sol_logs > thresh.get("solubility_high"):
        sol_class = "high"
    elif sol_logs is not None and sol_logs >= thresh.get("solubility_moderate"):
        sol_class = "moderate"
    elif sol_logs is not None and sol_logs >= thresh.get("solubility_low"):
        sol_class = "low"
    else:
        sol_class = "insoluble"

    sol_result: dict[str, Any] = {
        "predicted_class": sol_class,
        "predicted_logS": sol_logs,
    }
    if sol_class == "insoluble":
        liabilities.append(f"solubility (insoluble, LogS = {sol_logs})")
        sol_result["status"] = "unacceptable"
    elif sol_class == "low":
        marginals.append(f"solubility (low, LogS = {sol_logs})")
        sol_result["status"] = "marginal"
    elif sol_class == "moderate":
        sol_result["status"] = "acceptable"
    else:
        sol_result["status"] = "acceptable"
    endpoint_results["solubility"] = sol_result

    # --- determine verdict ---
    if liabilities:
        verdict = "liabilities-identified"
        statement = (
            f"{canonical} has {len(liabilities)} ADMET liability(ies): "
            f"{'; '.join(liabilities)}."
        )
    elif marginals:
        verdict = "flagged"
        statement = (
            f"{canonical} has {len(marginals)} marginal ADMET endpoint(s): "
            f"{'; '.join(marginals)}. No critical liabilities identified."
        )
    else:
        verdict = "developable"
        statement = (
            f"{canonical} passes all predicted ADMET endpoints. "
            "All endpoints are in acceptable range."
        )

    # --- conditional relay: prediction_not_measurement ---
    # Fires only when verdict is "developable" — the over-read risk is
    # highest when the profile looks clean.
    if verdict == "developable":
        relays.append(
            provenance.relay(
                "admet.prediction_not_measurement",
                f"{canonical} has a clean predicted ADMET profile, but every "
                "number here is a rule-based prediction from molecular "
                "descriptors, not an in vitro measurement. A clean predicted "
                "ADMET profile does not substitute for in vitro ADMET studies "
                "— it identifies which studies to prioritize, not which to skip.",
            )
        )

    # --- conditional relay: herg_structural_flag ---
    # Fires when hERG structural features are detected.
    if herg_flagged:
        features_text = (
            "; ".join(herg_features) if herg_features else "structural features"
        )
        relays.append(
            provenance.relay(
                "admet.herg_structural_flag",
                f"hERG flag triggered on {canonical} by: {features_text}. "
                "This is a pharmacophore-based prediction, not a measured IC50 "
                "or patch-clamp result. Rule-based hERG prediction has a "
                "documented false-negative rate: absence of this flag is not "
                "evidence of hERG safety.",
            )
        )

    # --- relay dispositions: make conditionality legible (#114) ---
    # An agent reading the output must see an explicit negative for
    # conditional relays that did not fire, not silence.  Without this,
    # "correctly excluded by conditionality" is indistinguishable from
    # "should have fired but didn't" — the gap WO-010's retro identified.
    relay_dispositions: list[dict[str, str]] = []

    if verdict == "developable":
        relay_dispositions.append(
            {
                "code": "admet.prediction_not_measurement",
                "status": "emitted",
                "condition": "verdict is developable",
            }
        )
    else:
        relay_dispositions.append(
            {
                "code": "admet.prediction_not_measurement",
                "status": "not_applicable",
                "condition": "verdict is developable",
                "reason": (
                    f"verdict is {verdict}; the over-read risk this relay guards "
                    "against — treating predictions as measurements — is highest "
                    "when the profile looks clean, which this one does not"
                ),
            }
        )

    if herg_flagged:
        relay_dispositions.append(
            {
                "code": "admet.herg_structural_flag",
                "status": "emitted",
                "condition": "hERG structural features detected",
            }
        )
    else:
        relay_dispositions.append(
            {
                "code": "admet.herg_structural_flag",
                "status": "not_applicable",
                "condition": "hERG structural features detected",
                "reason": "no hERG pharmacophore features matched",
            }
        )

    metrics: dict[str, Any] = {
        "canonical_smiles": canonical,
        "endpoint_results": endpoint_results,
        "n_liabilities": len(liabilities),
        "n_marginals": len(marginals),
        "liabilities": liabilities,
        "marginals": marginals,
    }
    assessment: dict[str, Any] = {
        "verdict": verdict,
        "statement": statement,
        "relay_dispositions": relay_dispositions,
    }

    analysis_path = target_dir / f"{slug}.analysis.json"
    provenance.write_analysis(
        analysis_path,
        source=state.project().relative(predict_path),
        threshold_set=thresh.tag,
        thresholds_applied=thresh.applied(),
        metrics=metrics,
        assessment=assessment,
        threshold_sources=thresh.sources(),
        threshold_provenance=thresh.provenance,
        unresolved=thresh.unresolved(),
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.line(f"{canonical}  [threshold_set {thresh.tag}]")
    emit.line(f"{verdict}: {statement}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    for disp in relay_dispositions:
        if disp["status"] == "not_applicable":
            emit.line(
                f"relay {disp['code']}: not applicable "
                f"(fires when {disp['condition']}; {disp['reason']})"
            )
    emit.path(analysis_path, role="analysis")
    emit.flush()
