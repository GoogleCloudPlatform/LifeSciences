# Stage 1 Gate: Target Nomination Package

**Program**: [program name]
**Date**: [date]
**Decision**: advance | loop | terminate
**Policy version**: [GP-NNN@V — the gate policy version in force for this decision]
**Snapshot ref**: [SNAP-NNN — the policy freeze snapshot recording the full state at decision time]

## Nominated Target

**Target**: [gene/protein]
**Indication**: [disease]
**Modality**: [small molecule | biologic | etc.]

## Gate Criteria Evaluation

Each criterion cites the accepted Layer 1 finding and the specific
values that support it. Tool thresholds are cited by name, not value.

### Causal Evidence

[Summary of genetic/functional evidence linking target to disease.
Cite accepted findings and the threshold set applied.]

### Tractability

[Summary of structural/binding site evidence confirming a druggable
interface for the chosen modality.]

### Safety / Tolerability

[Summary of constraint data, tissue expression profile, and known
on-target liabilities.]

### Functional Validation

[Summary of phenotypic or functional evidence in disease-relevant
models, where available.]

## Unresolved Liabilities

[Any open items from liability-tracker.md that were not mitigated
before this gate. Each must have a recorded disposition: accepted
risk, deferred to Stage 2, or blocking.]

## Pre-Mortem Dissent Record

[This section is mandatory when a pre-mortem review was conducted.
It is derived from the decision record's conditions and the
pre-mortem review artifacts — not editorially curated.
"Presentation is derived from the decision, not its authority."

An unresolved objection that exists in the underlying decision
record or liability tracker MUST appear here. Dropping it in the
gate-document generation step is a process failure.

Use `generate_gate_dissent_section()` from `core/premortem.py` to
produce this content from program state, or populate manually from
the pre-mortem template and decision record conditions.]

### Unresolved Objections

[List each unresolved objection from the pre-mortem review:
- OBJ-NNN: description, plausibility, consequence, discriminating check]

### Accepted Risks

[List each objection resolved as accepted_risk:
- OBJ-NNN: rationale, policy reference]

## Evidence Snapshot

| Finding | Path | Accepted | Reviewer |
|---|---|---|---|

## Decision Rationale

[Why the decision was made, citing the evidence above. For advance:
what makes the target worth pursuing. For loop: what additional
evidence is needed. For terminate: what disqualified the target.]
