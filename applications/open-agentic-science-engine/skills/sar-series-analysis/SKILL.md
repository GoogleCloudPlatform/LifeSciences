---
name: sar-series-analysis
description: >
  Decompose compounds into BRICS fragments, identify matched molecular
  pairs across a series, and detect property cliffs — large property
  changes at a single R-group transformation. Use when performing
  systematic SAR analysis across a DMTA round, identifying which
  structural transformations correlate with property changes, decomposing
  a compound into its BRICS fragments, or detecting property cliffs —
  large property changes at a single R-group position that warrant
  investigation. Do not use for single-compound
  profiling (use compound-property-profile), binding mode or docking (use
  binding-mode-analysis), or dose-response quality (use
  bioactivity-landscape).
---

## 1. When to use, and when not

Use this skill when you need to analyze structure-activity relationships
across a compound series using matched molecular pairs. Entry points
include:

- Systematic SAR analysis across a compound series — identifying which
  R-group transformations correlate with property changes in a DMTA
  round.
- Identifying property cliffs — large property changes at a single
  R-group position that warrant investigation.
- Decomposing a compound into BRICS fragments — recording the full
  fragmentation (core scaffold and R-group positions) for downstream
  pairing.
- Identifying matched molecular pairs between compounds — finding
  compound pairs that share the same BRICS core but differ at exactly
  one R-group.
- Identifying which R-group transformations correlate with ADMET
  endpoint changes (metabolic stability, permeability, hERG) across
  a series.

**Do not use when:**

- You need single-compound profiling (descriptors, drug-likeness,
  structural alerts) -> `compound-property-profile`.
- You need binding affinity, docking scores, or pose interpretation
  -> `binding-mode-analysis`.
- You need dose-response quality, Z-factor, or curve fitting
  -> `bioactivity-landscape`.
- You need fold confidence or domain boundaries
  -> `protein-structure-confidence`.
- You need genetic constraint or loss-of-function intolerance
  -> `target-genetic-evidence`.

## 2. Preconditions

- **fragment**: a single SMILES string. Unparseable SMILES exits 9
  (Refusal). RDKit required.
- **pairs**: a JSON file matching schema `dde.mmp-series.v1` with a
  top-level `schema` field and a `compounds` array. Each compound record
  must have `compound_id`, `smiles`, and a non-empty `properties` dict.
  Maximum 10,000 compounds. RDKit required.
- **analyze**: a `.mmp-pairs.json` artifact produced by the `pairs`
  step. RDKit is **not** required — analyze is Phase 2 (offline,
  deterministic, reads from disk).
- Run `dde doctor` before first use. Its verdict line (`STOP` or
  `PROCEED`) is the gate.
- **No authentication** needed — all operations are offline.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What are this compound's BRICS fragments? | `dde mmp fragment <SMILES>` | `raw/compounds/<slug>.mmp-fragment.json`<br>`raw/compounds/<slug>.mmp-fragment.meta.json` |
| Which compounds form matched molecular pairs? | `dde mmp pairs <SERIES_FILE>` | `raw/compounds/<name>.mmp-pairs.json`<br>`raw/compounds/<name>.mmp-pairs.meta.json` |
| Where are the property cliffs? | `dde mmp analyze <ARTIFACT>` | `raw/compounds/<stem>.mmp-analysis.json` |

Run Phase 1 (`fragment`, `pairs`) before Phase 2 (`analyze`). `analyze`
reads stored `.mmp-pairs.json` from disk and can be re-run with
different thresholds without recomputing pairs.

The `<slug>` is derived from canonical SMILES — deterministic and
filesystem-safe. The `<name>` is derived from the input filename. The
`<ARTIFACT>` for `analyze` is the `.mmp-pairs.json` produced by `pairs`.

## 4. Interpretation contract

### Output structure

The tool does not produce a single verdict string. `analyze` produces a
multi-component analysis:

- **Per-transformation records**: `transformation` string, `n_pairs`,
  `cliffs` list (each with `pair`, `property`, `delta`, `reasons`),
  `advisories`, `low_pair_count` flag, and `not_evaluated_properties`
  (list of property names for which no cliff threshold is configured),
  with per-property advisories in `advisories`.
- **Assessment**: `cliff_results` (the per-transformation records),
  `unresolved_cliff_thresholds` list, an advisory when thresholds are
  unresolved, and `not_evaluated_advisory` when any properties lacked
  a configured threshold.
- **Metrics**: `n_pairs_total`, `n_transformations`, `n_cliffs`,
  `n_low_pair_count_transformations`, `n_properties_not_evaluated`,
  and `properties_not_evaluated`.

Interpret each component for what it measures. Do not collapse this
structure into a pass/fail verdict.

### What a property cliff means, and what it does not

A property cliff is a correlation in a dataset. A large property change
across a single R-group transformation flags a substitution worth
attention — it does not explain why the transformation matters
mechanistically. The structural change may be incidental to the true
driver.

A cliff detected from one matched pair is an observation, not a
validated SAR rule. The pair count for the transformation must be
considered alongside the cliff itself.

### Threshold structure — cliff_absolute_delta

The `mmp-cliffs@1.0` threshold set contains:

- `min_pair_count` — resolved. Cite by name; quote values from
  `thresholds_applied` in the analysis being cited.
- `cliff_absolute_delta` — a per-property dict. In `mmp-cliffs@1.0`,
  recognized properties (`cliff_absolute_delta["pic50"]` and
  `cliff_absolute_delta["pki"]`) have resolved thresholds. Cite the
  threshold by name from `thresholds_applied`; do not embed the numeric
  value in prose.

Properties not present in `cliff_absolute_delta` are explicitly not
evaluated for cliffs. The tool emits per-transformation
`not_evaluated_properties` lists and advisories naming exactly which
properties were not evaluated and why. This is not a silent skip.

"Not evaluated" is not "no cliffs found." The question was not asked for
those properties. Do not invent a threshold for an unconfigured property.

Property names are matched case-insensitively (e.g. pIC50 resolves
against the pic50 entry).

Programs can add property thresholds via `.dde/thresholds.yaml` under
`mmp-cliffs.cliff_absolute_delta`.

If a program overrides the entire `cliff_absolute_delta` to UNRESOLVED,
the tool skips all cliff detection and emits the original advisory. The
"do not invent" rule applies in full in that case — the absence of cliff
detection is not "no cliffs exist," it means the question was not asked.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `mmp.small_pair_count` | Qualifier (defect-triggered) | Any transformation group has fewer matched pairs than the `min_pair_count` threshold | Note the low pair count and scope any SAR conclusion from that transformation as low-confidence. Do not report a property trend from this transformation with confidence — the number of matched pairs supporting it is below the minimum required for a reliable conclusion. Report the raw observation and the pair count, not a trend. |
| `mmp.cliff_not_causation` | Stop (claim-triggered) | One or more property cliffs are detected | Report the cliff as a correlation, not as evidence of a causal mechanism. A large property change across a single R-group transformation flags a substitution worth attention but does not demonstrate that the R-group change caused the property change. Adding a disclaimer does not satisfy this relay — if the question is about mechanism, the answer is that the question is untooled by MMP analysis alone. |

Check `mandatory_relays` in both the `.mmp-analysis.json` and any
upstream `.meta.json`. Every relay code present must be satisfied in
the finding.

### Consequence rules

- **Cliffs detected**: state the transformation, the property, the
  delta (from the analysis, not from recomputation), and the pair count.
  Carry the `mmp.cliff_not_causation` relay — the cliff is a
  correlation, not a mechanism. Cross-reference
  `compound-property-profile` for the individual compounds involved.
- **Low pair count**: carry the `mmp.small_pair_count` relay. State the
  pair count and scope the observation as low-confidence. Do not
  extrapolate a trend from too few pairs.
- **No cliffs detected (with resolved threshold)**: state that no
  property changes exceeded the configured cliff threshold for any
  transformation. This does not mean properties are insensitive to
  structural changes — it means no large change was observed in this
  dataset under the configured threshold.
- **Properties not evaluated**: when the analysis reports
  `not_evaluated_properties`, carry the advisory unchanged. The question
  of whether cliffs exist for those properties is unanswered — no
  threshold was configured. Do not infer absence of cliffs from absence
  of evaluation.
- **Entire cliff_absolute_delta UNRESOLVED** (program override): the
  tool skipped all cliff detection. Carry the advisory unchanged. The
  question is unanswered for all properties.

### Cross-disciplinary consequences

- MMPs only cover the transformations present in the series. Absence of
  a cliff does not mean the property is insensitive to that
  transformation in other scaffolds or chemical contexts.
- A property change at one R-group position may not generalize to other
  positions or scaffolds — pairwise SAR is not global SAR.
- Cross-reference `compound-property-profile` for the computed
  properties and structural alerts of individual compounds in a cliff
  pair. Cross-reference `bioactivity-landscape` when the properties
  include measured assay readouts.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.mmp-analysis.json`, satisfies every mandatory relay that
fired, names the threshold set applied, carries any not-evaluated or
UNRESOLVED-threshold advisory through unchanged, and never reads absence
of evaluation as absence of cliffs. Everything the tools emitted stays
under `raw/compounds/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Treating a 2-compound cliff as a general rule.** A property cliff
  from one matched pair is an observation, not a validated SAR rule.
  The pair count for the transformation must be considered — the
  `mmp.small_pair_count` relay fires when the count is below the
  configured minimum.
- **Extrapolating beyond explored chemical space.** MMPs only cover the
  transformations present in the series. Absence of a cliff does not
  mean the property is insensitive to that transformation in other
  scaffolds.
- **Confusing pairwise SAR with global SAR.** A property change at one
  R-group position may not generalize to other positions or scaffolds.
  Matched molecular pair analysis characterizes local SAR within the
  observed series.
- **Confusing "no cliff" with insensitivity.** Absence of a cliff means
  no large change was observed in this dataset under the configured
  threshold — not that the property is insensitive to structural
  changes.
- **Treating "not evaluated" as "no cliffs."** When a property is
  absent from `cliff_absolute_delta`, the tool does not evaluate it for
  cliffs. The `not_evaluated_properties` field names these explicitly.
  "Not evaluated" means the question was not asked — not that the answer
  is "no cliffs." Do not supply a threshold the tool did not configure.
- **Computing MMP fragmentation yourself instead of using the tool.**
  The tool pins the RDKit version and BRICS decomposition algorithm. A
  fragmentation computed outside the tool may use different bond
  definitions or a different RDKit version. The finding must cite the
  tool's output, not a recalculation.

## 6. Provenance

RDKit BRICS decomposition. RDKit is BSD-3-Clause licensed. The tool
records `rdkit_version` in every Phase 1 artifact.
