# Path Traversal Fix — Phase 2A (Batch A)

**Date:** 2026-09-18
**Author:** dev-phase2a

## Summary

Fixed path-traversal vulnerabilities in 4 command files where unsanitized
user-supplied strings were used as filename slugs, allowing `../`-based
directory escape. Each fix wraps the slug assignment with the shared
`sanitize_slug()` helper from `core/paths.py`.

## Files changed

### `commands/dice.py` (#185)
- Added `from ..core.paths import sanitize_slug` import
- `search_cmd`: `slug = sanitize_slug(gene.upper())`
- `analyze_cmd`: `slug = sanitize_slug(gene.upper())`

### `commands/gwas.py` (#194)
- Added `from ..core.paths import sanitize_slug` import
- `search_cmd`: `slug = sanitize_slug(gene.lower())`
- `search_disease_cmd`: replaced inline ad-hoc regex sanitizer with
  `slug = sanitize_slug(disease.lower()) if disease.strip() else "disease"`
- `analyze_cmd`: `slug = sanitize_slug(gene.lower())`

### `commands/phenotype.py` (#203)
- Added `from ..core.paths import sanitize_slug` import
- `search_cmd`: `slug = sanitize_slug(gene.lower())`
- `analyze_cmd`: `slug = sanitize_slug(gene.lower())`

### `commands/pubchem.py` (#201)
- Added `from ..core.paths import sanitize_slug` import
- `fetch_cmd`: `slug = sanitize_slug(slug_override) if slug_override else str(cid)`
  — only the user-supplied `--name` override needs sanitization; `str(cid)` is
  an integer string and safe by construction.

## Tests added

`tests/test_pathtraversal_phase2a.py` — 17 regression tests across 5 test classes:
- `TestDiceSlugSanitized`: traversal neutralized, normal gene preserved, GENCODE ID preserved
- `TestGwasSlugSanitized`: traversal neutralized, normal gene preserved, GENCODE ID preserved
- `TestGwasDiseaseSlugSanitized`: traversal neutralized, normal disease preserved, spaces hyphenated, empty fallback
- `TestPhenotypeSlugSanitized`: traversal neutralized, normal gene preserved, GENCODE ID preserved
- `TestPubchemSlugSanitized`: traversal neutralized, normal name preserved, integer CID fallback safe, dots preserved

## Verification

- `pytest tests/test_pathtraversal_phase2a.py -v` — 17/17 passed
- `ruff check` — all 5 files clean
- `ruff format --check` — all 5 files already formatted
