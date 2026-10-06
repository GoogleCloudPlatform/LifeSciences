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

"""`dde site` — deterministic site build from accepted work-order deliverables.

Builds a static HTML site from scientifically accepted work-order
revisions.  The site reflects the artifact hierarchy:

  Executive Summary (L4) → Program State (L2) → Findings (L1) → Raw Data (L0)

Output is written atomically: a temporary directory is built first,
then moved into the final location only on complete success.  A broken
link or missing reference aborts the build and leaves no partial output.

This is Phase 3 of issue #22 — control plane CLI.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
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
from ..core.errors import ArtifactError, Refusal, UsageError
from ..core.paths import confine_path

# ---------------------------------------------------------------------------
# Viewer mapping — file extension to viewer HTML file
# ---------------------------------------------------------------------------

# Ordered most-specific suffix first. Matching stops at the first hit.
VIEWER_MAP: list[tuple[str, str]] = [
    (".afdb.json", "plddt-viewer.html"),
    (".pae.json", "pae-viewer.html"),
    (".gnomad-constraint.json", "constraint-viewer.html"),
    (".tissue.json", "expression-viewer.html"),
    (".tournament.json", "tournament-viewer.html"),
    (".pockets.json", "pockets-viewer.html"),
    (".predict.json", "admet-viewer.html"),
    (".docking_result.json", "docking-scores-viewer.html"),
    (".3d.sdf", "sdf-viewer.html"),
    (".cif", "structure-viewer.html"),
    (".poses.pdbqt", "docking-viewer.html"),
    (".receptor.pdbqt", "docking-viewer.html"),
    (".brics.json", "brics-viewer.html"),
    (".json", "json-viewer.html"),  # fallback for any .json
]


def viewer_url_for(
    filename: str,
    artifact_dir: str,
    bundled: bool = True,
) -> str | None:
    """Return viewer URL with ?file= param, or None if no viewer matches.

    The artifact_dir is the relative path to the artifact directory from
    the project root (e.g., "raw/structures"). The viewer HTML lives in
    viewers/ under the site output. The ?file= path is relative to the
    viewer's location in the output directory.

    When *bundled* is True (default), raw/ is copied into _site/ so
    viewers reach artifacts via ``../raw/…``.  When False, the old
    ``../../raw/…`` paths are used for an unbundled layout.
    """
    lower = filename.lower()
    prefix = ".." if bundled else "../.."
    for suffix, viewer in VIEWER_MAP:
        if lower.endswith(suffix):
            return f"viewers/{viewer}?file={prefix}/{artifact_dir}/{filename}"
    return None


_MARKDOWN_LINK_RE = re.compile(r"!?\[(?:[^\]]*)\]\(([^)]+)\)")


def _strip_code(text: str) -> str:
    """Remove fenced code blocks and inline code spans from markdown.

    This prevents code content (especially SMILES notation) from being
    matched as markdown links by the link validator.
    """
    # Remove fenced code blocks (```...```)
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code spans (`...`)
    text = re.sub(r"`[^`]+`", "", text)
    return text


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _collect_accepted_work_orders(
    project_root: Path,
) -> list[dict[str, Any]]:
    """Return all scientifically_accepted work-order revisions, sorted by id."""
    records = controlstore.list_records(
        project_root,
        "work-order",
        filter_fn=lambda r: r.get("state") == "scientifically_accepted",
    )
    # Deterministic sort: by id, then by revision
    records.sort(key=lambda r: (r.get("id", ""), r.get("revision", 0)))
    return records


def _collect_findings(
    project_root: Path,
    work_orders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect Layer 1 findings from accepted work orders.

    Returns a list of finding dicts with metadata for template rendering.
    """
    findings: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for wo in work_orders:
        deliverables_raw = wo.get("deliverables", {})
        if not isinstance(deliverables_raw, dict):
            continue
        deliverables = normalize_deliverables(deliverables_raw)
        layer_1 = deliverables.get("layer_1", [])
        if not isinstance(layer_1, list):
            continue

        # Collect artifact classes for provenance linking
        layer_0_classes = deliverables.get("layer_0_classes", [])
        if not isinstance(layer_0_classes, list):
            layer_0_classes = []

        ac_links = [
            {
                "name": ac,
                "html_filename": f"artifact_{re.sub(r'[^a-zA-Z0-9_-]', '_', ac)}.html",
            }
            for ac in sorted(layer_0_classes)
        ]

        for rel_path in sorted(layer_1):
            if rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)

            resolved = confine_path(project_root, Path(rel_path))
            if resolved is None or not resolved.is_file():
                continue

            content = resolved.read_text(encoding="utf-8", errors="replace")

            # Derive a title from the first markdown heading, or the filename
            title = Path(rel_path).stem.replace("-", " ").replace("_", " ").title()
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break

            # Sanitise the filename for HTML output (use full rel_path for uniqueness)
            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", str(Path(rel_path)))
            html_filename = f"finding_{safe_name}.html"

            findings.append(
                {
                    "title": title,
                    "source_path": rel_path,
                    "html_filename": html_filename,
                    "content": content,
                    "work_order_id": wo.get("id", ""),
                    "revision": wo.get("revision", 0),
                    "requested_role": wo.get("requested_role", ""),
                    "state": wo.get("state", ""),
                    "stage": wo.get("stage", ""),
                    "cycle": wo.get("cycle", ""),
                    "artifact_classes": ac_links,
                }
            )

    return findings


def _collect_artifact_classes(
    project_root: Path,
    work_orders: list[dict[str, Any]],
    bundled: bool = True,
) -> list[dict[str, Any]]:
    """Collect unique Layer 0 artifact classes referenced by accepted WOs.

    Returns a list of artifact-class dicts for template rendering.
    """
    # Gather unique classes and their referencing work orders
    class_to_wos: dict[str, list[dict[str, Any]]] = {}
    for wo in work_orders:
        deliverables_raw = wo.get("deliverables", {})
        if not isinstance(deliverables_raw, dict):
            continue
        deliverables = normalize_deliverables(deliverables_raw)
        layer_0_classes = deliverables.get("layer_0_classes", [])
        if not isinstance(layer_0_classes, list):
            continue
        for ac in layer_0_classes:
            class_to_wos.setdefault(ac, []).append(wo)

    result: list[dict[str, Any]] = []
    for ac_name in sorted(class_to_wos):
        normalized = normalize_artifact_class(ac_name)
        rel_dir = ARTIFACT_DIRS.get(normalized)
        if rel_dir is None:
            # Unknown artifact class — skip rather than using unsanitized fallback
            continue
        art_dir = project_root / rel_dir

        artifacts: list[dict[str, str]] = []
        if art_dir.is_dir():
            root_resolved = project_root.resolve()
            for child in sorted(art_dir.iterdir()):
                if not child.is_file():
                    continue
                if child.name.endswith(".meta.json") or child.name.endswith(
                    ".analysis.json"
                ):
                    continue
                # Confine symlinks
                if not child.resolve().is_relative_to(root_resolved):
                    continue
                artifacts.append(
                    {
                        "name": child.name,
                        "rel_path": str(child.relative_to(project_root)),
                        "viewer_url": viewer_url_for(
                            child.name, rel_dir, bundled=bundled
                        ),
                    }
                )

        safe_ac_name = re.sub(r"[^a-zA-Z0-9_-]", "_", ac_name)
        result.append(
            {
                "name": ac_name,
                "html_filename": f"artifact_{safe_ac_name}.html",
                "artifact_dir": rel_dir,
                "count": len(artifacts),
                "artifacts": artifacts,
                "work_orders": class_to_wos[ac_name],
            }
        )

    return result


def _collect_program_state(project_root: Path) -> list[dict[str, Any]]:
    """Collect program-state markdown documents for site rendering.

    Walks ``program-state/`` under *project_root* for ``.md`` files.
    Returns a sorted list of dicts with title, content, source_file,
    and html_filename.  Returns an empty list if the directory does not
    exist.
    """
    ps_dir = project_root / "program-state"
    if not ps_dir.is_dir():
        return []

    root_resolved = project_root.resolve()
    docs: list[dict[str, Any]] = []
    for md_file in sorted(ps_dir.iterdir()):
        if not md_file.is_file() or md_file.suffix != ".md":
            continue
        # Confine symlinks to project root
        if not md_file.resolve().is_relative_to(root_resolved):
            continue

        content = md_file.read_text(encoding="utf-8", errors="replace")

        # Extract title from first ``# `` heading; fallback to filename stem
        title = md_file.stem.replace("-", " ").replace("_", " ").title()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break

        source_file = f"program-state/{md_file.name}"
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", md_file.stem)
        html_filename = f"program_state_{safe_stem}.html"

        docs.append(
            {
                "title": title,
                "source_file": source_file,
                "content": content,
                "html_filename": html_filename,
            }
        )

    return docs


def _collect_executive(project_root: Path) -> dict[str, Any] | None:
    """Collect the executive summary document for site rendering.

    Reads ``executive/program-summary.md`` if it exists.  Returns a dict
    with ``content`` and ``title``, or *None* if the file is absent.
    """
    resolved = confine_path(project_root, Path("executive/program-summary.md"))
    if resolved is None or not resolved.is_file():
        return None

    content = resolved.read_text(encoding="utf-8", errors="replace")

    # Extract title from first ``# `` heading; fallback to "Executive Summary"
    title = "Executive Summary"
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break

    return {"content": content, "title": title}


def _collect_retrospectives(retro_dir: Path) -> list[dict[str, Any]]:
    """Collect retrospective markdown files from *retro_dir*.

    Walks the directory for ``.md`` files, extracts a title from the
    first ``# `` heading, and returns a sorted list of dicts with title,
    content, source_file, and html_filename.

    Symlinks are confined to *retro_dir* itself (not the project root).
    """
    retro_resolved = retro_dir.resolve()
    docs: list[dict[str, Any]] = []

    for md_file in sorted(retro_dir.rglob("*.md")):
        if not md_file.is_file():
            continue
        # Confine symlinks within the retrospectives directory
        if not md_file.resolve().is_relative_to(retro_resolved):
            continue

        content = md_file.read_text(encoding="utf-8", errors="replace")

        # Extract title from first ``# `` heading; fallback to filename stem
        title = md_file.stem.replace("-", " ").replace("_", " ").title()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break

        source_file = md_file.name
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", md_file.stem)
        html_filename = f"retrospective_{safe_stem}.html"

        docs.append(
            {
                "title": title,
                "content": content,
                "source_file": source_file,
                "html_filename": html_filename,
            }
        )

    # Sort by source_file for determinism
    docs.sort(key=lambda d: d["source_file"])
    return docs


def _collect_gates(project_root: Path) -> list[dict[str, Any]]:
    """Collect gate documents from ``gates/stage*/`` subdirectories.

    Returns a sorted list of gate dicts.  Returns an empty list when the
    ``gates/`` directory does not exist.
    """
    gates_dir = project_root / "gates"
    if not gates_dir.is_dir():
        return []

    root_resolved = project_root.resolve()
    gates: list[dict[str, Any]] = []

    for stage_dir in sorted(gates_dir.iterdir()):
        if not stage_dir.is_dir() or not stage_dir.name.startswith("stage"):
            continue
        # Confine the stage directory itself
        if not stage_dir.resolve().is_relative_to(root_resolved):
            continue

        stage_name = stage_dir.name
        for md_file in sorted(stage_dir.iterdir()):
            if not md_file.is_file() or md_file.suffix != ".md":
                continue
            # Confine each file
            if not md_file.resolve().is_relative_to(root_resolved):
                continue

            content = md_file.read_text(encoding="utf-8", errors="replace")

            # Extract title from first ``# `` heading; fallback to filename
            title = md_file.stem.replace("-", " ").replace("_", " ").title()
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break

            source_path = f"gates/{stage_name}/{md_file.name}"
            safe_name = re.sub(
                r"[^a-zA-Z0-9_-]",
                "_",
                f"{stage_name}_{md_file.stem}",
            )
            html_filename = f"gate_{safe_name}.html"

            gates.append(
                {
                    "stage_name": stage_name,
                    "title": title,
                    "content": content,
                    "source_path": source_path,
                    "html_filename": html_filename,
                }
            )

    # Sort by stage_name then source_path for determinism
    gates.sort(key=lambda g: (g["stage_name"], g["source_path"]))
    return gates


# ---------------------------------------------------------------------------
# Link validation
# ---------------------------------------------------------------------------


def _validate_links(
    project_root: Path,
    work_orders: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Validate internal links in Layer 1 deliverables.

    Returns a list of issue dicts for broken/escaped links.
    """
    issues: list[dict[str, str]] = []
    root_resolved = project_root.resolve()

    for wo in work_orders:
        deliverables_raw = wo.get("deliverables", {})
        if not isinstance(deliverables_raw, dict):
            continue
        deliverables = normalize_deliverables(deliverables_raw)
        layer_1 = deliverables.get("layer_1", [])
        if not isinstance(layer_1, list):
            continue

        for rel_path in sorted(layer_1):
            resolved = confine_path(project_root, Path(rel_path))
            if resolved is None:
                issues.append(
                    {
                        "file": str(rel_path),
                        "issue": "path escapes project root",
                    }
                )
                continue
            if not resolved.is_file():
                issues.append(
                    {
                        "file": str(rel_path),
                        "issue": "file does not exist",
                    }
                )
                continue

            content = resolved.read_text(encoding="utf-8", errors="replace")
            content_no_code = _strip_code(
                content
            )  # Strip code to avoid SMILES false positives
            for m in _MARKDOWN_LINK_RE.finditer(content_no_code):
                target = m.group(1).strip()
                # Skip external URLs
                if target.startswith("http://") or target.startswith("https://"):
                    continue
                # Strip fragment identifiers
                target_no_fragment = target.split("#")[0]
                if not target_no_fragment:
                    continue  # pure fragment link
                # Resolve relative to the file's directory
                link_resolved = (resolved.parent / target_no_fragment).resolve()
                if not link_resolved.is_relative_to(root_resolved):
                    issues.append(
                        {
                            "file": str(rel_path),
                            "link": target,
                            "issue": "link escapes project root",
                        }
                    )
                elif not link_resolved.exists():
                    issues.append(
                        {
                            "file": str(rel_path),
                            "link": target,
                            "issue": "broken link — target does not exist",
                        }
                    )

        # Also validate that declared layer_0_classes have artifacts
        layer_0_classes = deliverables.get("layer_0_classes", [])
        if isinstance(layer_0_classes, list):
            for ac in sorted(layer_0_classes):
                normalized = normalize_artifact_class(ac)
                rel_dir = ARTIFACT_DIRS.get(normalized)
                if rel_dir is None:
                    issues.append(
                        {
                            "artifact_class": ac,
                            "issue": f"unknown artifact class: {ac!r}",
                        }
                    )
                    continue
                art_dir = project_root / rel_dir
                if not art_dir.is_dir():
                    issues.append(
                        {
                            "artifact_class": ac,
                            "issue": f"artifact directory does not exist: {rel_dir}",
                        }
                    )

    return issues


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _build_nav_sections(
    program_state_docs: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    artifact_classes: list[dict[str, Any]],
    active_filename: str | None = None,
    executive: dict[str, Any] | None = None,
    gates: list[dict[str, Any]] | None = None,
    work_orders_with_pages: list[dict[str, Any]] | None = None,
    has_retrospectives: bool = False,
) -> list[dict[str, Any]]:
    """Build sidebar nav_sections with the current page marked active."""
    sections: list[dict[str, Any]] = []

    # Executive Summary at the top
    if executive is not None:
        sections.append(
            {
                "title": "Executive Summary",
                "links": [
                    {
                        "label": executive.get("title", "Executive Summary"),
                        "url": "executive.html",
                        "active": "executive.html" == active_filename,
                    },
                ],
            }
        )

    if program_state_docs:
        sections.append(
            {
                "title": "Program State",
                "links": [
                    {
                        "label": doc["title"],
                        "url": doc["html_filename"],
                        "active": doc["html_filename"] == active_filename,
                    }
                    for doc in program_state_docs
                ],
            }
        )

    # Findings grouped by discipline subdirectory
    if findings:
        # Parse discipline groups from source_path
        # e.g. "findings/structural-biology/analysis.md" → "structural-biology"
        grouped: dict[str, list[dict[str, Any]]] = {}
        ungrouped: list[dict[str, Any]] = []
        for f in findings:
            parts = Path(f["source_path"]).parts
            # If path is like findings/<discipline>/<file>.md, group by discipline
            if len(parts) >= 3 and parts[0] == "findings":
                discipline = parts[1]
                grouped.setdefault(discipline, []).append(f)
            else:
                ungrouped.append(f)

        # Build findings links: ungrouped first, then each discipline
        findings_links: list[dict[str, Any]] = []
        for f in ungrouped:
            findings_links.append(
                {
                    "label": f["title"],
                    "url": f["html_filename"],
                    "active": f["html_filename"] == active_filename,
                }
            )
        for discipline in sorted(grouped):
            display_name = discipline.replace("-", " ").replace("_", " ").title()
            findings_links.append(
                {
                    "label": f"— {display_name} —",
                    "url": "",
                    "active": False,
                    "is_group_header": True,
                }
            )
            for f in grouped[discipline]:
                findings_links.append(
                    {
                        "label": f["title"],
                        "url": f["html_filename"],
                        "active": f["html_filename"] == active_filename,
                    }
                )

        sections.append(
            {
                "title": "Findings",
                "links": findings_links,
            }
        )

    # Operations section — per-work-order pages
    if work_orders_with_pages:
        sections.append(
            {
                "title": "Operations",
                "links": [
                    {
                        "label": f"{wo['id']}-r{wo['revision']}",
                        "url": wo["html_filename"],
                        "active": wo["html_filename"] == active_filename,
                    }
                    for wo in work_orders_with_pages
                ],
            }
        )

    # Gates section
    if gates:
        sections.append(
            {
                "title": "Gates",
                "links": [
                    {
                        "label": g["title"],
                        "url": g["html_filename"],
                        "active": g["html_filename"] == active_filename,
                    }
                    for g in gates
                ],
            }
        )

    if artifact_classes:
        sections.append(
            {
                "title": "Raw Data",
                "links": [
                    {
                        "label": ac["name"],
                        "url": ac["html_filename"],
                        "active": ac["html_filename"] == active_filename,
                    }
                    for ac in artifact_classes
                ],
            }
        )

    # Retrospectives section — only if retrospectives exist
    if has_retrospectives:
        sections.append(
            {
                "title": "Retrospectives",
                "links": [
                    {
                        "label": "Retrospectives",
                        "url": "retrospectives.html",
                        "active": "retrospectives.html" == active_filename,
                    },
                ],
            }
        )

    return sections


def _enrich_findings_with_viewers(
    findings: list[dict[str, Any]],
    artifact_classes: list[dict[str, Any]],
) -> None:
    """Cross-reference finding artifact_classes with full artifact data.

    For each finding, adds a ``viewer_artifacts`` list containing the
    artifacts (with viewer URLs) from each matching artifact class.
    Mutates the finding dicts in place.
    """
    ac_by_name: dict[str, dict[str, Any]] = {ac["name"]: ac for ac in artifact_classes}
    for finding in findings:
        viewer_artifacts: list[dict[str, Any]] = []
        for ac_link in finding.get("artifact_classes", []):
            ac_data = ac_by_name.get(ac_link["name"])
            if ac_data is None:
                continue
            for artifact in ac_data.get("artifacts", []):
                if artifact.get("viewer_url"):
                    viewer_artifacts.append(
                        {
                            "name": artifact["name"],
                            "viewer_url": artifact["viewer_url"],
                            "artifact_class": ac_link["name"],
                        }
                    )
        finding["viewer_artifacts"] = viewer_artifacts


def _strip_leading_h1(text: str) -> str:
    """Strip the first markdown H1 (``# Title``) to avoid duplication with template H1.

    The Jinja2 templates already supply ``<h1>{{ title }}</h1>`` from
    page metadata.  The markdown source typically starts with ``# Title``
    which would produce a second ``<h1>`` after rendering.  Stripping the
    markdown H1 before rendering eliminates the duplicate.

    Only the *first* line matching ``^# `` (single hash + space) is
    removed; deeper headings (``##``, ``###``, …) are left untouched.
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^# ", line):
            lines.pop(i)
            return "\n".join(lines)
    return text


def _slugify_heading(text: str) -> str:
    """Slugify heading text for use as an HTML ``id`` attribute.

    Lowercases, replaces whitespace runs with single hyphens, and strips
    everything that isn't alphanumeric or a hyphen.

    Example::

        >>> _slugify_heading("Decision DEC-003")
        'decision-dec-003'
    """
    slug = text.lower()
    # Strip non-alphanumeric characters except spaces and hyphens
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    # Collapse whitespace to single hyphens
    slug = re.sub(r"[\s]+", "-", slug.strip())
    # Collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug)
    return slug


_HEADING_TAG_RE = re.compile(r"<(h[1-6])(\s[^>]*)?>(.+?)</\1>", re.DOTALL)


def _add_heading_ids(html: str) -> str:
    """Add ``id`` attributes to ``<h1>``-``<h6>`` elements.

    Generates stable, slugified IDs from heading text content.
    Duplicate heading text receives ``-1``, ``-2``, … suffixes to keep
    IDs unique within the page.

    Headings that already carry an ``id=`` attribute are left unchanged.
    """
    seen: dict[str, int] = {}

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        tag = match.group(1)  # e.g. "h2"
        attrs = match.group(2)  # existing attributes or None
        content = match.group(3)  # inner HTML

        # Don't overwrite an existing id
        if attrs and "id=" in attrs:
            return match.group(0)

        # Strip inner HTML tags to get plain text for the slug
        plain = re.sub(r"<[^>]+>", "", content)
        slug = _slugify_heading(plain)

        if not slug:
            return match.group(0)

        # De-duplicate
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 0

        attrs_str = attrs if attrs else ""
        return f'<{tag} id="{slug}"{attrs_str}>{content}</{tag}>'

    return _HEADING_TAG_RE.sub(_replace, html)


_MD_HREF_RE = re.compile(r'href="([^"]*\.md(?:#[^"]*)?)"')


def _rewrite_md_links(html: str) -> str:
    """Rewrite internal ``.md`` links to ``.html`` in rendered HTML.

    Only relative links are rewritten — external URLs (``http://``,
    ``https://``, ``//``) are left unchanged.  Fragment identifiers
    (``#section``) are preserved across the rewrite.

    Examples::

        href="other-page.md"            → href="other-page.html"
        href="other-page.md#section"    → href="other-page.html#section"
        href="../dir/page.md"           → href="../dir/page.html"
        href="https://example.com/f.md" → (unchanged)
    """

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        url = match.group(1)
        # Skip external URLs
        if url.startswith(("http://", "https://", "//")):
            return match.group(0)
        # Split off fragment
        if "#" in url:
            path, fragment = url.split("#", 1)
            fragment = "#" + fragment
        else:
            path = url
            fragment = ""
        # Rewrite .md → .html
        if path.endswith(".md"):
            path = path[:-3] + ".html"
        return f'href="{path}{fragment}"'

    return _MD_HREF_RE.sub(_replace, html)


# ---------------------------------------------------------------------------
# External URL sanitization (#170) — defense-in-depth Layer 1
# ---------------------------------------------------------------------------

# WHATWG URL Standard §4.2 — characters to strip for safe URL comparison.
# Step 1: leading C0 control characters (U+0000-U+001F) and space (U+0020).
_WHATWG_C0_SPACE_RE = re.compile(r"^[\x00-\x20]+")
# Step 3: embedded tab (\t), newline (\n), and carriage return (\r).
_WHATWG_TAB_NL_RE = re.compile(r"[\x09\x0a\x0d]")


def _whatwg_normalize_url(url: str) -> str:
    """Normalize a URL per WHATWG URL Standard §4.2 for safe comparison.

    Step 1: Strip leading C0 control characters (U+0000-U+001F) and space.
    Step 3: Remove embedded tab (``\\t``), newline (``\\n``), and CR (``\\r``).

    This prevents bypasses where browsers strip these characters but Python's
    ``str.lstrip()`` / ``str.strip()`` only strip whitespace.
    """
    cleaned = _WHATWG_C0_SPACE_RE.sub("", url)
    cleaned = _WHATWG_TAB_NL_RE.sub("", cleaned)
    return cleaned


# Match <img ...> tags
_IMG_TAG_RE = re.compile(r"<img\s+[^>]*>", re.IGNORECASE)
# Extract src="..." from an img tag
_IMG_SRC_RE = re.compile(r'src="([^"]*)"', re.IGNORECASE)

# Match <a href="..."> tags
_A_TAG_RE = re.compile(r"<a\s+([^>]*)>", re.IGNORECASE)
# Extract href="..." from an a tag
_A_HREF_RE = re.compile(r'href="([^"]*)"', re.IGNORECASE)

_DANGEROUS_SCHEMES = ("javascript:", "data:", "vbscript:", "blob:")


def _is_external_url(url: str) -> bool:
    """Return True if *url* is external (has a scheme or is protocol-relative).

    Uses WHATWG normalization (C0 control char stripping) and
    ``urllib.parse.urlsplit`` for robust scheme detection.
    """
    from urllib.parse import urlsplit

    normalized = _whatwg_normalize_url(url)
    # Normalize backslashes → forward slashes (WHATWG URL Standard §4.2).
    normalized = normalized.replace("\\", "/")
    # Protocol-relative URLs: //host/…
    if normalized.startswith("//"):
        return True
    parsed = urlsplit(normalized)
    # Any non-empty scheme means the URL is absolute / external.
    return bool(parsed.scheme)


# Module-level state for security warning aggregation across render calls.
# Cleared at the start of each _render_site() invocation.
_security_warnings: list[str] = []
_current_source_path: str = "<unknown>"


def _sanitize_external_urls(html: str) -> tuple[str, list[str]]:
    """Neutralize external URLs in rendered HTML.

    Designed for mistune's HTML output format (double-quoted attributes,
    no srcset). If reused for arbitrary HTML from other sources, additional
    attribute quoting patterns would need to be handled.

    - External ``<img src>`` tags → visible placeholder
    - ``data:`` URI ``<img>`` tags → visible placeholder
    - Dangerous-scheme ``<a href>`` → href removed
    - External ``<a href>`` → rel/target safety attributes added

    Returns ``(sanitized_html, list_of_warning_strings)``.
    """
    from html import escape as html_escape

    warnings: list[str] = []

    def _replace_img(match: re.Match) -> str:  # type: ignore[type-arg]
        tag = match.group(0)
        src_match = _IMG_SRC_RE.search(tag)
        if not src_match:
            return tag
        url = src_match.group(1)
        if _is_external_url(url):
            safe_url = html_escape(url, quote=True)
            warnings.append(f"blocked external image: {url}")
            return (
                f'<span class="blocked-image" '
                f'title="External image blocked: {safe_url}">'
                f"[external image removed — {safe_url}]</span>"
            )
        if _whatwg_normalize_url(url.lower()).startswith(("data:",)):
            warnings.append(f"blocked data URI image: {url[:80]}")
            return '<span class="blocked-image">[data URI image removed]</span>'
        return tag  # relative/internal images are fine

    def _replace_a(match: re.Match) -> str:  # type: ignore[type-arg]
        attrs = match.group(1)
        href_match = _A_HREF_RE.search(attrs)
        if not href_match:
            return match.group(0)
        url = href_match.group(1)
        if _whatwg_normalize_url(url.lower()).startswith(_DANGEROUS_SCHEMES):
            warnings.append(f"blocked dangerous link scheme: {url[:80]}")
            new_attrs = _A_HREF_RE.sub('href="#"', attrs)
            return f'<a {new_attrs} class="blocked-link">'
        if _is_external_url(url):
            if "rel=" not in attrs.lower():
                attrs += ' rel="nofollow noopener noreferrer"'
            if "target=" not in attrs.lower():
                attrs += ' target="_blank"'
            return f"<a {attrs}>"
        return match.group(0)

    html = _IMG_TAG_RE.sub(_replace_img, html)
    html = _A_TAG_RE.sub(_replace_a, html)
    return html, warnings


def _dedent_tables(text: str) -> str:
    """Dedent pipe tables that are indented inside list items.

    Mistune's table plugin only recognises tables starting at column 0.
    This preprocessor finds contiguous blocks of lines starting with
    optional whitespace followed by a pipe character, and strips the
    common leading whitespace so the table parser can recognise them.
    """
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        # Detect start of a potential indented table block
        if re.match(r"^\s+\|", lines[i]):
            # Collect contiguous indented-pipe lines
            block = []
            while i < len(lines) and re.match(r"^\s+\|", lines[i]):
                block.append(lines[i])
                i += 1
            # Only dedent if it looks like a table (has separator row)
            if len(block) >= 2 and re.search(r"[-:]+\s*\|", block[1]):
                # Find common leading whitespace
                leading = min(len(line) - len(line.lstrip()) for line in block)
                result.extend(line[leading:] for line in block)
            else:
                result.extend(block)
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _render_site(
    project_root: Path,
    output_dir: Path,
    work_orders: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    artifact_classes: list[dict[str, Any]],
    program_state_docs: list[dict[str, Any]] | None = None,
    executive: dict[str, Any] | None = None,
    gates: list[dict[str, Any]] | None = None,
    retrospectives: list[dict[str, Any]] | None = None,
) -> int:
    """Render all pages into *output_dir* using Jinja2. Returns page count."""
    global _security_warnings, _current_source_path
    _security_warnings = []
    _current_source_path = "<unknown>"

    import mistune
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    if program_state_docs is None:
        program_state_docs = []
    if gates is None:
        gates = []

    # Build work-order page metadata with html_filename for each accepted WO
    work_orders_with_pages: list[dict[str, Any]] = []
    for wo in work_orders:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", wo.get("id", ""))
        html_filename = f"workorder_{safe_id}_r{wo.get('revision', 0)}.html"
        work_orders_with_pages.append({**wo, "html_filename": html_filename})

    has_retrospectives = bool(retrospectives)

    # Enrich findings with viewer link data before rendering
    _enrich_findings_with_viewers(findings, artifact_classes)

    template_dir = Path(__file__).resolve().parent.parent / "site_templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    from markupsafe import Markup

    _md = mistune.create_markdown(
        escape=True, plugins=["table", "strikethrough", "math"]
    )

    def _render_markdown(text: str) -> Markup:
        """Render markdown with H1 de-dup, heading IDs, .md link rewriting, and URL sanitization."""
        text = _strip_leading_h1(text)
        text = _dedent_tables(text)
        html = _md(text)
        html = _add_heading_ids(html)
        html = _rewrite_md_links(html)
        html, warnings = _sanitize_external_urls(html)
        for w in warnings:
            _security_warnings.append(f"{_current_source_path}: {w}")
        return Markup(html)

    env.filters["markdown"] = _render_markdown

    # Copy viewers into output
    viewers_src = template_dir / "viewers"
    if viewers_src.is_dir():
        viewers_dst = output_dir / "viewers"
        shutil.copytree(str(viewers_src), str(viewers_dst))

    # Copy KaTeX assets for math rendering.
    # KaTeX is npm-installed at provision time (install.sh); the dist/
    # directory under the npm package contains the files the site needs.
    # The generated site remains fully self-contained — no CDN dependency.
    tools_home = os.environ.get("DDE_TOOLS_HOME", "/scion-volumes/tools")
    katex_npm = Path(tools_home) / "npm" / "node_modules" / "katex" / "dist"
    if not katex_npm.is_dir():
        raise ArtifactError(
            "KaTeX not installed",
            detail=f"expected KaTeX at {katex_npm}",
            remedy="Run install.sh to provision npm dependencies.",
        )
    katex_dst = output_dir / "katex"
    katex_dst.mkdir()
    # Copy the specific assets the site templates reference:
    #   katex.min.css, katex.min.js, contrib/auto-render.min.js, fonts/
    shutil.copy2(str(katex_npm / "katex.min.css"), str(katex_dst / "katex.min.css"))
    shutil.copy2(str(katex_npm / "katex.min.js"), str(katex_dst / "katex.min.js"))
    contrib_dst = katex_dst / "contrib"
    contrib_dst.mkdir()
    shutil.copy2(
        str(katex_npm / "contrib" / "auto-render.min.js"),
        str(contrib_dst / "auto-render.min.js"),
    )
    shutil.copytree(str(katex_npm / "fonts"), str(katex_dst / "fonts"))

    # Helper for nav_sections kwargs shared across all pages
    def _nav(active: str | None = None) -> list[dict[str, Any]]:
        return _build_nav_sections(
            program_state_docs,
            findings,
            artifact_classes,
            active_filename=active,
            executive=executive,
            gates=gates,
            work_orders_with_pages=work_orders_with_pages,
            has_retrospectives=has_retrospectives,
        )

    pages_rendered = 0

    # --- Index page ---
    index_tmpl = env.get_template("index.html")
    # Build executive excerpt for the landing page
    executive_content = None
    if executive is not None:
        raw = executive["content"]
        # Extract first paragraph (up to first blank line) or ~200 chars
        paragraphs = raw.split("\n\n")
        # Skip the title line if present
        excerpt_parts = []
        chars = 0
        for para in paragraphs:
            stripped = para.strip()
            if not stripped:
                continue
            # Skip heading lines for excerpt
            if stripped.startswith("# "):
                continue
            excerpt_parts.append(stripped)
            chars += len(stripped)
            if chars >= 200:
                break
        executive_content = "\n\n".join(excerpt_parts)

    _current_source_path = (
        executive.get("source_file", "executive") if executive else "<unknown>"
    )
    index_html = index_tmpl.render(
        cli_version=CLI_VERSION,
        work_orders=work_orders,
        findings=findings,
        artifact_classes=artifact_classes,
        program_state_docs=program_state_docs,
        executive_content=executive_content,
        executive=executive,
        gates=gates,
        work_order_pages=work_orders_with_pages,
        has_retrospectives=has_retrospectives,
        accepted_revisions=[f"{wo['id']}-r{wo['revision']}" for wo in work_orders],
        pages_rendered=0,  # placeholder, updated after all rendering
        nav_sections=_nav("index.html"),
    )
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    pages_rendered += 1

    # --- Executive page ---
    if executive is not None:
        _current_source_path = executive.get("source_file", "executive")
        exec_tmpl = env.get_template("executive.html")
        html = exec_tmpl.render(
            cli_version=CLI_VERSION,
            content=executive["content"],
            title=executive["title"],
            nav_sections=_nav("executive.html"),
        )
        (output_dir / "executive.html").write_text(html, encoding="utf-8")
        pages_rendered += 1

    # --- Gate pages ---
    if gates:
        gate_tmpl = env.get_template("gate.html")
        for gate in gates:
            _current_source_path = gate.get(
                "source_file", gate.get("html_filename", "<unknown>")
            )
            html = gate_tmpl.render(
                cli_version=CLI_VERSION,
                nav_sections=_nav(gate["html_filename"]),
                **gate,
            )
            (output_dir / gate["html_filename"]).write_text(html, encoding="utf-8")
            pages_rendered += 1

    # --- Finding pages ---
    finding_tmpl = env.get_template("finding.html")
    for finding in findings:
        _current_source_path = finding.get(
            "source_file", finding.get("html_filename", "<unknown>")
        )
        html = finding_tmpl.render(
            cli_version=CLI_VERSION,
            nav_sections=_nav(finding["html_filename"]),
            **finding,
        )
        (output_dir / finding["html_filename"]).write_text(html, encoding="utf-8")
        pages_rendered += 1

    # --- Artifact class pages ---
    artifact_tmpl = env.get_template("artifact.html")
    for ac in artifact_classes:
        html = artifact_tmpl.render(
            cli_version=CLI_VERSION,
            artifact_class=ac["name"],
            artifact_dir=ac["artifact_dir"],
            artifacts=ac["artifacts"],
            work_orders=ac["work_orders"],
            nav_sections=_nav(ac["html_filename"]),
        )
        (output_dir / ac["html_filename"]).write_text(html, encoding="utf-8")
        pages_rendered += 1

    # --- Program state pages ---
    ps_tmpl = env.get_template("program_state.html")
    for doc in program_state_docs:
        _current_source_path = doc.get(
            "source_file", doc.get("html_filename", "<unknown>")
        )
        html = ps_tmpl.render(
            cli_version=CLI_VERSION,
            title=doc["title"],
            content=doc["content"],
            source_file=doc["source_file"],
            nav_sections=_nav(doc["html_filename"]),
        )
        (output_dir / doc["html_filename"]).write_text(html, encoding="utf-8")
        pages_rendered += 1

    # --- Work order pages ---
    wo_tmpl = env.get_template("workorder.html")
    for wo_page in work_orders_with_pages:
        # Cross-reference: findings produced by this WO
        linked_findings = [
            f for f in findings if f.get("work_order_id") == wo_page.get("id")
        ]
        # Cross-reference: artifact classes referencing this WO
        linked_artifact_classes = [
            ac
            for ac in artifact_classes
            if any(w.get("id") == wo_page.get("id") for w in ac.get("work_orders", []))
        ]
        html = wo_tmpl.render(
            cli_version=CLI_VERSION,
            wo=wo_page,
            linked_findings=linked_findings,
            linked_artifact_classes=linked_artifact_classes,
            nav_sections=_nav(wo_page["html_filename"]),
        )
        (output_dir / wo_page["html_filename"]).write_text(html, encoding="utf-8")
        pages_rendered += 1

    # --- Retrospectives page ---
    if retrospectives:
        _current_source_path = "retrospectives"
        retro_tmpl = env.get_template("retrospectives.html")
        html = retro_tmpl.render(
            cli_version=CLI_VERSION,
            retrospectives=retrospectives,
            nav_sections=_nav("retrospectives.html"),
        )
        (output_dir / "retrospectives.html").write_text(html, encoding="utf-8")
        pages_rendered += 1

    # Re-render index with actual page count for determinism
    _current_source_path = (
        executive.get("source_file", "executive") if executive else "<unknown>"
    )
    index_html = index_tmpl.render(
        cli_version=CLI_VERSION,
        work_orders=work_orders,
        findings=findings,
        artifact_classes=artifact_classes,
        program_state_docs=program_state_docs,
        executive_content=executive_content,
        executive=executive,
        gates=gates,
        work_order_pages=work_orders_with_pages,
        has_retrospectives=has_retrospectives,
        accepted_revisions=[f"{wo['id']}-r{wo['revision']}" for wo in work_orders],
        pages_rendered=pages_rendered,
        nav_sections=_nav("index.html"),
    )
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")

    return pages_rendered


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def site() -> None:
    """Deterministic site build from accepted work-order deliverables."""


# ---------------------------------------------------------------------------
# Prerequisite check — all WOs must be accepted before site build
# ---------------------------------------------------------------------------


def _check_all_accepted(project_root: Path, emit: Any) -> None:
    """Verify every work order is in ``scientifically_accepted`` state.

    Lists all work orders (latest revision per ID), checks each state,
    and raises ``ArtifactError`` with a status table if any are not yet
    accepted.  Called by ``build_cmd`` before starting the build.
    """
    all_records = controlstore.list_records(project_root, "work-order")
    if not all_records:
        return  # No WOs at all — the existing "no accepted WOs" guard handles this.

    # Deduplicate to latest revision per ID.
    by_id: dict[str, dict[str, Any]] = {}
    for r in all_records:
        wo_id = r.get("id", "")
        existing = by_id.get(wo_id)
        if existing is None or r.get("revision", 0) > existing.get("revision", 0):
            by_id[wo_id] = r
    latest = sorted(by_id.values(), key=lambda r: r.get("id", ""))

    not_accepted: list[dict[str, Any]] = []
    for wo in latest:
        if wo.get("state") != "scientifically_accepted":
            not_accepted.append(wo)

    if not not_accepted:
        return  # All accepted — proceed with build.

    # Build status table.
    lines = ["Work order status:"]
    for wo in latest:
        wo_id = wo.get("id", "?")
        wo_state = wo.get("state", "?")
        if wo_state == "scientifically_accepted":
            mark = "✓"
            note = ""
        else:
            mark = "✗"
            if wo_state == "submitted":
                note = " (needs acceptance)"
            elif wo_state in ("draft", "proposed"):
                note = " (needs submission + acceptance)"
            elif wo_state == "committed":
                note = " (needs acceptance)"
            elif wo_state == "validation_failed":
                note = " (needs override or fix)"
            elif wo_state == "mechanically_validated":
                note = " (needs scientific acceptance)"
            else:
                note = f" (state: {wo_state})"
        lines.append(f"  {wo_id:<10} {wo_state:<26} {mark}{note}")

    lines.append("")
    lines.append(
        f"{len(not_accepted)} work order(s) not yet accepted. "
        f"Run `dde workorder accept` on each,\n"
        f"or use `dde workorder accept-all` to validate and accept "
        f"all passing WOs."
    )

    raise ArtifactError(
        f"site build blocked: {len(not_accepted)} work order(s) not in "
        f"scientifically_accepted state",
        detail="\n".join(lines),
        remedy=(
            "run `dde workorder accept` on each work order, "
            "or use `dde workorder accept-all` to batch-accept"
        ),
    )


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@site.command("build")
@click.option(
    "--output-dir",
    default="_site",
    help="Output directory for the built site (default: _site).",
)
@click.option(
    "--retrospectives-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help="Directory containing retrospective markdown files.",
)
@click.option(
    "--no-bundle-raw",
    is_flag=True,
    default=False,
    help="Do not copy raw/ artifacts into the site output.",
)
@output_options
@pass_state
def build_cmd(
    state: AppState,
    output_dir: str,
    retrospectives_dir: str | None,
    no_bundle_raw: bool,
    as_json: bool,
    quiet: bool,
) -> None:
    """Build a static site from accepted work-order deliverables.

    All work orders must be in ``scientifically_accepted`` state before
    the build can proceed.  Run ``dde workorder accept`` on each, or
    use ``dde workorder accept-all`` to validate and accept all passing
    WOs in one pass.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    # 0. Prerequisite check — all WOs must be scientifically_accepted.
    _check_all_accepted(project.root, emit)

    # 1. Collect accepted work orders
    work_orders = _collect_accepted_work_orders(project.root)
    if not work_orders:
        raise ArtifactError(
            "no scientifically_accepted work orders found",
            detail="the site build requires at least one accepted revision",
            remedy="accept a work order with `dde workorder accept` first",
        )

    # 2. Collect findings, artifact classes, program state, executive, gates, and retrospectives
    bundled = not no_bundle_raw
    findings = _collect_findings(project.root, work_orders)
    artifact_classes = _collect_artifact_classes(
        project.root, work_orders, bundled=bundled
    )
    program_state_docs = _collect_program_state(project.root)
    executive = _collect_executive(project.root)
    gates = _collect_gates(project.root)

    # Retrospectives — only collected when explicitly opted-in via CLI flag
    retrospectives: list[dict[str, Any]] | None = None
    if retrospectives_dir is not None:
        retrospectives = _collect_retrospectives(Path(retrospectives_dir))

    # 3. Validate links — abort if any are broken
    issues = _validate_links(project.root, work_orders)
    if issues:
        detail_parts = []
        for issue in issues:
            parts = []
            for k, v in sorted(issue.items()):
                parts.append(f"{k}={v}")
            detail_parts.append("; ".join(parts))
        raise ArtifactError(
            f"site build aborted: {len(issues)} broken link(s) or missing reference(s)",
            detail="\n".join(detail_parts),
            remedy="fix the broken links and re-run `dde site build`",
        )

    # 4. Resolve output directory (relative to project root)
    out_path = Path(output_dir)
    if not out_path.is_absolute():
        out_path = project.root / out_path
    # Confine output to project root
    out_resolved = out_path.resolve()
    project_resolved = project.root.resolve()
    if not out_resolved.is_relative_to(project_resolved):
        raise ArtifactError(
            f"output directory escapes project root: {output_dir}",
            detail=f"resolved to {out_resolved}",
            remedy="use a path within the project directory",
        )
    # Guard: output dir must not BE the project root itself (#271).
    # confine_path passes because a path is relative to itself, but
    # the atomic-swap logic would rename the entire project root away.
    if out_resolved == project_resolved:
        raise Refusal(
            "--output-dir must not be the project root itself",
            detail=f"resolved output directory {out_resolved} equals project root",
            remedy="use a subdirectory such as '_site'",
        )
    # Guard: output dir must not be a parent of .dde/ (the control store).
    dde_dir = (project_resolved / ".dde").resolve()
    if dde_dir.is_relative_to(out_resolved) and out_resolved != project_resolved:
        raise Refusal(
            "--output-dir must not contain the .dde control directory",
            detail=f"resolved output directory {out_resolved} is a parent of {dde_dir}",
            remedy="use a subdirectory that does not contain .dde/",
        )

    # 5. Atomic write: render to temp dir, then move into place
    parent_dir = out_path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=parent_dir, prefix=".dde-site-"))

    try:
        pages_rendered = _render_site(
            project.root,
            tmp_dir,
            work_orders,
            findings,
            artifact_classes,
            program_state_docs=program_state_docs,
            executive=executive,
            gates=gates,
            retrospectives=retrospectives,
        )
    except Exception:
        # Clean up temp dir on any failure
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    # Check for security warnings from URL sanitization (#170)
    if _security_warnings:
        # Clean up temp dir — build is refused
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Deduplicate warnings (index re-render can produce duplicates)
        unique_warnings = list(dict.fromkeys(_security_warnings))
        detail_lines = []
        for w in unique_warnings:
            detail_lines.append(f"  • {w}")
        raise Refusal(
            f"site build refused: {len(unique_warnings)} external URL(s) "
            f"blocked in rendered content",
            detail="\n".join(detail_lines),
            remedy=(
                "Remove external image URLs from the source markdown files "
                "listed above. If these are legitimate references, convert "
                "them to text citations."
            ),
        )

    # Move into final location atomically
    trash_dir = None
    if out_path.exists():
        trash_dir = Path(tempfile.mkdtemp(dir=parent_dir, prefix=".dde-site-old-"))
        shutil.rmtree(trash_dir)  # mkdtemp creates it; we need just the name
        out_path.rename(trash_dir)
    try:
        shutil.move(str(tmp_dir), str(out_path))
    except Exception:
        # Restore old site if move failed
        if trash_dir is not None and trash_dir.exists():
            trash_dir.rename(out_path)
        raise
    # Clean up old site only after new is safely in place
    if trash_dir is not None and trash_dir.exists():
        shutil.rmtree(trash_dir, ignore_errors=True)

    # 5b. Bundle raw/ artifacts into the site output for self-containment
    if bundled:
        raw_src = project.root / "raw"
        if raw_src.is_dir():
            raw_dst = out_path / "raw"
            if raw_dst.exists():
                shutil.rmtree(raw_dst)
            shutil.copytree(
                raw_src,
                raw_dst,
                symlinks=True,
                ignore=shutil.ignore_patterns(
                    ".*",
                    "__pycache__",
                    "*.pyc",
                    ".DS_Store",
                ),
            )

    # 6. Record build in publish-state.json
    accepted_revisions = [f"{wo['id']}-r{wo['revision']}" for wo in work_orders]
    publish_state = {
        "last_build_at": _deterministic_build_id(work_orders),
        "accepted_revisions": accepted_revisions,
        "output_dir": str(output_dir),
        "cli_version": CLI_VERSION,
        "pages_rendered": pages_rendered,
    }
    controlstore.write_publish_state(project.root, publish_state)

    # 7. Append event
    controlstore.append_event(
        project.root,
        {
            "type": "site.published",
            "subject_id": None,
            "revision": None,
            "from_state": None,
            "to_state": None,
            "detail": {
                "pages_rendered": pages_rendered,
                "output_dir": str(output_dir),
                "accepted_revisions": accepted_revisions,
            },
        },
    )

    # 8. Output
    if as_json:
        emit.data("pages_rendered", pages_rendered)
        emit.data("output_dir", str(out_path))
        emit.data("accepted_revisions", accepted_revisions)
        emit.data("cli_version", CLI_VERSION)
        emit.path(out_path, role="site_output")
        emit.flush()
        return

    if quiet:
        emit.path(out_path, role="site_output")
        emit.flush()
        return

    emit.line(f"Site built: {pages_rendered} page(s) in {out_path}")
    emit.line(f"  Accepted revisions: {', '.join(accepted_revisions)}")
    emit.line(f"  Findings: {len(findings)}")
    emit.line(f"  Artifact classes: {len(artifact_classes)}")
    emit.path(out_path, role="site_output")
    emit.flush()


def _deterministic_build_id(work_orders: list[dict[str, Any]]) -> str:
    """Derive a build identifier from the accepted work orders.

    Uses the latest ``committed_at`` or ``created_at`` timestamp from the
    accepted work orders.  Falls back to an empty string if none found.
    This avoids wall-clock timestamps in the publish state, keeping builds
    deterministic given the same input.
    """
    timestamps: list[str] = []
    for wo in work_orders:
        for field in ("committed_at", "created_at"):
            ts = wo.get(field)
            if isinstance(ts, str) and ts:
                timestamps.append(ts)
    return max(timestamps) if timestamps else ""


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@site.command("validate")
@output_options
@pass_state
def validate_cmd(
    state: AppState,
    as_json: bool,
    quiet: bool,
) -> None:
    """Validate links and references without rendering the site."""
    emit = emitter(as_json, quiet)
    project = state.project()

    # 1. Collect accepted work orders
    work_orders = _collect_accepted_work_orders(project.root)
    if not work_orders:
        raise ArtifactError(
            "no scientifically_accepted work orders found",
            detail="site validation requires at least one accepted revision",
            remedy="accept a work order with `dde workorder accept` first",
        )

    # 2. Collect findings for reporting
    findings = _collect_findings(project.root, work_orders)
    artifact_classes = _collect_artifact_classes(project.root, work_orders)

    # 3. Validate links
    issues = _validate_links(project.root, work_orders)

    accepted_revisions = [f"{wo['id']}-r{wo['revision']}" for wo in work_orders]

    # 4. Output
    if as_json:
        emit.data("accepted_revisions", accepted_revisions)
        emit.data("findings_count", len(findings))
        emit.data("artifact_classes_count", len(artifact_classes))
        emit.data("issues", issues)
        emit.data("valid", len(issues) == 0)
        emit.flush()
    elif quiet:
        if issues:
            for issue in issues:
                parts = []
                for k, v in sorted(issue.items()):
                    parts.append(f"{k}={v}")
                emit.line("; ".join(parts))
        emit.flush()
    else:
        emit.line(f"Site validation: {len(work_orders)} accepted revision(s)")
        emit.line(f"  Revisions: {', '.join(accepted_revisions)}")
        emit.line(f"  Findings: {len(findings)}")
        emit.line(f"  Artifact classes: {len(artifact_classes)}")
        emit.line("")
        if issues:
            emit.line(f"Issues ({len(issues)}):")
            for issue in issues:
                parts = []
                for k, v in sorted(issue.items()):
                    parts.append(f"{k}={v}")
                emit.line(f"  {'; '.join(parts)}")
        else:
            emit.line("No issues found — site is ready to build.")
        emit.flush()

    # Raise AFTER output in all modes
    if issues:
        raise ArtifactError(
            f"site validation failed: {len(issues)} issue(s) found",
            remedy="fix the issues and re-run `dde site validate`",
        )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@site.command("export")
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output zip file path (default: <project-slug>-site.zip).",
)
@click.option(
    "--site-dir",
    default="_site",
    help="Site directory to bundle (default: _site).",
)
@pass_state
def export_cmd(state: AppState, output: str | None, site_dir: str) -> None:
    """Create a relocatable zip archive of the built site.

    Packages the site output directory into a standalone zip archive
    with index.html at the root.  Run ``dde site build`` first to
    generate the site.
    """
    project = state.project()
    project_root = project.root
    site_path = confine_path(project_root, Path(site_dir))
    if site_path is None:
        raise UsageError(
            f"site directory escapes project root: {site_dir}",
            remedy="provide a site directory within the project root",
        )

    if not site_path.is_dir():
        raise UsageError(
            f"site directory {site_dir}/ does not exist",
            remedy="run `dde site build` first",
        )

    # Validate index.html exists
    if not (site_path / "index.html").is_file():
        raise UsageError(
            "no index.html found in site directory",
            remedy="run `dde site build` to generate the site",
        )

    # Validate raw/ is bundled
    if not (site_path / "raw").is_dir():
        click.echo(
            "Warning: raw/ not bundled in site. Viewers may not work standalone.",
            err=True,
        )

    # Default output filename
    if output is None:
        slug = project_root.name or "dde-site"
        output = str(project_root / f"{slug}-site.zip")

    out_path = Path(output)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root_dir, dirs, files in os.walk(site_path):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                full = Path(root_dir) / f
                arcname = full.relative_to(site_path)
                zf.write(full, arcname)

    click.echo(f"Site exported to {out_path} ({out_path.stat().st_size} bytes)")
