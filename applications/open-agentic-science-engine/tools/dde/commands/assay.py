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

"""`dde assay` — T3 assay data ingestion and analysis.

Phase 1 (no judgment):
  ingest   Parse a canonical assay JSON file (schema ``dde.assay.v1``),
           validate required fields, normalise and write a Layer 0
           artifact plus a ``.meta.json`` sidecar to ``raw/assays/``.

Phase 2 (offline, applies thresholds):
  analyze  Read stored assay data, apply named threshold sets for
           activity cutoffs, dose-response curve fitting (4PL/Hill),
           screen quality (Z-factor), and bell-shaped curve detection.
           Produces ``.analysis.json`` with per-compound verdicts.

The ``analyze`` subcommand is genuinely offline — the phase-2 latch
enforces this automatically for any command named ``analyze*``.
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
    emitter,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import http, provenance
from ..core.errors import ArtifactError, Refusal, SchemaError, ThresholdError
from ..core.output import Emitter
from ..core.qps import qps_for_host

TOOL = "assay"
ARTIFACT_CLASS = "assays"

# Bioactivity fetch — external database endpoints and pacing.
CHEMBL_API = "https://www.ebi.ac.uk/chembl/api/data"

PUBCHEM_API = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# Required top-level fields in a canonical assay record.
_REQUIRED_FIELDS = (
    "well_id",
    "compound_id",
    "readout_value",
    "readout_type",
    "assay_type",
    "plate_id",
    "run_id",
    "concentration",
)


# ---------------------------------------------------------------------------
# Lazy scipy import
# ---------------------------------------------------------------------------


def _require_scipy():
    """Lazy-import scipy.optimize, raising DependencyError if absent."""
    try:
        from scipy.optimize import curve_fit

        return curve_fit
    except ImportError as e:
        from ..core.errors import DependencyError

        raise DependencyError(
            "scipy is not installed",
            detail="dose-response curve fitting requires scipy.optimize.curve_fit",
            remedy="install scipy into the tools environment (pip install scipy)",
        ) from e


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


def _validate_record(record: dict) -> list[str]:
    """Validate a single assay data point. Returns list of problems."""
    problems: list[str] = []
    for field in _REQUIRED_FIELDS:
        if field not in record:
            problems.append(f"missing required field: {field}")
        elif record[field] is None:
            problems.append(f"null value for required field: {field}")

    if "readout_value" in record and record["readout_value"] is not None:
        if not isinstance(record["readout_value"], (int, float)):
            problems.append(
                f"readout_value must be numeric, got {type(record['readout_value']).__name__}"
            )
        elif isinstance(record["readout_value"], float) and (
            math.isnan(record["readout_value"]) or math.isinf(record["readout_value"])
        ):
            problems.append("readout_value must be finite, got non-finite float")

    if "concentration" in record and record["concentration"] is not None:
        conc = record["concentration"]
        if isinstance(conc, dict):
            if "value" not in conc:
                problems.append("concentration dict missing 'value' key")
            if "unit" not in conc:
                problems.append("concentration dict missing 'unit' key")
            if "value" in conc and not isinstance(conc["value"], (int, float)):
                problems.append("concentration value must be numeric")
            elif (
                "value" in conc
                and isinstance(conc["value"], float)
                and (math.isnan(conc["value"]) or math.isinf(conc["value"]))
            ):
                problems.append(
                    "concentration value must be finite, got non-finite float"
                )
        elif not isinstance(conc, (int, float)):
            problems.append(
                f"concentration must be numeric or a dict with value/unit, "
                f"got {type(conc).__name__}"
            )

    if "readout_type" in record and record["readout_type"] is not None:
        valid_types = ("percent_inhibition", "fold_change")
        if record["readout_type"] not in valid_types:
            problems.append(
                f"readout_type must be one of {valid_types}, "
                f"got {record['readout_type']!r}"
            )

    return problems


def _normalise_concentration(conc: Any) -> dict[str, Any]:
    """Normalise concentration to a dict with value and unit."""
    if isinstance(conc, dict):
        return {"value": conc.get("value"), "unit": conc.get("unit", "M")}
    return {"value": conc, "unit": "M"}


def _normalise(doc: dict, source: Path) -> dict:
    """Build the stable Layer 0 record from a canonical assay input."""
    data_points = doc.get("data", [])
    if not isinstance(data_points, list):
        raise SchemaError(
            "assay data 'data' field must be a list",
            detail=f"got {type(data_points).__name__}",
        )

    MAX_DATA_POINTS = 500_000
    if len(data_points) > MAX_DATA_POINTS:
        raise SchemaError(
            f"assay data exceeds maximum of {MAX_DATA_POINTS:,} data points "
            f"(got {len(data_points):,})",
            remedy="split the input into multiple files by plate or run",
        )

    all_problems: list[str] = []
    normalised_points: list[dict[str, Any]] = []

    for i, point in enumerate(data_points):
        if not isinstance(point, dict):
            all_problems.append(
                f"data[{i}]: expected a dict, got {type(point).__name__}"
            )
            continue

        problems = _validate_record(point)
        if problems:
            for p in problems:
                all_problems.append(f"data[{i}]: {p}")
            continue

        normalised_points.append(
            {
                "well_id": point["well_id"],
                "compound_id": point["compound_id"],
                "readout_value": point["readout_value"],
                "readout_type": point["readout_type"],
                "assay_type": point["assay_type"],
                "plate_id": point["plate_id"],
                "run_id": point["run_id"],
                "concentration": _normalise_concentration(point["concentration"]),
                "metadata": point.get("metadata", {}),
            }
        )

    if all_problems:
        raise SchemaError(
            f"{len(all_problems)} validation error(s) in assay data",
            detail="; ".join(all_problems[:10])
            + (
                f" (and {len(all_problems) - 10} more)"
                if len(all_problems) > 10
                else ""
            ),
            remedy="fix the input data and re-run ingest",
        )

    # Collect unique values for the summary.
    compounds = sorted({p["compound_id"] for p in normalised_points})
    plates = sorted({p["plate_id"] for p in normalised_points})
    runs = sorted({p["run_id"] for p in normalised_points})
    readout_types = sorted({p["readout_type"] for p in normalised_points})
    assay_types = sorted({p["assay_type"] for p in normalised_points})

    return {
        "schema": "dde.assay.v1",
        "source_file": source.name,
        "summary": {
            "n_data_points": len(normalised_points),
            "n_compounds": len(compounds),
            "n_plates": len(plates),
            "n_runs": len(runs),
            "readout_types": readout_types,
            "assay_types": assay_types,
            "compounds": compounds,
            "plates": plates,
            "runs": runs,
        },
        "data": normalised_points,
    }


# ---------------------------------------------------------------------------
# Dose-response curve fitting (4PL / Hill equation)
# ---------------------------------------------------------------------------


def _hill_equation(x: Any, bottom: float, top: float, ec50: float, hill: float) -> Any:
    """4-parameter logistic (Hill equation).

    y = bottom + (top - bottom) / (1 + (ec50/x)^hill)

    Uses the log-space form to avoid overflow/underflow in the power term
    when concentrations span many orders of magnitude.
    """
    import numpy as np

    ratio = np.where(x > 0, ec50 / x, np.inf)
    with np.errstate(over="ignore", invalid="ignore"):
        power = np.power(ratio, hill)
    return bottom + (top - bottom) / (1.0 + power)


def _fit_dose_response(
    concentrations: list[float], responses: list[float]
) -> dict[str, Any] | None:
    """Fit a 4PL dose-response curve. Returns fit parameters or None on failure."""
    curve_fit = _require_scipy()
    import numpy as np

    conc_arr = np.array(concentrations, dtype=float)
    resp_arr = np.array(responses, dtype=float)

    # Need at least 4 points for 4 parameters.
    if len(conc_arr) < 4:
        return None

    # Filter out zero/negative concentrations (can't take log).
    mask = conc_arr > 0
    conc_arr = conc_arr[mask]
    resp_arr = resp_arr[mask]

    if len(conc_arr) < 4:
        return None

    # Initial guesses.
    bottom_guess = float(np.min(resp_arr))
    top_guess = float(np.max(resp_arr))
    ec50_guess = float(np.median(conc_arr))
    hill_guess = 1.0

    try:
        popt, _ = curve_fit(
            _hill_equation,
            conc_arr,
            resp_arr,
            p0=[bottom_guess, top_guess, ec50_guess, hill_guess],
            maxfev=10000,
        )
        bottom, top, ec50, hill_slope = popt

        # Compute R^2.
        predicted = _hill_equation(conc_arr, *popt)
        ss_res = float(np.sum((resp_arr - predicted) ** 2))
        ss_tot = float(np.sum((resp_arr - np.mean(resp_arr)) ** 2))
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        return {
            "bottom": round(float(bottom), 4),
            "top": round(float(top), 4),
            "ec50": float(ec50),
            "hill_slope": round(float(hill_slope), 4),
            "r_squared": round(r_squared, 4),
            "n_points": len(conc_arr),
        }
    except (RuntimeError, ValueError, TypeError):
        return None


def _is_bell_shaped(concentrations: list[float], responses: list[float]) -> bool:
    """Detect a bell-shaped (non-monotonic) dose-response curve.

    A bell-shaped curve is one where activity increases then decreases
    (or vice versa) at higher concentrations. This is a hallmark of
    cytotoxicity masking the primary pharmacological effect.

    We sort by concentration and check if the response rises to a peak
    then falls (or falls to a trough then rises).
    """
    if len(concentrations) < 5:
        return False

    # Sort by concentration.
    paired = sorted(zip(concentrations, responses, strict=False))
    sorted_responses = [r for _, r in paired]

    # Find the index of the maximum response.
    max_idx = sorted_responses.index(max(sorted_responses))
    min_idx = sorted_responses.index(min(sorted_responses))

    # Range-based thresholds: surrounding values must differ from the
    # extremum by at least this fraction of the total data range.
    # Multiplicative thresholds (peak * 0.8) fail for negative and
    # near-zero values — e.g. percent_inhibition can legitimately be
    # negative, and trough * 1.2 would produce a wrong-direction
    # comparison.
    data_range = max(sorted_responses) - min(sorted_responses)
    if data_range == 0:
        return False
    threshold_fraction = 0.2  # surrounding must differ by 20% of total range

    # A bell shape means the extremum is in the interior, not at the edges.
    n = len(sorted_responses)
    margin = max(1, n // 5)  # At least 1 point on each side.

    # Check for peak in the interior (activity goes up then down).
    if margin <= max_idx <= n - 1 - margin:
        left = sorted_responses[:max_idx]
        right = sorted_responses[max_idx + 1 :]
        if left and right:
            left_mean = sum(left) / len(left)
            right_mean = sum(right) / len(right)
            peak = sorted_responses[max_idx]
            if (
                left_mean < peak - threshold_fraction * data_range
                and right_mean < peak - threshold_fraction * data_range
            ):
                return True

    # Check for trough in the interior (activity goes down then up).
    if margin <= min_idx <= n - 1 - margin:
        left = sorted_responses[:min_idx]
        right = sorted_responses[min_idx + 1 :]
        if left and right:
            left_mean = sum(left) / len(left)
            right_mean = sum(right) / len(right)
            trough = sorted_responses[min_idx]
            if (
                left_mean > trough + threshold_fraction * data_range
                and right_mean > trough + threshold_fraction * data_range
            ):
                return True

    return False


_CONTROL_IDS_EXACT = {"POS_CTRL", "NEG_CTRL", "POS", "NEG", "DMSO"}
_CONTROL_IDS_SUBSTRING = ("POSITIVE_CONTROL", "NEGATIVE_CONTROL")


def _is_control_well(compound_id: str) -> bool:
    """Return True if compound_id is a recognised control-well identifier.

    Uses exact match for short forms (POS, NEG, DMSO, POS_CTRL, NEG_CTRL)
    and substring match only for the unambiguous long forms
    (POSITIVE_CONTROL, NEGATIVE_CONTROL).  This prevents a compound like
    "COMPOSITE" from matching on the substring "POS".
    """
    upper = compound_id.upper()
    if upper in _CONTROL_IDS_EXACT:
        return True
    return any(tag in upper for tag in _CONTROL_IDS_SUBSTRING)


def _compute_z_factor(
    positive_controls: list[float], negative_controls: list[float]
) -> float | None:
    """Compute the Z-factor (Zhang et al. 1999).

    Z' = 1 - (3 * (sd_p + sd_n) / |mean_p - mean_n|)

    Returns None if there are insufficient data.
    """
    if len(positive_controls) < 2 or len(negative_controls) < 2:
        return None

    mean_p = sum(positive_controls) / len(positive_controls)
    mean_n = sum(negative_controls) / len(negative_controls)

    # Zhang et al. 1999 defines Z' using population SD (N denominator),
    # not sample SD (N-1 denominator).
    sd_p = (
        sum((x - mean_p) ** 2 for x in positive_controls) / len(positive_controls)
    ) ** 0.5
    sd_n = (
        sum((x - mean_n) ** 2 for x in negative_controls) / len(negative_controls)
    ) ** 0.5

    window = abs(mean_p - mean_n)
    if window == 0:
        return None

    return 1.0 - (3.0 * (sd_p + sd_n) / window)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def assay() -> None:
    """T3 assay data: ingest and analyze."""


@assay.command()
@click.argument("assay_file")
@out_option
@output_options
@pass_state
def ingest(
    state: AppState,
    assay_file: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Normalise a canonical assay JSON file into a Layer 0 artifact + sidecar.

    ASSAY_FILE is a JSON file matching the ``dde.assay.v1`` schema.
    The file must contain a top-level ``schema`` field set to
    ``"dde.assay.v1"`` and a ``data`` array of assay data points.
    """
    source = Path(assay_file).expanduser()
    project = state.project()
    if not source.is_absolute():
        candidates = [Path.cwd() / source, project.root / source]
        source = next((c for c in candidates if c.is_file()), candidates[0])
    if not source.is_file():
        raise ArtifactError(
            f"assay file not found: {assay_file}",
            detail=f"looked in {Path.cwd()} and {project.root}",
            remedy="pass an absolute path to the canonical assay JSON file",
        )
    source = source.resolve()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    doc = provenance.read_json(source, "assay data")
    if not isinstance(doc, dict):
        raise SchemaError(
            "assay file must be a JSON object",
            detail=f"got {type(doc).__name__}",
        )

    # Schema version check — non-canonical input is a refusal.
    if doc.get("schema") != "dde.assay.v1":
        raise Refusal(
            "assay file does not match canonical schema dde.assay.v1",
            detail=f"got schema {doc.get('schema')!r}",
            remedy='ensure the input file has \'"schema": "dde.assay.v1"\' at the top level',
        )

    record = _normalise(doc, source)

    if not record["data"]:
        raise SchemaError(
            "assay file contains no data points after validation",
            remedy="confirm the input file contains a non-empty 'data' array",
        )

    run_id = record["summary"]["runs"][0] if record["summary"]["runs"] else None
    assay_type = (
        record["summary"]["assay_types"][0]
        if record["summary"]["assay_types"]
        else None
    )
    name_parts = [_slug(assay_type, "assay")]
    if run_id:
        name_parts.append(_slug(run_id, "run"))
    name = "-".join(name_parts)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="ingest",
        endpoint=None,
        parameters={"assay_file": str(source)},
    )

    artifact_path = target_dir / f"{name}.assay.json"
    artifact_path.write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    sidecar.note("source_sha256", provenance.sha256_file(source))
    sidecar.note("n_data_points", record["summary"]["n_data_points"])
    sidecar.note("n_compounds", record["summary"]["n_compounds"])
    sidecar.note("n_plates", record["summary"]["n_plates"])

    sidecar.add_output(artifact_path)
    meta = sidecar.write(target_dir / f"{name}.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("n_data_points", record["summary"]["n_data_points"])
    emit.data("n_compounds", record["summary"]["n_compounds"])
    emit.data("n_plates", record["summary"]["n_plates"])
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(artifact_path), "assay")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


@assay.command()
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
    """Apply activity cutoffs and screen-quality thresholds to ingested assay data.

    ARTIFACT is the ``.assay.json`` produced by ``ingest``.  Reads stored
    data from disk, applies named threshold sets, and produces an
    ``.analysis.json`` with per-compound activity verdicts.

    Implements:
      - Activity cutoffs (percent inhibition / fold-change thresholds)
      - Dose-response curve fitting (4PL/Hill equation)
      - Curve fitting quality (Hill slope range and R-squared floor)
      - Z-factor screen quality validation
      - Bell-shaped dose-response detection (cytotoxicity confound)
    """
    project = state.project()
    path = resolve_artifact(state, artifact, "normalised assay data")
    record = provenance.read_json(path, "normalised assay data")

    if record.get("schema") != "dde.assay.v1":
        raise SchemaError(
            f"{path.name} is not a normalised assay artifact",
            detail=f"expected schema dde.assay.v1, got {record.get('schema')!r}",
            remedy="run `dde assay ingest` on the raw assay file first",
        )

    activity_thresholds = load_thresholds(state, "assay-activity")
    screen_thresholds = load_thresholds(state, "assay-screen-quality")

    data_points = record.get("data", [])

    # --- Group by compound ---
    compound_data: dict[str, list[dict]] = {}
    for point in data_points:
        cid = point["compound_id"]
        compound_data.setdefault(cid, []).append(point)

    # --- Collect upstream relays from the ingest sidecar ---
    relays: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    meta_path = path.with_name(path.name.replace(".assay.json", ".meta.json"))
    if meta_path.is_file():
        ingest_meta = provenance.read_json(meta_path, "provenance sidecar")
        for item in ingest_meta.get("mandatory_relays", []) or []:
            if item.get("code") not in seen_codes:
                relays.append(item)
                seen_codes.add(item["code"])

    # --- Z-factor: screen quality check ---
    # Uses _is_control_well() as the gatekeeper so the set of recognised
    # control IDs cannot diverge from the compound-skip loop below.
    positive_controls: list[float] = []
    negative_controls: list[float] = []
    for point in data_points:
        cid = point["compound_id"]
        if not _is_control_well(cid):
            continue
        upper = cid.upper()
        if "POSITIVE_CONTROL" in upper or upper in ("POS_CTRL", "POS"):
            positive_controls.append(point["readout_value"])
        else:
            negative_controls.append(point["readout_value"])

    z_factor = _compute_z_factor(positive_controls, negative_controls)
    z_factor_acceptable = screen_thresholds.get("z_factor_acceptable")
    z_factor_excellent = screen_thresholds.get("z_factor_excellent")

    screen_quality: dict[str, Any] = {
        "z_factor": round(z_factor, 4) if z_factor is not None else None,
        "n_positive_controls": len(positive_controls),
        "n_negative_controls": len(negative_controls),
        "z_factor_excellent_threshold": z_factor_excellent,
        "z_factor_acceptable_threshold": z_factor_acceptable,
    }

    if z_factor is not None:
        if z_factor >= z_factor_excellent:
            screen_quality["quality_verdict"] = "excellent"
        elif z_factor >= z_factor_acceptable:
            screen_quality["quality_verdict"] = "acceptable"
        else:
            screen_quality["quality_verdict"] = "unusable"
            relays.append(
                provenance.relay(
                    "assay.screen_quality_insufficient",
                    f"Z-factor is {z_factor:.4f}, below the usability threshold "
                    f"of {z_factor_acceptable} (Zhang et al. 1999). The assay "
                    "window is too narrow to distinguish active from inactive "
                    "compounds.",
                )
            )
            seen_codes.add("assay.screen_quality_insufficient")
    else:
        screen_quality["quality_verdict"] = "unknown"

    # --- Per-compound analysis ---
    per_compound: list[dict[str, Any]] = []
    for compound_id, points in sorted(compound_data.items()):
        # Skip control wells in compound analysis.
        if _is_control_well(compound_id):
            continue

        concentrations = [
            p["concentration"]["value"]
            for p in points
            if p["concentration"]["value"] is not None
        ]
        responses = [p["readout_value"] for p in points]
        readout_type = points[0]["readout_type"]

        compound_result: dict[str, Any] = {
            "compound_id": compound_id,
            "readout_type": readout_type,
            "n_data_points": len(points),
        }

        # Dose-response curve fitting (when multiple concentrations).
        unique_concs = set(concentrations)
        fit_result = None
        if len(unique_concs) >= 4 and len(concentrations) >= 4:
            fit_result = _fit_dose_response(concentrations, responses)

        if fit_result:
            compound_result["curve_fit"] = fit_result
        else:
            compound_result["curve_fit"] = None

        # Bell-shaped curve detection.
        bell_shaped = False
        if len(concentrations) >= 5:
            bell_shaped = _is_bell_shaped(concentrations, responses)
        compound_result["bell_shaped"] = bell_shaped

        if bell_shaped and "assay.cytotoxicity_confound" not in seen_codes:
            relays.append(
                provenance.relay(
                    "assay.cytotoxicity_confound",
                    "One or more compounds show a bell-shaped (non-monotonic) "
                    "dose-response curve, potentially confounded by cytotoxicity. "
                    "See per-compound advisories for details.",
                )
            )
            seen_codes.add("assay.cytotoxicity_confound")

        # Advisories.
        advisories: list[str] = []

        # Activity cutoff check — compare mean readout against the
        # appropriate threshold.  Thresholds are declared UNRESOLVED and
        # program-specific; if UNRESOLVED, skip with an advisory (same
        # pattern as hill_slope).
        mean_readout = sum(responses) / len(responses) if responses else None
        if mean_readout is not None:
            cutoff_key = (
                "activity_cutoff_inhibition"
                if readout_type == "percent_inhibition"
                else "activity_cutoff_fold_change"
            )
            try:
                cutoff = activity_thresholds.get(cutoff_key)
                compound_result["activity_cutoff"] = cutoff
                if readout_type == "percent_inhibition":
                    compound_result["active"] = mean_readout >= cutoff
                    if mean_readout < cutoff:
                        advisories.append(
                            f"Mean {readout_type} {mean_readout:.2f} below "
                            f"activity cutoff {cutoff}"
                        )
                else:
                    compound_result["active"] = mean_readout >= cutoff
                    if mean_readout < cutoff:
                        advisories.append(
                            f"Mean {readout_type} {mean_readout:.2f} below "
                            f"activity cutoff {cutoff}"
                        )
            except ThresholdError:
                advisories.append(
                    f"Activity cutoff check skipped: {cutoff_key} is UNRESOLVED"
                )

        if fit_result:
            # Hill slope range check (uses UNRESOLVED thresholds -- will raise
            # if not set by program).
            try:
                hill_min = activity_thresholds.get("hill_slope_min")
                hill_max = activity_thresholds.get("hill_slope_max")
                actual_hill = abs(fit_result["hill_slope"])
                if actual_hill < hill_min:
                    advisories.append(
                        f"Hill slope {fit_result['hill_slope']:.4f} below "
                        f"minimum {hill_min}"
                    )
                if actual_hill > hill_max:
                    advisories.append(
                        f"Hill slope {fit_result['hill_slope']:.4f} above "
                        f"maximum {hill_max}"
                    )
            except ThresholdError:
                # Thresholds are UNRESOLVED — skip Hill slope check.
                advisories.append(
                    "Hill slope range check skipped: threshold is UNRESOLVED"
                )

            # R-squared floor check.
            try:
                r2_floor = activity_thresholds.get("r_squared_floor")
                if fit_result["r_squared"] < r2_floor:
                    advisories.append(
                        f"R-squared {fit_result['r_squared']:.4f} below "
                        f"floor {r2_floor}"
                    )
            except ThresholdError:
                advisories.append(
                    "R-squared floor check skipped: threshold is UNRESOLVED"
                )

        if bell_shaped:
            advisories.append(
                "Bell-shaped dose-response detected; potential cytotoxicity confound"
            )

        compound_result["advisories"] = advisories
        per_compound.append(compound_result)

    # --- Build overall metrics and assessment ---
    n_bell_shaped = sum(1 for c in per_compound if c["bell_shaped"])
    n_fitted = sum(1 for c in per_compound if c["curve_fit"] is not None)

    metrics: dict[str, Any] = {
        "n_data_points": len(data_points),
        "n_compounds_analyzed": len(per_compound),
        "n_curves_fitted": n_fitted,
        "n_bell_shaped": n_bell_shaped,
        "screen_quality": screen_quality,
    }

    n_flagged = sum(1 for c in per_compound if c["advisories"])
    assessment: dict[str, Any] = {
        "screen_quality_verdict": screen_quality.get("quality_verdict", "unknown"),
        "n_compounds_flagged": n_flagged,
        "compounds": per_compound,
    }

    combined_tag = f"{activity_thresholds.tag}+{screen_thresholds.tag}"
    analysis_path = beside_or_out(
        state, path, path.name.replace(".assay.json", ".analysis.json"), out
    )
    provenance.write_analysis(
        analysis_path,
        source=path,
        threshold_set=combined_tag,
        thresholds_applied={
            **activity_thresholds.applied(),
            **screen_thresholds.applied(),
        },
        threshold_sources={
            **activity_thresholds.sources(),
            **screen_thresholds.sources(),
        },
        threshold_provenance=(
            f"{activity_thresholds.provenance}; {screen_thresholds.provenance}"
        ),
        unresolved=(activity_thresholds.unresolved() + screen_thresholds.unresolved()),
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", combined_tag)

    emit.line(f"Screen quality: {screen_quality.get('quality_verdict', 'unknown')}")
    if z_factor is not None:
        emit.line(f"Z-factor: {z_factor:.4f}")
    emit.line(f"Compounds analyzed: {len(per_compound)}")
    emit.line(f"Curves fitted: {n_fitted}")
    if n_bell_shaped:
        emit.line(f"Bell-shaped curves: {n_bell_shaped}")
    emit.line(f"Compounds flagged: {n_flagged}")
    for compound in per_compound[:5]:
        if compound["advisories"]:
            emit.line(f"  {compound['compound_id']}: {compound['advisories'][0]}")
    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()


# ---------------------------------------------------------------------------
# bioactivity-fetch — retrieve published bioactivity data from ChEMBL/PubChem
# ---------------------------------------------------------------------------


def _bioactivity_slug(compound_id: str, source: str) -> str:
    """Derive a filesystem-safe slug from compound ID and source."""
    if source == "pubchem":
        raw = f"cid-{compound_id}"
    else:
        raw = compound_id
    return _slug(raw, "bioactivity")


def _fetch_chembl(compound_id: str, target: str | None) -> list[dict[str, Any]]:
    """Fetch bioactivity records from ChEMBL. Handles pagination."""
    url = f"{CHEMBL_API}/activity.json?molecule_chembl_id={compound_id}&format=json&limit=500"
    if target:
        url += f"&target_chembl_id={target}"

    activities: list[dict[str, Any]] = []
    page_url: str | None = url

    while page_url:
        resp = http.request(
            "GET", page_url, qps=qps_for_host("www.ebi.ac.uk"), tolerate_status=(404,)
        )

        # 404 means the compound was not found.
        if resp.status_code == 404:
            raise Refusal(
                f"compound {compound_id!r} not found in ChEMBL",
                remedy="check the ChEMBL ID (e.g. CHEMBL25) and try again",
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise Refusal(
                f"unexpected response from ChEMBL for {compound_id!r}",
                remedy="check the ChEMBL ID (e.g. CHEMBL25) and try again",
            ) from e

        if not isinstance(data, dict):
            raise Refusal(
                f"compound {compound_id!r} not found in ChEMBL",
                remedy="check the ChEMBL ID (e.g. CHEMBL25) and try again",
            )

        for record in data.get("activities", []):
            value = record.get("standard_value")
            if value is not None:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    value = None

            pchembl = record.get("pchembl_value")
            if pchembl is not None:
                try:
                    pchembl = float(pchembl)
                except (ValueError, TypeError):
                    pchembl = None

            activities.append(
                {
                    "source_db": "chembl",
                    "source_id": record.get("assay_chembl_id"),
                    "compound_id": record.get("molecule_chembl_id"),
                    "compound_name": record.get("molecule_pref_name"),
                    "target_id": record.get("target_chembl_id"),
                    "target_name": record.get("target_pref_name"),
                    "target_organism": record.get("target_organism"),
                    "activity_type": record.get("standard_type"),
                    "value": value,
                    "units": record.get("standard_units"),
                    "relation": record.get("standard_relation"),
                    "pchembl_value": pchembl,
                    "assay_description": record.get("assay_description"),
                    "document_id": record.get("document_chembl_id"),
                }
            )

        # Pagination: page_meta.next is a relative URL or None.
        page_meta = data.get("page_meta", {})
        next_url = page_meta.get("next")
        if next_url:
            # next is a relative path like "/chembl/api/data/activity.json?..."
            page_url = f"https://www.ebi.ac.uk{next_url}"
        else:
            page_url = None

    return activities


def _fetch_pubchem(compound_id: str, target: str | None) -> list[dict[str, Any]]:
    """Fetch bioactivity records from PubChem assay summary."""
    url = f"{PUBCHEM_API}/compound/cid/{compound_id}/assaysummary/JSON"
    raw_resp = http.request(
        "GET", url, qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"), tolerate_status=(404,)
    )

    if raw_resp.status_code == 404:
        raise Refusal(
            f"compound CID {compound_id!r} not found in PubChem",
            remedy="check the PubChem CID (e.g. 2244) and try again",
        )

    try:
        resp = raw_resp.json()
    except ValueError as e:
        raise Refusal(
            f"unexpected response from PubChem for CID {compound_id!r}",
            remedy="check the PubChem CID (e.g. 2244) and try again",
        ) from e

    # PubChem returns an error object with a Fault key on some errors.
    if isinstance(resp, dict) and "Fault" in resp:
        raise Refusal(
            f"compound CID {compound_id!r} not found in PubChem",
            remedy="check the PubChem CID (e.g. 2244) and try again",
        )

    # PubChem table format: Table.Columns.Column[] and Table.Row[].Cell[]
    table = (resp or {}).get("Table", {})
    columns_meta = table.get("Columns", {}).get("Column", [])
    rows = table.get("Row", [])

    if not columns_meta or not rows:
        return []

    # Build column index for named access.
    col_idx: dict[str, int] = {}
    for i, col_name in enumerate(columns_meta):
        col_idx[col_name] = i

    def _cell(cells: list, name: str) -> Any:
        idx = col_idx.get(name)
        if idx is None or idx >= len(cells):
            return None
        return cells[idx]

    activities: list[dict[str, Any]] = []
    for row in rows:
        cells = row.get("Cell", [])
        if not cells:
            continue

        # Parse activity value.
        raw_value = _cell(cells, "Activity Value [uM]")
        value: float | None = None
        if raw_value is not None:
            try:
                value = float(raw_value)
            except (ValueError, TypeError):
                pass

        target_accession = _cell(cells, "Target Accession")
        target_gene_id = _cell(cells, "Target GeneID")

        # If --target provided, filter by target accession or gene ID.
        if target:
            target_match = False
            if target_accession and str(target_accession) == str(target):
                target_match = True
            if target_gene_id and str(target_gene_id) == str(target):
                target_match = True
            if not target_match:
                continue

        aid = _cell(cells, "AID")
        activities.append(
            {
                "source_db": "pubchem",
                "source_id": str(aid) if aid is not None else None,
                "compound_id": str(_cell(cells, "CID") or compound_id),
                "compound_name": None,
                "target_id": str(target_accession) if target_accession else None,
                "target_name": None,
                "target_organism": None,
                "activity_type": _cell(cells, "Activity Name"),
                "value": value,
                "units": "uM" if value is not None else None,
                "relation": None,
                "pchembl_value": None,
                "assay_description": _cell(cells, "Assay Name"),
                "document_id": str(_cell(cells, "PubMed ID"))
                if _cell(cells, "PubMed ID")
                else None,
            }
        )

    return activities


@assay.command("bioactivity-fetch")
@click.argument("compound_id")
@click.option(
    "--target",
    default=None,
    help="Filter by target ID (ChEMBL target ID or PubChem accession/gene ID).",
)
@click.option(
    "--source",
    "source_db",
    type=click.Choice(["chembl", "pubchem"]),
    default="chembl",
    help="Source database (default: chembl).",
)
@out_option
@output_options
@pass_state
def bioactivity_fetch(
    state: AppState,
    compound_id: str,
    target: str | None,
    source_db: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Fetch published bioactivity data from ChEMBL or PubChem.

    COMPOUND_ID is a ChEMBL ID (e.g. CHEMBL25) when --source is chembl,
    or a PubChem CID (e.g. 2244) when --source is pubchem.

    Results are written to ``raw/assays/`` as a ``dde.bioactivity.v1``
    artifact with a provenance sidecar.
    """
    emit = emitter(as_json, quiet)
    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    # Fetch from the selected source.
    if source_db == "chembl":
        endpoint = f"{CHEMBL_API}/activity.json"
        activities = _fetch_chembl(compound_id, target)
    else:
        endpoint = f"{PUBCHEM_API}/compound/cid/{compound_id}/assaysummary/JSON"
        activities = _fetch_pubchem(compound_id, target)

    # Build activity type summary.
    activity_types = sorted(
        {a["activity_type"] for a in activities if a["activity_type"] is not None}
    )

    # Build the artifact record.
    record = {
        "schema": "dde.bioactivity.v1",
        "query": {
            "compound_id": compound_id,
            "target_id": target,
            "source_db": source_db,
        },
        "summary": {
            "n_activities": len(activities),
            "activity_types": activity_types,
            "sources": [source_db],
        },
        "activities": activities,
    }

    slug = _bioactivity_slug(compound_id, source_db)
    artifact_path = target_dir / f"{slug}.bioactivity.json"
    artifact_path.write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    # Build provenance sidecar.
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="bioactivity-fetch",
        endpoint=endpoint,
        parameters={
            "compound_id": compound_id,
            "target_id": target,
            "source_db": source_db,
        },
    )

    if source_db == "chembl":
        sidecar.note("source", "ChEMBL (EMBL-EBI)")
        sidecar.note("licence", "CC BY-SA 3.0")
    else:
        sidecar.note("source", "PubChem (NCBI)")

    sidecar.note("n_activities", len(activities))

    # Attach mandatory relay: externally sourced data.
    sidecar.warn(
        "Bioactivity values are literature-derived and retrieved from a public "
        "database; they are not validated in-house measurements.",
        code="bioactivity.externally_sourced",
    )

    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(target_dir / f"{slug}.meta.json")

    emit.data("n_activities", len(activities))
    emit.data("activity_types", activity_types)
    emit.data("source_db", source_db)
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(artifact_path), "bioactivity")
    emit.path(project.relative(meta_path), "sidecar")
    emit.flush()
