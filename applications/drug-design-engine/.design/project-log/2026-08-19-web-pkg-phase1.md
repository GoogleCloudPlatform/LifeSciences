# Phase 1: Vertical Slice — Program State + Sidebar + Markdown

**Date:** 2026-08-19
**Agent:** web-pkg-dev-p1
**Branch:** scion/web-pkg-p1
**Issue:** #129

## Summary

Implemented Phase 1 of the web package vertical slice, proving the full
pipeline end-to-end: data collection from `program-state/*.md` files,
markdown rendering via mistune, a new template, sidebar navigation replacing
the top-bar nav, and breadcrumbs on all page types.

## Changes

### New dependency
- Added `mistune>=3.0` to `tools/requirements.txt` for markdown-to-HTML rendering.

### New function: `_collect_program_state()`
- Walks `program-state/` directory for `.md` files.
- Extracts title from first `# ` heading (fallback: filename stem titlecased).
- Returns sorted list of dicts with title, source_file, content, html_filename.
- Gracefully returns empty list if `program-state/` does not exist.

### New helper: `_build_nav_sections()`
- Builds sidebar navigation data structure from program state docs, findings,
  and artifact classes.
- Accepts `active_filename` to mark the current page in the sidebar.
- Sections are omitted when their data source is empty.

### Modified `_render_site()`
- Accepts `program_state_docs` parameter (default `None` for backward compat).
- Registers `markdown` Jinja2 filter using `mistune.html` wrapped in `Markup`
  for autoescape compatibility.
- Builds `nav_sections` per page render with active state highlighting.
- Passes `nav_sections` and `program_state_docs` to all template renders.
- Renders program state pages using new `program_state.html` template.

### Modified `build_cmd()`
- Calls `_collect_program_state(project.root)` after existing collections.
- Passes `program_state_docs` to `_render_site()`.

### Template changes
- **base.html**: Replaced top-bar `.layer-nav` with flexbox sidebar layout.
  Sidebar renders `nav_sections` as collapsible `<details open>` sections.
  Added `{% block breadcrumbs %}` above content. Responsive via media query.
- **program_state.html** (NEW): Extends base, renders markdown content via
  `{{ content | markdown }}` filter. Shows breadcrumbs and source provenance.
- **index.html**: Added breadcrumbs block. Program State section shows links
  to program state pages when docs exist, falls back to WO table otherwise.
- **finding.html**: Added breadcrumbs block (Home > Findings > title).
- **artifact.html**: Added breadcrumbs block (Home > Raw Data > class).

## Design Decisions
- `nav_sections` built in Python, not in templates — data-driven sidebar.
- `mistune.html` wrapped in `markupsafe.Markup` for Jinja2 autoescape compat.
- Graceful absence: missing `program-state/` produces no errors, no empty nav section.
- Deterministic ordering: program state docs sorted by `source_file`.

## Verification
- Syntax check passed on `site.py`.
- No existing site-specific tests to run (noted in brief).
- No pip/venv available in environment; `mistune` import verified at syntax level.
- Template Jinja2 syntax manually verified against base.html block structure.
