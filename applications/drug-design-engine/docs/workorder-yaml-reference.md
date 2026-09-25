# Work Order YAML Reference

Reference for the YAML schema accepted by `dde workorder create --from <file>`.

> **Alias:** `wo` is a shorthand for `workorder`. Every command below works
> identically with either name — e.g. `dde wo create` and
> `dde workorder create` are the same command.

For the design rationale behind work orders, see
[orchestration-design-guidance.md](orchestration-design-guidance.md) sections 3-5.

---

## 1. Required fields

Every work-order YAML file must include all 13 content fields listed below.
Missing any of them causes a `SchemaError` at creation time.

| Field                | Type   | Description |
|----------------------|--------|-------------|
| `decision_question`  | string | The scientific question this work order answers. |
| `requested_role`     | string | The specialist role needed (e.g. `"analyst"`, `"modeler"`). |
| `stage`              | string | Program stage this work belongs to. |
| `cycle`              | string | Iteration cycle within the stage. |
| `context`            | dict   | Background context for the specialist. See [Context field details](#7-context-field-details). |
| `dependencies`       | list   | Prior work-order IDs or artifacts this work depends on. |
| `capabilities`       | list   | Required specialist capabilities or tool access. |
| `deliverables`       | dict   | **Artifact layers the specialist must produce.** Must be a dict (not a list) with keys `layer_1` (list of report paths) and/or `layer_0_classes` (list of registered artifact-class names). See [Deliverables schema](#8-deliverables-schema). |
| `acceptance_criteria` | dict or list | Criteria the deliverables must satisfy to pass scientific review. |
| `alert_policy`       | dict   | **Conditions that trigger alerts during execution.** Keys are alert names; values define thresholds or rules. Must be a dict, not a list. |
| `priority`           | string | Priority level (e.g. `"high"`, `"normal"`, `"low"`). |
| `resource_class`     | string | Compute resource tier to allocate. |
| `report_to`          | string | Who receives the results (e.g. `"science-lead"`). |

### Type enforcement

The control-plane store validates these field types on every write:

- **`context`** must be a `dict` — not a string or list.
- **`dependencies`** must be a `list` — not a dict.
- **`capabilities`** must be a `list` — not a dict.
- **`deliverables`** must be a `dict` — not a list.
- **`alert_policy`** must be a `dict` — not a list.

Other fields (`decision_question`, `requested_role`, `stage`, `cycle`,
`priority`, `resource_class`, `report_to`, `acceptance_criteria`) are checked
for presence but not for type at the store layer.

---

## 2. CLI-managed fields

The following fields are set automatically by the CLI. **Do not include them in
your YAML input** — they will be ignored if present.

| Field          | Set by            | Description |
|----------------|-------------------|-------------|
| `id`           | `workorder create` | Sequential ID in the form `WO-001`, `WO-002`, etc. |
| `revision`     | `workorder create` / `revise` | Positive integer, starts at 1. |
| `state`        | `workorder create` / `transition` | Current lifecycle state (see [Lifecycle states](#3-work-order-lifecycle-states)). |
| `created_at`   | `workorder create` / `revise` | ISO 8601 UTC timestamp. |
| `committed_at` | `workorder commit` | ISO 8601 UTC timestamp, set when the work order is committed. |

---

## 3. Work order lifecycle states

A work order moves through a state machine from creation to a terminal state.
Each arrow represents a legal transition.

```
(new) ──► proposed ──► committed ──► queued ──┬──► in_progress ──┬──► submitted
                         │                    │                  │
                         │                    ├──► blocked       ├──► blocked
                         │                    │                  │
                         │                    └──► cancelled     └──► cancelled
                         │
                         ▼
                       queued
                         │
                         ▼
                    in_progress
                         │
                         ▼
                     submitted ──┬──► validation_failed ──► in_progress
                                │                              (retry)
                                │
                                ▼
                     mechanically_validated
                                │
                        ┌───────┼───────────────────┐
                        ▼       ▼                   ▼
          scientifically   under_scientific    revision_requested
            _accepted        _review           scientifically
                               │                 _rejected
                        ┌──────┼──────┐
                        ▼      ▼      ▼
            scientifically  revision  scientifically
              _accepted   _requested   _rejected
```

### Transition table

| From state                | Legal targets |
|---------------------------|---------------|
| *(initial — no state)*    | `proposed` |
| `proposed`                | `committed` |
| `committed`               | `queued` |
| `queued`                  | `in_progress`, `blocked`, `cancelled` |
| `in_progress`             | `submitted`, `blocked`, `cancelled` |
| `submitted`               | `validation_failed`, `mechanically_validated` |
| `validation_failed`       | `in_progress` |
| `mechanically_validated`  | `scientifically_accepted`, `under_scientific_review`, `revision_requested`, `scientifically_rejected` |
| `under_scientific_review` | `scientifically_accepted`, `revision_requested`, `scientifically_rejected` |

### Terminal states (no outgoing transitions)

- `scientifically_accepted`
- `revision_requested`
- `scientifically_rejected`
- `blocked`
- `cancelled`

The `validation_failed → in_progress` loop is the recovery path for mechanical
failures (missing sidecar, wrong path) without requiring a new revision.

---

## 4. Run lifecycle states

A run tracks a single execution attempt against a committed work-order revision.
It moves through its own state machine from creation to a terminal state. Each
arrow represents a legal transition.

```
(new) ──► queued ──► starting ──► running ──┬──► succeeded
                                            │
                                            ├──► failed
                                            │
                                            ├──► blocked
                                            │
                                            └──► cancelled
```

### Transition table

| From state             | Legal targets |
|------------------------|---------------|
| *(initial — no state)* | `queued` |
| `queued`               | `starting` |
| `starting`             | `running` |
| `running`              | `succeeded`, `failed`, `blocked`, `cancelled` |

### Terminal states (no outgoing transitions)

- `succeeded`
- `failed`
- `blocked`
- `cancelled`

### Transition mode

All run transitions are explicit CLI invocations — no transition is automatic.
A new run is created in the `queued` state by `dde run create <WO-ID>`, and
every subsequent transition is performed with
`dde run transition <RUN-ID> <STATE>`.

### Commands

| Command | Effect |
|---------|--------|
| `dde run create <WO-ID>` | Creates a new run targeting the latest committed revision, in `queued` state. |
| `dde run transition <RUN-ID> <STATE>` | Advances the run to `<STATE>` if the transition is legal. |

If you attempt an illegal transition, `dde run transition` exits with code 9
(`Refusal`) and reports the current state, the rejected target, and the set of
legal targets — so you can discover the valid next states from the error message
itself. This section documents the full graph upfront to remove the need to
discover valid transitions by trial and error.

---

## 5. Annotated example YAML

A complete, minimal work order YAML that passes validation:

```yaml
# The scientific question this work order answers.
decision_question: "What is the correlation between variables X and Y in dataset A?"

# Specialist role needed to do the work.
requested_role: "analyst"

# Program stage and cycle.
stage: "exploration"
cycle: "cycle-1"

# Background context for the specialist.
# Must be a dict. Can include artifact_links with paths and content.
context:
  description: "Analyze the relationship between X and Y using linear regression."
  artifact_links:
    - path: "data/dataset-a.csv"
      description: "Primary dataset"
  content: "Prior work suggests a positive correlation (see WO-001 findings)."

# Prior work this depends on. Must be a list.
dependencies:
  - "WO-001"

# Required capabilities. Must be a list.
capabilities:
  - "statistical-analysis"
  - "python"

# Artifact layers to produce. Must be a dict — NOT a list.
# Two separate checks apply at different points in the lifecycle:
#   1. `workorder create` verifies this is a dict (type check).
#   2. `validate check` requires specific keys: layer_1 and/or layer_0_classes
#      (schema check) — arbitrary named keys will cause 5 of 8 checks to skip.
# Values for layer_0_classes must be registered artifact classes —
# see "Deliverables schema" (§8) for the current list.
deliverables:
  layer_0_classes: ["structures", "docking"]
  layer_1: ["findings/structural-biology/wo-002-report.md"]

# Acceptance criteria for scientific review.
acceptance_criteria:
  - "Report includes p-values and confidence intervals"
  - "Analysis accounts for potential confounders"

# Alert conditions during execution. Must be a dict — NOT a list.
# Each key is an alert name; value defines the condition.
alert_policy:
  data_quality:
    condition: "missing values exceed 10%"
    severity: "warning"
  runtime:
    condition: "execution exceeds 30 minutes"
    severity: "critical"

# Priority and resource allocation.
priority: "normal"
resource_class: "standard"

# Who receives the results.
report_to: "science-lead"
```

---

## 6. Common validation errors

### "missing required fields in YAML input: ..."

One or more of the 13 required fields is absent from the YAML file.

**Fix:** add every field from the [Required fields](#1-required-fields) table.

### "deliverables must be dict, got list"

The `deliverables` field was written as a YAML list (sequence), but the schema
requires a dict (mapping) with named keys.

```yaml
# WRONG — list
deliverables:
  - "correlation report"
  - "regression plot"

# WRONG — dict but with arbitrary keys (passes `workorder create` type
# check, but `validate check` will fail: no layer_1 or layer_0_classes)
deliverables:
  correlation_report:
    description: "correlation report"

# RIGHT — dict with the required layer keys
deliverables:
  layer_0_classes: ["structures", "docking"]
  layer_1: ["findings/structural-biology/wo-002-report.md"]
```

### "alert_policy must be dict, got list"

Same issue as deliverables. Use named keys, not a list.

```yaml
# WRONG — list
alert_policy:
  - condition: "missing values exceed 10%"
  - condition: "execution exceeds 30 minutes"

# RIGHT — dict with named keys
alert_policy:
  data_quality:
    condition: "missing values exceed 10%"
  runtime:
    condition: "execution exceeds 30 minutes"
```

### "YAML input must be a mapping, got ..."

The YAML file does not parse to a top-level dict. This usually means the file
starts with a list (`- ...`) or contains only a scalar value.

**Fix:** ensure the file is a YAML mapping (key-value pairs at the top level).

### "context content exceeds size limit"

The `context.content` field, when encoded as UTF-8, exceeds the 256 KB limit.

**Fix:** reduce the inline content, or move large context into artifact links
that reference files by path instead of embedding their content.

---

## 7. Context field details

The `context` field is a dict that provides background information to the
specialist. It supports these keys:

| Key              | Type   | Description |
|------------------|--------|-------------|
| `description`    | string | Free-text description of the task context. |
| `artifact_links` | list   | References to project files relevant to the work. |
| `content`        | string | Inline content (capped at **256 KB** UTF-8). |

### Artifact links

Each entry in `artifact_links` is a dict with:

| Key           | Type   | Description |
|---------------|--------|-------------|
| `path`        | string | Path relative to the project root. Must not escape the project root. |
| `description` | string | What this artifact provides. |

When a work order is **committed** (`dde workorder commit`), the CLI:

1. Resolves each `path` relative to the project root.
2. Verifies the file exists and is within the project boundary.
3. Computes a `sha256` checksum for each artifact and stores it in the link.
4. Creates a context snapshot record under `.dde/control/contexts/`.

<!-- NOTE: §8 numbering follows §7; update if sections are reordered. -->

### Content size limit

The `context.content` field is capped at **256 KB** (262,144 bytes) of UTF-8
text. Exceeding this limit produces a `SchemaError` at commit time. To include
larger context, split it across multiple artifact links that reference files by
path rather than embedding content inline.

---

## 8. Deliverables schema

The `deliverables` field is validated at **two separate points** in the work
order lifecycle, each enforcing a different constraint:

| Check point          | Command               | What it checks |
|----------------------|-----------------------|----------------|
| **Type check**       | `dde workorder create` | `deliverables` must be a `dict`, not a `list`. Rejects the wrong container type. |
| **Schema check**     | `dde validate check`   | The dict must contain at least one of the recognized keys: `layer_1` and/or `layer_0_classes` (alias: `layer_0`). A dict with only arbitrary keys causes 5 of 8 mechanical checks to skip, which is treated as a failure. |

A work order that passes creation (type check) can still fail validation
(schema check) if its `deliverables` dict uses unrecognized keys.

### Required keys

| Key                | Type           | Description |
|--------------------|----------------|-------------|
| `layer_0_classes`  | list of strings | Registered artifact-class names whose Layer 0 artifacts the work order covers. The alias `layer_0` is also accepted and normalized to `layer_0_classes` internally. |
| `layer_1`          | list of strings | Paths (relative to the project root) to Layer 1 findings reports. |

At least one of `layer_0_classes` / `layer_0` or `layer_1` must be present.
Both may be present when a work order covers raw artifacts and a written report.

### Registered artifact classes

Values in `layer_0_classes` must be registered artifact-class names — each
one corresponds to a subdirectory under `raw/` in the project. The
authoritative list lives in `tools/dde/core/context.py` (`ARTIFACT_DIRS`).

<!-- MAINTENANCE NOTE: This list is inlined for convenience. If it drifts from
     ARTIFACT_DIRS in core/context.py, context.py is the source of truth.
     The tradeoff: inlining gives readers immediate visibility without reading
     source code, but requires manual updates when new classes are added. -->

As of this writing, the registered classes are:

| Class          | Directory           |
|----------------|---------------------|
| `structures`   | `raw/structures`    |
| `docking`      | `raw/docking`       |
| `compounds`    | `raw/compounds`     |
| `admet`        | `raw/admet`         |
| `analogs`      | `raw/analogs`       |
| `assays`       | `raw/assays`        |
| `expression`   | `raw/expression`    |
| `literature`   | `raw/literature`    |
| `genomics`     | `raw/genomics`      |
| `hypotheses`   | `raw/hypotheses`    |
| `gtex`         | `raw/gtex`          |
| `mpo`          | `raw/mpo`           |
| `pk`           | `raw/pk`            |
| `tox`          | `raw/tox`           |

Using a value not in this list (e.g. `"expression_or_gtex"`, `"reanalysis"`)
will pass the schema check but cause the `deliverables_exist` check to fail,
because no `raw/` subdirectory maps to that name.

### Common mistakes

```yaml
# WRONG — invented key names (not recognized by validate check)
deliverables:
  layer1_path: "findings/report.md"
  required_layer0_classes: ["structures"]

# WRONG — non-registered artifact class
deliverables:
  layer_0_classes: ["expression_or_gtex"]   # not a real class; use "expression" and/or "gtex"

# RIGHT
deliverables:
  layer_0_classes: ["structures", "docking"]
  layer_1: ["findings/structural-biology/wo-002-report.md"]
```

---

## 9. Create → commit workflow

A work order must be **created** (state: `proposed`) and then **committed**
(state: `committed`) before it can be queued for execution. Committing freezes
the content fields and creates a context snapshot with artifact checksums.

### Two-step (explicit)

```bash
# Step 1: create the work order in proposed state.
dde workorder create --from wo-spec.yaml
# → WO-003 created (revision 1, state: proposed)

# Step 2: commit it — freezes content, creates context snapshot.
dde workorder commit WO-003
# → WO-003 committed (revision 1)
```

Between create and commit you may update content fields with
`dde workorder update WO-003 --from updated-spec.yaml` as many times as
needed. Once committed, content is frozen; further changes require a new
revision (`dde workorder revise`).

### Single-step shortcut

When the YAML is already final and no intermediate edits are needed, pass
`--commit` to create both records in one invocation:

```bash
dde workorder create --from wo-spec.yaml --commit
# → WO-003 created and committed (revision 1, state: committed)
```

This runs the full create validation followed by the full commit validation —
no checks are weakened or skipped. The result is identical to running the two
commands separately.

### Which to use

| Situation | Command |
|-----------|---------|
| YAML is final, ready to queue immediately | `dde wo create --from spec.yaml --commit` |
| Need to review or edit after creation | `dde wo create --from spec.yaml`, then `dde wo commit <ID>` |
| Updating a proposed WO before committing | `dde wo update <ID> --from updated.yaml` |


---

## 10. Accept shortcut — mechanical auto-step to validated

Getting a committed work order all the way to `scientifically_accepted`
normally requires 5–6 separate CLI calls. Only the last transition
(`mechanically_validated → scientifically_accepted`) is a genuine scientific
judgment call. Everything before it — `committed → queued → in_progress →
submitted → mechanically_validated` — is mechanical bookkeeping.

`wo accept` automates the mechanical portion in a single invocation.

### Usage

```bash
dde wo accept <ID>
```

### What it does

1. **Auto-steps** through every mechanical intermediate transition from the
   WO's current state to `submitted`: `committed → queued → in_progress →
   submitted`. If the WO is already partway through (e.g. already
   `in_progress`), it walks forward from there.

2. **Runs real mechanical validation** — the same 8-check `validate check`
   logic — at the `submitted → mechanically_validated` boundary. This is not
   a state flip; all 8 checks run with real pass/fail consequences.

3. **Stops at `mechanically_validated`** if all checks pass. It does **not**
   auto-transition to `scientifically_accepted` — that requires an explicit
   scientific judgment:

   ```bash
   dde workorder transition <ID> scientifically_accepted
   ```

4. **Stops at `validation_failed`** if any check fails, surfacing the same
   failure output that `validate check` would produce, plus a remedy
   pointing at `override` or retry.

5. **Preserves full audit granularity** — every intermediate transition is
   logged as a separate event in `events.ndjson`, identical to manual calls.

### Examples

```bash
# Full chain from committed:
dde wo accept WO-003
#   WO-003: committed → queued
#   WO-003: queued → in_progress
#   WO-003: in_progress → submitted
#
#   Running mechanical validation on WO-003...
#
#   deliverables_exist        PASS
#   report_headings           PASS
#   ...
#
#   WO-003: submitted → mechanically_validated  [validation PASS]
#
#   WO-003 is now mechanically_validated and ready for scientific acceptance.
#     dde workorder transition WO-003 scientifically_accepted

# Starting partway through (already in_progress):
dde wo accept WO-003
#   WO-003: in_progress → submitted
#
#   Running mechanical validation on WO-003...
#   ...

# Validation failure — stops with actionable output:
dde wo accept WO-003
#   WO-003: committed → queued
#   WO-003: queued → in_progress
#   WO-003: in_progress → submitted
#
#   Running mechanical validation on WO-003...
#
#   report_headings           FAIL
#   ...
#
#   WO-003: submitted → validation_failed  [validation FAIL]
#
#   Failed checks: report_headings
#
#   Remedy:
#     1. Fix the failing deliverables and resubmit, or
#     2. Use `dde workorder override WO-003` to override eligible checks
```

### What it does NOT do

- **Does not auto-transition to `scientifically_accepted`** — that is a
  human judgment call.
- **Does not bypass the #178 override guard** — `validation_failed →
  mechanically_validated` still requires `dde workorder override`.
- **Does not bypass the #237 submitted-bypass guard** — the real
  `validate check` logic runs; the `submitted → mechanically_validated`
  transition is never a direct state flip.
- **Does not handle `proposed` state** — the WO must be committed first
  (`dde workorder commit <ID>`).

### Interaction with override and retry

If `accept` stops at `validation_failed`:

| Next step | Command |
|-----------|---------|
| Fix deliverables and retry | `dde workorder transition <ID> in_progress`, fix issues, `dde wo accept <ID>` |
| Override eligible checks | `dde workorder override <ID> --reason "..." --evidence "..." --checks "..." --actor "..."` |

---

## 11. Work order templates

Reusable YAML templates for common work-order patterns live in
`tools/templates/work-orders/`. Each template is a complete, valid YAML file
with `[PLACEHOLDER]` values that you copy and fill in before creating the work
order.

### Available templates

| Template | File | Purpose |
|----------|------|---------|
| Foundational Claim Check | `tools/templates/work-orders/foundational-claim-check.yaml` | Fast-fail a single contradicted claim before committing a full validation cohort. |

### Foundational Claim Check

**What it is.** A lightweight, single work order that tests ONE foundational
claim identified as contradicted during `dde coscientist analyze`. The outcome
is binary: SUPPORTED or REFUTED.

**When to use it.** After `dde coscientist analyze` flags contradicted claims
on the recommended idea (verdict `leader-with-advisories`), instead of
immediately dispatching a full validation cohort (e.g. 5 parallel work orders),
dispatch this single claim-check first. It targets the most-contradicted
foundational claim.

**The fast-fail short-circuit rule.** The acceptance criteria in the template
enforce a binary outcome:

- **SUPPORTED** — the foundational claim holds. Proceed with the full
  validation cohort as planned.
- **REFUTED** — the foundational claim fails. Skip the full cohort, pivot to
  the next candidate idea, and save the compute that would have been wasted on
  a doomed cohort.

The template's `alert_policy` requests a CRITICAL alert if the claim is
directly refuted, so the coordinator can act immediately without waiting for the
full analysis cycle.

**Example workflow:**

```
1. dde coscientist analyze tournament.json
   → Verdict: leader-with-advisories
   → Leader: GENE_X (3 contradicted claims)

2. Copy the foundational-claim-check template, fill in GENE_X details:
   cp tools/templates/work-orders/foundational-claim-check.yaml \
      wo-gene-x-claim-check.yaml
   # Edit wo-gene-x-claim-check.yaml — fill in all [PLACEHOLDER] values

3. dde workorder create --from wo-gene-x-claim-check.yaml --commit
   → WO-007 created and committed

4. Execute WO-007 (single specialist, fast turnaround)

5a. Outcome: SUPPORTED
    → Dispatch full validation cohort (WO-008 through WO-012)

5b. Outcome: REFUTED
    → Skip full cohort, pivot to runner-up idea
```

**Template location:** `tools/templates/work-orders/foundational-claim-check.yaml`
