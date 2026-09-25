# `venter program resume` command (#78)

**Date:** 2026-08-19
**Author:** tools-lead-em-78-dev-program-resume-1
**Issue:** #78

## What was built

New CLI command `venter program resume <source>` that imports accepted
work-order records (and their associated runs, contexts, validations)
from a prior phase's control plane into the current project's control
plane.  This solves the Phase 1 to Phase 2 state continuity problem.

### Files changed

- **`tools/venter/commands/program.py`** (new) — Click command group
  `program` with a `resume` subcommand.
- **`tools/venter/cli.py`** — Added import and registration of the
  `program` command group.

### Behavior

- Accepts a source path (project root or `.venter/control/` directly).
- Reads all work-order records from the source.
- Only imports work orders in `scientifically_accepted` state; all other
  states are skipped with a clear report.
- For each accepted work order, also imports associated contexts, runs,
  and validations (matched by `work_order_id`).  Leases are never imported.
- Preserves original IDs (never renumbers).
- Refuses with exit 9 on ID conflict (handles idempotency: a second run
  against the same source refuses cleanly).
- Adds `imported_from` field to each imported record.
- Logs a `program.resumed` event in `events.ndjson`.
- Reports imported/skipped work orders to stdout.
- Supports `--json` and `--quiet` output options.

### Verification

- End-to-end test verified: 2 accepted WOs imported, 1 proposed WO
  skipped, associated records imported, `next_id()` continues correctly,
  re-run refuses with exit 9, event log contains `program.resumed`.
- `check_invocations.py` passes cleanly.
- `check_artifact_paths.py` and `check_threshold_names.py` have
  pre-existing failures unrelated to this change.
