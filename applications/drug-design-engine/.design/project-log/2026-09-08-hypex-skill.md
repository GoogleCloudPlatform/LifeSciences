# Project Log: hypothesis-run-corpus skill

**Date:** 2026-09-08
**Agent:** dev-hypex-skill
**Branch:** scion/dev-hypex-skill
**Task:** Create the interpretation contract for `dde hypex ingest` / `analyze`

## What was done

Created `skills/hypothesis-run-corpus/SKILL.md` — the interpretation
contract for reading a hypex tournament run result. This is the hypex
analogue of `tournament-corpus` (the co-scientist reading guide).

## Key content

1. **Six verdicts**: `clear-leader`, `leader-with-advisories`,
   `no-clear-leader`, `single-candidate`, `unrankable`, `unconverged`.
   The sixth (`unconverged`) is new relative to `tournament-corpus` and
   covers budget exhaustion or abort.

2. **Nine relay codes**: all `hypex.*` codes from `core/provenance.py`
   (on branch `scion/dev-hypex-commands`), with kind, firing condition,
   and full obligation prose copied from the registered text.

3. **The denominator contrast**: explicitly states that a phantom
   citation rate is a valid computation for hypex (unlike co-scientist),
   why (the manifest carries `summary.total`), and the condition
   (`cite.extraction_incomplete` must be honoured). Pre-empts a reader
   carrying the co-scientist "never compute a rate" rule across.

4. **`leader_gap_is_decisive` tri-state**: `true`/`false`/`null`, with
   day-one always-`null` because `elo_decisive_gap` is UNRESOLVED.

5. **Three UNRESOLVED thresholds**: `elo_decisive_gap`,
   `max_suspect_citations`, `min_safety_score` — `get()` raises, task
   reports blocked. Stated as correct behaviour.

6. **Assessment core**: `dde.hypothesis-assessment.v1` with
   `score.basis: "hypex-elo@1.0"` (differs from co-scientist's
   `"coscientist-elo@1.1"`), cross-strategy comparison in prose only.

7. **Anti-fabrication guard**: 12 named pathologies including both
   directions of the denominator error, `null` vs `false` confusion,
   composite-as-ELO, and the unvalidated epoch loop.

## Modelled on

- `tournament-corpus/SKILL.md` — section structure, verdict format,
  consequence rules, anti-fabrication guard pattern
- `literature-search/SKILL.md` — routing table format
- `citation-verification/SKILL.md` — relay table format

## Verification

- `check_skill_uris.py`: ran, found the new skill. No template declares
  it yet (expected — template wiring is Phase B1 / `dev-hypex-commands`).
- `check_threshold_names.py`: ran clean.
- `check_relay_codes.py`, `check_invocations.py`: could not run (no
  `click` in this environment — missing tools venv).

## Boundaries respected

- Did NOT create `commands/hypex.py` (that is `dev-hypex-commands`).
- Did NOT modify `tournament-corpus/SKILL.md`.
- Did NOT modify any template.
- Single SKILL.md file, no Python code.
