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

"""`dde pk` -- in vivo PK analysis and human dose projection.

Phase A subcommands:

  ingest    Parse a canonical PK study JSON file (schema
            ``dde.pk-study.v1``) and store normalised study data
            with a provenance sidecar.

  nca       Non-compartmental analysis of ingested PK data: Cmax,
            Tmax, AUC (linear-log trapezoidal), t1/2, CL, Vd.
            Phase 1 record — computes parameters, judges nothing.

Phase B subcommands:

  scale     Allometric scaling from animal PK parameters to predicted
            human PK.  Uses published exponents (single species) or
            fitted log-log regression (multi-species).

  ddi       Static drug-drug interaction prediction from measured
            in vitro CYP inhibition data (FDA/EMA basic static model).

  analyze   Apply the ``pk-parameters`` threshold set to NCA, scaling,
            or DDI artifacts and produce a ``.analysis.json`` verdict.

BLQ (below limit of quantitation) handling is an explicit parameter
on ``pk nca``, never a silent default.  The ``pk ingest`` command
records the BLQ marker value but does not decide how BLQ points are
treated — that decision belongs to the analyst at NCA time.

Note: PBPK (physiologically based pharmacokinetic) modelling is a
documented future extension point and is not implemented here.  Only
allometric/inline scaling is available.
"""

from __future__ import annotations

import json
import math
import re
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
from ..core.errors import Refusal, SchemaError
from ..core.output import Emitter
from ..core.paths import is_safe_to_open
from ..core.schema_registry import suggest_match

ARTIFACT_CLASS = "pk"

# Recognised unit strings — anything else is refused as ambiguous.
VALID_TIME_UNITS = {"h", "min", "s"}
VALID_CONC_UNITS = {"ng/mL", "ug/mL", "mg/mL", "uM", "nM"}
VALID_ROUTES = {
    "iv",
    "oral",
    "sc",
    "im",
    "ip",
    "dermal",
    "topical",
    "inhaled",
    "ophthalmic",
    "intranasal",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_id(raw: str) -> str:
    """Make an ID safe for use in filenames, preventing path traversal."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw)).strip("-")
    if not safe:
        raise SchemaError(
            "study_id contains no usable characters after sanitization",
            remedy="use alphanumeric characters, hyphens, dots, or underscores",
        )
    return safe[:120]


def _validate_study(doc: dict[str, Any]) -> None:
    """Validate a pk-study document, raising on any problem."""
    # Schema tag
    if doc.get("schema") != "dde.pk-study.v1":
        raise Refusal(
            "unsupported or missing schema tag",
            detail=f"expected 'dde.pk-study.v1', got {doc.get('schema')!r}",
            remedy='ensure the input JSON contains \'"schema": "dde.pk-study.v1"\'',
        )

    # Required scalar fields
    _PK_STUDY_REQUIRED = (
        "species",
        "route",
        "dose_mg_kg",
        "time_units",
        "concentration_units",
    )
    for field in _PK_STUDY_REQUIRED:
        if field not in doc or doc[field] is None:
            raise SchemaError(
                f"required field {field!r} is missing or null",
                detail=f"all required fields for dde.pk-study.v1: "
                f"{['schema', 'study_id', *list(_PK_STUDY_REQUIRED)]}",
                remedy=f"add {field!r} to the input JSON",
            )

    # Unit validation — with fuzzy matching suggestions
    time_units = doc["time_units"]
    if time_units not in VALID_TIME_UNITS:
        hint = suggest_match(time_units, VALID_TIME_UNITS)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised time_units: {time_units!r}.{suggestion}",
            detail=f"accepted values: {sorted(VALID_TIME_UNITS)}",
            remedy="use one of: h, min, s",
        )

    conc_units = doc["concentration_units"]
    if conc_units not in VALID_CONC_UNITS:
        hint = suggest_match(conc_units, VALID_CONC_UNITS)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised concentration_units: {conc_units!r}.{suggestion}",
            detail=f"accepted values: {sorted(VALID_CONC_UNITS)}",
            remedy="use one of: ng/mL, ug/mL, mg/mL, uM, nM",
        )

    route = doc["route"]
    if route not in VALID_ROUTES:
        hint = suggest_match(route, VALID_ROUTES)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised route: {route!r}.{suggestion}",
            detail=f"accepted values: {sorted(VALID_ROUTES)}",
            remedy=f"use one of: {', '.join(sorted(VALID_ROUTES))}",
        )

    # dose_mg_kg positivity
    if not isinstance(doc["dose_mg_kg"], (int, float)) or doc["dose_mg_kg"] <= 0:
        raise SchemaError(
            f"dose_mg_kg must be a positive number, got {doc['dose_mg_kg']!r}",
            remedy="provide the administered dose in mg/kg as a positive number",
        )

    # Arrays
    time_points = doc.get("time_points")
    concentrations = doc.get("concentrations")

    if not isinstance(time_points, list) or not time_points:
        raise SchemaError(
            "time_points must be a non-empty array",
            remedy="provide at least 3 numeric time points",
        )
    if not isinstance(concentrations, list) or not concentrations:
        raise SchemaError(
            "concentrations must be a non-empty array",
            remedy="provide concentration values matching time_points",
        )
    if len(time_points) != len(concentrations):
        raise SchemaError(
            f"time_points ({len(time_points)}) and concentrations "
            f"({len(concentrations)}) must have equal length",
        )
    if len(time_points) < 3:
        raise SchemaError(
            f"at least 3 time points required for NCA, got {len(time_points)}",
            remedy="provide a concentration-time profile with >= 3 points",
        )

    # Numeric type validation on array elements and negative concentrations
    for i, (t, c) in enumerate(zip(time_points, concentrations, strict=False)):
        if not isinstance(t, (int, float)):
            raise SchemaError(
                f"time_points[{i}] must be a number, got {type(t).__name__}: {t!r}",
                remedy="replace string markers like '<LLOQ' with numeric values and "
                "use blq_value to mark BLQ concentrations",
            )
        if not isinstance(c, (int, float)):
            raise SchemaError(
                f"concentrations[{i}] must be a number, got {type(c).__name__}: {c!r}",
                remedy="replace string markers like '<LLOQ' with the numeric LOQ value "
                "and set blq_value to that value",
            )
        if c < 0:
            raise SchemaError(
                f"concentrations[{i}] must be non-negative, got {c}",
                detail="negative concentrations are pharmacokinetically nonsensical",
                remedy="check the input data for data entry errors",
            )

    # Monotonically increasing time points
    for i in range(1, len(time_points)):
        if time_points[i] <= time_points[i - 1]:
            raise SchemaError(
                f"time_points must be monotonically increasing; "
                f"violation at index {i}: {time_points[i - 1]} >= {time_points[i]}",
            )

    # BLQ value validation
    blq_value = doc.get("blq_value")
    if blq_value is not None:
        if not isinstance(blq_value, (int, float)) or blq_value <= 0:
            raise SchemaError(
                f"blq_value must be a positive number when specified, got {blq_value!r}",
                detail="blq_value=0.0 would match legitimate zero concentrations (e.g. pre-dose)",
                remedy="set blq_value to the assay's limit of quantitation (a positive number), "
                "or omit blq_value if no BLQ handling is needed",
            )


def _apply_blq(
    time_points: list[float],
    concentrations: list[float],
    blq_value: float | None,
    blq_method: str,
) -> tuple[list[float], list[float], int]:
    """Apply BLQ handling, returning (times, concs, n_blq_points).

    Methods:
      exclude   — remove BLQ points entirely
      zero      — replace BLQ concentrations with 0.0
      loq_half  — replace BLQ concentrations with blq_value / 2
    """
    if blq_value is None:
        return time_points, concentrations, 0

    n_blq = sum(1 for c in concentrations if c == blq_value)
    if n_blq == 0:
        return time_points, concentrations, 0

    if blq_method == "exclude":
        pairs = [
            (t, c)
            for t, c in zip(time_points, concentrations, strict=False)
            if c != blq_value
        ]
        if not pairs:
            raise Refusal(
                "all concentrations are BLQ — no data remains after exclusion",
                remedy="check the input data or choose a different BLQ method",
            )
        times, concs = zip(*pairs, strict=False)
        return list(times), list(concs), n_blq
    elif blq_method == "zero":
        concs = [0.0 if c == blq_value else c for c in concentrations]
        return time_points, concs, n_blq
    elif blq_method == "loq_half":
        half = blq_value / 2.0
        concs = [half if c == blq_value else c for c in concentrations]
        return time_points, concs, n_blq
    else:
        raise Refusal(
            f"unknown BLQ method: {blq_method!r}",
            detail="accepted methods: exclude, zero, loq_half",
        )


def _auc_linear_log_trapezoidal(times: list[float], concs: list[float]) -> float:
    """Compute AUC using the linear-log trapezoidal method.

    Linear trapezoidal when concentration is increasing;
    log trapezoidal when concentration is decreasing.
    """
    auc = 0.0
    for i in range(1, len(times)):
        dt = times[i] - times[i - 1]
        c1, c2 = concs[i - 1], concs[i]
        if dt <= 0:
            continue
        if c1 <= 0 or c2 <= 0:
            # Cannot take log of zero/negative — use linear
            auc += 0.5 * (c1 + c2) * dt
        elif c2 >= c1:
            # Ascending: linear trapezoidal
            auc += 0.5 * (c1 + c2) * dt
        else:
            # Descending: log trapezoidal
            auc += (c1 - c2) * dt / math.log(c1 / c2)
    return auc


def _find_terminal_phase(
    times: list[float], concs: list[float]
) -> tuple[list[float], list[float], int]:
    """Identify terminal phase points for log-linear regression.

    Walks backwards from the last positive concentration, collecting
    all contiguous declining points (positive concentrations only).
    Stops when a concentration rises or stays flat relative to the
    next-later point.  Requires at least 3 points in the terminal
    decline.

    Returns (terminal_times, terminal_log_concs, start_index).
    """
    # Collect indices with positive concentrations
    positive_pairs = [
        (i, times[i], concs[i]) for i in range(len(times)) if concs[i] > 0
    ]

    if len(positive_pairs) < 3:
        raise Refusal(
            "fewer than 3 positive concentration points — cannot "
            "determine terminal phase for half-life calculation",
            remedy="provide more time points with measurable concentrations",
        )

    # Walk backwards: collect points while concentration is strictly declining
    terminal: list[tuple[int, float, float]] = [positive_pairs[-1]]
    for j in range(len(positive_pairs) - 2, -1, -1):
        _idx, _t, c = positive_pairs[j]
        # Current point's concentration must be strictly greater than the
        # next-later point (which is already in terminal) for the segment
        # to remain a decline.
        if c <= terminal[-1][2]:
            # Not declining — stop collecting
            break
        terminal.append(positive_pairs[j])

    terminal.reverse()

    if len(terminal) < 3:
        raise Refusal(
            f"only {len(terminal)} points in the terminal decline phase — "
            "need at least 3 for log-linear regression",
            remedy="provide more time points in the terminal elimination phase",
        )

    t_times = [p[1] for p in terminal]
    t_log_concs = [math.log(p[2]) for p in terminal]
    start_idx = terminal[0][0]

    return t_times, t_log_concs, start_idx


def _linear_regression(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """Simple linear regression returning (slope, intercept, r_squared).

    Uses scipy if available for numerical stability, falls back to
    manual computation.
    """
    try:
        from scipy import stats

        result = stats.linregress(x, y)
        return result.slope, result.intercept, result.rvalue**2
    except ImportError:
        pass

    # Manual fallback
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y, strict=False))
    sum_x2 = sum(xi * xi for xi in x)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-15:
        return 0.0, 0.0, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R-squared
    y_mean = sum_y / n
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    ss_res = sum(
        (yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y, strict=False)
    )
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return slope, intercept, r_squared


# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group()
def pk() -> None:
    """In vivo PK analysis and human dose projection."""


# ---------------------------------------------------------------------------
# pk ingest
# ---------------------------------------------------------------------------


@pk.command("ingest")
@click.argument("input_file", type=click.Path(exists=True))
@out_option
@output_options
@pass_state
def ingest_cmd(
    state: AppState,
    input_file: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Parse and store a PK study data file.

    Reads a JSON file matching schema ``dde.pk-study.v1``, validates
    it, and writes normalised study data with a provenance sidecar under
    ``raw/pk/``.

    \b
    Outputs:
      {study_id}.pk-study.json       -- validated, normalised study data
      {study_id}.pk-study.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- read and validate ---
    input_path = resolve_artifact(state, input_file, "PK study input")
    doc = provenance.read_json(input_path, "PK study input")

    if not isinstance(doc, dict):
        raise SchemaError("PK study input must be a JSON object")

    _validate_study(doc)

    raw_study_id = doc.get("study_id")
    if not raw_study_id:
        raise SchemaError(
            "study_id is required",
            remedy="add a 'study_id' field to the input JSON",
        )
    study_id = _sanitize_id(raw_study_id)

    # --- write normalised study data ---
    normalised: dict[str, Any] = {
        "schema": "dde.pk-study.v1",
        "study_id": study_id,
        "species": doc["species"],
        "route": doc["route"],
        "dose_mg_kg": doc["dose_mg_kg"],
        "dose_units": doc.get("dose_units", "mg/kg"),
        "time_units": doc["time_units"],
        "concentration_units": doc["concentration_units"],
        "time_points": doc["time_points"],
        "concentrations": doc["concentrations"],
        "blq_value": doc.get("blq_value"),
        "body_weight_kg": doc.get("body_weight_kg"),
        "notes": doc.get("notes"),
    }

    study_path = target_dir / f"{study_id}.pk-study.json"
    study_path.write_text(json.dumps(normalised, indent=2) + "\n", encoding="utf-8")

    # --- provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="pk",
        subcommand="ingest",
        endpoint=None,
        parameters={
            "input_file": input_path.name,
            "study_id": study_id,
            "species": doc["species"],
            "route": doc["route"],
        },
    )
    sidecar.note("input_sha256", provenance.sha256_file(input_path))
    sidecar.note("n_time_points", len(doc["time_points"]))
    sidecar.note("species", doc["species"])
    sidecar.note("route", doc["route"])
    sidecar.note("dose_mg_kg", doc["dose_mg_kg"])

    sidecar.add_output(study_path)
    meta_path = sidecar.write(target_dir / f"{study_id}.pk-study.meta.json")

    emit.path(study_path, role="study")
    emit.path(meta_path, role="sidecar")
    emit.line(f"ingested {study_id} ({doc['species']}, {doc['route']})")
    emit.line(f"{len(doc['time_points'])} time points")
    emit.flush()


# ---------------------------------------------------------------------------
# pk nca
# ---------------------------------------------------------------------------


@pk.command("nca")
@click.argument("study_file", type=click.Path(exists=True))
@click.option(
    "--blq-method",
    type=click.Choice(["exclude", "zero", "loq_half"]),
    default=None,
    help=(
        "How to handle below-limit-of-quantitation (BLQ) concentrations. "
        "REQUIRED when any BLQ values are present in the data. "
        "Choices: exclude (remove BLQ points), zero (substitute 0), "
        "loq_half (substitute LOQ/2)."
    ),
)
@out_option
@output_options
@pass_state
def nca_cmd(
    state: AppState,
    study_file: str,
    blq_method: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Non-compartmental analysis of ingested PK data.

    Reads a ``.pk-study.json`` artifact from ``pk ingest`` and computes
    standard NCA parameters: Cmax, Tmax, AUC (linear-log trapezoidal),
    terminal half-life, clearance, and volume of distribution.

    BLQ handling must be specified explicitly via ``--blq-method`` when
    any BLQ values are present in the data.  This is never a silent
    default.

    \b
    Outputs:
      {study_id}.pk-nca.json       -- computed PK parameters
      {study_id}.pk-nca.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- load study data ---
    study_path = resolve_artifact(state, study_file, "PK study")
    doc = provenance.read_json(study_path, "PK study")

    if not isinstance(doc, dict):
        raise SchemaError("PK study file must be a JSON object")

    if doc.get("schema") != "dde.pk-study.v1":
        raise Refusal(
            "unsupported or missing schema tag on study file",
            detail=f"expected 'dde.pk-study.v1', got {doc.get('schema')!r}",
            remedy="use a study file produced by `dde pk ingest`",
        )

    study_id = _sanitize_id(doc["study_id"])
    species = doc["species"]
    route = doc["route"]
    dose_mg_kg = doc["dose_mg_kg"]
    time_units = doc["time_units"]
    conc_units = doc["concentration_units"]
    blq_value = doc.get("blq_value")
    body_weight_kg = doc.get("body_weight_kg")

    time_points = list(doc["time_points"])
    concentrations = list(doc["concentrations"])

    # --- BLQ handling ---
    has_blq = blq_value is not None and any(c == blq_value for c in concentrations)

    if has_blq and blq_method is None:
        n_blq = sum(1 for c in concentrations if c == blq_value)
        raise Refusal(
            f"BLQ values detected ({n_blq} point(s) equal to blq_value={blq_value}) "
            "but --blq-method was not specified",
            detail=(
                "BLQ handling is never a silent default. The choice of method "
                "(exclude, zero, or loq_half) affects computed PK parameters "
                "and must be an explicit analyst decision."
            ),
            remedy="re-run with --blq-method <exclude|zero|loq_half>",
        )

    effective_blq_method = blq_method or "exclude"  # only used when no BLQ
    times, concs, n_blq_points = _apply_blq(
        time_points, concentrations, blq_value, effective_blq_method
    )

    if len(times) < 3:
        raise Refusal(
            f"fewer than 3 data points remain after BLQ handling ({len(times)} points)",
            remedy="provide more measurable concentration points",
        )

    # --- Cmax and Tmax ---
    cmax = max(concs)
    tmax_idx = concs.index(cmax)
    tmax = times[tmax_idx]

    # --- AUC0-t (linear-log trapezoidal) ---
    # Compute AUC only up to the last positive concentration (Tlast) to
    # avoid double-counting when BLQ handling produces trailing zeros.
    tlast_idx = max(i for i, c in enumerate(concs) if c > 0)
    auc_0_t = _auc_linear_log_trapezoidal(
        times[: tlast_idx + 1], concs[: tlast_idx + 1]
    )
    last_conc = concs[tlast_idx]

    # --- Terminal phase: lambda_z and half-life ---
    terminal_times, terminal_log_concs, _term_start_idx = _find_terminal_phase(
        times, concs
    )
    slope, _intercept, r_squared = _linear_regression(
        terminal_times, terminal_log_concs
    )

    # lambda_z is the negative slope (slope should be negative for decay)
    lambda_z = -slope
    if lambda_z <= 0:
        raise Refusal(
            "terminal phase slope is non-negative — cannot compute "
            "elimination rate constant",
            detail=f"slope = {slope:.6f} (expected negative for elimination)",
            remedy="check the concentration-time profile for anomalies",
        )

    half_life = math.log(2) / lambda_z

    # --- AUC0-inf ---
    if last_conc <= 0:
        raise Refusal(
            "no positive concentration found for AUC extrapolation",
            remedy="check the concentration-time data",
        )

    auc_extrapolated = last_conc / lambda_z
    auc_0_inf = auc_0_t + auc_extrapolated

    # AUC extrapolation percentage
    auc_extrapolation_pct = (
        (auc_0_inf - auc_0_t) / auc_0_inf * 100.0 if auc_0_inf > 0 else 0.0
    )

    # --- Clearance and Vd ---
    # For IV: CL = Dose / AUC0-inf, Vd = CL / lambda_z
    # For non-IV: Dose/AUC gives apparent clearance (CL/F) and apparent Vd (Vd/F)
    if route == "iv":
        cl_label, vd_label = "clearance", "vd"
    else:
        cl_label, vd_label = "clearance_f", "vd_f"

    # Dose in absolute terms: dose_mg_kg * body_weight_kg (mg)
    # But if body_weight_kg not provided, report CL in per-kg units
    apparent = "" if route == "iv" else " (apparent)"
    if body_weight_kg is not None and body_weight_kg > 0:
        dose_absolute = dose_mg_kg * body_weight_kg  # mg
        clearance = dose_absolute / auc_0_inf  # mg / (conc_units * time_units)
        cl_units = f"mg / ({conc_units} * {time_units}){apparent}"
        vd = clearance / lambda_z
        vd_units = f"mg / {conc_units}{apparent}"
    else:
        # Report per-kg
        clearance = dose_mg_kg / auc_0_inf
        cl_units = f"mg/kg / ({conc_units} * {time_units}){apparent}"
        vd = clearance / lambda_z
        vd_units = f"mg/kg / {conc_units}{apparent}"

    # Convert time_units for half-life reporting
    half_life_units = time_units

    # AUC units
    auc_units = f"{conc_units} * {time_units}"

    # --- Build NCA output ---
    nca_record: dict[str, Any] = {
        "tool": "pk",
        "subcommand": "nca",
        "schema": "dde.pk-nca.v1",
        "study_id": study_id,
        "species": species,
        "route": route,
        "parameters": {
            "cmax": round(cmax, 4),
            "cmax_units": conc_units,
            "tmax": round(tmax, 4),
            "tmax_units": time_units,
            "auc_0_t": round(auc_0_t, 4),
            "auc_0_inf": round(auc_0_inf, 4),
            "auc_units": auc_units,
            "auc_extrapolation_pct": round(auc_extrapolation_pct, 2),
            "half_life": round(half_life, 4),
            "half_life_units": half_life_units,
            "lambda_z": round(lambda_z, 6),
            cl_label: round(clearance, 6),
            f"{cl_label}_units": cl_units,
            vd_label: round(vd, 6),
            f"{vd_label}_units": vd_units,
            "bioavailability": "not determinable (single study)",
        },
        "blq_handling": {
            "method": blq_method if has_blq else "none (no BLQ values)",
            "blq_value": blq_value,
            "n_blq_points": n_blq_points,
        },
        "terminal_phase": {
            "n_points": len(terminal_times),
            "r_squared": round(r_squared, 6),
            "time_range": [
                round(terminal_times[0], 4),
                round(terminal_times[-1], 4),
            ],
        },
    }

    nca_path = target_dir / f"{study_id}.pk-nca.json"
    nca_path.write_text(json.dumps(nca_record, indent=2) + "\n", encoding="utf-8")

    # --- Provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="pk",
        subcommand="nca",
        endpoint=None,
        parameters={
            "study_file": study_path.name,
            "study_id": study_id,
            "blq_method": blq_method,
        },
    )
    sidecar.note("study_sha256", provenance.sha256_file(study_path))
    sidecar.note("species", species)
    sidecar.note("route", route)
    sidecar.note("n_time_points", len(times))
    sidecar.note("n_blq_points", n_blq_points)

    # NCA linearity assumption is a standing property of the method, not a
    # conditional warning — moved to sidecar field per the always-true rule
    # (tool-design-guidance section 5.1, exit 2: relabel and move). See #146.
    sidecar.note(
        "method_caveat",
        (
            "NCA assumes dose-proportional exposure (linear PK). If the compound "
            "shows nonlinear PK (saturable metabolism, saturable absorption), NCA "
            "parameters are dose-dependent and the therapeutic index derived from "
            "them is specific to the study dose."
        ),
    )

    sidecar.add_output(nca_path)
    meta_path = sidecar.write(target_dir / f"{study_id}.pk-nca.meta.json")

    # --- emit summary ---
    emit.path(nca_path, role="nca")
    emit.path(meta_path, role="sidecar")
    emit.line(f"NCA: {study_id} ({species}, {route})")
    emit.line(f"Cmax = {cmax:.1f} {conc_units} at Tmax = {tmax:.2f} {time_units}")
    emit.line(f"AUC0-t = {auc_0_t:.1f}, AUC0-inf = {auc_0_inf:.1f} {auc_units}")
    emit.line(f"t1/2 = {half_life:.2f} {half_life_units}")
    emit.line(f"CL = {clearance:.4f} {cl_units}")
    emit.line(f"Vd = {vd:.4f} {vd_units}")
    if auc_extrapolation_pct > 20:
        emit.line(
            f"WARNING: AUC extrapolation = {auc_extrapolation_pct:.1f}% "
            f"(> 20% FDA limit)"
        )
    emit.flush()


# ---------------------------------------------------------------------------
# pk scale — allometric scaling
# ---------------------------------------------------------------------------

# Standard reference body weights (kg) for allometric scaling.
# Sources: Boxenbaum 1982, Mahmood & Balian 1996, Davies & Morris 1993.
REFERENCE_BODY_WEIGHTS_KG: dict[str, float] = {
    "mouse": 0.02,
    "rat": 0.25,
    "guinea pig": 0.4,
    "rabbit": 3.5,
    "cat": 4.0,
    "monkey": 5.0,
    "cynomolgus": 5.0,
    "rhesus": 5.0,
    "dog": 10.0,
    "mini-pig": 20.0,
    "pig": 70.0,
}

# Published scaling exponents (Boxenbaum 1982, Mahmood & Balian 1996).
DEFAULT_CL_EXPONENT = 0.75
DEFAULT_VD_EXPONENT = 1.0


def _get_body_weight(nca_doc: dict[str, Any], nca_path: Path) -> float:
    """Resolve body weight for a species from NCA doc, study file, or reference.

    Resolution order:
      1. ``body_weight_kg`` key in the NCA document itself.
      2. ``body_weight_kg`` in the companion ``.pk-study.json`` file
         (same directory, same ``study_id``).
      3. Published reference body weight for a recognised species name.
    """
    # 1. NCA document
    bw = nca_doc.get("body_weight_kg")
    if isinstance(bw, (int, float)) and bw > 0:
        return float(bw)

    # 2. Companion study file
    raw_study_id = nca_doc.get("study_id", "")
    study_id = _sanitize_id(raw_study_id) if raw_study_id else ""
    study_path = nca_path.parent / f"{study_id}.pk-study.json"
    if study_path.exists():
        if not is_safe_to_open(study_path):
            raise Refusal(f"Refusing to read through symlink: {study_path}")
        try:
            study_doc = json.loads(study_path.read_text(encoding="utf-8"))
            bw = study_doc.get("body_weight_kg")
            if isinstance(bw, (int, float)) and bw > 0:
                return float(bw)
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Reference body weight
    species = nca_doc.get("species", "").lower()
    if species in REFERENCE_BODY_WEIGHTS_KG:
        return REFERENCE_BODY_WEIGHTS_KG[species]

    raise Refusal(
        f"cannot determine body weight for species '{nca_doc.get('species')}'",
        detail="body weight is required for allometric scaling",
        remedy=(
            "either provide body_weight_kg in the study file, or use a "
            "recognised species name: " + ", ".join(sorted(REFERENCE_BODY_WEIGHTS_KG))
        ),
    )


def _extract_pk_for_scaling(
    nca_doc: dict[str, Any],
) -> tuple[float, float, str, str]:
    """Extract clearance and Vd from NCA output for scaling.

    Returns ``(clearance, vd, cl_units, vd_units)``.  Handles both IV
    (``clearance``, ``vd``) and non-IV (``clearance_f``, ``vd_f``) keys.
    """
    params = nca_doc.get("parameters", {})

    if "clearance" in params:
        cl = params["clearance"]
        cl_units = params.get("clearance_units", "unknown")
    elif "clearance_f" in params:
        cl = params["clearance_f"]
        cl_units = params.get("clearance_f_units", "unknown")
    else:
        raise SchemaError(
            "NCA output missing clearance or clearance_f",
            remedy="ensure the NCA file was produced by `dde pk nca`",
        )

    if "vd" in params:
        vd = params["vd"]
        vd_units = params.get("vd_units", "unknown")
    elif "vd_f" in params:
        vd = params["vd_f"]
        vd_units = params.get("vd_f_units", "unknown")
    else:
        raise SchemaError(
            "NCA output missing vd or vd_f",
            remedy="ensure the NCA file was produced by `dde pk nca`",
        )

    cl = float(cl)
    vd = float(vd)

    if cl <= 0 or vd <= 0:
        raise SchemaError(
            "clearance and vd must be positive for allometric scaling",
            remedy="check the NCA file — these values should always be positive",
        )

    return cl, vd, cl_units, vd_units


# NOTE (issue #136): Dermal and oral NCA yields apparent clearance
# (CL/F) and apparent volume of distribution (Vd/F), not true CL and Vd.
# Absolute bioavailability (F) requires an IV reference arm.  Without it,
# CL/F and Vd/F cannot be deconvolved into CL and F individually, and a
# human dose projection from dermal NCA carries this confound.  See FDA
# Guidance for Industry: Bioavailability and Bioequivalence Studies.


@pk.command("scale")
@click.argument("nca_files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--human-bw",
    type=float,
    default=70.0,
    show_default=True,
    help="Human body weight in kg.",
)
@click.option(
    "--target-auc",
    type=float,
    default=None,
    help="Target AUC for dose projection.",
)
@click.option(
    "--target-cmax",
    type=float,
    default=None,
    help="Target Cmax for dose projection.",
)
@out_option
@output_options
@pass_state
def scale_cmd(
    state: AppState,
    nca_files: tuple[str, ...],
    human_bw: float,
    target_auc: float | None,
    target_cmax: float | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Allometric scaling from animal PK to predicted human PK.

    Takes one or more ``.pk-nca.json`` files (from ``pk nca``), one per
    species.  Fits allometric scaling (Y = a x BW^b) to predict human PK
    parameters.

    With a single species, uses published average exponents (CL: 0.75,
    Vd: 1.0).  With two or more species, fits species-specific exponents
    via log-log regression and applies the rule of exponents (Mahmood &
    Balian 1996).

    \b
    Outputs:
      {compound_id}.pk-scaling.json       -- predicted human PK
      {compound_id}.pk-scaling.meta.json  -- provenance sidecar
    """
    # NOTE: PBPK modelling is a documented future extension point.
    # Only allometric/inline scaling is implemented.

    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    if human_bw <= 0:
        raise Refusal(
            f"--human-bw must be a positive number, got {human_bw}",
            remedy="provide the target human body weight in kg (default: 70)",
        )

    if target_auc is not None and target_cmax is not None:
        raise Refusal(
            "cannot specify both --target-auc and --target-cmax",
            remedy="choose one target exposure metric for dose projection",
        )

    # --- Load NCA data per species ---
    species_data: list[dict[str, Any]] = []
    species_names: list[str] = []

    for nca_file in nca_files:
        nca_path = resolve_artifact(state, nca_file, "NCA result")
        doc = provenance.read_json(nca_path, "NCA result")

        if not isinstance(doc, dict):
            raise SchemaError("NCA file must be a JSON object")

        if doc.get("schema") != "dde.pk-nca.v1":
            raise Refusal(
                f"unsupported schema in {nca_path.name}",
                detail=f"expected 'dde.pk-nca.v1', got {doc.get('schema')!r}",
                remedy="use a file produced by `dde pk nca`",
            )

        species = doc.get("species", "unknown")
        bw = _get_body_weight(doc, nca_path)
        cl, vd, cl_units, vd_units = _extract_pk_for_scaling(doc)

        species_data.append(
            {
                "species": species,
                "body_weight_kg": bw,
                "clearance": cl,
                "clearance_units": cl_units,
                "vd": vd,
                "vd_units": vd_units,
                "study_id": doc.get("study_id", "unknown"),
                "source_file": nca_path.name,
                "route": doc.get("route", "unknown"),
                "half_life_units": doc.get("parameters", {}).get(
                    "half_life_units", "h"
                ),
            }
        )
        species_names.append(species.lower())

    n_species = len(species_data)

    # --- Route consistency validation ---
    routes = {sd["route"] for sd in species_data}
    iv_routes = {r for r in routes if r == "iv"}
    non_iv_routes = routes - iv_routes
    if iv_routes and non_iv_routes:
        raise Refusal(
            "cannot scale across IV and non-IV routes in the same regression",
            detail=(
                "IV NCA produces CL and Vd; non-IV produces CL/F and Vd/F. "
                "These are different quantities and must not be mixed."
            ),
            remedy=("provide NCA files from the same route, or compute F first"),
        )

    # --- Unit consistency validation ---
    cl_units_set = {sd["clearance_units"] for sd in species_data}
    vd_units_set = {sd["vd_units"] for sd in species_data}
    if len(cl_units_set) > 1:
        raise Refusal(
            "clearance units are inconsistent across species",
            detail=f"found: {sorted(cl_units_set)}",
            remedy="ensure all NCA files use the same units for clearance",
        )
    if len(vd_units_set) > 1:
        raise Refusal(
            "vd units are inconsistent across species",
            detail=f"found: {sorted(vd_units_set)}",
            remedy="ensure all NCA files use the same units for Vd",
        )

    # --- Derive compound_id ---
    compound_id = _sanitize_id("scaling-" + "-".join(sorted(species_names)))

    # --- Allometric scaling ---
    roe_warning: str | None = None

    if n_species == 1:
        # Single species: Y_human = Y_animal x (BW_human / BW_animal)^b
        # using published average exponents (Boxenbaum 1982).
        sd = species_data[0]
        cl_exponent = DEFAULT_CL_EXPONENT
        vd_exponent = DEFAULT_VD_EXPONENT

        predicted_cl = (
            sd["clearance"] * (human_bw / sd["body_weight_kg"]) ** cl_exponent
        )
        predicted_vd = sd["vd"] * (human_bw / sd["body_weight_kg"]) ** vd_exponent

        scaling_method = "single_species_published_exponents"
        confidence_class = "low"
        scaling_details: dict[str, Any] = {
            "cl_exponent": cl_exponent,
            "vd_exponent": vd_exponent,
            "cl_coefficient": None,
            "vd_coefficient": None,
            "r_squared_cl": None,
            "r_squared_vd": None,
            "note": (
                "Single-species scaling uses published average exponents "
                "(CL: 0.75, Vd: 1.0). Cannot fit regression with one point."
            ),
        }
    else:
        # Multi-species: fit log(Y) = log(a) + b*log(BW) via regression.
        log_bws = [math.log(sd["body_weight_kg"]) for sd in species_data]
        log_cls = [math.log(sd["clearance"]) for sd in species_data]
        log_vds = [math.log(sd["vd"]) for sd in species_data]

        cl_slope, cl_intercept, cl_r2 = _linear_regression(log_bws, log_cls)
        vd_slope, vd_intercept, vd_r2 = _linear_regression(log_bws, log_vds)

        cl_exponent = cl_slope
        vd_exponent = vd_slope
        cl_coefficient = math.exp(cl_intercept)
        vd_coefficient = math.exp(vd_intercept)

        # Rule of exponents classification (Mahmood & Balian 1996) for CL.
        # The prediction formula is always simple allometry (Y = a x BW^b);
        # MLP and brain weight corrections are NOT applied — only classified.
        scaling_method = "simple_allometry"
        roe_class: str | None = None

        if cl_exponent > 1.0:
            roe_class = "brain_weight_recommended"
            roe_warning = (
                "CL exponent > 1.0: brain weight correction is recommended "
                "per Mahmood & Balian 1996 but not applied. Prediction "
                "reliability is reduced without this correction."
            )
        elif cl_exponent > 0.70:
            roe_class = "mlp_recommended"
            roe_warning = (
                "CL exponent 0.71-1.0: MLP (maximum life-span potential) "
                "correction is recommended per Mahmood & Balian 1996 but "
                "not applied. Prediction reliability is reduced without "
                "this correction."
            )

        # Y_human = a x BW_human^b
        predicted_cl = cl_coefficient * (human_bw**cl_exponent)
        predicted_vd = vd_coefficient * (human_bw**vd_exponent)

        confidence_class = "low" if cl_exponent > 0.70 else "moderate"
        scaling_details: dict[str, Any] = {
            "cl_exponent": round(cl_exponent, 6),
            "vd_exponent": round(vd_exponent, 6),
            "cl_coefficient": round(cl_coefficient, 6),
            "vd_coefficient": round(vd_coefficient, 6),
            "r_squared_cl": round(cl_r2, 6),
            "r_squared_vd": round(vd_r2, 6),
        }
        if roe_class is not None:
            scaling_details["rule_of_exponents_class"] = roe_class
        if roe_warning is not None:
            scaling_details["warning"] = roe_warning

    # Derived half-life: t1/2 = 0.693 x Vd / CL
    predicted_half_life = (
        0.693 * predicted_vd / predicted_cl if predicted_cl > 0 else None
    )

    # --- Dose projection (optional) ---
    projected_dose: dict[str, Any] | None = None
    if target_auc is not None and predicted_cl > 0:
        projected_dose = {
            "target_metric": "AUC",
            "target_value": target_auc,
            "projected_dose": round(predicted_cl * target_auc, 4),
            "dose_units": f"mg (absolute, for {human_bw} kg human)",
        }
    elif target_cmax is not None and predicted_vd > 0:
        projected_dose = {
            "target_metric": "Cmax",
            "target_value": target_cmax,
            "projected_dose": round(target_cmax * predicted_vd, 4),
            "dose_units": f"mg (absolute, for {human_bw} kg human)",
        }

    # --- Build output record ---
    scaling_record: dict[str, Any] = {
        "tool": "pk",
        "subcommand": "scale",
        "schema": "dde.pk-scaling.v1",
        "species_used": [sd["species"] for sd in species_data],
        "scaling_method": scaling_method,
        "human_body_weight_kg": human_bw,
        "predicted_human": {
            "clearance": round(predicted_cl, 6),
            "clearance_units": species_data[0]["clearance_units"],
            "vd": round(predicted_vd, 6),
            "vd_units": species_data[0]["vd_units"],
            "half_life": (
                round(predicted_half_life, 4)
                if predicted_half_life is not None
                else None
            ),
            "half_life_units": species_data[0]["half_life_units"],
        },
        "scaling_details": scaling_details,
        "confidence_class": confidence_class,
        "projected_dose": projected_dose,
        "species_input": [
            {
                "species": sd["species"],
                "body_weight_kg": sd["body_weight_kg"],
                "clearance": sd["clearance"],
                "vd": sd["vd"],
                "source_file": sd["source_file"],
            }
            for sd in species_data
        ],
    }

    scaling_path = target_dir / f"{compound_id}.pk-scaling.json"
    scaling_path.write_text(
        json.dumps(scaling_record, indent=2) + "\n", encoding="utf-8"
    )

    # --- Provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="pk",
        subcommand="scale",
        endpoint=None,
        parameters={
            "n_species": n_species,
            "human_bw_kg": human_bw,
            "scaling_method": scaling_method,
            "species": [sd["species"] for sd in species_data],
        },
    )

    for sd in species_data:
        sidecar.note(f"source_{sd['species']}", sd["source_file"])

    sidecar.note("confidence_class", confidence_class)
    sidecar.note("cl_exponent", scaling_details["cl_exponent"])
    sidecar.note("vd_exponent", scaling_details["vd_exponent"])

    # Rule-of-exponents warning (multi-species only, when exponent > 0.70)
    if roe_warning is not None:
        sidecar.warn(roe_warning, code="pk.rule_of_exponents_uncorrected")

    if n_species == 1:
        # Single-species relay: genuinely conditional, fires only when data
        # quality concern exists.
        sidecar.warn(
            "Single-species allometry has high uncertainty. The rule of "
            "exponents requires data from at least two species for reliable "
            "CL prediction. A human dose projection from one species should "
            "not be presented as a validated estimate.",
            code="pk.single_species_scaling",
        )

    # Allometric-vs-PBPK qualifier is a standing property of the method —
    # moved to sidecar field per the always-true rule
    # (tool-design-guidance section 5.1, exit 2: relabel and move). See #146.
    sidecar.note(
        "method_caveat",
        (
            "This is allometric scaling (an empirical correlation across "
            "species), not a mechanistic PBPK model of human physiology. "
            "A predicted human dose from allometry is a starting estimate, "
            "not a validated projection."
        ),
    )

    sidecar.add_output(scaling_path)
    meta_path = sidecar.write(target_dir / f"{compound_id}.pk-scaling.meta.json")

    # --- Emit summary ---
    emit.path(scaling_path, role="scaling")
    emit.path(meta_path, role="sidecar")
    emit.line(f"Scaling: {compound_id} ({n_species} species)")
    emit.line(f"Method: {scaling_method}")
    emit.line(
        f"Predicted human CL = {predicted_cl:.4f} {species_data[0]['clearance_units']}"
    )
    emit.line(f"Predicted human Vd = {predicted_vd:.4f} {species_data[0]['vd_units']}")
    if predicted_half_life is not None:
        emit.line(f"Predicted human t1/2 = {predicted_half_life:.2f} h")
    emit.line(f"Confidence: {confidence_class}")
    if projected_dose is not None:
        emit.line(
            f"Projected dose: {projected_dose['projected_dose']:.2f} "
            f"{projected_dose['dose_units']}"
        )
    emit.flush()


# ---------------------------------------------------------------------------
# pk ddi — static DDI prediction
# ---------------------------------------------------------------------------


def _validate_ddi_input(doc: dict[str, Any]) -> None:
    """Validate a DDI input document (schema ``dde.pk-ddi-input.v1``)."""
    if doc.get("schema") != "dde.pk-ddi-input.v1":
        raise Refusal(
            "unsupported or missing schema tag",
            detail=(f"expected 'dde.pk-ddi-input.v1', got {doc.get('schema')!r}"),
            remedy=(
                'ensure the input JSON contains \'"schema": "dde.pk-ddi-input.v1"\''
            ),
        )

    if "compound_id" not in doc or not doc["compound_id"]:
        raise SchemaError(
            "compound_id is required",
            remedy="add a 'compound_id' field to the input JSON",
        )

    cmax = doc.get("cmax_unbound")
    if not isinstance(cmax, (int, float)) or cmax <= 0:
        raise SchemaError(
            f"cmax_unbound must be a positive number, got {cmax!r}",
            remedy="provide the unbound Cmax at therapeutic dose",
        )

    if "cmax_units" not in doc:
        raise SchemaError(
            "cmax_units is required",
            remedy="specify the units for cmax_unbound (e.g. 'nM')",
        )

    inhibition = doc.get("cyp_inhibition")
    if not isinstance(inhibition, list) or not inhibition:
        raise SchemaError(
            "cyp_inhibition must be a non-empty array",
            remedy="provide at least one CYP inhibition entry",
        )

    cmax_units = doc["cmax_units"]
    for i, entry in enumerate(inhibition):
        if not isinstance(entry, dict):
            raise SchemaError(f"cyp_inhibition[{i}] must be an object")

        for field in ("isoform", "ic50_or_ki", "value_type", "units"):
            if field not in entry:
                raise SchemaError(
                    f"cyp_inhibition[{i}] missing required field '{field}'",
                    remedy=f"add '{field}' to each CYP inhibition entry",
                )

        val = entry["ic50_or_ki"]
        if not isinstance(val, (int, float)) or val <= 0:
            raise SchemaError(
                f"cyp_inhibition[{i}].ic50_or_ki must be a positive number, "
                f"got {val!r}",
            )

        vtype = entry["value_type"]
        if vtype not in ("ic50", "ki"):
            hint = suggest_match(str(vtype), ("ic50", "ki"))
            suggestion = f" Did you mean {hint!r}?" if hint else ""
            raise SchemaError(
                f"cyp_inhibition[{i}].value_type must be 'ic50' or 'ki', "
                f"got {vtype!r}.{suggestion}",
                detail="accepted values: ['ic50', 'ki']",
            )

        entry_units = entry["units"]
        if entry_units != cmax_units:
            raise Refusal(
                f"unit mismatch: cmax_units={cmax_units!r} but "
                f"cyp_inhibition[{i}].units={entry_units!r}",
                detail=(
                    "the static DDI model requires [I] and Ki in the same "
                    "units — silent unit conversion is refused"
                ),
                remedy="convert all values to the same concentration units",
            )


def _classify_ddi_risk(
    r_value: float,
    r_possible: float,
    r_clinical: float,
) -> str:
    """Classify DDI risk from an R value using pk-parameters thresholds.

    Boundaries (from the ``pk-parameters`` threshold set):
      R < r_possible → no clinically significant interaction likely
      r_possible ≤ R < r_clinical → possible interaction
      R ≥ r_clinical → clinical DDI study recommended
    """
    if r_value < r_possible:
        return "no clinically significant interaction likely"
    if r_value < r_clinical:
        return "possible interaction"
    return "clinical DDI study recommended"


@pk.command("ddi")
@click.argument("input_file", type=click.Path(exists=True))
@out_option
@output_options
@pass_state
def ddi_cmd(
    state: AppState,
    input_file: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Static DDI prediction from in vitro CYP inhibition data.

    Implements the FDA/EMA recommended basic static model:
    ``R = 1 + [I]max,u / Ki`` for reversible inhibition.  When the
    input provides IC₅₀ instead of Ki, Ki is estimated as IC₅₀ / 2
    (competitive inhibition assumption).

    DDI risk is classified per FDA 2020 guidance thresholds.  The static
    model is worst-case by design.

    \b
    Outputs:
      {compound_id}.pk-ddi.json       -- DDI prediction results
      {compound_id}.pk-ddi.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- Load and validate input ---
    input_path = resolve_artifact(state, input_file, "DDI input")
    doc = provenance.read_json(input_path, "DDI input")

    if not isinstance(doc, dict):
        raise SchemaError("DDI input must be a JSON object")

    _validate_ddi_input(doc)

    compound_id = _sanitize_id(doc["compound_id"])
    cmax_unbound = float(doc["cmax_unbound"])
    cmax_units = doc["cmax_units"]

    # --- Load DDI thresholds ---
    thresholds = load_thresholds(state, "pk-parameters")
    r_possible = thresholds.get("ddi_r_possible_interaction")
    r_clinical = thresholds.get("ddi_r_clinical_study")

    # --- Compute R values per isoform ---
    isoform_results: list[dict[str, Any]] = []

    for entry in doc["cyp_inhibition"]:
        isoform = entry["isoform"]
        value_type = entry["value_type"]
        raw_value = float(entry["ic50_or_ki"])

        # Derive Ki from IC50 if needed (competitive inhibition assumption)
        if value_type == "ic50":
            ki = raw_value / 2.0
        else:
            ki = raw_value

        # R = 1 + [I]max,u / Ki
        r_value = 1.0 + cmax_unbound / ki
        risk_class = _classify_ddi_risk(r_value, r_possible, r_clinical)

        isoform_results.append(
            {
                "isoform": isoform,
                "r_value": round(r_value, 4),
                "risk_classification": risk_class,
                "ki_used": round(ki, 4),
                "ki_source": (
                    f"IC50/2 ({raw_value})"
                    if value_type == "ic50"
                    else f"Ki ({raw_value})"
                ),
                "formula": (
                    f"R = 1 + [I]max,u / Ki = 1 + {cmax_unbound} / {round(ki, 4)}"
                ),
                "ki_units": entry["units"],
            }
        )

    # --- Build output record ---
    ddi_record: dict[str, Any] = {
        "tool": "pk",
        "subcommand": "ddi",
        "schema": "dde.pk-ddi.v1",
        "compound_id": compound_id,
        "cmax_unbound": cmax_unbound,
        "cmax_units": cmax_units,
        "model": "basic_static_r1",
        "guidance_reference": "FDA 2020 In Vitro Drug Interaction Studies",
        "thresholds_applied": {
            "ddi_r_possible_interaction": r_possible,
            "ddi_r_clinical_study": r_clinical,
        },
        "isoform_results": isoform_results,
    }

    ddi_path = target_dir / f"{compound_id}.pk-ddi.json"
    ddi_path.write_text(json.dumps(ddi_record, indent=2) + "\n", encoding="utf-8")

    # --- Provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="pk",
        subcommand="ddi",
        endpoint=None,
        parameters={
            "input_file": input_path.name,
            "compound_id": compound_id,
            "n_isoforms": len(isoform_results),
            "model": "basic_static_r1",
        },
    )
    sidecar.note("input_sha256", provenance.sha256_file(input_path))
    sidecar.note("cmax_unbound", cmax_unbound)
    sidecar.note("cmax_units", cmax_units)
    sidecar.note(
        "fda_guidance_version",
        "FDA 2020 In Vitro Drug Interaction Studies",
    )

    for ir in isoform_results:
        sidecar.note(f"R_{ir['isoform']}", ir["r_value"])

    # Static-model qualifier is a standing property of the method, not a
    # conditional warning — moved to sidecar field per the always-true rule
    # (tool-design-guidance section 5.1, exit 2: relabel and move). See #146.
    sidecar.note(
        "method_caveat",
        (
            "The static R model is worst-case by design (worst-case assumptions "
            "about intestinal and hepatic inhibitor concentrations). 'Possible "
            "interaction' means 'do a clinical DDI study', not 'a DDI exists'."
        ),
    )

    sidecar.add_output(ddi_path)
    meta_path = sidecar.write(target_dir / f"{compound_id}.pk-ddi.meta.json")

    # --- Emit summary ---
    emit.path(ddi_path, role="ddi")
    emit.path(meta_path, role="sidecar")
    emit.line(f"DDI prediction: {compound_id}")
    emit.line(f"[I]max,u = {cmax_unbound} {cmax_units}")
    for ir in isoform_results:
        emit.line(
            f"  {ir['isoform']}: R = {ir['r_value']:.3f} — {ir['risk_classification']}"
        )
    emit.flush()


# ---------------------------------------------------------------------------
# pk analyze — Phase 2 threshold application
# ---------------------------------------------------------------------------

# Map schema tag → (analysis_type, file_suffix, meta_suffix)
_SCHEMA_MAP: dict[str, tuple[str, str, str]] = {
    "dde.pk-nca.v1": ("nca", ".pk-nca.json", ".pk-nca.meta.json"),
    "dde.pk-scaling.v1": (
        "scaling",
        ".pk-scaling.json",
        ".pk-scaling.meta.json",
    ),
    "dde.pk-ddi.v1": ("ddi", ".pk-ddi.json", ".pk-ddi.meta.json"),
}


@pk.command("analyze")
@click.argument("path", type=click.Path())
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    path: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply pk-parameters thresholds to PK artifacts.

    Reads a ``.pk-nca.json``, ``.pk-scaling.json``, or ``.pk-ddi.json``
    artifact and applies the ``pk-parameters`` threshold set to produce
    an analysis record with margin assessments.

    \b
    Outputs:
      {stem}.pk.analysis.json  -- analysis record with verdict
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    # --- Resolve input ---
    source = resolve_artifact(state, path, "PK artifact")
    result_doc = provenance.read_json(source, "PK artifact")

    if not isinstance(result_doc, dict):
        raise SchemaError("PK artifact must be a JSON object")

    schema = result_doc.get("schema", "")
    if schema not in _SCHEMA_MAP:
        raise Refusal(
            f"unrecognised PK artifact schema: {schema!r}",
            detail=("expected one of: " + ", ".join(sorted(_SCHEMA_MAP))),
            remedy=("provide a file produced by `dde pk nca`, `pk scale`, or `pk ddi`"),
        )

    analysis_type, file_suffix, meta_suffix = _SCHEMA_MAP[schema]
    stem = source.name.replace(file_suffix, "")

    # --- Derive record type from schema for unique output filename ---
    record_type = provenance.record_type_from_schema(schema)
    new_analysis_name = f"{stem}.{record_type}.analysis.json"

    # Backward compatibility: warn if an old-format analysis file exists
    old_analysis_name = f"{stem}.pk.analysis.json"
    if old_analysis_name != new_analysis_name:
        old_candidate = source.parent / old_analysis_name
        if old_candidate.exists():
            from ..core.output import warn

            warn(
                f"old-format analysis exists: {old_analysis_name}; "
                f"new analysis uses: {new_analysis_name}"
            )

    # --- Load thresholds ---
    thresholds = load_thresholds(state, "pk-parameters")

    # --- Collect upstream relays from phase-1 sidecar ---
    relays: list[dict[str, str]] = []
    seen_codes: set[str] = set()

    lookup_dir = (
        state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
        if from_dir
        else source.parent
    )
    meta_path = lookup_dir / f"{stem}{meta_suffix}"
    if meta_path.exists():
        try:
            meta_doc = json.loads(meta_path.read_text(encoding="utf-8"))
            for r in meta_doc.get("mandatory_relays", []):
                code = r.get("code", "")
                if code and code not in seen_codes:
                    relays.append(r)
                    seen_codes.add(code)
        except (json.JSONDecodeError, OSError):
            pass

    # --- Build metrics and assessment per analysis type ---
    metrics: dict[str, Any] = {"analysis_type": analysis_type}
    assessment: dict[str, Any] = {}

    if analysis_type == "nca":
        _analyze_nca(result_doc, thresholds, metrics, assessment)
    elif analysis_type == "scaling":
        _analyze_scaling(result_doc, thresholds, metrics, assessment)
    elif analysis_type == "ddi":
        _analyze_ddi(result_doc, thresholds, metrics, assessment)

    # --- Write analysis ---
    analysis_path = beside_or_out(state, source, new_analysis_name, out)

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
        mandatory_relays=relays or None,
        suppress_warnings=as_json,
    )

    # --- Emit summary ---
    emit.path(analysis_path, role="analysis")
    emit.line(f"PK analysis: {stem} ({analysis_type})")
    emit.line(f"Threshold set: {thresholds.tag}")
    emit.line(f"Verdict: {assessment.get('verdict', 'unknown')}")
    if thresholds.unresolved():
        emit.line(f"Unresolved thresholds: {', '.join(thresholds.unresolved())}")
    emit.flush()


def _analyze_nca(
    doc: dict[str, Any],
    thresholds: Any,
    metrics: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Build NCA analysis: AUC extrapolation check + exposure adequacy."""
    params = doc.get("parameters", {})

    # AUC extrapolation check
    auc_extrap_pct = params.get("auc_extrapolation_pct", 0.0)
    auc_limit = thresholds.get("auc_extrapolation_limit")

    metrics["auc_extrapolation_pct"] = auc_extrap_pct
    metrics["auc_extrapolation_limit"] = auc_limit

    if auc_extrap_pct > auc_limit:
        assessment["auc_extrapolation"] = {
            "status": "flagged",
            "message": (
                f"AUC extrapolation ({auc_extrap_pct:.1f}%) exceeds "
                f"{auc_limit}% limit — AUC0-inf estimate may be unreliable"
            ),
        }
    else:
        assessment["auc_extrapolation"] = {
            "status": "acceptable",
            "message": (
                f"AUC extrapolation ({auc_extrap_pct:.1f}%) within {auc_limit}% limit"
            ),
        }

    # Therapeutic exposure adequacy — UNRESOLVED.
    # Do NOT invent a default.  Calling thresholds.get() would raise
    # ThresholdError; instead we record the gap explicitly so downstream
    # consumers know the assessment is incomplete.
    if "therapeutic_exposure_adequacy" in thresholds.unresolved():
        assessment["therapeutic_exposure"] = {
            "status": "unresolved",
            "message": (
                "therapeutic_exposure_adequacy threshold is UNRESOLVED — "
                "a program-specific value must be set in "
                ".dde/thresholds.yaml before this assessment can be made"
            ),
        }

    # NCA summary metrics
    for key in ("cmax", "half_life", "auc_0_inf"):
        if key in params:
            metrics[key] = params[key]

    verdict = (
        "flagged"
        if assessment.get("auc_extrapolation", {}).get("status") == "flagged"
        else "acceptable"
    )
    assessment["verdict"] = verdict


def _analyze_scaling(
    doc: dict[str, Any],
    thresholds: Any,
    metrics: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Build scaling analysis: confidence class assessment."""
    confidence = doc.get("confidence_class", "unknown")
    method = doc.get("scaling_method", "unknown")
    species_used = doc.get("species_used", [])

    metrics["scaling_method"] = method
    metrics["confidence_class"] = confidence
    metrics["species_used"] = species_used

    predicted = doc.get("predicted_human", {})
    for key in ("clearance", "vd", "half_life"):
        if key in predicted:
            metrics[f"predicted_{key}"] = predicted[key]

    assessment["scaling_confidence"] = {
        "status": confidence,
        "message": (
            f"Scaling confidence: {confidence} "
            f"(method: {method}, species: {', '.join(species_used)})"
        ),
    }

    # Therapeutic exposure adequacy — UNRESOLVED (same as NCA).
    if "therapeutic_exposure_adequacy" in thresholds.unresolved():
        assessment["therapeutic_exposure"] = {
            "status": "unresolved",
            "message": (
                "therapeutic_exposure_adequacy threshold is UNRESOLVED — "
                "a program-specific value must be set in "
                ".dde/thresholds.yaml before this assessment can be made"
            ),
        }

    verdict = "low_confidence" if confidence == "low" else "acceptable"
    assessment["verdict"] = verdict


def _analyze_ddi(
    doc: dict[str, Any],
    thresholds: Any,
    metrics: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Build DDI analysis: per-isoform R-value classification."""
    isoform_results = doc.get("isoform_results", [])

    r_possible = thresholds.get("ddi_r_possible_interaction")
    r_clinical = thresholds.get("ddi_r_clinical_study")

    metrics["n_isoforms"] = len(isoform_results)
    metrics["thresholds"] = {
        "ddi_r_possible_interaction": r_possible,
        "ddi_r_clinical_study": r_clinical,
    }

    isoform_assessments: list[dict[str, Any]] = []
    worst_class = "no clinically significant interaction likely"

    for ir in isoform_results:
        r_val = ir.get("r_value", 0)

        # Re-classify using official threshold set values
        if r_val >= r_clinical:
            risk = "clinical DDI study recommended"
        elif r_val >= r_possible:
            risk = "possible interaction"
        else:
            risk = "no clinically significant interaction likely"

        isoform_assessments.append(
            {
                "isoform": ir.get("isoform"),
                "r_value": r_val,
                "risk_classification": risk,
            }
        )

        # Track worst case
        if risk == "clinical DDI study recommended":
            worst_class = risk
        elif (
            risk == "possible interaction"
            and worst_class != "clinical DDI study recommended"
        ):
            worst_class = risk

    metrics["isoform_details"] = isoform_assessments

    assessment["ddi_risk"] = {
        "status": worst_class,
        "message": f"Overall DDI risk: {worst_class}",
        "per_isoform": isoform_assessments,
    }

    if worst_class == "clinical DDI study recommended":
        verdict = "clinical_study_needed"
    elif worst_class == "possible interaction":
        verdict = "possible_interaction"
    else:
        verdict = "acceptable"
    assessment["verdict"] = verdict


# ---------------------------------------------------------------------------
# pk dermal-partition — steady-state dermal absorption estimate
# ---------------------------------------------------------------------------


@pk.command("dermal-partition")
@click.option(
    "--kp",
    type=float,
    required=True,
    help="Skin permeability coefficient Kp (cm/hr).",
)
@click.option(
    "--strength",
    type=float,
    required=True,
    help="Formulation strength (% w/w).",
)
@click.option(
    "--area",
    type=float,
    required=True,
    help="Application area (cm2).",
)
@click.option(
    "--dose-interval",
    type=float,
    required=True,
    help="Dosing interval (hr).",
)
@click.option(
    "--density",
    type=float,
    default=1.0,
    show_default=True,
    help="Formulation density (g/mL). Default 1.0 for aqueous.",
)
@out_option
@output_options
@pass_state
def dermal_partition_cmd(
    state: AppState,
    kp: float,
    strength: float,
    area: float,
    dose_interval: float,
    density: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Estimate steady-state dermal absorption from Fick's first law.

    Computes steady-state flux (Jss), total absorption rate, and total
    absorbed dose per interval from the permeability coefficient (Kp),
    formulation strength, application area, and dosing interval.

    Kp should come from ``dde admet topical`` (Potts-Guy estimate) or
    from measured ex-vivo/in-vivo permeability data.

    \b
    Outputs:
      dermal-partition.pk-dermal.json       -- dermal partition estimate
      dermal-partition.pk-dermal.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- Input validation ---
    if kp <= 0:
        raise Refusal(
            f"--kp must be positive, got {kp}",
            remedy="provide a positive permeability coefficient in cm/hr",
        )
    if strength <= 0 or strength > 100:
        raise Refusal(
            f"--strength must be between 0 and 100 (% w/w), got {strength}",
            remedy="provide formulation strength as a percentage (0-100)",
        )
    if area <= 0:
        raise Refusal(
            f"--area must be positive, got {area}",
            remedy="provide a positive application area in cm2",
        )
    if dose_interval <= 0:
        raise Refusal(
            f"--dose-interval must be positive, got {dose_interval}",
            remedy="provide a positive dosing interval in hours",
        )
    if density <= 0:
        raise Refusal(
            f"--density must be positive, got {density}",
            remedy="provide a positive density in g/mL",
        )

    # --- Compute dermal partition parameters ---
    # Cv (vehicle concentration in ug/mL) = strength (%) * density (g/mL) * 10000
    # The factor 10000 converts from g/100mL (% w/w * density) to ug/mL:
    #   strength/100 * density * 1e6 ug/g = strength * density * 10000
    cv = strength * density * 10000  # ug/mL

    # Steady-state flux: Jss = Kp * Cv (ug/cm2/hr)
    jss = kp * cv

    # Total absorption rate: absorption_rate = Jss * area (ug/hr)
    absorption_rate = jss * area

    # Total absorbed per interval: total_absorbed = absorption_rate * dose_interval (ug)
    total_absorbed = absorption_rate * dose_interval

    # --- Build output record ---
    record: dict[str, Any] = {
        "tool": "pk",
        "subcommand": "dermal-partition",
        "schema": "dde.pk-dermal.v1",
        "parameters": {
            "kp_cm_hr": kp,
            "strength_pct": strength,
            "area_cm2": area,
            "dose_interval_hr": dose_interval,
            "density_g_ml": density,
        },
        "computed": {
            "cv_ug_ml": round(cv, 4),
            "jss_ug_cm2_hr": round(jss, 6),
            "absorption_rate_ug_hr": round(absorption_rate, 4),
            "total_absorbed_ug": round(total_absorbed, 4),
        },
        "assumptions": [
            "Steady-state (infinite dose, constant Cv at skin surface)",
            "Fick's first law of diffusion",
            "Homogeneous membrane (stratum corneum as rate-limiting barrier)",
        ],
    }

    record_path = target_dir / "dermal-partition.pk-dermal.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # --- Provenance sidecar ---
    sidecar = provenance.Sidecar(
        tool="pk",
        subcommand="dermal-partition",
        endpoint=None,
        parameters={
            "kp_cm_hr": kp,
            "strength_pct": strength,
            "area_cm2": area,
            "dose_interval_hr": dose_interval,
            "density_g_ml": density,
        },
    )
    sidecar.note("cv_ug_ml", round(cv, 4))
    sidecar.note("jss_ug_cm2_hr", round(jss, 6))
    sidecar.note("absorption_rate_ug_hr", round(absorption_rate, 4))
    sidecar.note("total_absorbed_ug", round(total_absorbed, 4))

    sidecar.warn(
        "Dermal partition parameters are estimated from steady-state "
        "assumptions and Fick's first law.",
        code="pk.dermal_partition_estimated",
    )

    sidecar.add_output(record_path)
    meta_path = sidecar.write(target_dir / "dermal-partition.pk-dermal.meta.json")

    # --- Emit summary ---
    emit.path(record_path, role="dermal-partition")
    emit.path(meta_path, role="sidecar")
    emit.line(f"Cv = {cv:.1f} ug/mL")
    emit.line(f"Jss = {jss:.4f} ug/cm2/hr")
    emit.line(f"Absorption rate = {absorption_rate:.2f} ug/hr")
    emit.line(f"Total absorbed per interval = {total_absorbed:.2f} ug")
    emit.flush()
