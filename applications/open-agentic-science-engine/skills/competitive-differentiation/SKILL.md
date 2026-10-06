# Competitive Differentiation Assessment

## Purpose

Interpret competitive landscape and patent search results for a concept,
producing structured findings that separate three independent dimensions.
This skill applies after `dde patent search` has been run and patent data
is available.

## When to use

- Before committing to Cohort B characterization (per Rule 16 in the lead
  template: "Screen for competitive landscape and FTO before structural work.")
- When assessing competitive positioning of a tournament-recommended idea.
- When Hypex generation/reflection identifies a target that may have
  existing competitor activity.

## The three dimensions

These three dimensions **must never be collapsed into a single score**.
Each is reported separately, with its own search metadata:

### 1. Competitor activity

What programs, compounds, publications, and patent filings exist for this
target, modality, or indication combination.

- **Not a veto.** A crowded field with a genuinely differentiated angle
  should surface as "differentiated despite crowding", not be rejected
  by a naive "many competitors = bad" heuristic.
- **Record**: density (uncrowded / low_activity / moderate_activity /
  high_activity), top assignees, recent patent count.

### 2. Patentability / novelty

Is there white space for new intellectual property?

- **Assessed by overlap.** How many existing patents cover the same
  modality + indication combination?
- **Record**: novelty_assessment (high_novelty / potential_novelty /
  partial_overlap / crowded_space).

### 3. Freedom to operate (FTO)

Can the concept be pursued without infringing existing intellectual property?

- **Never implies legal clearance.** Every FTO finding carries the
  mandatory disclaimer (see below).
- **Record**: risk_level (no_recent_filings / low_risk / moderate_risk /
  high_risk), jurisdictions found, unresolved questions.

## Mandatory disclaimers

### FTO disclaimer (NEVER omit)

> This assessment is based on public patent database searches and publicly
> available information. It is NOT formal legal clearance. A public search
> or structural similarity analysis cannot substitute for a formal
> freedom-to-operate opinion by qualified patent counsel. Material FTO
> conclusions require qualified legal review.

This disclaimer must appear on every FTO-related finding, assessment
record, and analysis output. It is not optional prose.

### Coverage limits (NEVER omit)

Every search finding must state:

1. **What was searched** (search scope) — e.g., "Google Patents public
   search for 'CDK4 inhibitor'".
2. **What was NOT searched** (coverage limits) — e.g., "Non-English
   filings may be underrepresented; unpublished applications within
   18-month window are not visible; patent databases other than Google
   Patents were not queried."
3. **The search date** — results reflect the database state at this date.

No patent search finding may be presented as exhaustive.

## Mapping to assessment records

The differentiation assessment produces `dde.evidence-assessment.v1`
records with the following evidence types:

| Dimension | evidence_type | Rationale |
|---|---|---|
| Competitor activity | `competitive_precedent` | Describes what exists in the competitive landscape |
| Patentability | `patent_landscape` | Describes the IP landscape and novelty potential |
| FTO | `patent_landscape` | Describes the freedom-to-operate risk |
| Charter constraint | `competitive_precedent` | Program-specific constraints from the charter |

### Evidence type naming convention

Following the design doc (SS1.5): lowercase, underscored, descriptive of
the evidence domain rather than the specific tool.

- `competitive_precedent` — ligand, program, or patent precedent in
  the competitive landscape.
- `patent_landscape` — patent filing landscape relevant to IP position.

## Workflow

```
1. Run `dde patent search "<target>"` to fetch patent data.
2. Run `dde differentiation assess <target> --concept IC-NNN
       --modality <modality> --indication <indication>`.
3. Review the three-dimension output.
4. Run `dde differentiation report <target> --concept IC-NNN`
   to generate assessment records.
5. Write the assessment records to the control store using
   `controlstore.write_record()`.
```

## Integration with Hypex

When Hypex generation or reflection produces hypotheses about a target:

1. The **generation agent** should use `dde patent search` as part of
   its Phase 1 broad exploration to check for existing IP.
2. The **reflection agent** should check patent landscape as part of
   its Phase 2c prior art search.
3. The competitive differentiation assessment complements Hypex's
   scientific evaluation — Hypex evaluates scientific merit, this skill
   evaluates competitive positioning.

The two are complementary and should not duplicate each other.

## Charter constraints

When the charter specifies program-level constraints (e.g., "no oral
formulations" or "oncology indications only"), these are recorded as
separate assessment records with `evidence_type: competitive_precedent`
and a rationale that explicitly identifies them as program constraints
rather than scientific rejections.

A charter constraint is a **program-specific exclusion**, distinct from:
- A scientific rejection (evidence contradicts the hypothesis)
- A competitive rejection (too many competitors)
- An FTO concern (blocking IP exists)

## Reconciliation with Cohort A FTO wording

The lead template (science-program-lead/agents.md, section 9) describes
Cohort A step 3: "Competitive landscape / FTO screen." The new tooling's
disclaimers must match the lead template's standard:

- The lead template's "If step 3 reveals blocking IP with no white space:
  **terminate**" is a decision by the lead, not an automated gate.
- The differentiation tool surfaces information; it does not make the
  terminate/proceed decision.
- Both upstream (lead template) and downstream (this skill) agree:
  a public search is not formal legal clearance.
