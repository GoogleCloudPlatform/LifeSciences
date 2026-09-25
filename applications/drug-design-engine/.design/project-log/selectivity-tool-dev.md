# Selectivity Tool Development Log

**Issue:** #52
**Branch:** `scion/tools-lead-em-52`
**Date:** 2026-08-19

## What was built

`tools/venter/commands/selectivity.py` — a two-phase command group for selectivity panel analysis:

### Phase 1: `selectivity compare`
- Accepts JSON matching canonical schema `venter.selectivity-panel.v1`
- Validates schema version: non-canonical input is a Refusal (exit 9)
- Validates required fields: compound_id, primary_target (name, activity_type, activity_value, activity_unit), off_targets array, optional panel_complete boolean
- Enforces measure-type consistency: refuses mixed IC50/Ki panels (exit 9) — computing a ratio between different measure types is misleading without Cheng-Prusoff correction
- Computes selectivity ratios: off_target_value / primary_value (higher = better selectivity)
- Writes Layer 0 artifact + `.meta.json` sidecar to `raw/assays/`
- Follows `assay.py` structural precedent (schema check, Sidecar, Emitter output, _slug helper)

### Phase 2: `selectivity analyze`
- Reads stored selectivity data, applies `selectivity-margins` threshold set
- hERG margin classified against ICH S7B ≥30-fold guideline:
  - adequate: ratio ≥ 30
  - marginal: ratio ≥ 10 but < 30
  - insufficient: ratio < 10
- All other off-target margins: UNRESOLVED (no universally citable conventions)
- Genuinely offline (phase-2 latch enforces automatically)

## Threshold definitions added

**`selectivity-margins`** (v1.0):
- `hERG_margin`: 30.0 — ICH S7B guideline figure

## ICH S7B Citation

ICH S7B, "The Non-Clinical Evaluation of the Potential for Delayed Ventricular Repolarization (QT Interval Prolongation) by Human Pharmaceuticals" (adopted 2005): recommends a ≥30-fold safety margin between the therapeutic free plasma concentration and the IC50 for hERG channel inhibition. This is the widely accepted regulatory standard.

## Relay codes added

- `selectivity.ratio_not_affinity` — claim-triggered relay, fires on every analysis. Obligation: a selectivity ratio from IC50 values inherits IC50's caveats as a measure of affinity; state the measure type and do not imply thermodynamic precision.
- `selectivity.panel_incomplete` — defect-triggered relay, fires when `panel_complete` is not true. Obligation: scope the selectivity claim to the off-targets actually tested; name them and do not generalise.

## Files changed

1. `tools/venter/commands/selectivity.py` — new file (complete implementation)
2. `tools/venter/core/thresholds.py` — `selectivity-margins` threshold set added
3. `tools/venter/core/provenance.py` — two relay codes added
4. `tools/venter/cli.py` — import and registration added (before `enforce_phase_two`)

## Verification

- `check_invocations.py`: 0 problems
- `check_artifact_paths.py`: 0 new problems (pre-existing `raw/pockets/` note)
- `check_threshold_names.py`: 0 new problems (pre-existing `hill_slope` note)
- End-to-end test: canonical compare produces artifact + sidecar with correct ratios
- Schema refusal: non-canonical schema exits 9
- Mixed measures: IC50/Ki mix exits 9 with clear explanation
- hERG adequate: 566.7x ratio → "adequate" (> 30)
- hERG marginal: 20x ratio → "marginal" (≥ 10, < 30)
- hERG insufficient: 5x ratio → "insufficient" (< 10)
- `selectivity.ratio_not_affinity` relay emitted on every analysis
- `selectivity.panel_incomplete` relay emitted when panel_complete is false/absent
- `selectivity.panel_incomplete` relay NOT emitted when panel_complete is true
- UNRESOLVED off-target margins reported without classification
- Analysis JSON structure includes ICH S7B citation in threshold_source
