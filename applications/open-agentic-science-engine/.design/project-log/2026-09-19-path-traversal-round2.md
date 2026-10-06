# Path Traversal Fixes — Round 2

**Date:** 2026-09-19
**Issues:** #274, #287, #291, #272, #276, #282, plus 2 addendum fixes

## Summary

Fixed eight path traversal vulnerabilities across the DDE toolchain using
the shared `sanitize_slug()` and `confine_path()` helpers from
`dde.core.paths`. All fixes follow the same pattern established in
Round 1: user-controlled input is sanitized before it enters filesystem
path construction.

## Issues Fixed

| Issue | File | Input | Fix |
|-------|------|-------|-----|
| #274 | `pathway.py:440` | `gene` argument | `sanitize_slug(gene.upper())` |
| #287 | `ppi.py:277,377` | `gene` argument | `sanitize_slug(gene.lower())` |
| #291 | `preprint.py:578` | `artifact` CLI arg | `confine_path()` + `sanitize_slug()` |
| #272 | `site.py:1712` | `--site-dir` option | `confine_path(project_root, Path(site_dir))` |
| #276 | `structure.py:1108` | `gene_label` | `sanitize_slug(gene_label.lower())` |
| #282 | `envstamp.py:594` | `version` string | `sanitize_slug()` + `confine_path()` |
| (A)  | `expression.py:1005` | `gene` in `_locate_single_cell` | `confine_path()` + `sanitize_slug()` |
| (B)  | `homology.py:826` | `uniprot_id_or_path` manifest path | `confine_path()` |

## Details

- **pathway.py** — Gene name used directly in `source_dir / f"{resolved}.{suffix}.json"`.
  Applied `sanitize_slug()` to strip directory separators.

- **ppi.py** — Gene slug used in three file paths (verbatim, artifact, meta).
  Applied `sanitize_slug()` at both slug assignment sites (search and analyze).

- **preprint.py** — Artifact CLI argument used directly as path component.
  Added `confine_path()` to verify the artifact path stays within `source_dir`,
  and `sanitize_slug()` for the fallback slug-based path. Added `Path` import.

- **site.py** — `--site-dir` option used directly in path construction.
  Applied `confine_path()` (already imported) to verify the site path stays
  within the project root.

- **structure.py** — Gene label used as filename stem for topology annotation
  artifacts. Applied `sanitize_slug()` to the lowercased label.

- **envstamp.py** — Version string flows into archive path after only
  colon-to-hyphen replacement. Applied `sanitize_slug()` after the colon
  replacement and `confine_path()` to verify the final path stays within
  the archive directory. Import uses `from .paths` (same `core/` package).

- **expression.py (_locate_single_cell)** — Functionally identical to the
  already-fixed `_locate()` but was not hardened. Applied `confine_path()`
  for path-based gene input and `sanitize_slug()` for all ensembl ID
  assignments, mirroring the Round 1 fix exactly.

- **homology.py (analyze)** — When `uniprot_id_or_path` has a suffix (path-like),
  it's resolved relative to project root without confinement. Added
  `confine_path()` check and `confine_path` to the existing import.

## Testing

Created `tests/test_path_traversal_round2.py` with 21 tests (at least 2 per
issue). Each issue has:
1. A traversal-rejection test proving malicious input is sanitized/rejected
2. A regression test proving normal input still works correctly

All 21 tests pass. All 42 pre-existing path tests (Round 1 + core paths) also pass.

## Verification

- All 8 modified files pass `py_compile` syntax checks
- `pytest tests/test_path_traversal_round2.py -v` — 21/21 passed
- `pytest tests/test_path_traversal_round1.py tests/test_paths.py -v` — 42/42 passed
- ruff was not available in the environment; standard Python formatting was applied manually
