# PK Phase B Review Fixes

**Date:** 2026-08-19
**Branch:** scion/tools-lead-em-90
**Commit:** pk: fix Phase B review findings (#90)

## Summary

Applied 7 review findings to `tools/venter/commands/pk.py` addressing
validation gaps, honest labeling, and input sanitization in the Phase B
subcommands (pk scale, pk ddi, pk analyze).

## Fixes Applied

### Fix 1: Route consistency validation
After loading NCA files in `scale_cmd`, check that all species used the same
route type (IV vs non-IV). IV NCA produces CL/Vd while non-IV produces CL/F
and Vd/F — mixing these in a single regression is pharmacokinetically invalid.

### Fix 2: Unit consistency validation
After loading NCA files, verify all clearance units match and all Vd units
match across species. Mismatched units would produce nonsensical allometric
regression.

### Fix 3: Rule of exponents — honest labeling
The code previously set `scaling_method` to `rule_of_exponents_mlp` or
`rule_of_exponents_brain_weight` when CL exponent exceeded thresholds, but
never actually applied MLP or brain weight corrections — the prediction
formula was always simple allometry. Fixed by:
- Always using `simple_allometry` as `scaling_method`
- Adding `rule_of_exponents_class` field (`mlp_recommended` or
  `brain_weight_recommended`) to `scaling_details`
- Adding a warning to both the output record and the provenance sidecar
- Setting `confidence_class` to `"low"` (not `"moderate"`) when exponent > 0.70

### Fix 4: CL/Vd positivity validation
Added validation in `_extract_pk_for_scaling` that clearance and Vd values
are positive before attempting allometric scaling (log of zero/negative would
fail or produce nonsense).

### Fix 5: Path traversal prevention
Applied `_sanitize_id()` to the `study_id` read from NCA docs in
`_get_body_weight`, preventing path traversal via a malicious study_id in a
hand-crafted NCA file.

### Fix 6: --human-bw positivity validation
Added early validation that `--human-bw` is positive. A zero or negative
body weight would produce division-by-zero or nonsensical scaling results.

### Fix 7: Half-life units from NCA input
Changed `half_life_units` in the scaling output from hardcoded `"h"` to the
value read from the first species' NCA document
(`doc["parameters"]["half_life_units"]`), falling back to `"h"` if not found.

## Verification

- `ast.parse()` syntax check: PASS
- `check_artifact_paths.py`: PASS (0 problems)
- `check_threshold_names.py`: 1 pre-existing problem in
  `skills/bioactivity-landscape/SKILL.md` (unrelated to this change)
