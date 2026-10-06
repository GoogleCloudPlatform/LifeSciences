---
name: run-protocol
description: Supervisor run protocol — state machine, worker lifecycle, budget accounting, convergence detection, and error handling for orchestrating multi-epoch hypothesis exploration runs.
---

# Run Protocol (v2)

This skill defines the supervisor's protocol for orchestrating a research
exploration run. The DDE port uses multi-epoch looping, evolution, meta-review
steering, convergence detection, and a finalization phase. The initial
generation-through-tournament sequence is followed by the v2 states and epoch
loop; the retired single-epoch pilot is not a fallback mode.

## State Machine (v2)

```
INIT → GENERATE → REVIEW → DEDUP → TOURNAMENT → EVOLVE → REVIEW_EVOLVED →
  TOURNAMENT_REMATCH → META → [convergence check] → loop to GENERATE or FINALIZE → DONE
```

Each state has a clear entry condition, a set of actions, and an exit condition
that advances to the next state.

### State Summary

| State | Purpose | Workers |
|---|---|---|
| INIT | Set up run directory, decompose goal, initialize budgets | None |
| GENERATE | Explore focus areas, produce hypotheses | 2–3 `hypex-generation` |
| REVIEW | Score and critique all hypotheses | 1–2 `hypex-reflection` |
| DEDUP | Cluster hypotheses, merge duplicates | 1 `hypex-proximity` |
| TOURNAMENT | Pairwise Elo-rated matches | 1–3 `hypex-tournament` |
| EVOLVE | Apply evolutionary operators to top-K hypotheses | 1 `hypex-evolution` |
| REVIEW_EVOLVED | Review evolved variants (recurrent mode) | 1 `hypex-reflection` |
| TOURNAMENT_REMATCH | Evolved variants compete; mandatory parent rematches | 1–3 `hypex-tournament` |
| META | Produce per-epoch steering memo | 1 `hypex-meta-review` |
| FINALIZE | Task meta-review agent for final report, then publish DDE artifacts | 1 `hypex-meta-review` |
| DONE | Signal completion | None |

---

## Budget Accounting

v2 maintains two levels of match budget:

### Per-Epoch Budget

- **Default:** 20 matches per epoch. Configurable in `run.yaml` or via task
  message.
- **Tracking:** The supervisor tracks `EPOCH_MATCHES_USED` within each epoch.
  This count is reset to 0 at the start of each epoch.
- **Enforcement:** The `--budget N` flag on `elo pair` limits pairings. Pass
  the *remaining per-epoch* budget (or remaining total budget, whichever is
  lower).
- **Budget exhaustion:** If the remaining per-epoch budget reaches 0, skip
  TOURNAMENT and proceed to EVOLVE (or META if this is a late epoch).

### Cross-Epoch Budget

- **Total match budget:** Default 100 matches across all epochs. Configurable.
- **Tracking:** The supervisor maintains `TOTAL_MATCHES_USED` across all
  epochs. After each `elo pair` call (in both TOURNAMENT and
  TOURNAMENT_REMATCH), add the pairing count.
- **Budget exhaustion:** When `TOTAL_MATCHES_USED >= TOTAL_MATCH_BUDGET`,
  finalize with `reason: budget_exhausted` regardless of stability.
- **Max epochs:** Default 5. When the epoch counter reaches the max, finalize
  with `reason: budget_exhausted`.
- **Wall-clock limit:** Optional. If the run exceeds the configured wall-clock
  limit, finalize with `reason: budget_exhausted` at the next check.

---

## Convergence Criteria

After META completes in each epoch, the supervisor checks whether to loop or
finalize:

### Stability Criterion

**Top-5 hypotheses (by Elo) unchanged across 2 consecutive epochs.**

"Unchanged" means both:
1. **Same 5 hypothesis IDs** in the top-5 (order may differ).
2. **All Elo deltas in the top-5 are below a configurable threshold**
   (default: 30 points).

To check stability:

1. Read the current epoch's standings:
   ```bash
   elo standings --run-dir <path> --format json
   ```
   Extract the top-5 hypothesis IDs and their Elo ratings.

2. Read the previous epoch's standings from `ratings/epoch-<N-1>.json`.
   Extract the top-5 hypothesis IDs and their Elo ratings.

3. Compare:
   - Are the same 5 IDs present in both top-5 sets?
   - For each ID in the top-5, is `|elo_current - elo_previous| < 30`?

4. If **both conditions are met for 2 consecutive epochs** (i.e., the top-5
   was also stable between epoch N-2 and epoch N-1), the stability criterion
   is satisfied.

   Note: This requires at least 3 epochs (0, 1, 2) to evaluate — stability
   is first checkable after epoch 2.

   Note: Convergence stability checks use raw `elo` (without `--composite`),
   because composite moves whenever a new review lands and convergence asks
   "has the tournament stopped learning".

### Hard Finalization Criterion

Any of the following triggers finalization without claiming convergence:

- **Max epochs reached:** `CURRENT_EPOCH + 1 >= MAX_EPOCHS` (default 5).
  That is, if starting the next epoch would exceed the limit, finalize now.
- **Total match budget exhausted:** `TOTAL_MATCHES_USED >= TOTAL_MATCH_BUDGET`
  (default 100).
- **Wall-clock limit exceeded:** If configured and the run has exceeded the
  time limit.

### Decision

After META completes:

1. Check the hard finalization criterion first.
2. Check stability criterion (soft limit — requires 2 consecutive stable
   epochs).
3. If **either** criterion is met → proceed to FINALIZE.
4. If **neither** is met → increment the epoch counter and loop back to
   GENERATE.

---

## Steering Memo Injection

When looping back to GENERATE after META, the supervisor injects the steering
memo into worker task messages:

1. Read the steering memo: `<run-dir>/meta/epoch-<N>-steering.md`.
2. On the next epoch's GENERATE, include the memo content in each generation
   worker's task message. This tells generation agents:
   - What kinds of hypotheses to produce more of
   - What weaknesses to avoid
   - Which focus areas need more attention
3. On the next epoch's REVIEW, include the memo content in each reflection
   worker's task message. This tells reviewers:
   - Any calibration adjustments
   - Patterns to watch for
   - Known systemic issues to flag

The memo is included as a section in the task message, prefixed with a clear
header:

```
--- STEERING MEMO (from epoch N) ---
<memo content>
--- END STEERING MEMO ---
```

---

## DDE Subgraph Boundary

The Hypex supervisor is dispatched from a DDE work order and remains the one
accountable agent at the controller boundary. It must maintain
`meta/roster.ndjson`, `meta/progress.json`, coordinated shared pacing, and
`meta/termination.json` throughout the state machine. Worker messages carry
the DDE run ID and absolute run directory.

Before FINALIZE completes, the supervisor runs `dde hypex ingest <run-dir>`
and `dde hypex analyze <artifact>` so the tournament enters DDE as a Layer 0
`dde.hypex.v1` artifact plus `dde.hypothesis-assessment.v1` analysis. Only
those DDE artifact paths, the final Hypex report path, and the termination
summary cross back to the research-operations controller.

---

## State: INIT

**Purpose:** Set up the run directory and decompose the research goal into
focus areas.

### Entry Condition

- Supervisor receives a task message containing a research goal.

### Actions

1. **Parse the research goal** from the DDE work order supplied by the
   dispatching controller. A direct launch may use a command like:

   ```
   scion start my-run --type hypex-supervisor "Explore mechanisms of age-related cognitive decline"
   ```

   The quoted string is the research goal.

2. **Resolve the artifact path.** Check whether the task message contains an
   override line of the form `Artifact path: <path>`. If present, set
   `ARTIFACT_PATH` to the specified value. If absent, default to the shared
   volume location:

   ```
   ARTIFACT_PATH = /scion-volumes/executions
   ```

   This resolved value is used for the remainder of the run — in every
   `hypex`, `elo`, and `prox` invocation, in every worker task message, and
   in every path reference to the run directory. The default points to the
   shared scratchpad volume so that all worker agents (which run in
   independent clones) can read and write the same datastore.

3. **Choose a run ID.** Derive a short, descriptive run ID from the goal
   (e.g., `cognitive-decline-01`). Use lowercase letters, hyphens, and digits
   only. Keep it under 30 characters.

4. **Initialize the run directory:**

   ```bash
   hypex init-run <run-id> --run-dir "${ARTIFACT_PATH}" --goal "<goal>"
   ```

   This creates the standard directory layout at
   `${ARTIFACT_PATH}/<run-id>/` with subdirectories: `hypotheses/`,
   `reviews/`, `matches/`, `ratings/`, `proximity/`, `meta/`, `quarantine/`,
   `report/`, and a `run.yaml` configuration file.

   Verify success by checking the output: `Initialized run: ${ARTIFACT_PATH}/<run-id>`

5. **Decompose the goal into 3–6 focus areas.** Each focus area should be:
   - A specific sub-topic that can be independently explored
   - Narrow enough for a generation agent to cover in one session
   - Broad enough to yield 2–4 hypotheses
   - Balanced so that the set covers the research space without major gaps

6. **Record focus areas** by writing them to `${ARTIFACT_PATH}/<run-id>/meta/focus-areas.md`.

7. **Initialize budgets:**
   - `EPOCH_MATCH_BUDGET = 20` (per-epoch, configurable)
   - `TOTAL_MATCH_BUDGET = 100` (cross-epoch, configurable)
   - `MAX_EPOCHS = 5` (configurable)
   - `TOTAL_MATCHES_USED = 0`
   - `CURRENT_EPOCH = 0`

### Error Handling

- **`hypex init-run` fails:** Report error and abort the run. Transition to
  DONE.

### Exit Condition

- Run directory exists with valid `run.yaml`.
- 3–6 focus areas are defined.
- All budgets initialized.

---

## State: GENERATE

**Purpose:** Start generation workers to explore each focus area and produce
hypotheses.

### Entry Condition

- INIT completed successfully (epoch 0), OR
- META completed and convergence check decided to loop (epoch > 0).
- Focus areas are defined.
- If epoch > 0, a steering memo exists from the previous epoch.

### Actions

1. **Start 2–3 generation workers** — one per focus area batch. If you have
   more focus areas than workers, distribute focus areas across workers
   (e.g., 5 focus areas across 3 workers: 2+2+1).

   For each worker:

   ```bash
   scion start gen-<short-name> --type hypex-generation "<task message>"
   ```

   The task message must include:
   - The focus area text
   - The run ID (so the worker can use `--run <run-id>` with `hypex`)
   - The run directory path: `${ARTIFACT_PATH}/<run-id>`
   - The current epoch number
   - How many hypotheses to produce (2–4)
   - **If epoch > 0:** The steering memo content (see Steering Memo Injection)

2. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for generation workers to complete"
   ```

3. **Wait for workers to complete.** Workers report back via `scion message`
   with the hypothesis IDs they created and a brief summary.

4. **After all workers complete**, verify hypotheses exist:

   ```bash
   hypex list --run <run-id> --run-dir "${ARTIFACT_PATH}" --type hypothesis
   ```

5. **Collect hypothesis IDs** from worker reports.

### Error Handling

- **Worker timeout or crash:** Log failure, continue with hypotheses from
  other workers. Do not abort the entire run.
- **No hypotheses produced:** Report failure to the dispatching agent with diagnostics.
  Transition to DONE.

### Exit Condition

- At least one hypothesis exists in `hypotheses/`.
- All worker reports have been received (or timed-out workers are logged).

---

## State: REVIEW

**Purpose:** Start reflection workers to review and score all hypotheses.

### Entry Condition

- GENERATE completed with at least one hypothesis.
- All hypothesis IDs are known.

### Actions

1. **Collect hypothesis IDs** to review:

   ```bash
   hypex list --run <run-id> --run-dir "${ARTIFACT_PATH}" --type hypothesis
   ```

2. **Start 1–2 reflection workers** depending on hypothesis count:
   - ≤ 6 hypotheses: 1 worker
   - > 6 hypotheses: 2 workers, split the hypothesis list evenly

   For each worker:

   ```bash
   scion start review-<N> --type hypex-reflection "<task message>"
   ```

   The task message must include:
   - The list of hypothesis IDs to review
   - The run ID
   - The run directory path
   - The current epoch
   - **If epoch > 0:** The steering memo content (see Steering Memo Injection)

3. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for reflection workers to complete"
   ```

4. **Wait for workers to complete.** Workers report back via `scion message`
   with review IDs, per-hypothesis summaries, and any quarantine events.

5. **After all workers complete**, verify reviews exist:

   ```bash
   hypex list --run <run-id> --run-dir "${ARTIFACT_PATH}" --type review
   ```

6. **Check for quarantined hypotheses.** Note these for tracking.

### Error Handling

- **Worker timeout or crash:** Log and continue. Some hypotheses will lack
  reviews. This is acceptable.
- **No reviews produced:** Proceed to DEDUP. Reviews are not required for
  dedup or tournament (though they inform tier selection).

### Exit Condition

- Worker reports received (or timed-out workers are logged).
- Proceed to DEDUP regardless of whether all reviews completed.

---

## State: DEDUP (new in v1)

**Purpose:** Run proximity analysis to identify clusters and near-duplicate
hypotheses, then retire duplicates before the tournament.

### Entry Condition

- REVIEW state completed.

### Actions

1. **Start the proximity worker:**

   ```bash
   scion start prox-worker --type hypex-proximity "<task message>"
   ```

   The task message must include the run directory path.

2. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for proximity worker to complete"
   ```

3. **Wait for the worker to complete.** The proximity worker runs the full
   pipeline: `prox embed`, `prox graph`, `prox clusters`, `prox dupes`.
   It adjudicates borderline pairs (0.80–0.92 similarity) and reports:
   - Cluster labels and member hypotheses
   - Merge recommendations (verdict: MERGE, KEEP, or FLAG)
   - Statistics

4. **Read proximity results:**

   ```bash
   cat ${ARTIFACT_PATH}/<run-id>/proximity/graph.json
   cat ${ARTIFACT_PATH}/<run-id>/proximity/clusters.json
   ```

5. **Process merge recommendations:**
   - For each MERGE recommendation: retire the duplicate hypothesis (set
     its status to `"merged"`). By convention, keep the earlier-ID hypothesis.
   - For each FLAG recommendation: note for human review in the summary.
   - Record all decisions in `meta/dedup-results.md`.

6. **Update the active hypothesis list:**

   ```bash
   hypex list --run <run-id> --run-dir "${ARTIFACT_PATH}" --type hypothesis
   ```

   Only active (non-merged) hypotheses proceed to TOURNAMENT.

### Error Handling

- **Proximity worker crash:** Log the failure. Skip DEDUP and proceed to
  TOURNAMENT without cluster information. `elo pair` works without a
  proximity graph — it just cannot use cluster-guided pairing.
- **No duplicates found:** Normal. Proceed to TOURNAMENT with all hypotheses.

### Exit Condition

- Proximity results read (or worker failure logged).
- Merge recommendations processed (or skipped on failure).
- Active hypothesis list updated.

---

## State: TOURNAMENT (new in v1)

**Purpose:** Run pairwise Elo-rated matches to produce competitive rankings.

### Entry Condition

- DEDUP state completed (or skipped on failure).
- At least 2 active hypotheses exist.
- Match budget has remaining capacity (both per-epoch and total).

### Actions

1. **Compute remaining budget:**

   ```
   EPOCH_REMAINING = EPOCH_MATCH_BUDGET - EPOCH_MATCHES_USED
   TOTAL_REMAINING = TOTAL_MATCH_BUDGET - TOTAL_MATCHES_USED
   REMAINING = min(EPOCH_REMAINING, TOTAL_REMAINING)
   ```

2. **Compute pairings:**

   ```bash
   elo pair --run-dir ${ARTIFACT_PATH}/<run-id> --epoch <N> --budget <REMAINING>
   ```

   The command outputs a JSON array of `{"a": "H-XXXX", "b": "H-YYYY"}`
   objects to stdout.

   **Update budgets:**
   ```
   EPOCH_MATCHES_USED += len(pairings)
   TOTAL_MATCHES_USED += len(pairings)
   ```

   If pairings is empty (no valid pairings possible), skip to EVOLVE.

3. **Split pairings across workers:**
   - ≤ 5 pairings: 1 ranking worker
   - 6–12 pairings: 2 ranking workers
   - > 12 pairings: 3 ranking workers

   Each worker gets a non-overlapping subset of pairings.

4. **Start ranking workers:**

   ```bash
   scion start rank-<N> --type hypex-tournament "<task message>"
   ```

   The task message must include:
   - The pairing list (JSON array)
   - The run ID and run directory path
   - The epoch number
   - Instruction to NOT run `elo recompute` (the supervisor handles this)

5. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for ranking workers to complete"
   ```

6. **Wait for ALL ranking workers to complete.** This is critical — ratings
   must be recomputed from the complete match ledger.

7. **Recompute ratings:**

   ```bash
   elo recompute --run-dir ${ARTIFACT_PATH}/<run-id> --epoch <N>
   ```

8. **View standings:**

   ```bash
   elo standings --run-dir ${ARTIFACT_PATH}/<run-id> --format json
   ```

   Save these standings — you need them for EVOLVE (top-K selection) and
   convergence checking.

### Error Handling

- **Ranking worker crash:** Log the failure. Proceed with whatever match
  records exist. Run `elo recompute` on the available matches.
- **No match records produced:** Skip `elo recompute`. Fall back to
  review-score ranking.
- **`elo recompute` fails:** Log the error. Fall back to review-score
  ranking.
- **Single active hypothesis:** Skip TOURNAMENT entirely (no pairings
  possible). Proceed to EVOLVE.

### Exit Condition

- Ratings computed (or failure logged with fallback plan).
- Standings available for EVOLVE and convergence checking.

---

## State: EVOLVE (new in v2)

**Purpose:** Apply evolutionary operators to top-K hypotheses to produce
improved variants.

### Entry Condition

- TOURNAMENT state completed with Elo standings available.
- At least 1 hypothesis has been rated (top-K selection takes as many as
  available, up to K).

### Actions

1. **Select top-K hypotheses** (default K=5) from the composite Elo standings:

   ```bash
   elo standings --run-dir ${ARTIFACT_PATH}/<run-id> --composite --format json
   ```

   Select the top-K hypotheses (default K=5) by `elo_composite`. Take the
   first K entries from the JSON array. Record their hypothesis IDs.

   > **Operational Note (Mid-run upgrades):** Old reviews are never backfilled.
   > A hypothesis reviewed before Phase 3 keeps `S_goal = 0.5` until reviewed
   > again. Operational mitigation: do not enable `--composite` mid-run on a
   > run whose reviews predate Phase 3.

2. **Gather inputs for the evolution agent:**
   - Top-K hypothesis IDs
   - Reviews for each hypothesis (read from `reviews/`)
   - Match-loss rationales: read match files from `matches/` where each
     top-K hypothesis was the loser. Extract the `rationale` field.
   - Cluster memberships from `proximity/clusters.json` (if available)

3. **Start the evolution worker:**

   ```bash
   scion start evolve-epoch-<N> --type hypex-evolution "<task message>"
   ```

   The task message must include:
   - The list of top-K hypothesis IDs
   - The reviews for each hypothesis (summaries with key criticisms and scores)
   - Match-loss rationales for each hypothesis
   - The run ID and run directory path
   - The current epoch number
   - Cluster memberships (if available)

4. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for evolution worker to complete"
   ```

5. **Wait for the worker to complete.** The evolution worker reports:
   - Variant hypothesis IDs created (e.g., H-0055, H-0056)
   - For each variant: parent ID(s), operator applied, summary
   - Any hypotheses not evolved and why

6. **Record evolution results:** Save the variant-to-parent mapping for use
   in REVIEW_EVOLVED and TOURNAMENT_REMATCH:
   ```
   EVOLVED_VARIANTS = {
     "H-0055": {"parents": ["H-0012"], "operator": "ground"},
     "H-0056": {"parents": ["H-0017"], "operator": "simplify"},
     ...
   }
   ```

### Error Handling

- **Evolution worker crash:** Log the failure. Skip EVOLVE,
  REVIEW_EVOLVED, and TOURNAMENT_REMATCH. Proceed directly to META.
- **No variants produced:** Same as crash — skip to META. Note in meta
  that evolution produced no variants.

### Exit Condition

- Evolution worker reported completion with variant IDs.
- Variant-to-parent mapping recorded.

---

## State: REVIEW_EVOLVED (new in v2)

**Purpose:** Review evolved hypothesis variants using recurrent review mode,
which compares each variant against its parent and checks whether prior review
criticisms were addressed.

### Entry Condition

- EVOLVE completed with at least one variant.
- Variant-to-parent mapping is known.

### Actions

1. **Collect evolved variant IDs** from the evolution worker's report.

2. **Start a reflection worker in recurrent mode:**

   ```bash
   scion start review-evolved-<N> --type hypex-reflection "<task message>"
   ```

   The task message must include:
   - The list of evolved variant hypothesis IDs
   - The run ID and run directory path
   - The current epoch number
   - **`recurrent: true`** — activates recurrent review mode
   - **For each variant:** the `parent_id` (e.g., `parent_id: H-0012`)
   - The parent hypothesis IDs so the reviewer can read them

3. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for recurrent review worker to complete"
   ```

4. **Wait for the worker to complete.** The reviewer reports:
   - Review IDs for each evolved variant
   - Per-variant summary including recurrent notes (criticisms addressed,
     remaining, net quality delta)
   - Any quarantine events

### Error Handling

- **Reflection worker crash:** Log the failure. Evolved variants proceed to
  TOURNAMENT_REMATCH without reviews. They will still compete against
  parents, but without the benefit of reviewer feedback in the record.

### Exit Condition

- Reviews for evolved variants completed (or failure logged).

---

## State: TOURNAMENT_REMATCH (new in v2)

**Purpose:** Run tournament matches where evolved variants compete against
their parents and optionally against each other. Parent rematches are
mandatory to validate that evolution produced genuine improvements.

### Entry Condition

- REVIEW_EVOLVED completed (or skipped on failure).
- Evolved variant IDs and their parent IDs are known.
- Match budget has remaining capacity (both per-epoch and total).

### Actions

1. **Write parent-vs-child pairings to a JSON file.** Build the mandatory
   rematch list and write it to `<run-dir>/rematch-pairs.json`:

   ```
   REMATCH_PAIRINGS = []
   for each variant in EVOLVED_VARIANTS:
     for each parent_id in variant.parents:
       REMATCH_PAIRINGS.append({"a": parent_id, "b": variant.id})
   ```

   **Example:** If H-0055 evolved from H-0012 and H-0056 evolved from
   H-0017, write:
   ```json
   [
     {"a": "H-0012", "b": "H-0055"},
     {"a": "H-0017", "b": "H-0056"}
   ]
   ```

2. **Generate all pairings with `elo pair --rematch`.** A single call
   handles both forced rematches and exploration pairings. Forced pairings
   are prepended and consume budget first; remaining budget goes to
   strategy-generated pairings. Deduplication is automatic.

   ```bash
   elo pair --run-dir <path> --epoch <N> --budget <REMAINING> --rematch <run-dir>/rematch-pairs.json
   ```

   The default `--rematch-window 2` applies to strategy-generated pairings
   (preventing rematches within 2 epochs). Forced `--rematch` pairings
   bypass the window — they are explicitly requested rematches.

3. **Enforce budget:**
   ```
   REMAINING = min(EPOCH_MATCH_BUDGET - EPOCH_MATCHES_USED, TOTAL_MATCH_BUDGET - TOTAL_MATCHES_USED)
   ```
   If `len(REMATCH_PAIRINGS) > REMAINING`, truncate the pairings JSON file
   to budget before passing it to `elo pair`. Parent-vs-child pairings are
   prioritized because they are prepended by `--rematch`.

   **Update budgets:**
   ```
   EPOCH_MATCHES_USED += len(all_pairings)
   TOTAL_MATCHES_USED += len(all_pairings)
   ```

4. **Split pairings across workers** (same rules as TOURNAMENT):
   - ≤ 5 pairings: 1 worker
   - 6–12 pairings: 2 workers
   - > 12 pairings: 3 workers

5. **Start ranking workers:**

   ```bash
   scion start rematch-<N> --type hypex-tournament "<task message>"
   ```

   The task message must include:
   - The pairing list (JSON array)
   - The run ID and run directory path
   - The epoch number
   - Instruction to NOT run `elo recompute`

6. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for rematch ranking workers to complete"
   ```

7. **Wait for ALL ranking workers to complete.**

8. **Recompute ratings** (includes all matches from all epochs):

   ```bash
   elo recompute --run-dir ${ARTIFACT_PATH}/<run-id> --epoch <N>
   ```

9. **View updated standings:**

   ```bash
   elo standings --run-dir ${ARTIFACT_PATH}/<run-id> --format json
   ```

   Save these standings for convergence checking and META input.

### Error Handling

- **Ranking worker crash:** Log the failure. Proceed with available match
  records. Run `elo recompute` on available matches.
- **Budget exhausted before rematches:** If no budget remains, skip
  TOURNAMENT_REMATCH entirely. Proceed to META.
- **No match records produced:** Log failure. Proceed to META with
  pre-rematch standings.

### Exit Condition

- Ratings recomputed with rematch results (or failure logged).
- Updated standings available.

---

## State: META (new in v2)

**Purpose:** Task the meta-review agent to produce a steering memo that
analyzes the current epoch's results and provides guidance for the next epoch.

### Entry Condition

- TOURNAMENT_REMATCH completed (or skipped).
- Current epoch standings are available.

### Actions

1. **Start the meta-review worker in steering-memo mode:**

   ```bash
   scion start meta-epoch-<N> --type hypex-meta-review "<task message>"
   ```

   The task message must include:
   - **Mode:** "steering memo"
   - The run directory path
   - The current epoch number
   - A note that the steering memo will be used to guide the next epoch's
     generation and review workers

2. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for meta-review worker to complete"
   ```

3. **Wait for the worker to complete.** The meta-review worker:
   - Reads reviews, match records, and standings for the current epoch
   - Reads prior steering memos (if any)
   - Identifies recurring patterns and systemic issues
   - Writes the steering memo to `meta/epoch-<N>-steering.md`
   - Reports a summary of key findings

4. **Read the steering memo:**

   ```bash
   cat ${ARTIFACT_PATH}/<run-id>/meta/epoch-<N>-steering.md
   ```

   Save this content — it will be injected into the next epoch's GENERATE
   and REVIEW task messages.

### Error Handling

- **Meta-review worker crash:** Log the failure. Proceed to convergence
  check without a steering memo. If looping, the next epoch's workers will
  not receive steering guidance (suboptimal but not fatal).

### Exit Condition

- Steering memo written to `meta/epoch-<N>-steering.md` (or failure logged).
- Proceed to convergence check.

---

## Convergence Check (after META)

This is a decision point, not a state with workers. The supervisor evaluates
convergence criteria immediately after META completes.

### Actions

1. **Check budget exhaustion** (hard limits):
   - Is `CURRENT_EPOCH + 1 >= MAX_EPOCHS`? → converged
   - Is `TOTAL_MATCHES_USED >= TOTAL_MATCH_BUDGET`? → converged
   - Is the wall-clock limit exceeded (if configured)? → converged

2. **Check top-5 stability** (soft limit):
   - Read current epoch standings: top-5 IDs and Elo values
   - Read previous epoch standings (`ratings/epoch-<N-1>.json`): top-5 IDs
     and Elo values
   - If epoch >= 2, also check epoch N-1 vs N-2 stability
   - Stability requires: same 5 IDs in top-5, AND all Elo deltas < 30
   - **Stable for 2 consecutive epoch transitions** → converged
   - Note: Convergence stability checks use raw `elo` (without `--composite`),
     because composite moves whenever a new review lands and convergence asks
     "has the tournament stopped learning".

3. **Decision:**
   - If converged → proceed to FINALIZE
   - If not converged:
     - Increment `CURRENT_EPOCH`
     - Reset `EPOCH_MATCHES_USED = 0`
     - Loop back to GENERATE with steering memo injection

### Recording the Decision

Write the convergence check result to the run metadata:

```bash
cat <<'EOF' >> ${ARTIFACT_PATH}/<run-id>/meta/convergence-log.md
## Epoch <N> Convergence Check

- Budget: <TOTAL_MATCHES_USED>/<TOTAL_MATCH_BUDGET> matches used, epoch <N+1> of <MAX_EPOCHS>
- Top-5 stability: <stable/not stable> (delta: <max delta>)
- Decision: <LOOP to epoch N+1 / FINALIZE>
- Reason: <why>
EOF
```

---

## State: FINALIZE (new in v2)

**Purpose:** Task the meta-review agent to produce the final research report,
then publish the completed run into DDE's Layer 0 artifact model.

### Entry Condition

- Convergence check decided to finalize.
- All epochs' data is available (hypotheses, reviews, matches, ratings,
  steering memos).

### Actions

1. **Start the meta-review worker in final-report mode:**

   ```bash
   scion start meta-final --type hypex-meta-review "<task message>"
   ```

   The task message must include:
   - **Mode:** "final report"
   - The run directory path
   - The total number of epochs completed
   - A note to include Elo trajectories across all epochs, verified evidence
     for top-10, and an experimental roadmap

2. **Signal that you are waiting:**

   ```bash
   sciontool status blocked "Waiting for meta-review worker to produce final report"
   ```

3. **Wait for the worker to complete.** The meta-review worker:
   - Reads all data across all epochs
   - Computes Elo trajectories
   - Independently verifies evidence for top-10 hypotheses
   - Identifies unresolved contradictions
   - Builds an experimental roadmap
   - Writes the final report to `report/final.md`

4. **Verify the report exists:**

   ```bash
   ls ${ARTIFACT_PATH}/<run-id>/report/final.md
   ```

5. **Write `meta/termination.json`, then publish through DDE:**

   ```bash
   hypex validate --run <run-id> --run-dir "${ARTIFACT_PATH}"
   dde hypex ingest "${ARTIFACT_PATH}/<run-id>" --json
   dde hypex analyze <raw-hypex-artifact> --json
   ```

6. **Report the final report and DDE artifact paths to the dispatching agent.**

### Error Handling

- **Meta-review worker crash:** Log the failure. Write a minimal summary
  report yourself using Elo standings and hypothesis data (ranked top-10
  with Elo ratings, review summaries, match counts). Still publish the run
  and report the limitation to the dispatching agent.

### Exit Condition

- Final report written to `report/final.md` (or fallback summary written).
- DDE artifacts verified and report delivered to the dispatching agent.

---

## State: DONE

**Purpose:** Signal task completion.

### Entry Condition

- FINALIZE completed.

### Actions

1. Signal completion:

   ```bash
   sciontool status task_completed "Hypothesis exploration run <run-id> complete"
   ```

---

## Worker Lifecycle Summary

| Worker Type | Template | Count | Started In | Reports |
|---|---|---|---|---|
| Generation | `hypex-generation` | 2–3 | GENERATE | Hypothesis IDs, summaries |
| Reflection | `hypex-reflection` | 1–2 | REVIEW | Review IDs, scores, quarantine events |
| Proximity | `hypex-proximity` | 1 | DEDUP | Clusters, merge recommendations |
| Ranking | `hypex-tournament` | 1–3 | TOURNAMENT | Match IDs, match summaries |
| Evolution | `hypex-evolution` | 1 | EVOLVE | Variant IDs, parent mappings, operators |
| Reflection (recurrent) | `hypex-reflection` | 1 | REVIEW_EVOLVED | Review IDs, recurrent notes, quality deltas |
| Ranking (rematch) | `hypex-tournament` | 1–3 | TOURNAMENT_REMATCH | Match IDs, match summaries |
| Meta-review (steering) | `hypex-meta-review` | 1 | META | Steering memo path, key findings |
| Meta-review (final) | `hypex-meta-review` | 1 | FINALIZE | Final report path, summary |

All workers are started with `scion start <name> --type <template>` and report
back via `scion message`. The supervisor uses `sciontool status blocked` while
waiting for each group of workers.

---

## Error Handling Summary

| Failure | Action | Abort Run? |
|---|---|---|
| Worker timeout / crash | Log failure, continue with remaining workers | No |
| No hypotheses produced by any worker | Report failure with diagnostics | Yes |
| Some hypotheses lack reviews | Include as "unreviewed" in summary | No |
| No reviews produced by any worker | Proceed to DEDUP/TOURNAMENT without reviews | No |
| Proximity worker crash | Skip DEDUP, proceed to TOURNAMENT | No |
| Ranking worker crash | Recompute from available matches | No |
| No match records produced | Fall back to review-score ranking | No |
| `elo recompute` fails | Fall back to review-score ranking | No |
| `hypex init-run` fails | Report error and abort | Yes |
| Per-epoch budget exhausted | Skip TOURNAMENT, proceed to EVOLVE | No |
| Total budget exhausted | Record `budget_exhausted`, proceed to FINALIZE | No |
| Only 1 active hypothesis after DEDUP | Skip TOURNAMENT | No |
| Evolution worker crash | Skip EVOLVE/REVIEW_EVOLVED/TOURNAMENT_REMATCH, go to META | No |
| No evolved variants produced | Skip REVIEW_EVOLVED/TOURNAMENT_REMATCH, go to META | No |
| Recurrent review worker crash | Proceed to TOURNAMENT_REMATCH without evolved reviews | No |
| Rematch budget exhausted | Skip TOURNAMENT_REMATCH, go to META | No |
| Meta-review worker crash (steering) | Loop without steering memo (suboptimal) | No |
| Meta-review worker crash (final) | Write fallback summary, report to dispatching agent | No |

---

## Epoch Flow Summary

```
Epoch 0:
  INIT → GENERATE → REVIEW → DEDUP → TOURNAMENT →
  EVOLVE → REVIEW_EVOLVED → TOURNAMENT_REMATCH →
  META → [check convergence] → not converged → loop

Epoch 1:
  GENERATE (with steering memo) → REVIEW (with steering memo) →
  DEDUP → TOURNAMENT → EVOLVE → REVIEW_EVOLVED →
  TOURNAMENT_REMATCH → META → [check convergence] → not converged → loop

Epoch 2:
  GENERATE (with steering memo) → REVIEW (with steering memo) →
  DEDUP → TOURNAMENT → EVOLVE → REVIEW_EVOLVED →
  TOURNAMENT_REMATCH → META → [check convergence] → converged (top-5 stable) →
  FINALIZE → DONE

OR:

Epoch 4 (max epochs):
  ... → META → [check limits] → budget_exhausted →
  FINALIZE → DONE
```
