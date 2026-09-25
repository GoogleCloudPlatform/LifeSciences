# ADMET Prediction Tool Implementation

**Date:** 2026-08-19
**Issue:** #50
**Branch:** scion/tools-lead-em-50

## What Was Implemented

Five-endpoint ADMET prediction tool (`venter admet predict` / `venter admet analyze`)
using rule-based models from published literature. All predictions are from
molecular descriptors and SMARTS pharmacophore patterns — no trained ML models.

### Files Created/Modified

1. **`tools/venter/commands/admet.py`** (new) — main command module with `predict`
   (phase 1) and `analyze` (phase 2) subcommands.
2. **`tools/venter/core/context.py`** — added `"admet": "raw/admet"` to `ARTIFACT_DIRS`.
3. **`tools/venter/core/provenance.py`** — registered two relay codes:
   `admet.prediction_not_measurement` and `admet.herg_structural_flag`.
4. **`tools/venter/core/thresholds.py`** — added `admet-endpoints` threshold set (v1.0).
5. **`tools/venter/cli.py`** — wired `admet` command group (before `enforce_phase_two`).

### Five ADMET Endpoints

| Endpoint | Model | Source |
|---|---|---|
| Metabolic Stability | LogP + aromatic ring count → CLint class | Gleeson, J Med Chem 2008;51:817-834 |
| CYP Inhibition Risk | SMARTS pharmacophore + descriptor rules per isoform | Gleeson 2008 (CYP2D6); general rules (CYP3A4, CYP2C9) |
| Permeability | Egan egg model (TPSA + LogP) | Egan et al., J Med Chem 2000;43:3867-3877 |
| hERG Liability | Basic nitrogen + LogP > 3.7 + aromatic rings >= 2 | Aronov, Drug Discov Today 2005;10:149-155 |
| Solubility (ESOL) | Delaney equation: LogS = 0.16 - 0.63·cLogP - 0.0062·MW + 0.066·RB - 0.74·AP | Delaney, J Chem Inf Comput Sci 2004;44:1000-1009 |

## Relay Design Reasoning

### `admet.prediction_not_measurement` — Conditional STOP relay

Fires only when the overall ADMET profile is favorable (verdict = "developable").
This is when the over-read risk is highest: someone treating "predicted: no
liabilities" as equivalent to "established: no liabilities" and skipping in vitro
ADMET. When liabilities are flagged, the reader is already alarmed. Follows the
pattern of `fpocket.druggability_is_not_affinity` (fires on favorable verdict)
and `compound.alerts_not_toxicology` (fires when clean).

### `admet.herg_structural_flag` — Conditional QUALIFIER relay

Fires when hERG structural features are detected. Names the specific triggering
features (basic nitrogen, LogP, aromatic rings) and states that this is a
pharmacophore-based prediction, not a measured IC50. Explicit about the documented
false-negative rate of rule-based hERG prediction.

## Threshold Set: `admet-endpoints@1.0`

All thresholds have cited sources except `permeability_mw_max` (500.0), which uses
the Lipinski MW cutoff as a permeability penalty but has no specific published
source for that application to permeability. None are marked UNRESOLVED — each has
at least one defensible source from the literature.

## Live Verification Results

All tests passed:

1. **Aspirin** (`CC(=O)Oc1ccccc1C(=O)O`): Low metabolic liability, no CYP flags,
   high permeability, no hERG flag, moderate solubility (LogS = -1.992). Analyze
   verdict: "developable" with `prediction_not_measurement` relay.

2. **Terfenadine** (known hERG liability, withdrawn from market): High metabolic
   liability, CYP2D6 + CYP3A4 flagged, hERG flagged with specific features
   (basic nitrogen, LogP 6.96, 3 aromatic rings), insoluble (LogS = -7.025).
   Analyze verdict: "liabilities-identified" with `herg_structural_flag` relay
   naming all triggering features.

3. **Malformed SMILES** (`not-a-smiles`): Exit code 9 (Refusal) as expected.

4. **hERG flag specificity**: Fires on terfenadine (correct — known hERG blocker),
   does not fire on aspirin (correct — no hERG concern).

5. **Relay codes**: `admet.prediction_not_measurement` fires only on favorable
   verdict (aspirin). `admet.herg_structural_flag` fires only when hERG features
   detected (terfenadine). Both are detected by `venter relays` and attributed
   to `admet analyze`.
