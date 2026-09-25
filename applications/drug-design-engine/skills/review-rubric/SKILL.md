---
name: review-rubric
description: Scoring axes with calibrated anchors, review types, and review output format for hypothesis evaluation.
---

# Review Rubric

This skill teaches you how to evaluate hypotheses using a structured rubric with
four core scoring axes plus two optional run-anchoring axes. Every review must assign
a score on each required axis using the calibrated anchors defined here. Consistent
anchoring is critical — these definitions are reused across reflection and ranking
agents to ensure scores are comparable.

This protocol contributes records to a run that the supervisor ultimately
publishes with `dde hypex analyze`; workers do not publish a separate result.

## Scoring Axes

Each axis is scored from 1 (worst) to 5 (best). Use the calibrated anchors
below to assign scores. When a hypothesis falls between two anchors, use the
lower score — err on the side of rigor.

---

### Correctness (1–5)

Evaluates whether the proposed mechanism is consistent with existing evidence.

| Score | Anchor | Description |
|---|---|---|
| **5** | Mechanism supported by multiple independent experimental lines | At least 3 independent studies directly support the core mechanism. Evidence spans different experimental systems (e.g., in vitro + in vivo + clinical). **Zero phantom citations.** No known contradictions. |
| **4** | Mechanism consistent with existing evidence, no contradictions found | 1–2 direct supporting studies exist. The mechanism is plausible and fits within established biological/chemical frameworks. **Zero phantom citations.** No contradicting evidence found after targeted search. |
| **3** | Mechanism plausible but limited direct evidence | The mechanism is biologically reasonable but relies primarily on indirect or analogical evidence. Direct experimental support is absent or from a single preliminary study. **Zero phantom citations.** |
| **2** | Mechanism speculative, some evidence is indirect or from distant domains | The mechanism requires extrapolation across domains (e.g., applying an in vitro finding to a whole-organism claim). Key steps in the causal chain lack any supporting evidence. **Also: Cap for non-critical phantom references** — if 1 non-core supporting citation is identified as phantom, but other citations are verified and the underlying mechanism remains plausible, correctness is strictly capped at 2. |
| **1** | Contradicted by existing evidence or logically inconsistent | Published evidence directly contradicts the core mechanism, OR the proposed causal chain contains a logical inconsistency (e.g., effect precedes cause, violates known physical constraints), **OR the primary or core causal mechanism relies on a phantom citation**. |

**How to assess correctness:**

1. **Automated citation verification (`dde cite verify`):**
   Run automated citation verification on the hypothesis file:
   ```bash
   dde cite verify <run-dir>/hypotheses/H-XXXX.json --out raw/citations
   ```
   Read the resulting manifest (`<run-dir>/citations/H-XXXX.json`) to populate:
   - `verified_citations`: Extracted from manifest entries where `status == "verified"`.
   - `phantom_citations`: Extracted from manifest entries where `status == "phantom"`.
   - `citation_manifest`: Path to the manifest (e.g., `"citations/H-0001.json"`).

2. **Apply phantom citation scoring penalties:**
   - **Zero phantom citations required for scores 5, 4, and 3:** Any phantom citation disqualifies the hypothesis from scores 3–5.
   - **Score 2 (Cap for Non-Critical Phantom References):** If 1 non-core supporting citation is identified as phantom, but other citations are verified and the underlying mechanism remains plausible:
     - Correctness score is **strictly capped at 2**.
     - Reviewer MUST log in `key_criticisms`:
       `"PHANTOM CITATION: Citation [lit_id] ([claimed_title]) does not exist or has a fabricated title. Correctness penalized."`
   - **Score 1 (Critical Phantom Evidence):** If the primary or core causal mechanism relies on a phantom citation, Correctness is **assigned 1**.
   - **Check quarantine triggers:** If `phantom_count >= 2`, OR `phantom_count / total_citations >= 0.50`, OR (`total_citations > 0` AND `verified_count == 0`), trigger immediate quarantine per the `safety-screen` skill.
   - **Unverified citations:** Citations with `status: "unverified"` (e.g., network error, timeout, upstream rate-limit) do NOT trigger phantom penalties; they are logged as unverified notes.

3. **Verify claims against paper contents:** For verified citations, resolve identifiers with `dde litref resolve` and inspect the stored DDE literature artifacts as needed to confirm that the paper actually demonstrates what the hypothesis claims in its `note` field.
4. **Search for contradicting evidence:** Search for contradicting evidence using terms designed to find counter-results (e.g., if the hypothesis claims "X increases Y", search for "X decreases Y" or "X no effect Y").
5. **Check causal chain completeness:** Check whether the causal chain has gaps — are there steps that are assumed but not supported by any citation?
6. **Evaluate evidence quality:** Consider the quality of the cited evidence: peer-reviewed > preprint > review article > conference abstract.

---

### Novelty (1–5)

Evaluates how much the hypothesis adds beyond what is already known or published.

| Score | Anchor | Description |
|---|---|---|
| **5** | Proposes an entirely unexplored mechanism or connection | No published work proposes this mechanism or connection. The hypothesis opens a new research direction that does not appear in any surveyed literature. |
| **4** | Novel combination of known mechanisms applied to a new context | Individual components are known, but the specific combination or application to this context has not been published. Represents a creative synthesis across fields. |
| **3** | Incremental extension of existing work with meaningful new insight | Extends existing published work in a non-trivial way — adds a new prediction, proposes a new experimental test, or identifies an unexplored implication. |
| **2** | Close variant of existing published hypotheses | Very similar to already-published proposals. The distinguishing features are minor (e.g., same mechanism applied to a slightly different tissue or organism). |
| **1** | Already published or obvious from current literature | The hypothesis (or its essential content) has already been published. A literature search returns papers that state essentially the same claim. |

**How to assess novelty:**

1. Search for prior art: use the hypothesis title and key mechanism terms as
   search queries across `dde pubmed search` and `dde preprint search --source arxiv`.
2. Check whether any of the cited papers already propose the same mechanism.
3. Consider the *combination* — even if individual elements are known, the
   specific synthesis may be novel.
4. Distinguish between "new to me" and "new to the field" — only the latter
   counts.

---

### Testability (1–5)

Evaluates how practically the hypothesis can be tested with current technology.

| Score | Anchor | Description |
|---|---|---|
| **5** | Can be tested with a single well-defined experiment using standard methods | A single experiment using widely available techniques (PCR, ELISA, standard cell culture, behavioral assays, etc.) would produce a clear positive or negative result. Timeline: months, not years. |
| **4** | Testable with 2–3 experiments using established techniques | Requires a small set of complementary experiments, all using established methods. May require some optimization but no fundamental method development. Timeline: ~1 year. |
| **3** | Testable but requires specialized equipment or novel assay development | The predictions are falsifiable in principle with existing technology, but testing requires access to specialized equipment (e.g., cryo-EM, advanced imaging, specific animal models) or development of a new assay. Timeline: 1–2 years. |
| **2** | Testable only in principle; no clear experimental path with current technology | The hypothesis makes predictions that are theoretically observable but cannot be measured with current methods. Would require significant technological advances or access to unavailable resources (e.g., human brain tissue with specific conditions). |
| **1** | Unfalsifiable or requires infeasible experimental conditions | The hypothesis cannot be falsified by any conceivable experiment, OR the required experimental conditions are practically impossible (e.g., decades-long longitudinal study in a non-existent cohort, ethically prohibited experiments). |

**How to assess testability:**

1. Read each proposed experiment carefully. Is the design complete enough to
   actually execute? Are controls specified?
2. Evaluate the readout — would the expected result unambiguously support or
   refute the hypothesis?
3. Consider feasibility: equipment availability, model system access, timeline,
   estimated cost (as reflected in `est_difficulty`).
4. Check whether the predictions are specific enough to be falsifiable — "X
   will change" is weak; "X will increase by ≥ 20% in condition Y vs. control Z"
   is strong.

---

### Safety (1–5)

Evaluates dual-use risk — whether the hypothesis, if pursued, could enable
misuse for harmful purposes.

| Score | Anchor | Description |
|---|---|---|
| **5** | No dual-use concern; purely observational or diagnostic | The hypothesis involves observation, measurement, or diagnostic methods with no plausible pathway to harmful application. Examples: disease biomarker discovery, ecological observation, computational modeling of existing data. |
| **4** | Minimal dual-use potential; standard lab safety measures sufficient | The research involves standard biological or chemical materials. Any dual-use potential is theoretical and well within established biosafety norms (BSL-1 or BSL-2). Standard institutional oversight applies. |
| **3** | Some dual-use consideration but well within established research norms | The research touches on areas with known dual-use considerations (e.g., antimicrobial resistance mechanisms, immune evasion pathways) but does not propose creating or enhancing dangerous capabilities. Institutional biosafety review is appropriate. |
| **2** | Notable dual-use potential; requires institutional review | The hypothesis proposes work that could, if extended or misapplied, enhance the transmissibility, virulence, or host range of pathogens, OR involves techniques that could be repurposed for harmful applications. Requires dual-use review (DURC) by an institutional committee. **Triggers quarantine.** |
| **1** | Clear dual-use risk; must be quarantined and flagged | The hypothesis directly proposes gain-of-function modifications, synthesis of dangerous pathogens, weaponizable delivery mechanisms, or other work with clear potential for catastrophic misuse. **Must be immediately quarantined.** |

**How to assess safety:**

1. Read the hypothesis mechanism and proposed experiments for any dual-use
   implications.
2. Apply the safety-screen skill's detailed dual-use rubric for borderline
   cases.
3. When safety score ≤ 2, invoke the quarantine procedure defined in the
   safety-screen skill.
4. When in doubt, score lower (more cautious). A false positive (quarantining
   a safe hypothesis) is far less costly than a false negative.

---

### Goal Alignment (1–5)

Evaluates how directly the hypothesis addresses the run's stated research goal in `run.yaml.goal`.

| Score | Anchor |
|---|---|
| **5** | Directly and centrally addresses the stated goal. If validated, it materially answers the goal question. |
| **4** | Addresses the goal via one intermediate step, or answers a well-defined sub-question of it. |
| **3** | Relevant to the goal's domain but targets an adjacent question; validation would inform the goal only indirectly. |
| **2** | Same field, different question. Full validation would not advance the stated goal. |
| **1** | Off-goal. Answers a question this run did not ask. |

**How to assess goal alignment:**

1. Read the research goal from `run.yaml` (in `<run-dir>/run.yaml`).
2. Compare the hypothesis statement, mechanism, and primary predictions against the goal.
3. Consider: if this hypothesis were completely proven true, how much progress would that represent toward answering the run's goal?
4. Assign the score using the anchors above. Feeds $S_{\text{goal}}$ in composite Elo ranking ($S = (\text{score} - 1) / 4$; score 3 is neutral $S = 0.5$).

---

### Constraint Compliance (1–5)

Evaluates whether the hypothesis satisfies every hard constraint listed in `run.yaml.constraints`.

| Score | Anchor |
|---|---|
| **5** | Satisfies every listed constraint, and the hypothesis or its experiments name the relevant constraint(s) explicitly. |
| **4** | Satisfies every listed constraint; compliance is evident from the text but not explicit. |
| **3** | Satisfies all constraints on the most likely reading, but at least one is ambiguous and turns on interpretation. |
| **2** | Violates one non-central constraint, or would require modification to comply. |
| **1** | Violates a central constraint. Not admissible as stated. |

**How to assess constraint compliance:**

1. Read the list of constraints from `run.yaml` (in `<run-dir>/run.yaml`).
2. If `run.yaml` lists no constraints (or the `constraints` field is absent), **omit** `constraint_compliance` entirely. Do not score it 3.
3. Check the hypothesis and its proposed experiments against each constraint individually.
4. Assign the score using the anchors above based on the least compliant constraint. Feeds $S_{\text{comp}}$ in composite Elo ranking ($S = (\text{score} - 1) / 4$; score 3 is neutral $S = 0.5$).

---

### Optional Scoring Axes and Omission Rules

`goal_alignment` and `constraint_compliance` are optional in the review schema but guided by convention based on review type and run configuration.

**Omission rule:** If `run.yaml` lists no constraints, **omit** `constraint_compliance` entirely. Do not score it 3. Absent scores map to neutral $S = 0.5$ in composite Elo without altering the rank, whereas an explicit score of 3 asserts that constraints were evaluated and found middling.

**Per-review-type requirements:**

| `review_type` | `goal_alignment` | `constraint_compliance` |
|---|---|---|
| `full` | required by convention | required by convention if constraints exist |
| `deep` | required by convention | required by convention if constraints exist |
| `recurrent` | required by convention (evolution is where goal drift enters) | required by convention if constraints exist |
| `initial` | optional | optional |
| `safety` | omit | omit |

"Required by convention" means the reflection template and rubric instruct it, while the JSON schema (`schemas/review.schema.json`) keeps both optional so that no review type is ever forced to invent a score it did not assess.

---

## Review Types

Each review type defines what the reviewer does and which tools are available.
The supervisor's task message specifies which review type to perform; if not
specified, use `full` as the default.

### `initial` — Plausibility Check (No Tools)

**Purpose:** Rapid first-pass assessment of internal consistency and
plausibility.

**Tools:** None — this is a pure reasoning pass.

**What to do:**
1. Read the hypothesis JSON.
2. Check internal consistency: does the mechanism logically lead to the
   predictions? Do the experiments actually test the predictions?
3. Check for obvious errors: circular reasoning, unfalsifiable claims,
   missing causal links.
4. Assign preliminary scores on the four core axes (preliminary scores for `goal_alignment` and `constraint_compliance` are optional).
5. Note concerns to investigate in a full review.

**When to use:** When the supervisor needs a quick triage pass to prioritize
which hypotheses deserve a full review. Not typically used in the standard
review pipeline — the standard pipeline starts at `full`.

### `full` — Standard Review (With Tools)

**Purpose:** Thorough evidence-based review with citation verification and
literature search.

**Tools:** `dde cite verify`, `dde cite analyze`, `dde litref resolve`,
`dde pubmed search`, and `dde preprint search`.

**What to do:**
1. **Verify citations:** Run automated citation verification on the hypothesis:
   ```bash
   dde cite verify <run-dir>/hypotheses/H-XXXX.json --out raw/citations
   ```
   Read the resulting manifest (`<run-dir>/citations/H-XXXX.json`) to populate:
   - `verified_citations`: Extracted from manifest entries where `status == "verified"`.
   - `phantom_citations`: Extracted from manifest entries where `status == "phantom"`.
   - `citation_manifest`: Path to the manifest (e.g., `"citations/H-XXXX.json"`).

   Evaluate the manifest findings and apply scoring penalties or quarantine:
   - **Scores 5, 4, 3** require zero phantom citations.
   - **Score 2 cap:** If 1 non-core supporting citation is identified as phantom, but other citations are verified and the underlying mechanism remains plausible:
     - Correctness score is **strictly capped at 2**.
     - Reviewer MUST log in `key_criticisms`:
       `"PHANTOM CITATION: Citation [lit_id] ([claimed_title]) does not exist or has a fabricated title. Correctness penalized."`
   - **Score 1 assignment:** If the primary or core causal mechanism relies on a phantom citation, assign Correctness score 1.
   - **Quarantine check:** Check if quarantine triggers are met (`phantom_count >= 2`, OR `phantom_count / total_citations >= 0.50`, OR `total_citations > 0` AND `verified_count == 0`). If triggered, execute quarantine procedure per the `safety-screen` skill.
   - **Unverified citations:** Citations with `status: "unverified"` (e.g., network error, timeout, upstream rate-limit) do NOT trigger phantom penalties; record them as unverified notes.
   - For verified citations, use `dde litref resolve <identifier>` and inspect the stored artifact to confirm the paper actually demonstrates what the hypothesis claims in its `note` field. If a paper does not support the claim, note as a key criticism.
2. **Search for contradicting evidence:** Construct search queries designed to
   find counter-evidence for the core mechanism. Use `dde pubmed search` with
   terms that negate the hypothesis (e.g., if the hypothesis claims A causes B,
   search for "A does not affect B" or "A inhibits B").
   - Record any contradicting papers in the `contradicting_evidence` array.
3. **Search for prior art:** Search for existing publications that propose the
   same or very similar hypothesis. This informs the novelty score.
4. **Assess each scoring axis** using the calibrated anchors above. Assign scores for all four core axes, plus `goal_alignment` and (if `run.yaml` lists constraints) `constraint_compliance`.
5. **Write a verdict** summarizing the overall assessment.
6. **List key criticisms** — specific, actionable concerns.

### `deep` — Assumption Decomposition

**Purpose:** Rigorous analysis of the hypothesis's foundational assumptions.

**Tools:** Same as `full`.

**What to do:**
1. **Decompose into assumptions:** Break the hypothesis mechanism into its
   constituent assumptions. Each step in the causal chain is a separate
   assumption. Example: "A causes B, which activates C, leading to D" has
   three assumptions (A→B, B→C, C→D).
2. **Rank assumptions by weakness:** For each assumption, assess the strength
   of supporting evidence. Identify the weakest link in the chain.
3. **Probe the weakest assumption:** Run targeted literature searches
   specifically on the weakest assumption. Try to find either supporting
   evidence or contradicting evidence.
4. **Update scores** based on findings — a weak foundational assumption may
   lower the correctness score even if other parts are well-supported. `goal_alignment` and `constraint_compliance` are also assessed (or carried forward).
5. **Add specific criticisms** about the weakest assumption(s).

### `safety` — Dual-Use Safety Screen

**Purpose:** Focused safety assessment using the dual-use rubric.

**Tools:** None required (reasoning-based), but DDE literature tools are available for
context.

**What to do:**
1. Apply the dual-use biosafety rubric defined in the `safety-screen` skill.
2. Evaluate the hypothesis mechanism and proposed experiments against the
   list of concerning topics.
3. Assign the safety score using the calibrated anchors above. Omit `goal_alignment` and `constraint_compliance`.
4. If safety score ≤ 2, execute the quarantine procedure from the
   `safety-screen` skill.

### `recurrent` — Diff vs. Parent (Deferred)

<!-- Deferred to Batch 3C. Recurrent review mode compares an evolved hypothesis
     against its parent, specifically checking whether prior review criticisms
     were addressed. This section will be expanded when evolution operators are
     implemented. -->

**Status:** Deferred to Batch 3C; run `hypex --help` for current review types. When active, `goal_alignment`
is required by convention (evolution is where goal drift enters); `constraint_compliance`
is required by convention if constraints exist.

---

## Review Output Format

Every review must produce a JSON file conforming to `schemas/review.schema.json`.
Use `hypex add-review --run <run-id> --run-dir <run-base> --for <hypothesis-id>` to automatically assign
the next scoped review ID, validate against the schema, and write atomically to
`reviews/H-XXXX.R-NN.json`:

```bash
cat <<'EOF' | hypex add-review --run <run-id> --run-dir <run-base> --for <hypothesis-id> -
{
  "review_type": "full",
  "scores": {
    "correctness": 4,
    "novelty": 3,
    "testability": 5,
    "safety": 5
  },
  "verdict": "...",
  "key_criticisms": [...],
  "verified_citations": [...],
  "contradicting_evidence": [...],
  "reviewer": "<your-agent-name>",
  "epoch": 0
}
EOF
```

The review ID can also be pre-allocated via `hypex next-id --run <run-id> --run-dir <run-base> review --for <hypothesis-id>`.
The required fields are:

```json
{
  "id": "H-0001.R-01",
  "hypothesis_id": "H-0001",
  "review_type": "full",
  "scores": {
    "correctness": 4,
    "novelty": 3,
    "testability": 5,
    "safety": 5,
    "goal_alignment": 5,
    "constraint_compliance": 4
  },
  "verdict": "Strong hypothesis with clear experimental path. Core mechanism is well-supported but relies heavily on a single mouse model study. Novelty is moderate — similar gut-brain axis proposals exist, though the specific SCFA-vagal pathway is less explored.",
  "key_criticisms": [
    "Primary supporting evidence (PMID:38012345) is correlational, not causal",
    "Vagotomy experiment is high-difficulty and may take 2+ years",
    "Prior art exists for general gut-brain axis in neurodegeneration (PMID:36543210)"
  ],
  "verified_citations": ["PMID:38012345", "arXiv:2401.12345"],
  "phantom_citations": [],
  "citation_manifest": "citations/H-0001.json",
  "contradicting_evidence": ["PMID:37654321"],
  "reviewer": "reflection-agent-1",
  "epoch": 0
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `id` | string | Review ID in `H-XXXX.R-NN` format (allocated automatically by `hypex add-review`, or via `hypex next-id --run <run-id> --run-dir <run-base> review --for <hypothesis-id>`). |
| `hypothesis_id` | string | ID of the hypothesis being reviewed. |
| `review_type` | string | One of: `initial`, `full`, `deep`, `safety`, `recurrent`. |
| `scores` | object | Scoring axes object (integers 1–5). Contains the 4 core required axes plus 2 optional axes. |
| `scores.correctness` | integer | Core required. Mechanism plausibility against evidence (1–5). |
| `scores.novelty` | integer | Core required. Distinctness from existing published literature (1–5). |
| `scores.testability` | integer | Core required. Feasibility and falsifiability with current technology (1–5). |
| `scores.safety` | integer | Core required. Dual-use biosafety risk (1–5; ≤ 2 triggers quarantine). |
| `scores.goal_alignment` | integer | Optional (required by convention for `full`, `deep`, `recurrent`). Directness addressing `run.yaml.goal` (1–5). Feeds $S_{\text{goal}}$ in composite Elo. |
| `scores.constraint_compliance` | integer | Optional (required by convention for `full`, `deep`, `recurrent` if constraints exist). Compliance with `run.yaml.constraints` (1–5). Omit if no constraints declared. Feeds $S_{\text{comp}}$ in composite Elo. |
| `verdict` | string | Summary assessment — 2–4 sentences covering the overall evaluation. |
| `key_criticisms` | array | Specific, actionable concerns. Each item is a concrete criticism, not a vague complaint. |
| `verified_citations` | array | `lit_id` values from the hypothesis evidence independently verified via `dde cite verify` / `dde litref resolve`. Empty array if review type is `initial`. |
| `phantom_citations` | array | `lit_id` values identified as non-existent or fabricated via `dde cite verify`. Empty array if none found. |
| `citation_manifest` | string | Relative path to the immutable citation manifest (e.g. `citations/H-0001.json`). |
| `contradicting_evidence` | array | `lit_id` values of papers found during the review that contradict the hypothesis. Empty array if none found. |
| `reviewer` | string | Name of the reviewing agent. |
| `epoch` | integer | Epoch in which this review was produced. |

### Score Aggregation

The **mean review score** is the unweighted mean of the four core axis scores. This
is used by the supervisor for triage and by the ranking agent for Swiss
seeding (distinguished from composite Elo rating). Do not include the mean review
score in the review JSON — it is computed by consumers.

```
mean_review_score = (correctness + novelty + testability + safety) / 4
```
