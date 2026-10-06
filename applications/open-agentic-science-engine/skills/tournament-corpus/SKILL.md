---
name: tournament-corpus
description: >
  Ingest and interpret a Co-Scientist tournament export — determine
  whether the ranking produced a clear leader, whether any ideas carry
  contradicted deep-verification claims, and whether the result is
  admissible for downstream work. This is the co-scientist-specific
  reading guide. Use when evaluating hypothesis quality from a
  co-scientist ranked tournament, or when reading one idea's prose for detailed
  assessment. Do not use for regulatory variant effects (use
  regulatory-variant-effect), protein structure confidence (use
  protein-structure-confidence), tissue expression (use
  tissue-expression-profile), genetic constraint (use
  target-genetic-evidence), or citation verification (use
  citation-resolution).
---

## 1. When to use, and when not

Use this skill when you need to interpret the output of a Co-Scientist
tournament. Entry points include:

- Ingesting a raw Co-Scientist tournament export as the co-scientist-specific
  entry point for hypothesis assessment. For the vendor-neutral entry
  point covering all strategies, see the `hypothesis-entry` skill.
- Determining whether a tournament produced a clear leader or whether
  the ranking is contested.
- Checking which ideas carry contradicted deep-verification claims.
- Reading one idea's prose (summary, description, reviews, verification)
  for detailed assessment without streaming it into context.

**Do not use when:**

- You need to evaluate the scientific evidence for or against a
  target identified by the tournament — use the capability skills
  for that evidence type (structure, expression, constraint,
  regulatory variants, citations).
- You need to search for literature supporting or contradicting an
  idea — use literature search tools and `citation-resolution`.

## 2. Preconditions

- **Export file** (for `ingest`): the raw Co-Scientist tournament
  export as a JSON file. The export is a serialised JavaScript object
  whose keys are minifier output — the idea list may be `Ur` or `gs`,
  not a stable name. The CLI locates each section by its shape
  (presence of `eloRating` and `ranking` fields) and fails loudly
  when a required section is absent or ambiguous.
- **Normalised artifact** (for `analyze` and `show`): the
  `.tournament.json` produced by `ingest`. This is the stable Layer 0
  record — do not pass the raw export to `analyze` or `show`.
- **No authentication** needed — the tool reads local files only.
- **No network** — `ingest`, `analyze`, and `show` are all offline.
- Run `dde doctor` before first use. It ends with a verdict line:
  `STOP` means fix or report before running anything; `PROCEED` means
  work, and the grouped warnings tell you which commands would refuse,
  which results need careful reading, and which are the tooling lead's
  to clear. Do not judge by the warning count; the verdict line grades
  them for you.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Normalise this tournament export | `dde coscientist ingest <EXPORT_FILE>` | `raw/hypotheses/<name>.tournament.json`<br>`raw/hypotheses/<name>.export.json`<br>`raw/hypotheses/<name>.meta.json` |
| Is the ranking admissible? Which ideas are flagged? | `dde coscientist analyze <ARTIFACT>` | `raw/hypotheses/<name>.analysis.json` |
| What does idea #N say? | `dde coscientist show <ARTIFACT> --rank N [--section S]` | `raw/hypotheses/<name>.idea-<label>.<section>.md` |

Run `ingest` before `analyze` or `show`. `analyze` reads from disk and
applies the `coscientist` threshold set. It can be re-run with
different thresholds (`--elo-decisive-gap`, `--min-win-rate`,
`--max-contradicted-claims`) without re-ingesting.

`ingest` preserves the raw export verbatim (`.export.json`) alongside
the normalised record (`.tournament.json`). The normalisation is lossy
and a reviewer must be able to reach the original bytes.

`show` writes one idea's prose to a file and prints the path — it does
not stream content into context. Select an idea by `--rank` or
`--gene`. A gene name is matched against each member of a multi-gene
idea (e.g. "CCNE1, CCNE2"), so a match on `CCNE1` selects the idea
even if the gene field lists both. Sections: `summary`, `description`,
`reviews-summary`, `verification-summary`, `all`.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Tournament verdicts

`analyze` applies the `coscientist` threshold set and emits one of:

- **clear-leader** — the top-ranked idea leads by at least the
  configured `elo_decisive_gap` and carries no advisories.
- **leader-with-advisories** — the ELO gap is decisive but the leader
  carries advisories (contradicted claims, low win rate, or too few
  matches).
- **no-clear-leader** — the ELO gap between #1 and #2 is below the
  configured `elo_decisive_gap`. The ranking does not support a
  confident selection.
- **single-candidate** — only one idea was ranked. No comparative
  judgment is possible.
- **unrankable** — no ideas had ELO ratings. The tournament produced
  no usable ranking.

### Per-idea advisories

Each ranked idea is checked against four conditions. An advisory is not
a veto — it is a fact that must travel with any claim based on the
idea's ranking:

- **Contradicted claims** — the idea has more deep-verification claims
  contradicted than the configured `max_contradicted_claims` threshold.
- **Low win rate** — the idea's win rate is below the configured
  `min_win_rate` threshold.
- **Too few matches** — fewer than the configured `min_matches`
  threshold were played. The ranking is not well supported.
- **No claims present** — the idea has no deep-verification claims at
  all. Absence of flagged claims is not evidence the idea was verified.

### The denominator caveat

Every `.analysis.json` carries a `metrics.claim_denominator` field
that states this caveat. The `deepVerification` section of the export
is an exception list, not a census: it contains only the claims the
verifier took issue with. In observed exports, every reported claim
has carried a negative verdict (INACCURATE or LEANING_INACCURATE).
That is not a high error rate; it is the definition of the list.

The analysis carries two fields that look like a denominator but are not:

- `n_claims_reported_total` — the length of the exception list. It is
  the number of claims the verifier flagged, not the
  number of claims the idea made.
- `claim_verdicts` — the same list broken down by verdict category
  (e.g. `{INACCURATE: 12, LEANING_INACCURATE: 4}`). Every entry is a
  disputed claim, because undisputed claims do not appear.

Both denominators are "claims already known to be disputed." Dividing
n_contradicted_claims by n_claims_reported_total gives ~1.0 and is
meaningless. The per-idea field n_contradicted_claims is an **absolute
count of flagged claims** (checked against the configured
`max_contradicted_claims` threshold). Do not compute a rate.

### Mandatory relays

`partial_export` is the sole mandatory relay — a **qualifier** that
scopes a result rather than blocking a substitution. It is conditional:
it fires when `n_ideas` < `n_ideas_generated`, confining conclusions
to the ideas present in the export. The denominator caveat, previously
carried as an always-true relay (`coscientist.claim_denominator`), is
now a `metrics.claim_denominator` field on every `.analysis.json` — it
no longer competes for attention in the relay channel. The skill's own
§4 ("ELO is relative ranking, not absolute quality") and the
`leader_gap_is_decisive` documentation are load-bearing rather than
supplementary: they carry the constraint that a single conditional
relay cannot, because a decisive ranking is not evidence that the idea
is scientifically correct.

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `coscientist.partial_export` | Qualifier | `n_ideas` < `n_ideas_generated` (conditional) | Confine conclusions to the ideas present in the export. Do not treat absence from it as evidence against an idea. |

Check `mandatory_relays` in both the `.analysis.json` and the
`.meta.json`. Every relay code present must be satisfied in the finding.

### ELO is relative ranking, not absolute quality

The ELO gap measures how consistently idea #1 beat idea #2 in pairwise
comparisons. A decisive gap means the ranking is stable, not that the
idea is correct. A "clear-leader" verdict says the tournament converged
on this idea — it does not say the idea is scientifically sound. A
finding must not conflate ranking confidence with hypothesis confidence.

The assessment carries `leader_gap_is_decisive` — a tri-state:

- **true** — the ELO gap meets `elo_decisive_gap`. The verdict is
  either `clear-leader` or `leader-with-advisories` depending on
  whether the leader carries advisories.
- **false** — the gap is below the threshold. The verdict is
  `no-clear-leader`.
- **null** — there was only one idea (`single-candidate`) or none
  (`unrankable`). No gap exists. Do not read null as false.

### Consequence rules

- **Clear leader**: state the gene, the ELO gap, and the win rate.
  Proceed to downstream assessment — but the ranking does not
  substitute for evidence from the tools that evaluate the idea.
- **Leader with advisories**: state the advisories alongside the
  ranking. Contradicted claims or low win rate may indicate the idea
  won despite weaknesses, not that the weaknesses do not matter.
- **No clear leader**: the tournament did not converge. Do not select
  an idea on the basis of this ranking.
- **Single candidate**: no comparative judgment was possible. The idea
  is not "the winner" — it is the only entry.
- **Partial export**: when `partial_export` fires, the ranking covers
  only `n_ideas` of `n_ideas_generated`. An
  idea absent from the export was not evaluated by this tool — it was
  not eliminated by the tournament.

### Cross-disciplinary consequences

- A tournament leader's gene becomes the subject of downstream
  evidence gathering (structure, expression, constraint, regulatory
  variants). The ranking is the entry point, not the conclusion.
- Contradicted deep-verification claims in the leader should be
  checked against the evidence tools — a fabricated claim about a
  real gene does not make the gene a bad target, but it does make
  the tournament's reasoning unreliable at that point.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies the mandatory relay, reports
contradicted claims as counts, and does not conflate ranking confidence
with hypothesis confidence. Everything the tools emitted stays under
`raw/hypotheses/`.

Thresholds are cited by name (e.g. "the configured `elo_decisive_gap`
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

- **Computing a contradicted-claim rate.** The denominator does not
  exist. `deepVerification` lists only claims the verifier disputed —
  in all exports observed, every listed claim carried an inaccurate
  verdict. Dividing by the number of listed claims gives ~1.0 and is
  meaningless. The `metrics.claim_denominator` field requires reporting
  the count.
- **Treating "no claims" as "no problems."** An idea with zero
  deep-verification claims has not been verified — it has been
  unexamined. The advisory exists because this silence looks like a
  clean bill of health.
- **Conflating ELO ranking with scientific merit.** A decisive ELO
  gap means the ranking is stable. It does not mean the idea is
  correct, safe, or supported by experimental evidence. The ranking
  is an input to investigation, not a conclusion.
- **Selecting an idea from a no-clear-leader tournament.** The
  tournament did not converge. Picking the top-ranked idea anyway
  imports a false precision — the gap is below the threshold that
  makes the ranking meaningful.
- **Ignoring advisories on a clear leader.** "Clear leader" means the
  gap is decisive **and** the leader has no advisories. "Leader with
  advisories" means the gap is decisive but the idea has problems.
  Dropping the advisories turns the second verdict into the first.
- **Treating a partial export as a complete census.** When
  `partial_export` fires, the ranking covers only the carried ideas.
  An idea absent from the export was not evaluated — it was not
  eliminated.
- **Reporting minified keys as meaningful identifiers.** The export's
  JavaScript keys (`Ur`, `gs`, `BVa`) are minifier output and change
  across Co-Scientist builds. The normalised `.tournament.json` uses
  stable names; cite those.
- **Streaming show output into context.** `show` writes prose to a
  file deliberately. Reading the file when needed keeps long-form
  tournament prose out of the working context. Pasting it inline
  defeats the zero-bloat design.
