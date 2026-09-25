---
name: stage1-gate-evaluation
description: >
  Evaluate intervention concepts against a Stage 1 (Target Nomination)
  gate policy. Use when the program lead is ready to make a gate
  decision — advancing, looping, or terminating a concept at Stage 1.
  Produces a structured evaluation against each applicable policy
  requirement, a policy freeze snapshot, and the gate document from
  the target-nomination-package template.
---

## 1. When to use, and when not

Use this skill when:

- A Stage 1 gate decision is due for one or more intervention concepts.
- The program has a defined gate policy (GP-NNN) for Stage 1.
- Evidence assessments (AR-NNN) exist for the concepts under evaluation.

Do not use this skill when:

- No gate policy exists — use the existing judgment-based gate process
  from the lead template instead.
- The concept is not yet in `active` state — it must be active to be
  evaluated at a gate.
- You need to create the policy itself — that is a separate task.

## 2. Prerequisites

Before starting, confirm:

1. The gate policy record exists in `.dde/control/policies/` and is the
   version referenced in `.dde/program.yaml` under `gate_policies.stage_1`.
2. The concept records under evaluation exist in `.dde/control/concepts/`.
3. Assessment records (AR-NNN) exist for the relevant evidence.
4. Threshold sets referenced by policy requirements are loaded and
   resolvable via `thresholds.py`.

## 3. Evaluation procedure

### Step 1: Identify concepts under evaluation

Read the concept records for the targets being evaluated at this gate.
Note each concept's `modality`, `disease_context.indication`, and
current revision.

### Step 2: Load the applicable policy

Load the Stage 1 gate policy at the version specified in
`program.yaml` → `gate_policies.stage_1`. If no version is specified,
check `.dde/control/policies/` for the latest version with
`stage: 1`.

### Step 3: Filter applicable requirements

For each concept, determine which policy requirements apply using the
applicability rules:

- `applicability.modality` is null OR contains the concept's `modality`
- `applicability.stage` is null OR contains `1`
- `applicability.indication` is null OR contains the concept's
  `disease_context.indication`

Requirements that do not apply are skipped entirely — they do not
produce `not_assessed` records.

### Step 4: Match assessments to requirements

For each applicable requirement, find assessment records with matching
`evidence_type`. Apply the compatibility checks from the design
(§3.1):

1. **`evidence_type` must match exactly.** This is the primary key.
2. **`units` must be compatible.** If both specify units and they
   differ, record a warning. Do not reject the match.
3. **`method` is informational.** Record it for provenance. If the
   requirement has `applicability.method_required`, only assessments
   using those methods satisfy the requirement.
4. **`endpoint` should match.** A mismatch is a warning, not a
   rejection.

### Step 5: Evaluate by requirement type

For each applicable requirement with a matching assessment:

- **`hard_constraint`**: Check `evidence_status`. If not `"supported"`,
  the requirement blocks advancement unless overridden with named
  authority and recorded justification.

- **`scientific_cutoff`**: Resolve the threshold value from the named
  threshold set via `ThresholdSet.get(threshold_key)`. Compare the
  assessment's metric value against the threshold using the specified
  `direction` (`above`, `below`, or `within`). The threshold value
  comes from `thresholds.py:load()`, never from the policy record.

- **`prioritization_heuristic`**: Record the assessment result. This
  informs decision ordering but does not block advancement.

For requirements with no matching assessment: record as unevaluated.
A hard constraint with no assessment blocks advancement.

### Step 6: Record unit/method compatibility warnings

Any unit or method mismatches from Step 4 must be acknowledged in the
decision rationale. They do not block the gate decision but must be
visible in the gate document.

## 4. Freeze and record

After evaluation, before compiling the gate document:

1. **Freeze the policy** using `policy.freeze_policy()`. This creates
   an immutable snapshot (SNAP-NNN) recording:
   - The policy version and all requirements (deep copy)
   - All referenced threshold sets with their `applied` values,
     `sources`, `provenance`, and `unresolved` keys
   - The concept refs under evaluation
   - The assessment IDs considered
   - The decision record ref (once recorded)

2. **Record the decision** as a decision record (DR-NNN) citing the
   assessments that informed it.

3. **Compile the gate document** from
   `artifact-templates/gates/stage1-target-nomination/target-nomination-package.md`,
   filling in:
   - `Policy version`: the GP-NNN@V reference
   - `Snapshot ref`: the SNAP-NNN identifier

## 5. UNRESOLVED threshold handling

If any threshold set referenced by a frozen policy has UNRESOLVED
values (non-empty `unresolved` list in the snapshot):

- The gate decision **must acknowledge the gap** explicitly.
- An UNRESOLVED threshold that a `scientific_cutoff` requirement
  depends on means that requirement **cannot be evaluated** — treat
  it the same as a missing assessment for a hard constraint.
- Record which thresholds are unresolved and why in the decision
  rationale.

## 6. Output

The skill produces:

1. A policy freeze snapshot (SNAP-NNN.json) in `.dde/control/snapshots/`
2. A gate document from the target-nomination-package template
3. A decision record (DR-NNN) if a decision is made

## 7. Verification

Before declaring the gate evaluation complete:

- [ ] Every applicable requirement has been evaluated or explicitly
      marked as unevaluated with rationale.
- [ ] All unit/method compatibility warnings have been recorded.
- [ ] The policy freeze snapshot includes all referenced threshold
      sets with their `unresolved` fields populated.
- [ ] The gate document cites the policy version and snapshot ref.
- [ ] UNRESOLVED thresholds are acknowledged in the decision rationale.
