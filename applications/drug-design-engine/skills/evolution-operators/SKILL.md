---
name: evolution-operators
description: Four evolutionary operators (ground, simplify, combine, oob) for refining hypotheses, plus lineage tracking rules.
---

# Evolution Operators

This skill teaches you the four evolutionary operators used to derive improved
hypothesis variants from existing hypotheses. Each operator targets a specific
weakness identified by reviewers or tournament loss patterns. Every variant
records its lineage — parent IDs and the operator used — so the system can
track evolutionary history and enforce mandatory rematches.

This protocol contributes records to a run that the supervisor ultimately
publishes with `dde hypex analyze`; workers do not publish a separate result.

---

## Operator Reference

### 1. Ground

**Purpose:** Strengthen the weakest evidential claim in a hypothesis by finding
new supporting or contradicting literature.

**When to use:** Review criticisms cite weak evidence, unsupported claims, or
missing citations.

**Procedure:**

1. Read the hypothesis and its reviews. Identify the weakest evidential claim —
   the one reviewers flagged as poorly supported or speculative.
2. Run targeted literature searches to find evidence that supports or
   contradicts the weak claim:

   ```bash
   # PubMed for peer-reviewed biomedical evidence
   dde pubmed search "<mechanism>[MeSH Terms] AND <target>[MeSH Terms]" --max-results 20

   # arXiv for computational/theoretical evidence
   dde preprint search --source arxiv "cat:<category> AND abs:<keywords>" --max-results 10

   # bioRxiv for recent preprints
   dde preprint search --source biorxiv "<keywords>" --max-results 10
   ```

3. **If new supporting evidence is found:** Strengthen the claim with additional
   citations and refine the mechanism description to incorporate the new
   findings. Add the new literature to the `evidence` array.
4. **If contradicting evidence is found:** Rewrite the claim to accommodate the
   contradiction — narrow the scope, add a qualifying condition, or pivot the
   mechanism. Add the contradicting evidence with `"role": "contradicts"` or
   `"role": "constrains"`.
5. Produce the variant hypothesis with the updated evidence array and revised
   statement/mechanism as needed.

**Lineage:** One parent.

```json
"lineage": {"parents": ["H-0042"], "operator": "ground"}
```

---

### 2. Simplify

**Purpose:** Reduce experimental complexity and strengthen parsimony while
preserving the core testable prediction.

**When to use:** Review criticisms cite overcomplexity, untestable experimental
designs, too many moving parts, or difficulty distinguishing between alternative
explanations.

**Procedure:**

1. Read the hypothesis and its reviews. Identify sources of complexity:
   - Experiments with `"est_difficulty": "high"` that could be replaced with
     simpler alternatives
   - Redundant experiments that test overlapping aspects of the hypothesis
   - Mechanism descriptions that invoke multiple untested intermediate steps
   - Predictions that are not independently testable

2. Simplify while preserving the core insight:
   - **Reduce experimental complexity:** Replace high-difficulty experiments
     with lower-difficulty alternatives where possible. Consolidate redundant
     experiments into a single, cleaner design.
   - **Sharpen the mechanism:** Remove speculative intermediate steps. Focus on
     the most directly testable causal chain.
   - **Tighten predictions:** Remove predictions that are derivative of or
     redundant with other predictions. Each prediction should add independent
     testable information.
   - **Preserve falsifiability:** The simplified hypothesis must still be
     refutable by the remaining experiments.

3. Produce the variant hypothesis with simplified experiments, mechanism, and
   predictions. Do not drop evidence — evidence items should remain unless they
   support a claim you removed.

**Lineage:** One parent.

```json
"lineage": {"parents": ["H-0042"], "operator": "simplify"}
```

---

### 3. Combine

**Purpose:** Merge complementary aspects of two hypotheses from the same
proximity cluster into a stronger unified variant.

**When to use:** Two hypotheses in the same cluster address complementary
aspects of the same phenomenon — one explains mechanism A, the other explains
mechanism B, and a combined A+B mechanism is more complete and testable than
either alone.

**Procedure:**

1. Read both parent hypotheses and their reviews. Identify complementary
   strengths:
   - Does one hypothesis have stronger evidence where the other is weak?
   - Does one propose a mechanism that explains an observation the other cannot?
   - Do their experimental designs test different aspects of the same system?
   - Can their predictions be unified into a more powerful joint prediction?

2. **Do not simply concatenate.** A combined hypothesis is not "hypothesis A
   plus hypothesis B." It is a new hypothesis that synthesizes the
   complementary insights into a single coherent mechanism with unified
   predictions and experiments.

3. Build the combined variant:
   - **Statement:** A new falsifiable claim that integrates both parents'
     insights. It should be stronger or broader than either parent alone.
   - **Mechanism:** A unified causal mechanism that incorporates both parents'
     mechanistic contributions.
   - **Predictions:** Include predictions that test the joint mechanism —
     especially predictions that would distinguish the combined hypothesis
     from either parent individually.
   - **Experiments:** Design experiments that test the interaction or combined
     effect, not just each parent's claim independently.
   - **Evidence:** Merge evidence arrays from both parents. Deduplicate by
     `lit_id` — if both parents cite the same paper, keep one entry with the
     more informative note.

4. Produce the variant hypothesis with both parent IDs in the lineage.

**Lineage:** Two parents (required).

```json
"lineage": {"parents": ["H-0042", "H-0047"], "operator": "combine"}
```

---

### 4. Out-of-the-Box (oob)

**Purpose:** Transfer an insight from an adjacent or distant field to create a
novel variant with a fresh perspective.

**When to use:** The hypothesis space feels saturated — all hypotheses share
the same literature base, the same mechanisms, and the same experimental
approaches. Or when a hypothesis has stalled in tournament rankings without
clear evidence-based weaknesses to fix.

**Procedure:**

1. Read the hypothesis and its reviews. Identify the core phenomenon or
   mechanism it addresses.

2. Search for analogies in adjacent or distant fields:

   ```bash
   # Cross-category arXiv search — look for structural analogies
   dde preprint search --source arxiv "cat:q-bio.MN AND abs:<analogous mechanism>" --max-results 10
   dde preprint search --source arxiv "cat:cs.AI AND abs:<phenomenon keyword>" --max-results 10
   dde preprint search --source arxiv "cat:physics.bio-ph AND abs:<system keyword>" --max-results 10

   # Cross-domain PubMed search
   dde pubmed search "<distant field mechanism>[MeSH Terms] AND <target system>[MeSH Terms]" --max-results 15

   # Broad exploration for unexpected connections (DDE fan-out)
   dde pubmed search "<phenomenon> <distant field keyword>" --max-results 15
   dde preprint search --source arxiv "<phenomenon> <distant field keyword>" --max-results 10
   dde preprint search --source biorxiv "<phenomenon> <distant field keyword>" --max-results 10
   ```

3. Identify a transferable insight: a mechanism, mathematical framework,
   experimental technique, or conceptual model from the distant field that
   maps onto the hypothesis's domain.

4. Build the variant:
   - **Statement:** Incorporate the cross-disciplinary insight into a new
     falsifiable claim. Make the analogy explicit — state what is being
     transferred and why it applies.
   - **Mechanism:** Describe the mechanism using the transferred framework.
     Explain both the analogy (why the systems are structurally similar) and
     the specific prediction it generates.
   - **Evidence:** Include literature from both the original field and the
     source field. The source-field evidence supports the analogy; the
     original-field evidence grounds the claim in the target domain.
   - **Experiments:** Design experiments that specifically test whether the
     transferred insight holds in the target domain.

5. Produce the variant hypothesis with a single parent and `"oob"` operator.

**Lineage:** One parent.

```json
"lineage": {"parents": ["H-0042"], "operator": "oob"}
```

---

## Lineage Rules

Every variant hypothesis must record its lineage accurately. The lineage
tracks evolutionary history and enables mandatory rematches between parents
and their offspring.

### Structure

```json
"lineage": {
  "parents": ["H-NNNN"],
  "operator": "ground|simplify|combine|oob"
}
```

### Parent Count by Operator

| Operator | Parents | Description |
|---|---|---|
| `null` | `[]` (empty) | Original hypothesis — no parent |
| `ground` | `["H-NNNN"]` (one) | Strengthened evidence from one parent |
| `simplify` | `["H-NNNN"]` (one) | Simplified from one parent |
| `combine` | `["H-NNNN", "H-MMMM"]` (two) | Merged from two parents |
| `oob` | `["H-NNNN"]` (one) | Cross-domain transfer from one parent |

### Rules

1. **Every variant must have at least one parent.** A variant with an empty
   `parents` array is invalid (except for `"operator": "null"` originals).
2. **`combine` must have exactly two parents.** Both must be from the same
   proximity cluster.
3. **Parents are retained.** Variants compete against their parents in the
   next tournament round. The supervisor writes parent-vs-child pairings to
   a JSON file and passes it to `elo pair --rematch <path>`, which
   prepends them as forced rematches that bypass the rematch window.
4. **Lineage is immutable.** Once a hypothesis is submitted, its lineage
   cannot be changed.
5. **Do not set `operator` to `"null"` for variants.** Only original
   hypotheses (produced by generation agents) use `"null"`.

---

## Operator Selection Guide

Choose the operator based on the hypothesis's weaknesses as identified by
reviewers and tournament outcomes:

| Signal | Recommended Operator |
|---|---|
| Reviewer flagged weak evidence or unsupported claims | **ground** |
| Reviewer flagged overcomplexity or untestable design | **simplify** |
| Two hypotheses in same cluster are complementary | **combine** |
| Hypothesis space is saturated; all share the same literature base | **oob** |
| Hypothesis lost on novelty scores in tournament | **oob** |
| Hypothesis lost on testability scores in tournament | **simplify** |
| Hypothesis lost on evidence quality in tournament | **ground** |
| Two complementary hypotheses both lost to a third | **combine** them |

When multiple signals apply, prioritize:
1. **ground** if evidence quality is the primary weakness — a well-grounded
   hypothesis survives all other criticisms better.
2. **simplify** if the hypothesis is strong in evidence but too complex to
   test — parsimony wins tournaments.
3. **combine** only when two hypotheses genuinely complement each other — do
   not force combinations between unrelated hypotheses.
4. **oob** as a strategic diversification move — use sparingly, not on every
   hypothesis.
