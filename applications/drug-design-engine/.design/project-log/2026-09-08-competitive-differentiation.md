# Project Log: Competitive Differentiation (#37)

**Date**: 2026-09-08
**Agent**: dev-differentiation-37
**Branch**: `scion/dev-differentiation-37`
**Issue**: #37 — Competitive Differentiation Including Hypex

## Summary

Implemented the competitive differentiation tooling required by issue #37.
The implementation produces structured competitive-differentiation findings
that keep three distinct dimensions separate (competitor activity,
patentability/novelty, freedom to operate), never blending them into one score.

## What was built

### 1. New command module: `tools/dde/commands/differentiation.py`

Two subcommands:
- `dde differentiation assess` — reads stored patent search results and
  produces a three-dimension assessment
- `dde differentiation report` — generates `dde.evidence-assessment.v1`
  records from the assessment

Key design decisions:
- Three dimensions are NEVER collapsed into a single score
- Every FTO finding carries a mandatory legal-clearance disclaimer
- Every finding carries search date, scope, and coverage limits
- Crowded competitor activity is NOT treated as an automatic veto
- Charter constraints are recorded as program-specific exclusions,
  distinct from scientific rejections

### 2. New skill: `skills/competitive-differentiation/SKILL.md`

Documents how to interpret competitive landscape results, the three-dimension
model, mandatory disclaimers, evidence type mappings, and integration with
Hypex generation/reflection workflows.

### 3. New relay codes in `provenance.py`

- `differentiation.crowded_landscape` — competitive landscape shows
  significant activity (informational, not a gate)
- `differentiation.not_legal_clearance` — mandatory disclaimer that
  public searches are not formal legal clearance

### 4. Evidence types

Following the naming convention from design doc §1.5:
- `competitive_precedent` — for competitor activity and charter constraints
- `patent_landscape` — for patentability and FTO findings

### 5. CLI registration

`dde differentiation` added to `cli.py` with proper import and add_command.

## Cohort A FTO wording reconciliation

**Found and reconciled a mismatch.** The existing lead template
(`templates/science-program-lead/agents.md`) had two locations discussing
FTO:

1. **Section 9, Cohort A step 3** (line 544): Described the FTO screen but
   lacked:
   - The legal clearance disclaimer
   - The three-dimension separation guidance
   - Reference to `dde differentiation`

2. **Rule 16** (line 707): Referenced `dde patent`, `dde trials`, and
   `dde pubchem` but lacked:
   - The legal clearance disclaimer
   - Reference to `dde differentiation`

**Changes made:**
- Added `dde differentiation` to the tools list in both locations
- Added the FTO disclaimer block to Cohort A step 3
- Added three-dimension separation guidance to Cohort A step 3
- Added disclaimer note to Rule 16
- Wording aligned so upstream (lead template) and downstream (this tooling)
  agree: a public search is not formal legal clearance

**No existing wording was weakened.** The changes add precision (the
disclaimer and three-dimension separation) without weakening the existing
"If step 3 reveals blocking IP with no white space: terminate" decision
rule.

## Tests

25 tests in `tests/test_differentiation.py`, all passing. Tests cover:

1. **Three-dimension separation** — all three dimensions present, marked
   independent, search metadata on each
2. **Strong differentiation AND FTO concern** — both surface independently,
   neither is netted out (the critical test)
3. **Crowded-but-differentiated concept** — competitor density alone doesn't
   produce a rejection; `crowding_is_veto` is explicitly False
4. **Differentiation despite crowding** — modality gap is recognized
5. **Patent-search miss with incomplete coverage** — coverage gap explicitly
   recorded, unpublished applications mentioned
6. **Incomplete jurisdiction coverage** — missing jurisdictions stated
7. **Charter constraint as program constraint** — recorded distinctly from
   scientific rejection
8. **FTO disclaimer mandatory** — present on top-level, FTO dimension,
   assessment records, even with no patents
9. **Assessment record validation** — all records pass
   `validate_assessment()`
10. **Relay codes registered** — new codes in `provenance.RELAY_CODES`

## Existing test suite

No regressions:
- `test_evidence.py`: 49/49 passed
- `test_concepts.py`: 74/74 passed
- `test_policy.py`: passed (exit 0)

## Gates run

- Syntax check: passed (ast.parse)
- All 25 new tests: passed
- Existing tests (evidence, concepts, policy): no regressions
- Full toolchain install not available in this environment (no pip/venv);
  tests run with `PYTHONPATH=tools` against installed `click` only

## Files changed

| File | Change |
|---|---|
| `tools/dde/commands/differentiation.py` | New — competitive differentiation module |
| `tools/dde/core/provenance.py` | Extended — two new relay codes |
| `tools/dde/cli.py` | Extended — import and add_command for differentiation |
| `templates/science-program-lead/agents.md` | Extended — FTO disclaimer and three-dimension guidance in Cohort A and Rule 16 |
| `skills/competitive-differentiation/SKILL.md` | New — interpretation skill |
| `tests/test_differentiation.py` | New — 25 tests |
| `.design/project-log/2026-09-08-competitive-differentiation.md` | New — this entry |
