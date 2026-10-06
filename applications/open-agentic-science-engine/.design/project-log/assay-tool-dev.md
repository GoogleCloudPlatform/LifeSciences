# Assay Tool Development Log

**Issue:** #34
**Branch:** `scion/tools-lead-em-34-dev-assay-1`
**Date:** 2026-08-19

## What was built

`tools/venter/commands/assay.py` — a two-phase command group:

### Phase 1: `assay ingest`
- Accepts JSON matching canonical schema `venter.assay.v1`
- Validates schema version: non-canonical input is a Refusal (exit 9)
- Validates required fields: well_id, compound_id, readout_value, readout_type, assay_type, plate_id, run_id, concentration (with units), optional metadata
- Normalises and writes Layer 0 artifact + `.meta.json` sidecar to `raw/assays/`
- Follows `coscientist.py` structural precedent (schema check, Sidecar, Emitter output)

### Phase 2: `assay analyze`
- Reads stored assay data, applies named threshold sets
- Genuinely offline (phase-2 latch enforces automatically)
- Implements:
  1. Activity cutoffs (UNRESOLVED — program-specific)
  2. 4PL/Hill dose-response curve fitting via `scipy.optimize.curve_fit`
  3. Hill slope range and R-squared floor checks (UNRESOLVED thresholds)
  4. Z-factor screen quality validation (Zhang et al. 1999)
  5. Bell-shaped dose-response detection for cytotoxicity confounds

## Threshold definitions added

**`assay-activity`** — all UNRESOLVED (no universal default):
- `activity_cutoff_inhibition`, `activity_cutoff_fold_change`
- `hill_slope_min`, `hill_slope_max`, `r_squared_floor`

**`assay-screen-quality`** — cited from Zhang et al. 1999:
- `z_factor_excellent`: 0.5
- `z_factor_acceptable`: 0.0

## Relay codes added

- `assay.screen_quality_insufficient` — stop relay for Z < 0
- `assay.cytotoxicity_confound` — qualifier relay for bell-shaped curves

## Files changed

1. `tools/venter/commands/assay.py` — new file
2. `tools/venter/core/thresholds.py` — two threshold sets added
3. `tools/venter/core/provenance.py` — two relay codes added
4. `tools/venter/cli.py` — import and registration added

## Verification

- `py_compile` passes on all changed files
- `check_invocations.py`: 0 problems
- `check_artifact_paths.py`: 0 new problems (pre-existing `raw/pockets/` note)
- `check_threshold_names.py`: all citations resolve
- End-to-end test: canonical ingest produces artifact + sidecar
- Schema refusal: non-canonical schema exits 9
- Malformed data: missing fields detected and rejected
- Z-factor: excellent screen (0.90), unusable screen (-11.0) both classified correctly
- Bell-shaped curve detection: CPD002 with non-monotonic data correctly flagged
- `assay.screen_quality_insufficient` relay emitted for Z < 0
- `assay.cytotoxicity_confound` relay emitted for bell-shaped curves
- UNRESOLVED thresholds produce advisory messages, not crashes
