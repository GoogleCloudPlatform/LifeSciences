---
name: in-vivo-pk-analysis
description: >
  Compute non-compartmental PK parameters from concentration-time data,
  project human doses via allometric scaling, and predict drug-drug
  interactions from in vitro CYP inhibition data. Use when analyzing
  in vivo PK study results: computing Cmax, AUC, half-life, and
  clearance from a concentration-time profile, scaling animal PK to
  predicted human parameters, assessing DDI risk via the FDA/EMA basic
  static model, or evaluating exposure margins against program
  thresholds. Do not use for predicted ADMET endpoints from molecular
  descriptors (use admet-property-prediction), binding mode or docking
  (use binding-mode-analysis), or dose-response curve fitting and
  screen quality (use bioactivity-landscape).
---

## 1. When to use, and when not

Use this skill when you need to analyze in vivo pharmacokinetic data
or project human dose parameters. Entry points include:

- Computing NCA parameters (Cmax, Tmax, AUC, t1/2, CL, Vd) from a
  concentration-time profile.
- Scaling animal PK parameters to predicted human PK via allometric
  scaling -- single-species (published exponents) or multi-species
  (fitted log-log regression with rule-of-exponents classification).
- Predicting drug-drug interaction risk from in vitro CYP inhibition
  data using the FDA/EMA basic static R model.
- Evaluating exposure margins and scaling confidence against the
  `pk-parameters` threshold set.

**Do not use when:**

- You need predicted ADMET endpoints from molecular descriptors
  (metabolic stability class, predicted permeability, hERG structural
  flag) -> `admet-property-prediction`.
- You need binding affinity, docking scores, or pose interpretation
  -> `binding-mode-analysis`.
- You need dose-response curve fitting or screen quality assessment
  -> `bioactivity-landscape`.
- You need compound drug-likeness rules or structural alerts
  -> `compound-property-profile`.

## 2. Preconditions

- **Input (NCA path)**: a JSON file matching schema
  `dde.pk-study.v1` for `ingest`, containing species, route,
  dose, time points, and concentrations. At least 3 time points
  required.
- **Input (DDI path)**: a JSON file matching schema
  `dde.pk-ddi-input.v1` for `ddi`, containing compound_id,
  cmax_unbound, and per-isoform CYP inhibition data with Ki or
  IC50 values in matching units.
- **scipy/numpy**: recommended for numerical stability in linear
  regression (terminal phase fitting, allometric regression). The
  tool falls back to manual computation if unavailable.
- **Environment**: run `dde doctor` before first use. It ends
  with a verdict line: `STOP` means fix or report before running
  anything; `PROCEED` means work. Do not judge by the warning
  count; the verdict line grades them for you.
- **No authentication** needed -- all operations are local.
- **No network** -- all subcommands are offline.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Parse and validate a PK study | `dde pk ingest <input_file>` | `raw/pk/{study_id}.pk-study.json`<br>`raw/pk/{study_id}.pk-study.meta.json` |
| Compute NCA parameters | `dde pk nca <study_file>` | `raw/pk/{study_id}.pk-nca.json`<br>`raw/pk/{study_id}.pk-nca.meta.json` |
| Scale animal PK to human | `dde pk scale <nca_files...>` | `raw/pk/{compound_id}.pk-scaling.json`<br>`raw/pk/{compound_id}.pk-scaling.meta.json` |
| Predict DDI risk | `dde pk ddi <input_file>` | `raw/pk/{compound_id}.pk-ddi.json`<br>`raw/pk/{compound_id}.pk-ddi.meta.json` |
| Apply thresholds and produce verdict | `dde pk analyze <path>` | `raw/pk/{stem}.pk.analysis.json` |

Run phase 1 (`ingest`, `nca`, `scale`, `ddi`) before phase 2
(`analyze`). `analyze` reads from disk and can be re-run with
different thresholds without recomputing.

`nca` requires explicit `--blq-method` when BLQ values are present
-- this is never a silent default. `scale` accepts one or more NCA
files; single-species uses published exponents, multi-species fits
regression. `ddi` estimates Ki as IC50/2 when the input provides
IC50 (competitive inhibition assumption).

## 4. Interpretation contract

### Verdicts

`analyze` applies the `pk-parameters@1.0` threshold set. Verdicts
depend on the artifact type:

- **NCA analysis**: `flagged` (AUC extrapolation exceeded the
  configured `auc_extrapolation_limit`) or `acceptable`.
- **Scaling analysis**: `low_confidence` (single-species, where
  confidence is always `scaling_confidence_single`; or multi-species
  with CL exponent outside the simple allometry range) or
  `acceptable` (multi-species within the simple allometry range,
  confidence is `scaling_confidence_multi`).
- **DDI analysis**: `clinical_study_needed` (any isoform R value
  at or above `ddi_r_clinical_study`), `possible_interaction` (any
  isoform R at or above `ddi_r_possible_interaction`), or
  `acceptable`.

### Unresolved threshold

The `therapeutic_exposure_adequacy` threshold in `pk-parameters@1.0`
is UNRESOLVED -- no universal default exists. **Do not invent a
value.** A program-specific value must be set in
`.dde/thresholds.yaml` before exposure adequacy assessments can
be made. The analysis records this gap explicitly.

Thresholds are cited by name. The values in force are in the
artifact: every `.analysis.json` carries `threshold_set`,
`thresholds_applied`, `threshold_sources`, and
`threshold_provenance`. Quote values from the analysis being cited,
never from prose.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `pk.single_species_scaling` | Stop | `scale` with one species | Do not present this as a validated estimate. Single-species allometry has high uncertainty; the rule of exponents requires data from at least two species for reliable CL prediction. |
| `pk.rule_of_exponents_uncorrected` | Qualifier (conditional) | `scale` multi-species when CL exponent > 0.70 | State that the fitted exponent falls outside the simple allometry range and that a correction (MLP or brain weight) per Mahmood & Balian 1996 has not been applied. The predicted human CL may be less reliable. |

The following standing qualifiers were moved from mandatory relays to
`method_caveat` sidecar fields per the always-true rule
(tool-design-guidance section 5.1, exit 2). They appear in the
`.meta.json` as the `method_caveat` key, not as relay codes.
See #146.

- **NCA linearity**: NCA assumes dose-proportional exposure (linear
  PK). Recorded as `method_caveat` on every `nca` sidecar.
- **Allometric-vs-PBPK**: allometric scaling is an empirical
  correlation, not a mechanistic PBPK model. Recorded as
  `method_caveat` on every `scale` sidecar.
- **DDI static model**: the static R model is worst-case by design.
  Recorded as `method_caveat` on every `ddi` sidecar.

Check `mandatory_relays` in both the `.analysis.json` and in each
phase-1 `.meta.json`. Every relay code present must be satisfied
in the finding.

### Consequence rules

- **NCA flagged**: AUC extrapolation exceeds the configured limit.
  The AUC0-inf estimate may be unreliable -- state the extrapolation
  percentage and the threshold from the analysis. Consider whether
  the study design captured enough of the terminal phase.
- **Scaling low_confidence**: either single-species (published
  exponents, no regression possible) or multi-species with an
  exponent outside the simple allometry range. State the confidence
  class and method from the analysis. Do not present the predicted
  human parameters as validated.
- **DDI clinical_study_needed**: at least one isoform's R value
  meets or exceeds the clinical study threshold. Name the
  isoform(s) and state the R values from the analysis. This is a
  static worst-case model -- a clinical DDI study is the
  recommended next step, not a conclusion that a DDI exists.
- **DDI possible_interaction**: R values indicate possible
  interaction but do not cross the clinical study threshold. State
  the isoform(s) and R values.

### Cross-disciplinary consequences

- NCA parameters are computed from one study at one dose. They do
  not predict behaviour at other doses if PK is nonlinear.
- Allometric scaling is an empirical correlation, not a mechanistic
  model. PBPK modelling is a documented future extension and is not
  available.
- The static DDI model uses worst-case assumptions. A "possible
  interaction" result is a trigger for a clinical study, not
  evidence that a DDI will occur clinically.
- A clean NCA or scaling result does not substitute for clinical PK
  data -- these are preclinical estimates.
- DDI risk classification applies the `pk-parameters` threshold
  set. A classification under one program's thresholds is not
  comparable to one under different thresholds.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/`
that cites the `.analysis.json`, satisfies every mandatory relay
that fired, names the study or compound identifier, and never
substitutes preclinical estimates for clinical PK data. Everything
the tools emitted stays under `raw/pk/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the
tool emitted. If the tool did not run, there is no value. If a
required tool is unavailable, report the task as blocked. Do not
estimate, and do not proceed on an assumed result.

### Named pathologies

- **Treating single-species scaling as a validated prediction.**
  The `pk.single_species_scaling` relay is a Stop, not a Qualifier.
  Single-species allometry uses published average exponents with no
  regression fit -- confidence is inherently low.
- **Omitting the linearity assumption from NCA findings.** The
  `pk.nca_assumes_linearity` relay fires on every NCA run. NCA
  parameters are dose-specific if the compound shows nonlinear PK.
- **Treating "possible interaction" as "a DDI exists".** The static
  model is worst-case by design. The `pk.ddi_static_model` relay
  exists for this -- "possible interaction" means "do a clinical
  DDI study".
- **Inventing a therapeutic_exposure_adequacy value.** This
  threshold is UNRESOLVED. The analysis records the gap. Do not
  supply a plausible number -- the program must set one in
  `.dde/thresholds.yaml` with a recorded justification.
- **Mixing IV and non-IV NCA files in allometric scaling.** IV
  produces CL and Vd; non-IV produces CL/F and Vd/F. The tool
  refuses this, but do not attempt to combine them manually.
- **Ignoring BLQ handling decisions.** BLQ method is never a
  silent default -- the choice of exclude, zero, or loq_half
  affects computed PK parameters and must be explicit.
- **Computing NCA parameters yourself.** The tool pins the AUC
  method (linear-log trapezoidal), terminal phase detection, and
  BLQ handling. A manual calculation may use different algorithms.
  The finding must cite the tool's output, not a recalculation.

## 6. Provenance

All computations are local. NCA uses the linear-log trapezoidal
method for AUC. Allometric scaling uses published exponents for
single-species (Boxenbaum 1982) and fitted log-log regression with
rule-of-exponents classification (Mahmood & Balian 1996) for
multi-species. DDI prediction uses the FDA/EMA basic static model
(FDA 2020 In Vitro Drug Interaction Studies guidance). Thresholds
from `pk-parameters@1.0` carry full provenance in the threshold
set declaration.
