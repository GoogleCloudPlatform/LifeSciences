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

"""`dde workorder` — work-order management for the control plane.

Creates, updates, commits, revises, transitions, accepts, displays,
and lists work-order records in the control-plane state store.  Work
orders are the unit of work assignment in the orchestration design
(orchestration-design-guidance.md S3-S5).

This is NOT a science tool: no ``Sidecar``, ``write_analysis``, HTTP
client, or thresholds.  It is CRUD + state-machine operations over
control records.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import yaml

from ..common import (
    AppState,
    DDEGroup,
    emitter,
    output_options,
    pass_state,
)
from ..core import controlstore
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.output import clip
from ..core.provenance import sha256_file
from ..core.statemachine import validate_transition

# ---------------------------------------------------------------------------
# YAML input fields — the required content fields the user supplies.
# (state, created_at, committed_at are set by the CLI, not by YAML.)
# ---------------------------------------------------------------------------

_YAML_REQUIRED_FIELDS = [
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
]

# Fields the CLI manages; never overwritten from YAML input.
_CLI_MANAGED_FIELDS = {"id", "revision", "state", "created_at", "committed_at"}

# ---------------------------------------------------------------------------
# Override allow-list — which validation checks may be hand-overridden.
# These are judgment calls where a human can verify the underlying
# property by alternative means.  Hardcoded per settled design decision
# (#178): do not make this configurable.
# ---------------------------------------------------------------------------

OVERRIDABLE_CHECKS: set[str] = {
    "report_headings",
    "paths_resolve",
    "provenance_valid",
    "analysis_citations",
    "relay_coverage",
}

# Checks that are never overridable — structural defects or security
# boundaries, not judgment calls.  Any check not in either set is
# rejected as unknown by the override command (safe default).
NON_OVERRIDABLE_CHECKS: set[str] = {
    "deliverables_exist",
    "findings_integrity",
    "deliverables_schema",
    "version_policy",
}


# ---------------------------------------------------------------------------
# Liability-tracker markdown patterns (compiled once at module level).
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^##\s+(.+)", re.MULTILINE)
_SEVERITY_RE = re.compile(r"\*\*Severity\*\*\s*:\s*(\S+)", re.IGNORECASE)
_STATUS_RE = re.compile(r"\*\*Status\*\*\s*:\s*(.+)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read and parse a YAML file, raising SchemaError on failure."""
    if not path.is_file():
        raise SchemaError(
            f"YAML input file not found: {path}",
            remedy="provide a valid path to a work-order YAML file",
        )
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise SchemaError(
            f"invalid YAML in {path}",
            detail=str(exc),
        ) from exc
    if not isinstance(data, dict):
        raise SchemaError(
            f"YAML input must be a mapping, got {type(data).__name__}",
            detail=f"file: {path}",
        )
    return data


def _validate_yaml_fields(data: dict[str, Any]) -> None:
    """Ensure all required YAML content fields are present."""
    missing = [f for f in _YAML_REQUIRED_FIELDS if f not in data]
    if missing:
        raise SchemaError(
            f"missing required fields in YAML input: {', '.join(missing)}",
            remedy="add the missing fields to the YAML file",
        )


def _resolve_yaml_path(project_root: Path, from_file: str) -> Path:
    """Resolve a --from path against the project root."""
    candidate = Path(from_file)
    return candidate if candidate.is_absolute() else project_root / candidate


def _find_latest_revision(
    project_root: Path,
    wo_id: str,
) -> dict[str, Any]:
    """Find the work-order revision with the highest revision number.

    Lists all records in ``work-orders/`` matching the given ID, parses
    each, and returns the one with the highest ``revision`` value.

    Raises
    ------
    ArtifactError
        If no records match.
    """

    def match(r: dict[str, Any]) -> bool:
        return r.get("id") == wo_id

    records = controlstore.list_records(project_root, "work-order", match)
    if not records:
        raise ArtifactError(
            f"work order not found: {wo_id}",
            detail=f"no records found for {wo_id} in work-orders/",
            remedy="check the ID and try again",
        )
    return max(records, key=lambda r: r.get("revision", 0))


def _find_critical_liabilities(project_root: Path) -> list[str]:
    """Return names of active Critical liabilities from the tracker.

    Reads ``program-state/liability-tracker.md`` under *project_root* and
    parses it for sections where severity is Critical and status is NOT
    ``mitigated`` or ``accepted``.

    Returns an empty list if the tracker does not exist or contains no
    active Critical entries.  An absent tracker is not an error — not
    every project has liabilities registered.
    """
    tracker_path = project_root / "program-state" / "liability-tracker.md"
    if not tracker_path.is_file():
        return []
    try:
        text = tracker_path.read_text(encoding="utf-8")
    except OSError:
        return []

    sections = _SECTION_RE.split(text)
    # sections alternates: [preamble, name1, body1, name2, body2, ...]
    critical: list[str] = []
    for i in range(1, len(sections), 2):
        name = sections[i].strip()
        body = sections[i + 1] if i + 1 < len(sections) else ""

        sev_match = _SEVERITY_RE.search(body)
        if not sev_match:
            continue
        severity = sev_match.group(1).strip().lower()
        if severity != "critical":
            continue

        status_match = _STATUS_RE.search(body)
        status = status_match.group(1).strip().lower() if status_match else "open"
        if status in ("mitigated", "accepted"):
            continue

        critical.append(name)

    return critical


def _log_transition(
    project_root: Path,
    wo_id: str,
    revision: int,
    from_state: str | None,
    to_state: str,
) -> None:
    """Append a ``workorder.transition`` event to the event log."""
    controlstore.append_event(
        project_root,
        {
            "type": "workorder.transition",
            "subject_id": wo_id,
            "revision": revision,
            "from_state": from_state,
            "to_state": to_state,
            "actor": None,
            "detail": None,
        },
    )


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def workorder() -> None:
    """Work-order management for the control plane."""


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@workorder.command("create")
@click.option(
    "--from",
    "from_file",
    required=True,
    type=click.Path(),
    help="Path to a YAML file containing work-order fields.",
)
@click.option(
    "--commit",
    "do_commit",
    is_flag=True,
    default=False,
    help="Immediately commit the work order after creation (create + commit in one step).",
)
@output_options
@pass_state
def create_cmd(
    state: AppState,
    from_file: str,
    do_commit: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Create a new work order from a YAML specification.

    With --commit, the work order is created and immediately committed in
    a single invocation.  Both the create and commit steps run with full
    validation — no checks are skipped.  This is equivalent to running
    ``dde workorder create --from <file>`` followed by
    ``dde workorder commit <ID>``.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    # Read and validate YAML input.
    yaml_path = _resolve_yaml_path(project.root, from_file)
    data = _load_yaml(yaml_path)
    _validate_yaml_fields(data)

    # Assign next ID.
    wo_id = controlstore.next_id(project.root, "work-order")
    revision = 1

    # Build the record.
    record: dict[str, Any] = {
        "id": wo_id,
        "revision": revision,
        "state": "proposed",
    }
    for field in _YAML_REQUIRED_FIELDS:
        record[field] = data[field]
    record["created_at"] = _utc_now()
    record["committed_at"] = None

    # Validate the initial transition.
    validate_transition("workorder", None, "proposed")

    # Write the record.
    identifier = f"{wo_id}-r{revision}"
    record_path = controlstore.write_record(
        project.root,
        "work-order",
        identifier,
        record,
    )

    # Log the transition event.
    _log_transition(project.root, wo_id, revision, None, "proposed")

    if do_commit:
        # Immediately commit: runs the full commit validation and
        # context-snapshot logic, identical to a separate `commit` call.
        committed_record, snapshot_path, record_path = _perform_commit(
            project.root,
            wo_id,
        )
        revision = committed_record["revision"]

        emit.data("id", wo_id)
        emit.data("revision", revision)
        emit.data("state", "committed")
        emit.line(
            f"{wo_id} created and committed (revision {revision}, state: committed)"
        )
        emit.path(snapshot_path, role="context_snapshot")
        emit.path(record_path, role="record")
    else:
        # Output (create only).
        emit.data("id", wo_id)
        emit.data("revision", revision)
        emit.data("state", "proposed")
        emit.line(f"{wo_id} created (revision {revision}, state: proposed)")
        emit.path(record_path, role="record")

    emit.flush()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@workorder.command("update")
@click.argument("id", metavar="ID")
@click.option(
    "--from",
    "from_file",
    required=True,
    type=click.Path(),
    help="Path to a YAML file with updated work-order fields.",
)
@output_options
@pass_state
def update_cmd(
    state: AppState,
    id: str,
    from_file: str,
    as_json: bool,
    quiet: bool,
) -> None:
    """Update a proposed work order's content fields."""
    emit = emitter(as_json, quiet)
    project = state.project()

    # Find the latest revision.
    record = _find_latest_revision(project.root, id)

    # Only allowed in proposed state.
    if record.get("state") != "proposed":
        raise Refusal(
            "content fields cannot be modified after committed",
            detail=f"{id} is in state {record.get('state')!r}",
            remedy="create a new revision with `dde workorder revise`",
        )

    # Read YAML input.
    yaml_path = _resolve_yaml_path(project.root, from_file)
    data = _load_yaml(yaml_path)

    # Update content fields (never touch CLI-managed fields).
    for field in _YAML_REQUIRED_FIELDS:
        if field in data:
            record[field] = data[field]

    # Write the updated record.
    revision = record["revision"]
    identifier = f"{id}-r{revision}"
    record_path = controlstore.write_record(
        project.root,
        "work-order",
        identifier,
        record,
    )

    emit.data("id", id)
    emit.data("revision", revision)
    emit.data("state", record["state"])
    emit.line(f"{id} updated (revision {revision})")
    emit.path(record_path, role="record")
    emit.flush()


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


def _perform_commit(
    project_root: Path,
    wo_id: str,
) -> tuple[dict[str, Any], Path, Path]:
    """Execute the commit step for a work order.

    Finds the latest revision, validates the proposed → committed
    transition, creates a context snapshot, and updates the record.
    All validation is identical to running ``commit`` as a separate
    command.

    Returns ``(record, snapshot_path, record_path)``.
    """
    # Find the latest revision.
    record = _find_latest_revision(project_root, wo_id)
    revision = record["revision"]
    current_state = record["state"]

    # Validate transition proposed → committed.
    validate_transition("workorder", current_state, "committed")

    # Validate all required content fields are present.
    missing = [f for f in _YAML_REQUIRED_FIELDS if f not in record]
    if missing:
        raise SchemaError(
            f"work order {wo_id} is missing required fields: {', '.join(missing)}",
            detail="all content fields must be present before committing",
        )

    # Process artifact links: resolve paths and compute checksums.
    context = record.get("context", {})
    artifact_links: list[dict[str, Any]] = []
    if isinstance(context, dict):
        artifact_links = context.get("artifact_links", [])
        if isinstance(artifact_links, list):
            for link in artifact_links:
                if isinstance(link, dict) and "path" in link:
                    artifact_path = (project_root / link["path"]).resolve()
                    if not artifact_path.is_relative_to(project_root.resolve()):
                        raise ArtifactError(
                            f"artifact path escapes project root: {link['path']}",
                            detail=f"resolved to {artifact_path}",
                            remedy="artifact links must reference files within the project",
                        )
                    if not artifact_path.is_file():
                        raise ArtifactError(
                            f"artifact not found: {link['path']}",
                            detail=f"resolved to {artifact_path}",
                            remedy="ensure the artifact exists before committing",
                        )
                    link["sha256"] = sha256_file(artifact_path)

    # Check for Critical liabilities requiring justification.
    critical_liabilities = _find_critical_liabilities(project_root)
    if critical_liabilities:
        justification = record.get("liability_justification")
        if not justification:
            raise Refusal(
                f"Critical liabilities exist but work order {wo_id} has no "
                f"liability_justification field",
                detail=f"Active Critical liabilities: {', '.join(critical_liabilities)}",
                remedy=(
                    "Add a liability_justification field to the work order "
                    "linking to each Critical liability and explaining why "
                    "work should proceed despite it. Example:\n"
                    "  liability_justification:\n"
                    '    L-2: "This cohort validates the contradicted claims"'
                ),
            )
        if not isinstance(justification, dict):
            raise SchemaError(
                f"liability_justification must be a dict mapping liability IDs "
                f"to justification strings, got {type(justification).__name__}",
                detail="each Critical liability must have a string justification",
            )
        # Validate that each value is a non-empty, non-whitespace string.
        for lid, val in justification.items():
            if not isinstance(val, str) or not val.strip():
                raise SchemaError(
                    f"liability_justification[{lid!r}] must be a non-empty "
                    f"string, got {type(val).__name__}"
                    + (": ''" if isinstance(val, str) else ""),
                    detail="each Critical liability must have a string justification",
                )
        unjustified = [lid for lid in critical_liabilities if lid not in justification]
        if unjustified:
            raise Refusal(
                f"liability_justification does not cover all active Critical "
                f"liabilities: {', '.join(unjustified)}",
                detail=f"Active Critical liabilities: {', '.join(critical_liabilities)}",
                remedy="add a justification entry for each listed liability",
            )

    # Build context snapshot.
    # 256 KB default cap (design §4 Q2); configurable via program.yaml
    # once program.yaml configuration support lands.
    MAX_CONTEXT_BYTES = 256 * 1024

    content_str = ""
    if isinstance(context, dict):
        content_str = str(context.get("content", ""))
    content_bytes = content_str.encode("utf-8")
    if len(content_bytes) > MAX_CONTEXT_BYTES:
        raise SchemaError(
            f"context content exceeds size limit "
            f"({len(content_bytes):,} bytes > {MAX_CONTEXT_BYTES:,})",
            detail="reduce the context content, or split it across multiple artifact links",
        )
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()

    snapshot_data: dict[str, Any] = {
        "work_order_id": wo_id,
        "revision": revision,
        "artifact_links": artifact_links if isinstance(artifact_links, list) else [],
        "content": content_str,
        "content_sha256": content_sha256,
        "created_at": _utc_now(),
    }

    snapshot_identifier = f"{wo_id}-r{revision}"
    snapshot_path = controlstore.write_record(
        project_root,
        "context",
        snapshot_identifier,
        snapshot_data,
    )

    # Compute snapshot file sha256 for reference integrity.
    snapshot_file_content = snapshot_path.read_text(encoding="utf-8")
    snapshot_sha256 = hashlib.sha256(
        snapshot_file_content.encode("utf-8"),
    ).hexdigest()

    # Update the work-order record.
    if isinstance(context, dict):
        context["snapshot"] = f"contexts/{snapshot_identifier}.json"
        context["snapshot_sha256"] = snapshot_sha256
    record["context"] = context
    record["committed_at"] = _utc_now()
    record["state"] = "committed"

    identifier = f"{wo_id}-r{revision}"
    record_path = controlstore.write_record(
        project_root,
        "work-order",
        identifier,
        record,
    )

    # Log the transition event.
    _log_transition(project_root, wo_id, revision, current_state, "committed")

    return record, snapshot_path, record_path


@workorder.command("commit")
@click.argument("id", metavar="ID")
@output_options
@pass_state
def commit_cmd(
    state: AppState,
    id: str,
    as_json: bool,
    quiet: bool,
) -> None:
    """Commit a proposed work order: freeze content, create context snapshot."""
    emit = emitter(as_json, quiet)
    project = state.project()

    record, snapshot_path, record_path = _perform_commit(project.root, id)
    revision = record["revision"]

    # Output.
    emit.data("id", id)
    emit.data("revision", revision)
    emit.data("state", "committed")
    emit.line(f"{id} committed (revision {revision})")
    emit.path(snapshot_path, role="context_snapshot")
    emit.path(record_path, role="record")
    emit.flush()


# ---------------------------------------------------------------------------
# revise
# ---------------------------------------------------------------------------


@workorder.command("revise")
@click.argument("id", metavar="ID")
@output_options
@pass_state
def revise_cmd(
    state: AppState,
    id: str,
    as_json: bool,
    quiet: bool,
) -> None:
    """Create a new revision of an existing work order."""
    emit = emitter(as_json, quiet)
    project = state.project()

    # Find the latest revision — must not be in proposed state.
    latest = _find_latest_revision(project.root, id)
    if latest.get("state") == "proposed":
        raise Refusal(
            f"cannot revise {id}: latest revision is still in proposed state",
            detail="commit the current revision before creating a new one",
            remedy=(
                "run `dde workorder commit` first, or use "
                "`update` to modify the proposed revision"
            ),
        )

    # Create new revision from the latest content.
    old_revision = latest["revision"]
    new_revision = old_revision + 1

    record: dict[str, Any] = {
        "id": id,
        "revision": new_revision,
        "state": "proposed",
    }
    for field in _YAML_REQUIRED_FIELDS:
        if field in latest:
            record[field] = latest[field]
    record["created_at"] = _utc_now()
    record["committed_at"] = None

    identifier = f"{id}-r{new_revision}"
    record_path = controlstore.write_record(
        project.root,
        "work-order",
        identifier,
        record,
    )

    # Log the transition event (new revision, from_state=None → proposed).
    _log_transition(project.root, id, new_revision, None, "proposed")

    emit.data("id", id)
    emit.data("revision", new_revision)
    emit.data("state", "proposed")
    emit.line(f"{id} revised (new revision {new_revision}, state: proposed)")
    emit.path(record_path, role="record")
    emit.flush()


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


@workorder.command("transition")
@click.argument("id", metavar="ID")
@click.argument("target_state", metavar="TARGET-STATE")
@output_options
@pass_state
def transition_cmd(
    state: AppState,
    id: str,
    target_state: str,
    as_json: bool,
    quiet: bool,
) -> None:
    """Transition a work order to a new state."""
    emit = emitter(as_json, quiet)
    project = state.project()

    # Find the latest revision.
    record = _find_latest_revision(project.root, id)
    revision = record["revision"]
    current_state = record["state"]

    # Guard: validation_failed → mechanically_validated requires the
    # override subcommand, which enforces mandatory audit metadata
    # (reason, evidence, checks, actor).  Allowing it here would
    # bypass those guardrails entirely.
    if (
        current_state == "validation_failed"
        and target_state == "mechanically_validated"
    ):
        raise Refusal(
            "cannot transition directly from 'validation_failed' to "
            "'mechanically_validated'",
            detail=(
                "this transition requires per-check justification, "
                "evidence, and actor attribution"
            ),
            remedy="use `dde workorder override` instead",
        )

    # Guard: submitted → mechanically_validated requires running
    # the 8 mechanical checks via `validate check`, which performs
    # this transition internally when all checks pass.  Allowing it
    # here would bypass the entire mechanical validation system.
    if current_state == "submitted" and target_state == "mechanically_validated":
        raise Refusal(
            "cannot transition directly from 'submitted' to 'mechanically_validated'",
            detail=("this transition requires all 8 mechanical checks to pass"),
            remedy=f"use `dde validate check {id}` instead",
        )

    # Guard: proposed → committed requires running the full commit
    # validation and context-snapshot logic via `dde workorder commit`.
    # Allowing it here would bypass artifact path checks, liability
    # gates, context size limits, and snapshot creation.
    if current_state == "proposed" and target_state == "committed":
        raise Refusal(
            "cannot transition directly from 'proposed' to 'committed'",
            detail=(
                "committing a work order requires content validation, "
                "liability checks, and context snapshotting"
            ),
            remedy=f"use `dde workorder commit {id}` instead",
        )

    # Validate the transition.
    validate_transition("workorder", current_state, target_state)

    # Update state in the record.
    record["state"] = target_state
    identifier = f"{id}-r{revision}"
    controlstore.write_record(project.root, "work-order", identifier, record)

    # Log the transition event.
    _log_transition(project.root, id, revision, current_state, target_state)

    emit.data("id", id)
    emit.data("revision", revision)
    emit.data("from_state", current_state)
    emit.data("to_state", target_state)
    emit.line(f"{id}: {current_state} → {target_state}")
    emit.flush()


# ---------------------------------------------------------------------------
# override
# ---------------------------------------------------------------------------


@workorder.command("override")
@click.argument("id", metavar="ID")
@click.option(
    "--reason",
    required=True,
    help="Human-authored justification for overriding validation failures.",
)
@click.option(
    "--evidence",
    required=True,
    help=(
        "Path or reference to supporting evidence "
        "(e.g. program-state/decision-log.md or a URL)."
    ),
)
@click.option(
    "--checks",
    required=True,
    help="Comma-separated list of failed checks to override.",
)
@click.option(
    "--actor",
    default=None,
    help="Name or role of the person authorizing this override (default: unattributed).",
)
@output_options
@pass_state
def override_cmd(
    state: AppState,
    id: str,
    reason: str,
    evidence: str,
    checks: str,
    actor: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Override specific validation failures and transition to mechanically_validated.

    Overrides are per-check, not blanket: each named check must be on the
    overridable allow-list and must actually appear in the work order's
    current checks_failed list.  Some checks (deliverables_exist,
    findings_integrity, path-confinement violations) are never overridable
    because they represent structural defects or security boundaries, not
    judgment calls.

    The override is recorded in the event log (with actor and detail) and
    as an entry in the work order's override_history list.  Neither the
    validation record nor the checks_failed list is modified — override
    history is permanent context, not a retroactive edit.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    # Resolve actor: quotable and true, never silently blank.
    actor_str = actor.strip() if actor else "unattributed"
    if not actor_str:
        actor_str = "unattributed"

    # Validate --reason is non-empty.
    reason = reason.strip()
    if not reason:
        raise Refusal(
            "override reason must not be empty",
            detail="a human-authored justification is required",
            remedy="provide a non-empty --reason explaining why the override is appropriate",
        )

    # Validate --evidence is non-empty.
    evidence = evidence.strip()
    if not evidence:
        raise Refusal(
            "override evidence must not be empty",
            detail="a path or reference to supporting evidence is required",
            remedy=(
                "provide --evidence pointing to a decision-log entry "
                "or other documentation"
            ),
        )

    # Parse and validate --checks.  Deduplicate while preserving order.
    check_names = list(dict.fromkeys(c.strip() for c in checks.split(",") if c.strip()))
    if not check_names:
        raise Refusal(
            "at least one check must be named for override",
            remedy=(
                "provide --checks with comma-separated check names "
                "from the failed checks list"
            ),
        )

    # Validate each check name against the allow-list.
    non_overridable_requested = [c for c in check_names if c in NON_OVERRIDABLE_CHECKS]
    if non_overridable_requested:
        raise Refusal(
            f"cannot override non-overridable check(s): "
            f"{', '.join(non_overridable_requested)}",
            detail=(
                f"{', '.join(non_overridable_requested)} represent structural "
                "defects or security boundaries that cannot be overridden"
            ),
            remedy=(
                "remove these checks from --checks; "
                "only overridable checks may be named"
            ),
        )

    unknown_checks = [c for c in check_names if c not in OVERRIDABLE_CHECKS]
    if unknown_checks:
        raise Refusal(
            f"unknown or non-overridable check(s): {', '.join(unknown_checks)}",
            detail=(f"overridable checks: {', '.join(sorted(OVERRIDABLE_CHECKS))}"),
            remedy="provide only check names from the overridable set",
        )

    # Find the latest WO revision.
    record = _find_latest_revision(project.root, id)
    revision = record["revision"]
    current_state = record["state"]

    # WO must be in validation_failed state.
    if current_state != "validation_failed":
        raise Refusal(
            f"work order {id} is in state {current_state!r}, "
            "expected 'validation_failed'",
            detail=(
                "overrides are only available for work orders "
                "that have failed validation"
            ),
            remedy=("only work orders in 'validation_failed' state can be overridden"),
        )

    # Read the latest validation record to get checks_failed.
    val_identifier = f"{id}-r{revision}"
    val_record = controlstore.read_record(
        project.root,
        "validation",
        val_identifier,
    )
    val_checks = val_record.get("checks", [])
    checks_failed = [c["name"] for c in val_checks if c.get("result") == "fail"]

    # Validate each named check actually failed.
    not_failed = [c for c in check_names if c not in checks_failed]
    if not_failed:
        raise Refusal(
            f"check(s) not in current checks_failed: {', '.join(not_failed)}",
            detail=f"current checks_failed: {', '.join(checks_failed)}",
            remedy=(
                "only name checks that actually failed in the most recent validation"
            ),
        )

    # Block if any failed check is non-overridable — even if the
    # operator only named overridable checks, the presence of a
    # non-overridable failure means the work order cannot be overridden
    # until that structural/security issue is resolved.
    non_overridable_in_failed = [
        c for c in checks_failed if c in NON_OVERRIDABLE_CHECKS
    ]
    if non_overridable_in_failed:
        raise Refusal(
            f"cannot override: non-overridable check(s) also failed: "
            f"{', '.join(non_overridable_in_failed)}",
            detail=(
                f"{', '.join(non_overridable_in_failed)} represent structural "
                "defects or security boundaries that must be resolved before "
                "any override is permitted"
            ),
            remedy=(
                "fix the non-overridable check failures first, then retry "
                "the override for the remaining overridable checks"
            ),
        )

    # Block if not all failed checks are covered — partial overrides
    # would leave unaddressed failures, effectively bypassing validation
    # for the uncovered checks.
    uncovered = set(checks_failed) - set(check_names)
    if uncovered:
        raise Refusal(
            f"override must cover all failed checks; uncovered: "
            f"{', '.join(sorted(uncovered))}",
            detail="partial overrides leave unaddressed failures that would bypass validation",
            remedy=f"add {', '.join(sorted(uncovered))} to --checks, or fix them first",
        )

    # Check for path-confinement violations in ALL failed checks' detail,
    # not just the ones named in --checks.  A check whose failure includes
    # path_confinement_failures represents a security boundary and is never
    # overridable regardless of the allow-list.
    checks_by_name = {c["name"]: c for c in val_checks}
    for failed_check_name in checks_failed:
        check_data = checks_by_name.get(failed_check_name, {})
        detail = check_data.get("detail", {})
        if isinstance(detail, dict):
            pcf = detail.get("path_confinement_failures")
            if pcf and isinstance(pcf, list) and len(pcf) > 0:
                raise Refusal(
                    f"cannot override: check {failed_check_name!r} has "
                    "path-confinement violations",
                    detail=(
                        "path-confinement violations represent security "
                        "boundaries that cannot be overridden"
                    ),
                    remedy=(
                        "resolve the path-confinement issue before "
                        "attempting any override"
                    ),
                )
            # Also check detail["issues"] for path-escape violations
            # (_check_analysis_citations stores them here instead of
            # path_confinement_failures).
            issues = detail.get("issues")
            if issues and isinstance(issues, list):
                path_escapes = [
                    i
                    for i in issues
                    if (isinstance(i, str) and "escapes project root" in i.lower())
                    or (
                        isinstance(i, dict)
                        and isinstance(i.get("issue"), str)
                        and "escapes project root" in i["issue"].lower()
                    )
                ]
                if path_escapes:
                    raise Refusal(
                        f"cannot override: check {failed_check_name!r} has "
                        "path-confinement violations",
                        detail=(
                            "path-confinement violations represent security "
                            "boundaries that cannot be overridden"
                        ),
                        remedy=(
                            "resolve the path-confinement issue before "
                            "attempting any override"
                        ),
                    )

    # All validation passed — perform the override.
    target_state = "mechanically_validated"
    validate_transition("workorder", current_state, target_state)

    # Build override entry for the WO record's override_history.
    override_entry = {
        "overridden_at": _utc_now(),
        "actor": actor_str,
        "reason": reason,
        "evidence": evidence,
        "checks_overridden": check_names,
        "from_state": current_state,
        "to_state": target_state,
        "revision": revision,
    }

    # Append to override_history on the WO record (append-only,
    # never cleared — a WO could be overridden more than once across
    # revisions or re-validations).
    override_history = record.get("override_history", [])
    if not isinstance(override_history, list):
        override_history = []
    override_history.append(override_entry)
    record["override_history"] = override_history

    # Update state.
    record["state"] = target_state
    identifier = f"{id}-r{revision}"
    controlstore.write_record(
        project.root,
        "work-order",
        identifier,
        record,
    )

    # Log the transition event with populated detail and actor.
    # This is the first transition event to ever populate these fields.
    controlstore.append_event(
        project.root,
        {
            "type": "workorder.transition",
            "subject_id": id,
            "revision": revision,
            "from_state": current_state,
            "to_state": target_state,
            "actor": actor_str,
            "detail": {
                "override": True,
                "reason": reason,
                "evidence": evidence,
                "checks_overridden": check_names,
            },
        },
    )

    # Output.
    emit.data("id", id)
    emit.data("revision", revision)
    emit.data("from_state", current_state)
    emit.data("to_state", target_state)
    emit.data("actor", actor_str)
    emit.data("checks_overridden", check_names)
    emit.line(f"{id}: {current_state} → {target_state} (override)")
    emit.line(f"  Actor:    {actor_str}")
    emit.line(f"  Reason:   {reason}")
    emit.line(f"  Evidence: {evidence}")
    emit.line(f"  Checks:   {', '.join(check_names)}")
    emit.flush()


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@workorder.command("show")
@click.argument("id", metavar="ID")
@click.option(
    "--revision",
    "revision_num",
    type=int,
    default=None,
    help="Show a specific revision (default: latest).",
)
@output_options
@pass_state
def show_cmd(
    state: AppState,
    id: str,
    revision_num: int | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Display a work order (default: latest revision)."""
    emit = emitter(as_json, quiet)
    project = state.project()

    if revision_num is not None:
        identifier = f"{id}-r{revision_num}"
        record = controlstore.read_record(project.root, "work-order", identifier)
    else:
        record = _find_latest_revision(project.root, id)
        revision_num = record["revision"]

    identifier = f"{id}-r{revision_num}"
    record_path = (
        project.root / controlstore.CONTROL_DIR / "work-orders" / f"{identifier}.json"
    )

    if as_json:
        # Full record dump.
        for k, v in record.items():
            emit.data(k, v)
        emit.path(record_path, role="record")
        emit.flush()
        return

    if quiet:
        emit.path(record_path)
        emit.flush()
        return

    # Bounded human summary.
    emit.line(
        f"Work Order: {record.get('id')}  "
        f"revision {record.get('revision')}  "
        f"[{record.get('state')}]"
    )
    question = record.get("decision_question", "")
    emit.line(f"  Question:    {clip(question, 100)}")
    emit.line(f"  Role:        {record.get('requested_role', '')}")
    emit.line(
        f"  Stage:       {record.get('stage', '')}  Cycle: {record.get('cycle', '')}"
    )
    emit.line(
        f"  Priority:    {record.get('priority', '')}  "
        f"Resource: {record.get('resource_class', '')}"
    )
    emit.line(f"  Report to:   {record.get('report_to', '')}")
    emit.line(f"  Created:     {record.get('created_at', '')}")
    committed = record.get("committed_at")
    if committed:
        emit.line(f"  Committed:   {committed}")
    # Display override history if present.
    override_history = record.get("override_history")
    if isinstance(override_history, list) and override_history:
        emit.line("")
        emit.line(f"  Override history ({len(override_history)}):")
        for i, entry in enumerate(override_history, 1):
            emit.line(f"    [{i}] {entry.get('overridden_at', '?')}")
            emit.line(f"        Actor:    {entry.get('actor', '?')}")
            emit.line(
                f"        Checks:   {', '.join(entry.get('checks_overridden', []))}"
            )
            emit.line(f"        Reason:   {clip(entry.get('reason', ''), 80)}")
            emit.line(f"        Evidence: {clip(entry.get('evidence', ''), 80)}")
    emit.path(record_path, role="record")
    emit.flush()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@workorder.command("list")
@click.option("--state", "state_filter", default=None, help="Filter by state.")
@click.option("--stage", "stage_filter", default=None, help="Filter by stage.")
@click.option("--cycle", "cycle_filter", default=None, help="Filter by cycle.")
@output_options
@pass_state
def list_cmd(
    state: AppState,
    state_filter: str | None,
    stage_filter: str | None,
    cycle_filter: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """List work orders, optionally filtered by state, stage, or cycle."""
    emit = emitter(as_json, quiet)
    project = state.project()

    def match(r: dict[str, Any]) -> bool:
        if state_filter and r.get("state") != state_filter:
            return False
        if stage_filter and r.get("stage") != stage_filter:
            return False
        if cycle_filter and r.get("cycle") != cycle_filter:
            return False
        return True

    records = controlstore.list_records(project.root, "work-order", match)

    # Deduplicate to latest revision per ID.
    by_id: dict[str, dict[str, Any]] = {}
    for r in records:
        wo_id = r.get("id", "")
        existing = by_id.get(wo_id)
        if existing is None or r.get("revision", 0) > existing.get("revision", 0):
            by_id[wo_id] = r
    latest = sorted(by_id.values(), key=lambda r: r.get("id", ""))

    if as_json:
        emit.data("work_orders", latest)
        emit.data("count", len(latest))
        emit.flush()
        return

    if quiet:
        for r in latest:
            wo_id = r.get("id", "")
            rev = r.get("revision", 1)
            ident = f"{wo_id}-r{rev}"
            path = (
                project.root
                / controlstore.CONTROL_DIR
                / "work-orders"
                / f"{ident}.json"
            )
            emit.path(path)
        emit.flush()
        return

    if not latest:
        emit.line("No work orders found.")
        emit.flush()
        return

    # Tabular summary.
    emit.line(f"{'ID':<10} {'Rev':>3} {'State':<22} Question")
    emit.line(f"{'─' * 10} {'─' * 3} {'─' * 22} {'─' * 40}")
    for r in latest:
        wo_id = r.get("id", "")
        rev = r.get("revision", "?")
        wo_state = r.get("state", "?")
        question = clip(r.get("decision_question", ""), 50)
        emit.line(f"{wo_id:<10} {rev:>3} {wo_state:<22} {question}")
    emit.flush()


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------

# The mechanical intermediate chain that ``accept`` auto-steps through.
# Only the final hop (submitted → mechanically_validated) carries a real
# validation gate; the earlier hops are bookkeeping that a specialist or
# system would normally drive individually.
_MECHANICAL_CHAIN: list[str] = [
    "committed",
    "queued",
    "in_progress",
    "submitted",
]


@workorder.command("accept")
@click.argument("id", metavar="ID")
@output_options
@pass_state
def accept_cmd(
    state: AppState,
    id: str,
    as_json: bool,
    quiet: bool,
) -> None:
    """Walk a work order through mechanical states to mechanically_validated.

    Auto-steps through committed → queued → in_progress → submitted,
    then runs the full 8-check mechanical validation (the real
    ``validate check`` logic — not a state flip).

    \b
    Behaviour:
      • Starts from the WO's current state and walks forward — a WO
        already at ``in_progress`` skips the committed → queued steps.
      • Every intermediate hop is logged via the event log (one event
        per transition) so the audit trail is identical to manual calls.
      • If mechanical validation passes the WO reaches
        ``mechanically_validated`` and the command stops — scientific
        acceptance is a separate judgment call.
      • If validation fails the WO moves to ``validation_failed`` and
        the command exits with the same failure output that
        ``validate check`` would produce, plus a remedy.

    \b
    Does NOT:
      • Auto-transition to ``scientifically_accepted``.
      • Bypass the #178 override guard or the #237 submitted-bypass guard.
      • Skip or weaken the 8 mechanical validation checks.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    # Find the latest revision.
    record = _find_latest_revision(project.root, id)
    revision = record["revision"]
    current_state = record["state"]

    # --- Already at or past the mechanical gate ---
    if current_state == "mechanically_validated":
        emit.data("id", id)
        emit.data("revision", revision)
        emit.data("state", current_state)
        emit.line(f"{id} is already mechanically_validated (revision {revision}).")
        emit.line("Ready for scientific acceptance:")
        emit.line(f"  dde workorder transition {id} scientifically_accepted")
        emit.flush()
        return

    # --- Gate: only states in the mechanical chain are auto-steppable ---
    if current_state == "proposed":
        raise Refusal(
            f"cannot accept {id}: work order is still in 'proposed' state",
            detail="the work order must be committed before acceptance",
            remedy=f"run `dde workorder commit {id}` first",
        )

    if current_state == "validation_failed":
        raise Refusal(
            f"cannot accept {id}: work order is in 'validation_failed' state",
            detail="fix the failing deliverables or override eligible checks first",
            remedy=(
                f"transition back with `dde workorder transition {id} in_progress`, "
                f"fix issues, then re-run `dde wo accept {id}`; "
                f"or use `dde workorder override {id}` to override eligible checks"
            ),
        )

    if current_state not in _MECHANICAL_CHAIN:
        raise Refusal(
            f"cannot accept {id} from state {current_state!r}",
            detail=(
                f"accept auto-steps through: "
                f"{' → '.join(_MECHANICAL_CHAIN)} → mechanically_validated"
            ),
            remedy=(
                f"the work order must be in one of "
                f"{', '.join(_MECHANICAL_CHAIN)} to use accept"
            ),
        )

    # --- Walk through the mechanical intermediate transitions ---
    start_idx = _MECHANICAL_CHAIN.index(current_state)

    for i in range(start_idx, len(_MECHANICAL_CHAIN) - 1):
        from_state = _MECHANICAL_CHAIN[i]
        to_state = _MECHANICAL_CHAIN[i + 1]

        validate_transition("workorder", from_state, to_state)
        record["state"] = to_state
        identifier = f"{id}-r{revision}"
        controlstore.write_record(
            project.root,
            "work-order",
            identifier,
            record,
        )
        _log_transition(project.root, id, revision, from_state, to_state)

        emit.line(f"  {id}: {from_state} → {to_state}")

    # --- At 'submitted': run real mechanical validation ---
    emit.line("")
    emit.line(f"Running mechanical validation on {id}...")
    emit.line("")

    # Import here to avoid circular imports at module level; the
    # validate module is a sibling in the same commands package.
    from .validate import _perform_validation

    _wo_record, val_path, overall_result, checks, checks_failed, val_from, val_to = (
        _perform_validation(project.root, id)
    )

    # --- Report validation results ---
    for c in checks:
        status = c["result"].upper()
        emit.line(f"  {c['name']:<25} {status}")
    emit.line("")

    if overall_result in ("pass", "pass_with_warnings"):
        emit.data("id", id)
        emit.data("revision", revision)
        emit.data("from_state", current_state)
        emit.data("to_state", val_to)
        emit.data("validation_result", overall_result)
        emit.line(f"{id}: {val_from} → {val_to}  [validation {overall_result.upper()}]")
        emit.line("")
        emit.line(
            f"{id} is now mechanically_validated and ready for scientific acceptance."
        )
        emit.line(f"  dde workorder transition {id} scientifically_accepted")
        emit.path(val_path, role="validation_record")
    else:
        emit.data("id", id)
        emit.data("revision", revision)
        emit.data("from_state", current_state)
        emit.data("to_state", val_to)
        emit.data("validation_result", overall_result)
        emit.data("checks_failed", checks_failed)
        emit.line(f"{id}: {val_from} → {val_to}  [validation {overall_result.upper()}]")
        emit.line("")
        emit.line(f"Failed checks: {', '.join(checks_failed)}")
        emit.line("")
        emit.line("Remedy:")
        emit.line("  1. Fix the failing deliverables and resubmit, or")
        emit.line(f"  2. Use `dde workorder override {id}` to override eligible checks")
        emit.path(val_path, role="validation_record")

    emit.flush()


# ---------------------------------------------------------------------------
# accept-all
# ---------------------------------------------------------------------------


def _list_latest_work_orders(project_root: Path) -> list[dict[str, Any]]:
    """Return the latest revision of every work order, sorted by id."""
    records = controlstore.list_records(project_root, "work-order")
    by_id: dict[str, dict[str, Any]] = {}
    for r in records:
        wo_id = r.get("id", "")
        existing = by_id.get(wo_id)
        if existing is None or r.get("revision", 0) > existing.get("revision", 0):
            by_id[wo_id] = r
    return sorted(by_id.values(), key=lambda r: r.get("id", ""))


def _try_accept_single(
    project_root: Path,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Attempt to walk a single work order through to scientifically_accepted.

    Returns a result dict with keys: id, revision, outcome, detail, checks.
    Does NOT raise on failure — returns status for batch reporting.
    """
    wo_id = record["id"]
    revision = record["revision"]
    current_state = record["state"]

    # Already accepted — nothing to do.
    if current_state == "scientifically_accepted":
        return {
            "id": wo_id,
            "revision": revision,
            "outcome": "already_accepted",
            "detail": "already scientifically_accepted",
            "checks": [],
        }

    # States that can't be auto-walked.
    if current_state == "proposed":
        return {
            "id": wo_id,
            "revision": revision,
            "outcome": "skip",
            "detail": "needs commit first",
            "checks": [],
        }

    if current_state == "validation_failed":
        return {
            "id": wo_id,
            "revision": revision,
            "outcome": "skip",
            "detail": "needs override or fix first",
            "checks": [],
        }

    if (
        current_state not in _MECHANICAL_CHAIN
        and current_state != "mechanically_validated"
    ):
        return {
            "id": wo_id,
            "revision": revision,
            "outcome": "skip",
            "detail": f"cannot accept from state {current_state!r}",
            "checks": [],
        }

    # Walk through mechanical intermediate transitions if needed.
    ran_checks: list[dict[str, Any]] = []

    if current_state in _MECHANICAL_CHAIN:
        start_idx = _MECHANICAL_CHAIN.index(current_state)
        for i in range(start_idx, len(_MECHANICAL_CHAIN) - 1):
            from_state = _MECHANICAL_CHAIN[i]
            to_state = _MECHANICAL_CHAIN[i + 1]
            validate_transition("workorder", from_state, to_state)
            record["state"] = to_state
            identifier = f"{wo_id}-r{revision}"
            controlstore.write_record(
                project_root,
                "work-order",
                identifier,
                record,
            )
            _log_transition(project_root, wo_id, revision, from_state, to_state)

        # Now at 'submitted' — run mechanical validation.
        from .validate import _perform_validation

        try:
            (
                wo_record,
                _val_path,
                overall_result,
                checks,
                _checks_failed,
                _val_from,
                _val_to,
            ) = _perform_validation(project_root, wo_id)
        except Exception as exc:
            return {
                "id": wo_id,
                "revision": revision,
                "outcome": "fail",
                "detail": str(exc),
                "checks": [],
            }

        ran_checks = checks

        if overall_result not in ("pass", "pass_with_warnings"):
            # Build failure detail from checks.
            fail_details: list[str] = []
            for c in checks:
                if c["result"] == "fail":
                    fail_details.append(c["name"])
            return {
                "id": wo_id,
                "revision": revision,
                "outcome": "fail",
                "detail": fail_details,
                "checks": checks,
            }

        record = wo_record  # Updated by _perform_validation.

    # At mechanically_validated — transition to scientifically_accepted.
    validate_transition(
        "workorder", "mechanically_validated", "scientifically_accepted"
    )
    record["state"] = "scientifically_accepted"
    identifier = f"{wo_id}-r{revision}"
    controlstore.write_record(project_root, "work-order", identifier, record)
    _log_transition(
        project_root,
        wo_id,
        revision,
        "mechanically_validated",
        "scientifically_accepted",
    )

    has_warnings = any(c.get("result") == "warning" for c in ran_checks)
    outcome = "pass_with_warnings" if has_warnings else "pass"

    return {
        "id": wo_id,
        "revision": revision,
        "outcome": outcome,
        "detail": "accepted",
        "checks": ran_checks,
    }


@workorder.command("accept-all")
@output_options
@pass_state
def accept_all_cmd(
    state: AppState,
    as_json: bool,
    quiet: bool,
) -> None:
    """Validate and accept all eligible work orders in one pass.

    Lists all work orders, runs mechanical validation on each eligible
    one, and transitions all PASSING work orders to
    ``scientifically_accepted``.

    \b
    Behaviour:
      • Auto-steps each WO through the mechanical chain
        (committed → queued → in_progress → submitted), runs the full
        8-check mechanical validation, and on pass transitions to
        scientifically_accepted.
      • Reports a consolidated table showing each WO's outcome.
      • Work orders that FAIL validation are left in validation_failed
        and reported — they are NOT auto-overridden.

    \b
    Does NOT:
      • Auto-override any failures — use ``dde workorder override``
        for individual WOs that need it.
      • Skip or weaken any mechanical validation checks.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    all_wos = _list_latest_work_orders(project.root)
    if not all_wos:
        emit.line("No work orders found.")
        emit.flush()
        return

    results: list[dict[str, Any]] = []
    for wo in all_wos:
        result = _try_accept_single(project.root, wo)
        results.append(result)

    # Build consolidated output.
    accepted_count = sum(
        1
        for r in results
        if r["outcome"] in ("pass", "pass_with_warnings", "already_accepted")
    )
    failed_count = sum(1 for r in results if r["outcome"] == "fail")
    skipped_count = sum(1 for r in results if r["outcome"] == "skip")

    if as_json:
        emit.data("results", results)
        emit.data("accepted", accepted_count)
        emit.data("failed", failed_count)
        emit.data("skipped", skipped_count)
        emit.flush()
        return

    emit.line("Work order validation:")
    for r in results:
        wo_id = r["id"]
        outcome = r["outcome"]

        if outcome == "pass":
            emit.line(f"  {wo_id:<10} PASS      → accepted")
        elif outcome == "pass_with_warnings":
            # Count warnings.
            warn_count = sum(
                1 for c in r.get("checks", []) if c.get("result") == "warning"
            )
            emit.line(
                f"  {wo_id:<10} PASS (w)  → accepted "
                f"({warn_count} warning{'s' if warn_count != 1 else ''})"
            )
        elif outcome == "already_accepted":
            emit.line(f"  {wo_id:<10} PASS      → already accepted")
        elif outcome == "fail":
            detail = r.get("detail", [])
            if isinstance(detail, list) and detail:
                first_line = f"  {wo_id:<10} FAIL      → {detail[0]}"
                emit.line(first_line)
                for reason in detail[1:]:
                    emit.line(f"  {'':10}             {reason}")
            else:
                emit.line(f"  {wo_id:<10} FAIL      → {detail}")
        elif outcome == "skip":
            emit.line(f"  {wo_id:<10} SKIP      → {r.get('detail', '?')}")

    emit.line("")
    emit.line(
        f"Accepted: {accepted_count}  Failed: {failed_count}  Skipped: {skipped_count}"
    )

    if failed_count > 0:
        emit.line("")
        emit.line(
            "Failed work orders need manual attention. "
            "Use `dde workorder override` for eligible checks."
        )

    emit.flush()
