# Phase 4: CDN Viewers — 9 Library-Dependent Viewers

**Date:** 2026-08-19
**Branch:** `scion/web-pkg-p4`
**Author:** web-pkg-dev-p4

## Summary

Implemented 9 standalone HTML viewer files that use CDN-loaded libraries
(3Dmol.js and Plotly.js) to render scientific data. These viewers are
standalone HTML with no Jinja2 dependencies — they are copied as-is into
the site output and accessed via `?file=` (or `?receptor=`/`?poses=`)
query parameters.

## Files Created

### 3Dmol.js Viewers (3 files)
- `structure-viewer.html` — Renders `.cif` molecular structures with cartoon/stick/sphere
  style selector and spin toggle control.
- `sdf-viewer.html` — Renders `.3d.sdf` molecules in stick representation with
  element colors and hover labels.
- `docking-viewer.html` — Dual-file viewer for receptor+poses `.pdbqt` files.
  Receptor shown as translucent cartoon, poses as colored sticks. Handles
  single-file fallback and `?file=` routing from VIEWER_MAP.

### Plotly.js Viewers (6 files)
- `pae-viewer.html` — Predicted Aligned Error heatmap from `.pae.json`.
- `plddt-viewer.html` — Per-residue pLDDT confidence bar chart from `.afdb.json`
  with color-coded confidence bands and threshold lines at 50/70/90.
- `expression-viewer.html` — Tissue expression horizontal bar chart from
  `.tissue.json`, sorted by expression level with color gradient.
- `contacts-viewer.html` — Contact map heatmap with support for both matrix
  and contact-list formats, with JSON fallback for unknown formats.
- `admet-viewer.html` — ADMET predictions as radar chart (3+ properties) or
  bar chart, with pass/fail color coding and details table.
- `docking-scores-viewer.html` — Docking score comparison bar chart from
  `.docking_result.json`, sorted by affinity, with optional RMSD overlay.

## Design Decisions

- **All CDN loads from `cdn.jsdelivr.net` only** per security requirements.
- **No `innerHTML` with dynamic content**, no `eval()`, no `new Function()`.
  All user-visible text set via `textContent`.
- **Flexible JSON parsing**: each Plotly viewer accepts multiple common JSON
  formats (bare arrays, wrapped objects, various key names) to handle real-world
  data variety.
- **Graceful error handling**: every viewer shows a clear error message for
  missing file parameters, HTTP errors, and unrecognised data formats.

## Verification

- No `innerHTML`, `eval()`, or `document.write()` found in any viewer.
- All 9 viewers load CDN scripts from `cdn.jsdelivr.net`.
- All 9 viewers handle missing `?file=` parameter with error display.
- No changes to `site.py` needed — VIEWER_MAP entries (from Phase 2) already
  route to these exact filenames.
