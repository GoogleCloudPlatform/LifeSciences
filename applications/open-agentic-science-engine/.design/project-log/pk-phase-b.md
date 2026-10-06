# PK Phase B — scale, ddi, analyze subcommands

**Date:** 2026-08-19
**Issue:** #90
**Branch:** `scion/tools-lead-em-90`
**Author:** tools-lead-em-90-dev-b1

## What was built

Three new subcommands added to `tools/venter/commands/pk.py` (Phase A had ingest + nca at 719 lines; Phase B brings the file to ~1690 lines):

### `pk scale` — Allometric scaling

- Accepts one or more `.pk-nca.json` files (one per species)
- **Single species:** uses published average exponents (CL: 0.75, Vd: 1.0 per Boxenbaum 1982). Fires `pk.single_species_scaling` stop relay. Confidence class: `low`.
- **Multi-species (≥2):** fits log-log regression (`log(Y) = log(a) + b*log(BW)`) to derive species-specific exponents. Applies rule of exponents (Mahmood & Balian 1996). Fires `pk.allometric_not_pbpk` qualifier relay. Confidence class: `moderate`.
- Body weight resolution: NCA doc → companion study file → published reference weights
- Derives half-life: `t½ = 0.693 × Vd / CL`
- Optional dose projection via `--target-auc` or `--target-cmax`
- Outputs: `{compound_id}.pk-scaling.json` + `.pk-scaling.meta.json`

### `pk ddi` — Static DDI prediction

- Implements FDA/EMA basic static model: `R = 1 + [I]max,u / Ki`
- IC₅₀ → Ki conversion: `Ki = IC₅₀ / 2` (competitive inhibition assumption)
- Input schema: `venter.pk-ddi-input.v1` with full validation
- Unit mismatch between `cmax_units` and inhibition `units` is refused (no silent conversion)
- DDI risk classification per FDA 2020 guidance:
  - R < 1.25 → no clinically significant interaction likely
  - 1.25 ≤ R < 2.0 → possible interaction
  - R ≥ 2.0 → clinical DDI study recommended
- Fires `pk.ddi_static_model` qualifier relay on every run
- Outputs: `{compound_id}.pk-ddi.json` + `.pk-ddi.meta.json`

### `pk analyze` — Phase 2 threshold application

- Accepts `.pk-nca.json`, `.pk-scaling.json`, or `.pk-ddi.json` (auto-detected from schema tag)
- Loads `pk-parameters` threshold set via `load_thresholds()`
- NCA analysis: checks AUC extrapolation against 20% limit
- Scaling analysis: reports confidence class
- DDI analysis: re-classifies R values using threshold set values
- `therapeutic_exposure_adequacy` remains UNRESOLVED — noted in assessment, not invented
- Collects upstream relays from `.meta.json` sidecars
- Follows `docking.py analyze_cmd` pattern: uses `beside_or_out`, `from_option`, `provenance.write_analysis()`

## Design decisions

1. **Body weight resolution ladder** — NCA output doesn't include body weight, so `pk scale` looks up the companion study file, then falls back to published reference weights. This avoids modifying Phase A's `nca` output format.

2. **PBPK not implemented** — left a code comment noting PBPK as a documented future extension point, per the design doc. Only allometric/inline scaling is available.

3. **DDI classification in two places** — the `ddi` command classifies using FDA guidance constants inline; the `analyze` command re-classifies using the `pk-parameters` threshold set. This allows program-specific overrides via `.venter/thresholds.yaml` to take effect at analysis time.

4. **Therapeutic exposure adequacy** — stays UNRESOLVED. The analyze command records the gap explicitly in the assessment output. Calling `thresholds.get()` would raise `ThresholdError`, which is the correct behavior.

## Infrastructure reused (not modified)

- Relay codes: `pk.allometric_not_pbpk`, `pk.single_species_scaling`, `pk.ddi_static_model`, `pk.nca_assumes_linearity` — all registered in Phase A
- Threshold set: `pk-parameters` in `thresholds.py` — registered in Phase A
- Artifact dir: `raw/pk` in `context.py` — registered in Phase A
- `pk` command group in `cli.py` — registered in Phase A

## Verification results

All tests run via standalone Python verification (no click dependency in environment):

| Test | Result |
|------|--------|
| Syntax (ast.parse) | ✓ |
| Single-species scaling (published exponents, low confidence) | ✓ |
| Single-species relay (pk.single_species_scaling stop) | ✓ |
| Multi-species scaling (log-log regression, moderate confidence) | ✓ |
| Multi-species relay (pk.allometric_not_pbpk qualifier) | ✓ |
| DDI R-value computation (R = 1 + [I]/Ki) | ✓ |
| DDI IC50→Ki conversion (Ki = IC50/2) | ✓ |
| DDI risk classification boundary: R < 1.25 → no significant | ✓ |
| DDI risk classification boundary: 1.25 ≤ R < 2.0 → possible | ✓ |
| DDI risk classification boundary: R ≥ 2.0 → clinical study | ✓ |
| DDI relay (pk.ddi_static_model qualifier) | ✓ |
| All 8 boundary test cases | ✓ |

### Gates not run

- Full CLI integration tests (no `click` module available in environment)
- `venter pk scale/ddi/analyze` end-to-end invocation (no package install capability)
- These are environment limitations, not test failures
