# Phase 3: Symlink Exploitation Fixes

**Date:** 2026-09-18
**Branch:** `scion/dev-phase3`
**Issues:** #178, #196, #207, #210, #212

## Summary

Fixed 5 symlink exploitation vulnerabilities across 5 Python files,
all using the shared `is_safe_to_open()` helper from `core/paths.py`
(Phase 1). Also refactored `validate.py` and `site.py` to use the
shared `confine_path()` instead of their local `_confine_path` copies.

## Changes

### Fix 1: artifact.py (#178) — Sidecar symlink write-through

Added `is_safe_to_open(sidecar_path)` check before the `exists()`
guard in `register_cmd`. A dangling symlink at the sidecar path would
have been written through to the symlink target.

### Fix 2: validate.py (#210) — `_is_analysis` called before confinement

In 4 functions (`_find_layer0_artifacts`, `_check_analysis_citations`,
`_collect_relay_codes`, `_check_unrecognized_json`), moved
`is_safe_to_open()` and `confine_path()` guards **before** the
`_is_analysis()` call. `_is_analysis` reads file content via
`path.read_text()` for `.json` files, so a symlink pointing outside
the project would be read before confinement rejected it.

Also replaced the local `_confine_path()` with the shared
`confine_path()` from `core/paths.py` (Phase 1b refactoring).

### Fix 3: hypex.py (#196) — Missing symlink check on metadata files

Added `is_safe_to_open()` guard to 7 direct file reads that bypassed
the existing `_walk_json_dir` symlink check: citation manifest,
epoch-*.json rating files, run.yaml, termination.json, roster.ndjson,
progress.json, and pacing.json.

### Fix 4: http.py (#212) — Symlink following in pace file

Added `is_safe_to_open()` check before `open(pace_file, "a+")` in
`_pace_disk`. When the pace file is a symlink, falls back to
`_pace_memory()` rather than writing through the symlink.

### Fix 5: site.py (#207) — `shutil.copytree` follows symlinks

Added `symlinks=True` to the `shutil.copytree` call in the raw/
bundling step. Without this flag, symlinks in raw/ pointing outside
the project would be dereferenced and their target content copied
into the site output.

Also replaced the local `_confine_path()` with the shared
`confine_path()` from `core/paths.py` (Phase 1b refactoring).

## Tests

Added `tests/test_symlink_fixes.py` with 14 regression tests covering
all 5 fixes:
- `TestArtifactSidecarSymlink` (3 tests)
- `TestValidateSymlinkOrdering` (2 tests)
- `TestHypexSymlinkSkip` (4 tests)
- `TestHttpPaceFileSymlink` (2 tests)
- `TestSiteCopytreeSymlinks` (3 tests)

## Verification

- `pytest tests/test_symlink_fixes.py` — 14 passed
- `pytest tests/test_paths.py` — 22 passed
- `ruff check` — all clean
- `ruff format --check` — all formatted

## Files Modified

- `tools/dde/commands/artifact.py` — import + symlink guard
- `tools/dde/commands/validate.py` — import, refactor, 4 loop fixes
- `tools/dde/commands/hypex.py` — import + 7 symlink guards
- `tools/dde/core/http.py` — import + symlink guard with fallback
- `tools/dde/commands/site.py` — import, refactor, copytree fix
- `tests/test_symlink_fixes.py` — new (14 tests)
