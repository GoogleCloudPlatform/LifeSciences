# Phase 2: Viewer Foundation (Native HTML Viewers + Wiring)

**Date:** 2026-08-19  
**Branch:** `scion/web-pkg-p2`  
**Issue:** #129 Phase 2

## Summary

Established the viewer wiring infrastructure and created 5 standalone HTML viewers
for the venter site build system. This enables artifact files to be opened in
dedicated viewers directly from artifact class pages.

## Changes

### `tools/venter/commands/site.py`
- Added `VIEWER_MAP` constant — ordered list of `(suffix, viewer_html)` tuples for
  mapping file extensions to viewer HTML files. Most-specific suffixes first so
  `.pockets.json` matches before the generic `.json` fallback.
- Added `viewer_url_for()` helper — returns a viewer URL with `?file=` parameter
  using relative paths from the viewer's location in the output directory.
- Updated `_collect_artifact_classes()` to populate `viewer_url` on each artifact dict.
- Updated `_render_site()` to copy the `viewers/` directory from site templates
  into the output directory via `shutil.copytree()`.

### `tools/venter/site_templates/artifact.html`
- Added "View" column to the artifacts table.
- Each artifact row shows a "View" link (opens in new tab) when `viewer_url` is set.

### New viewer files in `tools/venter/site_templates/viewers/`
1. **`json-viewer.html`** — Generic JSON viewer with highlight.js syntax highlighting
   (loaded from `cdn.jsdelivr.net`). Parses and re-formats JSON for consistent display.
2. **`pockets-viewer.html`** — Renders binding site pocket data as an HTML table.
   Supports both array format and `{pockets: [...]}` wrapper.
3. **`constraint-viewer.html`** — Displays gnomAD constraint metrics as a styled card.
   Color-codes key metrics (pLI > 0.9, LOEUF < 0.35) for quick assessment.
4. **`tournament-viewer.html`** — Renders hypothesis tournament rankings as a sorted
   table with rank highlighting (gold/silver/bronze for top 3).
5. **`brics-viewer.html`** — Displays BRICS decomposition data. Attempts table
   rendering for arrays of objects; falls back to pretty-printed JSON.

## Design Decisions
- Viewers are standalone HTML — no Jinja2, no base.html dependency. Copied as-is.
- `VIEWER_MAP` uses list-of-tuples (not dict) for ordered suffix matching.
- `?file=` paths are relative to the viewer's directory (`viewers/`), using `../`
  to reach artifact directories.
- All CDN dependencies use `cdn.jsdelivr.net` exclusively.

## Verification
- `site.py` parses cleanly (Python AST validation).
- All 5 viewer HTML files validated (DOCTYPE, closing tags, structure).
- VIEWER_MAP and viewer_url_for are correctly defined at module level.
- No test suite available in this environment; syntax and structure verified.
