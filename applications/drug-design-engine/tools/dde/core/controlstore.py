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

"""Record I/O for the control plane state store under ``.dde/control/``.

The state store holds one JSON file per record — work orders, context
snapshots, runs, and validation records — plus an append-only event log.
This module provides the CRUD layer with schema validation on every
write.  It never touches the network, never applies thresholds, and
never produces Layer 0 artifacts.

Record files are the source of truth for current state; the event log
(``events.ndjson``) is the supplementary audit trail.  See the design
document (issue-22-design.md §3.1) for the rationale.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .concepts import validate_concept
from .errors import ArtifactError, Refusal, SchemaError
from .evidence import validate_assessment, validate_decision
from .paths import is_safe_to_open
from .statemachine import RUN_STATES, WORK_ORDER_STATES

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

CONTROL_DIR = ".dde/control"

RECORD_TYPES: dict[str, str] = {
    "work-order": "work-orders",
    "context": "contexts",
    "run": "runs",
    "validation": "validations",
    "lease": "leases",
    "assessment": "assessments",
    "decision": "decisions",
    "concept": "concepts",
    "policy": "policies",
    "snapshot": "snapshots",
}

_SUBDIRS = list(RECORD_TYPES.values())
_EVENT_LOG = "events.ndjson"


def ensure_control_dirs(project_root: str | Path) -> None:
    """Idempotently create the control directory tree.

    Creates ``.dde/control/`` and all record subdirectories, plus an
    empty ``events.ndjson`` file (only if it does not already exist).
    """
    root = Path(project_root)
    control = root / CONTROL_DIR
    control.mkdir(parents=True, exist_ok=True)
    for subdir in _SUBDIRS:
        (control / subdir).mkdir(parents=True, exist_ok=True)
    events = control / _EVENT_LOG
    if not events.exists():
        events.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _control_path(project_root: Path, record_type: str) -> Path:
    """Return the directory for *record_type*, raising on unknown types."""
    subdir = RECORD_TYPES.get(record_type)
    if subdir is None:
        raise SchemaError(
            f"unknown record type {record_type!r}",
            detail=f"known types: {', '.join(sorted(RECORD_TYPES))}",
        )
    return project_root / CONTROL_DIR / subdir


_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _record_path(project_root: Path, record_type: str, identifier: str) -> Path:
    """Full path to a single record file.

    Validates that *identifier* contains only safe characters to prevent
    path traversal (e.g. ``../../evil`` writing outside the control dir).
    """
    if not _SAFE_IDENTIFIER_RE.match(identifier):
        raise SchemaError(
            f"invalid record identifier {identifier!r}",
            detail="identifiers must contain only alphanumeric characters, "
            "hyphens, and underscores",
        )
    return _control_path(project_root, record_type) / f"{identifier}.json"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

_WO_ID_RE = re.compile(r"^WO-\d{3,}$")
_RUN_ID_RE = re.compile(r"^RUN-\d{3,}$")


def _flatten_entry(entry: Any, key: str) -> str:
    """Extract a string from a structured deliverable entry.

    If *entry* is a dict, return the value of *key* (falling back to
    ``"name"``, ``"class"``, then ``"path"``).  If *entry* is already a
    string, return it unchanged.
    """
    if isinstance(entry, dict):
        for k in (key, "name", "class", "path"):
            if k in entry:
                return str(entry[k])
        # Last resort: use the first string value
        for v in entry.values():
            if isinstance(v, str):
                return v
        return str(entry)
    return str(entry)


def normalize_deliverables(deliverables: dict[str, Any]) -> dict[str, Any]:
    """Normalize deliverable key names to the canonical schema.

    Handles the ``required_classes`` / ``authorized_classes`` split
    (#103) with full backward compatibility:

    1. If ``required_classes`` is present → use it as the canonical source.
    2. If ``layer_0_classes`` is present (and no ``required_classes``) →
       map to ``required_classes``.
    3. If ``layer_0`` is present (and neither of the above) → map to
       ``required_classes``.
    4. ``authorized_classes`` is a separate optional field — preserved
       as-is.

    For backward compatibility, ``layer_0_classes`` is always populated
    in the output from ``required_classes`` so that downstream checks
    (4-6) that read ``layer_0_classes`` continue to work.

    ``required_classes`` entries may be plain strings or dicts with a
    ``not_applicable`` key.  Plain strings are flattened for
    ``layer_0_classes``; dicts with ``not_applicable`` are preserved in
    ``required_classes`` but their class name is still included in
    ``layer_0_classes`` for backward compatibility.

    Returns a *new* dict with canonical keys.
    """
    normalized = dict(deliverables)

    # --- Resolve the canonical required_classes source ---
    if "required_classes" in normalized:
        # New canonical field present — use it.
        # Drop legacy aliases to prevent orphan-key confusion.
        normalized.pop("layer_0_classes", None)
        normalized.pop("layer_0", None)
    elif "layer_0_classes" in normalized:
        # Legacy canonical name → map to required_classes.
        normalized["required_classes"] = normalized.pop("layer_0_classes")
        normalized.pop("layer_0", None)
    elif "layer_0" in normalized:
        # Shortest alias → map to required_classes.
        normalized["required_classes"] = normalized.pop("layer_0")

    # --- Normalize required_classes entries ---
    if "required_classes" in normalized and isinstance(
        normalized["required_classes"], list
    ):
        req_raw = normalized["required_classes"]
        # Build layer_0_classes (flattened strings for backward compat)
        # and keep required_classes with not_applicable dicts preserved.
        flattened: list[str] = []
        req_normalized: list[Any] = []
        for entry in req_raw:
            if isinstance(entry, dict) and "not_applicable" in entry:
                # Preserve the dict so _check_deliverables_exist can
                # detect the not_applicable flag.
                cls_name = _flatten_entry(entry, "class")
                req_normalized.append(entry)
                flattened.append(cls_name)
            else:
                flat = _flatten_entry(entry, "class")
                req_normalized.append(flat)
                flattened.append(flat)
        normalized["required_classes"] = req_normalized
        normalized["layer_0_classes"] = flattened
    elif "required_classes" in normalized:
        # Non-list required_classes — pass through for downstream error
        # handling; set layer_0_classes to empty.
        normalized["layer_0_classes"] = []
    else:
        # No layer-0 class info at all.
        normalized.setdefault("layer_0_classes", [])

    # --- authorized_classes passes through as-is ---
    if "authorized_classes" in normalized and isinstance(
        normalized["authorized_classes"], list
    ):
        normalized["authorized_classes"] = [
            _flatten_entry(e, "class") for e in normalized["authorized_classes"]
        ]

    # --- Flatten layer_1 entries ---
    if "layer_1" in normalized and isinstance(normalized["layer_1"], list):
        normalized["layer_1"] = [
            _flatten_entry(e, "path") for e in normalized["layer_1"]
        ]

    # Pass through `consumes` without modification (#87).  The field is
    # new and optional — don't strip it, don't transform it.
    # (Validation of its structure is deferred to the command layer.)

    # Normalize `layer_0_classes_optional` entries (#132).
    # Classes listed here are checked for existence but do not fail
    # validation if absent.
    if "layer_0_classes_optional" in normalized and isinstance(
        normalized["layer_0_classes_optional"], list
    ):
        normalized["layer_0_classes_optional"] = [
            _flatten_entry(e, "class") for e in normalized["layer_0_classes_optional"]
        ]

    return normalized


def _validate_work_order(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "id",
        "revision",
        "state",
        "decision_question",
        "requested_role",
        "stage",
        "cycle",
        "context",
        "dependencies",
        "capabilities",
        "deliverables",
        "acceptance_criteria",
        "alert_policy",
        "priority",
        "resource_class",
        "report_to",
        "created_at",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "id" in data and not _WO_ID_RE.match(str(data["id"])):
        errors.append(f"id must match WO-NNN, got {data['id']!r}")

    if "revision" in data:
        rev = data["revision"]
        if not isinstance(rev, int) or rev < 1:
            errors.append(f"revision must be a positive integer, got {rev!r}")

    if "state" in data and data["state"] not in WORK_ORDER_STATES:
        errors.append(
            f"state {data['state']!r} is not a legal work-order state; "
            f"legal states: {', '.join(sorted(WORK_ORDER_STATES))}"
        )

    # Top-level type checks for structured fields (design §3.1: "validates
    # field types").  Deep validation of nested structure is deferred to
    # the command layer (e.g. commit checksums artifact links).
    _FIELD_TYPES: dict[str, type] = {
        "context": dict,
        "dependencies": list,
        "capabilities": list,
        "deliverables": dict,
        "alert_policy": dict,
    }
    for field, expected_type in _FIELD_TYPES.items():
        if field in data and not isinstance(data[field], expected_type):
            errors.append(
                f"{field} must be {expected_type.__name__}, "
                f"got {type(data[field]).__name__}"
            )

    return errors


def _validate_run(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "run_id",
        "work_order_id",
        "work_order_revision",
        "state",
        "attempt",
        "created_at",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "run_id" in data and not _RUN_ID_RE.match(str(data["run_id"])):
        errors.append(f"run_id must match RUN-NNN, got {data['run_id']!r}")

    if "attempt" in data:
        att = data["attempt"]
        if not isinstance(att, int) or att < 1:
            errors.append(f"attempt must be a positive integer, got {att!r}")

    if "state" in data and data["state"] not in RUN_STATES:
        errors.append(
            f"state {data['state']!r} is not a legal run state; "
            f"legal states: {', '.join(sorted(RUN_STATES))}"
        )

    return errors


def _validate_context(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "work_order_id",
        "revision",
        "artifact_links",
        "content",
        "content_sha256",
        "created_at",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    return errors


def _validate_validation(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "work_order_id",
        "work_order_revision",
        "run_id",
        "validated_at",
        "result",
        "checks",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    return errors


def _validate_lease(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "resource",
        "state",
        "work_order_id",
        "work_order_revision",
        "run_id",
        "holder_agent",
        "granted_at",
        "ttl_minutes",
        "expires_at",
        "extensions",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "state" in data and data["state"] not in {"held", "released", "expired"}:
        errors.append(
            f"state {data['state']!r} is not a legal lease state; "
            "legal states: held, released, expired"
        )

    if "extensions" in data:
        ext = data["extensions"]
        if not isinstance(ext, int) or ext < 0:
            errors.append(f"extensions must be a non-negative integer, got {ext!r}")

    if "ttl_minutes" in data:
        ttl = data["ttl_minutes"]
        if not isinstance(ttl, int) or ttl < 1:
            errors.append(f"ttl_minutes must be a positive integer, got {ttl!r}")

    return errors


def _validate_policy_record(data: dict[str, Any]) -> list[str]:
    """Delegate to policy module's validator."""
    from .policy import validate_policy

    return validate_policy(data)


def _validate_snapshot_record(data: dict[str, Any]) -> list[str]:
    """Delegate to policy module's validator."""
    from .policy import validate_snapshot

    return validate_snapshot(data)


_VALIDATORS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "work-order": _validate_work_order,
    "context": _validate_context,
    "run": _validate_run,
    "validation": _validate_validation,
    "lease": _validate_lease,
    "assessment": validate_assessment,
    "decision": validate_decision,
    "concept": validate_concept,
    "policy": _validate_policy_record,
    "snapshot": _validate_snapshot_record,
}


# ---------------------------------------------------------------------------
# Record I/O
# ---------------------------------------------------------------------------


def read_record(
    project_root: str | Path, record_type: str, identifier: str
) -> dict[str, Any]:
    """Read a JSON record from the state store.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        One of ``"work-order"``, ``"context"``, ``"run"``, ``"validation"``.
    identifier:
        Filename stem, e.g. ``"WO-001-r1"`` or ``"RUN-003"``.

    Returns
    -------
    dict
        The parsed record.

    Raises
    ------
    ArtifactError
        If the file is missing or not valid JSON.
    """
    root = Path(project_root)
    path = _record_path(root, record_type, identifier)
    if not path.is_file():
        raise ArtifactError(
            f"{record_type} record not found: {identifier}",
            detail=f"expected at {path}",
            remedy=f"create the {record_type} first",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(
            f"{record_type} record is not valid JSON: {identifier}",
            detail=str(exc),
        ) from exc


def _default_concept_loader(project_root: Path):
    """Build a concept-record loader for the human-approval gate.

    Returns a callable ``(concept_id: str) -> dict | None`` that reads
    concept records from ``.dde/control/concepts/``.  If the concept
    record type is not registered (the #74 sibling branch has not yet
    been merged), or the record does not exist, returns ``None`` —
    the validator treats unknown authority as ``"human"`` (safe default).
    """

    def _load(concept_id: str) -> dict[str, Any] | None:
        # Defense-in-depth: validate concept_id format before touching
        # the filesystem.
        if not re.match(r"^IC-\d{3,}$", concept_id):
            return None

        concepts_dir = project_root / CONTROL_DIR / "concepts"
        if not concepts_dir.is_dir():
            return None

        # Scan for versioned revisions (IC-NNN-r1.json, IC-NNN-r2.json, ...)
        # and return the latest.  Only fall back to the unversioned filename
        # if no revisioned record exists.
        import re as _re

        revision_re = _re.compile(r"^" + _re.escape(concept_id) + r"-r(\d+)\.json$")
        best: tuple[int, Path] | None = None
        for p in concepts_dir.iterdir():
            m = revision_re.match(p.name)
            if m:
                rev = int(m.group(1))
                if best is None or rev > best[0]:
                    best = (rev, p)

        if best is not None:
            if not is_safe_to_open(best[1]):
                return None  # fail-closed: symlink exploitation guard
            try:
                return json.loads(best[1].read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None  # fail-closed: gate treats None as requiring human approval

        # Fallback: unversioned file (IC-NNN.json) — only when no
        # versioned records exist.
        direct = concepts_dir / f"{concept_id}.json"
        if direct.is_file():
            if not is_safe_to_open(direct):
                return None  # fail-closed: symlink exploitation guard
            try:
                return json.loads(direct.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None

        return None

    return _load


def write_record(
    project_root: str | Path,
    record_type: str,
    identifier: str,
    data: dict[str, Any],
    *,
    concept_loader: Callable[[str], dict[str, Any] | None] | None = None,
) -> Path:
    """Validate schema and write a record to the state store.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        One of the keys in ``RECORD_TYPES``.
    identifier:
        Filename stem, e.g. ``"WO-001-r1"`` or ``"DR-001"``.
    data:
        The record dict.  Validated against the schema for *record_type*
        before writing.
    concept_loader:
        Optional callable to look up concept records by ID.  When
        writing a decision record, this is used by the human-approval
        gate to determine ``termination_authority``.  If not provided,
        a default loader that reads from ``.dde/control/concepts/`` is
        used automatically.

    Returns
    -------
    Path
        The path that was written.

    Raises
    ------
    SchemaError
        If the data fails schema validation.
    Refusal
        If a decision record fails the human-approval gate.
    """
    root = Path(project_root)
    validator = _VALIDATORS.get(record_type)
    if validator is None:
        raise SchemaError(
            f"unknown record type {record_type!r}",
            detail=f"known types: {', '.join(sorted(RECORD_TYPES))}",
        )

    # For decision records, supply the concept_loader so the
    # human-approval gate can look up termination_authority from
    # real concept records — not from a fictional field on the
    # decision record itself.
    if record_type == "decision":
        loader = concept_loader or _default_concept_loader(root)
        errors = validator(data, concept_loader=loader)
    else:
        errors = validator(data)

    if errors:
        raise SchemaError(
            f"invalid {record_type} record: {'; '.join(errors)}",
            detail=f"identifier: {identifier}",
        )

    path = _record_path(root, record_type, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not is_safe_to_open(path):
        raise Refusal(
            f"refusing to write through symlink: {path}",
            detail="symlink exploitation guard",
        )
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def list_records(
    project_root: str | Path,
    record_type: str,
    filter_fn: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Read all JSON records of a given type, optionally filtered.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        One of ``"work-order"``, ``"context"``, ``"run"``, ``"validation"``.
    filter_fn:
        Optional predicate applied to each parsed record.  Only records
        for which it returns ``True`` are included.

    Returns
    -------
    list[dict]
        The matching records, sorted by filename for determinism.
    """
    root = Path(project_root)
    directory = _control_path(root, record_type)
    if not directory.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # skip unreadable files silently during listing
        if not isinstance(record, dict):
            continue
        if filter_fn is None or filter_fn(record):
            results.append(record)
    return results


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


def append_event(project_root: str | Path, event: dict[str, Any]) -> None:
    """Append a single event to ``events.ndjson``.

    The event dict should contain: ``timestamp`` (ISO 8601), ``type``,
    ``subject_id``, ``revision`` (int or None), ``from_state``,
    ``to_state``, ``actor`` (str or None), and optionally ``detail``.

    The event is serialised as a single JSON line and appended.  The
    log is never edited, only appended.
    """
    root = Path(project_root)
    events_path = root / CONTROL_DIR / _EVENT_LOG
    events_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure a timestamp is present.
    if "timestamp" not in event:
        event = dict(
            event, timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    line = json.dumps(event, separators=(",", ":")) + "\n"
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Publish state
# ---------------------------------------------------------------------------

_PUBLISH_STATE_FILE = "publish-state.json"


def read_publish_state(project_root: str | Path) -> dict[str, Any] | None:
    """Read ``publish-state.json`` from ``.dde/control/``.

    Returns the parsed dict, or ``None`` if the file does not exist.

    Raises
    ------
    ArtifactError
        If the file exists but is not valid JSON.
    """
    root = Path(project_root)
    path = root / CONTROL_DIR / _PUBLISH_STATE_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(
            "publish-state.json is not valid JSON",
            detail=str(exc),
        ) from exc


def write_publish_state(project_root: str | Path, data: dict[str, Any]) -> Path:
    """Write ``publish-state.json`` to ``.dde/control/``.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    data:
        The publish-state dict.

    Returns
    -------
    Path
        The path that was written.
    """
    root = Path(project_root)
    path = root / CONTROL_DIR / _PUBLISH_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

_WO_PREFIX_RE = re.compile(r"^WO-(\d{3,})")
_RUN_PREFIX_RE = re.compile(r"^RUN-(\d{3,})")
_AR_PREFIX_RE = re.compile(r"^AR-(\d{3,})")
_DR_PREFIX_RE = re.compile(r"^DR-(\d{3,})")
_IC_PREFIX_RE = re.compile(r"^IC-(\d{3,})")


def next_id(project_root: str | Path, record_type: str) -> str:
    """Generate the next sequential ID for a record type.

    For ``"work-order"``: scans ``work-orders/`` for the highest
    ``WO-NNN`` and returns the next.  Defaults to ``WO-001``.

    For ``"run"``: scans ``runs/`` for the highest ``RUN-NNN`` and
    returns the next.  Defaults to ``RUN-001``.

    For ``"concept"``: scans ``concepts/`` for the highest
    ``IC-NNN`` and returns the next.  Defaults to ``IC-001``.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        ``"work-order"``, ``"run"``, or ``"concept"``.

    Returns
    -------
    str
        The next ID, e.g. ``"WO-003"``, ``"RUN-001"``, or ``"IC-001"``.
    """
    root = Path(project_root)

    if record_type == "work-order":
        directory = _control_path(root, "work-order")
        pattern = _WO_PREFIX_RE
        prefix = "WO"
    elif record_type == "run":
        directory = _control_path(root, "run")
        pattern = _RUN_PREFIX_RE
        prefix = "RUN"
    elif record_type == "assessment":
        directory = _control_path(root, "assessment")
        pattern = _AR_PREFIX_RE
        prefix = "AR"
    elif record_type == "decision":
        directory = _control_path(root, "decision")
        pattern = _DR_PREFIX_RE
        prefix = "DR"
    elif record_type == "concept":
        directory = _control_path(root, "concept")
        pattern = _IC_PREFIX_RE
        prefix = "IC"
    else:
        raise SchemaError(
            f"next_id does not support record type {record_type!r}",
            detail="supported types: work-order, run, assessment, decision, concept",
        )

    if not directory.is_dir():
        return f"{prefix}-001"

    max_num = 0
    for path in directory.glob("*.json"):
        m = pattern.match(path.stem)
        if m:
            max_num = max(max_num, int(m.group(1)))

    return f"{prefix}-{max_num + 1:03d}"
