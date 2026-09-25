# Phase 5: Work Order Pages + Retrospectives

**Date:** 2026-08-19
**Branch:** `scion/web-pkg-p5`
**Phase:** 5 of issue #129

## Summary

Implemented per-work-order operations pages and a retrospectives section
with `--retrospectives-dir` CLI flag.

## Changes

### New templates
- `workorder.html` — per-WO page showing full metadata (id, revision,
  requested_role, stage, cycle, decision_question, priority, created_at,
  state) with cross-referenced links to findings and artifact classes.
- `retrospectives.html` — renders collected retrospective markdown files
  with title extraction and source provenance.

### Updated templates
- `index.html` — added Operations section listing WO pages with links,
  and a Retrospectives link (conditionally shown when retrospectives exist).

### Python changes (`tools/venter/commands/site.py`)
- Added `_collect_retrospectives(retro_dir)` — walks a directory for `.md`
  files, extracts titles, confines symlinks to the retrospectives directory
  itself (not project root, per design doc OQ1).
- Updated `_build_nav_sections()` — accepts `work_orders_with_pages` and
  `has_retrospectives` parameters; adds Operations section (after findings,
  before gates) and Retrospectives section (at bottom, only if present).
- Updated `_render_site()` — builds `work_orders_with_pages` list with
  sanitized `html_filename`, renders one page per accepted WO with
  cross-referenced findings and artifact classes, renders retrospectives
  page when present.
- Updated `build_cmd()` — added `--retrospectives-dir` CLI option
  (read-only, explicitly opted-in, no `_confine_path()` applied).

## Design Decisions
- `--retrospectives-dir` is not confined to project root per design doc —
  it's an external read-only directory.
- Symlinks within retrospectives dir are confined to that directory.
- Omitting `--retrospectives-dir` produces no retrospectives section.
- Operations section appears in sidebar after Findings, before Gates.
- Retrospectives section appears at the bottom of the sidebar.

## Verification
- Python syntax check passed (`py_compile`).
- Template structure verified against existing patterns.
- Jinja2 and full test suite could not run (dependencies not installed
  in this environment).
