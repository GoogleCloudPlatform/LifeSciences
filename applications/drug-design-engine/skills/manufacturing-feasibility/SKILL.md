---
name: manufacturing-feasibility
description: >
  Assess whether a concept's modality, delivery assumptions, and entity
  can be manufactured — production-platform fit, synthetic accessibility
  heuristics, and biologic feasibility. Use when evaluating whether a
  proposed intervention has a plausible manufacturing path at Stage 0,
  when incorporating SA-score into a manufacturing assessment (not a
  drug-likeness assessment), or when checking whether a pre-entity
  concept needs manufacturing evidence yet. Do not use for drug-likeness
  or compound property profiling (use compound-property-profile), for
  synthesis route planning or retrosynthetic analysis (not yet tooled),
  or for GMP/regulatory manufacturing readiness (Stage 4, not yet
  implemented).
---

## 1. When to use, and when not

Use this skill when you need to assess manufacturing feasibility for
an intervention concept:

- **Stage 0 gate**: does the proposed modality + delivery combination
  have a plausible production path at all?
- **SA-score interpretation for manufacturing**: what does the SA-score
  mean for manufacturing feasibility (as distinct from drug-likeness)?
- **Pre-entity concepts**: a concept with no physical entity yet should
  produce `not_yet_applicable`, not a failure — this skill handles
  that correctly.
- **Biologic manufacturing**: qualitative assessment of expression
  system feasibility for antibodies, biologics, peptides, etc.
- **Complexity flagging**: stereocenter/step-count heuristics for
  prioritization (NOT for automatic rejection).

**Do not use when:**

- You need drug-likeness or compound property profiling
  -> `compound-property-profile`.
- You need pocket druggability
  -> `pocket-druggability`.
- You need a demonstrated synthetic route or retrosynthetic analysis
  -> not yet tooled; report as blocked.
- You need real process chemistry, formulation, or developability data
  -> Stages 2-4; underlying tools not yet available.
- You need GMP or regulatory manufacturing readiness
  -> Stage 4; not yet implemented.

## 2. Preconditions

- **Input**: a concept record (JSON) with at least `modality`.
  Optionally `entity_ref` (compound SMILES, sequence identifier, or
  construct ID) and `delivery_assumptions` (route, formulation, vehicle).
- **SA-score** (optional): if a small-molecule concept has a structure,
  an existing SA-score record from `dde compound sa-score` can be
  incorporated.  The SA-score is NOT recomputed by this skill — use
  the existing `compound sa-score` command to compute it first.
- **RDKit** (optional): if installed, complexity heuristics
  (stereocenter count, ring analysis, step estimates) are computed
  from the entity_ref SMILES.  If RDKit is not available, these
  heuristics are skipped (not fabricated).
- **No authentication** needed — all operations are local.
- **No network** — all operations are offline.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Does this concept have a plausible manufacturing path? | `dde manufacturing assess-stage0 <CONCEPT_PATH>` | `raw/manufacturing/<IC-NNN>.manufacturing-stage0.json` |
| What are the stage-gated requirement structures? | `dde manufacturing stage-requirements` | (stdout only) |
| What is this compound's SA-score? (prerequisite) | `dde compound sa-score <SMILES>` | `raw/compounds/<slug>.sa-score.json` |

To incorporate SA-score into the manufacturing assessment:
```
dde compound sa-score <SMILES>
dde manufacturing assess-stage0 <CONCEPT_PATH> --sa-score-path raw/compounds/<slug>.sa-score.json
```

## 4. Interpretation contract

### The SA-score distinction (CRITICAL)

**SA-score is NOT synthesizability.** The SA-score (Ertl & Schuffenhauer
2009) is a fragment-frequency heuristic: it rates how common the
molecule's fragments are in known compounds, on a 1-10 scale (1 = easy,
10 = hard).

- A **low SA-score** does NOT mean the molecule is synthesizable —
  it means its substructures are common in known chemistry.
- A **high SA-score** does NOT mean the molecule cannot be synthesized
  — it means its substructures are unusual.
- **Neither** substitutes for a demonstrated synthetic route or a
  scalable manufacturing process.

This distinction must survive into every finding.  A finding that says
"this compound is manufacturable because SA-score = 2.1" has collapsed
the distinction.  The correct statement is "SA-score 2.1 indicates
common substructure fragments; manufacturing feasibility requires route
analysis."

### Stereocenter/step-count heuristics

Stereocenter counts and estimated step counts are **scoped heuristics
for prioritization**, not universal scientific vetoes.

- A molecule with many stereocenters is **flagged with the specific
  concern** — it is not automatically rejected.
- The requirement type is `prioritization_heuristic` (informs ordering)
  or sponsor-specific `hard_constraint` (per #11 policy) — never an
  unconditional `scientific_cutoff` that silently kills a concept.
- Step-count estimates are derived from molecular size, not from
  retrosynthetic analysis.  They are rough ranges, not predictions.

### Pre-entity concepts

A concept with `entity_ref: null` (no physical entity yet) produces
`not_yet_applicable` for entity-level manufacturing assessments.  This
is per #75's evidence status semantics:

- `not_yet_applicable` = cannot be assessed at this stage, with a
  later trigger.
- NOT `not_assessed` (which would mean no assessment was performed —
  but we did assess and determined it's not applicable yet).
- NOT a failure or error.
- NOT a fabricated result.

The trigger for future assessment is documented in the finding: it
becomes applicable when `entity_ref` is populated.

### No fabrication

**No yield, cost of goods, stability, or formulation property may be
invented.**  If the data does not exist:

- The evidence status is `not_assessed` or `not_yet_applicable`
- Never a plausible-looking number
- The finding explicitly states what is NOT assessed
- The `next_evidence` field names what would be needed

Qualitative Phase 1 findings cite:
- **Precedent**: established production platforms for the modality
- **Assumptions**: listed explicitly (e.g., standard infrastructure)
- **Limitations**: what is NOT assessed (e.g., sequence liabilities)
- **Owner**: who should perform the next assessment
- **Next required evidence**: what Stage 2+ would need

### Stage-gated requirements

Manufacturing assessment uses progressive requirements:

| Stage | Assessment | Status |
|---|---|---|
| **0** | Product/modality/delivery and production-platform fit | Implemented |
| **2** | Entity/route or therapeutic-sequence feasibility | Placeholder |
| **3** | Formulation/process/developability evidence | Placeholder |
| **4** | Supply and applicable readiness evidence | Placeholder |

Stages 2-4 are defined structurally (evidence types, required inputs)
so that #11 policy requirements can reference them by evidence_type.
Their actual assessment logic is not implemented because the underlying
tools do not exist yet.

### Reassessment

Manufacturing assessment records use the `supersedes` field from #75's
assessment schema (§2.2).  An initial manufacturing concern can be
superseded by a new assessment when new evidence arrives.  This keeps
manufacturing obstacles eligible for reviewed reassessment under #78.

### Neither early screen implies GMP or manufacturing clearance

Stage 0 manufacturing assessment is a coarse feasibility check.  It
determines whether a plausible production path exists for the proposed
modality, not whether the product can be manufactured at clinical or
commercial scale.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the
tool emitted.  If the tool did not run, there is no value.  If a
required tool is unavailable, report the task as blocked.  Do not
estimate, and do not proceed on an assumed result.

### Named pathologies

- **Treating SA-score as synthesizability.**  SA-score 2.0 does NOT
  mean "this compound can be synthesized."  It means the substructures
  are common in known chemistry.  Manufacturing feasibility requires a
  demonstrated route, not a heuristic score.
- **Treating stereocenter count as an automatic rejection.**  A
  molecule with 5 stereocenters requires careful planning, but is not
  automatically unmanufacturable.  Taxol has 11 stereocenters and is
  manufactured at scale.  Flag the concern; do not reject.
- **Fabricating yield, cost, or stability data.**  These require real
  process data.  A plausible-looking number ("estimated yield 45%")
  is fabrication.  The correct output is `not_assessed` with a
  `next_evidence` pointing to the required data.
- **Confusing modality-level and entity-level assessment.**  A
  "biologic" modality has known production platforms (modality-level:
  supported).  Whether a specific antibody sequence can be expressed
  without aggregation is an entity-level question (requires sequence
  analysis).  The skill keeps these distinct.
- **Treating not_yet_applicable as a failure.**  A pre-entity concept
  that produces `not_yet_applicable` is correctly assessed — the
  assessment is that entity-level manufacturing evaluation cannot
  happen yet, not that it failed.
