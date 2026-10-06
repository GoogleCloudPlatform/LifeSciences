# Issue #23: Progressive Manufacturing Assessment

**Date:** 2026-09-08
**Branch:** `scion/dev-manufacturing-23`
**Author:** dev-manufacturing-23

## Summary

Implemented Stage 0 progressive manufacturing assessment for the DDE
toolkit.  The assessment evaluates production-platform fit for
intervention concepts based on modality, delivery assumptions, and
entity reference (SMILES, sequence, or construct).

## Inventory of Existing Capabilities

Before building the manufacturing assessment, an inventory of existing
compound.py capabilities was performed to understand what is already
available and to avoid duplication.

### `compound sa-score` (Phase 1)
- Computes Ertl & Schuffenhauer SA-score (2009) via `rdkit.Contrib.SA_Score.sascorer`
- Outputs raw SA-score (1-10, 1=easy) to `raw/compounds/<slug>.sa-score.json`
- **Critical distinction**: SA-score is a fragment-frequency heuristic —
  it rates how common the molecule's substructures are in known compounds.
  It is NOT synthesizability and NOT a synthesis route.

### `compound descriptors` (Phase 1)
- Computes molecular descriptors (MW, LogP, HBD, HBA, TPSA, rotatable bonds)
- Phase 1 only — no judgment applied

### `compound alerts` (Phase 1)
- Runs PAINS and other structural alert filters
- Phase 1 only — flags presence of known interference patterns

### `compound analyze` (Phase 2)
- Reads stored descriptors + alerts + SA-score
- Applies Lipinski/Veber thresholds
- Phase 2 judgment layer — depends on Phase 1 outputs existing

### Manufacturing implications
- SA-score exists and is reusable; the manufacturing assessment
  **incorporates** existing SA-score data rather than re-computing it
- The manufacturing assessment explicitly preserves the SA-score ≠
  synthesizability distinction in every finding
- Complexity heuristics (stereocenters, rings, step estimates) are
  NEW capabilities — they complement but do not duplicate SA-score

## What Was Built

### Core Assessment Logic (`tools/dde/core/manufacturing.py`)
- `assess_stage0()`: produces `dde.evidence-assessment.v1` records
- `compute_complexity_heuristics()`: stereocenter/ring/step analysis
- `STAGE_REQUIREMENTS`: progressive stage definitions (0=concrete, 2-4=placeholders)
- `PRODUCTION_PLATFORMS`: 9 modality-to-platform mappings
- `SA_SCORE_MODALITIES`: chemical synthesis modalities only

### CLI Commands (`tools/dde/commands/manufacturing.py`)
- `dde manufacturing assess-stage0`: assess concept manufacturing feasibility
- `dde manufacturing stage-requirements`: print stage structure

### Skill (`skills/manufacturing-feasibility/SKILL.md`)
- Interpretation contract documenting SA-score distinction, stereocenter
  handling, pre-entity semantics, no-fabrication guard, and failure modes

### Tests (`tests/test_manufacturing.py`)
- 35 tests covering: inventory, small-molecule, biologic, no-entity,
  stereocenters, qualitative findings, supersedes chain, stage requirements,
  production platforms, complexity heuristics, schema validation, SA-score
  scoping, GMP disclaimer, evidence types, delivery assumptions

## Three Demonstration Cases

1. **Small-molecule with structure**: entity_ref (SMILES) present,
   SA-score incorporated → `supported` with SA-score distinction preserved
   and complexity heuristics computed
2. **Biologic**: modality-level qualitative assessment → `supported` with
   explicit limitations (no fabricated developability data), owner and
   next_evidence specified
3. **Concept without entity_ref**: `not_yet_applicable` (per #75 evidence
   semantics) — not a failure, not fabrication; trigger documented for when
   entity_ref is populated

## Key Design Decisions

- **SA-score ≠ synthesizability**: distinction enforced in every finding
  that references SA-score; the interpretation contract in the skill makes
  this explicit
- **Stereocenter/step counts as `prioritization_heuristic`**: never
  `scientific_cutoff`; a molecule with many stereocenters is flagged, not
  rejected (Taxol = 11 stereocenters, manufactured at scale)
- **No fabrication**: no yield, cost of goods, stability, or formulation
  properties invented; qualitative findings cite precedent + assumptions +
  limitations + owner + next evidence
- **Core/command separation**: assessment logic in `core/manufacturing.py`
  (no click dependency), CLI in `commands/manufacturing.py` — matches
  project pattern and enables testing without click
- **`supersedes` chain**: supports #78 reviewed reassessment via existing
  evidence schema field

## Verification

| Gate | Result | Notes |
|---|---|---|
| Manufacturing tests | 35/35 pass | Standalone runner, no click needed |
| Evidence tests | 49/49 pass | No regressions |
| Concept tests | 74/74 pass | No regressions |
| Policy tests | pass | No regressions |
| Eval metrics tests | 26/26 pass | No regressions |
| Click-dependent tests | not run | Environment lacks click — pre-existing constraint, not a regression |
