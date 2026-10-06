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

"""``dde program`` — cross-phase state continuity for the control plane.

Imports accepted work-order records (and their associated runs,
contexts, validations) from a prior phase's control plane into the
current project's control plane, solving the Phase 1 → Phase 2 state
continuity problem described in issue #78.

This is NOT a science tool: no ``Sidecar``, no HTTP client, no
thresholds.  It is a structural import operation over control records.
"""

from __future__ import annotations

import json
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
from ..core.concepts import CONCEPT_SCHEMA
from ..core.errors import ArtifactError, Refusal, SchemaError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACCEPTED_STATE = "scientifically_accepted"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_source(source: str) -> Path:
    """Resolve the source path to a project root containing ``.dde/control/``.

    Accepts either a project root (containing ``.dde/control/``) or a
    direct path to a ``.dde/control/`` directory.  Returns the project
    root in both cases.

    Raises
    ------
    ArtifactError
        If the path is not a readable directory or does not contain a
        dde control plane.
    """
    path = Path(source).resolve()
    if not path.is_dir():
        raise ArtifactError(
            f"source path is not a readable directory: {path}",
            remedy="provide a path to a prior phase's project root "
            "or its .dde/control/ directory",
        )

    # If pointing directly at a .dde/control/ directory, go up two levels.
    if path.name == "control" and path.parent.name == ".dde":
        project_root = path.parent.parent
        if (project_root / controlstore.CONTROL_DIR).is_dir():
            return project_root

    # Check if it's a project root with .dde/control/ inside.
    if (path / controlstore.CONTROL_DIR).is_dir():
        return path

    raise ArtifactError(
        f"source path does not contain a dde control plane: {path}",
        detail=f"expected {controlstore.CONTROL_DIR}/ directory inside",
        remedy="provide a path to a prior phase's project root "
        "or its .dde/control/ directory",
    )


def _read_source_records(
    source_root: Path,
    record_type: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Read all records of a given type from the source.

    Returns a list of ``(identifier, data)`` pairs sorted by filename.
    The identifier is the file stem (e.g. ``WO-001-r1``, ``RUN-003``).

    Silently skips files that are not valid JSON or not dicts — the same
    lenience ``list_records`` shows during listing.
    """
    subdir = controlstore.RECORD_TYPES.get(record_type)
    if subdir is None:
        return []
    directory = source_root / controlstore.CONTROL_DIR / subdir
    if not directory.is_dir():
        return []

    source_resolved = source_root.resolve()

    results: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        if not path.resolve().is_relative_to(source_resolved):
            continue  # symlink escapes source tree
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            results.append((path.stem, data))
    return results


def _conflict_exists(
    dest_root: Path,
    record_type: str,
    identifier: str,
) -> bool:
    """Return True if a record with this identifier already exists."""
    subdir = controlstore.RECORD_TYPES.get(record_type)
    if subdir is None:
        return False
    path = dest_root / controlstore.CONTROL_DIR / subdir / f"{identifier}.json"
    return path.is_file()


# ---------------------------------------------------------------------------
# Markdown work-order parsing
# ---------------------------------------------------------------------------
#
# INFERRED FORMAT — NOT SOURCED FROM A REAL ARTIFACT
#
# No actual Phase 1 hand-tracked markdown work-order files were found in
# any reachable directory.  The format below was approved by tools-lead
# (2026-08-19) as a representative schema for the parser to target.  If a
# real example surfaces later, this parser should be reviewed against it
# and adjusted.
#
# Expected format:
#
#     ---
#     id: WO-001
#     state: scientifically_accepted
#     decision_question: "..."
#     requested_role: "..."
#     stage: 1
#     cycle: 1
#     priority: normal
#     resource_class: standard
#     report_to: "..."
#     dependencies: []
#     capabilities: []
#     ---
#
#     ## Context
#     (free text → context.content)
#
#     ## Deliverables
#     - name: description   → deliverables dict
#
#     ## Acceptance Criteria
#     - bullet list          → acceptance_criteria list
#
#     ## Alert Policy
#     - name: condition      → alert_policy dict
#     (or free text          → {"mode": text})
#
# The parser is tolerant but not silent: missing frontmatter or fields
# that cannot be confidently extracted cause the file to be refused with
# a specific reason, never silently skipped or guessed.

_WO_ID_PATTERN = re.compile(r"^WO-\d{3,}$")
_WO_NUM_PATTERN = re.compile(r"^WO-(\d{3,})")

# Frontmatter fields that map directly to work-order record fields.
_FRONTMATTER_FIELDS = [
    "id",
    "state",
    "decision_question",
    "requested_role",
    "stage",
    "cycle",
    "priority",
    "resource_class",
    "report_to",
    "dependencies",
    "capabilities",
]

# Body sections (H2 headings) that map to work-order record fields.
_BODY_SECTION_MAP = {
    "Context": "context",
    "Deliverables": "deliverables",
    "Acceptance Criteria": "acceptance_criteria",
    "Alert Policy": "alert_policy",
}


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.

    Expects the file to start with ``---``, followed by YAML, followed
    by a closing ``---``, then the markdown body.

    Returns ``(frontmatter_dict, body_text)``.

    Raises
    ------
    SchemaError
        If frontmatter is missing, unclosed, or not valid YAML.
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise SchemaError(
            "markdown work order has no YAML frontmatter",
            detail="expected file to start with '---' followed by YAML fields",
            remedy="add YAML frontmatter with required fields "
            "(id, state, decision_question, etc.)",
        )
    # Find closing delimiter.
    rest = stripped[3:]
    # Skip the newline after the opening ---
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end == -1:
        raise SchemaError(
            "markdown work order has unclosed YAML frontmatter",
            detail="expected a closing '---' line after the frontmatter block",
        )
    frontmatter_text = rest[:end]
    body = rest[end + 4 :]  # skip \n---

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise SchemaError(
            "markdown work order has invalid YAML frontmatter",
            detail=str(exc),
        ) from exc

    if not isinstance(frontmatter, dict):
        raise SchemaError(
            "markdown work order frontmatter must be a YAML mapping",
            detail=f"got {type(frontmatter).__name__}",
        )

    return frontmatter, body.strip()


def _parse_sections(body: str) -> dict[str, str]:
    """Parse markdown H2 sections into ``{heading: content}``.

    Only ``## `` headings are recognised; deeper headings are treated as
    body text within the current section.
    """
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    if current_heading is not None:
        sections[current_heading] = "\n".join(current_lines).strip()

    return sections


def _parse_bullet_list(text: str) -> list[str]:
    """Extract items from a markdown bullet list (``-``, ``*``, ``+``)."""
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in ("- ", "* ", "+ "):
            if stripped.startswith(prefix):
                items.append(stripped[len(prefix) :].strip())
                break
    return items


def _parse_deliverables_section(text: str) -> dict[str, Any] | str:
    """Parse the Deliverables section into a dict.

    Expects bullet items.  Items containing ``": "`` are split into
    ``name: description``; the name is normalised to a ``snake_case``
    key.  Items without a colon derive their key from the full text.

    Returns a dict on success, or an error string on failure.
    """
    items = _parse_bullet_list(text)
    if not items:
        return "Deliverables section has no bullet items"

    deliverables: dict[str, Any] = {}
    for item in items:
        if ": " in item:
            name, desc = item.split(": ", 1)
        else:
            name, desc = item, item
        key = re.sub(
            r"[^a-z0-9_]", "", name.strip().lower().replace(" ", "_").replace("-", "_")
        )
        if not key:
            return f"cannot derive a key from deliverable item: {item!r}"
        deliverables[key] = {"description": desc.strip()}
    return deliverables


def _parse_alert_policy_section(text: str) -> dict[str, Any]:
    """Parse the Alert Policy section into a dict.

    Tries bullet-list format first (``name: condition``).  Falls back
    to ``{"mode": text}`` for free-text content.
    """
    items = _parse_bullet_list(text)
    if items:
        policy: dict[str, Any] = {}
        for item in items:
            if ": " in item:
                name, cond = item.split(": ", 1)
                key = re.sub(
                    r"[^a-z0-9_]",
                    "",
                    name.strip().lower().replace(" ", "_").replace("-", "_"),
                )
                if key:
                    policy[key] = {"condition": cond.strip()}
            else:
                key = re.sub(
                    r"[^a-z0-9_]",
                    "",
                    item.strip().lower().replace(" ", "_"),
                )[:30]
                if key:
                    policy[key] = {"condition": item.strip()}
        if policy:
            return policy

    # Free-text fallback.
    content = text.strip()
    return {"mode": content} if content else {"mode": "default"}


def _build_markdown_record(
    frontmatter: dict[str, Any],
    sections: dict[str, str],
    filename: str,
) -> tuple[str, dict[str, Any]] | str:
    """Build a work-order record dict from parsed markdown components.

    Returns ``(identifier, data)`` on success, or an error string
    describing why the file was refused.
    """
    errors: list[str] = []

    # --- Frontmatter fields ---
    missing_fm = [f for f in _FRONTMATTER_FIELDS if f not in frontmatter]
    if missing_fm:
        errors.append(f"missing frontmatter fields: {', '.join(missing_fm)}")

    # --- Body sections ---
    missing_sections: list[str] = []
    for heading, _field in _BODY_SECTION_MAP.items():
        if heading not in sections or not sections[heading].strip():
            missing_sections.append(heading)
    if missing_sections:
        errors.append(f"missing or empty body sections: {', '.join(missing_sections)}")

    if errors:
        return "; ".join(errors)

    # --- Parse body sections ---
    # Context → dict with "content" key
    context_content = sections["Context"]
    context: dict[str, Any] = {"content": context_content}

    # Deliverables → dict
    deliverables = _parse_deliverables_section(sections["Deliverables"])
    if isinstance(deliverables, str):
        return deliverables  # error message

    # Acceptance Criteria → list
    criteria = _parse_bullet_list(sections["Acceptance Criteria"])
    if not criteria:
        # If no bullets, treat the whole section as a single criterion.
        criteria_text = sections["Acceptance Criteria"].strip()
        if criteria_text:
            criteria = [criteria_text]
        else:
            return "Acceptance Criteria section is empty"

    # Alert Policy → dict
    alert_policy = _parse_alert_policy_section(sections["Alert Policy"])

    # --- Build the record ---
    wo_id = str(frontmatter["id"])
    revision = 1  # Markdown WOs are always revision 1.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    data: dict[str, Any] = {
        "id": wo_id,
        "revision": revision,
        "state": str(frontmatter["state"]),
        "decision_question": str(frontmatter["decision_question"]),
        "requested_role": str(frontmatter["requested_role"]),
        "stage": str(frontmatter["stage"]),
        "cycle": str(frontmatter["cycle"]),
        "context": context,
        "dependencies": frontmatter.get("dependencies", []),
        "capabilities": frontmatter.get("capabilities", []),
        "deliverables": deliverables,
        "acceptance_criteria": criteria,
        "alert_policy": alert_policy,
        "priority": str(frontmatter["priority"]),
        "resource_class": str(frontmatter["resource_class"]),
        "report_to": str(frontmatter["report_to"]),
        "created_at": now,
    }

    # Ensure type constraints match the schema.
    if not isinstance(data["dependencies"], list):
        data["dependencies"] = [data["dependencies"]]
    if not isinstance(data["capabilities"], list):
        data["capabilities"] = [data["capabilities"]]

    identifier = f"{wo_id}-r{revision}"
    return identifier, data


def _read_markdown_work_orders(
    source_root: Path,
    *,
    control_plane: bool = True,
) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str]]]:
    """Read markdown work orders from the source.

    Parameters
    ----------
    source_root:
        The source root directory.
    control_plane:
        If ``True``, look in ``.dde/control/work-orders/`` under
        *source_root*.  If ``False``, look for ``.md`` files directly
        in *source_root*.

    Returns
    -------
    tuple
        ``(records, refused)`` where *records* is a list of
        ``(identifier, data)`` pairs and *refused* is a list of
        ``(filename, reason)`` pairs.
    """
    if control_plane:
        subdir = controlstore.RECORD_TYPES.get("work-order")
        if subdir is None:
            return [], []
        directory = source_root / controlstore.CONTROL_DIR / subdir
    else:
        directory = source_root

    if not directory.is_dir():
        return [], []

    source_resolved = source_root.resolve()
    records: list[tuple[str, dict[str, Any]]] = []
    refused: list[tuple[str, str]] = []

    for path in sorted(directory.glob("*.md")):
        # Symlink-escape protection (same as JSON path).
        if not path.resolve().is_relative_to(source_resolved):
            refused.append((path.name, "path escapes source tree (symlink)"))
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            refused.append((path.name, f"unreadable: {exc}"))
            continue

        try:
            frontmatter, body = _parse_frontmatter(text)
        except SchemaError as exc:
            refused.append((path.name, exc.message))
            continue

        sections = _parse_sections(body)
        result = _build_markdown_record(frontmatter, sections, path.name)

        if isinstance(result, str):
            # Error message — file refused.
            refused.append((path.name, result))
        else:
            ident, data = result
            data["imported_from_format"] = "markdown"
            records.append((ident, data))

    return records, refused


def _check_prior_markdown_imports(
    dest_root: Path,
    md_records: list[tuple[str, dict[str, Any]]],
    source_str: str,
) -> None:
    """Refuse if any non-canonical markdown WO was already imported.

    Non-canonical IDs (those not matching ``WO-NNN``) are remapped to
    a fresh sequential ID on each invocation, which means the normal
    ``_conflict_exists`` check can never catch a re-import — the
    reconciled ID is different every time by construction.

    This function closes that gap by scanning the destination for
    records whose ``imported_original_id`` and ``imported_from`` match
    a record about to be imported.

    Raises
    ------
    Refusal
        If a prior import of the same original record is found.
    """
    # Only non-canonical IDs need this check; canonical ones are caught
    # by _conflict_exists downstream.
    non_canonical = [
        (ident, data)
        for ident, data in md_records
        if not _WO_ID_PATTERN.match(str(data.get("id", "")))
    ]
    if not non_canonical:
        return

    original_ids = {str(data.get("id", "")) for _, data in non_canonical}

    # Scan destination work-orders for prior imports from this source.
    dest_wos = controlstore.list_records(dest_root, "work-order")
    for wo in dest_wos:
        prior_original = wo.get("imported_original_id")
        prior_source = wo.get("imported_from")
        if (
            prior_original is not None
            and str(prior_original) in original_ids
            and prior_source == source_str
        ):
            raise Refusal(
                f"ID conflict: markdown work order with original ID "
                f"{prior_original!r} was already imported from this source "
                f"(as {wo.get('id', '?')})",
                detail="re-running resume against the same source is not "
                "permitted once records have been imported",
                remedy="the records from this source have already been imported",
            )


def _reconcile_markdown_ids(
    md_records: list[tuple[str, dict[str, Any]]],
    existing_wo_nums: set[int],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, str]]:
    """Reconcile non-``WO-NNN`` IDs in markdown records.

    For records whose ``id`` does not match the ``WO-NNN`` pattern,
    assigns a new sequential ID and records the original in
    ``imported_original_id``.

    Parameters
    ----------
    md_records:
        ``(identifier, data)`` pairs from ``_read_markdown_work_orders``.
    existing_wo_nums:
        Set of WO numbers already in use (from both source and
        destination).

    Returns
    -------
    tuple
        ``(reconciled_records, id_remapping)`` where *id_remapping*
        maps original IDs to their new ``WO-NNN`` IDs.
    """
    next_num = max(existing_wo_nums, default=0) + 1
    reconciled: list[tuple[str, dict[str, Any]]] = []
    id_remapping: dict[str, str] = {}

    for _ident, data in md_records:
        wo_id = data.get("id", "")
        if not _WO_ID_PATTERN.match(str(wo_id)):
            new_id = f"WO-{next_num:03d}"
            id_remapping[str(wo_id)] = new_id
            data["imported_original_id"] = str(wo_id)
            data["id"] = new_id
            ident = f"{new_id}-r1"
            next_num += 1
        else:
            ident = _ident
        reconciled.append((ident, data))

    return reconciled, id_remapping


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def program() -> None:
    """Cross-phase program management for the control plane."""


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@program.command("resume")
@click.argument("source", type=click.Path())
@output_options
@pass_state
def resume_cmd(
    state: AppState,
    source: str,
    as_json: bool,
    quiet: bool,
) -> None:
    """Import accepted work orders from a prior phase's control plane.

    SOURCE is a path to a prior phase's project root (containing
    ``.dde/control/``), or a directory containing legacy markdown
    work-order files.  If the path points directly to a
    ``.dde/control/`` directory, that is accepted too.

    Both JSON records (native CLI format) and markdown files (legacy
    hand-tracked format with YAML frontmatter) are recognised.

    Only work orders in ``scientifically_accepted`` state are imported.
    Associated contexts, runs, and validations are imported alongside
    JSON work orders.  Leases are never imported.

    If any imported record ID already exists in the destination, the
    command refuses (exit 9) to prevent accidental overwrites.
    """
    emit = emitter(as_json, quiet)
    project = state.project()
    dest_root = project.root

    # ------------------------------------------------------------------
    # Resolve source — control-plane project or markdown-only directory.
    # ------------------------------------------------------------------
    has_control_plane = True
    try:
        source_root = _resolve_source(source)
    except ArtifactError as e:
        # No control plane — check for a directory of markdown files.
        source_path = Path(source).resolve()
        if not source_path.is_dir():
            raise ArtifactError(
                f"source path is not a readable directory: {source_path}",
                remedy="provide a path to a prior phase's project root, "
                "its .dde/control/ directory, or a directory of "
                "markdown work-order files",
            ) from e
        if not list(source_path.glob("*.md")):
            raise ArtifactError(
                f"source path contains neither a dde control plane "
                f"nor markdown work-order files: {source_path}",
                remedy="provide a path to a prior phase's project root, "
                "its .dde/control/ directory, or a directory of "
                "markdown work-order files",
            ) from e
        source_root = source_path
        has_control_plane = False

    source_str = str(source_root)

    # Ensure destination control dirs exist.
    controlstore.ensure_control_dirs(dest_root)

    # ------------------------------------------------------------------
    # 1. Read all work orders from source.
    # ------------------------------------------------------------------

    # 1a. JSON records (only when a control plane exists).
    json_wos: list[tuple[str, dict[str, Any]]] = []
    if has_control_plane:
        json_wos = _read_source_records(source_root, "work-order")

    # 1b. Markdown records.
    md_records, md_refused = _read_markdown_work_orders(
        source_root,
        control_plane=has_control_plane,
    )

    # 1c. Guard against duplicate IDs across formats.
    json_ids = {data.get("id", "") for _, data in json_wos} - {""}
    md_ids = {data.get("id", "") for _, data in md_records} - {""}
    overlap = json_ids & md_ids
    if overlap:
        raise Refusal(
            f"work-order IDs found in both JSON and markdown sources: "
            f"{', '.join(sorted(overlap))}",
            detail="each work order must exist in only one format within the source",
            remedy="remove the duplicate from either the JSON or markdown source",
        )

    # 1c-ii. Guard against duplicate IDs within markdown files (#295).
    md_id_list = [data.get("id", "") for _, data in md_records]
    md_id_list_filtered = [i for i in md_id_list if i]
    if len(md_id_list_filtered) != len(set(md_id_list_filtered)):
        seen: set[str] = set()
        dupes: set[str] = set()
        for i in md_id_list_filtered:
            if i in seen:
                dupes.add(i)
            seen.add(i)
        raise Refusal(
            f"duplicate work-order IDs within markdown sources: "
            f"{', '.join(sorted(dupes))}",
            detail="each markdown work order must have a unique ID",
            remedy="remove or rename the duplicate markdown work-order files",
        )

    # 1d. Check for prior imports of non-canonical markdown WOs.
    #     This must run BEFORE reconciliation, because reconciliation
    #     assigns a fresh WO-NNN on every invocation, making the
    #     downstream _conflict_exists check unable to catch re-imports.
    _check_prior_markdown_imports(dest_root, md_records, source_str)

    # 1e. Reconcile non-WO-NNN IDs in markdown records.
    existing_wo_nums: set[int] = set()
    for _, data in json_wos + md_records:
        m = _WO_NUM_PATTERN.match(str(data.get("id", "")))
        if m:
            existing_wo_nums.add(int(m.group(1)))
    # Include destination WO numbers to avoid conflicts.
    dest_wo_dir = dest_root / controlstore.CONTROL_DIR / "work-orders"
    if dest_wo_dir.is_dir():
        for p in dest_wo_dir.glob("*.json"):
            m = _WO_NUM_PATTERN.match(p.stem)
            if m:
                existing_wo_nums.add(int(m.group(1)))

    md_records, id_remapping = _reconcile_markdown_ids(
        md_records,
        existing_wo_nums,
    )

    # 1f. Merge all source WOs.
    source_wos = json_wos + md_records

    # ------------------------------------------------------------------
    # 2. Classify work orders by acceptance state.
    # ------------------------------------------------------------------

    # Group revisions by WO ID.
    wo_by_id: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for ident, data in source_wos:
        wo_id = data.get("id", "")
        wo_by_id.setdefault(wo_id, []).append((ident, data))

    # Classify each WO by its latest revision's state.
    accepted_wos: list[tuple[str, dict[str, Any]]] = []
    skipped_wos: dict[str, str] = {}  # wo_id → state

    for wo_id, revisions in sorted(wo_by_id.items()):
        latest = max(revisions, key=lambda x: x[1].get("revision", 0))
        latest_state = latest[1].get("state", "")
        if latest_state == _ACCEPTED_STATE:
            accepted_wos.extend(revisions)
        else:
            skipped_wos[wo_id] = latest_state

    accepted_wo_ids = {data.get("id", "") for _, data in accepted_wos}

    # ------------------------------------------------------------------
    # 3. Collect associated records for accepted WOs (JSON path only).
    # ------------------------------------------------------------------
    if has_control_plane:
        import_contexts = [
            (ident, data)
            for ident, data in _read_source_records(source_root, "context")
            if data.get("work_order_id") in accepted_wo_ids
        ]
        import_runs = [
            (ident, data)
            for ident, data in _read_source_records(source_root, "run")
            if data.get("work_order_id") in accepted_wo_ids
        ]
        import_validations = [
            (ident, data)
            for ident, data in _read_source_records(source_root, "validation")
            if data.get("work_order_id") in accepted_wo_ids
        ]
    else:
        import_contexts = []
        import_runs = []
        import_validations = []

    # ------------------------------------------------------------------
    # 4. Check for ID conflicts — refuse on any conflict.
    # ------------------------------------------------------------------
    all_imports: list[tuple[str, str, dict[str, Any]]] = []
    for ident, data in accepted_wos:
        all_imports.append(("work-order", ident, data))
    for ident, data in import_contexts:
        all_imports.append(("context", ident, data))
    for ident, data in import_runs:
        all_imports.append(("run", ident, data))
    for ident, data in import_validations:
        all_imports.append(("validation", ident, data))

    # 4a. Check for duplicate identifiers within the import batch (#295).
    seen_imports: set[tuple[str, str]] = set()
    batch_dupes: list[str] = []
    for record_type, ident, _ in all_imports:
        key = (record_type, ident)
        if key in seen_imports:
            batch_dupes.append(f"{record_type} {ident}")
        seen_imports.add(key)
    if batch_dupes:
        raise Refusal(
            f"duplicate identifiers within import batch: "
            f"{', '.join(sorted(batch_dupes))}",
            detail="each record must have a unique (record_type, identifier) "
            "pair within the import batch",
            remedy="remove duplicate records from the source",
        )

    # 4b. Check for ID conflicts against existing records on disk.
    for record_type, ident, _ in all_imports:
        if _conflict_exists(dest_root, record_type, ident):
            raise Refusal(
                f"ID conflict: {record_type} record {ident!r} already exists "
                "in destination",
                detail="re-running resume against the same source is not "
                "permitted once records have been imported",
                remedy="the records from this source have already been imported",
            )

    # ------------------------------------------------------------------
    # 5. Pre-validate all records before writing any to disk.
    #    A record that fails validation after some writes have landed
    #    leaves the destination in a stuck state (re-run hits ID conflict).
    # ------------------------------------------------------------------
    validation_errors: list[str] = []
    for record_type, ident, data in all_imports:
        # 5a. Validate identifier against _SAFE_IDENTIFIER_RE (#298).
        if not controlstore._SAFE_IDENTIFIER_RE.match(ident):
            validation_errors.append(
                f"{record_type} {ident}: invalid identifier — "
                "identifiers must contain only alphanumeric characters, "
                "hyphens, and underscores"
            )

        # 5b. Validate JSON serializability (#298).
        try:
            json.dumps(data)
        except (TypeError, ValueError, OverflowError) as exc:
            validation_errors.append(
                f"{record_type} {ident}: data is not JSON-serializable — {exc}"
            )

        # 5c. Schema validation.
        validator = controlstore._VALIDATORS.get(record_type)
        if validator is not None:
            errors = validator(data)
            if errors:
                validation_errors.append(f"{record_type} {ident}: {'; '.join(errors)}")

    if validation_errors:
        raise SchemaError(
            f"source contains {len(validation_errors)} invalid record(s); "
            "no records were imported",
            detail="\n".join(validation_errors),
            remedy="fix the invalid records in the source before resuming",
        )

    # ------------------------------------------------------------------
    # 6. Write all imported records.
    # ------------------------------------------------------------------
    imported_wo_ids: list[str] = []
    imported_run_ids: list[str] = []

    for record_type, ident, data in all_imports:
        data["imported_from"] = source_str
        controlstore.write_record(dest_root, record_type, ident, data)

        if record_type == "work-order":
            wo_id = data.get("id", "")
            if wo_id not in imported_wo_ids:
                imported_wo_ids.append(wo_id)
        elif record_type == "run":
            imported_run_ids.append(data.get("run_id", ident))

    # ------------------------------------------------------------------
    # 7. Log program.resumed event.
    # ------------------------------------------------------------------
    event_data: dict[str, Any] = {
        "type": "program.resumed",
        "source": source_str,
        "imported_work_orders": sorted(imported_wo_ids),
        "imported_runs": sorted(imported_run_ids),
        "skipped_work_orders": skipped_wos,
        "actor": None,
    }
    if id_remapping:
        event_data["id_remapping"] = id_remapping
    if md_refused:
        event_data["refused_markdown_files"] = dict(md_refused)
    controlstore.append_event(dest_root, event_data)

    # ------------------------------------------------------------------
    # 8. Report.
    # ------------------------------------------------------------------
    emit.data("source", source_str)
    emit.data("imported_work_orders", sorted(imported_wo_ids))
    emit.data("imported_runs", sorted(imported_run_ids))
    emit.data("skipped_work_orders", skipped_wos)
    emit.data("imported_contexts", len(import_contexts))
    emit.data("imported_validations", len(import_validations))
    if id_remapping:
        emit.data("id_remapping", id_remapping)
    if md_refused:
        emit.data("refused_markdown_files", dict(md_refused))

    if not quiet:
        emit.line(f"Source: {source_str}")
        emit.line("")

        if not imported_wo_ids:
            emit.line(
                "No work orders in scientifically_accepted state found in source."
            )

        if imported_wo_ids:
            emit.line("Imported:")
            for wo_id in sorted(imported_wo_ids):
                format_note = ""
                # Check if this was from markdown.
                for _ident, d in accepted_wos:
                    if (
                        d.get("id") == wo_id
                        and d.get("imported_from_format") == "markdown"
                    ):
                        original = d.get("imported_original_id")
                        if original:
                            format_note = f" (from markdown, original ID: {original})"
                        else:
                            format_note = " (from markdown)"
                        break
                emit.line(f"  {wo_id} (state: {_ACCEPTED_STATE}){format_note}")

        if skipped_wos:
            emit.line("Skipped:")
            for wo_id, wo_state in sorted(skipped_wos.items()):
                emit.line(f"  {wo_id} skipped: state is {wo_state}")

        if md_refused:
            emit.line("Refused markdown files:")
            for name, reason in md_refused:
                emit.line(f"  {name}: {reason}")

        emit.line("")
        summary_parts = [
            f"{len(imported_wo_ids)} work orders imported",
            f"{len(skipped_wos)} skipped",
            f"{len(import_runs)} runs",
            f"{len(import_contexts)} contexts",
            f"{len(import_validations)} validations",
        ]
        if md_refused:
            summary_parts.append(f"{len(md_refused)} markdown file(s) refused")
        emit.line(f"Summary: {', '.join(summary_parts)}")

    emit.flush()


# ---------------------------------------------------------------------------
# migrate-concepts
# ---------------------------------------------------------------------------

_SERIES_HEADING_RE = re.compile(r"^##\s+(.+)$")
_CONCEPT_ID_RE_FIELD = re.compile(
    r"\*\*Concept ID\*\*:\s*(.+?)(?:\s*$)",
    re.MULTILINE,
)
_STATUS_RE = re.compile(
    r"\*\*Status\*\*:\s*(.+?)(?:\s*$)",
    re.MULTILINE,
)


def _parse_active_series(text: str) -> list[dict[str, Any]]:
    """Parse ``active-series.md`` into a list of series entry dicts.

    Each dict has keys: ``name``, ``status``, ``concept_id`` (if present).
    Entries inside HTML comments are skipped (template blocks).
    """
    # Strip HTML comments (template blocks).
    import re as _re

    cleaned = _re.sub(r"<!--.*?-->", "", text, flags=_re.DOTALL)

    entries: list[dict[str, Any]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_name is None:
            return
        block = "\n".join(current_lines)
        entry: dict[str, Any] = {"name": current_name}

        # Extract concept ID if present.
        cid_match = _CONCEPT_ID_RE_FIELD.search(block)
        if cid_match:
            cid_val = cid_match.group(1).strip()
            if cid_val and cid_val.lower() not in (
                "not yet assigned",
                "n/a",
                "none",
                "",
            ):
                entry["concept_id"] = cid_val

        # Extract status.
        status_match = _STATUS_RE.search(block)
        if status_match:
            entry["status"] = status_match.group(1).strip()

        entries.append(entry)

    for line in cleaned.splitlines():
        heading_match = _SERIES_HEADING_RE.match(line)
        if heading_match:
            _flush()
            current_name = heading_match.group(1).strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    _flush()
    return entries


def _propose_concept_record(
    entry: dict[str, Any],
    concept_id: str,
    now: str,
) -> dict[str, Any]:
    """Build a proposed concept record from an active-series entry.

    Gaps are declared as null, following design §3.4 point 2.
    """
    return {
        "schema": CONCEPT_SCHEMA,
        "id": concept_id,
        "revision": 1,
        "state": "draft",
        "disease_context": {
            "indication": None,
            "stage": None,
            "patient_population": None,
        },
        "target_pathway": {
            "gene": entry["name"],
            "protein": None,
            "pathway": None,
            "mechanism_hypothesis": None,
        },
        "modality": None,
        "entity_ref": None,
        "delivery_assumptions": None,
        "biomarker_assumptions": None,
        "charter_ref": None,
        "hypothesis_refs": None,
        "work_order_refs": None,
        "decision_log_refs": None,
        "applicable_policies": None,
        "termination_authority": None,
        "notes": f"Migrated from active-series.md entry: {entry['name']}",
        "created_at": now,
    }


@program.command("migrate-concepts")
@click.option(
    "--active-series",
    "series_path",
    type=click.Path(exists=True),
    default=None,
    help="Path to active-series.md; defaults to program-state/active-series.md "
    "under the project root.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show proposed records without writing.",
)
@output_options
@pass_state
def migrate_concepts_cmd(
    state: AppState,
    series_path: str | None,
    dry_run: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Propose concept records from active-series.md entries.

    Reads ``active-series.md``, identifies entries without a concept
    ID, and proposes draft concept records with gaps declared as null.
    Use ``--dry-run`` to preview without writing.

    Entries that already have a Concept ID field are skipped.
    """
    emit = emitter(as_json, quiet)
    project = state.project()
    dest_root = project.root

    # Resolve active-series.md path.
    if series_path is None:
        candidates = [
            dest_root / "program-state" / "active-series.md",
            dest_root / "artifact-templates" / "program-state" / "active-series.md",
        ]
        resolved = None
        for c in candidates:
            if c.is_file():
                resolved = c
                break
        if resolved is None:
            raise ArtifactError(
                "active-series.md not found",
                detail="looked in program-state/ and artifact-templates/program-state/",
                remedy="provide an explicit --active-series path",
            )
    else:
        resolved = Path(series_path)

    text = resolved.read_text(encoding="utf-8")
    entries = _parse_active_series(text)

    if not entries:
        if not quiet:
            emit.line("No entries found in active-series.md.")
        emit.flush()
        return

    # Filter to entries without a concept ID.
    to_migrate = [e for e in entries if "concept_id" not in e]
    already_linked = [e for e in entries if "concept_id" in e]

    if not to_migrate:
        if not quiet:
            emit.line(
                f"All {len(entries)} entries already have concept IDs. "
                "Nothing to migrate."
            )
        emit.flush()
        return

    # Ensure control dirs exist.
    controlstore.ensure_control_dirs(dest_root)

    # Generate IDs.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    next_concept_id = controlstore.next_id(dest_root, "concept")
    # Parse the numeric part to increment.
    next_num = int(re.search(r"\d+", next_concept_id).group())

    proposed: list[tuple[str, dict[str, Any]]] = []
    for entry in to_migrate:
        concept_id = f"IC-{next_num:03d}"
        record = _propose_concept_record(entry, concept_id, now)
        identifier = f"{concept_id}-r1"
        proposed.append((identifier, record))
        next_num += 1

    # Report.
    if not quiet:
        emit.line(f"Found {len(entries)} entries in active-series.md:")
        emit.line(f"  {len(already_linked)} already linked to concept records")
        emit.line(f"  {len(to_migrate)} to migrate")
        emit.line("")

        for identifier, record in proposed:
            emit.line(f"  {identifier}: {record['target_pathway']['gene']}")

    emit.data(
        "proposed", [{"identifier": ident, "record": rec} for ident, rec in proposed]
    )
    emit.data("already_linked", [e["name"] for e in already_linked])

    if dry_run:
        if not quiet:
            emit.line("")
            emit.line("Dry run — no records written.")
        emit.flush()
        return

    # Write records.
    written: list[str] = []
    for identifier, record in proposed:
        controlstore.write_record(dest_root, "concept", identifier, record)
        written.append(identifier)

    # Log event.
    controlstore.append_event(
        dest_root,
        {
            "type": "concept.migrated",
            "source": str(resolved),
            "records": written,
            "actor": None,
        },
    )

    if not quiet:
        emit.line("")
        emit.line(f"Wrote {len(written)} concept record(s).")

    emit.data("written", written)
    emit.flush()
