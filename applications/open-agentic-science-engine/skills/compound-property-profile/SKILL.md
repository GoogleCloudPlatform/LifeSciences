---
name: compound-property-profile
description: >
  Validate SMILES input, compute molecular descriptors, and screen for
  structural alerts — drug-likeness rules, PAINS/Brenk/aggregator
  filters, and multi-fragment handling. Use when profiling a compound's
  physicochemical properties: checking Lipinski and Veber compliance,
  screening for pan-assay interference or undesirable substructures, or
  validating and canonicalizing a SMILES string before downstream work.
  Do not use for binding affinity or docking (use binding-mode-analysis
  when available), genetic constraint (use target-genetic-evidence), or
  structure confidence (use protein-structure-confidence).
---

## 1. When to use, and when not

Use this skill when you need to establish a compound's computed
properties and structural alert profile. Entry points include:

- Profiling a hit or lead compound for drug-likeness before advancing
  it — the first physicochemical filter in a compound campaign.
- Screening a SMILES for PAINS, Brenk, or aggregator alerts before
  investing in synthesis or assay design.
- Validating and canonicalizing a SMILES string, especially when the
  input may contain salts or mixtures (multi-fragment handling).
- Re-analyzing stored descriptors and alerts against updated program
  thresholds without recomputing.

**Do not use when:**

- You need binding affinity, docking scores, or pose interpretation
  -> `binding-mode-analysis` (when available).
- You need genetic constraint or loss-of-function intolerance
  -> `target-genetic-evidence`.
- You need fold confidence or domain boundaries
  -> `protein-structure-confidence`.
- You need pocket druggability
  -> `pocket-druggability`.
- You need tissue expression evidence
  -> `tissue-expression-profile`.

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
| Is this SMILES valid? What is the canonical form? | `dde compound validate <SMILES>` | `raw/compounds/<slug>.validate.json`<br>`raw/compounds/<slug>.validate.meta.json` |
| What are the molecular descriptors? | `dde compound descriptors <SMILES>` | `raw/compounds/<slug>.descriptors.json`<br>`raw/compounds/<slug>.descriptors.meta.json` |
| Does this compound hit any structural alerts? | `dde compound alerts <SMILES>` | `raw/compounds/<slug>.alerts.json`<br>`raw/compounds/<slug>.alerts.meta.json` |
| Is this compound drug-like? | `dde compound analyze <SMILES>` | `raw/compounds/<slug>.analysis.json` |

Run phase 1 (`validate`, `descriptors`, `alerts`) before phase 2
(`analyze`). `analyze` reads from disk and can be re-run with different
thresholds without recomputing descriptors or alerts.

When phase-1 output lives in a non-default directory, pass `--from` to
`analyze` so it reads from that location. `--out` overrides the output
directory independently.

The `<slug>` is derived from the canonical SMILES — deterministic and
filesystem-safe. Pass the canonical SMILES printed by phase 1 to
`analyze`.

All output options: `--json` for machine-readable output, `--quiet` for
paths only.

## 4. Interpretation contract

### Verdicts

`analyze` applies the `compound-descriptors` and `compound-alerts`
threshold sets and emits one of:

- **drug-like** — passes all drug-likeness rules (Lipinski Rule of Five
  and Veber criteria) and has no structural alerts above threshold.
- **drug-like-with-alerts** — passes rules but has structural alerts
  (PAINS, Brenk, or aggregator hits above threshold).
- **rule-violations** — violates drug-likeness rules but has no
  structural alerts above threshold.
- **rule-violations-and-alerts** — both rule violations and structural
  alerts above threshold.

### What the rules mean

The `compound-descriptors@1.0` threshold set applies the Lipinski Rule
of Five (MW, LogP, HBD, HBA) and Veber criteria (rotatable bonds,
TPSA). The Rule of Five tolerates one violation — a compound with
exactly one Lipinski violation is marginal, not failing. Two or more
Lipinski violations, or any Veber violation, counts against drug-likeness.

The `compound-alerts@1.0` threshold set applies hit-count thresholds for
PAINS, Brenk, and aggregator filters separately.

Thresholds are cited by name. The values in force are in the artifact:
every `.analysis.json` carries `threshold_set`, `thresholds_applied`,
`threshold_sources`, and `threshold_provenance`. Quote values from the
analysis being cited, never from prose.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `compound.fragment_stripped` | Qualifier | The input SMILES contained multiple fragments (salts, mixtures) and counterions were stripped (defect-triggered, conditional) | Name the stripped fragments alongside any finding about the parent molecule. Descriptors and alerts were computed on the largest fragment only — do not attribute these properties to the original multi-component input without noting what was removed. |
| `compound.alerts_not_toxicology` | Stop | The compound has no PAINS, Brenk, or aggregator hits — the alert screen is clean (claim-triggered, conditional) | Do not conclude the compound is non-toxic from a clean alert screen. These filters detect known assay interference patterns and undesirable substructures, not toxicity mechanisms. A compound that passes them may still be toxic, reactive, or genotoxic by pathways these filters do not cover. If the question is about safety, the answer is that the question is untooled by this capability — not that the compound passed. |

Check `mandatory_relays` in both the `.analysis.json` and in each
phase-1 `.meta.json`. Every relay code present must be satisfied in the
finding.

### Consequence rules

- **Drug-like**: state the verdict, the threshold set applied, and the
  canonical SMILES. Proceed to downstream work — the compound passes
  computed property filters.
- **Drug-like-with-alerts**: state which alert types fired. PAINS alerts
  warrant investigation — they indicate potential assay interference.
  Brenk alerts flag undesirable substructures. Aggregator alerts flag
  colloidal aggregation risk. The compound passes rules but the alerts
  need addressing before advancing.
- **Rule-violations**: state which rules were violated and by how much
  (from the analysis, not from recomputation). A single Lipinski
  violation is marginal and the analysis carries an advisory. Two or
  more violations or any Veber violation is a substantive concern for
  oral bioavailability.
- **Rule-violations-and-alerts**: both concerns apply. State both.

### Cross-disciplinary consequences

- A clean property profile does not substitute for measured ADMET data —
  computed descriptors predict drug-likeness heuristically, not
  pharmacokinetics.
- Structural alerts (PAINS, Brenk, aggregator) flag interference
  patterns. They do not predict toxicity, off-target activity, or
  metabolic liability.
- A compound at exactly the threshold boundary for any descriptor is
  classified by the tool. Do not reclassify it — the tool's verdict
  under the configured threshold set is the finding.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies every mandatory relay that fired,
names the canonical SMILES, and never substitutes a clean alert screen
for a safety claim. Everything the tools emitted stays under
`raw/compounds/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Multi-fragment SMILES canonicalizing unexpectedly.** When a SMILES
  contains salts or mixtures, the parent is selected by heavy atom
  count and the canonical form may differ from what the user intended.
  The `compound.fragment_stripped` relay fires and documents what was
  removed — do not suppress or skip it.
- **Treating a clean alert screen as evidence of safety.** The
  `compound.alerts_not_toxicology` relay exists for this. Absence of
  PAINS/Brenk/aggregator hits means the compound did not match known
  interference patterns — it says nothing about toxicity, reactivity,
  or genotoxicity. This is the most common over-read of a clean result.
- **PAINS A patterns and false-positive rates.** PAINS filters are
  divided into classes A, B, and C. PAINS A has the highest
  false-positive rate (Baell & Holloway). A PAINS A hit warrants
  investigation but is not automatically disqualifying — report the
  pattern class and let the finding carry the distinction.
- **Treating one Lipinski violation as a failure.** The Rule of Five
  tolerates one violation. The analysis carries an advisory when exactly
  one Lipinski criterion is exceeded. A finding that says "fails
  Lipinski" from one violation has made a stricter claim than the rule.
- **Boundary effects at threshold values.** A compound at exactly the
  configured MW or LogP threshold is classified by the tool under the
  configured threshold set. Do not reclassify — the verdict is a
  function of the configured thresholds, and the thresholds are stamped
  in the artifact.
- **Comparing descriptors across threshold sets or programs.** Descriptor
  values are comparable; verdicts are not, because different threshold
  sets may apply different limits. A "drug-like" verdict under one
  program's thresholds and a "rule-violations" verdict under another do
  not conflict — they were judged against different criteria.
- **Computing descriptors yourself instead of using the tool.** The tool
  pins the RDKit version and the descriptor set. A descriptor computed
  outside the tool may use a different algorithm, different hydrogen
  handling, or a different version. The finding must cite the tool's
  output, not a recalculation.
