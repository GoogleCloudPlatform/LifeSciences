# Hypex B1 Review Fixes

**Date:** 2026-09-08
**Branch:** `scion/b1-review`
**Commit:** 8380cb7

## Summary

Addressed 6 findings from 3 independent reviews (code, test, security) of the
B1 hypex integration.

## Changes

### 1. Pacing Persistence Bug (highest priority)
- **File:** `tools/dde/commands/hypex.py`
- `_build_hypex_record` now includes `"pacing"` and `"progress"` in the returned
  record dict.
- `analyze` reads pacing from `record.get("pacing")` instead of from the
  filesystem (`meta/pacing.json` via `hypex_run_dir`).
- This prevents false-positive `hypex.pacing_uncoordinated` relays after the
  original run directory is archived or deleted.

### 2. Binary Checksum Verification (security HIGH)
- **File:** `tools/install.sh`
- Added `HYPEX_SHA256`, `HYPEX_ELO_SHA256`, `HYPEX_PROX_SHA256` constants (all
  `PLACEHOLDER`) with guard clauses matching the existing `fpocket` pattern.
- When set to a real hash, downloads are verified; when `PLACEHOLDER`, a warning
  is emitted but installation proceeds.

### 3. Symlink Following (security MEDIUM)
- **File:** `tools/dde/commands/hypex.py`
- `_walk_json_dir` now skips symlinks (`f.is_symlink() → continue`).
- `_archive_run_dir` now uses a `_safe_filter` that strips symlinks and hard
  links from tar archives.

### 4. File Size Guards (security MEDIUM)
- **File:** `tools/dde/commands/hypex.py`
- Added `MAX_INPUT_BYTES = 50 * 1024 * 1024` at module level.
- `_walk_json_dir` raises `ArtifactError` for files exceeding the limit.

### 5. Broad Exception Catch (code review N1)
- **File:** `tools/dde/commands/hypex.py`
- Changed `except Exception:` to `except ThresholdError:` for the
  `elo_decisive_gap` resolution block.
- Added `ThresholdError` to the imports from `core.errors`.

### 6. Missing Tests
- **File:** `tests/test_hypex.py`
- Added 7 new tests (31 total, all passing):
  - `test_pacing_persisted_in_record` — pacing data stored in record
  - `test_pacing_relay_after_run_dir_deleted` — no false-positive after cleanup
  - `test_composite_ranking_fires` — composite_preset triggers relay
  - `test_no_composite_no_relay` — negative test for composite
  - `test_roster_ingested` — roster.ndjson → record["roster"]
  - `test_ingest_produces_archive` — .tar.zst file exists
  - `test_converged_run_no_run_not_converged_relay` — negative relay test

## Verification

- All 3 changed files pass syntax validation.
- All 31 tests pass (pytest, 0.57s).
- No existing tests were modified; all 24 pre-existing tests continue to pass.
- Full test runner was used (pip deps installed in temporary venv).

## Gates Not Run

- Full `install.sh` provisioning was not run (no apt/network access for binary
  downloads). The install.sh changes are syntax-checked and follow the exact
  pattern of the existing `fpocket` function which has passing verification.
