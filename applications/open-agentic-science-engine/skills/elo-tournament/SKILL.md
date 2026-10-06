---
name: elo-tournament
description: Rating math, pairing policies, match format, and elo CLI usage for hypothesis tournaments.
---

# Elo Tournament

This skill teaches you how to run tournament matches between hypotheses and
maintain Elo ratings using the `elo` CLI. You compare hypotheses head-to-head,
write match records, and trigger rating recomputation.

This protocol contributes records to a run that the supervisor ultimately
publishes with `dde hypex analyze`; workers do not publish a separate result.

## Match Tiers

Every match uses one of two tiers, selected based on the contestants' current
ratings.

### Tier 1 — Single-Turn Comparison

A quick structured comparison. Used for:

- **Placement matches** — when one or both hypotheses are unrated.
- **Large rating gaps** — when the rating difference exceeds 50 points.

**Format:**

1. Read both hypotheses from `hypotheses/`.
2. Read the latest review for each from `reviews/` (if available).
3. Score each hypothesis on three criteria (1–5 scale each):
   - **Novelty** — Does it propose something genuinely new?
   - **Plausibility** — Is the mechanism well-supported by evidence?
   - **Testability** — Are predictions specific and experiments feasible?
4. Declare a winner with margin (`decisive` or `narrow`) and rationale.
5. No transcript file is needed — the rationale is captured in the match record.

### Tier 2 — Multi-Turn Adversarial Debate

An in-depth adversarial debate. Used for:

- **Top-quartile matches** — when both hypotheses are in the top 25% by rating.
- **Close matches** — when the rating difference is ≤ 50 points.

Either condition triggers Tier 2. This ensures that closely-rated and
high-performing hypotheses receive the most thorough evaluation.

**Format:** Follow the adversarial match format defined in the `debate-protocol`
skill. The full multi-turn debate produces a transcript saved alongside the
match record.

## Tier Selection Logic

```
if either hypothesis is unrated:
    → Tier 1 (placement match)
elif abs(rating_a - rating_b) > 50:
    → Tier 1 (large gap — likely a mismatch)
else:
    → Tier 2 (close or high-stakes match)
```

Note: once the first two branches are evaluated, the remaining hypotheses are
all rated with a gap ≤ 50 — exactly the population where Tier 2 applies.
Top-quartile matches are a subset of this group (they are rated and typically
close together).

## Randomization Rule

**Always randomize which hypothesis is presented as "Hypothesis A" vs
"Hypothesis B".** Position bias — the tendency to favor whichever hypothesis
is read first — is a well-documented cognitive bias. Before each match, flip
a coin (use any random method) to decide presentation order.

The match record fields `a` and `b` always reflect the *original* hypothesis
IDs from the pairing, regardless of presentation order. The randomization
affects only which hypothesis you read and evaluate first.

## Match Record Format

Every match produces a JSON record conforming to `schemas/match.schema.json`.
Write it to `matches/M-NNNN.json` using atomic writes (temp file + rename).

```json
{
  "id": "M-0001",
  "epoch": 0,
  "a": "H-0001",
  "b": "H-0002",
  "format": "single-turn",
  "winner": "H-0001",
  "margin": "decisive",
  "criterion_scores": {
    "novelty": {"a": 4, "b": 3},
    "plausibility": {"a": 5, "b": 4},
    "testability": {"a": 3, "b": 4}
  },
  "rationale": "H-0001 proposes a more specific and novel mechanism...",
  "judge": "ranking-agent-1",
  "created_at": "2026-09-05T12:00:00Z"
}
```

For Tier 2 matches, add the `transcript_path` field:

```json
{
  "id": "M-0002",
  "epoch": 0,
  "a": "H-0003",
  "b": "H-0004",
  "format": "multi-turn-debate",
  "winner": "H-0004",
  "margin": "narrow",
  "criterion_scores": {
    "novelty": {"a": 3, "b": 4},
    "plausibility": {"a": 4, "b": 4},
    "testability": {"a": 3, "b": 5}
  },
  "rationale": "After multi-turn debate, H-0004 demonstrated stronger...",
  "transcript_path": "matches/M-0002.transcript.md",
  "judge": "ranking-agent-1",
  "created_at": "2026-09-05T12:30:00Z"
}
```

### Required Fields

| Field | Type | Description |
|---|---|---|
| `id` | string | `M-NNNN` — allocated automatically by `hypex add-match` (or `hypex next-id --run <run-id> match`) |
| `epoch` | integer | Current epoch number |
| `a` | string | First contestant hypothesis ID (`H-NNNN`) |
| `b` | string | Second contestant hypothesis ID (`H-NNNN`) |
| `format` | string | `"single-turn"` or `"multi-turn-debate"` |
| `winner` | string | Winning hypothesis ID (`H-NNNN`) or `"draw"` |
| `margin` | string | `"decisive"` or `"narrow"` (for draws, use `"narrow"`) |
| `criterion_scores` | object | Per-criterion scores (see below) |
| `rationale` | string | Judge's reasoning for the verdict |
| `judge` | string | Name of the judging agent |
| `created_at` | string | ISO 8601 timestamp of match completion |

### Optional Fields

| Field | Type | Description |
|---|---|---|
| `transcript_path` | string | Path to debate transcript (Tier 2 only) |

### Criterion Scores

Each criterion is scored 1–5 for both contestants:

- **novelty** — Originality of the hypothesis beyond existing literature.
- **plausibility** — Strength of the proposed causal mechanism and evidence.
- **testability** — Specificity of predictions and feasibility of experiments.

Scores in `criterion_scores` use `a` and `b` keys matching the `a` and `b`
fields in the match record (the original hypothesis IDs, not the randomized
presentation order).

### Margin Guidelines

- **decisive** — Winner is clearly stronger across multiple criteria; score
  gap ≥ 3 total points.
- **narrow** — Close contest; score gap ≤ 2 total points or winner is stronger
  on some criteria but weaker on others.

### Margin Multipliers (Rating Impact)

The margin field is not just a label — it directly affects the Elo rating
calculation. The `elo recompute` engine applies a **margin multiplier** to the
rating delta (the K × (S − E) term):

| Margin | Multiplier | Effect |
|---|---|---|
| `decisive` | 1.0× | Full rating change applied |
| `narrow` | 0.75× | Rating change reduced by 25% |

A narrow win moves ratings less than a decisive win. This means close matches
have a smaller impact on rankings than clear-cut victories, reflecting the
lower confidence in the outcome. For draws, use `"narrow"` as the margin
(per the required fields table above); the draw itself already halves the
actual score (0.5 instead of 1.0), and the 0.75× multiplier further dampens
the rating exchange between unevenly-rated contestants.

## elo CLI Usage

The `elo` CLI is the interface to the Elo rating engine. All commands require
`--run-dir <path>` pointing to the run directory.

### Generate Pairings

```bash
elo pair --run-dir <path> --epoch N --budget N
```

Outputs a JSON array of pairings to stdout. Each pairing is an object with
`a` and `b` fields (hypothesis IDs).

Options:

| Flag | Default | Description |
|---|---|---|
| `--epoch` | `0` | Epoch number to read ratings from |
| `--budget` | `10` | Maximum number of pairings to emit |
| `--strategy` | `default` | Pairing strategy: `default` (proximity-guided) or `swiss` |

### Recompute Ratings

```bash
elo recompute --run-dir <path> --epoch N
```

Replays the full match ledger (`matches/M-*.json`) in chronological order and
writes updated ratings to `ratings/epoch-N.json`. The output is deterministic:
the same match ledger always produces byte-identical ratings.

Options:

| Flag | Default | Description |
|---|---|---|
| `--epoch` | `0` | Epoch number for the output ratings file |

### View Standings

```bash
elo standings --run-dir <path>
```

Reads the latest ratings file and prints sorted standings (highest Elo first).

Options:

| Flag | Default | Description |
|---|---|---|
| `--composite` | `false` | Compute `Elo_comp`, add composite columns/fields, sort by `Elo_comp` descending |
| `--preset` | unset | Named preset (`balanced`, `disable_novelty`, `focus_on_breakthroughs`, `strict_constraints`, `pure_tournament`). Requires `--composite` |
| `--weights` | unset | Comma-separated `axis=value` overrides (e.g. `goal=50,constraint=50,novelty=20`). Requires `--composite` |
| `--explain` | `false` | Add per-component breakdown to table or JSON output. Requires `--composite` |
| `--format` | `table` | Output format: `table` (human-readable) or `json` |

## Pairing Policies

The `elo pair` command applies pairing policies in order, filling the budget:

1. **Placement** — Unrated hypotheses are paired against high-rated,
   median-rated, and low-rated anchors (3 matches each). This calibrates new
   entrants quickly.

2. **Refinement** — Among top-quartile rated hypotheses, pair nearest-rated
   within the same proximity cluster. This produces the most informative
   matches — close contests between the strongest related hypotheses.

3. **Exploration** — 15% of the remaining budget is allocated to cross-cluster
   pairings. This prevents clusters from becoming rating silos where hypotheses
   are only compared to their neighbors.

4. **Rematch constraint** — No two hypotheses can be paired more than once
   within an epoch, unless one has undergone evolution (producing a new
   hypothesis variant) since the last match.

These policies are implemented in the `elo` tool. You do not need to implement
them — just call `elo pair` and use the pairings it returns.

## Atomic Writes & Match Creation

All match record files must be written atomically and validated against the match schema. Use `hypex add-match`:

```bash
cat <<'EOF' | hypex add-match --run <run-id> -
{
  "epoch": 0,
  "a": "H-0001",
  "b": "H-0002",
  "format": "single-turn",
  "winner": "H-0001",
  "margin": "decisive",
  "criterion_scores": {
    "novelty": {"a": 4, "b": 3},
    "plausibility": {"a": 5, "b": 4},
    "testability": {"a": 4, "b": 3}
  },
  "rationale": "Contestant A proposed a more testable and plausible mechanism.",
  "judge": "ranking-agent-1",
  "created_at": "2026-09-06T12:00:00Z"
}
EOF
```

`hypex add-match` handles sequential `M-NNNN` ID allocation, schema validation against `match.schema.json`, and atomic write to `matches/M-NNNN.json` in one command.

## Concurrency

Multiple ranking agents can run in parallel against different pairings. Safety
is ensured by:

- **Unique IDs** — Each match gets a unique `M-NNNN` ID from `hypex add-match`
  (which calls `hypex next-id` internally), using filesystem locking (`flock`)
  to serialize ID allocation across concurrent processes.
- **One-writer-per-file** — Each match record is a separate file. No two agents
  write to the same match file.
- **Deferred recomputation** — Ratings are recomputed *after* all matches
  complete by replaying the full match ledger. No agent writes to ratings files
  during matches.
