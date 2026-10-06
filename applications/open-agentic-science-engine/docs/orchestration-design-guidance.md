# DDE Orchestration Design Guidance

**Status**: v0.1. Expect revision after the pilot program.
**Purpose**: the normative design for end-to-end program orchestration: decision
authority, work dispatch, agent supervision, artifact intake, scientific review,
stage transitions, and publication.
**Companions**:
[`dde-plan.md`](dde-plan.md),
[`skill-design-guidance.md`](skill-design-guidance.md), and
[`tool-design-guidance.md`](tool-design-guidance.md).

---

## 1. The orchestration boundary

DDE has two different kinds of orchestration:

1. **Scientific orchestration** decides what the program should learn next and
   what the accumulated evidence licenses the program to conclude.
2. **Operational orchestration** turns an approved scientific work order into
   supervised agent runs and validated, published artifacts.

These concerns have different cadences and failure modes. Scientific reasoning is
cohort-oriented and must preserve a coherent view of the whole program. Agent
supervision is event-oriented and must handle retries, timeouts, resource limits,
and malformed deliverables without consuming the science lead's reasoning context.

DDE therefore uses two persistent roles, but only one decision authority:

- The **Science Program Lead** is the user-facing orchestrator and the sole owner
  of scientific direction and program decisions.
- The **Research Operations Controller** executes approved work orders and owns
  the operational control plane.

They are not peer orchestrators. The controller has bounded autonomy over how an
approved task is executed, but no authority to reinterpret its scientific purpose.
This asymmetry prevents split-brain decisions while preserving operational
independence.

Specialists remain ephemeral. A **Scientific Reviewer** is also ephemeral and is
engaged when policy or the science lead requires an independent evidence audit.

---

## 2. Authority model

### 2.1 Science Program Lead

The Science Program Lead owns:

- translating the user objective into a program charter and initial hypotheses
- selecting the next decision question and the specialist role needed to answer it
- choosing the relevant context slice and evidence dependencies
- defining scientific acceptance criteria and alert conditions
- accepting, rejecting, or requesting revision of specialist interpretations
- synthesizing accepted findings into Layer 2 program state
- deciding when to batch-review a cohort of work
- evaluating critical scientific alerts
- advancing, looping, pivoting, pausing, or terminating the program
- authorizing stage gate decisions and the evidence snapshot behind them

The science lead does not manage retry timing, inspect terminal sessions, repair
artifact paths, or manually keep the website synchronized.

### 2.2 Research Operations Controller

The Research Operations Controller owns:

- initializing the program filesystem and machine-readable control state
- validating that a work order is complete and executable
- creating specialist and reviewer agents from approved templates
- scheduling dependencies, parallel work, leases, and long-running jobs
- monitoring run state and applying bounded retry policy
- distinguishing infrastructure failures from scientific blocks
- validating deliverable shape, provenance, checksums, warnings, and links
- recording every run transition and preserving failed attempts
- notifying the science lead when work is ready for scientific review
- building and publishing the presentation layer from accepted artifacts

The controller must not:

- change the scientific question or substitute a different specialist role
- weaken acceptance criteria or suppress an alert
- accept a Layer 1 interpretation as scientifically sound
- edit Layer 2 scientific conclusions on its own authority
- advance a stage gate, pivot modality, or terminate a program

### 2.3 Scientific Reviewer

Mechanical validation is not scientific review. A reviewer independently checks
whether a specialist's Layer 1 claims are supported by the cited evidence. The
reviewer may:

- re-run deterministic `analyze` phases against stored Layer 0 inputs
- compare the regenerated result with the specialist's cited result
- enumerate the `mandatory_relays` codes in the artifacts and confirm the finding
  acted on each one, not merely mentioned it
- identify unsupported inference, conflicting evidence, or overclaimed confidence
- recommend acceptance, revision, or rejection to the Science Program Lead

The reviewer does not replace the science lead's decision authority. Review is
required at stage gates and for any finding selected by program policy; it may also
be requested ad hoc for high-impact or disputed conclusions.

### 2.4 User authority

The user sets the scientific objective, risk posture, resource constraints, and any
decisions reserved for human approval. The program charter must name those reserved
decisions. At minimum, a recommendation to enter regulated or wet-lab work must be
presented for explicit human approval unless organization policy says otherwise.

---

## 3. The work order is the handoff contract

The science lead and controller collaborate through durable, versioned work orders.
A chat message can notify the controller that a work order exists; it is not the
work order itself.

Each work order has a stable identity. Each execution attempt has a separate run
identity, so retries never overwrite provenance or make a failed attempt disappear.

### 3.1 Required fields

A work order records:

| Field | Meaning |
|---|---|
| `id` and `revision` | Stable task identity and immutable revision number |
| `decision_question` | The scientific question this work must answer |
| `requested_role` | Approved specialist template; not a free-form persona |
| `stage` and `cycle` | Current program stage and DMTA or equivalent cohort |
| `context` | Bounded artifact links plus a checksummed context snapshot |
| `dependencies` | Accepted findings or completed work orders required first |
| `capabilities` | Expected DDE capability skills, when constraints are needed |
| `deliverables` | Expected Layer 1 paths and any required Layer 0 classes |
| `acceptance_criteria` | What makes the answer decision-useful, not a gate verdict |
| `alert_policy` | Structured conditions that require immediate escalation |
| `priority` and `resource_class` | Scheduling inputs, including single-flight needs |
| `report_to` | The Science Program Lead or named delegated decision owner |

The context snapshot is load-bearing. Links identify the authoritative artifacts;
the snapshot records the exact excerpts and revisions supplied to an ephemeral
specialist. A mutable program-state file read later is not sufficient provenance for
what the specialist was told at dispatch time.

### 3.2 Work-order rules

- The science lead owns the scientific fields and commits a revision before work
  can be queued.
- The controller may reject an incomplete or operationally impossible work order,
  but may not silently repair scientific intent.
- A material change to question, context, role, acceptance criteria, or alert policy
  creates a new revision. It is never edited into an active run.
- Operational annotations such as queue position or retry count belong to the run,
  not the work order.
- A specialist must cite the work-order ID and revision in its Layer 1 finding.

**An offer addressed to two people is not a delegation, it is a race** (template-builder).
A caveat was sent to two agents with *placement is yours or plan-review's, whichever of you
owns that paragraph*. Both owned it, both placed it, and the same passage landed twice in
one section twenty minutes apart. The polite hedge is what caused it: it reads as
deference and functions as an unassigned task, which is the one thing a work order is
built to prevent. **Name one owner, or do the work yourself.** The same applies to any
request that leaves the assignee implicit — `report_to` exists precisely so that a
question has one addressee rather than a plausible set of them.

The duplicate was cheap to find only because both copies landed in **one** section of
**one** file, where a reader met them consecutively. The same hedge across two documents
produces two passages that never meet, drift apart under separate edits, and end as a pair
of caches with no arbiter. Where a rule is genuinely two-ended and must appear in both
documents, state it once in full and have the second site point at the first.

---

## 4. Control plane and artifact plane

The five artifact layers are the program's scientific evidence and reporting
architecture. Orchestration records are a separate control plane, not a sixth
scientific layer.

The default layout is:

```text
.dde/
|-- thresholds.yaml
|-- program.yaml
`-- control/
    |-- work-orders/
    |-- contexts/
    |-- runs/
    |-- events.ndjson
    `-- publish-state.json
```

- `program.yaml` records the program identity, modality, current stage, policy
  versions, human approval boundaries, and active cohort.
- `work-orders/` contains immutable work-order revisions.
- `contexts/` contains dispatch snapshots and their checksums.
- `runs/` contains one machine-written record per execution attempt.
- `events.ndjson` is an append-only transition and alert log.
- `publish-state.json` records which accepted artifact revision was last rendered
  and deployed.

Control records are machine-readable and written through the `dde` CLI. Agents
must not emulate state transitions with ad hoc markdown edits. The CLI validates
schemas, legal transitions, identities, timestamps, and referential integrity.

The control plane is auditable and may be exposed in an operations view, but it is
not a scientific citation source. Scientific claims continue to cite Layers 0-2.

---

## 5. Lifecycle and state machine

### 5.1 Work-order states

The canonical states are:

```text
proposed -> committed -> queued -> in_progress -> submitted
                                                  |-> validation_failed
                                                  `-> mechanically_validated

submitted -> validation_failed (mechanical) -> submitted  [correction, max 2]
submitted -> validation_failed (data_integrity) -> [escalate to science lead]

mechanically_validated -> scientifically_accepted
                       |-> under_scientific_review -> scientifically_accepted
                       |                            |-> revision_requested
                       |                            `-> scientifically_rejected
                       |-> revision_requested
                       `-> scientifically_rejected

queued or in_progress -> blocked | cancelled
```

The `submitted` state is entered when the specialist messages the controller with
a completion notification. While in `submitted`, the specialist agent is alive in
`blocked` state, awaiting mechanical validation. A mechanical validation failure on
a mechanical-only defect may loop back to `submitted` (via in-place correction by
the specialist) up to 2 times without creating a new work-order revision.

`scientifically_accepted` is the only state that licenses incorporation into Layer
2. A mechanically valid finding can still be scientifically weak or wrong.

`committed` makes a revision immutable and executable. `under_scientific_review` is
used when an independent reviewer is required; review output informs but does not
replace the science lead's acceptance decision. Blocked and cancelled work remains
in the record.

### 5.2 Run states

An execution attempt has its own lifecycle:

```text
queued -> starting -> running -> succeeded | failed | blocked | cancelled
```

`succeeded` means the specialist returned its declared deliverables. It does not
mean the artifact contract passed or the science was accepted. Resuming a blocked
task or retrying a failed run creates a new run record under the same work-order
revision. Changing the scientific task requires a new committed revision.

A mechanical correction within a run is an operational annotation, not a new run.
The run record may carry a `correction_count` field; individual correction events
(`correction_returned`, `correction_validated`, `correction_escalated`,
`correction_exhausted`) are appended to `events.ndjson` with the run ID.

### 5.3 Normal flow

1. The science lead reads Layer 2 state and issues a committed work order.
2. The controller validates it, resolves dependencies, and queues it.
3. The controller starts a specialist with the work order and context snapshot.
4. The specialist runs tools, writes Layer 0 artifacts, and submits a Layer 1
   finding.
5. The controller performs mechanical artifact validation.
6. When required, the controller dispatches an independent scientific reviewer.
7. The science lead accepts, rejects, or requests revision of the interpretation.
8. On acceptance, the science lead updates Layer 2 state and records the decision
   rationale and next question.
9. The controller rebuilds and publishes the presentation layer from the accepted
   artifact graph.

### 5.4 Batch completion

The controller determines when the work orders belonging to a declared cohort have
reached terminal or reviewable states. The science lead determines what the cohort
means. A `batch_complete` event triggers holistic scientific review; it must not
auto-advance a stage.

### 5.5 Interrupts

Critical alerts originate in structured tool analysis or explicit program policy,
not only in specialist prose. The controller routes the alert immediately and may
pause dependent work when policy requires it. The science lead determines the
scientific response.

Examples include a genotoxicity signal, an hERG margin breach, an isoform mismatch
that invalidates downstream analysis, or a binding-mode result that contradicts the
assumption behind an active design series.

---

## 6. Validation and acceptance

### 6.1 Mechanical validation

Before scientific review, the controller verifies:

- the declared deliverables exist in the expected artifact layer
- required report headings and the work-order reference are present
- every cited path resolves within the program root
- Layer 0 outputs have valid provenance sidecars and checksums
- `.analysis.json` cites its source artifact and threshold set
- every code in `mandatory_relays`, on the sidecar and on the analysis, is addressed
  in the Layer 1 finding
- tool and environment versions satisfy program policy
- no tool-written byte appears under `findings/`

Mechanical validation produces a validation record, not a scientific verdict.

When validation fails, the controller classifies each defect as **mechanical**
(deliverable existence, heading form, path resolution, relay codes, tool versions,
source tag format) or **data-integrity** (provenance checksum mismatch, missing
analysis source, source tag value mismatch, layer boundary violation). If all
defects are mechanical, the controller returns them to the still-alive specialist
for in-place correction (up to 2 correction cycles). If any data-integrity defect
is present, the controller escalates to the science lead for a new revision
decision. Correction cycles are recorded as `correction_returned` and
`correction_validated` events in `events.ndjson`; they do not create new work-order
revisions or new run records.

### 6.2 Scientific acceptance

The science lead evaluates whether:

- the finding answers the decision question actually asked
- claims are licensed by the cited evidence and its limitations
- conflicting findings have been reconciled or made explicit
- confidence is calibrated to model and experimental uncertainty
- the implications and open questions are useful for the next decision

Acceptance records the finding revision and its effect on Layer 2. Rejection and
revision requests record a reason so the program does not repeat the same failure.

### 6.3 Gate decisions

A stage gate is a scientific decision followed by a reporting snapshot:

1. Freeze the candidate evidence set and applicable policy versions.
2. Complete independent scientific review of gate-critical claims.
3. Evaluate the configured gate criteria and unresolved liabilities.
4. Record the decision to advance, loop, pivot, pause, or terminate.
5. Only then compile the Layer 3 gate document from the accepted evidence snapshot.

Gate documents never become the operational routing surface. Subsequent routing
continues from the updated Layer 2 state and decision record.

---

## 7. Publication is a deterministic projection

The website and dashboards are presentation views over accepted artifacts. Their
default build path must be deterministic and idempotent:

- traverse the accepted Layer 1-4 artifact graph
- validate links and anchors
- render standard headings, metadata, provenance links, and layer navigation
- identify stale pages and the artifact revision from which each page was built
- fail the build on broken required links or schema violations
- record the successfully published revision in `publish-state.json`

This belongs in `dde site build`, `dde site validate`, and the configured
deployment command, supervised by the Research Operations Controller.

A Project Curator agent is optional. Use it only for editorial work that requires
judgment, such as improving an executive narrative or designing a new stakeholder
view. The curator must not become responsible for basic synchronization, link
integrity, or determining which scientific conclusion is current.

Draft, rejected, and mechanically invalid artifacts must not appear as accepted
science in the default stakeholder view. An operations view may expose them with
their state clearly labeled.

---

## 8. Orchestration skill set

Orchestration skills are procedural capabilities at the program-control end of the
pipeline. They do not replace the specialist-neutral science capability skills.

### 8.0 Skill or template body?

A skill is **optional context**. The agent loads it when it judges the task calls
for it. That makes a skill the wrong container for a rule the agent must obey
whether or not it thinks to look.

**Apply the test to the content, not to the behaviour.** For each part of a
behaviour, ask:

> Is this reference material the agent can look up when it needs it? Or is it a
> constraint that must hold whether or not the agent thought to look?

Reference material can be a skill. A constraint belongs in the role template's
`agents.md`. As a rough sort: mechanics, formats and worked examples go to the
skill; authority, prohibitions and ordering constraints go to the body.

**Expect most behaviours to split.** Taking the whole behaviour as the unit
forces a mixed item to a single verdict, and the looser half then drags the
binding half into a skill. Evidence synthesis in §8.1 is the worked example:
document mechanics are a skill, while what may enter Layer 2 and on whose
authority is body.

Two further tests settle the rest:

- **Is the part occasional, and can the agent reliably recognise when it
  applies?** Then a skill is correct. Gate compilation is a genuine example: it
  is inert until a gate is in play.
- **Would failing to load it be unsafe rather than merely inefficient?** Then it
  belongs in the body. A missing document is a lesser fault than a wrong one.

**A new seam has a cost.** A join between a body and a skill is a place where
the two can drift. The cost being weighed is the cost of **creating** a seam; an
existing one is already paid. So:

- Where the mechanics are **already packaged** as a skill, the seam exists
  whether or not you want it. The only question left is which side the authority
  goes, and authority goes to the body.
- Where **nothing is packaged**, splitting means creating a new seam to hold a
  fragment. Do not. Keep the whole behaviour in the body.

This is what happened across §8.1 and §8.2. All ten behaviours had some
look-up-able residue. Nine stayed whole in the body, because packaging their
residue would have created a seam for a fragment. Evidence synthesis split only
because `program-state-management` already existed.

The rule is therefore not "never split". It is **do not create a seam to hold a
fragment**.

**And sometimes the answer is neither side, but both.** A skill that fails to
load fails **silently**: a wrong skill URI provisions an agent that never
learns it is missing anything. Where the body says only "write to the
conventional location" and the convention lives in the skill that did not load,
the agent is left with no location — and falls through to whatever the default
is. `scientific-reviewer` is the live case, reported by template-builder
2026-08-18: `artifact-conventions` carries `raw/reanalysis/<date>-<agent>/`,
the reviewer template loads that skill, and the template body repeats part of
it anyway.

That duplication is correct, but not for the reason it first looks. Do not
reach for it as the general answer to silent load failure, because two things
come before it:

1. **Guard at the point of damage first.** A property protected only by prose
   is protected by nothing: an agent can read the line and still not act on it,
   which is the same outcome as never having read it. `write_analysis` already
   refuses to overwrite a conflicting record and exits 9. That guard does not
   care whether the skill loaded, whether the body said anything, or whether the
   agent understood. Where such a guard exists, the body text is there for
   comprehension, and comprehension does not need duplicating.
2. **Then duplicate only the part no guard can cover.** For the reviewer that
   is **never pass `--overwrite`**. The flag exists because replacing a record
   is sometimes right, and the CLI cannot distinguish an authorised replacement
   from a reviewer destroying the evidence it was sent to audit.

So the narrow rule: **duplicate the instruction that turns a guard off, not the
convention the guard already protects.** That keeps the duplicated surface to a
line or two, which is the surface that has to be kept in step.

**The worked example that first accompanied this rule was wrong, and the way it
was wrong is worth more than the rule.** It read: the directory convention needs
no duplicating on safety grounds, because a reviewer who writes to the wrong
place still cannot clobber the original. template-builder tested that claim
instead of accepting it and reproduced the opposite, three runs on a real
tournament artifact, all exit 0:

```
specialist-alice  written_by=specialist-alice  sha=a43013535760
reviewer-bob      written_by=reviewer-bob      sha=932c16e9d51d
specialist-alice  written_by=specialist-alice  sha=a43013535760
```

`_refuse_conflicting_overwrite` compares records through `_comparable`, which
drops `_VOLATILE_ANALYSIS_FIELDS = ("timestamp", "written_by")`. So an
**agreeing** re-run is correctly judged not to be a conflict — and the caller
then writes anyway, and the second agent's `written_by` replaces the first's.
A reviewer who omits `--out` and happens to confirm the specialist silently
takes ownership of the specialist's record. Nothing raises, because the guard
compares conclusions and provenance is not a conclusion.

Two things follow.

**For §8.0:** `--out` is load-bearing for safety after all, so the directory
instruction joins `--overwrite` in the duplicated set. Two lines, not four. The
rule survives; the example it was resting on did not.

**For guards generally, and this is the transferable part:** one function was
answering *is this a conflict?* while its caller read the answer as *may I
write?* Those two questions agree everywhere except on the fields excluded from
the comparison — which is to say they diverge, by construction, exactly where
nobody is looking. So: **a field excluded from an equality test is a field that
can change without anyone noticing. Exclude it only if losing it costs
nothing.** `timestamp` qualifies; it asserts nothing. `written_by` does not; it
is a claim of authorship, and it is the field the whole re-analysis scheme
exists to keep straight.

The tuple's **name** is what let the two sit together unchallenged. Calling
them volatile asserts that they change between runs, which is true of both, and
quietly implies that losing them costs nothing, which is true of one. A single
list holding both invites exactly the reading that was given it. Two fields
belong in one exclusion list only when they are excluded **for the same
reason**.

Detection existed and prevention did not — the reviewer template's pre/post
checksum over `raw/` goes VOID, because the file did change. That is the right
way round, and it still costs a discarded review.

The silent load failure is a separate defect, and mitigating it is not fixing
it. The repair is to make it loud: check that every skill URI a template
declares actually resolves, in the same mechanical way `tools/check_invocations.py`
checks that every `dde ...` command in the repo resolves. Until that check
exists, treat every duplication justified by "the skill might not load" as a
stopgap with the repair named next to it, not as a settled placement.

The consequence, adopted 2026-08-18: the orchestration templates carry
substantial `agents.md` bodies and **few skill grants**. The tables in §8.1 and
§8.2 name the behaviours that must exist; they no longer assert that each is a
separately packaged skill. The role templates are the authority on which of
these are template body and which are grants.

### 8.1 Science Program Lead behaviours

Template: `science-program-lead`. Placement as built, 2026-08-18.

| Behaviour | Owns | Placement |
|---|---|---|
| Program intake and chartering | Convert an objective into scope, hypotheses, policy needs, and human approval boundaries | Body §3 |
| Scientific work planning | Select the next decision question, role, evidence context, acceptance criteria, and cohort | Body §4 |
| Evidence synthesis and program state | Accept or reject findings and update Layer 2 with cited rationale | Body §5–§6, over the `program-state-management` grant |
| Cohort and critical-signal review | Perform batch synthesis and respond to interrupt-driven scientific alerts | Body §7 |
| Stage-gate decision | Freeze evidence, obtain review, evaluate criteria, and record advance/loop/pivot/stop decisions | Body §8 |

All five govern every task the role performs, so all five are body. The split inside
evidence synthesis is the one worth noting: **document mechanics** are a skill
(`program-state-management`), because they are reference material the agent can look
up; **what may be written into Layer 2, and on what authority** is body, because a lead
that only applies the acceptance rules when it remembers to load them is not bound by
them.

Grants: `artifact-conventions`, `program-state-management`, plus the platform skills
`artifact-durability` and `agent-state-continuity`.

### 8.2 Research Operations Controller behaviours

Template: `research-operations-controller`. Placement as built, 2026-08-18.

| Behaviour | Owns | Placement |
|---|---|---|
| Program bootstrap | Initialize program configuration, control state, artifact directories, and environment checks | Body §3, over `dde init` and `dde doctor` |
| Work-order supervision | Validate, queue, dispatch, monitor, retry, block, and close agent runs | Body §4–§6 |
| Artifact contract validation | Check deliverables, provenance, checksums, warnings, paths, and layer boundaries | Body §8, over the `artifact-conventions` grant |
| Resource and long-run management | Manage dependencies, leases, concurrency, external waits, and retry policy | Body §6 |
| Presentation and publication | Validate, build, deploy, and record presentation-layer revisions | Body §10 — **skill candidate**, see below |

Grants: `artifact-conventions`, plus `artifact-durability` and `agent-state-continuity`.

Publication is the one entry that passes the §8.0 test for a skill: it is occasional,
and a publish is self-evidently in play or not. It is in the body only because there is
nothing yet to package — `dde site build` does not exist, and the sole current
instruction is that publication is unavailable and must not be faked by hand or
delegated to a curator. **Move it to a skill when the CLI lands**, not before; a skill
whose entire content is "this does not work yet" is worse than a line in the body,
because it can go unloaded.

The four remaining entries govern every work order the controller handles and stay in
the body permanently.

### 8.3 Supporting procedural behaviours

| Behaviour | Belongs to | Placement |
|---|---|---|
| Independent scientific review | Scientific Reviewer | **Body** of `scientific-reviewer` |
| Gate report compilation | Regulatory Scientist or report writer | Skill — not yet written |
| Stakeholder narrative editing | Optional Project Curator | Skill — not yet written |

Independent review is the clearest case in this document for §8.0's first test. The
reviewer is spawned per review and performs exactly one kind of task, so its procedure
governs *every* task it will ever run. Packaging that as optional context would make
the program's fabrication check contingent on the reviewer choosing to load it.

The other two are genuine skills: both are occasional, both are obviously applicable
when they apply, and neither is unsafe to omit — a gate report that is not compiled is
a missing document, not a wrong one.

### 8.4 Placement rules

- Scientific knowledge about interpreting a tool result stays in the corresponding
  science capability skill.
- DDE-specific orchestration semantics belong in the orchestration behaviours
  above, placed in a template body or a skill by the §8.0 test — not automatically in
  a skill.
- Generic Scion command syntax and agent-management behavior come from platform
  skills; do not copy them into DDE skills.
- Schemas, legal state transitions, retries, locks, validation, and rendering belong
  in CLI code, not prose.
- Role authority, forbidden actions, and who receives an escalation belong in the
  role template's `agents.md`.
- Shared handoff fields belong in one CLI schema, not duplicate skills maintained by
  both persistent roles.

### 8.5 A capability claim in a template is a cache, and a planning cache is the dangerous kind

Any sentence in a template that describes what the system can currently do — a
capability table, a "no tool exists for this yet" placeholder, a named gap on
the critical path — is a **cache of the tool and skill inventory**, in the same
sense that a document describing code is a cache of that code. It is written at
a moment, it is never revalidated, and the inventory moves without telling it.

Both halves of this went wrong on 2026-08-18, within an hour of each other, and
the difference between them is the rule.

**In a doing position** (`f0714e3`): three specialist templates carried
placeholders saying no tool existed for tissue expression and no tool could
verify a citation. Both skills existed. Three agents were told to report blocked
on work they could do — and `experimental-biologist` was told it could not check
a citation directly above its own warning about fabricated PMIDs, which is the
anti-fabrication guard disarmed by a stale note about the guard.

**In a planning position** (`55f12d9`): `science-program-lead` carried a table
of which role can produce which evidence, and it named genetic and expression
evidence as the largest gap on the Stage 1 critical path. The gap had closed.

The second is worse, and not by degree. **A false capability claim in a doing
position is next to the evidence that refutes it** — the agent is standing
beside the skill and will eventually trip over it. **A false capability claim in
a planning position prevents the dispatch that would have refuted it.** The
capability is never exercised, so nothing fails, so no signal is generated. The
error suppresses its own evidence. That is the same shape as every other fault
in this document — something silently self-consistent — but arrived at by
withholding an action rather than by taking one, which is why none of the
existing checks would find it.

So: **caches in a planning position need shorter fuses than caches in a doing
position**, and a claim that stops work needs a shorter fuse than one that
merely misdirects it.

Where the cache cannot be mechanically expired, do not try to keep it fresh.
Refreshing resets the clock and changes nothing structurally. Instead **stop
staleness from being authoritative**, in three parts (template-builder,
`55f12d9`):

1. **Name the authority the cache is a copy of** — here, each role's own
   `scion-agent.yaml` and the specialist itself.
2. **Date the cache and say in the text that it decays from that date.** A
   snapshot labelled as a snapshot is honest; the same content unlabelled is a
   standing claim.
3. **Write the tie-break down, in the template body.** *If a specialist says it
   can do something the table says it cannot, the specialist is right and the
   table is stale. Proceed, and report the discrepancy.* Never argue a
   specialist out of a capability on the strength of a cache.

The tie-break is the cheap general fix. It does not prevent the cache going
stale. It converts the failure from a silent non-dispatch into a report — which
is the difference between an error that conceals itself and one that announces
itself the first time it matters.

### 8.6 The first tool in a role's domain is the one most likely to be over-read

**While a role has no tool in its domain, the pressure to answer has no outlet.**
A specialist instructed to report blocked rather than answer from background
knowledge is under real pressure to produce something, and while it holds
nothing that pressure is inert — the blocked report is the only move available,
so it is easy. The moment the role holds *one* number in its domain, the
pressure has somewhere to go.

This is why the first tool to land in a partially-tooled role is the most
over-read tool that role will ever hold, and why it is structural rather than a
matter of care. The substitution that results is also much harder to catch than
the blocked report it replaces: it arrives under a real tool's name, with a real
artifact, a real provenance sidecar and a real number behind it. Everything
about it looks like evidence except its relevance.

**The skill cannot carry this guard.** A skill's do-not-use list is correctly
scoped to *other skills* — use `protein-structure-confidence` for fold
confidence, `target-genetic-evidence` for constraint — because a skill cannot
see which role loaded it. But the dangerous substitution is not skill-for-skill,
it is **tool-for-untooled-question**, and which questions are untooled is a
property of the role, not of the skill. By the §8.0 placement test the content
is role-specific, so it belongs in the template body.

**Nor can the CLI carry it, and that is worth stating because the CLI carries
the neighbouring guard so well** (tooling-lead). DDE's tools are already
structurally proof against *fabrication*: a refusal exits 9, a phase-2 latch
will not invent the input it is missing, and no command will produce a number
whose computation did not happen. None of that touches *substitution*, because
the substituting invocation is legitimate on its face — the CLI cannot know
that a druggability score was requested only because an affinity tool was
missing. **Loud refusal, silent substitution, and a finding that looks normal.**
The defence has to sit where the missing capability is known, and that is the
template.

**The CLI carries a partial guard, and it must not be read as discharging this
one** (tooling-lead, `7a5a682`). A tool that cannot detect the substitution can
still say, in the sidecar beside the number, what the number is not:
`fpocket.druggability_is_not_affinity` fires on a druggable verdict and names
the substituting reader directly. That is real and it is upstream of the
template, so the temptation is to conclude the role-level prohibition is now
redundant. It is not, for two reasons. A relay **arrives with the artifact**,
which means it reaches an agent that already ran the wrong tool; the template
prohibition reaches the agent **deciding which tool to run**, which is the only
point at which the substitution can be declined rather than annotated. And a
relay can only contradict the wrong reading of *the number it accompanies* —
it cannot know which question went unanswered, so it cannot say *report this
task blocked*, which is the actual instruction the role needs.

That second reason is a **category limit, not a gap someone could close**
(plan-review). The artifact knows what it is; only the role knows what it was
asked. So these are not weak and strong versions of one defence, and no
improvement to the relay will ever begin to answer the role's question —
which is the inference to head off, because "the tooling is getting better at
this" is the most natural reason a future editor would have for deleting the
prohibition. Treat the relay as evidence that will be in the artifact when a
reviewer looks, not as a guard that fires before the mistake.

**And do not audit a role's exposure by counting relays, because the guard is
missing in exactly the place the count looks healthy** (tooling-lead's audit,
re-derived from the emission conditions). Relays divide into those that fire on
a *fault in the data* and those that fire on *the answer itself* — the
distinction and its consequences are in tool-design-guidance §8. The half that
matters when writing a template is this: a tool whose relays all fire on faults
**emits nothing at all on a clean run**, and the clean run is the one carrying
the most quotable number. So the artifact reaches your role barest exactly when
it is most over-readable, and a long relay table means the tool has many ways to
be *defective*, not that it has any guard against being *quoted*.

Two tools are in that state now, and they are a pair rather than a coincidence:
the structure predictor and the variant-effect predictor, the only two whose
output is a **model prediction rendered as a number**. Everything else in the
set reports a measurement, a database state or a tournament result — and a
measurement over-read is still a measurement, while a prediction over-read
becomes a fact. Nobody writes a caveat about a number their tool did not make
up, which is why the guard is absent in precisely the two tools that did. For
any role holding one of these, **the prohibition in the template is not merely
un-retired by upstream progress; in the common case it is the only guard that
exists.**

**A relay table is not evidence that a guard fires.** A code can be registered,
documented in a skill's table, and emitted by nothing — coverage on paper, and
indistinguishable from the real thing at the two places a template author looks.
So the check is: for each code a skill's relay table lists, confirm something
emits it. Enumerate from the registry and search for each code by name. Do not
grep for the emission idiom and read the result as a census: an audit that did
exactly that lost a whole call style to a `grep -v` written for a file and
applied to a line, and the missing rows included a relay registered an hour
earlier by the person running the audit. The population must come from the
definitive list, never from the pattern you hoped would match every spelling.

This is the mirror of the reader rule in tool-design-guidance §8 — that a
failure is loud only if it reaches someone who will *report* it. An agent
holding a refusal is by definition a blocked reader who is not going to report,
which is precisely the reader the exit code cannot help. The two rules are one
rule from opposite ends: that one says do not trust loudness when the reader is
an agent under pressure to produce; this one says name what that reader will
reach for instead, and forbid it by name.

**Name the near neighbours, not every gap.** The substitution that actually
happens is the semantically adjacent one. A toxicologist will never offer a
pocket score as a toxicity result; it will offer a tolerated knockout as
evidence of compound safety, because both live in the vocabulary of harm. So
name the closest one or two substitutions and stop. A do-not list that tries to
be complete gets skimmed, and the entries that mattered are skimmed with it —
false-alarm decay (§8.5's twin, in prose rather than in an instrument).

**Give every prohibition an expiry condition, and make it an observable test.**
A prohibition is a planning object, so §8.5 applies to it in the other
position: *"do not let a pocket score stand in for an affinity"* is true only
until an affinity tool reaches that role, after which it is a stale rule still
being obeyed. Write what retires it, in the prohibition.

Write it as **something a reader can check, not an intention** (tooling-lead,
`cf8fa75`). *"Retires when the upstream API improves"* names no act of
observation, so nobody performs one, and the prohibition outlives the fault by
however long it takes someone to wonder. *"Retires when this role's skill list
holds a docking or affinity skill — check `scion-agent.yaml`"* can be evaluated
in ten seconds by whoever next reads the page. The same distinction applies to
the standing caveats an instrument prints: of six upstream advisories in
`dde doctor`, all six said how to work around the fault and only one said
what would end it, which is the state in which a live warning and a warning
nobody has re-checked in a year are indistinguishable.

**Then say who can run the test, when not everyone can.** An observable test is
not automatically an available one. Two of those six advisories retire on
evidence that needs a credential and a live endpoint — score one variant per
output type, or send a deliberately malformed request — and a test only a
privileged reader can run will be run by nobody, leaving the advisory to
outlive its fault exactly as if it had named no test at all. *An unrunnable
test that names its owner is an assigned check; an unrunnable test that says
nothing is a dead one that looks alive.*

Where the tool can tell which reader it has, it should say so rather than make
the reader work it out: `dde doctor` resolves the credential and prints
either *"you can run this here"* or *"not runnable here — this test belongs to
whoever holds the key"* (`704c4f3`). The general form is the one template-builder
named in review: a rule states a condition, an instrument states which side of
it you are standing on, and the second is worth building wherever the tool
already holds the fact.

Also say **whether the risk decays with the expiry or peaks just before it**
— plan-review's point, in review of an earlier draft of this section. Those are
opposite operational instructions drawn from the same sentence, and both
readings look like compliance. The substitution prohibitions below peak: the
pressure to offer the wrong number is greatest on exactly the tasks where that
number is the only one the role has.

**The operational rule.** When wiring a skill into a partially-tooled role, do
not stop at correcting the now-false "you have no tool for this". Ask what
question the role is *still* not tooled for that sits closest in meaning to the
new tool's output, forbid that substitution by name, and say what retires the
prohibition.

Two live instances, both in `27ff4cd`: `computational-chemist` holds
`pocket-druggability` and exactly one structure-based number in a charter whose
central question is binding affinity; `preclinical-toxicologist` holds two
target-level skills in a role whose question is compound safety. The second was
written before this section was, which is some evidence the rule is real rather
than fitted to its examples — but both templates are the same author's, so the
confirmation is partial. It is a rule applied before it was named, not one
another party's work independently produced.

---

## 9. Failure and escalation policy

The controller classifies failures before acting:

| Class | Example | Controller action |
|---|---|---|
| Transient infrastructure | Rate limit, network reset, preempted container | Retry within recorded policy; preserve each run |
| Persistent infrastructure | Missing credential, failed `dde doctor`, unavailable binary | Block and report the exact prerequisite |
| Contract failure | Missing sidecar, broken link, report in wrong layer | Return for correction; do not send for scientific acceptance |
| Scientific block | Required input does not exist, assay cannot distinguish hypotheses | Escalate to science lead; do not substitute a different question |
| Critical scientific alert | Safety breach or invalidating contradiction | Pause affected dependents when policy says so and interrupt the science lead |
| Policy boundary | Human approval or resource limit reached | Block until the named authority decides |

Retries must be bounded and visible. The controller must never convert a scientific
block into an apparently successful result by changing inputs, relaxing criteria,
or selecting a cheaper fallback without authorization.

---

## 10. Pilot acceptance tests

The co-scientist, AlphaFold, and AlphaGenome pilot should test orchestration as well
as tool and skill conversion. A successful pilot demonstrates:

1. parallel AlphaFold and AlphaGenome work orders with immutable context snapshots
2. a long-running co-scientist job that survives a controller wait and restart
3. a forced transient failure and retry with separate run provenance
4. rejection of an intentionally broken sidecar or artifact link
5. routing of a structured critical alert before normal batch completion
6. independent review followed by science-lead acceptance or revision
7. Layer 2 synthesis using only scientifically accepted findings
8. threshold re-analysis without repeating the expensive fetch or run
9. an idempotent site rebuild that exposes the accepted revision and no stale claim

Revise this document, the tool guidance, and the skill guidance from observed pilot
failures before converting the remaining science-skills corpus.

---

## 11. Checklist

- [ ] One Science Program Lead has unambiguous scientific decision authority
- [ ] The Research Operations Controller has bounded operational autonomy
- [ ] Every specialist run references an immutable work-order revision
- [ ] Work-order identity and run identity are separate
- [ ] Dispatch context is bounded, linked, versioned, and checksummed
- [ ] Control-plane records are machine-written outside Layers 0-4
- [ ] Mechanical validation precedes scientific acceptance
- [ ] Only accepted findings enter Layer 2 state
- [ ] Gate-critical claims receive independent scientific review
- [ ] Alerts originate from structured output or explicit policy where possible
- [ ] Retries preserve failed attempts and never modify scientific intent
- [ ] Publication is deterministic, idempotent, and limited to accepted artifacts
- [ ] Role-specific authority and escalation rules live in templates
- [ ] Obligatory behaviour is in the template body; only genuinely occasional behaviour is a skill grant
- [ ] Every role prohibition has been tested by walking the program from zero, so that no rule written to prevent role creep blocks the bootstrap
- [ ] Each exception to a prohibition is restated wherever the prohibition is restated, including in compressed rules lists
- [ ] State transitions, validation, and rendering are enforced in CLI code
