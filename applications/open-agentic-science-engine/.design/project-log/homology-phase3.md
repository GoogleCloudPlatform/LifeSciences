# Homology Phase 3 — `fetch-structure` subcommand

**Date:** 2026-08-20
**Issue:** #184
**Branch:** `scion/tools-lead-em-184`
**Agent:** dev-homology-p3

## Summary

Implemented the `homology fetch-structure` subcommand that downloads a single
PDB coordinate file from RCSB and writes a provenance sidecar.

## Changes

### `tools/venter/commands/homology.py`
- Added `TOOL_FETCH = "homology-fetch"` constant (distinct from `TOOL` for search).
- Added `RCSB_DOWNLOAD_BASE` and `RCSB_DOWNLOAD_QPS` constants.
- Added `_parse_pdb_entity_id()` helper — extracts PDB ID from `7FD3_1` or `7FD3`,
  uppercases it.
- Added `fetch_structure` Click command registered as `fetch-structure` on the
  `homology` group. Downloads CIF (default) or PDB from
  `https://files.rcsb.org/download/{PDB_ID}.{format}` using `http.get_bytes()`,
  writes provenance sidecar via `provenance.Sidecar`, emits output via `Emitter`.
- Updated module docstring to document Phase 3.

### `tests/test_homology.py`
- Added 6 Phase 3 tests:
  - `test_parse_pdb_entity_id_with_entity` — `7FD3_1` → PDB ID `7FD3`
  - `test_parse_pdb_entity_id_bare` — `7FD3` (no entity suffix) works
  - `test_parse_pdb_entity_id_lowercase` — lowercase input uppercased
  - `test_fetch_structure_download_and_sidecar` — mock download, verify file
    written, sidecar records correct endpoint/parameters/SHA-256
  - `test_fetch_structure_pdb_format` — `--format pdb` uses `.pdb` extension
  - `test_fetch_structure_sidecar_sha256` — sidecar outputs array contains
    valid SHA-256 hash
- Updated test runner and module docstring.

## Verification

- Both modified files pass `ast.parse()` syntax check.
- Could not run full test suite (`click` and `httpx` not installed in container).
- Code follows the exact same pattern as `alphafold fetch`'s CIF download
  (`http.get_bytes` → `write_bytes` → `sidecar.add_output` → `sidecar.write`).
