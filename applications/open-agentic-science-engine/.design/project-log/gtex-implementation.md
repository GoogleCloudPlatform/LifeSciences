# GTEx Whole-Blood Expression Tool — Implementation Log

**Date**: 2026-08-19
**Issue**: #30 — Add peripheral blood coverage to expression tool or document gap
**Agent**: tools-lead-em-30-dev-gtex

## What was built

`venter gtex` — a two-phase command group for GTEx whole-blood median gene expression:

- **`gtex fetch <gene>`**: Resolves gene via GTEx `/reference/gene`, fetches whole-blood median expression from `/expression/medianGeneExpression`, writes Layer 0 artifacts to `raw/gtex/`.
- **`gtex analyze <gene>`**: Reads from disk, reports the median TPM with the tissue caveat relay. Genuinely offline (enforced by `enforce_phase_two`).

## Files changed

| File | Change |
|---|---|
| `tools/venter/commands/gtex.py` | New command module (fetch + analyze) |
| `tools/venter/core/context.py` | Added `"gtex": "raw/gtex"` to `ARTIFACT_DIRS` |
| `tools/venter/cli.py` | Imported and registered `gtex` command |
| `tools/venter/core/provenance.py` | Registered `gtex.whole_blood_is_not_peripheral_blood` relay code |

## Design decisions

### Relay vs sidecar field (Option A chosen)

The whole-blood-is-not-peripheral-blood caveat fires on every GTEx query — it is a standing property of the data source. Per tool-design-guidance.md §5.1, an unconditional relay is usually a smell. However, the relay was chosen because:

1. `venter relays` enumerates it mechanically — reviewers and skill authors can name the code.
2. The obligation is specific and actionable: it changes how the claim is worded.
3. A sidecar field nobody checks is a worse failure mode than a relay that is always present.

### No threshold set

GTEx publishes no expression-level cutoffs analogous to HPA's "detected at nTPM >= 1". The raw median TPM is reported without classification. Inventing a cutoff would put an uncited number behind a claim about a gene. The analysis records `threshold_set: "gtex@none"` to make this absence explicit.

## Verification

- **check_invocations.py**: 0 problems (122 invocations checked)
- **check_artifact_paths.py**: 0 new problems (1 pre-existing in pocket skill)
- **check_threshold_names.py**: 0 new problems (1 pre-existing in bioactivity skill)
- **Live API test**: TP53 (7.6981 TPM), SPDYA (0.2497 TPM), WEE1 (0.8747 TPM) — all match verified values from the brief.
- **Analyze test**: Both TP53 and SPDYA analyzed successfully from disk with relay attached.

## API details confirmed

- Base URL: `https://gtexportal.org/api/v2/`
- No authentication required
- Dataset: `gtex_v8`
- 200-with-empty-data pattern handled (same as gnomAD)
- Gene search is exact-match only
