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

"""dde tox — preclinical toxicology data processing.

Phase A subcommands:

  ingest    Parse a canonical tox study JSON file and store normalised
            data with a provenance sidecar.  Three schemas:
            tox-repeat-dose, tox-safety-pharm, tox-genotox.

  genotox   ICH S2(R1) weight-of-evidence assessment of a genotoxicity
            battery.  Reads a .tox-genotox.json artifact from ingest.

  margins   Cross-artifact therapeutic index and hERG margin calculation.
            Reads repeat-dose tox + PK NCA artifacts.

  analyze   Apply the tox-safety-package threshold set to phase-1
            artifacts and produce .analysis.json verdicts.
"""

from __future__ import annotations

import json
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
from ..core.schema_registry import suggest_match

ARTIFACT_CLASS = "tox"

# ---------------------------------------------------------------------------
# Constants — the full set for all planned schemas, not just repeat-dose.
# ---------------------------------------------------------------------------

VALID_ROUTES = {"iv", "oral", "sc", "im", "ip", "dermal", "inhalation"}
VALID_SPECIES = {"rat", "mouse", "dog", "monkey", "rabbit", "minipig"}
VALID_ORGAN_SYSTEMS = {
    "liver",
    "kidney",
    "heart",
    "lung",
    "brain",
    "gi",
    "hematologic",
    "endocrine",
    "reproductive",
    "skin",
    "musculoskeletal",
    "immune",
    "other",
}
VALID_SEVERITIES = {"minimal", "mild", "moderate", "marked", "severe"}
VALID_GENOTOX_ASSAY_TYPES = {
    "ames",
    "chromosomal_aberration",
    "micronucleus_in_vitro",
    "micronucleus_in_vivo",
}
VALID_GENOTOX_RESULTS = {"negative", "positive", "equivocal"}
VALID_CONC_UNITS = {"nM", "uM", "ng/mL", "ug/mL", "mg/mL"}
VALID_GLP_STATUS = {"compliant", "non-compliant", "not_stated"}
VALID_AUC_UNITS = {
    "ng*h/mL",
    "ug*h/mL",
    "nM*h",
    "uM*h",
    # pk.py writes "conc_units * time_units" — accept that convention too
    "ng/mL * h",
    "ug/mL * h",
    "nM * h",
    "uM * h",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_id(raw: str) -> str:
    """Make an ID safe for use in filenames, preventing path traversal."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw)).strip("-")
    if not safe:
        raise SchemaError(
            "ID contains no usable characters after sanitization",
            remedy="use alphanumeric characters, hyphens, dots, or underscores",
        )
    return safe[:120]


def _normalize_auc_units(units_str: str) -> str:
    """Normalize AUC unit strings for comparison.

    pk.py writes ``"ng/mL * h"`` (conc * time) while tox uses
    ``"ng*h/mL"`` (conc-time).  This function maps both conventions
    to a single canonical form so that equivalent units compare equal.

    Canonical form: compact tox-style (``"ng*h/mL"``, ``"nM*h"``).
    """
    s = units_str.strip()
    # Strip whitespace around '*' — "ng/mL * h" → "ng/mL*h"
    s = re.sub(r"\s*\*\s*", "*", s)

    # Handle "conc/vol*time" → "conc*time/vol" (e.g. "ng/mL*h" → "ng*h/mL")
    m = re.match(r"^([A-Za-z]+)/([A-Za-z]+)\*([A-Za-z]+)$", s)
    if m:
        conc, vol, time = m.group(1), m.group(2), m.group(3)
        return f"{conc}*{time}/{vol}"

    # Already in canonical form ("ng*h/mL") or simple ("nM*h")
    return s


def _validate_repeat_dose(doc: dict[str, Any]) -> None:
    """Validate a tox-repeat-dose document exhaustively.

    Every required field, type, valid-value set, and cross-field
    constraint is checked.  Raises ``SchemaError`` for structural/type
    issues and ``Refusal`` for semantic issues.
    """
    # Schema tag
    if doc.get("schema") != "dde.tox-repeat-dose.v1":
        raise Refusal(
            "unsupported or missing schema tag",
            detail=f"expected 'dde.tox-repeat-dose.v1', got {doc.get('schema')!r}",
            remedy='ensure the input JSON contains \'"schema": "dde.tox-repeat-dose.v1"\'',
        )

    # --- Required scalar fields ---

    # study_id
    study_id = doc.get("study_id")
    if not study_id or not isinstance(study_id, str):
        raise SchemaError(
            "required field 'study_id' is missing, null, or not a string",
            remedy="add a non-empty 'study_id' string to the input JSON",
        )

    # species — any non-empty string accepted; sidecar notes non-standard
    species = doc.get("species")
    if not species or not isinstance(species, str):
        raise SchemaError(
            "required field 'species' is missing, null, or not a string",
            remedy="add a non-empty 'species' string to the input JSON",
        )

    # route — must be in VALID_ROUTES
    route = doc.get("route")
    if not route or not isinstance(route, str):
        raise SchemaError(
            "required field 'route' is missing, null, or not a string",
            remedy="add 'route' to the input JSON",
        )
    if route not in VALID_ROUTES:
        hint = suggest_match(route, VALID_ROUTES)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised route: {route!r}.{suggestion}",
            detail=f"accepted values: {sorted(VALID_ROUTES)}",
            remedy=f"use one of: {', '.join(sorted(VALID_ROUTES))}",
        )

    # duration_days — positive integer
    duration_days = doc.get("duration_days")
    if duration_days is None:
        raise SchemaError(
            "required field 'duration_days' is missing or null",
            remedy="add 'duration_days' as a positive integer",
        )
    if not isinstance(duration_days, int) or isinstance(duration_days, bool):
        raise SchemaError(
            f"'duration_days' must be an integer, got {type(duration_days).__name__}: {duration_days!r}",
            remedy="provide 'duration_days' as a positive integer",
        )
    if duration_days <= 0:
        raise SchemaError(
            f"'duration_days' must be positive, got {duration_days}",
            remedy="provide a positive integer for study duration in days",
        )

    # noael_mg_kg — positive number
    noael = doc.get("noael_mg_kg")
    if noael is None:
        raise SchemaError(
            "required field 'noael_mg_kg' is missing or null",
            remedy="add 'noael_mg_kg' as a positive number",
        )
    if not isinstance(noael, (int, float)) or isinstance(noael, bool):
        raise SchemaError(
            f"'noael_mg_kg' must be a number, got {type(noael).__name__}: {noael!r}",
            remedy="provide 'noael_mg_kg' as a positive number",
        )
    if noael <= 0:
        raise SchemaError(
            f"'noael_mg_kg' must be positive, got {noael}",
            remedy="provide a positive number for the no-adverse-effect level",
        )

    # noael_basis — non-empty string
    noael_basis = doc.get("noael_basis")
    if not noael_basis or not isinstance(noael_basis, str):
        raise SchemaError(
            "required field 'noael_basis' is missing, null, or not a string",
            remedy="add a non-empty 'noael_basis' string describing the basis "
            "for the NOAEL determination",
        )

    # --- dose_groups — non-empty array of dose group objects ---

    dose_groups = doc.get("dose_groups")
    if not isinstance(dose_groups, list) or not dose_groups:
        raise SchemaError(
            "'dose_groups' must be a non-empty array",
            remedy="provide at least one dose group object",
        )

    dose_values: list[float] = []
    for i, dg in enumerate(dose_groups):
        if not isinstance(dg, dict):
            raise SchemaError(f"dose_groups[{i}] must be an object")

        # dose_mg_kg — non-negative (0 for vehicle control)
        dose = dg.get("dose_mg_kg")
        if dose is None:
            raise SchemaError(
                f"dose_groups[{i}].dose_mg_kg is required",
                remedy="add 'dose_mg_kg' to each dose group",
            )
        if not isinstance(dose, (int, float)) or isinstance(dose, bool):
            raise SchemaError(
                f"dose_groups[{i}].dose_mg_kg must be a number, "
                f"got {type(dose).__name__}: {dose!r}",
            )
        if dose < 0:
            raise SchemaError(
                f"dose_groups[{i}].dose_mg_kg must be non-negative, got {dose}",
                remedy="use 0 for vehicle control groups",
            )
        dose_values.append(float(dose))

        # n_animals — positive integer
        n = dg.get("n_animals")
        if n is None:
            raise SchemaError(
                f"dose_groups[{i}].n_animals is required",
                remedy="add 'n_animals' to each dose group",
            )
        if not isinstance(n, int) or isinstance(n, bool):
            raise SchemaError(
                f"dose_groups[{i}].n_animals must be an integer, "
                f"got {type(n).__name__}: {n!r}",
            )
        if n <= 0:
            raise SchemaError(
                f"dose_groups[{i}].n_animals must be positive, got {n}",
            )

        # findings — array of finding objects (may be empty)
        findings = dg.get("findings")
        if not isinstance(findings, list):
            raise SchemaError(
                f"dose_groups[{i}].findings must be an array",
                remedy="provide a 'findings' array (may be empty for clean dose groups)",
            )
        for j, finding in enumerate(findings):
            _validate_finding(i, j, finding)

        # mortality — optional, non-negative integer
        mortality = dg.get("mortality")
        if mortality is not None:
            if not isinstance(mortality, int) or isinstance(mortality, bool):
                raise SchemaError(
                    f"dose_groups[{i}].mortality must be an integer, "
                    f"got {type(mortality).__name__}: {mortality!r}",
                )
            if mortality < 0:
                raise SchemaError(
                    f"dose_groups[{i}].mortality must be non-negative, got {mortality}",
                )

    # --- Cross-field: at least one dose group with dose_mg_kg > 0 ---
    if not any(d > 0 for d in dose_values):
        raise SchemaError(
            "dose_groups must include at least one group with dose_mg_kg > 0",
            remedy="add a treatment dose group (vehicle control alone is insufficient)",
        )

    # --- Cross-field: noael_mg_kg must match a dose group value ---
    if float(noael) not in dose_values:
        raise Refusal(
            f"noael_mg_kg ({noael}) does not match any dose_groups[].dose_mg_kg value",
            detail=f"dose group values: {dose_values}",
            remedy="the NOAEL must correspond to one of the tested dose levels",
        )

    # --- Optional fields ---

    # loael_mg_kg — positive, must be > noael_mg_kg
    loael = doc.get("loael_mg_kg")
    if loael is not None:
        if not isinstance(loael, (int, float)) or isinstance(loael, bool):
            raise SchemaError(
                f"'loael_mg_kg' must be a number, got {type(loael).__name__}: {loael!r}",
            )
        if loael <= 0:
            raise SchemaError(
                f"'loael_mg_kg' must be positive, got {loael}",
            )
        if loael <= noael:
            raise Refusal(
                f"loael_mg_kg ({loael}) must be greater than noael_mg_kg ({noael})",
                detail="the LOAEL is by definition the lowest dose with an adverse "
                "effect, which must be above the NOAEL",
                remedy="check the NOAEL and LOAEL values",
            )

    # glp_status — must be in VALID_GLP_STATUS when present
    glp = doc.get("glp_status")
    if glp is not None:
        if not isinstance(glp, str):
            raise SchemaError(
                f"'glp_status' must be a string, got {type(glp).__name__}",
            )
        if glp not in VALID_GLP_STATUS:
            hint = suggest_match(glp, VALID_GLP_STATUS)
            suggestion = f" Did you mean {hint!r}?" if hint else ""
            raise Refusal(
                f"unrecognised glp_status: {glp!r}.{suggestion}",
                detail=f"accepted values: {sorted(VALID_GLP_STATUS)}",
                remedy=f"use one of: {', '.join(sorted(VALID_GLP_STATUS))}",
            )

    # strain — string when present
    strain = doc.get("strain")
    if strain is not None and not isinstance(strain, str):
        raise SchemaError(
            f"'strain' must be a string when present, got {type(strain).__name__}",
        )

    # body_weight_kg — positive number when present
    bw = doc.get("body_weight_kg")
    if bw is not None:
        if not isinstance(bw, (int, float)) or isinstance(bw, bool):
            raise SchemaError(
                f"'body_weight_kg' must be a number, got {type(bw).__name__}: {bw!r}",
            )
        if bw <= 0:
            raise SchemaError(
                f"'body_weight_kg' must be positive, got {bw}",
            )

    # notes — string when present
    notes = doc.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise SchemaError(
            f"'notes' must be a string when present, got {type(notes).__name__}",
        )

    # noael_exposure — optional object with at least one of auc/cmax
    _validate_noael_exposure(doc.get("noael_exposure"))


def _validate_finding(dg_idx: int, f_idx: int, finding: Any) -> None:
    """Validate a single finding object within a dose group."""
    if not isinstance(finding, dict):
        raise SchemaError(f"dose_groups[{dg_idx}].findings[{f_idx}] must be an object")

    # finding (description) — required, non-empty string
    fdesc = finding.get("finding")
    if not fdesc or not isinstance(fdesc, str):
        raise SchemaError(
            f"dose_groups[{dg_idx}].findings[{f_idx}].finding is required "
            "and must be a non-empty string",
            remedy="add a 'finding' description to each finding object",
        )

    # organ_system — optional, must be in VALID_ORGAN_SYSTEMS
    organ = finding.get("organ_system")
    if organ is not None:
        if not isinstance(organ, str):
            raise SchemaError(
                f"dose_groups[{dg_idx}].findings[{f_idx}].organ_system must be a string",
            )
        if organ not in VALID_ORGAN_SYSTEMS:
            hint = suggest_match(organ, VALID_ORGAN_SYSTEMS)
            suggestion = f" Did you mean {hint!r}?" if hint else ""
            raise Refusal(
                f"unrecognised organ_system in dose_groups[{dg_idx}].findings[{f_idx}]: {organ!r}.{suggestion}",
                detail=f"accepted values: {sorted(VALID_ORGAN_SYSTEMS)}",
                remedy=f"use one of: {', '.join(sorted(VALID_ORGAN_SYSTEMS))}",
            )

    # severity — optional, must be in VALID_SEVERITIES
    severity = finding.get("severity")
    if severity is not None:
        if not isinstance(severity, str):
            raise SchemaError(
                f"dose_groups[{dg_idx}].findings[{f_idx}].severity must be a string",
            )
        if severity not in VALID_SEVERITIES:
            hint = suggest_match(severity, VALID_SEVERITIES)
            suggestion = f" Did you mean {hint!r}?" if hint else ""
            raise Refusal(
                f"unrecognised severity in dose_groups[{dg_idx}].findings[{f_idx}]: {severity!r}.{suggestion}",
                detail=f"accepted values: {sorted(VALID_SEVERITIES)}",
                remedy=f"use one of: {', '.join(sorted(VALID_SEVERITIES))}",
            )

    # incidence — optional string
    incidence = finding.get("incidence")
    if incidence is not None and not isinstance(incidence, str):
        raise SchemaError(
            f"dose_groups[{dg_idx}].findings[{f_idx}].incidence must be a string",
        )

    # dose_related — optional boolean
    dose_related = finding.get("dose_related")
    if dose_related is not None and not isinstance(dose_related, bool):
        raise SchemaError(
            f"dose_groups[{dg_idx}].findings[{f_idx}].dose_related must be a boolean",
        )


def _validate_noael_exposure(exposure: Any) -> None:
    """Validate the optional noael_exposure object."""
    if exposure is None:
        return

    if not isinstance(exposure, dict):
        raise SchemaError("'noael_exposure' must be an object when present")

    auc = exposure.get("auc")
    cmax = exposure.get("cmax")

    # At least one of auc or cmax must be present and positive
    if auc is None and cmax is None:
        raise SchemaError(
            "noael_exposure must contain at least one of 'auc' or 'cmax'",
            remedy="provide at least one exposure metric",
        )

    if auc is not None:
        if not isinstance(auc, (int, float)) or isinstance(auc, bool):
            raise SchemaError(
                f"noael_exposure.auc must be a number, "
                f"got {type(auc).__name__}: {auc!r}",
            )
        if auc <= 0:
            raise SchemaError(
                f"noael_exposure.auc must be positive, got {auc}",
            )
        auc_units = exposure.get("auc_units")
        if not auc_units:
            raise SchemaError(
                "noael_exposure.auc_units is required when auc is present",
                remedy="add 'auc_units' to the noael_exposure object",
            )
        valid_auc_unit_set = VALID_CONC_UNITS | VALID_AUC_UNITS
        if auc_units not in valid_auc_unit_set:
            hint = suggest_match(auc_units, valid_auc_unit_set)
            suggestion = f" Did you mean {hint!r}?" if hint else ""
            raise Refusal(
                f"unrecognised noael_exposure.auc_units: {auc_units!r}.{suggestion}",
                detail=f"accepted values: {sorted(valid_auc_unit_set)}",
                remedy=f"use one of: {', '.join(sorted(valid_auc_unit_set))}",
            )

    if cmax is not None:
        if not isinstance(cmax, (int, float)) or isinstance(cmax, bool):
            raise SchemaError(
                f"noael_exposure.cmax must be a number, "
                f"got {type(cmax).__name__}: {cmax!r}",
            )
        if cmax <= 0:
            raise SchemaError(
                f"noael_exposure.cmax must be positive, got {cmax}",
            )
        cmax_units = exposure.get("cmax_units")
        if not cmax_units:
            raise SchemaError(
                "noael_exposure.cmax_units is required when cmax is present",
                remedy="add 'cmax_units' to the noael_exposure object",
            )
        if cmax_units not in VALID_CONC_UNITS:
            hint = suggest_match(cmax_units, VALID_CONC_UNITS)
            suggestion = f" Did you mean {hint!r}?" if hint else ""
            raise Refusal(
                f"unrecognised noael_exposure.cmax_units: {cmax_units!r}.{suggestion}",
                detail=f"accepted values: {sorted(VALID_CONC_UNITS)}",
                remedy=f"use one of: {', '.join(sorted(VALID_CONC_UNITS))}",
            )


# ---------------------------------------------------------------------------
# Safety-pharm sub-object field specs (name → expected type)
# ---------------------------------------------------------------------------

_CARDIOVASCULAR_FIELDS: dict[str, type] = {
    "qtc_prolongation": bool,
    "qtc_details": str,
    "blood_pressure_effect": str,
    "heart_rate_effect": str,
    "ecg_findings": str,
}

_RESPIRATORY_FIELDS: dict[str, type] = {
    "tidal_volume_effect": str,
    "respiratory_rate_effect": str,
    "summary": str,
}

_CNS_FIELDS: dict[str, type] = {
    "irwin_fob_summary": str,
    "findings": str,
}


def _validate_sub_object(
    doc: dict[str, Any],
    field_name: str,
    allowed_fields: dict[str, type],
) -> None:
    """Validate a structured optional sub-object (cardiovascular, respiratory, cns).

    If *field_name* is present in *doc*, checks that:
      - it is a dict
      - every key is in *allowed_fields*
      - every value has the expected type
    """
    obj = doc.get(field_name)
    if obj is None:
        return

    if not isinstance(obj, dict):
        raise SchemaError(
            f"'{field_name}' must be an object when present, got {type(obj).__name__}",
        )

    unknown = set(obj) - set(allowed_fields)
    if unknown:
        raise SchemaError(
            f"'{field_name}' contains unknown fields: {sorted(unknown)}",
            remedy=f"accepted fields: {sorted(allowed_fields)}",
        )

    for key, expected_type in allowed_fields.items():
        val = obj.get(key)
        if val is None:
            continue
        if not isinstance(val, expected_type) or (
            expected_type is not bool and isinstance(val, bool)
        ):
            raise SchemaError(
                f"'{field_name}.{key}' must be a {expected_type.__name__}, "
                f"got {type(val).__name__}: {val!r}",
            )


def _validate_safety_pharm(doc: dict[str, Any]) -> None:
    """Validate a tox-safety-pharm document exhaustively.

    Checks all required fields, types, value sets, and sub-object structure.
    Raises ``SchemaError`` for structural/type issues and ``Refusal`` for
    semantic issues.
    """
    # Schema tag
    if doc.get("schema") != "dde.tox-safety-pharm.v1":
        raise Refusal(
            "unsupported or missing schema tag",
            detail=f"expected 'dde.tox-safety-pharm.v1', got {doc.get('schema')!r}",
            remedy="ensure the input JSON contains "
            '\'"schema": "dde.tox-safety-pharm.v1"\'',
        )

    # compound_id — required, non-empty string
    compound_id = doc.get("compound_id")
    if not compound_id or not isinstance(compound_id, str):
        raise SchemaError(
            "required field 'compound_id' is missing, null, or not a string",
            remedy="add a non-empty 'compound_id' string to the input JSON",
        )

    # herg_ic50 — required, positive number
    herg_ic50 = doc.get("herg_ic50")
    if herg_ic50 is None:
        raise SchemaError(
            "required field 'herg_ic50' is missing or null",
            remedy="add 'herg_ic50' as a positive number",
        )
    if not isinstance(herg_ic50, (int, float)) or isinstance(herg_ic50, bool):
        raise SchemaError(
            f"'herg_ic50' must be a number, got {type(herg_ic50).__name__}: {herg_ic50!r}",
            remedy="provide 'herg_ic50' as a positive number",
        )
    if herg_ic50 <= 0:
        raise SchemaError(
            f"'herg_ic50' must be positive, got {herg_ic50}",
            remedy="provide a positive number for the hERG IC50",
        )

    # herg_ic50_units — required, must be in VALID_CONC_UNITS
    herg_units = doc.get("herg_ic50_units")
    if not herg_units or not isinstance(herg_units, str):
        raise SchemaError(
            "required field 'herg_ic50_units' is missing, null, or not a string",
            remedy="add 'herg_ic50_units' to the input JSON",
        )
    if herg_units not in VALID_CONC_UNITS:
        hint = suggest_match(herg_units, VALID_CONC_UNITS)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised herg_ic50_units: {herg_units!r}.{suggestion}",
            detail=f"accepted values: {sorted(VALID_CONC_UNITS)}",
            remedy=f"use one of: {', '.join(sorted(VALID_CONC_UNITS))}",
        )

    # Structured optional sub-objects
    _validate_sub_object(doc, "cardiovascular", _CARDIOVASCULAR_FIELDS)
    _validate_sub_object(doc, "respiratory", _RESPIRATORY_FIELDS)
    _validate_sub_object(doc, "cns", _CNS_FIELDS)

    # notes — optional string
    notes = doc.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise SchemaError(
            f"'notes' must be a string when present, got {type(notes).__name__}",
        )


# ---------------------------------------------------------------------------
# Genotox assay-result validation
# ---------------------------------------------------------------------------

_VALID_METABOLIC_ACTIVATION = {"+S9", "-S9", "both"}

_GENOTOX_ASSAY_OPTIONAL_FIELDS: dict[str, type] = {
    "dose_range": str,
    "cell_line": str,
    "species": str,
    "details": str,
}


def _validate_assay(idx: int, assay: Any) -> None:
    """Validate a single assay-result object inside the genotox assays array."""
    if not isinstance(assay, dict):
        raise SchemaError(f"assays[{idx}] must be an object")

    # type — required, must be in VALID_GENOTOX_ASSAY_TYPES
    atype = assay.get("type")
    if not atype or not isinstance(atype, str):
        raise SchemaError(
            f"assays[{idx}].type is required and must be a non-empty string",
            remedy="add 'type' to each assay object",
        )
    if atype not in VALID_GENOTOX_ASSAY_TYPES:
        hint = suggest_match(atype, VALID_GENOTOX_ASSAY_TYPES)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised assay type in assays[{idx}]: {atype!r}.{suggestion}",
            detail=f"accepted values: {sorted(VALID_GENOTOX_ASSAY_TYPES)}",
            remedy=f"use one of: {', '.join(sorted(VALID_GENOTOX_ASSAY_TYPES))}",
        )

    # result — required, must be in VALID_GENOTOX_RESULTS
    result = assay.get("result")
    if not result or not isinstance(result, str):
        raise SchemaError(
            f"assays[{idx}].result is required and must be a non-empty string",
            remedy="add 'result' to each assay object",
        )
    if result not in VALID_GENOTOX_RESULTS:
        hint = suggest_match(result, VALID_GENOTOX_RESULTS)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised assay result in assays[{idx}]: {result!r}.{suggestion}",
            detail=f"accepted values: {sorted(VALID_GENOTOX_RESULTS)}",
            remedy=f"use one of: {', '.join(sorted(VALID_GENOTOX_RESULTS))}",
        )

    # metabolic_activation — required, one of +S9, -S9, both
    ma = assay.get("metabolic_activation")
    if not ma or not isinstance(ma, str):
        raise SchemaError(
            f"assays[{idx}].metabolic_activation is required and must be a string",
            remedy="add 'metabolic_activation' to each assay object",
        )
    if ma not in _VALID_METABOLIC_ACTIVATION:
        hint = suggest_match(ma, _VALID_METABOLIC_ACTIVATION)
        suggestion = f" Did you mean {hint!r}?" if hint else ""
        raise Refusal(
            f"unrecognised metabolic_activation in assays[{idx}]: {ma!r}.{suggestion}",
            detail=f"accepted values: {sorted(_VALID_METABOLIC_ACTIVATION)}",
            remedy=f"use one of: {', '.join(sorted(_VALID_METABOLIC_ACTIVATION))}",
        )

    # Optional string fields
    for field, expected_type in _GENOTOX_ASSAY_OPTIONAL_FIELDS.items():
        val = assay.get(field)
        if val is not None and not isinstance(val, expected_type):
            raise SchemaError(
                f"assays[{idx}].{field} must be a {expected_type.__name__} when "
                f"present, got {type(val).__name__}: {val!r}",
            )


def _validate_genotox(doc: dict[str, Any]) -> None:
    """Validate a tox-genotox document exhaustively.

    Checks all required fields, types, value sets, and the
    battery_complete cross-field constraint.  Raises ``SchemaError`` for
    structural/type issues and ``Refusal`` for semantic issues.
    """
    # Schema tag
    if doc.get("schema") != "dde.tox-genotox.v1":
        raise Refusal(
            "unsupported or missing schema tag",
            detail=f"expected 'dde.tox-genotox.v1', got {doc.get('schema')!r}",
            remedy='ensure the input JSON contains \'"schema": "dde.tox-genotox.v1"\'',
        )

    # compound_id — required, non-empty string
    compound_id = doc.get("compound_id")
    if not compound_id or not isinstance(compound_id, str):
        raise SchemaError(
            "required field 'compound_id' is missing, null, or not a string",
            remedy="add a non-empty 'compound_id' string to the input JSON",
        )

    # assays — required, non-empty array of assay-result objects
    assays = doc.get("assays")
    if not isinstance(assays, list) or not assays:
        raise SchemaError(
            "'assays' must be a non-empty array",
            remedy="provide at least one assay result object",
        )

    for i, assay in enumerate(assays):
        _validate_assay(i, assay)

    # battery_complete — required boolean
    bc = doc.get("battery_complete")
    if bc is None:
        raise SchemaError(
            "required field 'battery_complete' is missing or null",
            remedy="add 'battery_complete' as a boolean",
        )
    if not isinstance(bc, bool):
        raise SchemaError(
            f"'battery_complete' must be a boolean, got {type(bc).__name__}: {bc!r}",
            remedy="provide 'battery_complete' as true or false",
        )

    # Cross-field: if battery_complete is true, validate the battery
    if bc:
        assay_types = {a["type"] for a in assays}
        has_ames = "ames" in assay_types
        has_in_vitro_clasto = bool(
            assay_types & {"chromosomal_aberration", "micronucleus_in_vitro"}
        )
        has_in_vivo_mn = "micronucleus_in_vivo" in assay_types

        missing: list[str] = []
        if not has_ames:
            missing.append("ames")
        if not has_in_vitro_clasto:
            missing.append(
                "in vitro clastogenicity (chromosomal_aberration or micronucleus_in_vitro)"
            )
        if not has_in_vivo_mn:
            missing.append("micronucleus_in_vivo")

        if missing:
            raise Refusal(
                "battery_complete is true but the assay set is incomplete",
                detail=f"missing: {', '.join(missing)}",
                remedy="either set battery_complete to false or add the missing "
                "assay types to complete the ICH S2(R1) standard battery",
            )

    # notes — optional string
    notes = doc.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise SchemaError(
            f"'notes' must be a string when present, got {type(notes).__name__}",
        )


# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group()
def tox() -> None:
    """Preclinical toxicology data processing and assessment."""


# ---------------------------------------------------------------------------
# tox ingest
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-schema ingest helpers: normalisation, sidecar notes, output naming
# ---------------------------------------------------------------------------


def _ingest_repeat_dose(
    doc: dict[str, Any],
    input_path: Path,
    sidecar: provenance.Sidecar,
) -> tuple[dict[str, Any], str, str]:
    """Return (normalised_doc, sanitised_id, artifact_suffix) for repeat-dose."""
    study_id = _sanitize_id(doc["study_id"])

    normalised: dict[str, Any] = {
        "schema": doc["schema"],
        "study_id": study_id,
        "species": doc["species"],
        "strain": doc.get("strain"),
        "route": doc["route"],
        "duration_days": doc["duration_days"],
        "dose_groups": doc["dose_groups"],
        "noael_mg_kg": doc["noael_mg_kg"],
        "noael_basis": doc["noael_basis"],
        "noael_exposure": doc.get("noael_exposure"),
        "loael_mg_kg": doc.get("loael_mg_kg"),
        "glp_status": doc.get("glp_status", "not_stated"),
        "body_weight_kg": doc.get("body_weight_kg"),
        "notes": doc.get("notes"),
    }

    # Sidecar notes
    sidecar.note("noael_study_id", doc["study_id"])
    sidecar.note("noael_species", doc["species"])
    sidecar.note("noael_duration_days", doc["duration_days"])
    sidecar.note("noael_basis", doc["noael_basis"])
    sidecar.note("noael_mg_kg", doc["noael_mg_kg"])
    sidecar.note("glp_status_reported", doc.get("glp_status", "not_stated"))

    # Note non-standard species for downstream review
    if doc["species"] not in VALID_SPECIES:
        sidecar.warn(
            f"non-standard species: {doc['species']!r} is not in the recognised "
            f"set ({', '.join(sorted(VALID_SPECIES))}); accepted but may require "
            "manual review",
        )

    return normalised, study_id, "tox-repeat-dose"


def _ingest_safety_pharm(
    doc: dict[str, Any],
    input_path: Path,
    sidecar: provenance.Sidecar,
) -> tuple[dict[str, Any], str, str]:
    """Return (normalised_doc, sanitised_id, artifact_suffix) for safety-pharm."""
    compound_id = _sanitize_id(doc["compound_id"])

    normalised: dict[str, Any] = {
        "schema": doc["schema"],
        "compound_id": compound_id,
        "herg_ic50": doc["herg_ic50"],
        "herg_ic50_units": doc["herg_ic50_units"],
        "cardiovascular": doc.get("cardiovascular"),
        "respiratory": doc.get("respiratory"),
        "cns": doc.get("cns"),
        "notes": doc.get("notes"),
    }

    # Sidecar notes
    sidecar.note("herg_ic50", doc["herg_ic50"])
    sidecar.note("herg_ic50_units", doc["herg_ic50_units"])

    return normalised, compound_id, "tox-safety-pharm"


def _ingest_genotox(
    doc: dict[str, Any],
    input_path: Path,
    sidecar: provenance.Sidecar,
) -> tuple[dict[str, Any], str, str]:
    """Return (normalised_doc, sanitised_id, artifact_suffix) for genotox."""
    compound_id = _sanitize_id(doc["compound_id"])

    normalised: dict[str, Any] = {
        "schema": doc["schema"],
        "compound_id": compound_id,
        "assays": doc["assays"],
        "battery_complete": doc["battery_complete"],
        "notes": doc.get("notes"),
    }

    # Sidecar notes
    sidecar.note("battery_complete", doc["battery_complete"])
    sidecar.note("n_assays", len(doc["assays"]))
    assay_types = sorted({a["type"] for a in doc["assays"]})
    sidecar.note("assay_types", assay_types)

    return normalised, compound_id, "tox-genotox"


# ---------------------------------------------------------------------------
# Genotox battery scoring — ICH S2(R1) weight-of-evidence
# ---------------------------------------------------------------------------


def _classify_genotox_battery(assays: list[dict]) -> tuple[str, bool]:
    """Classify battery result. Returns (verdict, weight_of_evidence_needed).

    Verdicts:
      "negative"          — all negative, no concern
      "positive"          — all positive, genotoxic concern
      "equivocal"         — mixed, in vitro positive only, in vivo negative
      "concern"           — in vivo positive with in vitro negatives
      "positive_concern"  — preponderance positive (2+ of 3)
    """
    results = [a["result"] for a in assays]

    n_positive = results.count("positive")
    n_negative = results.count("negative")
    n_equivocal = results.count("equivocal")

    # All negative
    if n_positive == 0 and n_equivocal == 0:
        return "negative", False

    # All positive
    if n_negative == 0 and n_equivocal == 0:
        return "positive", False

    # Mixed results — weight of evidence needed
    in_vivo_positive = any(
        a["result"] == "positive" and a["type"] == "micronucleus_in_vivo"
        for a in assays
    )

    in_vitro_positive_only = n_positive >= 1 and not in_vivo_positive

    if n_positive >= 2:
        return "positive_concern", True
    elif in_vivo_positive:
        return "concern", True
    elif in_vitro_positive_only:
        return "equivocal", True
    else:
        # Equivocal results without clear positives
        return "equivocal", True


# Schema dispatch table: schema tag → (validator, ingest helper)
_INGEST_DISPATCH = {
    "dde.tox-repeat-dose.v1": (_validate_repeat_dose, _ingest_repeat_dose),
    "dde.tox-safety-pharm.v1": (_validate_safety_pharm, _ingest_safety_pharm),
    "dde.tox-genotox.v1": (_validate_genotox, _ingest_genotox),
}


@tox.command("ingest")
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
    """Parse and store a tox study data file.

    Reads a JSON file matching one of the three tox study schemas,
    validates it, and writes normalised study data with a provenance
    sidecar under ``raw/tox/``.

    Supported schemas:
      - ``dde.tox-repeat-dose.v1``
      - ``dde.tox-safety-pharm.v1``
      - ``dde.tox-genotox.v1``

    \b
    Outputs per schema:
      {id}.tox-repeat-dose.json / .meta.json
      {id}.tox-safety-pharm.json / .meta.json
      {id}.tox-genotox.json / .meta.json
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- read and validate ---
    input_path = resolve_artifact(state, input_file, "tox study input")
    doc = provenance.read_json(input_path, "tox study input")

    if not isinstance(doc, dict):
        raise SchemaError("tox study input must be a JSON object")

    # Dispatch on schema tag
    schema = doc.get("schema")
    dispatch = _INGEST_DISPATCH.get(schema)  # type: ignore[arg-type]
    if dispatch is None:
        raise Refusal(
            f"unsupported or missing schema tag: {schema!r}",
            detail=f"expected one of: {', '.join(sorted(_INGEST_DISPATCH))}",
            remedy="ensure the input JSON contains a supported schema tag",
        )

    validate_fn, ingest_fn = dispatch
    validate_fn(doc)

    # --- schema-specific normalisation, ID extraction, sidecar notes ---
    sidecar = provenance.Sidecar(
        tool=ARTIFACT_CLASS,
        subcommand="ingest",
        endpoint=None,
        parameters={"input_file": input_path.name},
    )
    sidecar.note("input_sha256", provenance.sha256_file(input_path))

    normalised, record_id, suffix = ingest_fn(doc, input_path, sidecar)

    # Strip None-valued optional fields for a clean artifact
    normalised = {k: v for k, v in normalised.items() if v is not None}

    # --- write normalised artifact ---
    artifact_path = target_dir / f"{record_id}.{suffix}.json"
    artifact_path.write_text(json.dumps(normalised, indent=2) + "\n", encoding="utf-8")

    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(target_dir / f"{record_id}.{suffix}.meta.json")

    # --- emit summary ---
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"ingested {record_id} (schema: {schema})")
    emit.flush()


# ---------------------------------------------------------------------------
# tox genotox — ICH S2(R1) weight-of-evidence assessment
# ---------------------------------------------------------------------------


def _build_weight_of_evidence_basis(assays: list[dict]) -> str:
    """Build descriptive weight-of-evidence basis text for mixed battery results.

    Describes which assays were positive and negative, mentions metabolic
    activation context for positive assays, and references ICH S2(R1) for
    the in vitro positive / in vivo negative pattern.
    """
    positive_assays = [a for a in assays if a["result"] == "positive"]
    negative_assays = [a for a in assays if a["result"] == "negative"]

    parts: list[str] = []

    # Describe positive assays with metabolic activation context
    for a in positive_assays:
        # Strip in_vivo/in_vitro suffix since the prefix carries that info
        if a["type"] in ("micronucleus_in_vivo", "micronucleus_in_vitro"):
            atype = "micronucleus"
        else:
            atype = a["type"].replace("_", " ")
        is_in_vivo = a["type"] == "micronucleus_in_vivo"
        prefix = "In vivo" if is_in_vivo else "In vitro"
        ma = a["metabolic_activation"]
        if ma == "both":
            if is_in_vivo:
                parts.append(f"{prefix} {atype} positive")
            else:
                parts.append(
                    f"{prefix} {atype} positive with and without S9 activation"
                )
        elif ma in ("+S9", "-S9"):
            parts.append(f"{prefix} {atype} positive with {ma} activation")
        else:
            parts.append(f"{atype} positive")

    # Describe negative assays
    if negative_assays:
        if any(a["type"] == "micronucleus_in_vivo" for a in negative_assays):
            parts.append("in vivo micronucleus negative")

    # ICH S2(R1) reference for in vitro positive / in vivo negative pattern
    has_in_vitro_pos = any(
        a["result"] == "positive" and a["type"] != "micronucleus_in_vivo"
        for a in assays
    )
    has_in_vivo_neg = any(
        a["result"] == "negative" and a["type"] == "micronucleus_in_vivo"
        for a in assays
    )
    if has_in_vitro_pos and has_in_vivo_neg:
        parts.append(
            "In vitro positive may reflect conditions not achieved in vivo (ICH S2(R1))"
        )

    # If no specific ICH pattern but still mixed, reference ICH S2(R1) generally
    if not (has_in_vitro_pos and has_in_vivo_neg):
        parts.append(
            "Weight-of-evidence assessment per ICH S2(R1) due to mixed battery results"
        )

    return "; ".join(parts) + "."


@tox.command("genotox")
@click.argument("input_file", type=click.Path(exists=True))
@out_option
@output_options
@pass_state
def genotox_cmd(
    state: AppState,
    input_file: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """ICH S2(R1) weight-of-evidence assessment of a genotoxicity battery.

    Reads a ``.tox-genotox.json`` artifact from ``tox ingest`` and produces
    a weight-of-evidence assessment classifying the battery result.

    \b
    Outputs:
      {compound_id}.tox-genotox-assessment.json
      {compound_id}.tox-genotox-assessment.meta.json
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- read and validate ---
    input_path = resolve_artifact(state, input_file, "genotox artifact")
    doc = provenance.read_json(input_path, "genotox artifact")

    if not isinstance(doc, dict):
        raise SchemaError("genotox artifact must be a JSON object")

    if doc.get("schema") != "dde.tox-genotox.v1":
        raise Refusal(
            "unsupported or missing schema tag",
            detail=f"expected 'dde.tox-genotox.v1', got {doc.get('schema')!r}",
            remedy="provide a .tox-genotox.json artifact produced by 'tox ingest'",
        )

    compound_id = doc["compound_id"]
    assays = doc["assays"]
    battery_complete = doc["battery_complete"]

    # --- classify battery ---
    verdict, weight_of_evidence_needed = _classify_genotox_battery(assays)

    # --- build assay summary ---
    assay_summary = [
        {
            "type": a["type"],
            "result": a["result"],
            "metabolic_activation": a["metabolic_activation"],
        }
        for a in assays
    ]

    positive_assays = [a["type"] for a in assays if a["result"] == "positive"]
    negative_assays = [a["type"] for a in assays if a["result"] == "negative"]

    # --- build assessment artifact ---
    assessment: dict[str, Any] = {
        "schema": "dde.tox-genotox-assessment.v1",
        "compound_id": compound_id,
        "battery_complete": battery_complete,
        "assay_summary": assay_summary,
        "verdict": verdict,
        "weight_of_evidence": weight_of_evidence_needed,
        "positive_assays": positive_assays,
        "negative_assays": negative_assays,
    }

    if weight_of_evidence_needed:
        assessment["weight_of_evidence_basis"] = _build_weight_of_evidence_basis(assays)

    # --- sidecar ---
    sidecar = provenance.Sidecar(
        tool=ARTIFACT_CLASS,
        subcommand="genotox",
        endpoint=None,
        parameters={"input_file": input_path.name},
    )
    sidecar.note("input_sha256", provenance.sha256_file(input_path))

    # Fire relay only on mixed battery results
    if weight_of_evidence_needed:
        relay_message = (
            f"Genotoxicity battery for {compound_id} produced mixed results "
            f"(verdict: {verdict}). Weight-of-evidence assessment applied per "
            f"ICH S2(R1). Positive assays: {', '.join(positive_assays) or 'none'}."
        )
        sidecar.warn(relay_message, code="tox.genotox_weight_of_evidence")

    # --- write assessment artifact ---
    safe_id = _sanitize_id(compound_id)
    artifact_path = target_dir / f"{safe_id}.tox-genotox-assessment.json"
    artifact_path.write_text(json.dumps(assessment, indent=2) + "\n", encoding="utf-8")

    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(
        target_dir / f"{safe_id}.tox-genotox-assessment.meta.json"
    )

    # --- emit paths ---
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"genotox assessment: {safe_id} — verdict: {verdict}")
    emit.flush()


# ---------------------------------------------------------------------------
# tox margins — dose context and same-study detection (#137)
# ---------------------------------------------------------------------------

VALID_DOSE_CONTEXTS = {
    "animal_limit_dose",
    "animal_therapeutic",
    "human_projected",
    "human_observed",
}


def _detect_same_study(
    tox_doc: dict[str, Any],
    pk_doc: dict[str, Any],
    tox_path: Path,
    pk_path: Path,
) -> str | None:
    """Detect when NOAEL and PK exposures derive from the same study.

    Returns a human-readable reason string if same-study is detected,
    or ``None`` if the two artifacts appear to come from different studies.

    Detection criteria (any one is sufficient):
      1. Same ``study_id`` field in both artifacts.
      2. Same source file (tox and PK path names share a study stem).
      3. Same species + route + dose combination.
    """
    tox_study_id = tox_doc.get("study_id", "")
    pk_study_id = pk_doc.get("study_id", "")

    # Criterion 1: matching study_id
    if tox_study_id and pk_study_id and tox_study_id == pk_study_id:
        return f"study_id={tox_study_id!r}"

    # Criterion 2: matching file stem (same source file)
    if tox_path.stem and pk_path.stem and tox_path.stem == pk_path.stem:
        return f"file={tox_path.name!r}"

    # Criterion 3: same species + route + dose
    tox_species = tox_doc.get("species", "").lower()
    pk_species = pk_doc.get("species", "").lower()
    tox_route = tox_doc.get("route", "").lower()
    pk_route = pk_doc.get("route", "").lower()
    tox_noael = tox_doc.get("noael_mg_kg")
    pk_dose = pk_doc.get("dose_mg_kg")
    if pk_dose is None:
        # PK NCA artifacts store dose at top level via study;
        # fall back to parameters if present
        pk_params = pk_doc.get("parameters", {})
        pk_dose = pk_params.get("dose_mg_kg")

    if (
        tox_species
        and pk_species
        and tox_species == pk_species
        and tox_route
        and pk_route
        and tox_route == pk_route
        and tox_noael is not None
        and pk_dose is not None
        and float(tox_noael) == float(pk_dose)
    ):
        return f"species={tox_species}, route={tox_route}, dose={tox_noael} mg/kg"

    return None


def _compute_ti_values(
    noael_exposure: dict[str, Any],
    pk_cmax: float | None,
    pk_cmax_units: str | None,
    pk_auc: float | None,
    pk_auc_units: str | None,
    margin_notes: list[str],
) -> dict[str, float]:
    """Compute TI values from NOAEL exposure vs PK exposure.

    Returns a dict of computed TI values (may be empty).
    Appends diagnostic messages to *margin_notes* for unit mismatches.
    """
    ti_values: dict[str, float] = {}

    # Cmax-based TI
    noael_cmax = noael_exposure.get("cmax")
    noael_cmax_units = noael_exposure.get("cmax_units")
    if noael_cmax is not None and pk_cmax is not None:
        if noael_cmax_units != pk_cmax_units:
            margin_notes.append(
                f"Cmax-based TI not computed: unit mismatch between "
                f"tox NOAEL Cmax ({noael_cmax_units}) and "
                f"PK Cmax ({pk_cmax_units})"
            )
        elif pk_cmax <= 0:
            raise SchemaError(
                f"PK Cmax must be positive, got {pk_cmax}",
                remedy="ensure the PK NCA artifact was produced by `dde pk nca`",
            )
        else:
            ti_values["ti_cmax"] = round(noael_cmax / pk_cmax, 4)

    # AUC-based TI
    noael_auc = noael_exposure.get("auc")
    noael_auc_units = noael_exposure.get("auc_units")
    if noael_auc is not None and pk_auc is not None:
        if (
            noael_auc_units is None
            or pk_auc_units is None
            or _normalize_auc_units(noael_auc_units)
            != _normalize_auc_units(pk_auc_units)
        ):
            margin_notes.append(
                f"AUC-based TI not computed: unit mismatch between "
                f"tox NOAEL AUC ({noael_auc_units}) and "
                f"PK AUC ({pk_auc_units})"
            )
        elif pk_auc <= 0:
            raise SchemaError(
                f"PK AUC must be positive, got {pk_auc}",
                remedy="ensure the PK NCA artifact was produced by `dde pk nca`",
            )
        else:
            ti_values["ti_auc"] = round(noael_auc / pk_auc, 4)

    return ti_values


@tox.command("margins")
@click.argument("tox_file", type=click.Path(exists=True))
@click.argument("pk_file", type=click.Path(exists=True))
@click.option(
    "--herg",
    "herg_file",
    type=click.Path(exists=True),
    default=None,
    help="Safety-pharm artifact for hERG margin calculation.",
)
@click.option(
    "--clinical-pk",
    "clinical_pk_file",
    type=click.Path(exists=True),
    default=None,
    help=(
        "PK NCA artifact representing projected clinical (human) exposure. "
        "When provided, computes both an animal self-comparison margin and "
        "a clinical therapeutic index; ICH M3(R2) thresholds apply only to "
        "the clinical TI."
    ),
)
@click.option(
    "--dose-context",
    "dose_context",
    type=click.Choice(sorted(VALID_DOSE_CONTEXTS)),
    default=None,
    help=(
        "Label what the primary PK input represents. Overridden by a "
        "dose_context field in the PK artifact when present."
    ),
)
@out_option
@output_options
@pass_state
def margins_cmd(
    state: AppState,
    tox_file: str,
    pk_file: str,
    herg_file: str | None,
    clinical_pk_file: str | None,
    dose_context: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Cross-artifact therapeutic index and hERG margin calculation.

    Reads a ``.tox-repeat-dose.json`` artifact (from ``tox ingest``) and
    a ``.pk-nca.json`` artifact (from ``pk nca``), computes safety margins
    (therapeutic index from NOAEL exposure vs PK exposure), and records
    cross-artifact provenance.

    When ``--clinical-pk`` is provided, computes two margins:

    \b
      animal_margin   NOAEL_exp / animal_PK_exp (self-comparison)
      clinical_ti     NOAEL_exp / clinical_PK_exp (therapeutic index)

    ICH M3(R2) pass/fail thresholds apply only to ``clinical_ti``.

    When both NOAEL and PK exposures come from the same study and no
    ``--clinical-pk`` is provided, the verdict is ``indeterminate``
    rather than ``flagged``, because the comparison is meaningless
    without a clinical reference exposure.

    When ``--herg`` is provided with a safety-pharm artifact, also computes
    the hERG safety margin (IC50 / therapeutic Cmax).

    \b
    Outputs:
      {study_id}.tox-margins.json       -- margin calculations
      {study_id}.tox-margins.meta.json  -- provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # --- 1. Read and validate tox repeat-dose artifact ---
    tox_path = resolve_artifact(state, tox_file, "tox repeat-dose artifact")
    tox_doc = provenance.read_json(tox_path, "tox repeat-dose artifact")

    if not isinstance(tox_doc, dict):
        raise SchemaError("tox repeat-dose artifact must be a JSON object")

    if tox_doc.get("schema") != "dde.tox-repeat-dose.v1":
        raise Refusal(
            "unsupported or missing schema tag on tox file",
            detail=(
                f"expected 'dde.tox-repeat-dose.v1', got {tox_doc.get('schema')!r}"
            ),
            remedy="use a file produced by `dde tox ingest`",
        )

    study_id = _sanitize_id(tox_doc["study_id"])
    species = tox_doc["species"]
    noael_mg_kg = tox_doc["noael_mg_kg"]
    noael_exposure = tox_doc.get("noael_exposure")
    duration_days = tox_doc["duration_days"]

    # --- 2. Read and validate PK NCA artifact ---
    pk_path = resolve_artifact(state, pk_file, "PK NCA artifact")
    pk_doc = provenance.read_json(pk_path, "PK NCA artifact")

    if not isinstance(pk_doc, dict):
        raise SchemaError("PK NCA artifact must be a JSON object")

    if pk_doc.get("schema") != "dde.pk-nca.v1":
        raise Refusal(
            "unsupported or missing schema tag on PK file",
            detail=(f"expected 'dde.pk-nca.v1', got {pk_doc.get('schema')!r}"),
            remedy="use a file produced by `dde pk nca`",
        )

    pk_params = pk_doc.get("parameters", {})
    pk_cmax = pk_params.get("cmax")
    pk_cmax_units = pk_params.get("cmax_units")
    pk_auc_0_inf = pk_params.get("auc_0_inf")
    pk_auc_0_t = pk_params.get("auc_0_t")
    pk_auc_units = pk_params.get("auc_units")
    pk_species = pk_doc.get("species")

    # Use auc_0_inf if available, fall back to auc_0_t
    pk_auc = pk_auc_0_inf if pk_auc_0_inf is not None else pk_auc_0_t
    pk_auc_label = "auc_0_inf" if pk_auc_0_inf is not None else "auc_0_t"

    # --- 2b. Resolve dose context (Item 4 → Item 1 cascade) ---
    # Priority: PK artifact field > CLI option > None
    resolved_dose_context = pk_doc.get("dose_context") or dose_context

    # --- 2c. Read clinical PK artifact (optional, Item 1) ---
    clinical_pk_doc: dict[str, Any] | None = None
    clinical_pk_path: Path | None = None
    if clinical_pk_file is not None:
        clinical_pk_path = resolve_artifact(
            state, clinical_pk_file, "clinical PK NCA artifact"
        )
        clinical_pk_doc = provenance.read_json(
            clinical_pk_path, "clinical PK NCA artifact"
        )
        if not isinstance(clinical_pk_doc, dict):
            raise SchemaError("clinical PK NCA artifact must be a JSON object")
        if clinical_pk_doc.get("schema") != "dde.pk-nca.v1":
            raise Refusal(
                "unsupported or missing schema tag on clinical PK file",
                detail=(
                    f"expected 'dde.pk-nca.v1', got {clinical_pk_doc.get('schema')!r}"
                ),
                remedy="use a file produced by `dde pk nca`",
            )

    # --- 3. Same-study detection (Item 2) ---
    same_study_reason = _detect_same_study(tox_doc, pk_doc, tox_path, pk_path)
    is_indeterminate = False
    indeterminate_reason: str | None = None

    if same_study_reason and clinical_pk_doc is None:
        # Same study detected and no clinical reference provided
        is_indeterminate = True
        indeterminate_reason = (
            f"Both NOAEL and PK exposures appear to derive from the same "
            f"study ({same_study_reason}). A therapeutic index requires "
            f"comparison to projected clinical exposure. Provide "
            f"--clinical-pk with human projected exposure."
        )
    # --- 4. Compute TI margins ---
    margins: dict[str, float] | None = None
    margin_note: str | None = None
    margin_notes: list[str] = []

    # Animal margin (NOAEL_exp / animal_PK_exp)
    animal_margin: dict[str, float] | None = None
    # Clinical TI (NOAEL_exp / clinical_PK_exp)
    clinical_ti: dict[str, float] | None = None

    if noael_exposure is not None and not is_indeterminate:
        ti_values = _compute_ti_values(
            noael_exposure,
            pk_cmax,
            pk_cmax_units,
            pk_auc,
            pk_auc_units,
            margin_notes,
        )

        if clinical_pk_doc is not None:
            # Dual-margin mode: animal_margin + clinical_ti
            animal_margin = ti_values if ti_values else None

            # Compute clinical TI from clinical PK
            clin_params = clinical_pk_doc.get("parameters", {})
            clin_cmax = clin_params.get("cmax")
            clin_cmax_units = clin_params.get("cmax_units")
            clin_auc_0_inf = clin_params.get("auc_0_inf")
            clin_auc_0_t = clin_params.get("auc_0_t")
            clin_auc_units = clin_params.get("auc_units")
            clin_auc = clin_auc_0_inf if clin_auc_0_inf is not None else clin_auc_0_t

            clinical_margin_notes: list[str] = []
            clinical_ti = (
                _compute_ti_values(
                    noael_exposure,
                    clin_cmax,
                    clin_cmax_units,
                    clin_auc,
                    clin_auc_units,
                    clinical_margin_notes,
                )
                or None
            )
            if clinical_margin_notes:
                margin_notes.extend(f"clinical: {n}" for n in clinical_margin_notes)

            # For backward compat, margins contains clinical_ti values
            margins = clinical_ti
        else:
            # Single-margin mode (original behaviour)
            margins = ti_values if ti_values else None

        if margins is None and not is_indeterminate:
            margin_note = (
                "NOAEL exposure (TK data) is present but neither Cmax nor "
                "AUC could be matched with PK parameters for TI calculation"
            )
    elif noael_exposure is None and not is_indeterminate:
        margin_note = (
            "NOAEL exposure (TK data) not provided; dose-based NOAEL "
            "recorded but therapeutic index cannot be computed without "
            "measured exposure at the NOAEL dose"
        )

    # --- Build pk_source record ---
    pk_source: dict[str, Any] = {
        "file": pk_path.name,
        "species": pk_species,
        "cmax": pk_cmax,
        "cmax_units": pk_cmax_units,
    }
    if pk_auc is not None:
        pk_source[pk_auc_label] = pk_auc
        pk_source["auc_units"] = pk_auc_units
    if resolved_dose_context is not None:
        pk_source["dose_context"] = resolved_dose_context

    # --- Build output artifact ---
    record: dict[str, Any] = {
        "schema": "dde.tox-margins.v1",
        "study_id": study_id,
        "species": species,
        "noael_mg_kg": noael_mg_kg,
        "noael_exposure": noael_exposure,
        "pk_source": pk_source,
    }

    if is_indeterminate:
        record["verdict"] = "indeterminate"
        record["verdict_reason"] = indeterminate_reason
        record["margins"] = None
        if same_study_reason:
            record["same_study_detected"] = same_study_reason
    else:
        record["margins"] = margins
        if animal_margin is not None:
            record["animal_margin"] = animal_margin
        if clinical_ti is not None:
            record["clinical_ti"] = clinical_ti
        if margins is not None:
            record["margin_calculation_method"] = "linear_exposure"
            assumptions = []
            if clinical_pk_doc is not None:
                assumptions.append(
                    "ICH M3(R2) pass/fail thresholds are applied only to "
                    "clinical_ti (NOAEL_exp / clinical_PK_exp), never to "
                    "animal_margin."
                )
            else:
                assumptions.append(
                    "TI assumes the supplied PK file (--pk-file) represents "
                    "exposure at the intended therapeutic/efficacious dose. "
                    "This cannot be verified from the PK artifact alone, "
                    "since dde.pk-nca.v1 does not tag dose context."
                )
            record["assumptions"] = assumptions
        if margin_note is not None:
            record["margin_note"] = margin_note
        if same_study_reason:
            record["same_study_detected"] = same_study_reason

    if margin_notes:
        record["margin_notes"] = margin_notes
    if resolved_dose_context is not None:
        record["dose_context"] = resolved_dose_context

    # --- 5. hERG margin (optional) ---
    if herg_file is not None:
        herg_path = resolve_artifact(state, herg_file, "safety-pharm artifact")
        herg_doc = provenance.read_json(herg_path, "safety-pharm artifact")

        if not isinstance(herg_doc, dict):
            raise SchemaError("safety-pharm artifact must be a JSON object")

        if herg_doc.get("schema") != "dde.tox-safety-pharm.v1":
            raise Refusal(
                "unsupported or missing schema tag on safety-pharm file",
                detail=(
                    f"expected 'dde.tox-safety-pharm.v1', "
                    f"got {herg_doc.get('schema')!r}"
                ),
                remedy=(
                    "use a file produced by `dde tox ingest` with a safety-pharm schema"
                ),
            )

        herg_ic50 = herg_doc.get("herg_ic50")
        herg_ic50_units = herg_doc.get("herg_ic50_units")

        if herg_ic50 is None or herg_ic50_units is None:
            raise SchemaError(
                "safety-pharm artifact missing herg_ic50 or herg_ic50_units",
                remedy="ensure the safety-pharm artifact has both fields",
            )

        # Guard: pk_cmax must exist and be positive before hERG margin
        if pk_cmax is None:
            raise SchemaError(
                "PK NCA artifact missing cmax parameter — "
                "cannot compute hERG safety margin",
                remedy="ensure the PK NCA artifact was produced by `dde pk nca`",
            )
        if pk_cmax <= 0:
            raise SchemaError(
                f"PK Cmax must be positive for hERG margin, got {pk_cmax}",
                remedy="ensure the PK NCA artifact was produced by `dde pk nca`",
            )

        # Verify units match between hERG IC50 and PK Cmax
        if herg_ic50_units != pk_cmax_units:
            raise Refusal(
                "unit mismatch between hERG IC50 and PK Cmax",
                detail=(
                    f"herg_ic50_units={herg_ic50_units!r}, "
                    f"PK cmax_units={pk_cmax_units!r}"
                ),
                remedy=(
                    "ensure both use the same concentration units "
                    "— this tool does not perform unit conversion"
                ),
            )

        herg_margin = herg_ic50 / pk_cmax
        record["herg"] = {
            "ic50": herg_ic50,
            "ic50_units": herg_ic50_units,
            "pk_cmax": pk_cmax,
            "pk_cmax_units": pk_cmax_units,
            "safety_margin": round(herg_margin, 4),
        }

    # --- 7. Write output artifact ---
    artifact_path = target_dir / f"{study_id}.tox-margins.json"
    artifact_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # --- Provenance sidecar ---
    sidecar_params: dict[str, Any] = {
        "tox_file": tox_path.name,
        "pk_file": pk_path.name,
        "study_id": study_id,
    }
    if herg_file is not None:
        sidecar_params["herg_file"] = herg_path.name
    if clinical_pk_file is not None:
        sidecar_params["clinical_pk_file"] = clinical_pk_path.name
    if dose_context is not None:
        sidecar_params["dose_context"] = dose_context
    sidecar = provenance.Sidecar(
        tool=ARTIFACT_CLASS,
        subcommand="margins",
        endpoint=None,
        parameters=sidecar_params,
    )
    sidecar.note("pk_source_file", pk_path.name)
    sidecar.note("pk_source_sha256", provenance.sha256_file(pk_path))
    if clinical_pk_path is not None:
        sidecar.note("clinical_pk_file", clinical_pk_path.name)
        sidecar.note(
            "clinical_pk_sha256",
            provenance.sha256_file(clinical_pk_path),
        )
    if herg_file is not None:
        sidecar.note("herg_source_file", herg_path.name)
        sidecar.note("herg_source_sha256", provenance.sha256_file(herg_path))
    if margins is not None:
        sidecar.note("margin_calculation_method", "linear_exposure")
        sidecar.note(
            "ti_efficacious_dose_assumed",
            "TI calculation assumes --pk-file represents exposure at the "
            "intended therapeutic/efficacious dose; dde.pk-nca.v1 does "
            "not tag dose context, so this cannot be verified from the "
            "artifact alone",
        )
    if same_study_reason:
        sidecar.note("same_study_detected", same_study_reason)
    if is_indeterminate:
        sidecar.warn(
            f"Therapeutic index could not be computed: "
            f"{indeterminate_reason}. Do not interpret the absence of a "
            f"safety flag as a clean result.",
            code="tox.margin_indeterminate",
        )
    sidecar.note("noael_study_id", study_id)
    sidecar.note("noael_species", species)
    sidecar.note("noael_duration_days", duration_days)
    if resolved_dose_context is not None:
        sidecar.note("dose_context", resolved_dose_context)
    if margin_notes:
        sidecar.note("margin_notes", margin_notes)

    # --- 6. Forward upstream relays from PK sidecars (animal + clinical) ---
    def _resolve_pk_sidecar(pk: Path) -> Path:
        """Resolve PK sidecar path using Path.with_suffix for robustness."""
        # Standard sidecar: replace the final .json with .meta.json
        # e.g. study.pk-nca.json → study.pk-nca.meta.json
        return pk.with_suffix(".meta.json")

    def _forward_pk_relays(pk: Path, label: str) -> None:
        """Read mandatory_relays from a PK sidecar and forward them."""
        meta = _resolve_pk_sidecar(pk)
        if meta.exists():
            try:
                pk_meta = json.loads(meta.read_text(encoding="utf-8"))
                for r in pk_meta.get("mandatory_relays", []):
                    code = r.get("code", "")
                    message = r.get("message", "")
                    if code and message:
                        sidecar.warn(message, code=code)
            except (json.JSONDecodeError, OSError):
                import logging

                logging.getLogger("dde.tox").warning(
                    "%s PK sidecar at %s exists but could not be read — "
                    "upstream relays may be missing",
                    label,
                    meta,
                )
        else:
            import logging

            logging.getLogger("dde.tox").warning(
                "%s PK sidecar not found at %s — upstream relays from "
                "%s PK will be missing from the margins sidecar",
                label,
                meta,
                label.lower(),
            )

    _forward_pk_relays(pk_path, "Animal")
    if clinical_pk_path is not None:
        _forward_pk_relays(clinical_pk_path, "Clinical")

    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(target_dir / f"{study_id}.tox-margins.meta.json")

    # --- Emit (paths only — phase 1 convention) ---
    emit.path(artifact_path, role="margins")
    emit.path(meta_path, role="sidecar")
    emit.line(f"margins: {study_id} ({species})")
    emit.line(f"NOAEL: {noael_mg_kg} mg/kg")
    if is_indeterminate:
        emit.line("verdict: indeterminate")
        emit.line(f"reason: {indeterminate_reason}")
    elif margins is not None:
        if clinical_ti is not None:
            # Dual-margin mode
            if animal_margin:
                if "ti_cmax" in animal_margin:
                    emit.line(f"animal margin (Cmax): {animal_margin['ti_cmax']:.1f}")
                if "ti_auc" in animal_margin:
                    emit.line(f"animal margin (AUC): {animal_margin['ti_auc']:.1f}")
            if "ti_cmax" in clinical_ti:
                emit.line(f"clinical TI (Cmax): {clinical_ti['ti_cmax']:.1f}")
            if "ti_auc" in clinical_ti:
                emit.line(f"clinical TI (AUC): {clinical_ti['ti_auc']:.1f}")
        else:
            if "ti_cmax" in margins:
                emit.line(f"TI (Cmax): {margins['ti_cmax']:.1f}")
            if "ti_auc" in margins:
                emit.line(f"TI (AUC): {margins['ti_auc']:.1f}")
    else:
        emit.line("TI: not computed (no NOAEL exposure data)")
    if "herg" in record:
        emit.line(f"hERG safety margin: {record['herg']['safety_margin']:.1f}")
    emit.flush()


# ---------------------------------------------------------------------------
# tox analyze — Phase 2 threshold application
# ---------------------------------------------------------------------------

# Map schema tag → (analysis_type, file_suffix, meta_suffix)
_SCHEMA_MAP: dict[str, tuple[str, str, str]] = {
    "dde.tox-margins.v1": (
        "margins",
        ".tox-margins.json",
        ".tox-margins.meta.json",
    ),
    "dde.tox-genotox-assessment.v1": (
        "genotox",
        ".tox-genotox-assessment.json",
        ".tox-genotox-assessment.meta.json",
    ),
    "dde.tox-safety-pharm.v1": (
        "safety-pharm",
        ".tox-safety-pharm.json",
        ".tox-safety-pharm.meta.json",
    ),
}


def _analyze_margins(
    doc: dict[str, Any],
    thresholds: Any,
    metrics: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Build margins analysis: TI and hERG margin vs thresholds.

    Handles three margin shapes:

    1. **Indeterminate** — same-study detection fired and no clinical PK
       was provided.  The verdict is ``indeterminate`` and no threshold
       is applied.
    2. **Dual-margin** — ``clinical_ti`` present.  ICH M3(R2) thresholds
       apply only to ``clinical_ti``; ``animal_margin`` is recorded but
       not graded.
    3. **Single-margin** (legacy) — ``margins`` contains the TI values
       and thresholds apply directly.
    """
    # --- Item 3: indeterminate verdict ---
    if doc.get("verdict") == "indeterminate":
        reason = doc.get(
            "verdict_reason",
            "Therapeutic index could not be computed — inputs insufficient",
        )
        metrics["same_study_detected"] = doc.get("same_study_detected")
        assessment["ti"] = {
            "status": "indeterminate",
            "message": reason,
        }
        assessment["verdict"] = "indeterminate"
        assessment["verdict_reason"] = reason
        return

    margins = doc.get("margins")
    ti_minimum = thresholds.get("ti_minimum")

    if margins is None:
        # NOAEL exposure absent — cannot compute TI
        note = doc.get(
            "margin_note",
            "NOAEL exposure data not available; therapeutic index cannot be computed",
        )
        assessment["ti"] = {
            "status": "incomplete",
            "message": note,
        }
        assessment["verdict"] = "incomplete"
        return

    # --- Dual-margin mode (Item 1): record animal_margin, grade clinical_ti ---
    animal_margin = doc.get("animal_margin")
    clinical_ti = doc.get("clinical_ti")

    if animal_margin is not None:
        # Record animal margin as informational — no threshold applied
        for key in ("ti_cmax", "ti_auc"):
            val = animal_margin.get(key)
            if val is not None:
                metrics[f"animal_{key}"] = val
        assessment["animal_margin"] = {
            "status": "informational",
            "message": (
                "Animal self-comparison margin recorded; ICH M3(R2) "
                "thresholds are not applied to same-species comparisons."
            ),
        }

    # Determine which TI values to grade against thresholds
    graded_ti = clinical_ti if clinical_ti is not None else margins

    # --- TI assessment ---
    ti_cmax = graded_ti.get("ti_cmax")
    ti_auc = graded_ti.get("ti_auc")

    ti_label = "clinical_ti" if clinical_ti is not None else "ti"
    ti_status = "acceptable"

    if ti_cmax is not None:
        metrics[f"{ti_label}_cmax"] = ti_cmax
        if ti_cmax < ti_minimum:
            assessment[f"{ti_label}_cmax"] = {
                "status": "flagged",
                "message": (
                    f"TI (Cmax) {ti_cmax:.1f} is below the "
                    f"{ti_minimum:.0f}-fold minimum"
                ),
            }
            ti_status = "flagged"
        else:
            assessment[f"{ti_label}_cmax"] = {
                "status": "acceptable",
                "message": (
                    f"TI (Cmax) {ti_cmax:.1f} meets the {ti_minimum:.0f}-fold minimum"
                ),
            }

    if ti_auc is not None:
        metrics[f"{ti_label}_auc"] = ti_auc
        if ti_auc < ti_minimum:
            assessment[f"{ti_label}_auc"] = {
                "status": "flagged",
                "message": (
                    f"TI (AUC) {ti_auc:.1f} is below the {ti_minimum:.0f}-fold minimum"
                ),
            }
            ti_status = "flagged"
        else:
            assessment[f"{ti_label}_auc"] = {
                "status": "acceptable",
                "message": (
                    f"TI (AUC) {ti_auc:.1f} meets the {ti_minimum:.0f}-fold minimum"
                ),
            }

    # --- hERG margin assessment (if present in margins artifact) ---
    herg = doc.get("herg")
    if herg is not None:
        herg_margin = herg.get("safety_margin")
        if herg_margin is not None:
            metrics["herg_safety_margin"] = herg_margin
            herg_threshold = thresholds.get("herg_safety_margin")
            herg_marginal = thresholds.get("herg_marginal")

            if herg_margin >= herg_threshold:
                assessment["herg"] = {
                    "status": "acceptable",
                    "message": (
                        f"hERG safety margin {herg_margin:.1f}-fold meets "
                        f"the {herg_threshold:.0f}-fold threshold"
                    ),
                }
            elif herg_margin >= herg_marginal:
                assessment["herg"] = {
                    "status": "flagged",
                    "message": (
                        f"hERG safety margin {herg_margin:.1f}-fold is below "
                        f"the {herg_threshold:.0f}-fold threshold but above "
                        f"the {herg_marginal:.0f}-fold marginal boundary"
                    ),
                }
                ti_status = "flagged"
            else:
                assessment["herg"] = {
                    "status": "flagged",
                    "message": (
                        f"hERG safety margin {herg_margin:.1f}-fold is below "
                        f"the {herg_marginal:.0f}-fold marginal boundary"
                    ),
                }
                ti_status = "flagged"

    assessment["verdict"] = ti_status


def _analyze_genotox(
    doc: dict[str, Any],
    thresholds: Any,
    metrics: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Build genotox analysis: verdict classification (categorical)."""
    verdict = doc.get("verdict", "unknown")
    metrics["genotox_verdict"] = verdict

    # Map verdict to risk level — categorical, no threshold application
    verdict_map: dict[str, str] = {
        "negative": "acceptable",
        "equivocal": "flagged_equivocal",
        "concern": "flagged_concern",
        "positive_concern": "flagged_concern",
        "positive": "flagged_concern",
    }

    risk_level = verdict_map.get(verdict, "unknown")
    assessment["genotox"] = {
        "status": risk_level,
        "message": f"Genotoxicity battery verdict: {verdict}",
    }

    weight_of_evidence = doc.get("weight_of_evidence", False)
    if weight_of_evidence:
        metrics["weight_of_evidence"] = True
        basis = doc.get("weight_of_evidence_basis", "")
        if basis:
            metrics["weight_of_evidence_basis"] = basis

    assessment["verdict"] = risk_level


def _analyze_safety_pharm(
    doc: dict[str, Any],
    thresholds: Any,
    metrics: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Build safety-pharm analysis: record IC50, summarise optional fields."""
    # Record hERG IC50 as a metric
    herg_ic50 = doc.get("herg_ic50")
    herg_ic50_units = doc.get("herg_ic50_units")
    if herg_ic50 is not None:
        metrics["herg_ic50"] = herg_ic50
    if herg_ic50_units is not None:
        metrics["herg_ic50_units"] = herg_ic50_units

    # Summarise cardiovascular, respiratory, CNS fields if present
    for field in ("cardiovascular", "respiratory", "cns"):
        section = doc.get(field)
        if section is not None and isinstance(section, dict):
            # Record which fields were provided
            present_keys = [k for k, v in section.items() if v is not None]
            if present_keys:
                metrics[f"{field}_fields_present"] = present_keys

    # Verdict: recorded — the safety pharmacology data is present but
    # quantitative margin assessment requires running `tox margins`
    assessment["safety_pharm"] = {
        "status": "recorded",
        "message": (
            "Safety pharmacology data recorded. Quantitative hERG "
            "safety margin assessment requires running `tox margins` "
            "with both the PK and safety-pharm artifacts."
        ),
    }
    assessment["verdict"] = "recorded"


@tox.command("analyze")
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
    """Apply tox-safety-package thresholds to tox artifacts.

    Reads a ``.tox-margins.json``, ``.tox-genotox-assessment.json``, or
    ``.tox-safety-pharm.json`` artifact and applies the
    ``tox-safety-package`` threshold set to produce an analysis record
    with margin assessments and verdicts.

    \b
    Outputs:
      {stem}.tox.analysis.json  -- analysis record with verdict
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    # --- Resolve input ---
    source = resolve_artifact(state, path, "tox artifact")
    result_doc = provenance.read_json(source, "tox artifact")

    if not isinstance(result_doc, dict):
        raise SchemaError("tox artifact must be a JSON object")

    schema = result_doc.get("schema", "")
    if schema not in _SCHEMA_MAP:
        raise Refusal(
            f"unrecognised tox artifact schema: {schema!r}",
            detail=("expected one of: " + ", ".join(sorted(_SCHEMA_MAP))),
            remedy=(
                "provide a file produced by "
                "`dde tox margins`, `tox genotox`, or `tox ingest` "
                "(safety-pharm)"
            ),
        )

    analysis_type, file_suffix, meta_suffix = _SCHEMA_MAP[schema]
    stem = source.name.replace(file_suffix, "")

    # --- Derive record type from schema for unique output filename ---
    record_type = provenance.record_type_from_schema(schema)
    new_analysis_name = f"{stem}.{record_type}.analysis.json"

    # Backward compatibility: warn if an old-format analysis file exists
    old_analysis_name = f"{stem}.tox.analysis.json"
    if old_analysis_name != new_analysis_name:
        old_candidate = source.parent / old_analysis_name
        if old_candidate.exists():
            from ..core.output import warn

            warn(
                f"old-format analysis exists: {old_analysis_name}; "
                f"new analysis uses: {new_analysis_name}"
            )

    # --- Load thresholds ---
    thresholds = load_thresholds(state, "tox-safety-package")

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

    if analysis_type == "margins":
        _analyze_margins(result_doc, thresholds, metrics, assessment)
    elif analysis_type == "genotox":
        _analyze_genotox(result_doc, thresholds, metrics, assessment)
    elif analysis_type == "safety-pharm":
        _analyze_safety_pharm(result_doc, thresholds, metrics, assessment)

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

    # --- Emit summary (bounded) ---
    emit.path(analysis_path, role="analysis")
    emit.line(f"tox analysis: {stem} ({analysis_type})")
    emit.line(f"Threshold set: {thresholds.tag}")
    emit.line(f"Verdict: {assessment.get('verdict', 'unknown')}")
    if thresholds.unresolved():
        emit.line(f"Unresolved thresholds: {', '.join(thresholds.unresolved())}")
    emit.flush()
