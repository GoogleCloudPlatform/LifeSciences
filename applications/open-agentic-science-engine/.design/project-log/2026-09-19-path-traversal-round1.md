# Path Traversal Fixes — Round 1

**Date:** 2026-09-19
**Branch:** DDE
**Issues:** #252, #249, #258, #265, #268, #260, #273

## Summary

Fixed 7 path traversal vulnerabilities across the DDE toolchain. Each fix
applies the existing `sanitize_slug()` and/or `confine_path()` helpers from
`dde.core.paths` to user-controlled input before it reaches path construction.

## Changes

| Issue | File | Vulnerability | Fix |
|-------|------|--------------|-----|
| #252 | `genetics.py` | Gene symbol used unsanitized in filename | `sanitize_slug(symbol.upper())` |
| #249 | `alphagenome.py` | Variant params (chrom/pos/ref/alt) in filename | `sanitize_slug()` on assembled stem |
| #258 | `docking.py` | `receptor_id` from JSON in sidecar path | `sanitize_slug()` on receptor_id and stem; `confine_path()` on final path |
| #265 | `env.py` | Version token in `diff()` flows to `archived()` | `sanitize_slug()` on token before passing to `envstamp.archived()` |
| #268 | `expression.py` | Gene/Ensembl ID in `_locate()` path construction | `sanitize_slug()` on ensembl IDs; `confine_path()` on path-form inputs |
| #260 | `homology.py` | `query_accession` and `query_gene` in stem | `sanitize_slug()` on both before stem construction |
| #273 | `litref.py` | Citation used as path; slug from DOI | `confine_path()` on .meta.json path; `sanitize_slug()` on slug |

## Testing

Created `tests/test_path_traversal_round1.py` with 20 tests (at least 2 per
issue). All tests pass. Existing `tests/test_paths.py` tests also pass.

## Verification

- All 20 new tests pass: `python3 -m pytest tests/test_path_traversal_round1.py -v`
- All 22 existing path tests pass: `python3 -m pytest tests/test_paths.py -v`
- `ruff check` and `ruff format` clean on all modified files
- License headers preserved in all files
