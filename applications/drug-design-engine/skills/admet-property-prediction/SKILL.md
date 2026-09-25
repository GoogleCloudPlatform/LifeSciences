---
name: admet-property-prediction
description: >
  Predict ADMET endpoint classifications from molecular descriptors —
  metabolic stability, CYP inhibition risk, permeability, hERG
  liability, and solubility. Use when assessing a compound's predicted
  ADMET profile before in vitro studies: identifying which endpoints
  may need early experimental attention, flagging structural features
  associated with hERG liability, or screening predicted solubility
  and permeability for a compound series. Do not use for measured ADMET
  data or in vitro results (use bioactivity-landscape when it covers
  in vitro data), for in vivo pharmacokinetics or PBPK modelling
  (untooled), or for compound drug-likeness rules and structural
  alerts (use compound-property-profile).
---

## 1. When to use, and when not

Use this skill when you need predicted ADMET endpoint classifications
for a compound. Entry points include:

- Screening a compound's predicted ADMET profile to identify which
  in vitro studies to prioritize.
- Flagging structural features associated with hERG channel blockade
  risk.
- Assessing predicted metabolic stability, CYP inhibition risk,
  permeability, and solubility class for a compound.
- Re-analyzing stored predictions against updated program thresholds
  without recomputing.

**Do not use when:**

- You need measured ADMET data or in vitro assay results
  -> `bioactivity-landscape` (when it covers in vitro data).
- You need in vivo pharmacokinetics, clearance, or half-life
  -> untooled.
- You need PBPK modelling
  -> untooled.
- You need drug-likeness rules (Lipinski, Veber) or structural
  alerts (PAINS, Brenk, aggregator filters)
  -> `compound-property-profile`.
- You need binding affinity or docking scores
  -> `binding-mode-analysis`.
- You need genetic constraint or loss-of-function intolerance
  -> `target-genetic-evidence`.

## 2. Preconditions

- **Input**: a SMILES string. Multi-fragment SMILES (salts, mixtures)
  are accepted: counterions are stripped, the parent molecule is
  selected by heavy atom count, and the `compound.fragment_stripped`
  relay fires. Unparseable SMILES exits 9 (Refusal) — a different
  input string is the remedy.
- **RDKit**: must be installed. Run `dde doctor` before first use.
  It ends with a verdict line: `STOP` means fix or report before
  running anything; `PROCEED` means work. Do not judge by the warning
  count; the verdict line grades them for you.
- **No authentication** needed — all operations are local.
- **No network** — all subcommands are offline.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What are the predicted ADMET endpoints? | `dde admet predict <SMILES>` | `raw/admet/<slug>.predict.json`<br>`raw/admet/<slug>.predict.meta.json` |
| How do the predictions classify against thresholds? | `dde admet analyze <SMILES>` | `raw/admet/<slug>.analysis.json` |

Run `predict` before `analyze`. `predict` computes molecular
descriptors and generates endpoint predictions. `analyze` reads
from disk, applies the `admet-endpoints` threshold set, and
produces a verdict with per-endpoint statuses.

When phase-1 output lives in a non-default directory, pass `--from`
to `analyze`. `--out` overrides the output directory independently.
The `<slug>` is derived from the canonical SMILES — deterministic
and filesystem-safe. Pass the canonical SMILES printed by `predict`
to `analyze`.

## 4. Interpretation contract

### Verdicts

`analyze` applies the `admet-endpoints@1.0` threshold set and
emits one of:

- **developable** — all five endpoints in acceptable range.
- **flagged** — no critical liabilities but one or more endpoints
  in marginal range.
- **liabilities-identified** — one or more endpoints in
  unacceptable range.

### Per-endpoint statuses

Each of the five endpoints receives a status:

- **acceptable** — within the configured threshold range.
- **marginal** — borderline; warrants attention but not a liability.
- **unacceptable** — outside the configured threshold range; a
  liability.

The five endpoints: metabolic_stability, cyp_inhibition,
permeability, herg_liability, solubility.

### What the predictions mean

All five endpoints are rule-based predictions from molecular
descriptors and SMARTS pharmacophore patterns — not trained ML
models and not measurements. Each prediction record names its
source publication and the rules applied.

- **Metabolic stability** (Gleeson 2008): liability class from LogP
  and aromatic ring count. Not clearance or half-life.
- **CYP inhibition**: risk flags per isoform (CYP2D6, CYP3A4,
  CYP2C9) from structural and lipophilicity rules. Not measured
  inhibition.
- **Permeability** (Egan 2000): passive permeability class from
  TPSA, LogP, and MW. Not Caco-2 or PAMPA measurements.
- **hERG liability** (Aronov 2005): pharmacophore-based flag from
  basic nitrogen, LogP, and aromatic ring count. Not measured IC50
  or patch-clamp.
- **Solubility** (Delaney 2004): predicted LogS for the free form.
  Not salt form solubility.

Thresholds are cited by name. The values in force are in the
artifact: every `.analysis.json` carries `threshold_set`,
`thresholds_applied`, `threshold_sources`, and
`threshold_provenance`. Quote values from the analysis being cited,
never from prose.

### Model applicability domains

Each prediction is derived from published rules calibrated on a
specific chemical space. Predictions for compounds outside that
space are extrapolations and should be flagged, not trusted at face
value.

| Endpoint | Source | Training domain / applicability | Known breakdown regions |
|---|---|---|---|
| Metabolic stability | Gleeson 2008 | Drug-like small molecules, predominantly MW <600 Da, LogP roughly −2 to 6. The liability classification was derived from oral drug candidates in medicinal-chemistry space. | Highly lipophilic compounds (LogP >6) may saturate the classification. Peptides, macrocycles, and PROTACs (MW >600–700 Da) fall outside the calibration set. |
| CYP inhibition | Structural / lipophilicity rules per isoform | Calibrated on known drug–CYP interaction data for CYP2D6, CYP3A4, CYP2C9 — primarily small-molecule drugs with MW <500 Da and conventional heteroatom content. | Unusual pharmacophores, covalent modifiers, and compounds with atypical metal-coordination chemistry may not match the rule patterns. Time-dependent inhibition (TDI) is not modelled. |
| Permeability | Egan 2000 (Egan egg) | Optimized for oral drug candidates; calibration set dominated by compounds with TPSA <140 Å², LogP roughly −1 to 5, MW <500 Da. | Compounds with TPSA >140 Å² or MW >500 Da fall outside the egg boundary and predictions are unreliable. Actively transported compounds, prodrugs, and peptides are not modelled. |
| hERG liability | Aronov 2005 | Pharmacophore rules derived from known hERG blockers — small molecules with a basic nitrogen, moderate-to-high LogP, and aromatic ring systems. | The pharmacophore covers one binding mode; hERG blockers that act through different motifs (e.g. neutral compounds, non-aromatic scaffolds) produce false negatives. The flag has a documented false-negative rate. |
| Solubility | Delaney 2004 (ESOL) | Trained on ~1,144 small molecules, predominantly MW <500 Da, LogP roughly −4 to 7. The model is a linear regression on LogP, MW, rotatable bonds, and aromatic proportion. | Predictions degrade for molecules >~600 Da, for compounds with unusual functional groups not well-represented in the training set (e.g. boronates, organometallics), and for salt forms (the model predicts free-form solubility only). |

**When a compound falls outside an endpoint's domain:**

- Flag the prediction as out-of-domain in the finding. State which
  endpoint is affected and why (e.g. "MW 780 Da; ESOL was trained
  on molecules predominantly <500 Da").
- Do not treat the prediction as reliable — it is an extrapolation,
  not a calibrated estimate.
- If the question being answered depends on the out-of-domain
  endpoint, report the endpoint as untooled for that compound
  class, not as a confident prediction with a caveat.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `admet.prediction_not_measurement` | Stop | The verdict is "developable" — all endpoints acceptable, the clean profile is available (claim-triggered, conditional) | Do not substitute predicted ADMET endpoints for measured values. A clean predicted ADMET profile identifies which in vitro studies to prioritize, not which to skip. If the question is whether a compound is safe, the answer is that the question requires measurement — not that the prediction is clean. |
| `admet.herg_structural_flag` | Qualifier | hERG structural features are flagged — basic nitrogen AND LogP above the configured `herg_logp_min` threshold AND aromatic rings at or above the configured `herg_min_aromatic_rings` threshold (defect-triggered, conditional) | Name the specific structural features that triggered the flag and state that it is a pharmacophore-based prediction, not a measured IC50 or patch-clamp result. Rule-based hERG prediction has a documented false-negative rate: absence of this flag is not evidence of hERG safety. |

**Upstream relays.** The tool propagates upstream
`compound.fragment_stripped` warnings from multi-fragment SMILES
handling via the phase-1 sidecar. Any upstream relay present in the
analysis record must also be satisfied:

| Relay code | Kind | Origin | Obligation |
|---|---|---|---|
| `compound.fragment_stripped` | Qualifier | compound pipeline | The input SMILES contained multiple fragments; counterions were stripped. Name the stripped fragments alongside any finding about the parent molecule. Predictions were computed on the largest fragment only. |

Check `mandatory_relays` in the `.analysis.json` and in the
phase-1 `.predict.meta.json`. Every relay code present must be
satisfied in the finding.

### Consequence rules

- **Developable**: state the verdict, threshold set, and canonical
  SMILES. The `admet.prediction_not_measurement` relay fires —
  satisfy it before proceeding. A clean predicted profile is a
  reason to prioritize confirmatory studies, not to skip them.
- **Flagged**: state which endpoints are marginal and the threshold
  set. Marginal endpoints warrant attention but are not liabilities.
- **Liabilities-identified**: state which endpoints are unacceptable
  (from the analysis, not from recomputation) and the threshold set.
  These predict where experimental confirmation is most urgent.

### Cross-disciplinary consequences

- A clean predicted ADMET profile does not substitute for measured
  ADMET data — every endpoint is a rule-based prediction, not an
  in vitro measurement.
- Predicted permeability is descriptor-based (Egan egg model), not
  cell-based. It is not a Caco-2 or PAMPA result.
- Predicted metabolic stability class does not predict in vivo
  clearance or half-life.
- Predicted solubility is for the free form. Salt form solubility
  is a formulation property not addressed by this prediction.
- The hERG structural flag has a documented false-negative rate.
  Absence of the flag is not evidence of hERG safety.
- A compound at exactly a threshold boundary is classified by the
  tool. Do not reclassify — the verdict under the configured
  threshold set is the finding.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/`
that cites the `.analysis.json`, satisfies every mandatory relay
that fired, names the canonical SMILES and the threshold set, and
never substitutes a clean prediction for a measured value.
Everything the tools emitted stays under `raw/admet/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the
tool emitted. If the tool did not run, there is no value. If a
required tool is unavailable, report the task as blocked. Do not
estimate, and do not proceed on an assumed result.

### Named pathologies

- **Treating "no hERG flag" as hERG safety clearance.** The flag
  has a documented false-negative rate. Absence means the compound
  did not match one pharmacophore pattern — not that it is safe
  from hERG liability. Many hERG blockers do not match this pattern.
- **Conflating predicted permeability with Caco-2.** The tool uses
  descriptor-based rules (Egan egg model), not cell-based data. A
  "high permeability" prediction cannot substitute for a Caco-2
  result.
- **Conflating stability class with in vivo half-life.** Predicted
  metabolic stability (Gleeson 2008) does not predict clearance or
  half-life. "Low metabolic liability" identifies structural
  features, not pharmacokinetics.
- **Over-reading solubility for salt forms.** Predicted solubility
  (ESOL model) is for the free form. Salt form solubility is a
  formulation property not predicted by this tool.
- **Treating a "developable" verdict as regulatory-ready.** Every
  endpoint is predicted, not measured. The
  `admet.prediction_not_measurement` relay exists for this.
- **Computing ADMET predictions yourself instead of using the
  tool.** The tool pins RDKit version, descriptor set, and published
  rule sets. The finding must cite the tool's output, not a
  recalculation.
