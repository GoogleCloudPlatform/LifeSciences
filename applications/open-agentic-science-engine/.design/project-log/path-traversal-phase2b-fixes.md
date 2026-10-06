# Path Traversal Vulnerability Fixes — Phase 2B+2C

**Date:** 2026-09-18
**Branch:** `scion/dev-phase2b`
**Issues:** #177, #181, #183, #193, #195, #199

## Summary

Fixed 6 path traversal vulnerabilities across the DDE toolchain where
user-controlled strings were interpolated into filesystem paths without
sanitization, allowing `../` traversal to escape target directories.

All fixes use the shared helper module `dde.core.paths` (from
`scion/dev-paths-helper`), which provides `sanitize_slug()` for
filename-safe slugs and `confine_path()` for directory confinement.

## Fixes Applied

### Fix 1: `coscientist.py` (#181) — Unsanitized session_id

`session_id` from tournament records was used directly in filenames
(`cs-{session}.export.json`, etc.) without sanitization. Applied
`sanitize_slug(session)` before constructing the filename.

### Fix 2: `conservation.py` (#183) — Unsanitized query_name

`query_name` derived from `--name` CLI option or MSA file stems was
used unsanitized in three commands (`compute`, `align`, `analyze`).
Applied `sanitize_slug()` immediately after each `query_name` assignment.

### Fix 3: `manufacturing.py` (#193) — Unsanitized concept_id

`concept_id` from concept data records was used directly in output
filenames. Applied `sanitize_slug()` to the value from
`concept_data.get("id", "IC-UNKNOWN")`.

### Fix 4: `homology.py` (#195) — Unsanitized PDB entity ID

`_parse_pdb_entity_id()` returned a PDB ID from user input without
validation. Applied `sanitize_slug()` at the call site in
`fetch_structure` after the function returns.

### Fix 5: `gtex.py` (#199) — Path traversal in `_locate`

Two attack vectors in `_locate()`:

1. **GENCODE ID branch:** Replaced the loose
   `gene.upper().startswith("ENSG") and "." in gene` check with a strict
   regex `VERSIONED_ENSG_RE = re.compile(r"^ENSG\d{11}(\.\d+)?$")`.

2. **Path branch:** Added `confine_path()` check to verify resolved
   paths stay within the project root.

Also sanitized `gencode_id` with `sanitize_slug()` in both `fetch_cmd`
and `analyze_cmd` before use in filenames.

### Fix 6: `fix_localize_cdn.py` (#177) — CDN URL path injection

`_url_to_vendor_path()` constructed filesystem paths from URL components
without sanitization. Applied three defenses:

- Sanitized domain component (replace non-`[a-zA-Z0-9._-]` with `_`).
- Filtered `..` segments from URL path components.
- Added inline `is_relative_to()` confinement checks in both
  `_localize_css_urls` and `_localize_html` before writing files.

## Testing

Created `tests/test_path_traversal_fixes.py` with 25 regression tests
covering all 6 fixes: traversal inputs produce safe slugs, normal inputs
are preserved, constructed paths stay within target directories, strict
GENCODE regex rejects malicious inputs, and CDN paths are confined to
`vendor/`.

## Verification

- All 25 tests pass (`pytest tests/test_path_traversal_fixes.py -v`)
- `ruff check` passes on all 6 modified source files
- `ruff format --check` passes on all 6 modified source files
- `ruff check` and `ruff format` pass on the test file
- Full test suite could not be run (dependencies not installed in
  environment beyond pytest and ruff)
