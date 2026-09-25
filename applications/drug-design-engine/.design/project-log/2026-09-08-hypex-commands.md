# Hypex Command Implementation (B1 CLI surface)

**Date:** 2026-09-08
**Agent:** dev-hypex-commands
**Branch:** scion/dev-hypex-commands

## Summary

Implemented the `dde hypex` command group with `ingest` and `analyze`
subcommands, declared the `hypex@1.0` threshold set, registered 9
`hypex.*` relay codes, wired the command into the CLI, and wrote tests.

## Deliverables

1. **`commands/hypex.py`** — new file with `ingest` (phase 1) and `analyze`
   (phase 2) subcommands following the coscientist two-phase pattern.

2. **`core/thresholds.py`** — `_declare("hypex", "1.0", ...)` with 6 values:
   3 resolved (`min_matches=5`, `min_win_rate=0.5`, `max_phantom_citations=0`)
   and 3 UNRESOLVED (`elo_decisive_gap`, `max_suspect_citations`,
   `min_safety_score`).

3. **`core/provenance.py`** — 9 `hypex.*` relay codes added to `RELAY_CODES`
   in alphabetical order:
   - `hypex.citation_manifest_absent` (Qualifier)
   - `hypex.composite_ranking` (Qualifier)
   - `hypex.integrity_violations` (Defect)
   - `hypex.pacing_uncoordinated` (Defect)
   - `hypex.phantom_citations_present` (Defect)
   - `hypex.quarantined_excluded` (Qualifier)
   - `hypex.run_aborted` (Defect)
   - `hypex.run_not_converged` (Qualifier)
   - `hypex.unrated_hypotheses` (Qualifier)

4. **`cli.py`** — `add_command(hypex)` before `enforce_phase_two`.

5. **`check_invocations.py`** — removed `hypex` from
   `PLANNED_BUT_UNIMPLEMENTED` since the command now exists.

6. **`tests/test_hypex.py`** — 24 tests covering:
   - Relay code registration (all 9 codes + alphabetical order)
   - Threshold set declaration (resolved + UNRESOLVED values)
   - Happy path ingest (valid run dir -> dde.hypex.v1)
   - Observed counts from datastore (not run.yaml)
   - Dangling match refs (integrity violations)
   - Aborted run (missing termination.json)
   - Unconverged verdict + relay
   - leader_gap_is_decisive = null (UNRESOLVED gap)
   - Integrity violations relay
   - Quarantined excluded relay
   - Pacing uncoordinated (missing and bad tier)
   - Assessment core score as {value, basis} object
   - Unrated hypothesis score = null
   - Analysis threshold_set tag
   - Analysis unresolved thresholds recorded
   - Schema gate on analyze
   - Conditional relay guards (criterion 27)
   - Unrated hypotheses relay

## Verification

- All 24 new tests pass
- `check_relay_codes.py` passes — all 92 codes registered and emitted
- `check_threshold_names.py` — no new problems
- `check_invocations.py` — no new problems
- `check_artifact_paths.py` — no new problems
- Existing test suites (`test_coscientist.py`, `test_hypothesis.py`,
  `test_cite.py`, etc.) pass unchanged

## Design decisions

- `ingest` archives the run directory as `.tar.zst` alongside the
  normalised artifact, following the coscientist pattern of preserving
  original bytes.
- `analyze` reads `meta/pacing.json` from the original run dir path
  to fire the `pacing_uncoordinated` relay at analysis time.
- `leader_gap_is_decisive` is tri-state: `true`, `false`, or `null`.
  When `elo_decisive_gap` is UNRESOLVED, it is `null` — not a fallback
  to 50.0.
- UNRESOLVED thresholds are recorded in the analysis output's
  `thresholds_unresolved` field.
