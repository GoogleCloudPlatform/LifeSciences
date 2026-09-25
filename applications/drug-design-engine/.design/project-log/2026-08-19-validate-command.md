# Project Log: `venter validate check` command (Issue #22, Phase 2)

**Date:** 2026-08-19
**Author:** tools-lead-em-22p2-dev-1
**Scope:** `tools/venter/commands/validate.py`, `tools/venter/cli.py`, `tools/check_invocations.py`

## Summary

Implemented the `venter validate` command group with a `check` subcommand that runs 8 mechanical validation checks against a submitted work order's deliverables. This is Phase 2 of the control plane CLI (issue #22).

## What was built

### Command: `venter validate check <work-order-id> [--revision N] [--json] [--quiet]`

Runs 8 checks, each producing a named pass/fail/skip result:

1. **deliverables_exist** — verifies Layer 1 files and Layer 0 artifact directories contain artifacts
2. **report_headings** — verifies Layer 1 findings contain the WO reference string (e.g. `WO-001-r1`)
3. **paths_resolve** — verifies internal markdown links resolve within the project root
4. **provenance_valid** — verifies `.meta.json` sidecars exist and sha256 checksums match
5. **analysis_citations** — verifies `.analysis.json` records have `source` and `threshold_set`
6. **relay_coverage** — checks mandatory relay codes appear in Layer 1 findings (substring match)
7. **version_policy** — skips gracefully when `program.yaml` absent (deferred to a future program.yaml configuration issue)
8. **findings_integrity** — flags `.meta.json`/`.analysis.json` files misplaced under `findings/`

### State machine integration

- Pass → WO transitions to `mechanically_validated`
- Fail → WO transitions to `validation_failed`
- Validation record written to `.venter/control/validations/{id}-r{revision}.json`
- `validation.completed` event appended to `events.ndjson`

### Security

Path confinement applied to every user-controllable path (deliverable paths, markdown link targets) — resolved and verified `is_relative_to(project.root.resolve())` before any file access.

## Patterns followed

- Click group with `VenterGroup`, `pass_state`, `output_options`, `Emitter` — matching `workorder.py` and `run.py`
- `controlstore.read_record()` / `write_record()` / `append_event()` / `list_records()`
- `statemachine.validate_transition()` for state changes
- `provenance.read_json()`, `provenance.sha256_file()`, `provenance.RELAY_CODES`
- `context.ARTIFACT_DIRS` for Layer 0 directory resolution

## Verification

- Syntax checks: all 3 modified files pass `ast.parse()`
- CLI registration: `validate` group with `check` subcommand confirmed via import
- `check_invocations.py`: passes with 0 problems, `validate` removed from allowlist
- End-to-end tests: passing validation, failing validation, path confinement, findings integrity all verified against test projects
