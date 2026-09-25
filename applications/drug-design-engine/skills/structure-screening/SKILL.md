---
name: structure-screening
description: >
  Bounded structure screening for Stage 0 pre-commitment fast-fail.
  Retrieves existing structures/models within a declared screen budget,
  runs pocket druggability analysis via the pocket-druggability contract,
  and produces evidence assessment records. Use when evaluating whether
  a target has a druggable pocket as a pre-commitment filter, before
  committing resources to detailed structural characterization.
  Do not use for detailed characterization (that follows accepted
  prerequisites and resource budgets), compound screening (use
  the screen command), or modalities where pocket geometry is not
  the relevant tractability test (the screening layer detects this
  and produces an appropriately scoped assessment).
---

## 1. When to use, and when not

Use this skill when you need to run a bounded structural druggability
screen as a pre-commitment filter (Stage 0 / Tier 0). Entry points
include:

- Determining whether a target has a druggable pocket before
  committing it as a primary intervention target.
- Evaluating structural tractability alongside competitive landscape
  and mechanism-direction as a pre-commitment filter.
- Screening a target when only existing structures/models are
  available (no new AF3 prediction budget).

**Do not use when:**

- You need detailed structural characterization (that requires accepted
  prerequisites and a resource budget — this skill produces a fast-fail
  filter, not a thorough characterization).
- You need to rank different targets against each other by pocket
  score — the drug score ranks conformations of one target, not
  targets against each other (the pocket-druggability contract forbids
  this, and this skill preserves that constraint).
- You need to run a new AF3 prediction — retrieval of existing models
  only; new predictions require separate justification.
- The modality is not small-molecule or molecular-glue — pocket
  geometry is not the relevant tractability test for biologics,
  antibodies, gene therapies, etc. The screening layer will detect
  this and produce a `not_yet_applicable` assessment.

## 2. Relationship to pocket-druggability

**`pocket-druggability/SKILL.md` is the interpretation authority.**
This skill orchestrates pocket-druggability; it does not replace or
duplicate it. The relationship is:

| Concern | Authority |
|---|---|
| Single-structure pocket detection and scoring | pocket-druggability |
| Verdict semantics and relay codes | pocket-druggability |
| Threshold set (`pocket@1.0`) | pocket-druggability / thresholds.py |
| Structure retrieval and budget | structure-screening (this skill) |
| Modality applicability | structure-screening (this skill) |
| Site relevance assessment | structure-screening (this skill) |
| Evidence assessment record production | structure-screening (this skill) |

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Screen structures for druggable pockets | `dde structure-screen run <STRUCTURE>... --concept-ref IC-NNN --modality small_molecule` | Evidence assessment records (stdout / `--json`) |
| Screen with site-specific query | `dde structure-screen run <STRUCTURE>... --concept-ref IC-NNN --modality small_molecule --near A:145,A:146` | Evidence assessment records with site relevance |
| Screen with custom budget | `dde structure-screen run <STRUCTURE>... --concept-ref IC-NNN --modality small_molecule --max-structures 3 --max-seconds 120` | Budget-bounded assessment records |

The `structure-screen run` command internally invokes `dde pocket run`
then `dde pocket analyze` for each candidate structure within the
screen budget. It produces `dde.evidence-assessment.v1` records with
relay codes and scoping preserved from the underlying pocket analysis.

Options:

- `--concept-ref` (required): concept reference (IC-NNN) the screen is for.
- `--modality` (required): intervention modality (`small_molecule`, `molecular_glue`, `antibody`, etc.). Non-pocket-relevant modalities produce a `not_yet_applicable` assessment immediately.
- `--near`: residues defining the intervention site (CHAIN:RESNUM, comma-separated). Passed through to `dde pocket analyze --near`.
- `--source`: structure source type (`pdb`, `alphafold_db`, `existing_model`). Default: `pdb`.
- `--experimental/--no-experimental`: whether structures are experimental. Auto-detected from source type if omitted.
- `--max-structures`: maximum structures to evaluate (default: 5).
- `--max-seconds`: maximum wall-clock seconds (default: 300).
- `--json`: machine-readable JSON output.
- `--quiet`: paths only.
- `--claim`: the claim being assessed (default: "target has a druggable binding pocket").

## 4. Preconditions

- **Structure retrieval**: existing AlphaFold DB models or PDB
  structures must be retrievable. If no suitable structure exists,
  the screen produces a `not_assessed` / `data_unavailable` record.
- **fpocket on PATH**: required for pocket analysis. If missing,
  the screen produces a `tool_unavailable` record.
- **Screen budget**: a declared budget (max structures, max wall-clock
  time). The screen respects this budget.

## 5. Workflow

### Step 1: Check modality applicability

Before any structural work, check whether pocket geometry is the
relevant tractability test for the declared modality.

- **Small molecule / molecular glue**: proceed to Step 2.
- **Biologic / antibody / antisense / gene therapy / etc.**: produce
  a `not_yet_applicable` assessment and stop. Do not force a pocket
  score onto a modality where it is not the relevant question.

### Step 2: Retrieve candidate structures within budget

Retrieve existing structures for the target, prioritizing:

1. Experimental crystal structures from PDB
2. AlphaFold DB predicted models

**Do not** initiate a new AF3 prediction. If no suitable structure
exists, produce a `not_assessed` / `data_unavailable` record with
a clear note that a new prediction would require separate
justification.

### Step 3: Run pocket analysis per the pocket-druggability contract

For each candidate structure within the screen budget:

1. Run `dde pocket run <STRUCTURE>` to detect pockets.
2. Run `dde pocket analyze <POCKETS_RECORD>` to judge tractability.
3. If site residues are known, use `--near` to assess the specific
   intervention site.

### Step 4: Assess site relevance

**Do not treat ANY high-scoring cavity as a pass without assessing
relevance to the intended intervention site.**

- If `--near` was used, the verdict already answers the site question.
- If global analysis was used, note that site relevance was not
  confirmed — a druggable pocket elsewhere in the structure is not
  evidence of tractability at the intended site.

### Step 5: Produce evidence assessment records

For each evaluated structure, produce a `dde.evidence-assessment.v1`
record that:

- Carries the evidence status mapped from the pocket verdict
- Preserves all relay codes from the pocket analysis
- Records provenance, confidence/coverage, and conformation
- Notes site relevance assessment
- Proposes the next discriminating characterization where justified

## 6. Mandatory relay preservation

The following relays from pocket-druggability MUST be preserved
through the screening layer into the assessment record. They must
not be dropped, summarized away, or contradicted by the screening
layer's own verdict.

| Relay code | Kind | Obligation in screening context |
|---|---|---|
| `fpocket.single_conformation` | Qualifier | The assessment rationale must name the structure and state that a single conformation cannot settle the druggability question. The next-conformation suggestion must be included. |
| `fpocket.conformation_dependent` | Qualifier | The assessment must note that the score was computed on a non-experimental structure and constrains the model in both directions. |
| `fpocket.druggability_is_not_affinity` | Stop | The assessment must not present the pocket score as evidence of binding or affinity. If the program question is about binding, the assessment states that the question is untooled. |

## 7. Evidence status mapping

| Pocket verdict | Evidence status | Rationale |
|---|---|---|
| `druggable-pocket-present` (global) | `supported` (with site-relevance caveat) | A druggable pocket exists; site relevance requires `--near` confirmation |
| `site-druggable` | `supported` | Pocket at the requested site is druggable |
| `borderline` | `insufficient` | Score is equivocal; score another conformation |
| `site-borderline` | `insufficient` | Score at the site is equivocal |
| `no-druggable-pocket-in-this-conformation` | `insufficient` | Single conformation; not conclusive |
| `site-not-druggable-in-this-conformation` | `insufficient` | Single conformation at the site |
| `no-pocket-at-site-in-this-conformation` | `insufficient` | No pocket at the site in this conformation |
| `no-pockets-detected` | `insufficient` | No cavities found; may indicate input issue |
| Non-experimental structure | `insufficient` (always) | Score constrains the model, not the target |

**Why `insufficient` rather than `contradicted` for low scores:**
The CDK2 calibration data (scores 0.17, 0.29, and 0.94 across three
crystal structures of the same drugged ATP site) demonstrates that a
sub-cutoff score on one conformation does not contradict druggability.
The appropriate evidence status is `insufficient` — the evidence
exists but is not decisive — with the `fpocket.single_conformation`
relay carrying the calibration numbers.

## 8. Hard constraints

- **No new AF3 predictions.** Retrieval only within budget.
- **No cross-target ranking by raw pocket score.** The drug score
  ranks conformations of one target. Do not aggregate or compare
  scores across different targets.
- **No modification of pocket-druggability.** This skill wraps the
  existing contract; it does not change verdicts, relays, or
  thresholds.
- **Carry relays through.** If a relay fires on an underlying pocket
  analysis, the assessment record must preserve it.

## 9. What this skill produces

Following this workflow produces:

- One or more `dde.evidence-assessment.v1` records per target,
  one per evaluated structure.
- Each record carries the evidence status, relay codes, provenance,
  confidence, and rationale.
- The next discriminating characterization is proposed when the
  evidence is `insufficient`.
- Budget adherence is recorded: if the screen stopped due to
  budget constraints, this is noted in the final assessment.
