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

"""`dde mmp` -- matched molecular pair analysis with RDKit BRICS.

Phase 1 (no judgment):
  fragment   BRICS-decompose a SMILES string, recording the full
             fragmentation (core scaffold + R-group positions).
  pairs      Parse a series JSON file (schema ``dde.mmp-series.v1``),
             fragment each compound, identify matched molecular pairs
             (same core, single R-group transformation), and compute
             property deltas.

Phase 2 (offline, applies thresholds):
  analyze    Read stored ``.mmp-pairs.json``, apply ``mmp-cliffs``
             threshold set, detect property cliffs, and produce an
             analysis record with mandatory relays.

A matched molecular pair is two molecules that share the same BRICS
core scaffold but differ at exactly one R-group position.  Property
deltas across a transformation are correlations in the dataset -- not
causal mechanisms.  See relay ``mmp.cliff_not_causation``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import provenance
from ..core.errors import (
    ArtifactError,
    DependencyError,
    Refusal,
    SchemaError,
    ThresholdError,
)
from ..core.output import Emitter

ARTIFACT_CLASS = "compounds"


# ---------------------------------------------------------------------------
# RDKit lazy import
# ---------------------------------------------------------------------------


def _require_rdkit():
    """Lazy-import RDKit, raising DependencyError if absent."""
    try:
        import rdkit
        from rdkit import Chem
        from rdkit.Chem import BRICS

        return Chem, BRICS, rdkit
    except ImportError as e:
        raise DependencyError(
            "RDKit is not installed",
            detail="matched molecular pair analysis requires RDKit (Chem, BRICS)",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi)",
        ) from e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(smiles: str) -> str:
    """Derive a deterministic, filesystem-safe slug from canonical SMILES."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", smiles).strip("-").lower()
    if not slug or len(slug) < 3:
        slug = "mol-" + hashlib.sha256(smiles.encode()).hexdigest()[:16]
    return slug[:80]


def _parse_smiles(smiles_input: str) -> tuple[Any, str]:
    """Parse a SMILES string and canonicalize it.

    Returns ``(mol, canonical_smiles)``.
    Raises :class:`Refusal` on unparseable input.
    """
    Chem, _, _ = _require_rdkit()

    mol = Chem.MolFromSmiles(smiles_input)
    if mol is None:
        raise Refusal(
            f"unparseable SMILES: {smiles_input!r}",
            detail="RDKit could not interpret this as a valid molecular structure",
            remedy="check the SMILES syntax; common issues include unclosed "
            "brackets, invalid atom symbols, and unbalanced parentheses",
        )
    canonical = Chem.MolToSmiles(mol)
    return mol, canonical


def _brics_fragment(mol: Any) -> dict[str, Any]:
    """Full BRICS decomposition of a molecule.

    Returns a dict with:
      - ``fragments``: the set of BRICS fragment SMILES (all cuts applied)
      - ``single_cuts``: list of dicts, one per BRICS bond, each with
        ``core`` and ``r_group`` SMILES from cutting at that single bond

    All valid BRICS cuts are reported -- the tool never silently picks one
    (tool-design-guidance section 8, "never rank and pick").
    """
    Chem, BRICS, _ = _require_rdkit()

    # Full decomposition (all BRICS bonds cut simultaneously)
    fragments = sorted(BRICS.BRICSDecompose(mol))

    # Single-bond cuts for MMP core/R-group identification
    bonds = list(BRICS.FindBRICSBonds(mol))
    single_cuts: list[dict[str, str]] = []

    for (begin_idx, end_idx), (begin_type, end_type) in bonds:
        bond = mol.GetBondBetweenAtoms(begin_idx, end_idx)
        if bond is None:
            continue
        bond_idx = bond.GetIdx()
        frag_mol = Chem.FragmentOnBonds(
            mol,
            [bond_idx],
            addDummies=True,
            dummyLabels=[(int(begin_type), int(end_type))],
        )
        pieces = Chem.MolToSmiles(frag_mol).split(".")
        if len(pieces) != 2:
            continue

        # The larger fragment (by heavy atom count in the SMILES) is the core.
        # When equal, report both orderings would be misleading; keep
        # alphabetical order for determinism.
        p0_mol = Chem.MolFromSmiles(pieces[0])
        p1_mol = Chem.MolFromSmiles(pieces[1])
        if p0_mol is None or p1_mol is None:
            continue
        p0_heavy = p0_mol.GetNumHeavyAtoms()
        p1_heavy = p1_mol.GetNumHeavyAtoms()

        if p0_heavy >= p1_heavy:
            core, r_group = pieces[0], pieces[1]
        else:
            core, r_group = pieces[1], pieces[0]

        single_cuts.append(
            {
                "core": core,
                "r_group": r_group,
                "bond_atoms": [begin_idx, end_idx],
                "bond_types": [begin_type, end_type],
            }
        )

    return {
        "fragments": fragments,
        "n_fragments": len(fragments),
        "single_cuts": single_cuts,
        "n_brics_bonds": len(bonds),
    }


def _find_matched_pairs(
    compounds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Identify matched molecular pairs across a set of compounds.

    Two compounds are a matched pair when a single-cut BRICS decomposition
    of both yields the same core but different R-groups.  Property deltas
    are computed for every shared property.

    Returns a list of pair records.
    """
    Chem, _BRICS, _ = _require_rdkit()

    # Build an index: core_smiles -> [(compound_id, r_group, properties, smiles)]
    core_index: dict[str, list[dict[str, Any]]] = {}

    for cpd in compounds:
        mol = Chem.MolFromSmiles(cpd["smiles"])
        if mol is None:
            continue

        canonical = Chem.MolToSmiles(mol)
        decomp = _brics_fragment(mol)

        for cut in decomp["single_cuts"]:
            core = cut["core"]
            entry = {
                "compound_id": cpd["compound_id"],
                "smiles": canonical,
                "r_group": cut["r_group"],
                "properties": cpd.get("properties", {}),
            }
            core_index.setdefault(core, []).append(entry)

    # For each core, generate all pairs where R-groups differ
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()  # (core, id_lo, id_hi, rg_lo, rg_hi) dedup

    for core, entries in core_index.items():
        if len(entries) < 2:
            continue
        for i, a in enumerate(entries):
            for b in entries[i + 1 :]:
                if a["compound_id"] == b["compound_id"]:
                    continue
                if a["r_group"] == b["r_group"]:
                    continue

                # Deduplicate: same pair for same core and R-group positions
                id_lo = min(a["compound_id"], b["compound_id"])
                id_hi = max(a["compound_id"], b["compound_id"])
                if a["compound_id"] == id_lo:
                    rg_lo, rg_hi = a["r_group"], b["r_group"]
                else:
                    rg_lo, rg_hi = b["r_group"], a["r_group"]
                pair_key = (core, id_lo, id_hi, rg_lo, rg_hi)
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                # Compute property deltas
                shared_props = set(a["properties"]) & set(b["properties"])
                deltas: dict[str, float] = {}
                for prop in sorted(shared_props):
                    val_a = a["properties"][prop]
                    val_b = b["properties"][prop]
                    if isinstance(val_a, (int, float)) and isinstance(
                        val_b, (int, float)
                    ):
                        deltas[prop] = round(val_b - val_a, 6)

                pairs.append(
                    {
                        "core": core,
                        "compound_a": {
                            "compound_id": a["compound_id"],
                            "smiles": a["smiles"],
                            "r_group": a["r_group"],
                        },
                        "compound_b": {
                            "compound_id": b["compound_id"],
                            "smiles": b["smiles"],
                            "r_group": b["r_group"],
                        },
                        "transformation": f"{a['r_group']} -> {b['r_group']}",
                        "property_deltas": deltas,
                    }
                )

    return pairs


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def mmp() -> None:
    """Matched molecular pair analysis (RDKit BRICS)."""


# ---------------------------------------------------------------------------
# Phase 1 -- fragment
# ---------------------------------------------------------------------------


@mmp.command("fragment")
@click.argument("smiles")
@out_option
@output_options
@pass_state
def fragment_cmd(
    state: AppState,
    smiles: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """BRICS-decompose a SMILES string.

    Records the molecule's full BRICS decomposition (all fragment SMILES)
    and every single-cut core/R-group pair.  All valid BRICS cuts are
    reported -- the tool never silently picks one.

    Phase 1: the decomposition is recorded, not judged.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    _, _, rdkit_mod = _require_rdkit()
    mol, canonical = _parse_smiles(smiles)
    slug = _slug(canonical)

    decomposition = _brics_fragment(mol)

    sidecar = provenance.Sidecar(
        tool="mmp",
        subcommand="fragment",
        endpoint=None,
        parameters={"input_smiles": smiles, "canonical_smiles": canonical},
    )
    sidecar.note("rdkit_version", rdkit_mod.__version__)

    record: dict[str, Any] = {
        "tool": "mmp",
        "subcommand": "fragment",
        "input_smiles": smiles,
        "canonical_smiles": canonical,
        "decomposition": decomposition,
        "rdkit_version": rdkit_mod.__version__,
    }

    record_path = target_dir / f"{slug}.mmp-fragment.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(record_path)
    meta_path = sidecar.write(target_dir / f"{slug}.mmp-fragment.meta.json")

    emit.data("canonical_smiles", canonical)
    emit.data("n_fragments", decomposition["n_fragments"])
    emit.data("n_brics_bonds", decomposition["n_brics_bonds"])
    emit.data("fragments", decomposition["fragments"])
    emit.path(record_path, role="fragment")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 -- pairs
# ---------------------------------------------------------------------------


@mmp.command("pairs")
@click.argument("series_file")
@out_option
@output_options
@pass_state
def pairs_cmd(
    state: AppState,
    series_file: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Identify matched molecular pairs from a series JSON file.

    SERIES_FILE is a JSON file matching the ``dde.mmp-series.v1``
    schema.  Each compound record must have ``compound_id``, ``smiles``,
    and at least one property in a ``properties`` dict.

    Fragments each compound using BRICS, identifies matched pairs (same
    core scaffold, single R-group transformation), and computes property
    deltas for each transformation.

    Phase 1: pairs and deltas are recorded, not judged.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    _, _, rdkit_mod = _require_rdkit()

    # Resolve the input file
    source = Path(series_file).expanduser()
    if not source.is_absolute():
        candidates = [Path.cwd() / source, project.root / source]
        source = next((c for c in candidates if c.is_file()), candidates[0])
    if not source.is_file():
        raise ArtifactError(
            f"series file not found: {series_file}",
            detail=f"looked in {Path.cwd()} and {project.root}",
            remedy="pass an absolute path to the series JSON file",
        )
    source = source.resolve()

    doc = provenance.read_json(source, "MMP series data")
    if not isinstance(doc, dict):
        raise SchemaError(
            "series file must be a JSON object",
            detail=f"got {type(doc).__name__}",
        )

    # Schema version check -- non-canonical input is a refusal.
    if doc.get("schema") != "dde.mmp-series.v1":
        raise Refusal(
            "series file does not match canonical schema dde.mmp-series.v1",
            detail=f"got schema {doc.get('schema')!r}",
            remedy='ensure the input file has \'"schema": "dde.mmp-series.v1"\' '
            "at the top level",
        )

    # Validate compound records
    compounds = doc.get("compounds", [])
    if not isinstance(compounds, list) or not compounds:
        raise SchemaError(
            "series file must contain a non-empty 'compounds' array",
            remedy="add compound records with compound_id, smiles, and properties",
        )

    MAX_COMPOUNDS = 10_000
    if len(compounds) > MAX_COMPOUNDS:
        raise SchemaError(
            f"series file exceeds maximum of {MAX_COMPOUNDS:,} compounds "
            f"(got {len(compounds):,})",
            remedy="split the input into multiple series files",
        )

    problems: list[str] = []
    valid_compounds: list[dict[str, Any]] = []
    for i, cpd in enumerate(compounds):
        if not isinstance(cpd, dict):
            problems.append(
                f"compounds[{i}]: expected a dict, got {type(cpd).__name__}"
            )
            continue
        if "compound_id" not in cpd:
            problems.append(f"compounds[{i}]: missing required field 'compound_id'")
        if "smiles" not in cpd:
            problems.append(f"compounds[{i}]: missing required field 'smiles'")
        props = cpd.get("properties")
        if not isinstance(props, dict) or not props:
            problems.append(f"compounds[{i}]: 'properties' must be a non-empty dict")
            continue
        valid_compounds.append(cpd)

    if problems:
        raise SchemaError(
            f"{len(problems)} validation error(s) in series file",
            detail="; ".join(problems[:10])
            + (f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""),
            remedy="fix the input data and re-run pairs",
        )

    # Find matched pairs
    pairs = _find_matched_pairs(valid_compounds)

    # Build sidecar
    sidecar = provenance.Sidecar(
        tool="mmp",
        subcommand="pairs",
        endpoint=None,
        parameters={"series_file": str(source)},
    )
    sidecar.note("rdkit_version", rdkit_mod.__version__)
    sidecar.note("source_sha256", provenance.sha256_file(source))
    sidecar.note("n_compounds", len(valid_compounds))
    sidecar.note("n_pairs", len(pairs))

    # Group pairs by transformation for summary
    transformations: dict[str, int] = {}
    for pair in pairs:
        t = pair["transformation"]
        transformations[t] = transformations.get(t, 0) + 1

    # Build the output record
    name = source.stem.replace(".mmp-series", "").replace(".json", "")
    if not name or len(name) < 3:
        name = "series-" + hashlib.sha256(str(source).encode()).hexdigest()[:12]

    record: dict[str, Any] = {
        "tool": "mmp",
        "subcommand": "pairs",
        "schema": "dde.mmp-pairs.v1",
        "source_file": source.name,
        "rdkit_version": rdkit_mod.__version__,
        "summary": {
            "n_compounds": len(valid_compounds),
            "n_pairs": len(pairs),
            "n_transformations": len(transformations),
            "transformations": transformations,
        },
        "pairs": pairs,
    }

    record_path = target_dir / f"{name}.mmp-pairs.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(record_path)
    meta_path = sidecar.write(target_dir / f"{name}.mmp-pairs.meta.json")

    emit.data("n_compounds", len(valid_compounds))
    emit.data("n_pairs", len(pairs))
    emit.data("n_transformations", len(transformations))
    emit.path(project.relative(record_path), role="pairs")
    emit.path(project.relative(meta_path), role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2 -- analyze
# ---------------------------------------------------------------------------


@mmp.command("analyze")
@click.argument("artifact")
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    artifact: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply property-cliff detection to stored MMP pairs.

    ARTIFACT is the ``.mmp-pairs.json`` produced by ``pairs``.  Reads
    stored data from disk, applies the ``mmp-cliffs`` threshold set, and
    produces an ``.analysis.json`` flagging transformations where a small
    structural change (single R-group) correlates with a large property
    change.

    A property cliff is a correlation in a dataset, not a demonstrated
    causal mechanism.  The ``mmp.cliff_not_causation`` relay fires when
    cliffs are detected.
    """
    project = state.project()
    source = resolve_artifact(state, artifact, "MMP pairs record")
    record = provenance.read_json(source, "MMP pairs record")

    if record.get("schema") != "dde.mmp-pairs.v1":
        raise Refusal(
            f"{source.name} does not match schema dde.mmp-pairs.v1",
            detail=f"got schema {record.get('schema')!r}",
            remedy="run `dde mmp pairs` on the series file first",
        )

    thresholds = load_thresholds(state, "mmp-cliffs")
    min_pair_count = thresholds.get("min_pair_count")

    pairs = record.get("pairs", [])

    # Collect upstream relays from the pairs sidecar
    relays: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    meta_path = source.with_name(
        source.name.replace(".mmp-pairs.json", ".mmp-pairs.meta.json")
    )
    if meta_path.is_file():
        pairs_meta = provenance.read_json(meta_path, "provenance sidecar")
        for item in pairs_meta.get("mandatory_relays", []) or []:
            if item.get("code") not in seen_codes:
                relays.append(item)
                seen_codes.add(item["code"])

    # Group pairs by transformation and detect cliffs
    # A "transformation" groups all pairs that share the same R-group change
    transform_groups: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        t = pair["transformation"]
        transform_groups.setdefault(t, []).append(pair)

    cliff_results: list[dict[str, Any]] = []
    small_pair_count_fired = False

    # cliff_absolute_delta is a per-property dict: {"pic50": 1.0, ...}.
    # Properties in the dict are evaluated for cliffs; properties not in
    # the dict produce an explicit "not evaluated" advisory.  The dict
    # may still be UNRESOLVED (ThresholdError) if the entire threshold
    # was removed or reset to UNRESOLVED via program config.
    cliff_thresholds: dict[str, float] = {}
    unresolved_cliff_thresholds: list[str] = []

    try:
        raw_cliff = thresholds.get("cliff_absolute_delta")
        if isinstance(raw_cliff, dict):
            # Normalize keys to lowercase for case-insensitive property matching.
            try:
                cliff_thresholds = {k.lower(): float(v) for k, v in raw_cliff.items()}
            except (ValueError, TypeError) as exc:
                raise ThresholdError(
                    "cliff_absolute_delta values must be numeric",
                    detail=f"could not convert to float: {exc}",
                    remedy=(
                        "each entry in cliff_absolute_delta must be a number "
                        "(e.g. cliff_absolute_delta: {pic50: 1.0})"
                    ),
                ) from exc
        else:
            # A scalar override was set — this cannot be applied uniformly
            # across heterogeneous properties.  Treat as misconfigured.
            unresolved_cliff_thresholds.append("cliff_absolute_delta")
    except ThresholdError:
        unresolved_cliff_thresholds.append("cliff_absolute_delta")

    for transformation, group_pairs in sorted(transform_groups.items()):
        n_pairs = len(group_pairs)
        result: dict[str, Any] = {
            "transformation": transformation,
            "n_pairs": n_pairs,
            "cliffs": [],
            "advisories": [],
        }

        # Check pair count threshold
        if n_pairs < min_pair_count:
            result["advisories"].append(
                f"Only {n_pairs} pair(s) for this transformation "
                f"(minimum {min_pair_count}); trend cannot be reported "
                "with confidence."
            )
            result["low_pair_count"] = True
            if not small_pair_count_fired:
                relays.append(
                    provenance.relay(
                        "mmp.small_pair_count",
                        f"Transformation '{transformation}' has {n_pairs} "
                        f"matched pair(s), below the minimum of {min_pair_count} "
                        "required for a reliable SAR conclusion.",
                    )
                )
                seen_codes.add("mmp.small_pair_count")
                small_pair_count_fired = True
        else:
            result["low_pair_count"] = False

        # Detect property cliffs within this transformation group.
        # cliff_thresholds is a per-property dict; properties not in the
        # dict are explicitly recorded as not evaluated.
        # Deduplicate on lowercase key so case variants (e.g. "LogD" vs
        # "logd") produce one advisory, keeping the first-seen spelling.
        not_evaluated_keys: set[str] = set()
        not_evaluated_display: dict[str, str] = {}  # lower -> first-seen name
        for pair in group_pairs:
            for prop, delta in pair.get("property_deltas", {}).items():
                prop_key = prop.lower()
                threshold = cliff_thresholds.get(prop_key)

                if threshold is None:
                    # No configured threshold for this property.
                    if prop_key not in not_evaluated_keys:
                        not_evaluated_keys.add(prop_key)
                        not_evaluated_display[prop_key] = prop
                    continue

                if abs(delta) >= threshold:
                    result["cliffs"].append(
                        {
                            "pair": {
                                "compound_a": pair["compound_a"]["compound_id"],
                                "compound_b": pair["compound_b"]["compound_id"],
                            },
                            "property": prop,
                            "delta": delta,
                            "reasons": [
                                f"|delta| {abs(delta):.4f} >= "
                                f"cliff_absolute_delta[{prop_key}] {threshold}"
                            ],
                        }
                    )

        if not_evaluated_display:
            display_names = sorted(not_evaluated_display.values())
            result["not_evaluated_properties"] = display_names
            configured = sorted(cliff_thresholds) if cliff_thresholds else []
            for prop in display_names:
                result["advisories"].append(
                    f"Property '{prop}' not evaluated for cliffs: no threshold "
                    f"configured for this property in cliff_absolute_delta. "
                    f"Configured properties: "
                    f"{', '.join(configured) if configured else 'none'}."
                )

        cliff_results.append(result)

    # Fire cliff_not_causation if any cliffs were detected
    total_cliffs = sum(len(r["cliffs"]) for r in cliff_results)
    if total_cliffs > 0 and "mmp.cliff_not_causation" not in seen_codes:
        relays.append(
            provenance.relay(
                "mmp.cliff_not_causation",
                f"{total_cliffs} property cliff(s) detected across "
                f"{len(transform_groups)} transformation(s). A large property "
                "change across a single R-group transformation is a correlation "
                "in this dataset, not evidence of a causal mechanism.",
            )
        )
        seen_codes.add("mmp.cliff_not_causation")

    # Collect all not-evaluated properties across all transformations
    all_not_evaluated: set[str] = set()
    for r in cliff_results:
        all_not_evaluated.update(r.get("not_evaluated_properties", []))

    # Build metrics and assessment
    n_low_pair_count = sum(1 for r in cliff_results if r.get("low_pair_count"))
    metrics: dict[str, Any] = {
        "n_pairs_total": len(pairs),
        "n_transformations": len(transform_groups),
        "n_cliffs": total_cliffs,
        "n_low_pair_count_transformations": n_low_pair_count,
        "n_properties_not_evaluated": len(all_not_evaluated),
        "properties_not_evaluated": sorted(all_not_evaluated),
    }

    assessment: dict[str, Any] = {
        "cliff_results": cliff_results,
        "unresolved_cliff_thresholds": unresolved_cliff_thresholds,
    }

    if unresolved_cliff_thresholds:
        assessment["advisory"] = (
            f"Cliff threshold(s) {', '.join(unresolved_cliff_thresholds)} are "
            "UNRESOLVED. Set them in .dde/thresholds.yaml under 'mmp-cliffs' "
            "to enable full cliff detection."
        )

    if all_not_evaluated:
        configured = sorted(cliff_thresholds) if cliff_thresholds else []
        assessment["not_evaluated_advisory"] = (
            f"Property/properties {', '.join(sorted(all_not_evaluated))} appeared "
            f"in property_deltas but have no cliff threshold configured in "
            f"cliff_absolute_delta. Cliff detection was not performed for "
            f"{'this property' if len(all_not_evaluated) == 1 else 'these properties'}. "
            f"This is not 'no cliffs' — the question was not asked. Configured "
            f"properties: {', '.join(configured) if configured else 'none'}."
        )

    stem = source.stem.replace(".mmp-pairs", "")
    analysis_path = beside_or_out(state, source, f"{stem}.mmp-analysis.json", out)
    provenance.write_analysis(
        analysis_path,
        source=source,
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        unresolved=thresholds.unresolved(),
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("metrics", metrics)
    emit.data("assessment", assessment)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)

    emit.line(f"Pairs analyzed: {len(pairs)}")
    emit.line(f"Transformations: {len(transform_groups)}")
    emit.line(f"Property cliffs: {total_cliffs}")
    if n_low_pair_count:
        emit.line(f"Low pair count transformations: {n_low_pair_count}")
    if all_not_evaluated:
        emit.line(
            f"Properties not evaluated for cliffs (no threshold configured): "
            f"{', '.join(sorted(all_not_evaluated))}"
        )
    if unresolved_cliff_thresholds:
        emit.line(f"Unresolved: {', '.join(unresolved_cliff_thresholds)}")
    for r in relays:
        emit.line(f"relay {r['code']}: {r['message']}")
    emit.path(project.relative(analysis_path), role="analysis")
    emit.flush()
