# Program State Management

How the orchestrator maintains Layer 2 program state — the living decision surface for the drug discovery program.

## Layer 2 Documents

The orchestrator maintains four living documents in `program-state/`:

### active-series.md
Tracks all compound series under active optimization. For each series:
- **Status**: Current stage, DMTA round, active/deprioritized/terminated
- **Current best compound**: ID, key metrics (potency, selectivity, metabolic stability)
- **Key liabilities**: With links to supporting findings
- **SAR summary**: Emerging structure-activity relationships
- **Decision pending**: What will trigger the next Go/No-Go decision

Update after every specialist finding that affects a compound series.

### liability-tracker.md
Cross-disciplinary risk register. Each entry includes:
- **Liability**: What the risk is
- **Source**: Link to originating specialist finding
- **Affected series**: Which compound series are impacted
- **Severity**: Critical (blocks progression) / Monitor (track across rounds) / Informational
- **Receiving role(s)**: Which specialist should investigate or mitigate
- **Status**: Open / Under investigation / Mitigated / Accepted / Program-terminating
- **Resolution**: How it was addressed (with link to resolution finding)

Specialists append new liabilities via the T-shaped handoff protocol (see artifact-conventions). The orchestrator triages severity and dispatches investigation.

### decision-log.md
Chronological record of every non-trivial program decision. Each
full entry starts with a level-3 heading that carries its sequential
ID and a one-line summary matching the Summary column in the decision
index:

```markdown
### DEC-004: Selected PFI-653 as lead series

- **Date and context**: What information was available
- **Decision**: What was decided
- **Rationale**: Why — with links to supporting evidence
- **Alternatives considered**: What was rejected and why
- **Outcome**: Updated after the decision's consequences are observed
```

IDs are assigned sequentially (`DEC-001`, `DEC-002`, …). The heading
makes each entry searchable by ID, enabling the context-efficient
loading pattern described below.

This is the audit trail. Every stage gate Go/No-Go, every series prioritization change, every significant re-routing is logged here.

#### Decision index

`decision-log.md` grows monotonically and becomes the largest single
artifact by mid-program. To avoid re-reading the full file after
context compaction, the log carries a mandatory index section at
its **top**, before the first full entry.

The index is a Markdown table with three columns — **ID**, **Date**,
and **Summary** (one line) — one row per full decision entry below:

```markdown
## Decision Index

| ID | Date | Summary |
|---|---|---|
| DEC-001 | 2026-07-15 | Selected PFI-653 as lead series |
| DEC-002 | 2026-07-16 | Deprioritized aminopyridine series (hERG liability) |
| DEC-003 | 2026-07-17 | Approved Stage 2 gate for VNN1 program |
```

When an agent appends a new decision entry, it **must** also append a
corresponding row to the index table. One action, two writes — the
index row and the full entry are always added together.

**Context-efficient loading.** An agent resuming after context
compaction follows this pattern instead of reading the entire file:

1. Read the index section (top of `decision-log.md`) to get an overview.
2. Identify which decisions are relevant to the current task.
3. Read only those full entries by searching for their IDs.

This replaces "read the entire file" with "read the index, then
selectively load."

### open-questions.md
Scientific uncertainties that affect program direction:
- **Question**: The specific uncertainty
- **Impact**: What decisions it blocks or influences
- **Assigned to**: Which specialist is investigating (or "unassigned")
- **Status**: Open / In progress / Resolved
- **Blocked since**: Cohort when this question was first blocked on an external dependency (tooling, data access, etc.), e.g. "Cohort 2". Omit when the question is actively being investigated or unblocked.
- **Resolution**: Answer and supporting evidence (when resolved)

## Update Cadence

- **After every specialist finding**: Update active-series.md if the finding affects a series. Triage any new liability-tracker entries. Log significant decisions.
- **After DMTA rounds (Stage 3)**: Full batch review of all Layer 2 documents. Spot emergent cross-series patterns. Update SAR summaries holistically.
- **At stage gates**: Freeze Layer 2 state as input to gate evaluation. Document the gate decision in decision-log.md.
- **Index maintenance**: Maintaining the decision-log index is part of every decision-log update, not a separate step. When you append a decision entry, you append its index row in the same edit pass.

## Continuous Executive Summary

After significant updates to Layer 2 state, update `executive/program-summary.md` with current program status. This keeps the Layer 4 executive view continuously fresh rather than only updated on demand.

## Reasoning Against State

The orchestrator's routing decisions are made by reading Layer 2 documents, not by recalling prior conversations. When deciding what to do next:

1. Read `active-series.md` — what is the status of each series?
2. Read `liability-tracker.md` — are there unresolved critical liabilities?
3. Read `open-questions.md` — are there blocking uncertainties?
4. Read `decision-log.md` — what was the last significant decision and its context?

This artifact-first reasoning ensures the orchestrator can be restarted without losing program continuity.

### Pre-routing integrity check

Before acting on Layer 2 state, the orchestrator verifies:

- Every **liability-tracker** entry with severity `Critical` has a
  non-empty `Receiving role(s)` and a `Status` from the allowed set
  (Open | Under investigation | Mitigated | Accepted |
  Program-terminating).
- Every **open-questions** entry with status `Open` or `In progress`
  has a non-empty `Impact` field.
- Every **open-questions** entry with a `Blocked since` field where the
  current cohort exceeds the blocked cohort by more than 1 has a linked
  `decision-log` entry recording escalation, workaround dispatch, or
  explicit risk acceptance. An entry blocked for >1 cohort without such
  evidence is **BLOCKED** — the one-cohort deadline requires the entry
  to be escalated, worked around, or accepted as a risk before routing
  proceeds.
- Every **decision-log** entry has a non-empty `Rationale` with at
  least one link to supporting evidence.
- The **decision-log index** row count equals the count of full
  decision entries. A mismatch is **BLOCKED** — either an entry was
  added without its index row, or vice versa.

### Concept lifecycle rules

When concept records exist under `.dde/control/concepts/`, the
following additional rules apply:

**Concept state machine.** Concept state transitions follow the
transition table in `core/concepts.py` (`CONCEPT_TRANSITIONS`).
Use `validate_transition("concept", current, target)` from
`statemachine.py` to enforce legal transitions.  Terminal states
(`terminated`, `withdrawn`) have no outgoing transitions.

**Charter-linkage gate.** A concept cannot transition from `draft`
to `active` without a non-empty `charter_ref` field.  This enforces
the lead template's rule that a charter must exist before work is
routed.

**Pivot governance.** When a concept enters `pivoting`:
1. The previous revision is preserved (revisions are immutable).
2. A new revision is created with the changed key fields.
3. `pivoting` can only go to `active` (pivot completed, charter
   revised) or `terminated`.
4. The decision log entry for the pivot must reference both the old
   and new concept revisions.

**Revision triggers.** A material change to any key field (see
`concepts.KEY_FIELDS`) requires a new revision.  Non-key fields
(`notes`, `work_order_refs`, `decision_log_refs`, etc.) can be
updated in place without a revision bump.

**Biomarker assumptions.** Each biomarker entry must have a valid
category (`patient_selection`, `target_engagement`, `response`,
`companion_diagnostic`).  When `status` is `unknown` or
`not_applicable`, a `rationale` must be provided explaining the gap.

**Concept integrity checks.** The pre-routing integrity check
additionally verifies:
- Every concept in `active` state has at least one assessment record
  (when assessment records exist — this check is skipped when no
  assessment infrastructure has been adopted).
- Every concept record passes schema validation
  (`validate_concept`).
- Concept state is a legal value from `CONCEPT_STATES`.

**Coverage, not just findings.** The check states what it examined:
"Checked N liability entries, M open questions, K decision records
across four documents." A program with zero critical liabilities
passes the field rules vacuously — the coverage line makes that
visible rather than silent. A document that cannot be read (missing
or unparseable) is **BLOCKED**, not clean: absence of a file and
absence of defects must not produce the same outcome.

**Monotonicity.** Layer 2 entries are append-only in practice
(liabilities are resolved by status change, not deletion; decisions
are never unmade), so the counts should never fall between two
routings. The coverage line is written into `decision-log.md` after
each routing — the same chronological, append-only document it
already lives beside — so the previous counts are an artifact that
survives a restart, not a memory that dies with one. A count that
drops is **BLOCKED** until explained, with two named outcomes:

- **Parse regression** — the file did not shrink; the reader failed
  to recognise an entry. Fix the parse, not the document.
- **Recorded consolidation** — duplicate liabilities were merged or a
  mis-filed entry was moved between documents. The block clears only
  when the merged entries are logged by name in `decision-log.md` —
  without the names, this branch is an escape hatch that launders any
  drop, including a parse regression.

**Repair authority.** A missing field and a wrong field are not the
same repair, and not all fields belong to the reader:

- **Reader-decidable** (the reader may supply these): `Receiving
  role(s)`, `Severity`, `Status`. These are triage decisions that
  belong to whoever owns routing.
- **Author-owned** (flag back to the author; the entry does not route
  until they return): `Impact`, `Rationale`, `Source`, `Resolution`.
  These record the author's reasoning. A reader who supplies them is
  inventing the author's reasoning while the `Source` link still
  points at the specialist — improving the appearance of provenance
  without improving the evidence.

These fields are required, not advisory: an entry missing its severity
or receiving role cannot be triaged, and an entry that cannot be
triaged sits in the decision surface without being acted on.

### Assessment and decision record integrity checks

The pre-routing integrity check is extended to cover structured
assessment and decision records (`.dde/control/assessments/`,
`.dde/control/decisions/`):

- **Evidence/execution mutual constraint.** Every assessment with
  `execution_outcome != "completed"` must have
  `evidence_status == "not_assessed"`.  Any other combination is a
  schema violation — a tool crash recorded as "insufficient evidence"
  conflates two distinct failure modes.

- **Decision supporting-assessment references.** Every decision's
  `supporting_assessments` entries reference assessment records
  (`AR-NNN`) that actually exist in `.dde/control/assessments/`.
  A dangling reference is a warning — the decision's evidence chain
  is broken.

- **Human-approval enforcement (critical finding).** Every decision
  with `action == "terminate"` where the affected concept's
  `termination_authority == "human"` must have a non-null
  `human_approval` field.  This condition is **primarily enforced at
  write time** by the decision-record validator (which raises
  `Refusal`, exit 9).  The integrity check is a supplementary
  defense that catches records that entered through direct file
  manipulation, data migration, or bugs in the write validator.

  If the integrity check finds a violation here, it flags it as a
  **critical finding**, not just a warning.  A terminate decision
  that bypassed the write-time validator represents a control
  failure — the record should not exist in its current state.

- **Assessment coverage.** Every concept in `active` state has at
  least one assessment record referencing it.  A concept with no
  assessments is not necessarily wrong (it may be newly created),
  but it is reported for visibility.

**Coverage reporting.** The check states what it examined: "Checked
N assessment records, M decision records."  A program with no
assessment or decision records passes vacuously — the coverage line
makes that visible.

### Pre-mortem dissent preservation checks

When pre-mortem review artifacts exist under
`findings/reviews/*-premortem.md`, the integrity check additionally
verifies:

- **Unresolved objections tracked.** Every substantive (non-speculative)
  failure hypothesis from a pre-mortem that has no resolution (accepted,
  rebutted, accepted_risk, or unresolved-with-owner) must have a
  corresponding entry in `liability-tracker.md`.  An unresolved
  objection that exists in a pre-mortem but not in the liability tracker
  is a **BLOCKED** finding — it represents dissent that has been
  silently dropped.

- **Resolution encoding in decision records.** Every objection
  resolution recorded in a pre-mortem's resolutions section should have
  a corresponding `objection_resolution:` condition string in the
  associated decision record's `conditions` field.  A mismatch is a
  **warning** — the pre-mortem and decision record are out of sync.

- **Speculative hypotheses excluded from gating.** Failure hypotheses
  with no `discriminating_check` must not appear in any gate-blocking
  list or critical-severity liability entry.  A speculative hypothesis
  recorded as a critical liability is a **warning** — it inflates the
  severity of an untestable concern.

- **Causal-language guard.** Failure hypotheses and liability entries
  that upgrade correlational/descriptor-level signals to causal claims
  are flagged as **warnings**.  Known patterns: "establishes hERG
  inhibition" from expression data alone, "establishes zero
  permeability" from descriptor-based prediction alone.  This check
  is pattern-based and non-exhaustive — it catches known
  problematic phrasings but does not replace scientific judgment.

**Coverage reporting.** Extends the existing coverage line: "Checked
N pre-mortem reviews, M unresolved objections, K liability-tracker
cross-references."
