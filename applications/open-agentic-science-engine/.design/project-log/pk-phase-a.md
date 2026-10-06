# PK Phase A: `ingest` and `nca` subcommands (#90)

**Date:** 2026-08-19
**Author:** tools-lead-em-90-dev-a1

## What was built

### Infrastructure registrations
- `tools/venter/core/context.py`: Added `"pk": "raw/pk"` to `ARTIFACT_DIRS`
- `tools/venter/core/provenance.py`: Added 4 relay codes:
  - `pk.allometric_not_pbpk` (for Phase B `pk scale`)
  - `pk.nca_assumes_linearity` (fires on every NCA run)
  - `pk.ddi_static_model` (for Phase B `pk ddi`)
  - `pk.single_species_scaling` (for Phase B `pk scale`)
- `tools/venter/core/thresholds.py`: Added `pk-parameters` threshold set with
  scaling confidence, AUC extrapolation limit (20%, FDA 2020), DDI R-value
  cutoffs (FDA 2020), and `therapeutic_exposure_adequacy` = UNRESOLVED
- `tools/venter/cli.py`: Registered `pk` command group

### `pk ingest` subcommand
- Validates `venter.pk-study.v1` schema
- Validates time/concentration units against recognised sets
- Enforces monotonically increasing time points, minimum 3 points
- Records BLQ marker value but does not decide handling method
- Writes `{study_id}.pk-study.json` + `.meta.json` sidecar

### `pk nca` subcommand
- Non-compartmental analysis: Cmax, Tmax, AUC (linear-log trapezoidal),
  terminal half-life, lambda_z, AUC0-inf, CL, Vd
- BLQ handling is a required `--blq-method` parameter (never silent default)
- Refuses if BLQ values exist and `--blq-method` not specified (exit 9)
- Fires `pk.nca_assumes_linearity` relay on every run
- Reports bioavailability as "not determinable (single study)"
- Writes `{study_id}.pk-nca.json` + `.meta.json` sidecar

## What was NOT built (Phase B scope)
- `pk scale` (allometric scaling)
- `pk ddi` (static DDI prediction)
- `pk analyze` (phase-2 threshold application)

## Verification results

### Checker scripts

```
$ python3 tools/check_invocations.py
checked 156 venter invocation(s) across docs, skills, templates, README.md
0 problem(s)

$ python3 tools/check_artifact_paths.py
artifact classes declared in core/context.py: 13
  written by some command: admet, analogs, assays, compounds, docking, expression, genomics, gtex, hypotheses, literature, mpo, pk, structures
0 problem(s), 0 note(s)

$ python3 tools/check_threshold_names.py
1 problem(s):
  skills/bioactivity-landscape/SKILL.md:108: cites `hill_slope` (pre-existing, not our code)
pk-parameters thresholds correctly declared

$ python3 tools/check_relay_codes.py
3 problem(s):
  pk.allometric_not_pbpk — no emission site (expected: Phase B pk scale)
  pk.ddi_static_model — no emission site (expected: Phase B pk ddi)
  pk.single_species_scaling — no emission site (expected: Phase B pk scale)
pk.nca_assumes_linearity correctly detected as emitted by pk nca
```

The 3 relay code warnings are expected — those codes are registered for Phase B
subcommands that are explicitly out of scope for this task.

### Live NCA validation

Test input: synthetic one-compartment IV bolus, C(t) = 1000 * exp(-0.231049 * t)
- Known half-life: 3.0 h, lambda_z = ln(2)/3 = 0.231049
- Analytical AUC0-inf = C0/lambda_z = 4328.0851 ng/mL*h

Results:
| Parameter | Computed | Expected | Error |
|-----------|----------|----------|-------|
| Half-life | 3.0004 h | 3.0000 h | 0.01% |
| Lambda_z  | 0.231015 | 0.231049 | 0.01% |
| Cmax      | 1000.0   | 1000.0   | exact |
| Tmax      | 0.0 h    | 0.0 h    | exact |
| AUC0-inf  | 4328.17  | 4328.09  | 0.002% |
| R-squared | 1.000000 | 1.0      | exact |

### BLQ handling verification
- NCA without `--blq-method` on data with BLQ values: correctly refused (exit 9)
- NCA with `--blq-method exclude`: correctly processed, 1 BLQ point excluded
- Sidecar contains `pk.nca_assumes_linearity` mandatory relay

### CLI registration
- `venter pk --help` shows both `ingest` and `nca` subcommands
