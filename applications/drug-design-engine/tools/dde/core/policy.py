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

"""Gate policy requirements — versioned, applicability-aware gate criteria.

A gate policy is a versioned collection of requirements that formalizes
what concepts must demonstrate to pass a stage gate. Each requirement
carries its own stable identity, type classification, and applicability
rules that determine which concepts it applies to.

Requirements reference threshold sets by ``name@version`` and key —
they never inline a threshold value. This follows the existing
convention in ``science-program-lead/agents.md`` §9: "tool thresholds
are cited by name, never by value."

Three requirement types:

  - ``hard_constraint``: must be met; failure blocks progression
  - ``prioritization_heuristic``: informs ordering, not a gate criterion
  - ``scientific_cutoff``: tied to a named ThresholdSet; value resolved
    at evaluation time via ``ThresholdSet.get()``

See the design document (tracker73-shared-contracts.md §2.3) for the
full schema specification.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any

from .errors import SchemaError
from .paths import is_safe_to_open

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIREMENT_TYPES = {
    "hard_constraint",
    "prioritization_heuristic",
    "scientific_cutoff",
}

DIRECTIONS = {"above", "below", "within"}

_GP_ID_RE = re.compile(r"^GP-\d{3,}$")
_REQ_ID_RE = re.compile(r"^REQ-\d{3,}$")
_SNAP_ID_RE = re.compile(r"^SNAP-\d{3,}$")

POLICY_SCHEMA = "dde.gate-policy.v1"
SNAPSHOT_SCHEMA = "dde.policy-snapshot.v1"

# Applicability fields — null means "applies to all".
APPLICABILITY_FIELDS = {
    "modality",
    "stage",
    "indication",
    "method_required",
    "data_required",
}


# ---------------------------------------------------------------------------
# Requirement validation
# ---------------------------------------------------------------------------


def _validate_applicability(app: Any) -> list[str]:
    """Validate an applicability dict. Returns a list of error strings."""
    errors: list[str] = []
    if not isinstance(app, dict):
        errors.append("applicability must be a dict")
        return errors

    for field in app:
        if field not in APPLICABILITY_FIELDS:
            errors.append(
                f"unknown applicability field {field!r}; "
                f"known fields: {', '.join(sorted(APPLICABILITY_FIELDS))}"
            )

    for field in ("modality", "indication", "method_required", "data_required"):
        value = app.get(field)
        if value is not None and not isinstance(value, list):
            errors.append(f"applicability.{field} must be a list or null")

    stage_val = app.get("stage")
    if stage_val is not None:
        if not isinstance(stage_val, list):
            errors.append("applicability.stage must be a list of ints or null")
        elif not all(isinstance(s, int) for s in stage_val):
            errors.append("applicability.stage entries must be integers")

    return errors


def _validate_requirement(req: Any, index: int) -> list[str]:
    """Validate a single requirement dict. Returns error strings."""
    errors: list[str] = []
    prefix = f"requirements[{index}]"

    if not isinstance(req, dict):
        errors.append(f"{prefix}: must be a dict")
        return errors

    required = [
        "req_id",
        "version",
        "description",
        "type",
        "evidence_type",
        "applicability",
        "authority",
        "override_permitted",
        "effective_date",
    ]
    missing = [f for f in required if f not in req]
    if missing:
        errors.append(f"{prefix}: missing required fields: {', '.join(missing)}")

    if "req_id" in req and not _REQ_ID_RE.match(str(req["req_id"])):
        errors.append(f"{prefix}: req_id must match REQ-NNN, got {req['req_id']!r}")

    if "version" in req:
        v = req["version"]
        if not isinstance(v, int) or v < 1:
            errors.append(f"{prefix}: version must be a positive integer, got {v!r}")

    if "type" in req:
        rtype = req["type"]
        if rtype not in REQUIREMENT_TYPES:
            errors.append(
                f"{prefix}: type {rtype!r} is not valid; "
                f"valid types: {', '.join(sorted(REQUIREMENT_TYPES))}"
            )
        # scientific_cutoff requires a threshold_set reference.
        if rtype == "scientific_cutoff":
            if not req.get("threshold_set"):
                errors.append(
                    f"{prefix}: scientific_cutoff requirements must specify "
                    "a threshold_set reference"
                )
            if not req.get("threshold_key"):
                errors.append(
                    f"{prefix}: scientific_cutoff requirements must specify "
                    "a threshold_key"
                )

    if "direction" in req and req["direction"] is not None:
        if req["direction"] not in DIRECTIONS:
            errors.append(
                f"{prefix}: direction must be one of "
                f"{', '.join(sorted(DIRECTIONS))} or null"
            )

    if "override_permitted" in req and not isinstance(req["override_permitted"], bool):
        errors.append(f"{prefix}: override_permitted must be a boolean")

    if "applicability" in req:
        app_errors = _validate_applicability(req["applicability"])
        for e in app_errors:
            errors.append(f"{prefix}: {e}")

    return errors


# ---------------------------------------------------------------------------
# Policy record validation
# ---------------------------------------------------------------------------


def validate_policy(data: dict[str, Any]) -> list[str]:
    """Validate a gate policy record. Returns a list of error strings.

    Registered in ``controlstore._VALIDATORS`` under ``"policy"``.
    """
    errors: list[str] = []

    required = [
        "id",
        "version",
        "stage",
        "gate_name",
        "effective_date",
        "requirements",
        "created_at",
    ]
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "schema" in data and data["schema"] != POLICY_SCHEMA:
        errors.append(f"schema must be {POLICY_SCHEMA!r}, got {data['schema']!r}")

    if "id" in data and not _GP_ID_RE.match(str(data["id"])):
        errors.append(f"id must match GP-NNN, got {data['id']!r}")

    if "version" in data:
        v = data["version"]
        if not isinstance(v, int) or v < 1:
            errors.append(f"version must be a positive integer, got {v!r}")

    if "stage" in data:
        s = data["stage"]
        if not isinstance(s, int) or s < 0 or s > 4:
            errors.append(f"stage must be an integer 0-4, got {s!r}")

    if "requirements" in data:
        reqs = data["requirements"]
        if not isinstance(reqs, list):
            errors.append("requirements must be a list")
        else:
            for i, req in enumerate(reqs):
                errors.extend(_validate_requirement(req, i))

    return errors


# ---------------------------------------------------------------------------
# Snapshot validation
# ---------------------------------------------------------------------------


def validate_snapshot(data: dict[str, Any]) -> list[str]:
    """Validate a policy freeze snapshot. Returns a list of error strings.

    Registered in ``controlstore._VALIDATORS`` under ``"snapshot"``.
    """
    errors: list[str] = []

    required = [
        "snapshot_id",
        "frozen_at",
        "gate_policy_ref",
        "requirements",
        "threshold_sets",
        "concept_refs",
        "assessments",
    ]
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "schema" in data and data["schema"] != SNAPSHOT_SCHEMA:
        errors.append(f"schema must be {SNAPSHOT_SCHEMA!r}, got {data['schema']!r}")

    if "snapshot_id" in data and not _SNAP_ID_RE.match(str(data["snapshot_id"])):
        errors.append(f"snapshot_id must match SNAP-NNN, got {data['snapshot_id']!r}")

    if "requirements" in data and not isinstance(data["requirements"], list):
        errors.append("requirements must be a list")

    if "threshold_sets" in data:
        ts = data["threshold_sets"]
        if not isinstance(ts, dict):
            errors.append("threshold_sets must be a dict")
        else:
            for name, entry in ts.items():
                if not isinstance(entry, dict):
                    errors.append(f"threshold_sets[{name!r}] must be a dict")
                    continue
                ts_required = ["tag", "applied", "sources", "provenance", "unresolved"]
                ts_missing = [f for f in ts_required if f not in entry]
                if ts_missing:
                    errors.append(
                        f"threshold_sets[{name!r}]: missing required fields: "
                        f"{', '.join(ts_missing)}"
                    )
                if "unresolved" in entry and not isinstance(entry["unresolved"], list):
                    errors.append(
                        f"threshold_sets[{name!r}]: unresolved must be a list"
                    )

    if "concept_refs" in data and not isinstance(data["concept_refs"], list):
        errors.append("concept_refs must be a list")

    if "assessments" in data and not isinstance(data["assessments"], list):
        errors.append("assessments must be a list")

    return errors


# ---------------------------------------------------------------------------
# Applicability matching
# ---------------------------------------------------------------------------


def requirement_applies(
    requirement: dict[str, Any], concept: dict[str, Any], *, stage: int | None = None
) -> bool:
    """Check whether a requirement applies to a concept.

    A requirement applies when all non-null applicability fields match
    the concept's corresponding fields. A null field means "applies
    regardless of this dimension" (design §2.3.1).

    Parameters
    ----------
    requirement:
        A policy requirement dict with an ``applicability`` key.
    concept:
        A concept dict (or dict-like) with ``modality`` and
        ``disease_context`` fields.
    stage:
        The current stage number, if checking stage applicability.

    Returns
    -------
    bool
        True if the requirement applies to the concept.
    """
    app = requirement.get("applicability", {})

    # Modality check.
    modality_filter = app.get("modality")
    if modality_filter is not None:
        concept_modality = concept.get("modality")
        if concept_modality not in modality_filter:
            return False

    # Stage check.
    stage_filter = app.get("stage")
    if stage_filter is not None and stage is not None:
        if stage not in stage_filter:
            return False

    # Indication check.
    indication_filter = app.get("indication")
    if indication_filter is not None:
        disease_context = concept.get("disease_context", {})
        concept_indication = (
            disease_context.get("indication")
            if isinstance(disease_context, dict)
            else None
        )
        if concept_indication not in indication_filter:
            return False

    return True


# ---------------------------------------------------------------------------
# Evidence / assessment matching (design §3.1)
# ---------------------------------------------------------------------------


def match_assessment_to_requirement(
    requirement: dict[str, Any],
    assessment: dict[str, Any],
) -> dict[str, Any]:
    """Check whether an assessment evaluates a requirement.

    Returns a result dict with:
      - ``matches``: bool — True if evidence_type matches
      - ``warnings``: list[str] — compatibility warnings
      - ``method_excluded``: bool — True if method_required filter
        excludes this assessment

    Design §3.1 rules:
      1. evidence_type must match exactly (primary key)
      2. units must be compatible (mismatch is a warning)
      3. method is informational, not gating (unless method_required)
      4. endpoint should match when specified (mismatch is a warning)
    """
    result: dict[str, Any] = {
        "matches": False,
        "warnings": [],
        "method_excluded": False,
    }

    evidence = assessment.get("evidence", {}) or {}

    # Rule 1: evidence_type must match exactly.
    # Issue #297: null or empty evidence_type on either side is a
    # non-match — fail-closed to prevent false-positive gate decisions.
    req_evidence_type = requirement.get("evidence_type")
    assessment_evidence_type = evidence.get("evidence_type")
    if (
        not isinstance(req_evidence_type, str)
        or not req_evidence_type.strip()
        or not isinstance(assessment_evidence_type, str)
        or not assessment_evidence_type.strip()
    ):
        return result  # matches=False
    if req_evidence_type != assessment_evidence_type:
        return result

    result["matches"] = True

    # Rule 2: units compatibility.
    req_units = requirement.get("units")
    evidence_units = evidence.get("metric_units")
    if req_units is not None and evidence_units is not None:
        if req_units != evidence_units:
            result["warnings"].append(
                f"units mismatch: requirement specifies {req_units!r}, "
                f"evidence has {evidence_units!r}"
            )
    elif req_units is not None and evidence_units is None:
        result["warnings"].append("requirement specifies units but evidence does not")
    elif req_units is None and evidence_units is not None:
        result["warnings"].append("evidence specifies units but requirement does not")

    # Rule 3: method_required applicability filter.
    app = requirement.get("applicability", {})
    method_required = app.get("method_required")
    if method_required is not None:
        evidence_method = evidence.get("method")
        if evidence_method not in method_required:
            result["method_excluded"] = True

    # Rule 4: endpoint / metric_name should match.
    req_endpoint = requirement.get("endpoint")
    evidence_metric = evidence.get("metric_name")
    if req_endpoint is not None and evidence_metric is not None:
        if req_endpoint != evidence_metric:
            result["warnings"].append(
                f"endpoint mismatch: requirement specifies {req_endpoint!r}, "
                f"evidence has metric_name {evidence_metric!r}"
            )

    return result


# ---------------------------------------------------------------------------
# Freeze logic (design §2.3.2)
# ---------------------------------------------------------------------------


def freeze_policy(
    policy: dict[str, Any],
    threshold_sets: dict[str, Any],
    *,
    snapshot_id: str,
    concept_refs: list[str],
    assessments: list[str],
    decision_ref: str | None = None,
) -> dict[str, Any]:
    """Create a policy freeze snapshot.

    Freezes the policy at its current version, resolving all referenced
    threshold sets to their applied values. UNRESOLVED thresholds are
    explicitly recorded in the ``unresolved`` field of each threshold
    set entry.

    Parameters
    ----------
    policy:
        The gate policy record to freeze.
    threshold_sets:
        Dict mapping threshold set names (e.g. ``"pocket@1.0"``) to
        ``ThresholdSet`` instances. Each set referenced by a policy
        requirement must be present.
    snapshot_id:
        The snapshot identifier (e.g. ``"SNAP-001"``).
    concept_refs:
        List of concept references under evaluation.
    assessments:
        List of assessment IDs at freeze time.
    decision_ref:
        The resulting decision record ID, if available.

    Returns
    -------
    dict
        The freeze snapshot record.
    """
    # Deep copy requirements so the snapshot is self-contained.
    frozen_requirements = copy.deepcopy(policy.get("requirements", []))

    # Collect all referenced threshold sets from requirements.
    referenced_sets: set[str] = set()
    for req in policy.get("requirements", []):
        ts_ref = req.get("threshold_set")
        if ts_ref:
            referenced_sets.add(ts_ref)

    # Freeze each referenced threshold set.
    frozen_threshold_sets: dict[str, dict[str, Any]] = {}
    for ts_name in sorted(referenced_sets):
        ts = threshold_sets.get(ts_name)
        if ts is None:
            # Threshold set not provided — record absence explicitly.
            frozen_threshold_sets[ts_name] = {
                "tag": ts_name,
                "applied": {},
                "sources": {},
                "provenance": f"threshold set {ts_name!r} not available at freeze time",
                "unresolved": [],
            }
            continue

        frozen_threshold_sets[ts_name] = {
            "tag": ts.tag,
            "applied": ts.applied(),
            "sources": ts.sources(),
            "provenance": ts.provenance,
            "unresolved": ts.unresolved(),
        }

    policy_ref = f"{policy.get('id', 'GP-???')}@{policy.get('version', '?')}"

    snapshot: dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate_policy_ref": policy_ref,
        "requirements": frozen_requirements,
        "threshold_sets": frozen_threshold_sets,
        "concept_refs": list(concept_refs),
        "assessments": list(assessments),
        "decision_ref": decision_ref,
    }

    return snapshot


# ---------------------------------------------------------------------------
# Program YAML loader (design §4.4.1)
# ---------------------------------------------------------------------------


def load_program_config(project_root: Any) -> dict[str, Any]:
    """Load and validate ``.dde/program.yaml``.

    Returns the parsed config dict with the following top-level keys:
      - ``program``: dict with name, indication, modality, charter_ref
      - ``gate_policies``: dict mapping stage keys to policy refs
      - ``human_reserved``: list of reserved decision types
      - ``concept_defaults``: dict of default concept field values

    Returns an empty dict if the file does not exist. This supports
    backward compatibility (design §3.4): a program without a
    ``program.yaml`` is valid.
    """
    from pathlib import Path

    root = Path(project_root)
    path = root / ".dde" / "program.yaml"

    if not path.is_file():
        return {}

    try:
        import yaml
    except ImportError as e:
        raise SchemaError(
            "PyYAML is required to read program configuration",
            detail=f"{path} exists but yaml is not importable",
            remedy="install PyYAML into the tools environment",
        ) from e

    if not is_safe_to_open(path):
        raise SchemaError(
            f"refusing to read through symlink: {path}",
            detail="symlink exploitation guard",
        )

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise SchemaError(f"could not parse {path}", detail=str(exc)) from exc

    if not isinstance(data, dict):
        raise SchemaError(f"{path} must contain a YAML mapping")

    errors = _validate_program_config(data)
    if errors:
        raise SchemaError(
            f"invalid program.yaml: {'; '.join(errors)}",
            detail=f"at {path}",
        )

    return data


def _validate_program_config(data: dict[str, Any]) -> list[str]:
    """Validate program.yaml structure. Returns error strings."""
    errors: list[str] = []

    program = data.get("program")
    if program is not None:
        if not isinstance(program, dict):
            errors.append("program must be a mapping")

    gate_policies = data.get("gate_policies")
    if gate_policies is not None:
        if not isinstance(gate_policies, dict):
            errors.append("gate_policies must be a mapping")

    human_reserved = data.get("human_reserved")
    if human_reserved is not None:
        if not isinstance(human_reserved, list):
            errors.append("human_reserved must be a list")

    concept_defaults = data.get("concept_defaults")
    if concept_defaults is not None:
        if not isinstance(concept_defaults, dict):
            errors.append("concept_defaults must be a mapping")

    return errors
