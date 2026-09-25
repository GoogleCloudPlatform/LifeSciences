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

"""`dde mpo` -- multiparameter optimization scoring.

Reads analysis artifacts from other tools (admet predict, docking analyze,
etc.), applies configurable parameter weights, and produces a ranked
compound list with normalized scores.

This is a Phase 1 command: it computes and records scores with full
provenance.  It does not judge -- that belongs to a downstream reviewer.
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
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import provenance
from ..core.errors import ArtifactError, UsageError
from ..core.output import Emitter, warn

ARTIFACT_CLASS = "mpo"

# Threshold for warning about small min-max ranges.  When a metric's
# observed range is less than this fraction of the maximum absolute value,
# we warn that min-max normalization may exaggerate small differences.
_SMALL_RANGE_FRACTION = 0.10

# Below this cohort size we suggest --scoring absolute.
_SMALL_COHORT_N = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Normalize text to a safe slug: lowercase, alphanumeric + hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-") or "unknown"


def _flatten_list_of_dicts(
    key: str,
    records: list[dict[str, Any]],
    *,
    identity_field: str | None = None,
    value_field: str | None = None,
) -> dict[str, float]:
    """Project a list of dicts into flat keyed numeric values.

    For each dict in *records*, picks an identity string and a numeric
    value, producing ``{key}.{slug(identity)}: value``.

    *identity_field* / *value_field* override automatic detection; when
    ``None`` the first string field and first numeric field are used.
    """
    flat: dict[str, float] = {}
    for record in records:
        if not isinstance(record, dict):
            continue

        # Resolve identity: configurable or first string field.
        ident: str | None = None
        if identity_field and identity_field in record:
            raw = record[identity_field]
            if isinstance(raw, str):
                ident = raw
        if ident is None:
            for v in record.values():
                if isinstance(v, str):
                    ident = v
                    break
        if ident is None:
            continue  # cannot identify this record

        # Resolve value: configurable or first numeric field.
        num: float | None = None
        if value_field and value_field in record:
            raw = record[value_field]
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                num = float(raw)
        if num is None:
            for v in record.values():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    num = float(v)
                    break
        if num is None:
            continue  # no numeric value

        flat_key = f"{key}.{_slugify(ident)}"
        flat[flat_key] = num
    return flat


def _extract_metrics(doc: dict[str, Any]) -> dict[str, Any]:
    """Extract flat metric values from an analysis JSON.

    Looks in ``metrics``, ``assessment``, ``endpoints``, and
    ``molecular_descriptors`` dicts, flattening one level of nesting.
    Numeric leaf values are kept; everything else is skipped.

    Lists of dicts (e.g. selectivity ratios) are projected into flat
    keys using the first string field as identity and the first numeric
    field as value.
    """
    flat: dict[str, Any] = {}

    for top_key in ("metrics", "assessment", "endpoints", "molecular_descriptors"):
        section = doc.get(top_key, {})
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if key in flat and flat[key] != value:
                    click.echo(
                        f"warning: metric {key!r} appears at multiple paths; "
                        f"using value from {top_key}.{key}",
                        err=True,
                    )
                flat[key] = value
            elif isinstance(value, list):
                # List of dicts: flatten using identity/value projection.
                if value and isinstance(value[0], dict):
                    projected = _flatten_list_of_dicts(key, value)
                    for pk, pv in projected.items():
                        if pk in flat and flat[pk] != pv:
                            click.echo(
                                f"warning: metric {pk!r} appears at multiple paths; "
                                f"using value from {top_key}.{key}",
                                err=True,
                            )
                        flat[pk] = pv
            elif isinstance(value, dict):
                # One level of nesting: e.g. endpoints.metabolic_stability
                for sub_key, sub_val in value.items():
                    if isinstance(sub_val, (int, float)) and not isinstance(
                        sub_val, bool
                    ):
                        flat_key = f"{key}.{sub_key}" if sub_key != key else key
                        if flat_key in flat and flat[flat_key] != sub_val:
                            click.echo(
                                f"warning: metric {flat_key!r} appears at multiple paths; "
                                f"using value from {top_key}.{key}.{sub_key}",
                                err=True,
                            )
                        flat[flat_key] = sub_val
                    elif isinstance(sub_val, list):
                        # Nested list of dicts under a dict key.
                        if sub_val and isinstance(sub_val[0], dict):
                            projected = _flatten_list_of_dicts(
                                f"{key}.{sub_key}",
                                sub_val,
                            )
                            for pk, pv in projected.items():
                                if pk in flat and flat[pk] != pv:
                                    click.echo(
                                        f"warning: metric {pk!r} appears at multiple paths; "
                                        f"using value from {top_key}.{key}.{sub_key}",
                                        err=True,
                                    )
                                flat[pk] = pv
                    elif isinstance(sub_val, dict):
                        # Two levels: e.g. endpoints.metabolic_stability.contributing_descriptors
                        for deep_key, deep_val in sub_val.items():
                            if isinstance(deep_val, (int, float)) and not isinstance(
                                deep_val, bool
                            ):
                                if deep_key in flat and flat[deep_key] != deep_val:
                                    click.echo(
                                        f"warning: metric {deep_key!r} appears at multiple paths; "
                                        f"using value from {top_key}.{key}.{sub_key}.{deep_key}",
                                        err=True,
                                    )
                                flat[deep_key] = deep_val

    # Also check top-level numeric fields (e.g. best_score)
    for key, value in doc.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if key not in flat:
                flat[key] = value

    return flat


def _compound_id(path: Path, doc: dict[str, Any]) -> str:
    """Derive a compound identifier from the JSON or filename."""
    for field in ("compound", "canonical_smiles", "ligand"):
        val = doc.get(field)
        if val and isinstance(val, str):
            return val
        # Check inside metrics
        metrics = doc.get("metrics", {})
        if isinstance(metrics, dict):
            val = metrics.get(field)
            if val and isinstance(val, str):
                return val
    return path.stem


def _load_weights(weights_path: Path | None) -> dict[str, dict[str, Any]] | None:
    """Load and validate a weights JSON file.

    Returns None when no weights file is provided (equal-weight mode).
    """
    if weights_path is None:
        return None

    doc = provenance.read_json(weights_path, "weights file")
    parameters = doc.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise UsageError(
            "weights file must contain a non-empty 'parameters' dict",
            detail=f"got: {type(parameters).__name__}",
            remedy="see the --weights format in `dde mpo score --help`",
        )

    for name, spec in parameters.items():
        if not isinstance(spec, dict):
            raise UsageError(
                f"weight spec for {name!r} must be a dict",
                remedy="each parameter needs at least a 'weight' key",
            )
        if "weight" not in spec:
            raise UsageError(
                f"weight spec for {name!r} is missing 'weight'",
                remedy="add a numeric 'weight' value",
            )
        direction = spec.get("direction", "minimize")
        if direction not in ("minimize", "maximize"):
            raise UsageError(
                f"direction for {name!r} must be 'minimize' or 'maximize', got {direction!r}",
            )

    return parameters


def _normalize_scores(
    compound_values: dict[str, float],
    direction: str,
) -> tuple[dict[str, float], dict[str, float]]:
    """Min-max normalize values to 0-1, respecting direction.

    For "minimize", lower raw values get higher normalized scores.
    For "maximize", higher raw values get higher normalized scores.

    Returns (normalized_dict, range_info) where range_info has
    ``min``, ``max``, ``range`` keys.
    """
    if not compound_values:
        return {}, {}

    values = list(compound_values.values())
    lo = min(values)
    hi = max(values)
    span = hi - lo

    range_info: dict[str, float] = {"min": lo, "max": hi, "range": span}

    normalized: dict[str, float] = {}
    for compound, raw in compound_values.items():
        if span == 0:
            # All values identical -- score 0.5
            normalized[compound] = 0.5
        elif direction == "minimize":
            # Lower is better: invert so lower raw -> higher normalized
            normalized[compound] = round((hi - raw) / span, 6)
        else:
            # Higher is better
            normalized[compound] = round((raw - lo) / span, 6)

    return normalized, range_info


def _normalize_absolute(
    compound_values: dict[str, float],
    direction: str,
    low: float,
    high: float,
) -> dict[str, float]:
    """Absolute desirability normalization to 0-1.

    Score = linear interpolation clamped to [0, 1]:
      (value - low) / (high - low)

    For "minimize", the scale is inverted: values at or below *low* score
    1.0 and values at or above *high* score 0.0.
    """
    if not compound_values:
        return {}

    span = high - low
    if span == 0:
        return dict.fromkeys(compound_values, 0.5)

    normalized: dict[str, float] = {}
    for compound, raw in compound_values.items():
        score = (raw - low) / span
        score = max(0.0, min(1.0, score))
        if direction == "minimize":
            score = 1.0 - score
        normalized[compound] = round(score, 6)

    return normalized


def _parse_bounds(bounds_list: tuple[str, ...]) -> dict[str, tuple[float, float]]:
    """Parse ``--bounds METRIC:LOW:HIGH`` arguments into a dict."""
    result: dict[str, tuple[float, float]] = {}
    for spec in bounds_list:
        parts = spec.split(":")
        if len(parts) != 3:
            raise UsageError(
                f"invalid --bounds format: {spec!r}",
                detail="expected METRIC:LOW:HIGH (e.g. pIC50:6.0:8.0)",
            )
        metric = parts[0]
        try:
            low = float(parts[1])
            high = float(parts[2])
        except ValueError as e:
            raise UsageError(
                f"--bounds values must be numeric: {spec!r}",
                detail="LOW and HIGH must be valid floating-point numbers",
            ) from e
        if low >= high:
            raise UsageError(
                f"--bounds LOW must be less than HIGH: {spec!r}",
                detail=f"got LOW={low}, HIGH={high}",
            )
        result[metric] = (low, high)
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def mpo() -> None:
    """Multiparameter optimization scoring."""


@mpo.command("score")
@click.argument("analyses", nargs=-1, required=True, type=click.Path())
@click.option(
    "--weights",
    "weights_file",
    default=None,
    type=click.Path(),
    help="JSON file defining parameter weights and optional thresholds. "
    "If omitted, all parameters found across inputs are weighted equally.",
)
@click.option(
    "--scoring",
    "scoring_mode",
    default="minmax",
    type=click.Choice(["minmax", "absolute"]),
    help="Normalization method: 'minmax' (default, cohort-relative) or "
    "'absolute' (desirability bounds, cohort-independent).",
)
@click.option(
    "--bounds",
    "bounds_specs",
    multiple=True,
    help="Absolute desirability bounds per metric: METRIC:LOW:HIGH. "
    "Required for each metric when --scoring=absolute. "
    "E.g. --bounds pIC50:6.0:8.0 --bounds SA_score:1.0:5.0",
)
@out_option
@output_options
@pass_state
def score_cmd(
    state: AppState,
    analyses: tuple[str, ...],
    weights_file: str | None,
    scoring_mode: str,
    bounds_specs: tuple[str, ...],
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Score and rank compounds across multiple analysis dimensions.

    Reads one or more analysis JSON files (from ``admet predict``,
    ``docking analyze``, etc.), applies configurable parameter weights,
    normalizes scores, and produces a ranked compound list.

    Without ``--weights``, all parameters found across the input
    analyses are weighted equally -- useful for quick comparisons.

    Supports two normalization modes:

    \b
      minmax    (default) Min-max normalization. Scores are relative to
                the cohort and NOT comparable across runs.
      absolute  Desirability-function normalization with explicit bounds.
                Each candidate's score is independent of cohort composition.

    Outputs under ``raw/mpo/``:

    \b
      mpo-{hash}.mpo.json       -- scoring results with per-compound detail
      mpo-{hash}.mpo.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- parse absolute bounds ---
    abs_bounds: dict[str, tuple[float, float]] = {}
    if bounds_specs:
        abs_bounds = _parse_bounds(bounds_specs)

    if scoring_mode == "absolute" and not abs_bounds:
        raise UsageError(
            "--scoring=absolute requires at least one --bounds specification",
            remedy="provide --bounds METRIC:LOW:HIGH for each metric to score",
        )

    # --- resolve inputs ---
    analysis_paths: list[Path] = []
    for arg in analyses:
        analysis_paths.append(resolve_artifact(state, arg, "analysis"))

    weights_path: Path | None = None
    if weights_file is not None:
        weights_path = resolve_artifact(state, weights_file, "weights file")

    weight_specs = _load_weights(weights_path)

    # --- read analysis files and extract per-compound metrics ---
    compounds: dict[str, dict[str, float]] = {}  # compound -> {param: raw_value}
    source_files: list[str] = []

    for apath in analysis_paths:
        doc = provenance.read_json(apath, "analysis")
        source_files.append(apath.name)
        cid = _compound_id(apath, doc)
        metrics = _extract_metrics(doc)

        if cid not in compounds:
            compounds[cid] = {}
        compounds[cid].update(metrics)

    if not compounds:
        raise ArtifactError(
            "no compounds found in the provided analysis files",
            remedy="check that the analysis files contain metrics or assessment data",
        )

    # --- determine parameter set and weights ---
    if weight_specs is not None:
        param_names = list(weight_specs.keys())
    else:
        # Equal-weight mode: collect all numeric parameters across compounds
        all_params: set[str] = set()
        for metrics in compounds.values():
            all_params.update(metrics.keys())
        param_names = sorted(all_params)
        if not param_names:
            raise ArtifactError(
                "no numeric parameters found in the provided analyses",
                remedy="check that analysis files contain numeric metrics",
            )
        equal_weight = round(1.0 / len(param_names), 6)
        weight_specs = {
            name: {"weight": equal_weight, "direction": "maximize"}
            for name in param_names
        }

    # --- normalize weights to sum to 1.0 ---
    total_weight = sum(spec["weight"] for spec in weight_specs.values())
    if total_weight <= 0:
        raise UsageError(
            "total weight must be positive",
            detail=f"sum of weights is {total_weight}",
        )
    for spec in weight_specs.values():
        spec["_normalized_weight"] = spec["weight"] / total_weight

    # --- collect raw values per parameter across all compounds ---
    param_raw: dict[str, dict[str, float]] = {}  # param -> {compound: value}
    missing_warnings: list[str] = []

    for param in param_names:
        param_raw[param] = {}
        for cid, metrics in compounds.items():
            if param in metrics:
                param_raw[param][cid] = metrics[param]
            else:
                missing_warnings.append(
                    f"compound {cid!r} missing parameter {param!r}; scoring 0"
                )

    for w in missing_warnings:
        warn(w)

    # --- normalize each parameter ---
    param_normalized: dict[str, dict[str, float]] = {}
    param_ranges: dict[str, dict[str, float]] = {}  # for min-max reporting
    relays: list[dict[str, str]] = []

    if scoring_mode == "absolute":
        # Absolute desirability normalization.
        for param in param_names:
            if param not in abs_bounds:
                raise UsageError(
                    f"--scoring=absolute requires --bounds for metric {param!r}",
                    detail=f"provide --bounds {param}:LOW:HIGH",
                )
            direction = weight_specs[param].get("direction", "maximize")
            low, high = abs_bounds[param]
            param_normalized[param] = _normalize_absolute(
                param_raw[param],
                direction,
                low,
                high,
            )
    else:
        # Min-max normalization.
        n_compounds = len(compounds)

        # Small-N warning.
        if n_compounds < _SMALL_COHORT_N:
            warn(
                f"Scoring {n_compounds} candidates with min-max normalization. "
                "Consider --scoring absolute for small cohorts."
            )

        for param in param_names:
            direction = weight_specs[param].get("direction", "maximize")
            normalized, range_info = _normalize_scores(param_raw[param], direction)
            param_normalized[param] = normalized
            param_ranges[param] = range_info

            # Small-range warning.
            if range_info:
                span = range_info["range"]
                hi = range_info["max"]
                max_abs = max(abs(hi), abs(range_info["min"])) if hi != 0 else 0
                if max_abs > 0 and span < _SMALL_RANGE_FRACTION * max_abs:
                    warn(
                        f"Metric {param!r} has range {span:.4g} across "
                        f"{n_compounds} candidates — min-max normalization "
                        "may exaggerate small differences."
                    )

        # Fire the mandatory relay for min-max.
        relays.append(
            provenance.relay(
                "mpo.minmax_cohort_relative",
                "MPO scores were computed with min-max normalization. "
                "Scores are relative to this specific cohort and are NOT "
                "comparable across different scoring runs or candidate sets.",
            )
        )

    # --- compute weighted totals ---
    compound_scores: dict[str, dict[str, Any]] = {}
    for cid in compounds:
        details: dict[str, Any] = {}
        total = 0.0
        for param in param_names:
            raw = param_raw[param].get(cid)
            norm = param_normalized[param].get(cid, 0.0)
            w = weight_specs[param]["_normalized_weight"]
            weighted = round(norm * w, 6)
            total += weighted
            details[param] = {
                "raw": raw,
                "normalized": norm,
                "weight": round(w, 6),
                "weighted": weighted,
            }

        compound_scores[cid] = {
            "compound": cid,
            "parameters": details,
            "total_score": round(total, 6),
        }

    # --- apply thresholds ---
    for cid, scores in compound_scores.items():
        threshold_results: dict[str, dict[str, Any]] = {}
        all_pass = True
        for param in param_names:
            threshold = weight_specs[param].get("threshold")
            if threshold is None:
                continue
            raw = param_raw[param].get(cid)
            if raw is None:
                threshold_results[param] = {
                    "threshold": threshold,
                    "pass": False,
                    "reason": "missing",
                }
                all_pass = False
                continue
            direction = weight_specs[param].get("direction", "maximize")
            if direction == "minimize":
                passed = raw <= threshold
            else:
                passed = raw >= threshold
            threshold_results[param] = {
                "threshold": threshold,
                "value": raw,
                "pass": passed,
            }
            if not passed:
                all_pass = False

        scores["thresholds"] = threshold_results
        scores["passes_all_thresholds"] = all_pass

    # --- rank by total score descending ---
    ranked = sorted(
        compound_scores.values(), key=lambda x: x["total_score"], reverse=True
    )
    for i, entry in enumerate(ranked, start=1):
        entry["rank"] = i

    # --- build output hash from inputs for filename ---
    h = hashlib.sha256()
    for apath in sorted(analysis_paths, key=lambda p: p.name):
        h.update(apath.name.encode())
    if weights_path:
        h.update(weights_path.name.encode())
    file_hash = h.hexdigest()[:12]

    # --- write .mpo.json artifact ---
    weights_summary = {
        name: {
            "weight": spec["_normalized_weight"],
            "direction": spec.get("direction", "maximize"),
            **({"threshold": spec["threshold"]} if "threshold" in spec else {}),
        }
        for name, spec in weight_specs.items()
    }

    mpo_record: dict[str, Any] = {
        "tool": "mpo",
        "subcommand": "score",
        "source_analyses": source_files,
        "weights": weights_summary,
        "normalization": scoring_mode,
        "n_compounds": len(ranked),
        "n_parameters": len(param_names),
        "compounds": ranked,
    }

    if scoring_mode == "absolute":
        mpo_record["bounds"] = {
            metric: {"low": low, "high": high}
            for metric, (low, high) in abs_bounds.items()
        }
    if param_ranges:
        mpo_record["metric_ranges"] = param_ranges
    if relays:
        mpo_record["mandatory_relays"] = relays

    result_path = target_dir / f"mpo-{file_hash}.mpo.json"
    result_path.write_text(
        json.dumps(mpo_record, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    # --- provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="mpo",
        subcommand="score",
        endpoint=None,
        parameters={
            "analyses": source_files,
            "weights_file": weights_path.name if weights_path else None,
            "n_parameters": len(param_names),
            "scoring_mode": scoring_mode,
        },
    )
    for apath in analysis_paths:
        sidecar.note(f"input_sha256_{apath.name}", provenance.sha256_file(apath))
    if weights_path:
        sidecar.note("weights_sha256", provenance.sha256_file(weights_path))
    sidecar.note("normalization_method", scoring_mode)
    sidecar.note("weights_applied", weights_summary)
    sidecar.note("parameter_names", param_names)
    if scoring_mode == "absolute":
        sidecar.note(
            "bounds", {m: {"low": lo, "high": hi} for m, (lo, hi) in abs_bounds.items()}
        )
    for r in relays:
        sidecar.warn(r["message"], code=r["code"])
    sidecar.add_output(result_path)

    meta_path = sidecar.write(target_dir / f"mpo-{file_hash}.mpo.meta.json")

    # --- emit min-max range table ---
    if scoring_mode == "minmax" and param_ranges:
        emit.line(f"{'Metric':<24} {'Range':>10} {'Min':>10} {'Max':>10}")
        for param in param_names:
            ri = param_ranges.get(param, {})
            if ri:
                emit.line(
                    f"{param:<24} {ri['range']:>10.4g} "
                    f"{ri['min']:>10.4g} {ri['max']:>10.4g}"
                )
        emit.line("")

    # --- emit ranked table ---
    for entry in ranked:
        failures = [
            p for p, t in entry.get("thresholds", {}).items() if not t.get("pass", True)
        ]
        fail_str = f"  FAIL: {', '.join(failures)}" if failures else ""
        emit.line(
            f"#{entry['rank']}  {entry['compound']}  "
            f"score={entry['total_score']:.4f}"
            f"{fail_str}"
        )

    # --- emit relays ---
    for r in relays:
        emit.line(f"relay {r['code']}: {r['message']}")

    emit.data("compounds", ranked)
    emit.data("weights", weights_summary)
    if relays:
        emit.data("mandatory_relays", relays)
    emit.path(result_path, role="mpo")
    emit.path(meta_path, role="sidecar")
    emit.flush()
