---
name: hypothesis-run-corpus
description: >
  Ingest and interpret a hypex tournament run — determine whether the
  ranking produced a clear leader, whether any hypotheses carry phantom
  citations or integrity violations, and whether the result is admissible
  for downstream work. This is the hypex-specific reading guide. Use when
  evaluating hypothesis quality from a hypex ranked tournament, or when
  reading one hypothesis's scores and citations. Do not use for
  co-scientist tournament interpretation (use tournament-corpus),
  vendor-neutral strategy selection (use hypothesis-entry), citation
  verification of a standalone document (use citation-verification), or
  regulatory variant effects (use regulatory-variant-effect).
---

## 1. When to use, and when not

Use this skill when you need to interpret the output of a hypex
tournament run. Entry points include:

- Ingesting a completed hypex run directory as the hypex-specific entry
  point for hypothesis assessment. For the vendor-neutral entry point
  covering all strategies, see the `hypothesis-entry` skill.
- Determining whether a tournament produced a clear leader or whether
  the ranking is contested.
- Checking which hypotheses carry phantom citations, integrity
  violations, or were quarantined.
- Reviewing the run's termination status to determine whether the
  ranking represents a converged result or is where the run stopped.

**Do not use when:**

- You need to interpret a Co-Scientist tournament export — use
  `tournament-corpus`.
- You need to decide which stage 0 strategy to use — use
  `hypothesis-entry`.
- You need to verify citations in a standalone document — use
  `citation-verification`.
- You need to evaluate the scientific evidence for or against a
  target identified by the tournament — use the capability skills
  for that evidence type (structure, expression, constraint,
  regulatory variants, citations).

## 2. Preconditions

- **Completed run directory** (for `ingest`): a hypex tournament run
  directory containing the datastore — `hypotheses/`, `matches/`,
  `ratings/`, `reviews/`, `meta/`, and optionally `quarantine/` and
  `proximity/`. The datastore is append-only with atomic writes and
  one writer per file. The ingest derives every count from the
  datastore, never from `run.yaml` budgets.
- **Normalised artifact** (for `analyze`): the `.hypex.json` produced
  by `ingest`. This is the stable Layer 0 record — do not pass the
  raw run directory to `analyze`.
- **No authentication** needed — the tool reads local files only.
- **No network** — `ingest` and `analyze` are both offline.
- Run `dde doctor` before first use. It ends with a verdict line:
  `STOP` means fix or report before running anything; `PROCEED` means
  work, and the grouped warnings tell you which commands would refuse,
  which results need careful reading, and which are the tooling lead's
  to clear. Do not judge by the warning count; the verdict line grades
  them for you.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Normalise this tournament run | `dde hypex ingest <RUN-DIR>` | `raw/hypotheses/hx-<slug>.hypex.json`<br>`raw/hypotheses/hx-<slug>.run.tar.zst`<br>`raw/hypotheses/hx-<slug>.meta.json` |
| Is the ranking admissible? Which hypotheses are flagged? | `dde hypex analyze <ARTIFACT>` | `raw/hypotheses/hx-<slug>.analysis.json` |

Run `ingest` before `analyze`. `analyze` reads from disk and applies the
`hypex@1.0` threshold set. It can be re-run with different thresholds
without re-ingesting.

`ingest` preserves the raw run directory as a `.tar.zst` archive
alongside the normalised record. The normalisation is lossy and a
reviewer must be able to reach the original bytes.

The ingest computes integrity checks that `hypex validate` does not
perform: dangling match references, dangling review references, dangling
lineage parents, ID/filename mismatches, unrated hypotheses, and
quarantine duplicates. These are surfaced in the `integrity` block of the
normalised artifact because `hypex validate` is schema-only.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Tournament verdicts

`analyze` applies the `hypex@1.0` threshold set and emits one of:

- **clear-leader** — one hypothesis has a decisive ELO gap over the
  runner-up and carries no advisories.
- **leader-with-advisories** — the ELO gap is decisive but the leader
  carries advisories (phantom citations, integrity issues, low win rate,
  or too few matches).
- **no-clear-leader** — the ELO gap between #1 and #2 is below the
  configured `elo_decisive_gap`. The ranking does not support a confident
  selection.
- **single-candidate** — only one hypothesis was in the run. No
  comparative judgment is possible; the hypothesis is trivially ranked.
- **unrankable** — insufficient matches to establish any ranking. The
  tournament produced no usable ordering.
- **unconverged** — budget was exhausted or the run was aborted. The
  ranking is where the run stopped, not where the tournament settled.
  A `clear-leader` or `no-clear-leader` within an `unconverged` run is
  not evidence the result would have held had the run continued.

### Per-hypothesis advisories

Each ranked hypothesis is checked against these conditions. An advisory
is not a veto — it is a fact that must travel with any claim based on
the hypothesis's ranking:

- **Phantom citations** — the hypothesis has phantom citations in its
  citation manifest (count above `max_phantom_citations`). A phantom
  citation invalidates the claim resting on it.
- **Suspect citations** — suspect-title-match citations are present.
  When `max_suspect_citations` resolves, this will become a threshold
  check; while UNRESOLVED, the advisory fires on any non-zero count.
- **Low win rate** — the hypothesis's win rate is below the configured
  `min_win_rate` threshold.
- **Too few matches** — fewer than the configured `min_matches`
  threshold were played. The ranking is not well supported.
- **Low safety score** — the reviewer-assessed safety score is below
  `min_safety_score`. While UNRESOLVED, this advisory cannot fire —
  the threshold has no defensible value yet.
- **Integrity violations** — dangling references or other structural
  problems detected by the ingest (not by `hypex validate`, which does
  not check these).

### The denominator contrast — hypex is different from Co-Scientist

**This section is load-bearing.** A reader carrying the co-scientist
rule ("never compute a rate from the deep-verification denominator")
would wrongly refuse a valid computation here. The two strategies have
different denominator semantics, and the skill must make that explicit.

**Co-Scientist (tournament-corpus):** The `deepVerification` section is
an exception list — it contains only the claims the verifier took issue
with. `n_claims_reported_total` is the count of disputed claims, not
total claims. Dividing `n_contradicted` by `n_reported` gives ~1.0 and
is meaningless. `tournament-corpus` forbids computing a rate.

**hypex (this skill):** The citation manifest carries a real
`summary.total`. This is a census of the citations the extractor found,
not a floor of exceptions. A phantom citation *rate*
(`citations.phantom / citations.total`) is therefore a meaningful
computation here where it was not for co-scientist.

**Why this holds:** The citation manifest's `summary.total` comes from
the `dde.citation-manifest.v1` schema, which counts every citation the
extractor found — verified, suspect, phantom, and unverified alike.
Unlike Co-Scientist's exception list, the manifest is a complete
enumeration of what the extractor saw.

**The condition:** This claim holds only if `cite.extraction_incomplete`
is honoured. When `extraction_basis != "structured"`, the `total` is the
count the extractor *found*, not the count the document *contains* — it
becomes a floor, not a census. If a finding rests on a phantom rate and
`cite.extraction_incomplete` fired on the underlying manifest, the rate
must carry that qualifier: the denominator is incomplete.

**In practice:** compute the phantom rate when a citation manifest is
present and `cite.extraction_incomplete` did not fire (or, if it fired,
carry the qualifier). Do NOT refuse to compute it on the grounds that
co-scientist forbids it — the prohibition exists because co-scientist
lacks the denominator, and hypex has one.

### UNRESOLVED thresholds

Three of the six thresholds in `hypex@1.0` ship UNRESOLVED:

| Threshold | Why UNRESOLVED |
|---|---|
| `elo_decisive_gap` | `MarginMultiplier`'s undocumented 0.75 damping puts hypex ELO on a different scale from Co-Scientist. Cannot inherit `50.0` by analogy. Needs the distribution measured over real converged runs. |
| `max_suspect_citations` | No corpus has been measured to establish a cutoff. |
| `min_safety_score` | The 1–5 anchors are LLM-reviewer judgements; no basis for a cutoff without calibration. |

Calling `get()` on an UNRESOLVED threshold raises. When a verdict
depends on an UNRESOLVED threshold, the task reports blocked. **This is
correct behaviour, not a bug** — it is better than a number nobody can
defend.

On day one, all three are UNRESOLVED. This means:
- `leader_gap_is_decisive` is always `null` (see below).
- `max_suspect_citations` advisories fire on any non-zero count rather
  than applying a calibrated threshold.
- `min_safety_score` advisories cannot fire at all.

### `leader_gap_is_decisive` — tri-state

The assessment carries `leader_gap_is_decisive`:

- **`true`** — the ELO gap between #1 and #2 exceeds the configured
  `elo_decisive_gap`. The verdict is `clear-leader` or
  `leader-with-advisories` depending on whether the leader carries
  advisories.
- **`false`** — the ELO gap is below `elo_decisive_gap`. The verdict
  is `no-clear-leader`.
- **`null`** — `elo_decisive_gap` is UNRESOLVED, so the question cannot
  be answered. This is also the value when there is only one candidate
  (`single-candidate`) or none (`unrankable`). Do not read `null` as
  `false`.

On day one, `null` is always the result because `elo_decisive_gap` has
no defensible value. A gap cannot be called decisive against a threshold
that has not been established.

### ELO is relative ranking, not absolute quality

The ELO gap measures how consistently hypothesis #1 beat hypothesis #2
in pairwise comparisons. A decisive gap means the ranking is stable, not
that the hypothesis is correct. A `clear-leader` verdict says the
tournament converged on this hypothesis — it does not say the hypothesis
is scientifically sound. A finding must not conflate ranking confidence
with hypothesis confidence.

Hypex ELO is on a **different scale** from Co-Scientist ELO. hypex
applies an undocumented `MarginMultiplier` of 0.75 to narrow margins,
which dampens deltas. A reader cannot compare ELO values across the
two strategies. `score.basis` enforces this: `hypex-elo@1.0` vs
`coscientist-elo@1.1`.

### Relay table

All nine `hypex.*` relay codes, with their kind, firing condition, and
obligation:

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `hypex.run_not_converged` | Qualifier | `termination.reason != "converged"` | The ranking is where the run stopped, not where the tournament settled. Do not report it as converged. |
| `hypex.run_aborted` | Defect | `termination.declared == false` or `reason == "aborted"` | No termination record was written. Treat the run as incomplete and state which epoch it reached. |
| `hypex.phantom_citations_present` | Defect | any hypothesis with `citations.phantom > 0` | Name the affected hypotheses. A phantom citation invalidates the claim resting on it, not merely the reference. |
| `hypex.citation_manifest_absent` | Qualifier | any carried hypothesis lacks a citation manifest | Absence of a citation manifest is not evidence of verified citations. State which hypotheses lack a manifest and do not infer citation quality from the absence. |
| `hypex.integrity_violations` | Defect | any `integrity.*` array non-empty | State the dangling references. `hypex validate` does not check these; the ingest is the only place they surface. A dangling match or review reference means the ranking rests on a record that cannot be traced to its source. |
| `hypex.unrated_hypotheses` | Qualifier | `integrity.unrated_hypotheses` non-empty | These hypotheses were not ranked; under Swiss pairing they were excluded entirely. Absence from the ranking is not elimination by it. |
| `hypex.composite_ranking` | Qualifier | standings taken with `--composite` | The ranking number is a composite blending ELO with reviewer scores, not a pure ELO. Report it as a composite and name the preset used. Do not write it into a field named 'elo'. |
| `hypex.quarantined_excluded` | Qualifier | `observed.n_quarantined > 0` | Report the quarantine count alongside the ranking. Quarantined hypotheses were excluded from the tournament and are not represented in the standings. |
| `hypex.pacing_uncoordinated` | Defect | `meta/pacing.json` absent, or tier is not `"shared"`, or sub-agents disagreed on the resolved path | The run fanned out without verified shared pacing, so its retrieval rate against upstream hosts was up to roster_size × the intended limit. Findings resting on this run's retrievals may be incomplete through throttling rather than through absence. State the roster size and the tier observed. |

Check `mandatory_relays` in both the `.analysis.json` and the
`.meta.json`. Every relay code present must be satisfied in the finding.

### Consequence rules

- **Clear leader**: state the hypothesis, the ELO gap, and the win
  rate. Proceed to downstream assessment — but the ranking does not
  substitute for evidence from the tools that evaluate the hypothesis.
- **Leader with advisories**: state the advisories alongside the
  ranking. Phantom citations, integrity violations, or low win rate
  may indicate the hypothesis won despite weaknesses, not that the
  weaknesses do not matter.
- **No clear leader**: the tournament did not converge on a winner.
  Do not select a hypothesis on the basis of this ranking.
- **Single candidate**: no comparative judgment was possible. The
  hypothesis is not "the winner" — it is the only entry.
- **Unrankable**: the tournament produced no usable ranking. The
  hypotheses cannot be ordered.
- **Unconverged**: the run stopped before settling. State the epoch
  reached and the termination reason. The ranking is interim, not
  final.

### Assessment core

The `analyze` command emits `dde.hypothesis-assessment.v1` alongside the
analysis. This is the shared contract across all stage 0 strategies:

```json
{
  "schema": "dde.hypothesis-assessment.v1",
  "strategy": "hypex",
  "source_artifact": "raw/hypotheses/hx-<slug>.hypex.json",
  "source_sha256": "...",
  "candidates": [
    {
      "candidate_id": "H-0001",
      "statement": "...",
      "rank": 1,
      "score": { "value": 1612.4, "basis": "hypex-elo@1.0" },
      "origin": "generated"
    }
  ]
}
```

- `origin` is `"generated"` for tournament-generated hypotheses.
- `score` is `{ "value": <elo>, "basis": "hypex-elo@1.0" }` for rated
  hypotheses. `score` is `null` for unrated hypotheses.
- `rank` is based on ELO ordering. `rank` is `null` for unrated
  hypotheses.
- `score.basis` is `"hypex-elo@1.0"`, which differs from Co-Scientist's
  `"coscientist-elo@1.1"`. **A reader cannot compare scores across
  strategies** — the scales are different, and `basis` is the
  enforcement. Cross-strategy comparison is a judgement a program lead
  makes in prose, not by sorting values.

### Cross-disciplinary consequences

- A tournament leader's hypothesis becomes the subject of downstream
  evidence gathering (structure, expression, constraint, regulatory
  variants, citations). The ranking is the entry point, not the
  conclusion.
- Phantom citations in the leader should be checked against the
  evidence tools — a fabricated citation about a real mechanism does not
  make the mechanism a bad target, but it does make the tournament's
  reasoning unreliable at that point.
- Integrity violations should be investigated before downstream work
  begins — a dangling match reference means the ranking may rest on a
  comparison that cannot be traced.

### What this section produces

Following this contract produces a Layer 1 finding in
`findings/hypothesis-exploration/` that cites the `.analysis.json`,
satisfies every mandatory relay, reports advisories alongside rankings,
and does not conflate ranking confidence with hypothesis confidence.
Everything the tools emitted stays under `raw/hypotheses/`.

Thresholds are cited by name (e.g. "the configured `min_matches`
threshold"), never by value. The values in force are in the artifact:
every `.analysis.json` carries `threshold_set` (name@version),
`thresholds_applied`, `threshold_sources` (default | program | flag),
and `threshold_provenance`. A specialist quoting a number should quote
it from the analysis they are citing.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Refusing to compute a phantom citation rate.** Unlike Co-Scientist,
  hypex carries a real citation manifest with `summary.total`. A phantom
  rate is a valid computation here (§4, "The denominator contrast").
  Refusing it because `tournament-corpus` says "do not compute a rate"
  is applying the wrong skill's rule. The condition: the rate is valid
  when a manifest is present; if `cite.extraction_incomplete` fired,
  carry the qualifier.
- **Computing a phantom rate without checking `extraction_incomplete`.**
  The inverse error. The rate is valid only if the denominator is a
  census. If `extraction_basis != "structured"` on the underlying
  manifest, the total is a floor and the rate understates.
- **Treating `leader_gap_is_decisive: null` as false.** `null` means
  `elo_decisive_gap` is UNRESOLVED — the question cannot be answered.
  It does not mean the gap is small. Do not report "no clear leader"
  when the threshold does not exist to measure against.
- **Inheriting Co-Scientist's `elo_decisive_gap: 50.0`.** hypex ELO is
  on a different scale due to `MarginMultiplier`'s 0.75 damping. The
  hypex threshold ships UNRESOLVED because there is no defensible value
  yet. Do not import one from another strategy.
- **Conflating ELO ranking with scientific merit.** A decisive ELO gap
  means the ranking is stable. It does not mean the hypothesis is
  correct, safe, or supported by experimental evidence. The ranking is
  an input to investigation, not a conclusion.
- **Selecting a hypothesis from an unconverged tournament.** Budget
  exhaustion or abort means the ranking is where the run stopped, not
  where it settled. The ordering may have changed had the run continued.
- **Ignoring advisories on a clear leader.** `clear-leader` means the
  gap is decisive **and** the leader has no advisories.
  `leader-with-advisories` means the gap is decisive but the hypothesis
  has problems. Dropping the advisories turns the second verdict into
  the first.
- **Treating quarantined hypotheses as eliminated.** Quarantine is
  removal from the tournament, not evidence against the hypothesis.
  `hypex.quarantined_excluded` requires reporting the count because the
  ranking is over a reduced field.
- **Trusting `run.yaml` budgets as observed counts.** The declared
  budgets are carried for context but are non-authoritative. The actual
  counts are derived from the datastore by the ingest. If
  `observed.n_matches` differs from `declared_budgets.max_matches`, the
  observed count is the one to cite.
- **Treating `hypex validate` as an integrity check.** `hypex validate`
  is schema-only. It does not check referential integrity — dangling
  match references, dangling review references, and ID/filename
  mismatches go undetected. The ingest computes integrity; the
  `integrity` block in the normalised artifact is the only place these
  violations surface.
- **Reporting a composite as an ELO.** When `hypex.composite_ranking`
  fires, the number blends ELO with reviewer scores (up to ±60 at
  `balanced`, ±90 at `focus_on_breakthroughs`). It is not an ELO. Do
  not write it into a field named `elo` or compare it against an ELO
  threshold.
- **Silently collapsing the v2 loop to one epoch.** The DDE Hypex contract is
  multi-epoch. A run may stop after one epoch only when an explicit budget or
  error condition requires finalization, and its termination reason must say
  so. Do not label such a run converged or substitute the retired pilot flow.
- **Treating `prox` similarity as semantic.** The `prox` similarity
  metric is lexical (TF-IDF), not semantic. Two hypotheses proposing the
  same mechanism in different vocabulary score near zero. Cluster labels
  ride through the ingest as data — nothing thresholds on them.
