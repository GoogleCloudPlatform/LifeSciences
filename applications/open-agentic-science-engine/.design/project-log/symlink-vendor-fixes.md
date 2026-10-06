# Symlink Exploitation Fixes — skills/ and vendor/ batch

**Date:** 2026-09-19
**Issues:** #246, #247, #306, #307
**Commit:** d59c45c on DDE branch

## Summary

Added symlink guards to 4 call sites in `skills/` and `vendor/` that iterate
directories with `rglob`/`glob`/`ReadDir` and read file contents without
checking for symlinks. An attacker who can plant a symlink in these directories
could read or write arbitrary files outside the intended scope.

## Changes

### #246 — `skills/site-generation/references/fix_add_themes.py`
- **Call site:** `fix_add_themes()` iterates `site_dir.rglob("*.html")` and
  calls `html_file.read_text()` then `html_file.write_text()`.
- **Fix:** Added `if html_file.is_symlink(): continue` before the read.

### #247 — `skills/site-generation/references/postbuild-template.py`
- **Call sites:** Three functions (`fix_tables`, `fix_duplicate_h1`,
  `fix_md_links`) each iterate `site_dir.rglob("*.html")` and call
  `html_file.read_text()` / `html_file.write_text()`.
- **Fix:** Added `if html_file.is_symlink(): continue` to all 3 loops.

### #306 — `tools/vendor/hypex/.../cmd/validate.go`
- **Call site:** `findArtifactFiles()` iterates `os.ReadDir()` entries and
  builds file paths from `e.Name()` without checking if the entry is a symlink.
- **Fix:** Added `if e.Type()&os.ModeSymlink != 0 { continue }` before the
  `IsDir` / suffix check.

### #307 — `tools/vendor/hypex/.../prox/prox/embed.py`
- **Call site:** `load_hypotheses()` iterates `hyp_dir.glob("H-*.json")` and
  opens each file with `open(path)`.
- **Fix:** Added `if path.is_symlink(): continue` with a warning log before
  the `open()` call.

## Tests

Created `tests/test_symlink_vendor.py` with 8 regression tests:

- `TestFixAddThemesSymlink` (2 tests) — verifies symlinked HTML is skipped and
  real HTML is still processed.
- `TestPostbuildTemplateSymlink` (3 tests) — one test per fix function verifying
  symlinked HTML is skipped.
- `TestEmbedLoadHypothesesSymlink` (2 tests) — verifies symlinked hypothesis
  files are skipped and real files still load.
- `TestGoDirEntrySymlinkConcept` (1 test) — verifies the DirEntry symlink
  filtering pattern using Python's `os.scandir` (conceptual equivalent of the
  Go fix).

All 8 tests pass. Go code passes `go vet` and `go build`.

## Verification

| Gate | Result |
|------|--------|
| `python3 -m pytest tests/test_symlink_vendor.py -v` | 8/8 passed |
| `py_compile` on all 3 Python files | OK |
| `go vet ./cmd/` | OK |
| `go build ./cmd/` | OK |
| ruff lint/format | Not available in environment |
