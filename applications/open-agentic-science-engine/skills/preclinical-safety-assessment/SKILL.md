---
name: preclinical-safety-assessment
description: >
  Interpret preclinical toxicology studies, compute therapeutic index
  and hERG safety margins, and assess genotoxicity batteries — repeat-dose
  study data, cross-artifact margin calculation, ICH S2(R1) weight-of-
  evidence genotoxicity assessment, and safety pharmacology recording.
  Use when interpreting tox study results for safety margin evaluation,
  computing therapeutic index from NOAEL exposure and PK data, assessing
  a genotoxicity battery, or evaluating measured hERG IC50 margins.
  Do not use for predicted ADMET endpoints (use admet-property-prediction),
  for in vivo PK parameters or NCA analysis (use in-vivo-pk-analysis,
  though PK data is a required input here), or for compound drug-likeness
  rules and structural alerts (use compound-property-profile).
---

## 1. When to use, and when not

Use this skill when you need to interpret preclinical toxicology data
and compute safety margins. Entry points include:

- Ingesting and normalising repeat-dose, safety pharmacology, or
  genotoxicity study data for downstream analysis.
- Computing therapeutic index (TI) from NOAEL exposure and PK NCA
  data — cross-artifact margin calculation requiring both tox and PK
  artifacts.
- Assessing a genotoxicity battery using ICH S2(R1) weight-of-evidence
  classification.
- Computing measured hERG IC50 safety margins from safety pharmacology
  data and PK exposure.
- Applying the `tox-safety-package` threshold set to produce verdicts
  on margins, genotoxicity, or safety pharmacology artifacts.

**Do not use when:**

- You need predicted ADMET endpoints (metabolic stability, permeability,
  predicted hERG structural flag) -> `admet-property-prediction`.
- You need in vivo PK parameters, NCA analysis, or allometric scaling
  -> `in-vivo-pk-analysis` (though PK NCA artifacts are a required
  input for margin calculation here).
- You need compound drug-likeness rules or structural alerts
  -> `compound-property-profile`.

## 2. Preconditions

- **Input schemas**: canonical JSON matching one of:
  - `dde.tox-repeat-dose.v1` — repeat-dose study data (for ingest,
    margins).
  - `dde.tox-safety-pharm.v1` — safety pharmacology data with
    measured hERG IC50 (for ingest, margins `--herg`, analyze).
  - `dde.tox-genotox.v1` — genotoxicity battery results (for ingest,
    genotox).
- **Cross-artifact dependency**: `tox margins` requires BOTH a tox
  repeat-dose artifact AND a PK NCA artifact (`dde.pk-nca.v1`,
  produced by `in-vivo-pk-analysis`). Optionally takes a safety-pharm
  artifact via `--herg` for measured hERG IC50 margin calculation.
  PK data is a precondition for margin computation — run
  `in-vivo-pk-analysis` first.
- **`dde doctor`**: run before first use. It ends with a verdict
  line: `STOP` means fix or report; `PROCEED` means work.
- **No authentication** needed — all operations are local.
- **No network** — all subcommands are offline.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Ingest and normalise a tox study? | `dde tox ingest <INPUT_FILE>` | `raw/tox/{id}.tox-repeat-dose.json` + `.meta.json`<br>`raw/tox/{id}.tox-safety-pharm.json` + `.meta.json`<br>`raw/tox/{id}.tox-genotox.json` + `.meta.json` |
| Assess a genotoxicity battery? | `dde tox genotox <GENOTOX_ARTIFACT>` | `raw/tox/{compound_id}.tox-genotox-assessment.json` + `.meta.json` |
| Compute therapeutic index and hERG margins? | `dde tox margins <TOX_FILE> <PK_FILE> [--herg <SAFETY_PHARM>]` | `raw/tox/{study_id}.tox-margins.json` + `.meta.json` |
| Apply thresholds and produce verdict? | `dde tox analyze <ARTIFACT>` | `raw/tox/{stem}.tox.analysis.json` |

Run phase 1 (`ingest`, `genotox`, `margins`) before phase 2
(`analyze`). `analyze` reads from disk and can be re-run with different
thresholds without recomputing.

`ingest` dispatches on the `schema` field in the input JSON — one
command handles all three study types.

## 4. Interpretation contract

### Verdicts

`analyze` applies the `tox-safety-package@1.0` threshold set and
emits per-artifact-type verdicts:

**Margins analysis** (`dde.tox-margins.v1`):
- **acceptable** — TI and hERG margins (when present) meet thresholds.
- **flagged** — one or more margins below threshold.
- **incomplete** — NOAEL exposure data not available; TI cannot be
  computed.

**Genotox analysis** (`dde.tox-genotox-assessment.v1`):
- **acceptable** — battery negative, no genotoxic concern.
- **flagged_equivocal** — mixed results, in vitro positive / in vivo
  negative pattern (equivocal).
- **flagged_concern** — positive or mixed results indicating genotoxic
  concern (includes all-positive, in vivo positive, or preponderance
  positive batteries).
- **unknown** — battery verdict not classifiable.

**Safety-pharm analysis** (`dde.tox-safety-pharm.v1`):
- **recorded** — safety pharmacology data recorded. Quantitative hERG
  margin requires running `tox margins` with both PK and safety-pharm
  artifacts.

### Genotox battery classification

`tox genotox` classifies the battery result per ICH S2(R1):
- **negative** — all assays negative.
- **positive** — all assays positive.
- **equivocal** — in vitro positive / in vivo negative (WoE needed).
- **concern** — in vivo micronucleus positive with mixed results
  (WoE needed).
- **positive_concern** — 2+ positive results in a mixed battery
  (WoE needed).

### Thresholds

The `tox-safety-package@1.0` threshold set provides:
- `ti_minimum` — minimum acceptable therapeutic index fold-margin
  (ICH M3(R2)).
- `herg_safety_margin` — minimum hERG safety margin fold (ICH S7B).
- `herg_marginal` — operational boundary below the full hERG margin.
- `noael_exposure_margin` — **UNRESOLVED**. This threshold has no
  defensible default value. It is entirely program-specific. Do not
  invent a value — set it via `.dde/thresholds.yaml` per program
  and record the justification.

Thresholds are cited by name. The values in force are in the artifact:
every `.analysis.json` carries `threshold_set`, `thresholds_applied`,
`threshold_sources`, and `threshold_provenance`. Quote values from the
analysis being cited, never from prose.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `tox.genotox_weight_of_evidence` | Qualifier | Genotox battery has mixed results requiring weight-of-evidence reasoning (conditional) | State that this genotoxicity assessment used ICH S2(R1) weight-of-evidence reasoning because the battery results were mixed. Do not report the verdict as a clean negative — name the positive assay and the basis for the overall assessment. |

**Upstream relay forwarding.** `tox margins` reads the PK NCA sidecar
and forwards any upstream mandatory relays (e.g.,
`pk.nca_assumes_linearity`). Any upstream relay present in the margins
`.meta.json` must also be satisfied in the finding. Check
`mandatory_relays` in both the `.analysis.json` and in each phase-1
`.meta.json`.

### Consequence rules

- **Margins acceptable**: state the verdict, the threshold set, and
  which margins were computed (TI Cmax, TI AUC, hERG). Proceed to
  downstream work.
- **Margins flagged**: state which margins are below threshold and by
  how much (from the analysis, not from recomputation). A flagged TI
  below the configured `ti_minimum` is a substantive safety concern.
  A flagged hERG margin between `herg_marginal` and
  `herg_safety_margin` warrants attention; below `herg_marginal` is
  elevated concern.
- **Margins incomplete**: NOAEL exposure data was absent. The NOAEL
  dose is recorded but TI cannot be computed without measured exposure.
  This is not a failure — it is a data gap that requires toxicokinetic
  (TK) data.
- **Genotox acceptable**: battery negative, no genotoxic concern.
- **Genotox flagged**: state the battery classification and the
  weight-of-evidence basis. Satisfy the
  `tox.genotox_weight_of_evidence` relay if present.
- **Safety-pharm recorded**: data is recorded but quantitative margin
  assessment requires running `tox margins` with both PK and
  safety-pharm artifacts.

### Stage 3 to Stage 4 boundary

When a safety-pharm artifact with measured hERG IC50 is available, the
measured hERG margin from `tox margins` supersedes the predicted
structural flag from Stage 3 ADMET (`admet.herg_structural_flag`). A
measured IC50 and computed safety margin are direct evidence; a
pharmacophore-based prediction is indirect. Do not carry both the
measured margin and the predicted flag as independent findings — the
measured margin replaces the prediction.

### Cross-disciplinary consequences

- The therapeutic index assumes the PK file represents exposure at the
  intended therapeutic dose. `dde.pk-nca.v1` does not tag dose
  context — this assumption cannot be verified from the artifact alone.
- A margins artifact without hERG data does not imply hERG safety —
  it means the measurement was not provided.
- Genotox battery classification is categorical. No threshold
  application occurs for genotox — the verdict maps directly from the
  battery result pattern.
- Safety-pharm data recording does not substitute for quantitative
  margin calculation. The hERG IC50 value is captured but not compared
  to therapeutic exposure until `tox margins` is run with both
  artifacts.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the
tool emitted. If the tool did not run, there is no value. If a
required tool is unavailable, report the task as blocked. Do not
estimate, and do not proceed on an assumed result.

### Named pathologies

- **Computing TI without PK data.** `tox margins` requires both a
  tox repeat-dose artifact and a PK NCA artifact. Do not estimate a
  therapeutic index from the NOAEL dose alone — exposure-based TI
  requires measured exposure data.
- **Treating an incomplete margins verdict as acceptable.** Incomplete
  means TI could not be computed, not that the margin is adequate. A
  missing TI is a data gap, not a pass.
- **Reporting a mixed genotox battery as negative.** When the
  `tox.genotox_weight_of_evidence` relay fires, the battery had mixed
  results. The verdict is the classification after WoE reasoning, not
  a clean negative.
- **Carrying both predicted and measured hERG findings.** When a
  measured hERG IC50 margin is available from `tox margins`, it
  supersedes `admet.herg_structural_flag`. Do not present both as
  independent lines of evidence.
- **Ignoring upstream PK relays on the margins artifact.** `tox margins`
  forwards upstream relays from the PK sidecar (e.g.,
  `pk.nca_assumes_linearity`). Every forwarded relay must be satisfied
  in the finding — they are not tox-specific, but they apply to the
  TI calculation that depends on the PK data.
- **Inventing a value for `noael_exposure_margin`.** This threshold is
  UNRESOLVED and program-specific. The CLI will refuse to apply it.
  Do not supply a value in prose or in a finding.

## 6. Provenance

Therapeutic index convention references ICH M3(R2) general guidance
for starting dose selection and safety margin assessment. hERG safety
margin references ICH S7B (adopted 2005) for the recommended margin
between therapeutic free plasma Cmax and hERG IC50. Genotoxicity
battery assessment references ICH S2(R1) for weight-of-evidence
evaluation of mixed battery results. Provenance details are stamped in
every `.analysis.json` via `threshold_provenance`.
