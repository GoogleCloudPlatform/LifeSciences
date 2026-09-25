# Relay Omission Batch 1 — Silent Safety Relay Fixes

**Date**: 2026-09-18
**Issues**: #180, #184, #186
**Branch**: `fix/relay-omission-batch1`

## Summary

Fixed three silent safety relay omission bugs where mandatory relays were
conditionally skipped or structurally unable to fire, violating the "fail
loudly, never degrade silently" principle (tool-design-guidance.md §8).

## Changes

### #180 — allen.py: brain_region_expression_only relay unconditional

**Problem**: The `allen.brain_region_expression_only` relay was guarded by
`if datasets:`. When a gene search returned zero datasets, the relay was
silently omitted, allowing a downstream agent to infer that peripheral
tissue expression was absent — a false negative.

**Fix**: Removed the `if datasets:` guard. The relay now appends
unconditionally in `analyze_cmd`, as it is a mandatory qualifier about
the Allen Brain Atlas coverage scope, not a data-dependent finding.

### #184 — conservation.py: canonical_length and low_coverage relay

**Problem**: `compute_cmd` never stored `canonical_length` in the JSON
record. In `analyze_cmd`, `canonical_length` fell back to
`total_positions` which equalled `len(residues)` (the count of scored
positions), making coverage always 1.0. The `conservation.low_coverage`
relay therefore could never fire.

**Fix**:
1. `compute_cmd` now computes `canonical_length` as
   `max(res["position"] for res in residues)` and stores it in the record.
2. `analyze_cmd` reads `canonical_length` from the record when present;
   for older records lacking it, derives it from `max(res["position"])`
   rather than falling back to `len(residues)`.
3. The `range(1, canonical_length + 1)` loop for unscored-position
   detection now uses the correct canonical length, enabling the
   `conservation.pocket_in_gap` relay to identify gaps at higher
   position indices.

### #186 — compreg.py: error on empty sources_queried

**Problem**: `analyze_cmd` iterated over expected registry response files
with `if not path.is_file(): continue`. When ALL files were missing,
`sources_queried` was empty, `n_matches` was 0, and the command reported
`outcome = "not_found"` with exit 0 — certifying a compound doesn't
exist when no database was actually consulted.

**Fix**: After the registry-file loop, validate that `sources_queried`
is non-empty. If not, raise `ArtifactError` with a message directing the
user to run `dde compreg resolve` first.

## Tests

All tests in `tests/test_relay_omission_batch1.py`:

| Test | Verifies |
|---|---|
| `test_allen_relay_fires_with_empty_datasets` | #180: relay present with empty datasets |
| `test_allen_relay_fires_with_nonempty_datasets` | Sanity: relay also present with datasets |
| `test_conservation_low_coverage_relay_fires` | #184: coverage < 1.0 and relay fires (with canonical_length) |
| `test_conservation_coverage_without_canonical_length_field` | #184: relay fires even for older records without canonical_length |
| `test_compreg_raises_when_no_registry_files` | #186: error raised when no registry files exist |
| `test_compreg_succeeds_with_registry_files` | Sanity: analyze succeeds with registry files present |

## Verification

- `ruff check` — all 4 modified files pass
- `ruff format --check` — all 4 files correctly formatted
- `pytest` — all 6 tests pass
- Note: `pytest` and `ruff` required manual installation in the environment;
  numpy, pyyaml, click were also installed as transitive dependencies of the
  dde CLI
