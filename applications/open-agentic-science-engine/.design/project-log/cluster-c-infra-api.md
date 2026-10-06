# Cluster C — Infrastructure/API Fixes (#254, #271)

**Date:** 2026-09-19
**Agent:** dev-cluster-c
**Branch:** `cluster-c/infra-api-fixes`

## Issues Fixed

### Issue #254 — ClinVar API Key Omitted from HTTP Requests

**Root cause:** `_fetch_clinvar()` in `gwas.py` built NCBI E-utilities URLs
without appending the NCBI API key, despite the QPS rate limiter
(`qps_for_host`) assuming the authenticated rate of 10 req/s. The comment
on line 79 explicitly said "public, unauthenticated", treating the omission
as intentional — but the QPS registry (`core/qps.py`) imports the dynamic
`EUTILS_QPS` from `core/ncbi.py`, which returns 10 when `NCBI_API_KEY` is
set. This mismatch caused 429 rate-limit errors.

**Fix:** Imported `api_key_suffix()` from `core/ncbi.py` (the same helper
already used by `pubmed.py` and `geo.py`) and appended it to both the
esearch and esummary URL strings. When the env var is set, the key appears
in the URL and the 10 req/s rate applies. When absent, the suffix is empty
and the QPS falls to 3 req/s — both sides of the contract now agree.

Updated the comment from "public, unauthenticated" to reference the
dynamic key behavior via `core/ncbi.py`.

### Issue #271 — --output-dir . Causes Project Root Deletion

**Root cause:** The site build resolves `output_dir` relative to
`project.root`, then checks confinement via `confine_path`. When
`output_dir` is `"."`, `out_resolved == project_resolved` — confinement
passes because a path is relative to itself. The atomic-swap logic then
renames the entire project root to a trash path and moves site-only
content in its place, effectively deleting all non-site project content.

**Fix:** Added an equality check immediately after `confine_path`:

```python
if out_resolved == project_resolved:
    raise Refusal("--output-dir must not be the project root itself", ...)
```

Also added a guard against output directories that are parents of `.dde/`
(the control store), preventing a similar class of destructive operations.
Used `Refusal` (not `ArtifactError`) because this is a security-adjacent
input rejection — the remedy is "change the input".

## Tests

Seven regression tests in `tests/test_infra_api_fixes.py`:

1. **test_api_key_included_when_env_set** — patches `api_key_suffix` to
   return a fake key, verifies both esearch and esummary URLs include it.
2. **test_no_api_key_when_env_unset** — patches suffix to return empty,
   verifies URLs have no `api_key` param, artifact is valid.
3. **test_no_results_still_works** — empty UID list, verifies graceful
   degradation with 0 associations.
4. **test_output_dir_dot_rejected** — `"."` raises `Refusal`.
5. **test_output_dir_equals_project_root_absolute** — absolute path
   equal to project root raises `Refusal`.
6. **test_valid_subdirectory_accepted** — `"_site"` passes all guards.
7. **test_confine_path_passes_for_dot** — confirms `confine_path`
   correctly accepts `"."` (documenting the bug's root cause).

## Verification

- All 7 tests pass (`pytest -v`)
- `ruff check` passes on all 3 modified files
- No other files modified
