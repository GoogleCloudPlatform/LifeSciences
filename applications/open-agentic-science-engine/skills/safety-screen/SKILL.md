---
name: safety-screen
description: Dual-use biosafety rubric, research integrity screen, quarantine procedure, and safety screening workflow for hypothesis review.
---

# Safety Screen

This skill teaches you how to evaluate hypotheses for dual-use biosafety risk
and research integrity violations (evidence fabrication and phantom citations),
and how to quarantine hypotheses that pose potential dangers or violate integrity
standards. Safety screening is a mandatory part of every review — it is never
skipped, even for seemingly benign hypotheses.

This protocol contributes records to a run that the supervisor ultimately
publishes with `dde hypex analyze`; workers do not publish a separate result.

## Dual-Use Biosafety Rubric

A hypothesis has **dual-use risk** when the research it proposes could be
directly misused or could provide knowledge enabling the creation of biological,
chemical, or radiological weapons, or other instruments of mass harm.

### Concerning Topics

The following research areas require heightened scrutiny. A hypothesis involving
any of these topics must receive careful safety evaluation:

1. **Gain-of-function modifications** — Proposals to increase the
   transmissibility, virulence, host range, or immune evasion capability of
   any pathogen. This includes computational predictions of gain-of-function
   mutations even without wet-lab work.

2. **Synthesis of dangerous pathogens** — Proposals to reconstruct, synthesize,
   or reverse-engineer select agents or other dangerous pathogens (e.g.,
   variola, H5N1, reconstructed 1918 influenza).

3. **Weaponizable delivery mechanisms** — Research on aerosolization,
   environmental persistence, or targeted delivery methods that could be
   repurposed to disseminate biological or chemical agents.

4. **Immune evasion engineering** — Proposals to modify organisms or molecules
   to evade host immune responses, vaccines, or therapeutic antibodies —
   particularly when applied to pathogens rather than for therapeutic purposes
   (e.g., CAR-T engineering is therapeutic; engineering a virus to evade
   vaccine-induced immunity is dual-use).

5. **Toxin enhancement** — Research to increase the potency, stability, or
   deliverability of biological toxins (botulinum, ricin, etc.).

6. **Antimicrobial resistance transfer** — Proposals involving the transfer of
   antibiotic resistance genes to new organisms, particularly via mobile
   genetic elements, unless the explicit purpose is to study resistance
   mechanisms for mitigation.

7. **Neurological or behavioral modification agents** — Research on agents
   designed to alter cognition, behavior, or consciousness in ways that could
   be coercive or weaponized.

8. **Environmental release of engineered organisms** — Proposals for releasing
   genetically modified organisms with potential for uncontrolled spread or
   ecological disruption.

### Risk Assessment Matrix

Use this matrix in conjunction with the safety scoring anchors from the
`review-rubric` skill:

| Factor | Low Risk | Moderate Risk | High Risk |
|---|---|---|---|
| **Pathogen class** | BSL-1/2 organisms | BSL-3 organisms | BSL-4 or select agents |
| **Modification type** | Observation/measurement | Genetic modification (therapeutic intent) | Enhancement of dangerous properties |
| **Knowledge barrier** | Results require specialized interpretation | Results describe methods reproducible by trained scientists | Results provide step-by-step protocols for dangerous applications |
| **Scope of harm** | Individual or small-group risk | Community-level risk | Population or global risk |
| **Reversibility** | Effects are reversible or containable | Effects are difficult to reverse | Effects are irreversible or self-propagating |

---

## Research Integrity & Evidence Fabrication (Phantom Citations)

In addition to biosafety risks, scientific integrity is strictly enforced across the pipeline. Fabricating evidence, citing hallucinated literature (non-existent PMIDs or DOIs), or pairing real identifiers with fabricated titles undermines scientific reasoning and corrupts downstream evolutionary crossover.

Automated citation verification is performed using:
```bash
dde cite verify <run-dir>/hypotheses/H-XXXX.json --out raw/citations
```
The resulting manifest records per-citation status (`verified`, `phantom`, `unverified`) and an overall summary.

---

## Quarantine Triggers

A hypothesis MUST be immediately quarantined if ANY of the following triggers are met:

### 1. Dual-Use Biosafety Risk (Safety Score ≤ 2)

Use the safety axis anchors from the `review-rubric` skill to assign a score from 1 to 5.
- **Score ≥ 3**: No quarantine required. Note any safety considerations in the review verdict and key criticisms, but the hypothesis proceeds normally.
- **Score = 2**: **Triggers quarantine.** Notable dual-use potential that requires institutional review. The hypothesis may still have scientific merit, but it cannot proceed through the standard pipeline without oversight.
- **Score = 1**: **Immediate quarantine.** Clear dual-use risk. The hypothesis is removed from the active pipeline.

When in doubt between scores 2 and 3, choose 2 (quarantine). False positives are recoverable — the supervisor can review quarantined hypotheses and release them if appropriate. False negatives cannot be undone.

### 2. Evidence Fabrication / Phantom Citations

A hypothesis MUST be immediately quarantined if the citation manifest (`<run-dir>/citations/H-XXXX.json`) meets ANY of these conditions:
- `phantom_count >= 2`, OR
- `phantom_count / total_citations >= 0.50`, OR
- `total_citations > 0` AND `verified_count == 0`.

*(Note: Citations with `status: "unverified"` due to network timeout or upstream API errors do NOT count toward `phantom_count` and do not trigger quarantine.)*

---

## Quarantine Procedure

When a hypothesis triggers quarantine (via safety score ≤ 2 or critical phantom citations), execute the following steps **in order**:

### Step 1: Complete the Review

Finish scoring all axes and write the full review JSON. Do not truncate the review just because the hypothesis will be quarantined — the review record is needed for downstream audit:
- For **dual-use biosafety concerns**, document the safety rationale in the review's `verdict` and `key_criticisms` fields.
- For **phantom citations / evidence fabrication**:
  - Assign `scores.correctness: 1`.
  - Populate `phantom_citations` with all phantom `lit_id`s.
  - Set `citation_manifest` to `"citations/H-XXXX.json"`.
  - Record the finding in `key_criticisms`:
    `"CRITICAL PHANTOM CITATIONS: <N> phantom references detected in manifest. Correctness assigned 1 and hypothesis quarantined."`

### Step 2: Write the Review File

Write the review using `hypex add-review --run <run-id> --run-dir <run-base> --for <hypothesis-id>`.
This assigns the next review ID, validates against `review.schema.json`, and
writes atomically to `reviews/H-XXXX.R-NN.json`.

### Step 3: Quarantine the Hypothesis

Use `hypex set-status` to update the hypothesis status and move it to quarantine:

```bash
hypex set-status --run <run-id> --run-dir <run-base> <hypothesis-id> quarantined
```

This updates the `status` field to `"quarantined"`, re-validates against
`schemas/hypothesis.schema.json`, and atomically moves the file from
`hypotheses/<hypothesis-id>.json` to `quarantine/<hypothesis-id>.json`.
This removes the hypothesis from the active pool so other agents (ranking,
evolution) scanning `hypotheses/` will not encounter it.

### Step 4: Log the Quarantine Event

Write a quarantine log entry to stdout so the supervisor can detect it. Use the standard format corresponding to the quarantine trigger:

- **For dual-use biosafety concerns:**
  ```
  QUARANTINE: H-XXXX quarantined by <reviewer-name>. Safety score: <N>. Reason: <brief reason>.
  ```
  Example:
  ```
  QUARANTINE: H-0042 quarantined by reflection-agent-1. Safety score: 1. Reason: Proposes gain-of-function modification of H5N1 to increase aerosol transmissibility.
  ```

- **For critical phantom citations / evidence fabrication:**
  ```
  QUARANTINE: H-XXXX quarantined by <reviewer-name>. Reason: Critical phantom citations detected (<N> phantom references in manifest).
  ```
  Example:
  ```
  QUARANTINE: H-0042 quarantined by reflection-agent-1. Reason: Critical phantom citations detected (2 phantom references in manifest).
  ```

The supervisor monitors for `QUARANTINE:` prefixed lines in agent output to detect quarantine events.

### Step 5: Record in Completion Report

When reporting completion to the supervisor, separately list any quarantine events with:
- The hypothesis ID
- The reason (dual-use biosafety score or critical phantom citation counts)
- A one-sentence explanation of the concern

---

## Screening Workflow

Apply this screening workflow to **every hypothesis** you review, regardless of
how benign it appears:

1. **Automated Citation Verification & Phantom Check:**
   - Run automated citation verification on the hypothesis:
     ```bash
     dde cite verify <run-dir>/hypotheses/H-XXXX.json --out raw/citations
     ```
   - Inspect the manifest summary (`summary.phantom`, `summary.verified`, `summary.total`).
   - If `phantom_count >= 2`, OR `phantom_count / total_citations >= 0.50`, OR (`total_citations > 0` AND `verified_count == 0`):
     - Trigger immediate quarantine for evidence fabrication.
     - Complete review with `scores.correctness: 1`, `phantom_citations`, and `citation_manifest`.
     - Execute the quarantine procedure above.
2. **Read the hypothesis** — focus on `mechanism`, `experiments`, and
   `predictions` fields.
3. **Check against the concerning topics list** — does the hypothesis touch on
   any of the eight categories above?
4. **If no concerning topics are triggered**, assign safety score 4 or 5 using
   the calibrated anchors and move on.
5. **If a concerning topic is triggered**, apply the risk assessment matrix:
   a. Assess the pathogen class (if applicable).
   b. Assess the modification type.
   c. Assess the knowledge barrier — would the hypothesis, if published as a
      paper, provide actionable instructions for harm?
   d. Assess the scope and reversibility of potential harm.
6. **Assign the safety score** using the calibrated anchors.
7. **If score ≤ 2**, execute the quarantine procedure above.

### Edge Cases

- **Therapeutic research on pathogens** (e.g., vaccine development, antiviral
  mechanism studies): Usually scores 3–4. The intent is defensive, and the
  knowledge produced is more useful for protection than for harm. Score 3 if
  the research involves BSL-3 organisms or could reveal evasion mechanisms.

- **Antimicrobial resistance studies** (for mitigation): Usually scores 3–4.
  Understanding resistance mechanisms is essential for public health. Score 2
  only if the hypothesis proposes creating novel resistance transfer mechanisms
  rather than studying existing ones.

- **Computational gain-of-function prediction**: Scores 2–3. Even without
  wet-lab work, computational predictions of enhancing mutations can guide
  malicious actors. Score 2 if the predictions are specific enough to be
  directly actionable.

- **Gene drives and environmental release**: Scores 2–3. Self-propagating
  genetic modifications with ecological consequences require careful review.
  Score 2 if the proposal involves release in uncontrolled environments.

---

## Examples

### Example 1: Safe Hypothesis (Score 5)

> "Gut microbiome composition correlates with cognitive decline rate in
> Alzheimer's patients, mediated by short-chain fatty acid levels."

- Observational study design
- No pathogen modification
- No dual-use concern
- **Safety score: 5**

### Example 2: Minimal Concern (Score 4)

> "CRISPR-Cas9 editing of APOE4 to APOE3 in iPSC-derived neurons reduces
> amyloid-beta accumulation."

- Standard gene editing techniques (therapeutic intent)
- BSL-1/2 work with human cell lines
- Minimal dual-use potential
- **Safety score: 4**

### Example 3: Moderate Concern (Score 3)

> "Immune evasion mechanisms of SARS-CoV-2 variants can be predicted by
> structural modeling of spike protein mutations."

- Touches on immune evasion (concerning topic #4)
- But: computational study, no wet-lab modification
- Knowledge is primarily useful for vaccine updates (defensive)
- **Safety score: 3** — note the dual-use consideration in the review

### Example 4: Quarantine Required (Score 2)

> "Engineering Pseudomonas aeruginosa with enhanced biofilm resistance to
> identify novel antibiotic targets."

- Creates an organism with enhanced resistance (concerning topic #6)
- Intent is therapeutic (finding targets), but the engineered organism itself
  is more dangerous than wild-type
- Knowledge barrier: the methods section would describe how to create a
  more resistant organism
- **Safety score: 2** — quarantine; requires institutional DURC review

### Example 5: Immediate Quarantine (Score 1)

> "Modifying H5N1 hemagglutinin to enable efficient aerosol transmission
> between ferrets as a model for pandemic risk assessment."

- Direct gain-of-function on a select agent (concerning topics #1 and #2)
- High scope of harm (pandemic potential)
- Irreversible if released
- **Safety score: 1** — immediate quarantine

### Example 6: Evidence Fabrication Quarantine (Phantom Citations)

> Hypothesis proposes a mitochondrial pathway in lupus nephritis, citing two papers: `PMID:99999999` (non-existent record) and `PMID:42297600` with a fabricated title ("Direct Mitochondrial Repair in Podocytes via Synthetic Peptides").

- `dde cite verify` generates a manifest whose summary reports `phantom: 2`, `verified: 0`, `total: 2`.
- Quarantine triggers met: `phantom_count >= 2`, `phantom_count / total >= 0.50`, and `verified_count == 0`.
- Review completed with `scores.correctness: 1`, `phantom_citations: ["PMID:99999999", "PMID:42297600"]`, and `citation_manifest: "citations/H-0042.json"`.
- Hypothesis moved from `hypotheses/H-0042.json` to `quarantine/H-0042.json` with `status: "quarantined"`.
- Log: `QUARANTINE: H-0042 quarantined by reflection-agent-1. Reason: Critical phantom citations detected (2 phantom references in manifest).`
