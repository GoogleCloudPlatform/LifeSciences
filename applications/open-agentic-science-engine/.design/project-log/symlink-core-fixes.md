# Symlink Exploitation Fixes — core/ Batch

**Date:** 2026-09-19
**Commit:** c1161c0 (DDE branch)
**Issues:** #292, #294, #296, #279, #299, #301, #302

## Summary

Added `is_safe_to_open()` guards to 7 symlink-vulnerable call sites in
`tools/dde/core/`. Each guard uses relative imports (`from .paths import
is_safe_to_open`) and follows the existing error-handling convention in
its file.

## Changes

| File | Line | Operation | Error Handling |
|------|------|-----------|---------------|
| `context.py` | ~226 | `_write_if_missing()` write | `ProjectRootError` |
| `controlstore.py` | ~519 | concept JSON read | `return None` (fail-closed) |
| `controlstore.py` | ~603 | `write_record()` write | `Refusal` |
| `env.py` | ~63 | `ENV_VERSION` marker read | skip (fall through to dev path) |
| `policy.py` | ~558 | `program.yaml` read | `SchemaError` |
| `provenance.py` | ~1106 | `Sidecar.write()` write | `Refusal` |
| `thresholds.py` | ~1271 | `thresholds.yaml` read | `ThresholdError` |

## Error Handling Conventions

Each file's existing error type was preserved:

- **context.py**: Uses `ProjectRootError` throughout for path issues
- **controlstore.py**: Read path uses fail-closed `return None` (matching
  existing `try/except` pattern); write path uses `Refusal` (security denial)
- **env.py**: No error module — symlink treated like missing file, falls
  through to unpinned-dev fallback
- **policy.py**: Uses `SchemaError` for all config read failures
- **provenance.py**: Uses `Refusal` (already imported for other guards)
- **thresholds.py**: Uses `ThresholdError` for all threshold file issues

## Testing

- Created `tests/test_symlink_core.py` with 15 regression tests
- All 15 tests pass (`python -m pytest tests/test_symlink_core.py -v`)
- Existing `test_symlink_fixes.py` and `test_symlink_commands.py` unaffected
- Ruff lint and format checks pass on all modified files

## Verification Gates

- **pytest**: 15/15 passing
- **ruff check**: all changed files clean
- **ruff format**: all changed files formatted
- **Could not run**: full project build (`click` not installed in this env);
  pre-existing failures in `test_symlink_fixes.py` due to missing `click`
  module are unrelated to these changes
