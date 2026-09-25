---
name: bioactivity-landscape
description: >
  Ingest screening data, assess dose-response quality, and compute
  selectivity margins — activity cutoffs, 4PL/Hill curve fitting,
  Z-factor screen quality, bell-shaped curve detection, and selectivity
  ratio computation with margin classification. Use when interpreting
  HTS screening results, evaluating dose-response curves, checking
  screen quality from control wells, detecting non-monotonic response
  patterns, ingesting assay data, or evaluating target selectivity
  from IC50 or Ki panels. Do not use for compound properties or
  structural alerts (use compound-property-profile), docking or binding
  mode (use binding-mode-analysis), or genetic constraint (use
  target-genetic-evidence).
---

## 1. When to use, and when not

Use this skill when you need to ingest assay data and interpret
screening or dose-response results. Entry points include:

- Interpreting HTS screening results — hit calling from plate-based
  readout data (percent inhibition or fold change).
- Evaluating dose-response quality — 4PL/Hill curve fitting, Hill slope
  assessment, and goodness-of-fit checks.
- Checking screen quality — Z-factor validation from control wells to
  determine whether the assay window separates signal from noise.
- Detecting cytotoxicity confounds — bell-shaped (non-monotonic)
  dose-response curves that suggest compound toxicity at high
  concentrations.
- Ingesting canonical assay data into Layer 0 artifacts for downstream
  analysis.
- Evaluating selectivity margins — computing IC50/Ki ratios between
  primary and off-targets from a selectivity panel.
- Checking whether a selectivity panel is complete enough to support a
  selectivity claim.

**Do not use when:**

- You need computed molecular properties or structural alerts (PAINS,
  Brenk, aggregator) -> `compound-property-profile`.
- You need docking scores, binding pose, or binding mode interpretation
  -> `binding-mode-analysis`.
- You need genetic constraint or loss-of-function intolerance
  -> `target-genetic-evidence`.
- You need fold confidence or domain boundaries
  -> `protein-structure-confidence`.
- You need pocket druggability
  -> `pocket-druggability`.

## 2. Preconditions

- **Input**: a canonical assay JSON file (schema `dde.assay.v1`) with
  a top-level `schema` field and a `data` array. Each data point
  requires: `well_id`, `compound_id`, `readout_value`, `readout_type`
  (`percent_inhibition` or `fold_change`), `assay_type`, `plate_id`,
  `run_id`, `concentration`.
- **readout_value** must be numeric and finite.
- **concentration** must be a number or a dict with `value`/`unit` keys.
- **Maximum** 500,000 data points per file.
- **Control wells** (`POS_CTRL`, `NEG_CTRL`, `POS`, `NEG`, `DMSO`,
  `POSITIVE_CONTROL`, `NEGATIVE_CONTROL`) are used for Z-factor
  calculation but excluded from compound analysis.
- **scipy** required for dose-response curve fitting. Run `dde doctor`
  before first use — its verdict line (`STOP` or `PROCEED`) is the gate.
- **No authentication** needed — all operations are offline.
- **Selectivity input**: a canonical selectivity panel JSON (schema
  `dde.selectivity-panel.v1`) with `compound_id`, `primary_target`,
  and `off_targets` array (max 10,000). Per target: `name`,
  `activity_type` (IC50 or Ki, must be uniform — mixed panels refused),
  `activity_value` (positive numeric, nM), `activity_unit`. Optional
  `panel_complete` boolean (default false).

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Ingest and validate assay data | `dde assay ingest <ASSAY_FILE>` | `raw/assays/<name>.assay.json`<br>`raw/assays/<name>.meta.json` |
| Analyze screen quality, activity, and dose-response | `dde assay analyze <ARTIFACT>` | `raw/assays/<stem>.analysis.json` |
| Compute selectivity ratios from a panel | `dde selectivity compare <PANEL_FILE>` | `raw/assays/<slug>.selectivity.json`<br>`raw/assays/<slug>.meta.json` |
| Apply margin thresholds to stored selectivity data | `dde selectivity analyze <ARTIFACT>` | `raw/assays/<slug>.analysis.json` |

Run `ingest` before `analyze`. `analyze` reads stored data from disk
and can be re-run with different thresholds without re-ingesting.

The `<name>` stem is derived from `assay_type` and `run_id` in the
input data. The `<ARTIFACT>` for `analyze` is the `.assay.json`
produced by `ingest`.

For selectivity, run `compare` before `analyze`. The `<ARTIFACT>` for
`analyze` is the `.selectivity.json` produced by `compare`.

## 4. Interpretation contract

### Analysis structure

Unlike tools that produce a single verdict, `assay analyze` produces a
multi-component analysis:

- **Screen quality**: `quality_verdict` — one of `excellent`,
  `acceptable`, `unusable`, or `unknown` — based on Z-factor computed
  from control wells. The Z-factor thresholds come from the
  `assay-screen-quality@1.0` threshold set: cite `z_factor_excellent`
  and `z_factor_acceptable` by name. Provenance: Zhang, Chung &
  Oldenburg, J Biomol Screen 1999;4(2):67-73.
- **Per-compound records**: `active` (boolean, present only when the
  activity cutoff threshold is resolved), `curve_fit` (4PL/Hill
  parameters — `bottom`, `top`, `ec50`, `hill_slope`, `r_squared`,
  `n_points` — or null when fitting fails or too few concentrations
  exist), `bell_shaped` (boolean), `advisories` (list of strings
  including any skipped-check notices).
- **Overall metrics** (in `metrics`): `n_compounds_analyzed`,
  `n_curves_fitted`, `n_bell_shaped`.
- **Assessment summary** (in `assessment`): `n_compounds_flagged`
  (compounds with any advisory), `screen_quality_verdict`.

Interpret each component for what it measures. Do not collapse this
structure into a single pass/fail verdict.

### Selectivity analysis structure

`selectivity analyze` produces per-off-target records: `off_target`,
`selectivity_ratio`, `activity_type`, `classification` (`adequate`,
`marginal`, `insufficient`, or `UNRESOLVED`). hERG: classified against
`selectivity-margins@1.0` — adequate (>= `herg_margin`), marginal (>=
`herg_marginal` but < `herg_margin`), insufficient (< `herg_marginal`).
Cite by name; quote from `thresholds_applied`. Non-hERG: always
UNRESOLVED — no citable conventions exist. **Do not invent a margin
threshold.** Assessment: `n_classified`, `n_adequate`, `n_marginal`,
`n_insufficient`, `n_unresolved`. No single overall verdict.

### UNRESOLVED thresholds — do not invent

All five thresholds in the `assay-activity@1.0` set are UNRESOLVED.
They are program-specific cutoffs with no universal default. The tool
skips each check when the threshold is UNRESOLVED and emits an advisory
on the per-compound record:

- `activity_cutoff_inhibition` — **UNRESOLVED**. Do not invent a value.
- `activity_cutoff_fold_change` — **UNRESOLVED**. Do not invent a value.
- `hill_slope_min` — **UNRESOLVED**. Do not invent a value.
- `hill_slope_max` — **UNRESOLVED**. Do not invent a value.
- `r_squared_floor` — **UNRESOLVED**. Do not invent a value.

The operator must set these in `.dde/thresholds.yaml` with a
recorded justification. When a threshold is UNRESOLVED, the
corresponding check does not run and no activity classification is
made. The absence of a check is not permission to call a compound
active or inactive — it means the question is unanswered until the
program sets the cutoff. Do not supply a value the tool declined to
publish; do not substitute a common convention.

The `assay-screen-quality@1.0` thresholds (`z_factor_excellent`,
`z_factor_acceptable`) are resolved. Cite by name; quote values from
`thresholds_applied` in the analysis being cited.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `assay.screen_quality_insufficient` | Stop (defect-triggered) | Z-factor falls below `z_factor_acceptable` — the signal and background distributions overlap | Do not trust active/inactive classifications from this screen. The assay window is too narrow to distinguish signal from noise. The screen itself is the problem, not the compounds. Report the screen as unusable and do not carry forward compound activity calls from it. |
| `assay.cytotoxicity_confound` | Qualifier (defect-triggered) | One or more compounds show a bell-shaped (non-monotonic) dose-response curve | Note the bell-shaped curve and carry the qualifier that the activity measurement may be confounded by compound cytotoxicity at high concentrations. Do not report the fitted EC50/IC50 without this qualifier. The per-compound advisories identify which compounds are affected. |
| `selectivity.ratio_not_affinity` | Stop (claim-triggered) | Always fires on every selectivity analysis | A selectivity ratio from IC50/Ki values inherits assay-dependent variability. Do not equate it to a thermodynamic binding constant ratio. Report the ratio and its measure type; do not claim the compound "has X-fold selectivity" without this qualifier. |
| `selectivity.panel_incomplete` | Qualifier (defect-triggered) | `panel_complete` is false (conditional) | The selectivity claim is bounded by the targets tested. Do not generalize beyond the panel. Name the off-targets that were included. |

Check `mandatory_relays` in both the `.analysis.json` and the
`.meta.json`. Every relay code present must be satisfied in the finding.

### Consequence rules

- **Screen quality excellent or acceptable**: state the Z-factor, the
  quality verdict, and the threshold set applied. Compound activity
  classifications from this screen may be used downstream.
- **Screen quality unusable**: the `assay.screen_quality_insufficient`
  relay fires. Do not report compound activity verdicts from this
  screen — the assay window cannot separate signal from noise.
- **Screen quality unknown**: Z-factor could not be calculated
  (insufficient control wells — fewer than 2 positive or 2 negative
  controls). State this; do not assume quality is acceptable.
- **Bell-shaped curves detected**: the `assay.cytotoxicity_confound`
  relay fires. Carry the qualifier on every affected compound. A
  dose-response that rises then falls at high concentrations suggests
  cytotoxicity is masking the primary pharmacological effect.
- **Curve fit present**: report the 4PL/Hill parameters from the
  analysis. Do not refit curves yourself.
- **UNRESOLVED activity cutoffs**: when the tool reports that a cutoff
  check was skipped because the threshold is UNRESOLVED, carry this
  forward. The compound has no activity classification — do not
  infer one.
- **hERG adequate**: state ratio, classification, and threshold set.
- **hERG marginal**: between `herg_marginal` and `herg_margin`; not
  clearly safe but not yet insufficient — flag for attention.
- **hERG insufficient**: below `herg_marginal`; safety concern, escalate.
- **Non-hERG UNRESOLVED**: report ratio; do not invent a margin.
- **Panel incomplete**: carry `selectivity.panel_incomplete` qualifier.

### Cross-disciplinary consequences

- A compound that scores active may also carry PAINS structural alerts.
  Cross-reference `compound-property-profile` when evaluating hits — a
  PAINS-positive active hit warrants investigation of whether the
  readout reflects genuine activity or assay interference.
- Z-factor varies by plate and run. Do not compare activity values
  across runs without checking that both screens meet quality thresholds.
- Dose-response parameters (EC50, Hill slope) from different assay
  types or readout types are not directly comparable.
- Adequate selectivity does not exclude ADMET liabilities.
- Selectivity ratios across activity types (IC50 vs Ki) are not
  comparable without Cheng-Prusoff correction.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies every mandatory relay that fired,
names the screen quality verdict, and carries UNRESOLVED-threshold
advisories through unchanged. Everything the tools emitted stays under
`raw/assays/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Compound fluorescence interference.** Fluorescent compounds produce
  artefactually high readout values that mimic activity. The tool does
  not detect this — it is a data-quality issue upstream of ingestion.
- **Bell-shaped dose-response (cytotoxicity confound).** The
  `assay.cytotoxicity_confound` relay exists for this. Do not report
  a fitted EC50 without the cytotoxicity qualifier.
- **PAINS-hit overlap.** A compound that scores active may also have
  PAINS alerts (`compound-property-profile`). Cross-reference when
  evaluating hits.
- **Treating UNRESOLVED activity cutoffs as "no cutoff".** When the
  threshold is UNRESOLVED, the tool skips the check and emits an
  advisory. The absence of a check is not permission to classify a
  compound. Do not supply a value the tool declined to publish.
- **Z-factor from too few controls.** The tool requires at least 2
  positive and 2 negative controls. Below that, Z-factor is null and
  quality verdict is `unknown`. Do not treat unknown as acceptable.
- **Comparing activity across runs without checking screen quality.**
  Z-factor varies by plate and run. Check screen quality before
  comparing activity calls across screens.
- **Computing dose-response fits yourself instead of using the tool.**
  The tool pins the curve-fitting algorithm and bell-shaped detection
  logic. A fit computed outside the tool may use a different model or
  convergence criterion. Cite the tool's output, not a recalculation.
- **Comparing ratios across IC50 and Ki.** The tool refuses mixed
  panels, but upstream data may have been harmonized incorrectly.
- **Treating UNRESOLVED non-hERG margins as "no concern."** UNRESOLVED
  means no threshold to evaluate against — not clearance.
- **Generalizing from an incomplete panel.** The
  `selectivity.panel_incomplete` relay fires when `panel_complete` is
  false. Three off-targets do not cover the hundreds not tested.
- **Equating a selectivity ratio to binding selectivity.** The
  `selectivity.ratio_not_affinity` relay exists for this. An IC50
  ratio is assay-dependent; a Kd ratio is thermodynamic.
