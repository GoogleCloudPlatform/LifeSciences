# PK DDI Threshold Fix

**Date:** 2026-08-19
**Issue:** #90
**Branch:** scion/tools-lead-em-90

## Problem

`_classify_ddi_risk()` in `pk.py` hardcoded R-value classification boundaries (1.25, 2.0) as inline literals instead of reading from the pk-parameters threshold set. This violated tool-design-guidance.md §7 and caused a real disagreement: when a program overrode `ddi_r_possible_interaction` to 1.5 via `.venter/thresholds.yaml`, `pk ddi` still used the hardcoded 1.25 cutoff while `pk analyze` correctly used 1.5. Same compound, two different answers.

Additionally, `ddi_r_no_interaction` (1.02) was declared in the pk-parameters threshold set but never used in any classification logic. The FDA 2020 guidance uses 3 tiers, not 4.

## Changes

### Fix A: `_classify_ddi_risk()` now accepts threshold parameters
- Changed function signature to accept `r_possible` and `r_clinical` instead of using hardcoded 1.25 and 2.0
- Classification logic now uses the passed-in threshold values

### Fix B: `ddi_cmd` loads the threshold set
- Added `load_thresholds(state, "pk-parameters")` call before the isoform loop
- Passes `r_possible` and `r_clinical` to `_classify_ddi_risk()`
- Added `thresholds_applied` dict to the DDI output record for provenance

### Fix C: Removed `ddi_r_no_interaction`
- Removed the 1.02 threshold from `thresholds.py` pk-parameters values
- Added comment explaining 3-tier classification per FDA 2020 guidance
- Removed `r_no_interaction` loading and recording from `_analyze_ddi()`

## Verification

- Syntax checks pass for both `pk.py` and `thresholds.py`
- AST verification confirms no hardcoded 1.25/2.0 in `_classify_ddi_risk`
- `ddi_r_no_interaction` fully removed from both files
- `check_artifact_paths.py` passes cleanly
- `check_threshold_names.py` has 1 pre-existing issue (unrelated `hill_slope` in bioactivity-landscape skill)

## Files Changed

- `tools/venter/commands/pk.py` — Fix A, Fix B, Fix C (analyze side)
- `tools/venter/core/thresholds.py` — Fix C (threshold declaration)
