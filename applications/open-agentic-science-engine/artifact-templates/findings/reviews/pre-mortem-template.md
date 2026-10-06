# Pre-Mortem Review: [finding title]

**Reviewer**: [reviewer role] | **Date**: [date]
**Finding under review**: [findings/<path>]
**Decision question**: [from brief]
**Review budget**: [N objections — the maximum number of failure hypotheses the lead will pursue]

## Summary

2-3 sentences. The purpose of this pre-mortem and the key failure modes
identified.

## Review Budget Declaration

The lead has declared a review budget of **[N]** objections for this
pre-mortem. This bounds the challenge process: the lead selects up to
[N] decision-relevant objections from the failure hypotheses below.
Remaining hypotheses are recorded but do not block progress unless they
carry a discriminating check that the lead elevates.

## Failure Hypotheses

Each hypothesis proposes a specific, scoped failure mode. Speculative
narratives without a discriminating check are noted but do not constitute
accepted fatal flaws — they cannot gate progress.

<!-- Entry template for each hypothesis:

### [OBJ-NNN]: [short name]

**Plausibility**: high | moderate | low
**Consequence**: [what happens if this failure mode is real]
**Evidence**: [what evidence exists for or against this hypothesis]
**Discriminating check**: [a specific, executable test that would
determine whether this failure mode is real — or "none" if this is
a speculative concern]

If discriminating check is "none", this hypothesis is speculative.
It is recorded for completeness but does not block work.

-->

## Speculative Concerns (No Discriminating Check)

Failure hypotheses listed here have no discriminating check. They are
recorded for transparency but do not gate progress or count against
the review budget.

## Causal-Language Discipline

When formulating failure hypotheses, maintain the distinction between
correlational/descriptor-level signals and causal/definitive claims:

- Cardiac target expression does **not** establish hERG inhibition.
- A descriptor-based permeability concern does **not** establish zero
  permeability.
- An expression-level signal does **not** establish a functional
  consequence.

Each failure hypothesis must state what the evidence actually shows at
the level it shows it. Upgrading a correlational signal to a causal
claim in the framing of a failure hypothesis is itself a finding error.

## Objection Resolutions

Populated by the Science Program Lead after reviewing the failure
hypotheses. Each objection within the review budget receives one of
four resolution types:

<!-- Resolution entry template:

### [OBJ-NNN] Resolution

**Resolution type**: accepted | rebutted | accepted_risk | unresolved
**Rationale**: [why this resolution was chosen]
**Evidence refs**: [for rebuttal — what evidence supports the rebuttal]
**Policy ref**: [for accepted_risk — the policy under which the risk is accepted]
**Owner**: [for unresolved — who will investigate the follow-up]
**Liability tracker ref**: [for unresolved/accepted_risk — entry in liability-tracker.md]

Resolution type definitions:
- **accepted**: The lead agrees with the objection. The plan changes.
- **rebutted**: The lead disagrees, citing specific evidence.
- **accepted_risk**: The lead acknowledges the risk but proceeds,
  citing the applicable policy.
- **unresolved**: Neither accepted nor rebutted. Assigned to an
  owner for follow-up investigation.

-->

## Budget Exhaustion

If the review budget is exhausted before all substantive (non-speculative)
failure hypotheses are addressed:

- **Remaining unaddressed hypotheses with discriminating checks** are
  recorded as **unresolved follow-ups** with assigned owners.
- They are entered in `liability-tracker.md` with severity and receiving
  role.
- They **must not be silently dropped** from the decision snapshot or
  gate documents.
