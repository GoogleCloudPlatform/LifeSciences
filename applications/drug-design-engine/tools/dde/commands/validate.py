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

"""`dde validate` — mechanical validation of submitted work-order deliverables.

Runs 10 mechanical checks against a submitted work order's deliverables.
Each check inspects a specific property of the Layer 0 and Layer 1
artifacts referenced by the work order and produces a pass/fail/skip
result.  This is Phase 2 of issue #22 — control plane CLI.

This is NOT a science tool: it reads artifacts and metadata but never
modifies them.  It writes only to ``.dde/control/validations/`` and
appends to ``events.ndjson``.
"""

from __future__ import annotations

import json as json_mod  # avoid shadowing; used by source-tag check
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    DDEGroup,
    emitter,
    output_options,
    pass_state,
)
from ..core import controlstore
from ..core.context import ARTIFACT_DIRS, normalize_artifact_class
from ..core.controlstore import normalize_deliverables
from ..core.env import CLI_VERSION
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.paths import confine_path, is_safe_to_open
from ..core.provenance import read_json, sha256_file
from ..core.statemachine import validate_transition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _overall_verdict(checks: list[dict[str, Any]]) -> str:
    """Compute overall validation verdict from individual check results."""
    non_skipped = [c for c in checks if c.get("status") != "skip"]
    if not non_skipped:
        return "fail"  # vacuous — nothing was validated
    if any(c.get("status") == "fail" for c in non_skipped):
        return "fail"
    if any(c.get("status") == "warn" for c in non_skipped):
        return "pass_with_warnings"
    return "pass"


def _find_latest_revision(
    project_root: Path,
    wo_id: str,
) -> dict[str, Any]:
    """Find the work-order revision with the highest revision number."""

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


def _check_deliverables_schema(
    deliverables: dict[str, Any],
) -> dict[str, Any] | None:
    """Return an advisory finding if the deliverable schema is unrecognized.

    A validator that silently skips a check because it doesn't recognize
    the deliverable shape is a vacuous pass (tool-design-guidance.md).
    This helper emits an explicit finding so the gap is visible.

    Returns ``None`` when the schema is recognized (has at least one of
    ``layer_0_classes`` / ``layer_0`` or ``layer_1``).
    """
    has_layer_0 = bool(
        deliverables.get("layer_0_classes")
        or deliverables.get("layer_0")
        or deliverables.get("required_classes")
        or deliverables.get("authorized_classes")
        or deliverables.get("layer_0_classes_optional")
    )
    has_layer_1 = bool(deliverables.get("layer_1"))

    if not has_layer_0 and not has_layer_1:
        known_keys = sorted(deliverables.keys())
        return {
            "name": "deliverables_schema",
            "result": "fail",
            "status": "fail",
            "kind": "COMPLETENESS",
            "detail": (
                "deliverables dict contains neither layer_0/layer_0_classes "
                "nor layer_1 — 5 of 9 checks cannot run and will be skipped. "
                f"Keys found: {known_keys}"
            ),
        }
    return None


def _is_sidecar(name: str) -> bool:
    """Recognise sidecar filenames: *.meta.json and *.sc-meta.json."""
    return name.endswith(".meta.json") or name.endswith(".sc-meta.json")


def _is_analysis(name: str, path: Path | None = None) -> bool:
    """Recognise analysis records by filename suffix or content.

    Fast path: filename suffix (``.analysis.json``, ``.sc-analysis.json``).
    Fallback: when *path* is provided and the file has a ``.json``
    extension, read the JSON and check for ``"record_type": "analysis"``
    (written by ``write_analysis``).  This allows new analysis types
    (e.g. ``.mmp-analysis.json``) to be recognised without a suffix
    allowlist update.
    """
    if name.endswith(".analysis.json") or name.endswith(".sc-analysis.json"):
        return True
    # Content-based fallback: read JSON and check record_type.
    if path is not None and name.endswith(".json"):
        try:
            data = json_mod.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("record_type") == "analysis":
                return True
        except (OSError, ValueError):
            pass
    return False


def _is_known_artifact_class(artifact_class: str) -> bool:
    """Return True if *artifact_class* (after normalization) is registered."""
    return normalize_artifact_class(artifact_class) in ARTIFACT_DIRS


def _find_layer0_artifacts(
    project_root: Path,
    artifact_class: str,
) -> list[Path]:
    """Find artifact files in an ARTIFACT_DIRS directory.

    Normalizes ``dde.*`` prefix before lookup.  Returns an empty list
    when the directory does not exist *or* the class is unknown (callers
    distinguish via ``_is_known_artifact_class``).

    Excludes sidecar and analysis files — those are metadata about
    artifacts, not artifacts themselves.

    Symlinks are resolved and confined to the project root before
    inclusion.  A symlink targeting a file outside the project
    (e.g. ``raw/structures/evil.pdb → /etc/shadow``) is silently
    skipped — ``sha256_file`` must never read outside the project.
    """
    normalized = normalize_artifact_class(artifact_class)
    rel_dir = ARTIFACT_DIRS.get(normalized)
    if rel_dir is None:
        return []
    art_dir = project_root / rel_dir
    if not art_dir.is_dir():
        return []
    artifacts: list[Path] = []
    for child in sorted(art_dir.iterdir()):
        if not child.is_file():
            continue
        # Reject symlinks and paths outside the project root FIRST.
        if not is_safe_to_open(child):
            continue
        if confine_path(project_root, child) is None:
            continue
        if _is_sidecar(child.name) or _is_analysis(child.name, child):
            continue
        artifacts.append(child)
    return artifacts


# ---------------------------------------------------------------------------
# Cross-WO consumption helpers (#87)
# ---------------------------------------------------------------------------


def _build_consumes_map(
    deliverables: dict[str, Any],
) -> dict[str, set[str]]:
    """Parse the ``consumes`` block into ``{normalized_class: {wo_id, …}}``.

    Returns an empty dict when ``consumes`` is absent or not a list.
    Malformed entries (wrong type, missing keys) are silently skipped —
    commit-time validation (Phase 3) is responsible for rejecting them.
    """
    consumes = deliverables.get("consumes", [])
    result: dict[str, set[str]] = {}
    if isinstance(consumes, list):
        for entry in consumes:
            if isinstance(entry, dict):
                cls = normalize_artifact_class(entry.get("artifact_class", ""))
                wo = entry.get("from_work_order", "")
                if cls and wo:
                    result.setdefault(cls, set()).add(wo)
    return result


def _has_artifacts_from_consumed_wos(
    art_dir: Path,
    project_root: Path,
    artifacts: list[Path],
    consumed_wo_ids: set[str],
) -> bool:
    """Check if any artifact in *art_dir* is attributed to a consumed WO.

    Calls ``_build_sidecar_index`` once per consumed WO (the set is
    expected to be small — typically 1).  Returns ``True`` as soon as a
    match is found.
    """
    for consumed_wo_id in consumed_wo_ids:
        consumed_index, _, _ = _build_sidecar_index(
            art_dir,
            project_root,
            wo_id=consumed_wo_id,
        )
        for artifact_path in artifacts:
            actual_sha = sha256_file(artifact_path)
            if actual_sha in consumed_index:
                return True
    return False


# ---------------------------------------------------------------------------
# The 9 mechanical checks
# ---------------------------------------------------------------------------


def _check_deliverables_exist(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 1 — verify all declared deliverable files exist.

    Handles both ``required_classes`` and ``authorized_classes`` (#103):

    - **required_classes** entries: missing → ``fail`` / ``COMPLETENESS``
      (existing behavior).  Entries with ``not_applicable`` → ``skip``
      with reason.
    - **authorized_classes** entries: missing → no finding (pass
      silently).  Present → provenance checked as before.

    Also supports consumed upstream classes (#132) and optional classes:

    - **consumed classes** (via ``consumes`` block): when a required
      class is not produced by this WO but is available from a consumed
      upstream WO → ``pass`` with cross-WO citation recorded.
    - **layer_0_classes_optional** entries: present → recorded as
      present, absent → recorded as absent (INFO, not a failure).

    When *wo_id* is provided, only artifacts attributed to that work
    order (or untagged, for backward compatibility with pre-#166 records)
    count toward satisfying each class entry.
    """
    missing: list[str] = []
    confined_failures: list[str] = []
    skipped: list[dict[str, str]] = []
    unknown_classes: list[dict[str, str]] = []
    consumed_satisfied: list[
        dict[str, Any]
    ] = []  # classes satisfied via consumes (#132)
    cross_wo_citations: list[dict[str, Any]] = []  # cross-WO citation records (#132)
    optional_info: list[dict[str, Any]] = []  # optional class status (#132)

    # Layer 1 paths
    layer_1 = deliverables.get("layer_1", [])
    if isinstance(layer_1, list):
        for rel_path in layer_1:
            resolved = confine_path(project_root, Path(rel_path))
            if resolved is None:
                confined_failures.append(str(rel_path))
                continue
            if not resolved.is_file():
                missing.append(str(rel_path))

    # --- Required classes (from required_classes or backward-compat layer_0_classes) ---
    consumes_map = _build_consumes_map(deliverables)

    required_classes = deliverables.get("required_classes", [])
    # Fallback: if no required_classes, use layer_0_classes (backward compat).
    if not required_classes:
        required_classes = deliverables.get("layer_0_classes", [])

    if isinstance(required_classes, list):
        for entry in required_classes:
            # Handle not_applicable entries.
            if isinstance(entry, dict) and "not_applicable" in entry:
                cls_name = entry.get("class") or entry.get("name") or str(entry)
                reason = entry["not_applicable"]
                skipped.append({"class": cls_name, "reason": reason})
                continue

            artifact_class = (
                entry
                if isinstance(entry, str)
                else (entry.get("class") or entry.get("name") or str(entry))
            )
            # Distinguish unknown class (toolchain bug) from empty results
            # (real scientific finding).  Issue #131 / #83 / #85.
            if not _is_known_artifact_class(artifact_class):
                unknown_classes.append(
                    {
                        "class": artifact_class,
                        "message": (
                            f"Artifact class {artifact_class!r} is not registered "
                            "in ARTIFACT_DIRS. This is a toolchain bug, not missing "
                            "science. File an issue."
                        ),
                    }
                )
                continue
            artifacts = _find_layer0_artifacts(project_root, artifact_class)
            if not artifacts:
                missing.append(f"layer_0_classes/{artifact_class} (no artifacts found)")
                continue
            # WO scoping
            if wo_id is not None:
                art_dir = artifacts[0].parent
                sidecar_index, _, other_wo_hashes = _build_sidecar_index(
                    art_dir,
                    project_root,
                    wo_id=wo_id,
                )
                has_own_artifact = False
                for artifact_path in artifacts:
                    actual_sha = sha256_file(artifact_path)
                    if (
                        actual_sha in other_wo_hashes
                        and actual_sha not in sidecar_index
                    ):
                        continue
                    has_own_artifact = True
                    break
                if not has_own_artifact:
                    # Check consumes before failing (#87): if this class
                    # is declared in the WO's consumes block, artifacts
                    # from the consumed WO satisfy the deliverables check.
                    consumed_wo_ids = consumes_map.get(
                        normalize_artifact_class(artifact_class), set()
                    )
                    if consumed_wo_ids and _has_artifacts_from_consumed_wos(
                        art_dir,
                        project_root,
                        artifacts,
                        consumed_wo_ids,
                    ):
                        # Record the cross-WO satisfaction (#132).
                        consumed_satisfied.append(
                            {
                                "class": artifact_class,
                                "satisfied_by": sorted(consumed_wo_ids),
                            }
                        )
                        cross_wo_citations.append(
                            {
                                "class": artifact_class,
                                "from_work_orders": sorted(consumed_wo_ids),
                            }
                        )
                        continue  # satisfied by consumption
                    missing.append(
                        f"layer_0_classes/{artifact_class} "
                        "(no artifacts attributed to this work order)"
                    )

    # --- Authorized classes: presence is optional, absence is not a failure ---
    # authorized_classes are NOT provenance-checked by the mechanical validator.
    # They do not appear in layer_0_classes, so checks 4-6 skip them.

    # --- Optional classes (layer_0_classes_optional, #132) ---
    # Classes listed here are checked but do not fail validation if absent.
    optional_classes = deliverables.get("layer_0_classes_optional", [])
    if isinstance(optional_classes, list):
        for entry in optional_classes:
            artifact_class = (
                entry
                if isinstance(entry, str)
                else (entry.get("class") or entry.get("name") or str(entry))
            )
            if not _is_known_artifact_class(artifact_class):
                optional_info.append(
                    {
                        "class": artifact_class,
                        "status": "unknown",
                        "message": (
                            f"Artifact class {artifact_class!r} is not registered "
                            "in ARTIFACT_DIRS."
                        ),
                    }
                )
                continue
            opt_artifacts = _find_layer0_artifacts(project_root, artifact_class)
            if not opt_artifacts:
                optional_info.append(
                    {
                        "class": artifact_class,
                        "status": "absent",
                    }
                )
                continue
            if wo_id is not None:
                opt_art_dir = opt_artifacts[0].parent
                opt_sidecar_index, _, opt_other_wo_hashes = _build_sidecar_index(
                    opt_art_dir,
                    project_root,
                    wo_id=wo_id,
                )
                has_own = any(
                    sha256_file(a) not in opt_other_wo_hashes
                    or sha256_file(a) in opt_sidecar_index
                    for a in opt_artifacts
                )
                if has_own:
                    optional_info.append(
                        {
                            "class": artifact_class,
                            "status": "present",
                        }
                    )
                else:
                    # Check if optional class is satisfied via consumes.
                    opt_consumed_wo_ids = consumes_map.get(
                        normalize_artifact_class(artifact_class), set()
                    )
                    if opt_consumed_wo_ids and _has_artifacts_from_consumed_wos(
                        opt_art_dir,
                        project_root,
                        opt_artifacts,
                        opt_consumed_wo_ids,
                    ):
                        optional_info.append(
                            {
                                "class": artifact_class,
                                "status": "present_via_consumes",
                                "satisfied_by": sorted(opt_consumed_wo_ids),
                            }
                        )
                    else:
                        optional_info.append(
                            {
                                "class": artifact_class,
                                "status": "absent",
                            }
                        )
            else:
                optional_info.append(
                    {
                        "class": artifact_class,
                        "status": "present",
                    }
                )

    detail: dict[str, Any] = {}
    if confined_failures:
        detail["path_confinement_failures"] = confined_failures
    if missing:
        detail["missing"] = missing
    if skipped:
        detail["not_applicable"] = skipped
    if unknown_classes:
        detail["unknown_artifact_classes"] = unknown_classes
    if consumed_satisfied:
        detail["consumed_satisfied"] = consumed_satisfied
    if cross_wo_citations:
        detail["cross_wo_citations"] = cross_wo_citations
    if optional_info:
        detail["layer_0_classes_optional"] = optional_info

    # Determine layer_0_classes list for the detail dict (backward compat).
    layer_0_classes = deliverables.get("layer_0_classes", [])

    # Unknown artifact classes are toolchain bugs — return a distinct
    # finding so they are never confused with "no artifacts found"
    # (which is a real scientific result).  Issue #131 / #83 / #85.
    if unknown_classes:
        return {
            "name": "deliverables_exist",
            "result": "fail",
            "status": "fail",
            "kind": "unknown_artifact_class",
            "detail": detail,
        }
    if confined_failures or missing:
        return {
            "name": "deliverables_exist",
            "result": "fail",
            "status": "fail",
            "kind": "COMPLETENESS",
            "detail": detail,
        }
    pass_detail: dict[str, Any] = {
        "layer_1_count": len(layer_1) if isinstance(layer_1, list) else 0,
        "layer_0_classes": layer_0_classes if isinstance(layer_0_classes, list) else [],
    }
    if skipped:
        pass_detail["not_applicable"] = skipped
    if consumed_satisfied:
        pass_detail["consumed_satisfied"] = consumed_satisfied
    if cross_wo_citations:
        pass_detail["cross_wo_citations"] = cross_wo_citations
    if optional_info:
        pass_detail["layer_0_classes_optional"] = optional_info
    return {
        "name": "deliverables_exist",
        "result": "pass",
        "status": "ok",
        "kind": "COMPLETENESS",
        "detail": pass_detail,
    }


def _wo_reference_found(
    content: str,
    wo_num: str,
    revision: int,
) -> tuple[str, str | None]:
    """Detect whether *content* identifies the work order unambiguously.

    *wo_num* is the bare WO number (e.g. ``"004"``), *revision* the
    integer revision.

    Returns ``(match_kind, found_text)`` where *match_kind* is one of:

    - ``"exact"`` — the canonical ``WO-<num>-r<rev>`` form is present.
    - ``"accepted"`` — a recognised alternative form is present
      (e.g. split metadata fields, ``WO-004 r1``, ``WO-004 (rev 1)``,
      ``WO-004, Revision 1``).  Triggers a warning, not a failure.
    - ``"none"`` — no WO identifier detected.  Triggers a failure.

    *found_text* is the literal text matched for the ``accepted`` case
    (``None`` for ``exact`` and ``none``).
    """
    canonical = f"WO-{wo_num}-r{revision}"
    if canonical in content:
        return ("exact", None)

    # --- Accepted alternative forms (case-insensitive where noted) ---

    # 1. Split metadata fields:
    #    Work Order: WO-<num>  +  Revision: <rev>
    wo_field_re = re.compile(
        rf"Work\s+Order\s*:\s*WO-{re.escape(wo_num)}",
        re.IGNORECASE,
    )
    rev_field_re = re.compile(
        rf"(?:Revision|Rev)\s*:\s*{revision}",
        re.IGNORECASE,
    )
    wo_match = wo_field_re.search(content)
    rev_match = rev_field_re.search(content)
    if wo_match and rev_match:
        return (
            "accepted",
            f"{wo_match.group(0)} + {rev_match.group(0)}",
        )

    # 2. Inline forms: WO-<num> r<rev>
    inline_re = re.compile(
        rf"WO-{re.escape(wo_num)}\s+r{revision}\b",
        re.IGNORECASE,
    )
    m = inline_re.search(content)
    if m:
        return ("accepted", m.group(0))

    # 3. Parenthesised: WO-<num> (rev <rev>)
    paren_re = re.compile(
        rf"WO-{re.escape(wo_num)}\s*\(\s*rev\s+{revision}\s*\)",
        re.IGNORECASE,
    )
    m = paren_re.search(content)
    if m:
        return ("accepted", m.group(0))

    # 4. Comma-separated: WO-<num>, Revision <rev>
    comma_re = re.compile(
        rf"WO-{re.escape(wo_num)}\s*,\s*(?:Revision|Rev)\s+{revision}\b",
        re.IGNORECASE,
    )
    m = comma_re.search(content)
    if m:
        return ("accepted", m.group(0))

    return ("none", None)


def _check_report_headings(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str,
    revision: int,
) -> dict[str, Any]:
    """Check 2 — verify Layer 1 findings contain the WO reference string
    and the ``## Key Findings`` heading (or a plausible equivalent).

    Two sub-checks:

    1. **WO reference** — the work order must be identified in each
       Layer 1 file.  The canonical form is ``WO-<id>-r<rev>``, but
       split metadata (``Work Order: WO-004`` + ``Revision: 1``) and
       other unambiguous forms are accepted with a warning.
       - Canonical form → ``ok``.
       - Accepted alternative form → ``warn`` / ``CONVENTION``.
       - No identifier found → ``fail`` / ``COMPLETENESS``.
    2. **Key Findings heading** — ``## Key Findings`` must appear.
       When absent, we look for any ``##``-level heading between
       ``## Summary`` and ``## Implications`` (or end-of-file).
       - Variant found → ``warn`` / ``CONVENTION``.
       - Nothing found → ``fail`` / ``COMPLETENESS``.

    The returned result reflects the *worst* status across both
    sub-checks.
    """
    wo_num = wo_id.removeprefix("WO-")
    reference = f"WO-{wo_num}-r{revision}"
    missing_ref: list[str] = []
    nonstandard_ref: list[dict[str, str]] = []  # accepted but non-canonical

    layer_1 = deliverables.get("layer_1", [])
    if not isinstance(layer_1, list) or not layer_1:
        return {
            "name": "report_headings",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_1 deliverables declared",
        }

    # Heading-text variance tracking.
    heading_variants: list[dict[str, str]] = []  # warn cases
    missing_headings: list[str] = []  # fail cases

    for rel_path in layer_1:
        resolved = confine_path(project_root, Path(rel_path))
        if resolved is None or not resolved.is_file():
            continue  # deliverables_exist already flags these
        content = resolved.read_text(encoding="utf-8", errors="replace")

        # Sub-check 1: WO reference string.
        match_kind, found_text = _wo_reference_found(
            content,
            wo_num,
            revision,
        )
        if match_kind == "none":
            missing_ref.append(str(rel_path))
        elif match_kind == "accepted":
            nonstandard_ref.append(
                {
                    "file": str(rel_path),
                    "found": found_text or "(alternative form)",
                    "expected": reference,
                }
            )

        # Sub-check 2: ## Key Findings heading.
        if "## Key Findings" not in content:
            # Look for a plausible equivalent: any ##-level heading
            # between ## Summary and ## Implications.
            lines = content.split("\n")
            summary_pos: int | None = None
            implications_pos: int | None = None
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("## Summary"):
                    summary_pos = idx
                elif stripped.startswith("## Implications"):
                    implications_pos = idx

            # Search region: after Summary, before Implications (or EOF).
            search_start = (summary_pos + 1) if summary_pos is not None else 0
            search_end = (
                implications_pos if implications_pos is not None else len(lines)
            )

            variant_heading: str | None = None
            for idx in range(search_start, search_end):
                stripped = lines[idx].strip()
                if stripped.startswith("## ") and not stripped.startswith("## Summary"):
                    variant_heading = stripped
                    break

            if variant_heading is not None:
                heading_variants.append(
                    {
                        "file": str(rel_path),
                        "found_heading": variant_heading,
                    }
                )
            else:
                missing_headings.append(str(rel_path))

    # Build detail dict.
    detail: dict[str, Any] = {"reference": reference}
    accepted_forms = (
        f"'{reference}', "
        f"'Work Order: WO-{wo_num}' + 'Revision: {revision}', "
        f"'WO-{wo_num} r{revision}'"
    )
    if missing_ref:
        detail["files_missing_reference"] = missing_ref
        detail["accepted_forms"] = accepted_forms
        detail["error_message"] = (
            f"Report does not identify work order {reference}.\n"
            f"Accepted forms: {accepted_forms}\n"
            f"Found: {', '.join(missing_ref) if missing_ref else 'no work order identifier detected'}"
        )
    if nonstandard_ref:
        detail["nonstandard_reference"] = nonstandard_ref
        detail["accepted_forms"] = accepted_forms
    if heading_variants:
        detail["heading_variants"] = heading_variants
    if missing_headings:
        detail["missing_headings"] = missing_headings

    # Determine worst status across both sub-checks.
    # Priority: fail > warn > ok.
    has_fail = bool(missing_ref) or bool(missing_headings)
    has_warn = bool(heading_variants) or bool(nonstandard_ref)

    if has_fail:
        # Determine kind: missing_ref and missing_headings are COMPLETENESS.
        return {
            "name": "report_headings",
            "result": "fail",
            "status": "fail",
            "kind": "COMPLETENESS",
            "detail": detail,
        }
    if has_warn:
        return {
            "name": "report_headings",
            "result": "pass",
            "status": "warn",
            "kind": "CONVENTION",
            "detail": detail,
        }
    return {
        "name": "report_headings",
        "result": "pass",
        "status": "ok",
        "kind": "COMPLETENESS",
        "detail": detail,
    }


_MARKDOWN_LINK_RE = re.compile(r"!?\[(?:[^\]]*)\]\(([^)]+)\)")


def _check_paths_resolve(
    project_root: Path,
    deliverables: dict[str, Any],
) -> dict[str, Any]:
    """Check 3 — verify internal markdown links resolve within the project.

    When a broken link would resolve from the project root, emit
    ``warn`` / ``CONVENTION`` and suggest the corrected relative path.
    Truly broken links (resolve nowhere) remain ``fail`` /
    ``DATA_INTEGRITY``.
    """
    import os

    broken: list[dict[str, Any]] = []
    confined_failures: list[dict[str, str]] = []
    links_checked = 0

    layer_1 = deliverables.get("layer_1", [])
    if not isinstance(layer_1, list) or not layer_1:
        return {
            "name": "paths_resolve",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_1 deliverables declared",
        }

    for rel_path in layer_1:
        resolved_file = confine_path(project_root, Path(rel_path))
        if resolved_file is None or not resolved_file.is_file():
            continue
        content = resolved_file.read_text(encoding="utf-8", errors="replace")
        for m in _MARKDOWN_LINK_RE.finditer(content):
            target = m.group(1).strip()
            # Skip external URLs
            if target.startswith("http://") or target.startswith("https://"):
                continue
            # Strip fragment identifiers
            target_no_fragment = target.split("#")[0]
            if not target_no_fragment:
                continue  # pure fragment link
            links_checked += 1
            # Resolve relative to the file's own directory.
            # link_path is always absolute (resolved_file.parent is).
            link_resolved = (resolved_file.parent / target_no_fragment).resolve()
            if not link_resolved.is_relative_to(project_root.resolve()):
                confined_failures.append({"file": str(rel_path), "link": target})
                continue
            if not link_resolved.exists():
                # Check whether the link would resolve from project root.
                root_resolved = (project_root / target_no_fragment).resolve()
                if root_resolved.exists() and root_resolved.is_relative_to(
                    project_root.resolve()
                ):
                    correct_relative = os.path.relpath(
                        root_resolved, resolved_file.parent
                    )
                    broken.append(
                        {
                            "file": str(rel_path),
                            "link": target,
                            "would_resolve_from_root": True,
                            "suggested_fix": correct_relative,
                            "detail": (
                                f"This link resolves from project root but not "
                                f"from the file directory. Use {correct_relative!r} instead."
                            ),
                        }
                    )
                else:
                    broken.append({"file": str(rel_path), "link": target})

    detail: dict[str, Any] = {"links_checked": links_checked}
    if confined_failures:
        detail["path_confinement_failures"] = confined_failures
    if broken:
        detail["broken_links"] = broken

    if confined_failures or broken:
        truly_broken = [b for b in broken if not b.get("would_resolve_from_root")]
        if confined_failures or truly_broken:
            return {
                "name": "paths_resolve",
                "result": "fail",
                "status": "fail",
                "kind": "DATA_INTEGRITY",
                "detail": detail,
            }
        else:
            # All broken links are root-resolvable → convention warning.
            return {
                "name": "paths_resolve",
                "result": "pass",
                "status": "warn",
                "kind": "CONVENTION",
                "detail": detail,
            }
    return {
        "name": "paths_resolve",
        "result": "pass",
        "status": "ok",
        "kind": "DATA_INTEGRITY",
        "detail": detail,
    }


def _build_sidecar_index(
    art_dir: Path,
    project_root: Path,
    wo_id: str | None = None,
) -> tuple[dict[str, Path], list[dict[str, str]], set[str]]:
    """Scan sidecar files in *art_dir* and build a hash index.

    Recognises both ``*.meta.json`` and ``*.sc-meta.json`` sidecars.

    Returns ``(index, sidecar_issues, other_wo_hashes)`` where *index*
    maps each ``sha256`` listed in any sidecar's ``outputs[]`` to the
    sidecar path, *sidecar_issues* collects structural problems with
    sidecars themselves (invalid JSON, wrong shape, etc.), and
    *other_wo_hashes* is the set of sha256 values from sidecars belonging
    to a different work order (empty when *wo_id* is ``None``).

    The index is built once per artifact-class directory so each sidecar
    file is read at most once regardless of how many artifacts it covers.

    Symlinks are resolved and confined to *project_root* — a sidecar
    symlink targeting a file outside the project is silently skipped.
    """
    index: dict[str, Path] = {}
    sidecar_issues: list[dict[str, str]] = []
    other_wo_hashes: set[str] = set()
    root_resolved = project_root.resolve()

    for child in sorted(art_dir.iterdir()):
        if not child.is_file() or not _is_sidecar(child.name):
            continue
        # Reject symlinks resolving outside the project root.
        if not child.resolve().is_relative_to(root_resolved):
            continue
        rel = str(child.relative_to(project_root))
        try:
            data = read_json(child, "provenance sidecar")
        except ArtifactError:
            sidecar_issues.append({"sidecar": rel, "issue": "not valid JSON"})
            continue
        if not isinstance(data, dict):
            sidecar_issues.append({"sidecar": rel, "issue": "not a JSON object"})
            continue
        outputs = data.get("outputs", [])
        if not isinstance(outputs, list):
            sidecar_issues.append({"sidecar": rel, "issue": "'outputs' is not a list"})
            continue
        # WO scoping: skip records from other work orders.
        sidecar_wo = data.get("work_order_id")
        if wo_id is not None and sidecar_wo is not None and sidecar_wo != wo_id:
            # This sidecar belongs to a different work order — still track its
            # hashes so we can distinguish "other WO's artifact" from "genuinely
            # missing provenance", but keep it out of the primary index.
            for entry in outputs:
                if isinstance(entry, dict) and "sha256" in entry:
                    other_wo_hashes.add(entry["sha256"])
            continue
        for entry in outputs:
            if isinstance(entry, dict) and "sha256" in entry:
                index[entry["sha256"]] = child

    return index, sidecar_issues, other_wo_hashes


def _check_provenance_valid(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 4 — verify provenance sidecars exist and checksums match.

    Scans all ``*.meta.json`` sidecars in each artifact-class directory
    and matches artifacts by ``sha256`` against the sidecars' ``outputs[]``
    arrays.  This is a scan-and-match approach: sidecars are shared across
    multiple output files and their filenames do not necessarily match any
    single artifact's filename (#147).

    When *wo_id* is provided, only sidecars tagged with that work order
    (or untagged sidecars, for backward compatibility) are included in the
    primary index.  Artifacts belonging to a different work order are
    silently skipped (#166).
    """
    issues: list[dict[str, str]] = []
    artifacts_checked = 0

    layer_0_classes = deliverables.get("layer_0_classes", [])
    if not isinstance(layer_0_classes, list) or not layer_0_classes:
        return {
            "name": "provenance_valid",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_0_classes declared",
        }

    for artifact_class in layer_0_classes:
        artifacts = _find_layer0_artifacts(project_root, artifact_class)
        if not artifacts:
            continue

        # Build a sha256 → sidecar mapping once for the whole directory.
        art_dir = artifacts[0].parent
        sidecar_index, sidecar_issues, other_wo_hashes = _build_sidecar_index(
            art_dir,
            project_root,
            wo_id=wo_id,
        )
        issues.extend(sidecar_issues)

        for artifact_path in artifacts:
            actual_sha = sha256_file(artifact_path)
            # Skip artifacts belonging to a different work order, but
            # only when our own WO does not also claim them.
            if actual_sha in other_wo_hashes and actual_sha not in sidecar_index:
                continue
            artifacts_checked += 1
            if actual_sha not in sidecar_index:
                # JSON files without provenance are handled by the
                # unrecognized_record_type check (#130) — they should
                # not be folded into provenance_valid.
                if artifact_path.name.endswith(".json"):
                    continue
                issues.append(
                    {
                        "artifact": str(artifact_path.relative_to(project_root)),
                        "issue": "no provenance sidecar covers this artifact",
                    }
                )

    detail: dict[str, Any] = {"artifacts_checked": artifacts_checked}
    if issues:
        detail["issues"] = issues
        return {
            "name": "provenance_valid",
            "result": "fail",
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": detail,
        }
    return {
        "name": "provenance_valid",
        "result": "pass",
        "status": "ok",
        "kind": "DATA_INTEGRITY",
        "detail": detail,
    }


def _check_analysis_citations(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 5 — verify analysis records have source and threshold_set.

    When *wo_id* is provided, only analysis files tagged with that work
    order (or untagged, for backward compatibility with pre-#166 records)
    are checked.  Analysis files tagged with a different work order are
    silently skipped (#253).
    """
    issues: list[dict[str, str]] = []
    analyses_checked = 0

    layer_0_classes = deliverables.get("layer_0_classes", [])
    if not isinstance(layer_0_classes, list) or not layer_0_classes:
        return {
            "name": "analysis_citations",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_0_classes declared",
        }

    for artifact_class in layer_0_classes:
        normalized = normalize_artifact_class(artifact_class)
        rel_dir = ARTIFACT_DIRS.get(normalized)
        if rel_dir is None:
            issues.append(
                {
                    "file": f"(artifact class {artifact_class!r})",
                    "issue": (
                        f"Artifact class {artifact_class!r} is not registered "
                        "in ARTIFACT_DIRS. This is a toolchain bug, not missing "
                        "science. File an issue."
                    ),
                }
            )
            continue
        art_dir = project_root / rel_dir
        if not art_dir.is_dir():
            continue
        for child in sorted(art_dir.iterdir()):
            if not child.is_file():
                continue
            if not is_safe_to_open(child):
                continue
            if confine_path(project_root, child) is None:
                continue
            if not _is_analysis(child.name, child):
                continue
            # WO scoping: read the record early to check work_order_id.
            # Skip analysis files tagged with a different work order.
            # Untagged files (work_order_id absent or null) are always
            # checked — backward compatibility with pre-#166 records.
            try:
                data = read_json(child, "analysis record")
            except ArtifactError:
                # Invalid JSON — still count and report below.
                data = None
            if data is not None and isinstance(data, dict):
                record_wo = data.get("work_order_id")
                if wo_id is not None and record_wo is not None and record_wo != wo_id:
                    continue
            analyses_checked += 1
            # data was already parsed above for WO scoping; reuse it.
            if data is None:
                issues.append(
                    {
                        "file": str(child.relative_to(project_root)),
                        "issue": "not valid JSON",
                    }
                )
                continue
            if not isinstance(data, dict):
                issues.append(
                    {
                        "file": str(child.relative_to(project_root)),
                        "issue": "not a JSON object",
                    }
                )
                continue
            if "source" not in data:
                issues.append(
                    {
                        "file": str(child.relative_to(project_root)),
                        "issue": "missing 'source' field",
                    }
                )
            if "threshold_set" not in data:
                issues.append(
                    {
                        "file": str(child.relative_to(project_root)),
                        "issue": "missing 'threshold_set' field",
                    }
                )
            # Verify source reference resolves within the project root.
            source = data.get("source")
            if isinstance(source, str) and source:
                source_path = confine_path(project_root, Path(source))
                if source_path is None:
                    issues.append(
                        {
                            "file": str(child.relative_to(project_root)),
                            "issue": f"source path escapes project root: {source}",
                        }
                    )
                elif not source_path.is_file():
                    issues.append(
                        {
                            "file": str(child.relative_to(project_root)),
                            "issue": (
                                f"Analysis cites source {source!r} which resolved "
                                f"to '{source_path}' — file not found. Expected "
                                "project-relative path like "
                                f"'raw/<class>/{Path(source).name}'."
                            ),
                        }
                    )

    detail: dict[str, Any] = {"analyses_checked": analyses_checked}
    if issues:
        detail["issues"] = issues
        return {
            "name": "analysis_citations",
            "result": "fail",
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": detail,
        }
    return {
        "name": "analysis_citations",
        "result": "pass",
        "status": "ok",
        "kind": "DATA_INTEGRITY",
        "detail": detail,
    }


def _collect_relay_codes(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> set[str]:
    """Collect mandatory relay codes from sidecar and analysis files.

    Scans all ``.meta.json`` and ``.analysis.json`` files in each
    artifact-class directory declared in *deliverables* and returns
    the set of relay code strings found in ``mandatory_relays[].code``.

    When *wo_id* is provided, records tagged with a different work
    order are skipped (#166).
    """
    layer_0_classes = deliverables.get("layer_0_classes", [])
    relay_codes: set[str] = set()

    if not isinstance(layer_0_classes, list):
        return relay_codes

    for artifact_class in layer_0_classes:
        normalized = normalize_artifact_class(artifact_class)
        rel_dir = ARTIFACT_DIRS.get(normalized)
        if rel_dir is None:
            continue
        art_dir = project_root / rel_dir
        if not art_dir.is_dir():
            continue
        for child in sorted(art_dir.iterdir()):
            if not child.is_file():
                continue
            if not is_safe_to_open(child):
                continue
            if confine_path(project_root, child) is None:
                continue
            if not (_is_sidecar(child.name) or _is_analysis(child.name, child)):
                continue
            try:
                data = read_json(child, "sidecar")
            except ArtifactError:
                continue
            if not isinstance(data, dict):
                continue
            # WO scoping: skip records from other work orders.
            record_wo = data.get("work_order_id")
            if wo_id is not None and record_wo is not None and record_wo != wo_id:
                continue
            relays = data.get("mandatory_relays", [])
            if isinstance(relays, list):
                for r in relays:
                    if isinstance(r, dict) and "code" in r:
                        relay_codes.add(r["code"])

    return relay_codes


# Regex for the standard relay label format: **Relay: `<code>`**
_RELAY_LABEL_RE = re.compile(r"\*\*Relay:\s*`([^`]+)`\*\*")


def _check_relay_coverage(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 6 — verify mandatory relay codes are addressed in findings.

    Two-tier check:

    1. **Presence** — the relay code string must appear somewhere in
       the findings text.  Missing → ``fail`` / ``COMPLETENESS``.
    2. **Label format** — when present, the code should appear in the
       standard ``**Relay: \\`<code>\\`**`` format.  Present without the
       label → ``warn`` / ``FORMAT`` sub-finding.

    When *wo_id* is provided, only relay codes from records tagged with
    that work order (or untagged records) are checked (#166).
    """
    layer_0_classes = deliverables.get("layer_0_classes", [])
    layer_1 = deliverables.get("layer_1", [])

    if not isinstance(layer_0_classes, list) or not layer_0_classes:
        return {
            "name": "relay_coverage",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_0_classes declared",
        }

    relay_codes = _collect_relay_codes(project_root, deliverables, wo_id)

    if not relay_codes:
        return {
            "name": "relay_coverage",
            "result": "pass",
            "status": "ok",
            "kind": "COMPLETENESS",
            "detail": {
                "codes_checked": 0,
                "codes_addressed": 0,
                "codes_not_addressed": 0,
                "unaddressed": [],
            },
        }

    # Read all Layer 1 findings content
    findings_text = ""
    if isinstance(layer_1, list):
        for rel_path in layer_1:
            resolved = confine_path(project_root, Path(rel_path))
            if resolved is not None and resolved.is_file():
                findings_text += resolved.read_text(encoding="utf-8", errors="replace")

    # Collect codes found in standard label format.
    labeled_codes: set[str] = set()
    for m in _RELAY_LABEL_RE.finditer(findings_text):
        labeled_codes.add(m.group(1))

    # Check each code against findings text
    addressed: set[str] = set()
    unaddressed: list[str] = []
    sub_findings: list[dict[str, Any]] = []

    for code in sorted(relay_codes):
        if code in findings_text:
            addressed.add(code)
            # Label-format sub-check.
            if code not in labeled_codes:
                sub_findings.append(
                    {
                        "code": code,
                        "status": "warn",
                        "kind": "FORMAT",
                        "detail": "relay code found in text but not in standard label format",
                    }
                )
        else:
            unaddressed.append(code)

    detail: dict[str, Any] = {
        "codes_checked": len(relay_codes),
        "codes_addressed": len(addressed),
        "codes_not_addressed": len(unaddressed),
        "unaddressed": unaddressed,
    }

    if unaddressed:
        result = {
            "name": "relay_coverage",
            "result": "fail",
            "status": "fail",
            "kind": "COMPLETENESS",
            "detail": detail,
        }
    elif sub_findings:
        result = {
            "name": "relay_coverage",
            "result": "pass",
            "status": "warn",
            "kind": "FORMAT",
            "detail": detail,
        }
    else:
        result = {
            "name": "relay_coverage",
            "result": "pass",
            "status": "ok",
            "kind": "COMPLETENESS",
            "detail": detail,
        }

    if sub_findings:
        result["sub_findings"] = sub_findings

    return result


def _check_version_policy(
    project_root: Path,
) -> dict[str, Any]:
    """Check 7 — verify version policy compliance.

    Today program.yaml does not exist, so skip is the expected result.
    When program.yaml is added (deferred to a future program.yaml /
    exemption-scope-configuration issue), this check will read version
    requirements and verify cli_version/env_version in sidecars.
    """
    program_yaml = project_root / "program.yaml"
    if not program_yaml.is_file():
        return {
            "name": "version_policy",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "program.yaml not present — check deferred to a future program.yaml configuration issue",
        }

    # Future: read version requirements and check against sidecars.
    # For now, this is a placeholder for when program.yaml exists.
    return {
        "name": "version_policy",
        "result": "pass",
        "status": "ok",
        "kind": "COMPLETENESS",
        "detail": "program.yaml present; version policy check not yet implemented",
    }


def _check_findings_integrity(
    project_root: Path,
) -> dict[str, Any]:
    """Check 8 — verify no provenance sidecars are misplaced under findings/.

    Scans findings/ recursively for sidecar and analysis files (including
    the ``.sc-`` variants).  These are tool-written provenance records
    that belong under raw/, not findings/.  Only flags actual files with
    those extensions, not markdown files that mention them in prose.
    """
    findings_dir = project_root / "findings"
    if not findings_dir.is_dir():
        return {
            "name": "findings_integrity",
            "result": "pass",
            "status": "ok",
            "kind": "CONVENTION",
            "detail": "no findings/ directory present",
        }

    misplaced: list[str] = []
    for child in sorted(findings_dir.rglob("*")):
        if not child.is_file():
            continue
        if not is_safe_to_open(child):
            continue
        if _is_sidecar(child.name) or _is_analysis(child.name, child):
            misplaced.append(str(child.relative_to(project_root)))

    if misplaced:
        return {
            "name": "findings_integrity",
            "result": "fail",
            "status": "fail",
            "kind": "CONVENTION",
            "detail": {"misplaced_files": misplaced},
        }
    return {
        "name": "findings_integrity",
        "result": "pass",
        "status": "ok",
        "kind": "CONVENTION",
        "detail": "no misplaced sidecars found under findings/",
    }


def _check_unrecognized_json(
    project_root: Path,
    deliverables: dict[str, Any],
    wo_id: str | None = None,
) -> dict[str, Any]:
    """Check 10 — flag JSON files that are neither analysis nor provenance-covered.

    Scans each artifact-class directory for ``.json`` files that are not
    sidecars, not analysis records (by suffix or content), and not covered
    by any provenance sidecar.  These files are unrecognised — they may
    indicate a toolchain bug (e.g. a new record type whose suffix was not
    added to the allowlist).

    Emits finding kind ``unrecognized_record_type`` so the result is
    distinguishable from ``provenance_valid`` failures (#130, #84).
    """
    unrecognized: list[dict[str, str]] = []

    layer_0_classes = deliverables.get("layer_0_classes", [])
    if not isinstance(layer_0_classes, list) or not layer_0_classes:
        return {
            "name": "unrecognized_json",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_0_classes declared",
        }

    for artifact_class in layer_0_classes:
        normalized = normalize_artifact_class(artifact_class)
        rel_dir = ARTIFACT_DIRS.get(normalized)
        if rel_dir is None:
            continue
        art_dir = project_root / rel_dir
        if not art_dir.is_dir():
            continue

        # Build sidecar index to check provenance coverage.
        sidecar_index, _, other_wo_hashes = _build_sidecar_index(
            art_dir,
            project_root,
            wo_id=wo_id,
        )

        for child in sorted(art_dir.iterdir()):
            if not child.is_file():
                continue
            if not child.name.endswith(".json"):
                continue
            # Reject symlinks and paths outside project root FIRST.
            if not is_safe_to_open(child):
                continue
            if confine_path(project_root, child) is None:
                continue
            # Skip recognised record types.
            if _is_sidecar(child.name):
                continue
            if _is_analysis(child.name, child):
                continue
            # Check whether a sidecar covers this file.
            actual_sha = sha256_file(child)
            if actual_sha in other_wo_hashes and actual_sha not in sidecar_index:
                continue  # belongs to another WO
            if actual_sha in sidecar_index:
                continue  # covered by provenance — it is a known raw artifact
            unrecognized.append(
                {
                    "file": str(child.relative_to(project_root)),
                    "message": (
                        f"File '{child.name}' is not a recognized analysis or "
                        "raw artifact type. If it was produced by a DDE command, "
                        "this may be a toolchain bug."
                    ),
                }
            )

    detail: dict[str, Any] = {"files_checked": len(unrecognized)}
    if unrecognized:
        detail["unrecognized"] = unrecognized
        return {
            "name": "unrecognized_json",
            "result": "fail",
            "status": "fail",
            "kind": "unrecognized_record_type",
            "detail": detail,
        }
    return {
        "name": "unrecognized_json",
        "result": "pass",
        "status": "ok",
        "kind": "unrecognized_record_type",
        "detail": "no unrecognized JSON files found",
    }


# ---------------------------------------------------------------------------
# Source-tag resolution helpers
# ---------------------------------------------------------------------------

# Regex for scalar source tags: {source: <path> [<locator>]}
_SOURCE_TAG_RE = re.compile(r"\{source:\s+(\S+)(?:\s+(.*?))?\}")
# Regex for table source tags: {source-table: <path> [<locator>]}
_SOURCE_TABLE_TAG_RE = re.compile(r"\{source-table:\s+(\S+)(?:\s+(.*?))?\}")

# Number immediately before a {source: token.
# Captures: optional negative sign, integer or decimal, optional scientific
# notation, optional trailing % sign.  We look backward from the {source:
# position so the match is on the content *before* the tag.
_CLAIMED_VALUE_RE = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*%?\s*$")

# Code fences: triple backtick blocks and inline backtick spans.
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]+`")

# Identifier-like tokens: letter immediately adjacent to digits (Q99650,
# CID12345, PDB:1ABC, rs123456) — used to reject non-numerical claims.
_IDENTIFIER_LIKE_RE = re.compile(r"[A-Za-z]\d+|\d+[A-Za-z]")


def _strip_code_spans(text: str) -> str:
    """Replace code fences and inline code with whitespace to skip tags inside them."""
    text = _CODE_FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)
    text = _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _values_match(claimed: float, actual: float) -> bool:
    """Check whether *claimed* matches *actual* within ±1% tolerance."""
    if claimed == actual:
        return True
    if abs(actual) < 1e-10:
        return abs(claimed - actual) < 1e-10
    return abs(claimed - actual) / abs(actual) <= 0.01


def _extract_claimed_value(text_before_tag: str) -> float | None:
    """Extract the numerical value immediately preceding a {source: tag.

    Returns ``None`` when no numerical claim is found (e.g. the tag
    provides file-level provenance for a non-numerical reference).
    """
    # Grab the last whitespace-separated tokens (up to ~60 chars is plenty).
    snippet = text_before_tag[-60:].rstrip()
    if not snippet:
        return None

    # Check for identifier-like tokens at the very end of the snippet.
    # Split by whitespace; look at the last token.
    last_token = snippet.split()[-1] if snippet.split() else ""
    # Strip trailing punctuation that is not part of a number (commas, colons,
    # parentheses, etc.) but keep %, -, ., e/E, +.
    cleaned = last_token.rstrip(",;:)")

    # Reject identifier-like tokens: letters adjacent to digits.
    if _IDENTIFIER_LIKE_RE.search(cleaned):
        return None

    m = _CLAIMED_VALUE_RE.search(snippet)
    if m is None:
        return None
    try:
        return float(m.group(1))
    except (ValueError, OverflowError):
        return None


def _check_source_tags_resolve(
    project_root: Path,
    deliverables: dict[str, Any],
) -> dict[str, Any]:
    """Check 9 — verify {source:} and {source-table:} tags resolve and match.

    Parses source tags from all Layer 1 files, resolves each tag's path
    and locator against the project root, and — for scalar tags with a
    claimed numerical value — compares the claimed value against the
    resolved actual value within ±1% tolerance.
    """
    try:
        from jsonpath_ng import parse as jsonpath_parse  # deferred import
    except ImportError:
        return {
            "name": "source_tags_resolve",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "jsonpath-ng is not installed — cannot evaluate source tags",
        }

    layer_1 = deliverables.get("layer_1", [])
    if not isinstance(layer_1, list) or not layer_1:
        return {
            "name": "source_tags_resolve",
            "result": "skip",
            "status": "skip",
            "kind": None,
            "detail": "no layer_1 deliverables declared",
        }

    sub_findings: list[dict[str, Any]] = []

    for rel_path in layer_1:
        resolved_file = confine_path(project_root, Path(rel_path))
        if resolved_file is None or not resolved_file.is_file():
            continue  # deliverables_exist already flags these

        raw_content = resolved_file.read_text(encoding="utf-8", errors="replace")
        content = _strip_code_spans(raw_content)

        # --- Parse scalar source tags ---
        for m in _SOURCE_TAG_RE.finditer(content):
            tag_path_str = m.group(1)
            locator = (m.group(2) or "").strip() or None
            tag_start = m.start()
            tag_text = m.group(0)

            # Line number of this tag in the original file.
            line_no = raw_content[:tag_start].count("\n") + 1

            # Extract the claimed value from text before the tag.
            text_before = content[:tag_start]
            claimed_value = _extract_claimed_value(text_before)

            sf = _evaluate_scalar_tag(
                project_root=project_root,
                tag_path_str=tag_path_str,
                locator=locator,
                claimed_value=claimed_value,
                tag_text=tag_text,
                finding_file=str(rel_path),
                line_no=line_no,
                jsonpath_parse=jsonpath_parse,
            )
            sub_findings.append(sf)

        # --- Parse source-table tags ---
        for m in _SOURCE_TABLE_TAG_RE.finditer(content):
            tag_path_str = m.group(1)
            locator = (m.group(2) or "").strip() or None
            tag_start = m.start()
            tag_text = m.group(0)
            line_no = raw_content[:tag_start].count("\n") + 1

            sf = _evaluate_table_tag(
                project_root=project_root,
                tag_path_str=tag_path_str,
                locator=locator,
                tag_text=tag_text,
                finding_file=str(rel_path),
                line_no=line_no,
                jsonpath_parse=jsonpath_parse,
            )
            sub_findings.append(sf)

    # --- Aggregate ---
    if not sub_findings:
        return {
            "name": "source_tags_resolve",
            "result": "pass",
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "detail": "no source tags found",
            "sub_findings": [],
        }

    has_fail = any(sf["status"] == "fail" for sf in sub_findings)
    has_warn = any(sf["status"] == "warn" for sf in sub_findings)

    if has_fail:
        return {
            "name": "source_tags_resolve",
            "result": "fail",
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": {
                "tags_checked": len(sub_findings),
                "tags_failed": sum(1 for sf in sub_findings if sf["status"] == "fail"),
                "tags_warned": sum(1 for sf in sub_findings if sf["status"] == "warn"),
            },
            "sub_findings": sub_findings,
        }
    if has_warn:
        return {
            "name": "source_tags_resolve",
            "result": "pass",
            "status": "warn",
            "kind": "FORMAT",
            "detail": {
                "tags_checked": len(sub_findings),
                "tags_warned": sum(1 for sf in sub_findings if sf["status"] == "warn"),
            },
            "sub_findings": sub_findings,
        }
    return {
        "name": "source_tags_resolve",
        "result": "pass",
        "status": "ok",
        "kind": "DATA_INTEGRITY",
        "detail": {"tags_checked": len(sub_findings)},
        "sub_findings": sub_findings,
    }


def _evaluate_scalar_tag(
    *,
    project_root: Path,
    tag_path_str: str,
    locator: str | None,
    claimed_value: float | None,
    tag_text: str,
    finding_file: str,
    line_no: int,
    jsonpath_parse: Any,
) -> dict[str, Any]:
    """Evaluate a single scalar {source: ...} tag and return a sub-finding."""
    base = {"tag": tag_text, "file": finding_file, "line": line_no}

    # Step 3: resolve path.
    resolved = confine_path(project_root, Path(tag_path_str))
    if resolved is None:
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"path escapes project root: {tag_path_str}",
        }
    if not resolved.is_file():
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"path does not resolve: {tag_path_str}",
        }

    # Step 4: evaluate locator.
    if locator is None:
        # No locator.
        if claimed_value is not None:
            return {
                **base,
                "status": "warn",
                "kind": "FORMAT",
                "detail": "missing locator — cannot verify value mechanically",
            }
        # Non-numerical reference — file-level provenance is sufficient.
        return {
            **base,
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "detail": "file-level provenance (no locator, non-numerical)",
        }

    # Locator present — determine type and evaluate.
    if locator.startswith("$"):
        return _evaluate_jsonpath_locator(
            resolved=resolved,
            locator=locator,
            claimed_value=claimed_value,
            base=base,
            is_table=False,
            jsonpath_parse=jsonpath_parse,
        )

    # Line-number locator: path has :N suffix — but in the source-tag convention
    # the locator is a separate token, so check if it looks like a line number.
    line_match = re.match(r"^:?(\d+)$", locator)
    if line_match:
        return _evaluate_line_locator(
            resolved=resolved,
            line_num=int(line_match.group(1)),
            claimed_value=claimed_value,
            base=base,
        )

    # Unrecognised locator format — treat as ok (might be a future extension).
    return {
        **base,
        "status": "ok",
        "kind": "DATA_INTEGRITY",
        "detail": f"locator format not recognised: {locator}",
    }


def _evaluate_table_tag(
    *,
    project_root: Path,
    tag_path_str: str,
    locator: str | None,
    tag_text: str,
    finding_file: str,
    line_no: int,
    jsonpath_parse: Any,
) -> dict[str, Any]:
    """Evaluate a single {source-table: ...} tag and return a sub-finding."""
    base = {"tag": tag_text, "file": finding_file, "line": line_no}

    resolved = confine_path(project_root, Path(tag_path_str))
    if resolved is None:
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"path escapes project root: {tag_path_str}",
        }
    if not resolved.is_file():
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"path does not resolve: {tag_path_str}",
        }

    if locator is None:
        # Table tag without locator — file-level provenance.
        return {
            **base,
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "detail": "file-level provenance (table tag, no locator)",
        }

    if locator.startswith("$"):
        return _evaluate_jsonpath_locator(
            resolved=resolved,
            locator=locator,
            claimed_value=None,
            base=base,
            is_table=True,
            jsonpath_parse=jsonpath_parse,
        )

    return {
        **base,
        "status": "ok",
        "kind": "DATA_INTEGRITY",
        "detail": f"locator format not recognised: {locator}",
    }


def _evaluate_jsonpath_locator(
    *,
    resolved: Path,
    locator: str,
    claimed_value: float | None,
    base: dict[str, Any],
    is_table: bool,
    jsonpath_parse: Any,
) -> dict[str, Any]:
    """Evaluate a JSONPath locator against a JSON file."""
    _MAX_JSON_READ_BYTES = 50 * 1024 * 1024  # 50 MB
    try:
        file_size = resolved.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _MAX_JSON_READ_BYTES:
        return {
            **base,
            "status": "warn",
            "kind": "FORMAT",
            "detail": f"JSON file too large to evaluate ({file_size} bytes)",
        }
    try:
        data = json_mod.loads(resolved.read_text(encoding="utf-8", errors="replace"))
    except (json_mod.JSONDecodeError, OSError) as exc:
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"cannot parse JSON: {exc}",
        }

    try:
        expr = jsonpath_parse(locator)
    except Exception as exc:
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"invalid JSONPath expression: {exc}",
        }

    matches = expr.find(data)
    if not matches:
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"locator does not resolve: {locator}",
        }

    actual = matches[0].value

    # For table tags: resolution is enough; non-scalar is expected.
    if is_table:
        return {
            **base,
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "detail": "table tag locator resolves",
        }

    # For scalar tags: non-scalar resolution is a format warning.
    if isinstance(actual, (dict, list)):
        return {
            **base,
            "status": "warn",
            "kind": "FORMAT",
            "detail": "locator resolves to non-scalar",
        }

    # If no claimed value was extracted, we can only confirm resolution.
    if claimed_value is None:
        return {
            **base,
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "detail": "locator resolves (no numerical claim to compare)",
        }

    # Compare values.
    if isinstance(actual, (int, float)):
        if _values_match(claimed_value, actual):
            return {
                **base,
                "status": "ok",
                "kind": "DATA_INTEGRITY",
                "claimed_value": str(claimed_value),
                "actual_value": str(actual),
                "detail": "values match within tolerance",
            }
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "claimed_value": str(claimed_value),
            "actual_value": str(actual),
            "detail": f"value mismatch: claimed {claimed_value}, actual {actual}",
        }

    # Actual is a string — attempt exact match with claimed value string.
    claimed_str = str(claimed_value)
    # Also try integer representation if the float is integral.
    if claimed_value == int(claimed_value):
        claimed_str_int = str(int(claimed_value))
        if str(actual) == claimed_str or str(actual) == claimed_str_int:
            return {
                **base,
                "status": "ok",
                "kind": "DATA_INTEGRITY",
                "claimed_value": claimed_str,
                "actual_value": str(actual),
                "detail": "string values match",
            }
    elif str(actual) == claimed_str:
        return {
            **base,
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "claimed_value": claimed_str,
            "actual_value": str(actual),
            "detail": "string values match",
        }

    return {
        **base,
        "status": "fail",
        "kind": "DATA_INTEGRITY",
        "claimed_value": claimed_str,
        "actual_value": str(actual),
        "detail": f"value mismatch: claimed {claimed_value}, actual {actual!r}",
    }


def _evaluate_line_locator(
    *,
    resolved: Path,
    line_num: int,
    claimed_value: float | None,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate a line-number locator against a text file."""
    try:
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"cannot read file: {exc}",
        }

    if line_num < 1 or line_num > len(lines):
        return {
            **base,
            "status": "fail",
            "kind": "DATA_INTEGRITY",
            "detail": f"line {line_num} does not exist (file has {len(lines)} lines)",
        }

    if claimed_value is None:
        return {
            **base,
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "detail": f"line {line_num} exists (no numerical claim to compare)",
        }

    # Best-effort: scan for a number on that line.
    line_text = lines[line_num - 1]
    num_match = re.search(r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", line_text)
    if num_match is None:
        return {
            **base,
            "status": "warn",
            "kind": "FORMAT",
            "detail": f"line {line_num} exists but no number found to compare",
        }

    try:
        actual = float(num_match.group(1))
    except (ValueError, OverflowError):
        return {
            **base,
            "status": "warn",
            "kind": "FORMAT",
            "detail": f"line {line_num} exists but number could not be parsed",
        }

    if _values_match(claimed_value, actual):
        return {
            **base,
            "status": "ok",
            "kind": "DATA_INTEGRITY",
            "claimed_value": str(claimed_value),
            "actual_value": str(actual),
            "detail": "values match within tolerance",
        }
    return {
        **base,
        "status": "fail",
        "kind": "DATA_INTEGRITY",
        "claimed_value": str(claimed_value),
        "actual_value": str(actual),
        "detail": f"value mismatch: claimed {claimed_value}, actual {actual}",
    }


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def validate() -> None:
    """Mechanical validation of submitted work-order deliverables."""


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _resolve_wo_record(
    project_root: Path,
    wo_id: str,
    revision_num: int | None = None,
) -> dict[str, Any]:
    """Resolve and return a work-order record.

    When *revision_num* is provided, reads that specific revision;
    otherwise finds the latest.  Validates that the ``deliverables``
    field is a dict.

    Raises
    ------
    SchemaError
        If the deliverables field is malformed.
    """
    if revision_num is not None:
        identifier = f"{wo_id}-r{revision_num}"
        return controlstore.read_record(project_root, "work-order", identifier)
    return _find_latest_revision(project_root, wo_id)


def _run_all_checks(
    project_root: Path,
    wo_record: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Run all mechanical validation checks and return results.

    This is the pure check-running logic, separated from record
    writing, state transitions, and state guards.  It can be called
    in any WO state (used by ``--dry-run``) or from the recording
    path (used by ``_perform_validation``).

    Parameters
    ----------
    project_root:
        Root of the project directory.
    wo_record:
        The resolved work-order record dict.

    Returns
    -------
    (checks, overall_result, checks_failed)
        *checks* is the list of individual check result dicts,
        *overall_result* is the severity-model verdict string,
        *checks_failed* is the list of check names that failed.
    """
    wo_id = wo_record["id"]
    revision = wo_record["revision"]
    deliverables_raw = wo_record.get("deliverables", {})
    if not isinstance(deliverables_raw, dict):
        raise SchemaError(
            f"work order {wo_id} has invalid deliverables field",
            detail=f"expected dict, got {type(deliverables_raw).__name__}",
        )

    # Normalize deliverable keys: accept both layer_0 and layer_0_classes.
    deliverables = normalize_deliverables(deliverables_raw)

    # Pre-flight: check whether the deliverable schema is recognizable.
    # If not, emit an explicit finding rather than silently skipping
    # checks (vacuous-pass avoidance).
    checks: list[dict[str, Any]] = []
    schema_finding = _check_deliverables_schema(deliverables_raw)
    if schema_finding is not None:
        checks.append(schema_finding)

    # Run all 10 checks.
    checks.extend(
        [
            _check_deliverables_exist(project_root, deliverables, wo_id=wo_id),
            _check_report_headings(project_root, deliverables, wo_id, revision),
            _check_paths_resolve(project_root, deliverables),
            _check_provenance_valid(project_root, deliverables, wo_id=wo_id),
            _check_analysis_citations(project_root, deliverables, wo_id=wo_id),
            _check_relay_coverage(project_root, deliverables, wo_id=wo_id),
            _check_version_policy(project_root),
            _check_findings_integrity(project_root),
            _check_source_tags_resolve(project_root, deliverables),
            _check_unrecognized_json(project_root, deliverables, wo_id=wo_id),
        ]
    )

    # Determine overall result using the severity-model verdict.
    overall_result = _overall_verdict(checks)
    checks_failed = [c["name"] for c in checks if c["result"] == "fail"]

    return checks, overall_result, checks_failed


def _perform_validation(
    project_root: Path,
    wo_id: str,
    revision_num: int | None = None,
) -> tuple[dict[str, Any], Path, str, list[dict[str, Any]], list[str], str, str]:
    """Execute the full 9-check mechanical validation on a submitted WO.

    Resolves the work-order record, enforces ``submitted`` state,
    runs all 9 mechanical checks, writes the validation record,
    performs the state transition, and appends the event log entry.

    This is the shared core behind ``validate check`` and
    ``workorder accept``.  Both code paths call this function so the
    validation logic — including the ``submitted``-state guard (#237)
    — is never reimplemented or bypassed.

    Returns ``(wo_record, validation_record_path, overall_result,
    checks, checks_failed, from_state, to_state)``.

    Raises
    ------
    Refusal
        If the WO is not in ``submitted`` state.
    SchemaError
        If the deliverables field is malformed.
    """
    wo_record = _resolve_wo_record(project_root, wo_id, revision_num)

    wo_id = wo_record["id"]
    revision = wo_record["revision"]
    current_state = wo_record["state"]

    # The WO must be in submitted state for validation.  Fail fast
    # before running any checks — otherwise we'd write an orphaned
    # validation record that no state transition references.
    if current_state != "submitted":
        raise Refusal(
            f"work order {wo_id} is in state {current_state!r}, expected 'submitted'",
            detail="validation can only be run against submitted work orders",
            remedy="transition the work order to 'submitted' first",
        )

    # Run all checks (shared logic with --dry-run).
    checks, overall_result, checks_failed = _run_all_checks(project_root, wo_record)

    # Find the latest run for this WO revision (informational).
    runs = controlstore.list_records(
        project_root,
        "run",
        filter_fn=lambda r: (
            r.get("work_order_id") == wo_id and r.get("work_order_revision") == revision
        ),
    )
    run_id = (
        max(runs, key=lambda r: r.get("created_at", "")).get("run_id") if runs else None
    )

    # Build validation record.
    record: dict[str, Any] = {
        "work_order_id": wo_id,
        "work_order_revision": revision,
        "run_id": run_id,
        "validated_at": _utc_now(),
        "cli_version": CLI_VERSION,
        "result": overall_result,
        "checks": checks,
    }

    # Write the validation record.
    val_identifier = f"{wo_id}-r{revision}"
    record_path = controlstore.write_record(
        project_root,
        "validation",
        val_identifier,
        record,
    )

    # State transition.
    if overall_result in ("pass", "pass_with_warnings"):
        target_state = "mechanically_validated"
    else:
        target_state = "validation_failed"

    validate_transition("workorder", current_state, target_state)
    wo_record["state"] = target_state
    wo_identifier = f"{wo_id}-r{revision}"
    controlstore.write_record(project_root, "work-order", wo_identifier, wo_record)

    # Append event.
    controlstore.append_event(
        project_root,
        {
            "type": "validation.completed",
            "subject_id": wo_id,
            "revision": revision,
            "from_state": current_state,
            "to_state": target_state,
            "detail": {"result": overall_result, "checks_failed": checks_failed},
        },
    )

    return (
        wo_record,
        record_path,
        overall_result,
        checks,
        checks_failed,
        current_state,
        target_state,
    )


@validate.command("check")
@click.argument("work_order_id", metavar="WORK-ORDER-ID")
@click.option(
    "--revision",
    "revision_num",
    type=int,
    default=None,
    help="Validate a specific revision (default: latest).",
)
@click.option(
    "--dry-run",
    "--preflight",
    "dry_run",
    is_flag=True,
    default=False,
    help=(
        "Run all checks without requiring submitted state, "
        "writing a validation record, or performing a state transition. "
        "Advisory only."
    ),
)
@output_options
@pass_state
def check_cmd(
    state: AppState,
    work_order_id: str,
    revision_num: int | None,
    dry_run: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Run 10 mechanical validation checks against a submitted work order.

    With --dry-run (alias --preflight), runs all checks against the
    current workspace regardless of work-order state.  No validation
    record is written and no state transition is performed.  The output
    is clearly marked as advisory.  Exits non-zero on any check failure
    so it can be composed in scripts.
    """
    import sys as _sys

    emit = emitter(as_json, quiet)
    project = state.project()

    if dry_run:
        # Dry-run mode: skip state guard, run checks only, no recording.
        wo_record = _resolve_wo_record(
            project.root,
            work_order_id,
            revision_num,
        )
        wo_id = wo_record["id"]
        revision = wo_record["revision"]

        checks, overall_result, checks_failed = _run_all_checks(
            project.root,
            wo_record,
        )

        has_failure = bool(checks_failed)

        # --- Output ---
        if as_json:
            emit.data("mode", "dry_run")
            emit.data("work_order_id", wo_id)
            emit.data("work_order_revision", revision)
            emit.data("result", overall_result)
            emit.data("checks", checks)
            if checks_failed:
                emit.data("checks_failed", checks_failed)
            emit.flush()
            if has_failure:
                _sys.exit(1)
            return

        if quiet:
            emit.line(f"{overall_result.upper()}")
            emit.flush()
            if has_failure:
                _sys.exit(1)
            return

        # Human-readable dry-run report.
        emit.line("=== PREFLIGHT VALIDATION (dry run) ===")
        emit.line("")
        emit.line(
            f"Validation: {wo_id} revision {revision}  [{overall_result.upper()}]"
        )
        emit.line("")
        for c in checks:
            status = c["result"].upper()
            emit.line(f"  {c['name']:<25} {status}")
        emit.line("")
        if checks_failed:
            emit.line(f"Failed checks: {', '.join(checks_failed)}")
            emit.line("")
        emit.line("This is an advisory check. No validation record has been written.")
        emit.flush()
        if has_failure:
            _sys.exit(1)
        return

    # --- Normal (non-dry-run) path: full validation with recording ---
    (
        wo_record,
        record_path,
        overall_result,
        checks,
        checks_failed,
        current_state,
        target_state,
    ) = _perform_validation(project.root, work_order_id, revision_num)
    wo_id = wo_record["id"]
    revision = wo_record["revision"]

    # Output.
    if as_json:
        # Read the full validation record from disk (includes run_id,
        # validated_at, cli_version written by _perform_validation).
        val_record = controlstore.read_record(
            project.root,
            "validation",
            f"{wo_id}-r{revision}",
        )
        for k, v in val_record.items():
            emit.data(k, v)
        emit.path(record_path, role="validation_record")
        emit.flush()
        return

    if quiet:
        emit.path(record_path, role="validation_record")
        emit.flush()
        return

    # Bounded human summary.
    emit.line(f"Validation: {wo_id} revision {revision}  [{overall_result.upper()}]")
    emit.line("")
    for c in checks:
        status = c["result"].upper()
        emit.line(f"  {c['name']:<25} {status}")
    emit.line("")
    if checks_failed:
        emit.line(f"Failed checks: {', '.join(checks_failed)}")
    emit.line(f"State: {current_state} → {target_state}")
    emit.path(record_path, role="validation_record")
    emit.flush()
