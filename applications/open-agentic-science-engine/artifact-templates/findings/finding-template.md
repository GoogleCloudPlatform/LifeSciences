# [Finding Title]

**Role**: [role-name] | **Date**: [date]
**Task ref**: [work-order ID and revision, or orchestrator dispatch reference]

## Summary

2-3 sentence bottom line up front. State the answer to the decision question,
the confidence level, and the single most important implication.

## Key Findings

Narrative with inline references to raw data. Every quantitative claim
links to its source artifact:

> Example: "GENE_X shows pLI 0.95, indicating strong loss-of-function
> intolerance ([raw/genomics/GENE_X.gnomad-constraint.json]), but LOEUF
> 0.48 exceeds the current threshold
> ([raw/genomics/GENE_X.gnomad-constraint.analysis.json])."

Use vertical links for Layer 0 data: `[raw/<category>/<file>]`.
Use lateral links for peer findings: `[findings/<discipline>/<file>]`.

## Implications

What this means for the program direction. Reference the program state
where relevant: `[program-state/decision-log.md]`.

Flag any cross-disciplinary liabilities found. If a liability was
appended to the tracker, reference it here.

## Recommendation

State the recommended course of action based on this finding's evidence.
The recommendation must be a clear, actionable statement that the
program lead can accept, reject, or modify.

### Supporting Evidence

Enumerate the evidence that supports this recommendation. Each entry
must reference a specific artifact and state what it demonstrates:

- [artifact path] — what it shows and how it supports the recommendation

### Assumptions

List every assumption underlying the recommendation. For each:
- What is assumed
- How sensitive the recommendation is to this assumption
- What evidence, if obtained, would invalidate the assumption

### Confidence Limits

- **Overall confidence**: high | moderate | low
- **Scope of applicability**: what conditions must hold for this
  recommendation to apply
- **Known boundaries**: where the evidence does not extend

### Strongest Material Counterargument

State the single strongest reason this recommendation could be wrong.
This is not a hedge or a caveat — it is the best argument against
your own conclusion, stated as strongly as the evidence permits.

If you cannot articulate a material counterargument, state that
explicitly and explain why (e.g., "No credible counterargument
identified because [reason]").

**Causal-language discipline**: Do not upgrade correlational,
expression-level, or descriptor-based signals to causal claims.
Cardiac target expression does not establish hERG inhibition.
A descriptor-based permeability concern does not establish zero
permeability. State what the evidence actually shows at the level
it shows it.

### What Would Change the Recommendation

Name the specific evidence or result that would cause you to reverse
or materially alter this recommendation. This must be concrete and
testable — not "if things are different" but "if [specific measurement]
shows [specific result], then [specific change to recommendation]."

## Open Questions

Unresolved items that may require follow-up from this or another role.
Each question should name what evidence would resolve it.

## Caveats & Confidence

- Model confidence metrics and their interpretation
- Data coverage and resolution limitations
- Assumptions made and their sensitivity
- What this finding cannot speak to
