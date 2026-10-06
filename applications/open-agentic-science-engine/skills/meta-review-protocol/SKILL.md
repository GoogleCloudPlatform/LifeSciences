---
name: meta-review-protocol
description: Steering memo format, final report format, and pattern-identification methodology for the meta-review agent.
---

# Meta-Review Protocol

This skill teaches you how to synthesize individual reviews and tournament match
data into two types of output: **steering memos** (per-epoch guidance) and
**final reports** (end-of-run deliverables). Your role is to see the big picture
that individual reviewers and judges cannot — recurring patterns, systemic
weaknesses, and strategic opportunities across the full hypothesis portfolio.

---

## Mode Selection

Your task message from the supervisor specifies which mode to operate in:

- **"steering memo"** or **"steering"** → produce a per-epoch steering memo.
- **"final report"** or **"final"** → produce the end-of-run final report.

If the task message is ambiguous, ask the supervisor for clarification before
proceeding.

---

## Mode 1: Steering Memo (Per-Epoch)

### Purpose

Guide the next epoch's generation, review, and evolution agents by identifying
recurring patterns in the current epoch's reviews and tournament matches. The
steering memo is injected by the supervisor into subsequent workers' task
messages — it must be concrete and actionable.

### Input

1. **All reviews from the current epoch** — read every `reviews/H-XXXX.R-NN.json`
   file where the review's `epoch` field matches the current epoch.
2. **Match records from the current epoch** — read every `matches/M-NNNN.json`
   file where the match's `epoch` field matches the current epoch.
3. **Match transcripts** — if a match record has a `transcript_path` field, read
   the transcript. Tier 2 multi-turn debate transcripts are especially valuable
   because they expose the reasoning behind losses.
4. **Current standings** — read `ratings/epoch-N.json` for the current epoch
   to understand relative hypothesis strength.
5. **Prior steering memos** (if any) — read `meta/epoch-*-steering.md` from
   previous epochs to build on past guidance and track whether prior
   recommendations were addressed.

### Analysis Methodology

The goal is to identify **recurring patterns**, not just list individual
findings. A useful steering memo says "Reviewers repeatedly flag missing dosing
rationale across hypotheses in the neuroinflammation cluster" — not "H-0003
got a low testability score."

#### Step 1: Aggregate Review Findings

For each review, extract:
- The four axis scores (correctness, novelty, testability, safety)
- Key criticisms
- Verified vs. unverified citations
- Contradicting evidence found

Then look for cross-cutting patterns:

- **Which criticisms appear in multiple reviews?** Group criticisms by theme
  (e.g., "weak experimental controls", "unsupported mechanistic step",
  "low novelty — prior art exists").
- **Which scoring axes are consistently low?** If testability scores are
  low across the board, generation agents need guidance on experimental design.
- **Which focus areas produce stronger hypotheses?** Compare score distributions
  across focus areas / clusters.
- **Are citation quality concerns recurring?** If multiple hypotheses rely on
  unverifiable citations or preprints, flag this.

#### Step 2: Analyze Tournament Losses

For each match, extract:
- Winner and loser
- Victory margin (decisive vs. narrow)
- Criterion scores for each contestant
- The judge's rationale
- Transcript content (for Tier 2 debates)

Then identify loss patterns:

- **Why do hypotheses lose?** Common loss reasons include:
  - Weaker experimental feasibility
  - Less specific predictions
  - Thinner evidence base
  - Lower novelty (too similar to existing work)
  - Mechanistic gaps identified during debate
- **Do certain clusters consistently underperform?** If hypotheses from cluster
  C-03 always lose their matches, the underlying focus area may need reframing.
- **Are narrow losses revealing?** A hypothesis that narrowly loses may need
  only a small improvement (e.g., one additional piece of evidence, a more
  specific prediction) to become competitive.

#### Step 3: Synthesize Into Guidance

Transform patterns into concrete, actionable guidance:

- **Guidance for generation** — what kinds of hypotheses to produce (or avoid)
  in the next epoch. Reference specific patterns: "Focus area X is underexplored
  — only 2 hypotheses from this area, both scored well. Consider generating more
  hypotheses here." Or: "Avoid hypotheses that rely solely on correlational
  evidence from animal models without a proposed causal mechanism."
- **Guidance for review** — calibration adjustments for reviewers. "Reviewers
  may be too lenient on testability — several hypotheses scored 4/5 despite
  lacking specific experimental readouts. Apply the testability anchors more
  strictly." Or: "Contradiction searches are effective — 3 reviews found
  contradicting evidence that lowered correctness scores. Continue this practice."
- **Guidance for evolution** — which hypotheses to target with which operators.
  "H-0007 lost its match on testability — consider applying the simplify
  operator to reduce experimental complexity." Or: "Hypotheses in cluster C-01
  share a common mechanistic gap at step 3 — a ground operator targeting this
  step could strengthen the entire cluster."

### Output Format

Write the steering memo to `meta/epoch-N-steering.md` where N is the current
epoch number:

```markdown
# Epoch N Steering Memo

## Summary Statistics
- Hypotheses reviewed: [count]
- Matches played: [count]
- Mean review scores: correctness [X.X], novelty [X.X], testability [X.X], safety [X.X]
- Hypotheses quarantined: [count]

## Recurring Patterns

### Pattern 1: [Short descriptive title]
- **Observation:** [What you found across multiple reviews/matches]
- **Evidence:** [Specific review IDs, match IDs, and excerpts]
- **Frequency:** [How many hypotheses/reviews exhibit this pattern]

### Pattern 2: [Short descriptive title]
...

## Guidance for Generation
- [Specific, actionable guidance based on identified patterns]
- [Reference the patterns above by name]
- [Include both "do more of" and "avoid" recommendations]

## Guidance for Review
- [Calibration adjustments for scoring axes]
- [Areas to probe deeper in the next epoch]
- [Effective practices to continue]

## Guidance for Evolution
- [Which hypotheses are strong candidates for which operators]
- [Specific weaknesses that evolution operators can address]
- [Cross-cluster combination opportunities]

## Priority Focus Areas
- [Underexplored areas to prioritize]
- [Saturated areas to deprioritize]
- [Areas where quality is strong vs. weak]

## Comparison to Prior Steering (if applicable)
- [Whether previous recommendations were addressed]
- [Patterns that persist from prior epochs]
- [New patterns that emerged this epoch]
```

### Quality Bar

A good steering memo:
- Identifies at least 2 recurring patterns with evidence from multiple reviews
  or matches
- Provides actionable guidance, not vague advice ("improve hypotheses" is
  useless; "add dose-response predictions to hypotheses proposing drug
  interventions" is actionable)
- Distinguishes between patterns that affect many hypotheses and issues that
  affect only one
- Connects tournament loss patterns to specific improvable weaknesses
- Is short enough to be read and acted upon by generation and review agents
  (aim for 1–3 pages, not 10)

---

## Mode 2: Final Report

### Purpose

Produce a comprehensive research report when the system converges or the
exploration budget is exhausted. This is the primary deliverable of the
DDE Hypex subgraph — it should be well-structured, evidence-based,
and actionable for a research team planning follow-up experiments.

### Input

1. **All hypotheses** — read every `hypotheses/H-XXXX.json` file (all epochs).
2. **All reviews** — read every `reviews/H-XXXX.R-NN.json` file (all epochs).
3. **All match records** — read every `matches/M-NNNN.json` file (all epochs).
4. **All ratings files** — read every `ratings/epoch-N.json` file to compute
   Elo trajectories.
5. **Final Elo standings** — use `elo standings --run-dir <run-dir> --composite --explain --format json`
   to get the definitive rankings. Each entry includes `elo`, `elo_composite`,
   `composite_delta`, `composite_weights`, and `components`.
6. **Cluster data** — read `proximity/clusters.json` for cluster assignments
   and labels.
7. **Steering memos** — read all `meta/epoch-*-steering.md` files for the
   system's learning trajectory.
8. **Run configuration** — read `run.yaml` for the original research goal,
   epoch count, budget configuration.

### Analysis Methodology

#### Step 1: Establish Rankings

Use the final composite Elo standings as the primary ranking. For each hypothesis in the
top 10:

1. Read the hypothesis JSON for the full statement, mechanism, predictions,
   experiments, and evidence.
2. Read all reviews for this hypothesis across all epochs.
3. Read all match records involving this hypothesis.
4. Compute the Elo trajectory: extract the hypothesis's Elo rating from each
   epoch's ratings file to show how its ranking evolved over time.

#### Step 2: Verify Evidence (Top 10 Only)

For each hypothesis in the top 10, independently verify its key evidence claims
using DDE's citation and literature commands:

```bash
# Verify the complete Hypex evidence array
dde cite verify <run-dir>/hypotheses/H-XXXX.json --out raw/citations

# Resolve any PMID, arXiv identifier, or DOI into a DDE literature artifact
dde litref resolve <identifier> --json
```

Check that each cited paper actually supports the claim made in the hypothesis's
`evidence[].note` field. Note any discrepancies in the report.

**Important:** Do not simply repeat the citations from the hypothesis file. You
must independently verify them. If a citation cannot be verified (fetch fails or
the paper does not support the claimed finding), note this explicitly in the
report.

#### Step 3: Synthesize Review History

For each top-10 hypothesis, produce a review digest:
- What were the strongest criticisms across all reviews?
- Were criticisms addressed in subsequent versions (via evolution)?
- What is the consensus assessment across reviewers?
- Were there disagreements between reviewers?

#### Step 4: Identify Contradictions

Look across the full hypothesis portfolio for:
- Hypotheses that propose contradictory mechanisms for the same phenomenon
- Evidence that supports one hypothesis but undermines another
- Unresolved debates that tournament matches did not settle definitively

#### Step 5: Build Experimental Roadmap

From the top-10 hypotheses' experiment fields, produce a prioritized
experimental plan:
- Group experiments by required resources and methodology
- Identify experiments that could test multiple hypotheses simultaneously
- Estimate difficulty tiers (easy / medium / hard)
- Suggest a phased approach: quick-win experiments first, high-investment
  experiments after initial validation

### Output Format

Write the final report to `report/final.md`:

```markdown
# Hypothesis-Explorer Final Report

## Research Goal
[Original research question from run.yaml]

## Executive Summary
[2–3 paragraph overview of key findings, top hypotheses, and recommended
next steps. Written for a PI or research director who needs the bottom line.]

## Methodology
- **Epochs run:** [N]
- **Total hypotheses generated:** [N]
- **Hypotheses after dedup:** [N]
- **Total reviews conducted:** [N]
- **Tournament matches played:** [N]
- **Final ranking method:** Elo tournament (with composite Elo anchoring)
- **Convergence:** [Whether top-5 stabilized, or budget was exhausted]

## Top Hypotheses (Ranked by Composite Elo)

| Rank | ID | Title | Composite Elo | Elo | Focus Area / Key Mechanism |
|------|-----|-------|---------------|-----|----------------------------|
| 1    | ... | ...   | ...           | ... | ...                        |
| 2    | ... | ...   | ...           | ... | ...                        |
...

### 1. [Title] (H-XXXX, Composite Elo: NNNN, Elo: NNNN)

**Statement:** [One-paragraph falsifiable claim]

**Mechanism:** [Summary of the proposed causal mechanism]

**Key Evidence:**
- [lit_id]: [What this paper shows and how it supports the hypothesis] ✓ verified
- [lit_id]: [What this paper shows] ✓ verified
- [lit_id]: [What this paper shows] ⚠ could not verify / does not support claim

**Review Digest:**
- Mean scores: correctness [X]/5, novelty [X]/5, testability [X]/5, safety [X]/5
- [Number] reviews across [number] epochs
- Key strengths: [from reviews]
- Persistent criticisms: [from reviews]

**Elo Trajectory:**
| Epoch | Elo | Matches | Wins | Rank |
|-------|-----|---------|------|------|
| 0     | ... | ...     | ...  | ...  |
| 1     | ... | ...     | ...  | ...  |
| ...   | ... | ...     | ...  | ...  |

**Proposed Experiments:**
1. [Design] — Readout: [readout] — Difficulty: [easy/med/hard]
2. [Design] — Readout: [readout] — Difficulty: [easy/med/hard]

---

### 2. [Title] (H-XXXX, Composite Elo: NNNN, Elo: NNNN)
[Repeat structure for top 10]

---

## Unresolved Contradictions

### Contradiction 1: [Short title]
- **Hypotheses involved:** H-XXXX vs. H-YYYY
- **Nature of contradiction:** [What they disagree about]
- **Evidence for each side:** [Brief summary]
- **Suggested resolution:** [What experiment or analysis could resolve this]

---

## Experimental Roadmap

### Phase 1: Quick Wins (Easy, 1–3 months)
- [Experiment from H-XXXX]: [brief description]
- [Experiment from H-YYYY]: [brief description]

### Phase 2: Core Validation (Medium, 3–12 months)
- [Experiment from H-XXXX]: [brief description]

### Phase 3: Deep Investigation (Hard, 12+ months)
- [Experiment from H-XXXX]: [brief description]

### Cross-Cutting Experiments
- [Experiments that test multiple hypotheses simultaneously]

---

## Cluster Analysis
- **C-01: [Label]** — [N] hypotheses, top-ranked: H-XXXX (Elo NNNN)
- **C-02: [Label]** — [N] hypotheses, top-ranked: H-XXXX (Elo NNNN)
...

## System Learning Trajectory
[Brief summary of how steering memos evolved across epochs — what the system
learned, what guidance was most effective, what patterns persisted]

## Appendix: Full Hypothesis Index
| Rank | ID | Title | Composite Elo | Elo | Cluster | Status |
|------|-----|-------|---------------|-----|---------|--------|
| 1    | ... | ...   | ...           | ... | ...     | ...    |
| 2    | ... | ...   | ...           | ... | ...     | ...    |
...
```

### Quality Bar

A good final report:
- Ranks hypotheses by the standings the tool emits, not by the author's subjective preference
- Includes Elo trajectories showing how rankings evolved across epochs
- Independently verifies citations for all top-10 hypotheses using DDE citation tooling
- Distinguishes between verified and unverified claims with clear markers
- Identifies unresolved contradictions between hypotheses, not just within them
- Provides an experimental roadmap that is prioritized and phased
- Is written for a scientist who will actually plan experiments, not for an
  agent or an automated pipeline
- Includes methodology notes so the exploration process is transparent
