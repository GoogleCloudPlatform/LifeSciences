# Comparison Report — Baseline vs. Stage 0 Workflow (#82)

**Date**: 2026-09-08
**Issue**: #82 (Evaluation Harness and Comparison Report)
**Phase**: Comparison (final phase of P1 scope)
**Branch**: `scion/dev-comparison-82`

## Summary

Implemented the comparison-report portion of issue #82, extending the existing
evaluation harness (Phase 1 baseline) to exercise the Stage 0 bounded triage
workflow (#22) against the same 8 fixtures, and producing a side-by-side
comparison report.

## What Was Built

1. **`eval/stage0_harness.py`** — Stage 0 harness that runs all 8 existing
   fixtures through the real `run_triage()` function, which internally invokes
   real CLI workstream commands (e.g., `dde manufacturing assess-stage0`) via
   `CliRunner`.  Each fixture is converted from its work-order context into a
   Stage 0 intervention concept record (`fixture_to_concept()`), then evaluated
   through the triage workflow.

2. **`eval/comparison.py`** — Comparison report generator that reads the frozen
   Phase 1 baseline (`eval/baseline/baseline-report.json`) and the new Stage 0
   results, producing a side-by-side comparison with metric deltas, per-fixture
   behavioral differences, declined-candidate follow-up, and scope
   documentation.

3. **`eval/run_comparison.py`** — Entry point for the comparison evaluation
   (`PYTHONPATH=tools python3 -m eval.run_comparison`), producing:
   - `eval/comparison/comparison-report.json` — full machine-readable report
   - `eval/comparison/comparison-report.md` — human-readable summary
   - `eval/comparison/stage0-report.json` — Stage 0 run results
   - `eval/comparison/run-manifest.json` — reproduction manifest

4. **`tests/test_comparison.py`** — 18 tests covering fixture-to-concept
   conversion, Stage 0 harness execution, baseline integrity, comparison
   generation, N/A-metric honesty, declined-candidate follow-up, CliRunner
   end-to-end invocation, report serialization, and function reachability.

## Scope and Limitations

### #77 Evidence Reuse — Out of Scope

Issue #82's acceptance criteria states: "Compare current workflow with bounded
Stage 0 **and evidence reuse**."  Evidence reuse is issue #77, which is **P2
and explicitly out of scope** for this tracker phase — it has not been
implemented.  This comparison covers **baseline vs. Stage 0 bounded triage
only**.  The evidence-reuse comparison will be addressed when #77 is
implemented in a future tracker phase.

### N/A Metrics

Four metrics remain N/A in both baseline and Stage 0 reports:

| Metric | Reason N/A |
|--------|-----------|
| `unsupported_claims_accepted` | Requires LLM agent workflow execution with scientific review |
| `mistaken_rejections` | Requires LLM agent workflow execution with review of declined candidates |
| `decision_reversals` | Requires multi-cycle workflow execution tracking |
| `expensive_work_avoided` | Requires cost-instrumented workflow with early-termination tracking |

These metrics require a live LLM agent in the loop making scientific decisions.
This evaluation harness exercises the control-plane and CLI layers only — it
does not drive a full LLM-mediated review workflow.  When a full-stack
evaluation harness is available, these metrics can be measured.

### Manufacturing Workstream CLI Errors

The Stage 0 manufacturing workstream invokes the real `dde manufacturing
assess-stage0` CLI command.  In the evaluation environment, this command
exits with code 2 because the "manufacturing" artifact class is not
registered in `ARTIFACT_DIRS` (the command computes the assessment
successfully but fails when writing output to the project's artifact
directory).  This is faithfully recorded in the comparison report as an
observation — no fabricated assessment data is substituted.

This is consistent with the existing triage test suite (`test_triage.py`),
which also accepts manufacturing CLI errors as valid behavior in test
environments where the full project infrastructure is not available.

## Key Comparison Findings

1. **Fixture completion**: Both baseline and Stage 0 complete all 8/8 fixtures
   without unexpected errors.

2. **Assessment approach**: The baseline uses control-plane operations
   (hypothesis adopt, validate check, state machine transitions). Stage 0 uses
   triage workstream commands (manufacturing assess-stage0). These are
   structurally different assessment paths measuring different aspects of the
   workflow.

3. **Declined candidates (EVAL-001, EVAL-002)**:
   - EVAL-001 (no genetic support): Baseline validates and detects missing
     deliverables. Stage 0 evaluates through triage — does not auto-terminate
     for lacking genetic evidence.
   - EVAL-002 (negative pocket): Baseline records unfavorable druggability
     metrics and relays. Stage 0 evaluates through triage — the unfavorable
     pocket score does not auto-terminate the concept, consistent with the
     no-automatic-veto design principle.

4. **No fabricated numbers**: Every metric in the comparison traces to an
   actual measurement. The four LLM-dependent metrics are honestly marked N/A.

## Verification

- **Comparison tests**: 18/18 passed
- **Existing eval metrics tests**: 26/26 passed (no regressions)
- **Existing triage tests**: 63/63 passed (no regressions)
- **Baseline artifacts**: SHA-256 checksums verified unchanged after Stage 0
  harness execution
- **Function reachability**: All public functions verified reachable from real
  entry points (run_comparison.py or test_comparison.py)

## Files Changed

- `eval/stage0_harness.py` (new)
- `eval/comparison.py` (new)
- `eval/run_comparison.py` (new)
- `eval/comparison/comparison-report.json` (generated)
- `eval/comparison/comparison-report.md` (generated)
- `eval/comparison/stage0-report.json` (generated)
- `eval/comparison/run-manifest.json` (generated)
- `tests/test_comparison.py` (new)
- `.design/project-log/2026-09-08-comparison-report-82.md` (this file)

## Files NOT Changed

- `eval/harness.py` — existing baseline harness (read-only import of helpers)
- `eval/fixtures/definitions.py` — existing fixture definitions (unchanged)
- `eval/metrics.py` — existing metrics module (unchanged)
- `eval/baseline/*` — Phase 1 frozen output (read-only, checksums verified)

## Addendum: Re-run After Manufacturing ARTIFACT_DIRS Fix (2026-09-08)

The initial comparison run (commit `89b0a23`) was affected by a pre-existing
bug in `tools/dde/core/context.py`: the `"manufacturing"` key was missing from
`ARTIFACT_DIRS`, causing `dde manufacturing assess-stage0` to exit with code 2
("unknown artifact class 'manufacturing'") on every invocation.  This meant the
initial Stage 0 report showed `cli_success_rate: 0/8` for manufacturing — an
artifact of the bug, not a reflection of Stage 0's actual behavior.

The bug was independently fixed and merged to the DDE branch at commit
`19eb355` (one-line addition: `"manufacturing": "raw/manufacturing"` in
`ARTIFACT_DIRS`).  See issue #23 for the full history.

After rebasing `scion/dev-comparison-82` onto the fix, the comparison harness
was re-run.  The corrected results:

| Metric (changed) | Before fix | After fix |
|-------------------|-----------|-----------|
| cli_success_rate (Stage 0) | 0/8 (0.0) | 8/8 (1.0) |
| cli_failure_rate (Stage 0) | 8/8 (1.0) | 0/8 (0.0) |
| Manufacturing observations | CLI error messages | Real assessments (evidence_status=not_yet_applicable / not_assessed) |

The manufacturing workstream now completes successfully for all 8 fixtures,
producing genuine `not_yet_applicable` (7 fixtures) and `not_assessed` (EVAL-008)
evidence statuses instead of CLI errors.  All other metrics remain unchanged
from the initial run.

No harness code was changed — only the generated comparison artifacts were
regenerated against the fixed underlying code.

### Re-run Verification

- **Comparison tests**: 18/18 passed
- **Manufacturing tests**: 37/37 passed (includes new regression test from fix commit)
- **Eval metrics tests**: 26/26 passed (no regressions)
- **Triage tests**: 63/63 passed (no regressions)
- **Total**: 144/144 tests passing
- **Baseline artifacts**: SHA-256 checksums verified unchanged
