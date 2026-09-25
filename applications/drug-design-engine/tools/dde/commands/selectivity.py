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

"""`dde selectivity` — selectivity panel analysis.

Phase 1 (no judgment):
  compare  Parse a canonical selectivity panel JSON file (schema
           ``dde.selectivity-panel.v1``), validate required fields,
           compute selectivity ratios (off-target / primary-target), and
           write a Layer 0 artifact plus a ``.meta.json`` sidecar to
           ``raw/assays/``.

Phase 2 (offline, applies thresholds):
  analyze  Read stored selectivity data, apply the ``selectivity-margins``
           threshold set.  hERG margin is classified against the ICH S7B
           ≥30-fold guideline; all other off-target margins report
           UNRESOLVED.  Produces ``.analysis.json`` with mandatory relays.

The two phases enforce the same contract as every other tool: phase 1
writes numbers, phase 2 writes verdicts. The ``analyze`` subcommand is
genuinely offline — the phase-2 latch enforces this automatically for
any command named ``analyze*``.
"""

from __future__ import annotations

import json
import math
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
from ..core.errors import ArtifactError, Refusal, SchemaError, ThresholdError
from ..core.output import Emitter

TOOL = "selectivity"
ARTIFACT_CLASS = "assays"

_VALID_ACTIVITY_TYPES = ("IC50", "Ki")

MAX_OFF_TARGETS = 10_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(text: str | None, fallback: str) -> str:
    """Derive a filesystem-safe slug from text."""
    if not text:
        return fallback
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:60] or fallback


def _validate_target(target: dict, label: str) -> list[str]:
    """Validate a target entry (primary or off-target). Returns problems."""
    problems: list[str] = []
    for field in ("name", "activity_type", "activity_value", "activity_unit"):
        if field not in target:
            problems.append(f"{label}: missing required field: {field}")
        elif target[field] is None:
            problems.append(f"{label}: null value for required field: {field}")

    if "activity_type" in target and target["activity_type"] is not None:
        if target["activity_type"] not in _VALID_ACTIVITY_TYPES:
            problems.append(
                f"{label}: activity_type must be one of {_VALID_ACTIVITY_TYPES}, "
                f"got {target['activity_type']!r}"
            )

    if "activity_value" in target and target["activity_value"] is not None:
        val = target["activity_value"]
        if not isinstance(val, (int, float)):
            problems.append(
                f"{label}: activity_value must be numeric, got {type(val).__name__}"
            )
        elif isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            problems.append(f"{label}: activity_value must be finite")
        elif val <= 0:
            problems.append(f"{label}: activity_value must be positive, got {val}")

    if "activity_unit" in target and target["activity_unit"] is not None:
        if target["activity_unit"] != "nM":
            problems.append(
                f"{label}: activity_unit must be 'nM', got {target['activity_unit']!r}"
            )

    return problems


def _validate_panel(doc: dict) -> list[str]:
    """Validate the full selectivity panel document. Returns problems."""
    problems: list[str] = []

    if "compound_id" not in doc:
        problems.append("missing required field: compound_id")
    elif not isinstance(doc["compound_id"], str) or not doc["compound_id"]:
        problems.append("compound_id must be a non-empty string")

    if "primary_target" not in doc:
        problems.append("missing required field: primary_target")
    elif not isinstance(doc["primary_target"], dict):
        problems.append(
            f"primary_target must be an object, got {type(doc['primary_target']).__name__}"
        )
    else:
        problems.extend(_validate_target(doc["primary_target"], "primary_target"))

    if "panel_complete" in doc and not isinstance(doc["panel_complete"], bool):
        problems.append(
            f"panel_complete must be a boolean, got {type(doc['panel_complete']).__name__} "
            f"({doc['panel_complete']!r})"
        )

    if "off_targets" not in doc:
        problems.append("missing required field: off_targets")
    elif not isinstance(doc["off_targets"], list):
        problems.append(
            f"off_targets must be an array, got {type(doc['off_targets']).__name__}"
        )
    elif len(doc["off_targets"]) == 0:
        problems.append("off_targets must contain at least one entry")
    elif len(doc["off_targets"]) > MAX_OFF_TARGETS:
        problems.append(
            f"off_targets exceeds maximum of {MAX_OFF_TARGETS:,} entries "
            f"(got {len(doc['off_targets']):,})"
        )
    else:
        for i, ot in enumerate(doc["off_targets"]):
            if not isinstance(ot, dict):
                problems.append(
                    f"off_targets[{i}]: expected an object, got {type(ot).__name__}"
                )
                continue
            problems.extend(_validate_target(ot, f"off_targets[{i}]"))

    return problems


def _check_measure_consistency(doc: dict) -> None:
    """Refuse if the panel mixes IC50 and Ki across targets.

    Computing IC50_off / Ki_primary (or vice versa) produces a misleading
    ratio without Cheng-Prusoff correction.  Either all values must be the
    same measure type, or the tool refuses.
    """
    primary_type = doc["primary_target"]["activity_type"]
    mixed = []
    for i, ot in enumerate(doc["off_targets"]):
        if ot["activity_type"] != primary_type:
            mixed.append(f"off_targets[{i}] ({ot['name']}): {ot['activity_type']}")

    if mixed:
        raise Refusal(
            f"selectivity panel mixes activity types: primary_target uses "
            f"{primary_type} but some off-targets use a different measure",
            detail=(
                "Mixed measures found: " + "; ".join(mixed) + ". "
                "Computing a ratio between IC50 and Ki values is misleading "
                "without Cheng-Prusoff correction. All values in the panel "
                "must use the same activity_type."
            ),
            remedy=(
                "ensure all targets (primary and off-target) use the same "
                "activity_type (all IC50 or all Ki), or apply Cheng-Prusoff "
                "correction upstream and express all values as Ki"
            ),
        )


def _compute_ratios(doc: dict) -> list[dict[str, Any]]:
    """Compute selectivity ratios: off_target_value / primary_value.

    A higher ratio means better selectivity (the compound is less potent
    against the off-target).
    """
    primary_value = doc["primary_target"]["activity_value"]
    primary_name = doc["primary_target"]["name"]
    activity_type = doc["primary_target"]["activity_type"]

    ratios: list[dict[str, Any]] = []
    for ot in doc["off_targets"]:
        ratio = ot["activity_value"] / primary_value
        ratios.append(
            {
                "off_target": ot["name"],
                "off_target_value": ot["activity_value"],
                "off_target_unit": ot["activity_unit"],
                "primary_target": primary_name,
                "primary_value": primary_value,
                "primary_unit": doc["primary_target"]["activity_unit"],
                "activity_type": activity_type,
                "selectivity_ratio": round(ratio, 4),
            }
        )
    return ratios


def _normalise(doc: dict, source: Path) -> dict:
    """Build the stable Layer 0 record from a canonical selectivity panel."""
    ratios = _compute_ratios(doc)
    panel_complete = doc.get("panel_complete", False)

    off_target_names = [ot["name"] for ot in doc["off_targets"]]

    return {
        "schema": "dde.selectivity-panel.v1",
        "source_file": source.name,
        "compound_id": doc["compound_id"],
        "primary_target": {
            "name": doc["primary_target"]["name"],
            "activity_type": doc["primary_target"]["activity_type"],
            "activity_value": doc["primary_target"]["activity_value"],
            "activity_unit": doc["primary_target"]["activity_unit"],
        },
        "off_targets": [
            {
                "name": ot["name"],
                "activity_type": ot["activity_type"],
                "activity_value": ot["activity_value"],
                "activity_unit": ot["activity_unit"],
            }
            for ot in doc["off_targets"]
        ],
        "panel_complete": panel_complete,
        "selectivity_ratios": ratios,
        "summary": {
            "n_off_targets": len(doc["off_targets"]),
            "off_target_names": off_target_names,
            "activity_type": doc["primary_target"]["activity_type"],
            "panel_complete": panel_complete,
        },
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def selectivity() -> None:
    """Selectivity panel: compare and analyze."""


@selectivity.command()
@click.argument("panel_file")
@out_option
@output_options
@pass_state
def compare(
    state: AppState,
    panel_file: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Compute selectivity ratios from a canonical selectivity panel JSON file.

    PANEL_FILE is a JSON file matching the ``dde.selectivity-panel.v1``
    schema.  The file must contain a primary target and one or more
    off-targets, all using the same activity_type (IC50 or Ki).

    Selectivity ratios are computed as off_target_value / primary_value.
    A higher ratio means better selectivity (less potent against the
    off-target).

    Refuses mixed IC50/Ki panels — computing a ratio between different
    measure types is misleading without Cheng-Prusoff correction.
    """
    source = Path(panel_file).expanduser()
    project = state.project()
    if not source.is_absolute():
        candidates = [Path.cwd() / source, project.root / source]
        source = next((c for c in candidates if c.is_file()), candidates[0])
    if not source.is_file():
        raise ArtifactError(
            f"selectivity panel file not found: {panel_file}",
            detail=f"looked in {Path.cwd()} and {project.root}",
            remedy="pass an absolute path to the canonical selectivity panel JSON file",
        )
    source = source.resolve()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    doc = provenance.read_json(source, "selectivity panel")
    if not isinstance(doc, dict):
        raise SchemaError(
            "selectivity panel file must be a JSON object",
            detail=f"got {type(doc).__name__}",
        )

    # Schema version check — non-canonical input is a refusal.
    if doc.get("schema") != "dde.selectivity-panel.v1":
        raise Refusal(
            "selectivity panel file does not match canonical schema "
            "dde.selectivity-panel.v1",
            detail=f"got schema {doc.get('schema')!r}",
            remedy=(
                "ensure the input file has "
                '\'"schema": "dde.selectivity-panel.v1"\' at the top level'
            ),
        )

    # Validate all fields.
    problems = _validate_panel(doc)
    if problems:
        raise SchemaError(
            f"{len(problems)} validation error(s) in selectivity panel",
            detail="; ".join(problems[:10])
            + (f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""),
            remedy="fix the input data and re-run compare",
        )

    # Refuse mixed IC50/Ki measures.
    _check_measure_consistency(doc)

    record = _normalise(doc, source)

    # Deterministic filename from compound_id and primary_target name.
    compound_slug = _slug(doc["compound_id"], "compound")
    target_slug = _slug(doc["primary_target"]["name"], "target")
    name = f"{compound_slug}-{target_slug}-selectivity"

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="compare",
        endpoint=None,
        parameters={"panel_file": str(source)},
    )

    artifact_path = target_dir / f"{name}.selectivity.json"
    artifact_path.write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    sidecar.note("source_sha256", provenance.sha256_file(source))
    sidecar.note("compound_id", doc["compound_id"])
    sidecar.note("primary_target", doc["primary_target"]["name"])
    sidecar.note("n_off_targets", len(doc["off_targets"]))
    sidecar.note("activity_type", doc["primary_target"]["activity_type"])
    sidecar.note("panel_complete", doc.get("panel_complete", False))

    sidecar.add_output(artifact_path)
    meta = sidecar.write(target_dir / f"{name}.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("compound_id", doc["compound_id"])
    emit.data("primary_target", doc["primary_target"]["name"])
    emit.data("n_off_targets", len(doc["off_targets"]))
    emit.data("activity_type", doc["primary_target"]["activity_type"])
    emit.data("selectivity_ratios", record["selectivity_ratios"])
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(artifact_path), "selectivity")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


@selectivity.command()
@click.argument("artifact")
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    artifact: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply selectivity margin thresholds to stored selectivity data.

    ARTIFACT is the ``.selectivity.json`` produced by ``compare``.  Reads
    stored ratios from disk, applies the ``selectivity-margins`` threshold
    set, and produces an ``.analysis.json`` with per-off-target verdicts.

    hERG margin is classified against the ICH S7B ≥30-fold guideline.
    All other off-target margins are UNRESOLVED — there are no universally
    citable conventions for off-target selectivity margins.
    """
    project = state.project()
    path = resolve_artifact(state, artifact, "selectivity data")
    record = provenance.read_json(path, "selectivity data")

    if record.get("schema") != "dde.selectivity-panel.v1":
        raise SchemaError(
            f"{path.name} is not a normalised selectivity artifact",
            detail=f"expected schema dde.selectivity-panel.v1, got {record.get('schema')!r}",
            remedy="run `dde selectivity compare` on the raw panel file first",
        )

    thresholds = load_thresholds(state, "selectivity-margins")

    ratios = record.get("selectivity_ratios", [])
    panel_complete = record.get("panel_complete", False)
    compound_id = record.get("compound_id", "unknown")
    activity_type = record.get("primary_target", {}).get("activity_type", "unknown")

    # --- Collect upstream relays from the compare sidecar ---
    relays: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    meta_path = path.with_name(path.name.replace(".selectivity.json", ".meta.json"))
    if meta_path.is_file():
        compare_meta = provenance.read_json(meta_path, "provenance sidecar")
        for item in compare_meta.get("mandatory_relays", []) or []:
            if item.get("code") not in seen_codes:
                relays.append(item)
                seen_codes.add(item["code"])

    # --- Claim-triggered relay: ratio_not_affinity ---
    # Fires on every analysis — a selectivity ratio computed from two
    # IC50 values inherits whatever caveats apply to IC50 as a measure
    # of affinity.
    code = "selectivity.ratio_not_affinity"
    if code not in seen_codes:
        relays.append(
            provenance.relay(
                code,
                f"Selectivity ratios for {compound_id} are computed from "
                f"{activity_type} values. {activity_type} is assay-dependent "
                "and not a thermodynamic binding constant; a ratio from two "
                f"{activity_type} values each with typical assay variability "
                "does not carry the same confidence as a ratio from Kd values.",
            )
        )
        seen_codes.add(code)

    # --- Defect-triggered relay: panel_incomplete ---
    # Fires when panel_complete is not exactly boolean True.  Using
    # ``is not True`` instead of ``not panel_complete`` prevents a
    # non-empty string like "false" from suppressing the relay.
    if panel_complete is not True:
        code = "selectivity.panel_incomplete"
        off_target_names = [r["off_target"] for r in ratios]
        if code not in seen_codes:
            relays.append(
                provenance.relay(
                    code,
                    f"Selectivity panel for {compound_id} is incomplete. "
                    f"Off-targets tested: {', '.join(off_target_names)}. "
                    "Do not generalise the selectivity claim beyond these "
                    "targets — a narrow panel cannot support a broad "
                    "selectivity statement.",
                )
            )
            seen_codes.add(code)

    # --- Per-off-target margin classification ---
    per_target: list[dict[str, Any]] = []
    for ratio_entry in ratios:
        off_target = ratio_entry["off_target"]
        ratio_value = ratio_entry["selectivity_ratio"]

        target_result: dict[str, Any] = {
            "off_target": off_target,
            "selectivity_ratio": ratio_value,
            "activity_type": ratio_entry["activity_type"],
        }

        # hERG gets the ICH S7B threshold; everything else is UNRESOLVED.
        if off_target.upper() == "HERG":
            try:
                herg_margin = thresholds.get("herg_margin")
                herg_marginal = thresholds.get("herg_marginal")
                if ratio_value >= herg_margin:
                    classification = "adequate"
                elif ratio_value >= herg_marginal:
                    classification = "marginal"
                else:
                    classification = "insufficient"

                target_result["threshold"] = herg_margin
                target_result["threshold_source"] = (
                    "ICH S7B, 'The Non-Clinical Evaluation of the Potential "
                    "for Delayed Ventricular Repolarization (QT Interval "
                    "Prolongation) by Human Pharmaceuticals' (adopted 2005): "
                    "recommends a ≥30-fold safety margin between the "
                    "therapeutic free plasma concentration and the IC50 for "
                    "hERG channel inhibition. herg_marginal is a local "
                    "operational boundary (herg_margin / 3), not a cited "
                    "value — see pocket's borderline_dscore for precedent"
                )
                target_result["classification"] = classification
            except ThresholdError:
                target_result["classification"] = "UNRESOLVED"
                target_result["advisory"] = "hERG margin threshold is UNRESOLVED"
        else:
            target_result["classification"] = "UNRESOLVED"
            target_result["advisory"] = (
                f"No universally citable convention exists for {off_target} "
                "selectivity margins; ratio reported without classification"
            )

        per_target.append(target_result)

    # --- Build metrics and assessment ---
    metrics: dict[str, Any] = {
        "compound_id": compound_id,
        "primary_target": record.get("primary_target", {}).get("name"),
        "activity_type": activity_type,
        "n_off_targets": len(ratios),
        "panel_complete": panel_complete,
        "ratios": per_target,
    }

    n_classified = sum(
        1 for t in per_target if t["classification"] not in ("UNRESOLVED",)
    )
    n_adequate = sum(1 for t in per_target if t.get("classification") == "adequate")
    n_marginal = sum(1 for t in per_target if t.get("classification") == "marginal")
    n_insufficient = sum(
        1 for t in per_target if t.get("classification") == "insufficient"
    )

    assessment: dict[str, Any] = {
        "n_classified": n_classified,
        "n_adequate": n_adequate,
        "n_marginal": n_marginal,
        "n_insufficient": n_insufficient,
        "n_unresolved": len(per_target) - n_classified,
        "targets": per_target,
    }

    # Derive record type from schema for consistent filename pattern
    record_type = provenance.record_type_from_schema(record.get("schema", ""))
    stem = path.name.replace(".selectivity.json", "")
    new_analysis_name = f"{stem}.{record_type}.analysis.json"

    # Backward compatibility: warn if an old-format analysis file exists
    old_analysis_name = path.name.replace(".selectivity.json", ".analysis.json")
    if old_analysis_name != new_analysis_name:
        old_candidate = path.parent / old_analysis_name
        if old_candidate.exists():
            from ..core.output import warn

            warn(
                f"old-format analysis exists: {old_analysis_name}; "
                f"new analysis uses: {new_analysis_name}"
            )

    analysis_path = beside_or_out(state, path, new_analysis_name, out)
    provenance.write_analysis(
        analysis_path,
        source=path,
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        unresolved=thresholds.unresolved() or None,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)

    emit.line(f"Compound: {compound_id}")
    emit.line(f"Primary target: {record.get('primary_target', {}).get('name')}")
    emit.line(f"Off-targets analyzed: {len(per_target)}")
    if n_classified:
        emit.line(
            f"Classified: {n_classified} (adequate: {n_adequate}, "
            f"marginal: {n_marginal}, insufficient: {n_insufficient})"
        )
    emit.line(f"Unresolved: {len(per_target) - n_classified}")
    for t in per_target:
        ratio_str = f"{t['selectivity_ratio']:.1f}x"
        emit.line(f"  {t['off_target']}: {ratio_str} [{t['classification']}]")
    for record_relay in relays:
        emit.line(f"relay {record_relay['code']}: {record_relay['message']}")
    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()
