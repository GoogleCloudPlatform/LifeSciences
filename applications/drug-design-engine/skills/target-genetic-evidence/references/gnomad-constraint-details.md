# gnomAD constraint — detailed interpretation notes

Referenced from the main `SKILL.md` for detailed gnomAD-specific
interpretation. The main skill covers all four genetic evidence sources
at consistent depth; this file carries the detailed gnomAD discussion
that overflows that scope.

## The split-verdict problem

The overall gnomAD constraint verdict derives from two independent
metrics that **can disagree**:

- **verdict_by_pli** — intolerant | tolerant | indeterminate. pLI at
  or above the configured `lof_intolerant_pli` threshold is intolerant;
  pLI below `pli_indeterminate_floor` is tolerant; between them is
  indeterminate (gnomAD documents this range as "too small to
  categorise").
- **verdict_by_loeuf** — intolerant | tolerant. LOEUF below the
  configured `loeuf_constrained` threshold is intolerant; at or above
  is tolerant.

When they disagree, `constraint_unreliable` fires, and the finding must
quote **both** pLI and LOEUF with the 90% confidence interval rather
than picking the metric that agrees with the hypothesis.

Example: a gene may be intolerant by pLI (e.g. 0.95) yet tolerant by
LOEUF (e.g. 0.48) in the same gnomAD release. Reporting only pLI would
make it appear clearly constrained; reporting only LOEUF would make it
appear tolerant. The gene sits at the boundary rather than in either
class. (Illustrative values; real metrics vary by release.)

## The v2/v4 LOEUF cutoff hazard

The widely-quoted LOEUF cutoff of 0.35 is the gnomAD **v2** figure.
gnomAD's own documentation states that o/e values are higher in v4 and
that "any LOEUF thresholds used on v2 will not give an equivalent number
of genes when applied to v4." The tool's default is the v4 figure from
gnomAD's published guidance. Do not import 0.35 from the literature —
carrying it forward would silently reclassify genes near the boundary.

## Unresolved threshold detail

`loeuf_unreliable_min_expected_lof` is **UNRESOLVED** — gnomAD
publishes no minimum expected-LoF count below which LOEUF should be
distrusted. The analysis reports `exp_lof` and the full 90% CI and
declines to threshold on them. Do not invent a floor.
