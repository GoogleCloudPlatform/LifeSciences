---
name: debate-protocol
description: Self-play debate and adversarial match formats for hypothesis evaluation.
---

# Debate Protocol

This skill teaches you how to run a structured self-play debate to stress-test
each hypothesis before submitting it. The debate forces you to find weaknesses
in your own reasoning and strengthen the hypothesis before it faces external
review.

This protocol contributes records to a run that the supervisor ultimately
publishes with `dde hypex analyze`; workers do not publish a separate result.

## Purpose

Every hypothesis you generate must survive a simulated scientific debate before
submission. This is not optional — it is a quality gate. The debate:

1. **Exposes weak evidence** — claims that sound plausible but lack support.
2. **Identifies untested assumptions** — hidden premises the hypothesis relies on.
3. **Sharpens predictions** — vague predictions become specific and testable.
4. **Improves experimental design** — impractical experiments get revised.
5. **Surfaces contradicting literature** — forces you to search for
   counter-evidence.

## Self-Play Format (v1)

You play both roles — **Advocate** and **Critic** — alternating across 3–5
turns. This is an internal deliberation, not a multi-agent protocol.

### Roles

| Role | Stance | Goal |
|---|---|---|
| **Advocate** | Defends the hypothesis | Present the strongest case for the hypothesis being true and testable |
| **Critic** | Attacks the hypothesis | Find the most damaging weaknesses, missing evidence, or flawed reasoning |

### Turn Structure

The debate follows a fixed cycle: **Propose → Attack → Defend → Refine**.
Run 3–5 turns total, depending on complexity.

#### Turn 1: Propose (Advocate)

Present the hypothesis in its initial form:

- State the core claim and mechanism.
- Present supporting evidence with identifiers returned by DDE literature commands.
- List the testable predictions.
- Propose initial experiments.

#### Turn 2: Attack (Critic)

Challenge the hypothesis on multiple axes:

- **Evidence quality:** Are the cited papers actually showing what the advocate
  claims? Are there confounders? Is the sample size adequate?
- **Mechanistic gaps:** Are there missing steps in the proposed causal chain?
  What alternative mechanisms could explain the same observations?
- **Prediction specificity:** Are the predictions truly falsifiable? What
  result would make the advocate abandon the hypothesis?
- **Experimental feasibility:** Are the proposed experiments actually doable?
  What are the practical barriers?
- **Prior art:** Has this idea been proposed before? Search for existing
  literature that already tests this hypothesis.
- **Contradicting evidence:** Search for papers that directly contradict the
  claim. Use `dde pubmed search` or `dde preprint search --source arxiv` with terms designed to
  find counter-evidence.

#### Turn 3: Defend (Advocate)

Respond to each criticism:

- Acknowledge valid weaknesses.
- Provide additional evidence (search for more literature if needed).
- Narrow the claim if the original was too broad.
- Strengthen predictions to address specificity concerns.
- Revise experimental designs to address feasibility concerns.

#### Turn 4: Refine (Both)

Synthesize the debate into an improved hypothesis:

- Incorporate the strongest criticisms into the hypothesis itself (as
  `constrains` evidence or narrowed scope).
- Add any new evidence found during the debate.
- Revise predictions to be more specific and falsifiable.
- Update experiments based on feasibility feedback.
- Decide: **submit** (the hypothesis is strong enough) or **abandon**
  (the hypothesis has fatal flaws).

#### Optional Turn 5: Final Challenge (Critic)

If the hypothesis survived Turn 4, one final check:

- Is this hypothesis *novel*? Does it add something beyond what the cited
  literature already establishes?
- Is the mechanism *necessary*? Could the predictions be true without the
  proposed mechanism?
- Would a practicing scientist find this *worth testing*?

If the hypothesis fails this final challenge, either revise further or abandon
it and move on.

### When to Run More Than 3 Turns

Run the full 5 turns when:

- The hypothesis involves a novel multi-step mechanism (many places for errors).
- The supporting evidence comes primarily from preprints (not yet peer-reviewed).
- The predictions require expensive or high-difficulty experiments (worth extra
  scrutiny before proposing).

3 turns are sufficient when:

- The hypothesis is a straightforward extension of well-established findings.
- The supporting evidence is from multiple peer-reviewed sources.
- The experiments use standard, low-difficulty techniques.

---

## Transcript Format

Save the debate transcript alongside the hypothesis for review. The transcript
should be structured as follows:

```markdown
# Debate Transcript: [Hypothesis Title]

## Turn 1: Propose (Advocate)

[Advocate's initial presentation]

## Turn 2: Attack (Critic)

[Critic's challenges]

## Turn 3: Defend (Advocate)

[Advocate's responses to challenges]

## Turn 4: Refine (Synthesis)

[Revised hypothesis summary]

### Decision: SUBMIT | ABANDON

[Justification for the decision]

## Turn 5: Final Challenge (Critic) [optional]

[Final challenge and response]
```

### Where to Save Transcripts

Save debate transcripts as markdown files in the run's `meta/` directory with
the naming convention:

```
meta/debate-<hypothesis-title-slug>.md
```

Use a short slug derived from the hypothesis title (lowercase, hyphens, no
spaces). For example: `meta/debate-gut-lactobacillus-tau.md`.

The transcript path is not currently tracked in the hypothesis JSON (the
`transcript_path` field is reserved for tournament match transcripts). The
transcript is saved for auditability and for the meta-review agent to read.

---

## Debate Quality Checklist

Before submitting a hypothesis that passed the debate, verify:

- [ ] At least one criticism was addressed by adding new evidence or narrowing
      the claim (the debate actually changed the hypothesis)
- [ ] Contradicting or constraining evidence was searched for — even if none
      was found, the search was performed
- [ ] Each prediction was challenged for specificity — vague predictions like
      "X will increase" were replaced with measurable outcomes
- [ ] The mechanism was challenged for necessity — the debate considered
      alternative explanations
- [ ] Experiments were challenged for feasibility — impractical designs were
      revised or flagged as high-difficulty

---

## Example Debate (Abbreviated)

### Turn 1: Propose (Advocate)

> I propose that gut Lactobacillus depletion accelerates hippocampal tau
> aggregation via vagus nerve signaling. PMID:38012345 shows altered microbiome
> in AD patients. The mechanism: Lactobacillus metabolites (SCFAs) suppress
> gut epithelial cytokines; depletion → ↑ TNF-α, IL-6 → vagal afferents →
> hippocampal microglial activation → tau hyperphosphorylation.

### Turn 2: Attack (Critic)

> 1. **Correlation ≠ causation**: PMID:38012345 shows association, not
>    causation. The altered microbiome could be a consequence of AD, not a
>    cause.
> 2. **Vagal specificity**: The vagus nerve carries thousands of signals. Why
>    would cytokines selectively activate tau phosphorylation pathways?
> 3. **Missing link**: No evidence cited showing vagal cytokine transport
>    specifically to hippocampus.
> 4. **Prior art**: Multiple groups have proposed gut-brain axis in AD — is
>    the vagal mechanism novel?

### Turn 3: Defend (Advocate)

> 1. Valid — I'll add a prediction requiring a causal experiment (germ-free
>    mice) and note this as a `constrains` evidence item.
> 2. I found PMID:37891234 showing vagal afferents express TNF receptors that
>    activate NF-κB in downstream neurons — adding as supporting evidence.
> 3. Acknowledged gap — proposing a vagotomy experiment to test necessity.
> 4. The novelty is the *specific* Lactobacillus → SCFA → cytokine → vagal
>    pathway. Prior work proposes general "gut-brain axis" without this
>    mechanism.

### Turn 4: Refine

> **Revised claim**: Narrowed to "Lactobacillus-derived SCFAs modulate
> hippocampal tau phosphorylation via vagal TNF-α/NF-κB signaling" —
> more specific mechanism. Added constraining evidence about correlation vs.
> causation. Added vagotomy experiment. Decision: **SUBMIT**.

---

## Adversarial Match Format

The adversarial match format is used by the **ranking agent** for tournament
matches between two hypotheses. Unlike the self-play format above (which
stress-tests a single hypothesis during generation), the adversarial format
compares two hypotheses head-to-head to determine which is stronger.

The ranking agent plays all roles — advocate, critic, and judge — to ensure
consistency and eliminate coordination overhead.

### Tier 1 — Single-Turn Comparison

A fast, structured comparison for placement matches or when the rating gap
between contestants is large (> 50 points).

**Steps:**

1. **Read both hypotheses** from `hypotheses/` and their latest reviews from
   `reviews/` (if available).
2. **Score each hypothesis** on three criteria (1–5 scale):
   - **Novelty** — Does it propose something genuinely new beyond existing
     literature?
   - **Plausibility** — Is the causal mechanism well-supported by evidence?
   - **Testability** — Are predictions specific and experiments feasible?
3. **Declare a winner** with:
   - **Margin**: `decisive` (score gap ≥ 3 total) or `narrow` (score gap ≤ 2).
   - **Rationale**: 2–4 sentences explaining the verdict with specific
     references to the hypotheses' strengths and weaknesses.
4. **Write the match record** to `matches/M-NNNN.json`.

No transcript file is produced. The rationale in the match record is sufficient.

### Tier 2 — Multi-Turn Adversarial Debate

An in-depth debate for top-quartile matches or when the rating difference is
≤ 50 points. This format produces the most thorough evaluation and a full
transcript.

**Turn structure (7 turns):**

#### Turn 1: Advocate-A Presents (2–3 paragraphs)

Present hypothesis A's strengths:

- Core claim and proposed mechanism.
- Key supporting evidence (cite specific `lit_id` references from reviews).
- Why the predictions are testable and experiments are feasible.
- What makes this hypothesis novel compared to the existing literature.

#### Turn 2: Advocate-B Presents (2–3 paragraphs)

Present hypothesis B's strengths using the same structure as Turn 1.

#### Turn 3: Critic Questions Both

Identify weaknesses in both hypotheses:

- **Evidence gaps** — Are cited papers actually showing what is claimed?
  Are there confounders or small sample sizes?
- **Mechanistic holes** — Are there missing steps in the causal chain?
  What alternative mechanisms could explain the same observations?
- **Prediction vagueness** — Are predictions truly falsifiable? What result
  would force abandonment of the hypothesis?
- **Experimental barriers** — Are proposed experiments feasible with current
  technology and reasonable resources?
- **Novelty questions** — Has this been proposed before? Is the mechanism
  necessary, or could the predictions hold without it?

#### Turn 4: Advocate-A Rebuts + Attacks B

- Respond to critic's challenges against hypothesis A.
- Acknowledge valid weaknesses; provide additional supporting arguments.
- Attack hypothesis B's weaknesses identified by the critic or newly discovered.

#### Turn 5: Advocate-B Rebuts + Attacks A

- Respond to critic's challenges against hypothesis B.
- Acknowledge valid weaknesses; provide additional supporting arguments.
- Attack hypothesis A's weaknesses identified by the critic or newly discovered.

#### Turn 6: Critic Closing Assessment

Synthesize the debate:

- Which criticisms were successfully addressed and which remain?
- Which hypothesis showed more resilience under scrutiny?
- Are there fatal flaws in either hypothesis that the advocates failed to
  address?

#### Turn 7: Judge Verdict

Render the final decision:

- **Criterion scores** — Score each hypothesis on novelty, plausibility, and
  testability (1–5 each).
- **Winner** — Declare which hypothesis prevails.
- **Margin** — `decisive` or `narrow`.
- **Rationale** — 3–5 sentences summarizing the reasoning, referencing specific
  moments from the debate.

**After the verdict:**

- Write the match record to `matches/M-NNNN.json` with
  `"format": "multi-turn-debate"`.
- Save the full transcript to `matches/M-NNNN.transcript.md`.

### Adversarial Debate Transcript Format

Save the transcript alongside the match record using this structure:

```markdown
# Match Transcript: M-NNNN

**Hypothesis A:** H-XXXX — [title]
**Hypothesis B:** H-YYYY — [title]
**Format:** multi-turn-debate
**Date:** [ISO 8601 timestamp]

---

## Turn 1: Advocate-A Presents

[Advocate-A's presentation of hypothesis A]

## Turn 2: Advocate-B Presents

[Advocate-B's presentation of hypothesis B]

## Turn 3: Critic Questions

[Critic's challenges to both hypotheses]

## Turn 4: Advocate-A Rebuts + Attacks B

[Advocate-A's defense and counterattack]

## Turn 5: Advocate-B Rebuts + Attacks A

[Advocate-B's defense and counterattack]

## Turn 6: Critic Closing Assessment

[Critic's synthesis of the debate]

## Turn 7: Judge Verdict

**Scores:**
- Novelty: A=[score] B=[score]
- Plausibility: A=[score] B=[score]
- Testability: A=[score] B=[score]

**Winner:** H-XXXX
**Margin:** decisive | narrow

**Rationale:**
[Judge's reasoning]
```

Save this file as `matches/M-NNNN.transcript.md` — the same directory as
the match JSON record, using the same match ID.
