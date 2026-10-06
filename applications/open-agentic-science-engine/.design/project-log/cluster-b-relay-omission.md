# Relay Omission Fix — Batch 4 (Cluster B)

**Date**: 2026-09-19
**Issues**: #250, #255, #256, #267
**Branch**: `fix/cluster-b-relay-omission-batch4`

## Problem

Four silent relay omission bugs across four commands, all violating the
unconditional relay principle (tool-design-guidance.md §8).

### #250 — cellxgene.py (line 432)
The `cellxgene.search_is_metadata_only` relay was appended inside an
`if total_datasets > 0:` guard. When a search returned zero datasets, the
mandatory relay was silently dropped.

### #255 — differentiation.py (line 926)
Relay data was emitted via `emit.line` (text mode only) but had no
`emit.data("relays", relays)` call. In JSON output mode (`--json`), the
structured relay data was absent from the output payload.

### #256 — disco.py (line 381)
The `disco.search_is_sample_metadata` relay was inside `if n > 0:`. Zero
samples silently dropped the relay.

### #267 — scp.py (line 291)
The `scp.search_is_study_metadata` relay was inside
`if outcome == "results_found":`. Zero results silently dropped the relay.

## Fix

### #250, #256, #267 — Same pattern
Moved the `relays.append(provenance.relay(...))` call outside the
count-guarded `if` block. The relay now fires unconditionally, regardless
of whether results were found.

### #255 — Missing emit.data
Added `emit.data("relays", relays)` alongside the other `emit.data()` calls
in `differentiation.py` so that relay data is included in the JSON output
payload. The relay was already present in the analysis sidecar's
`mandatory_relays` field.

## Files Changed

- `tools/dde/commands/cellxgene.py` — relay moved outside `if total_datasets > 0`
- `tools/dde/commands/differentiation.py` — added `emit.data("relays", relays)`
- `tools/dde/commands/disco.py` — relay moved outside `if n > 0`
- `tools/dde/commands/scp.py` — relay moved outside `if outcome == "results_found"`
- `tests/test_relay_omission_batch4.py` — 8 regression tests

## Tests

Eight regression tests in `tests/test_relay_omission_batch4.py`:

1. **test_cellxgene_relay_fires_with_zero_datasets** — zero collections, relay must fire
2. **test_cellxgene_relay_still_fires_with_datasets** — sanity: relay fires with data
3. **test_differentiation_assess_emits_relay_data_in_json_mode** — JSON output includes relays
4. **test_differentiation_assess_relays_in_analysis_sidecar** — relay in analysis sidecar
5. **test_disco_relay_fires_with_zero_samples** — zero samples, relay must fire
6. **test_disco_relay_still_fires_with_samples** — sanity: relay fires with data
7. **test_scp_relay_fires_with_zero_results** — zero results, relay must fire
8. **test_scp_relay_still_fires_with_results** — sanity: relay fires with data

## Verification

- All 8 tests pass (`pytest tests/test_relay_omission_batch4.py -v`)
- `ruff check` and `ruff format --check` pass on all modified files
- Committed and pushed to `fix/cluster-b-relay-omission-batch4`
