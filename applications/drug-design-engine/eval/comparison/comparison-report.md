# DDE Evaluation Comparison Report — Baseline vs. Stage 0

**Evaluation version**: 1.0-comparison
**Baseline run**: 2026-09-08T15:07:57Z
**Stage 0 run**: 2026-09-08T18:01:30Z
**Comparison generated**: 2026-09-08T18:01:30Z

## Scope and Limitations

- This comparison covers baseline (pre-Stage 0) vs. Stage 0 bounded triage workflows only.
- Evidence reuse (#77) is P2 and explicitly out of scope for this tracker phase — it has not been implemented and is not measured in this comparison.
- #82's acceptance criteria requests comparison with 'bounded Stage 0 and evidence reuse.'  Only the bounded Stage 0 portion is covered here.  The evidence-reuse comparison will be addressed when #77 is implemented in a future tracker phase.
- This harness exercises the control-plane and CLI layers.  The four LLM-dependent metrics (unsupported_claims_accepted, mistaken_rejections, decision_reversals, expensive_work_avoided) remain N/A in both workflows because this harness does not include a live LLM agent in the loop.  These metrics require full workflow execution with scientific review, which this evaluation harness does not provide.
- Stage 0 workstreams invoke real CLI commands (dde manufacturing assess-stage0, etc.) — no mocked results.

## Aggregate Metrics Comparison

| Metric | Baseline | Stage 0 | Note |
|--------|----------|---------|------|
| fixtures_completed | 8/8 (1.0) | 8/8 (1.0) | No change |
| cli_success_rate | 4/4 (1.0) | 8/8 (1.0) | Delta: +4 |
| cli_failure_rate | 0/4 (0.0) | 0/8 (0.0) | No change |
| total_state_transitions | 48 | 8 | Delta: -40 |
| total_relay_codes_fired | 4 | 1 | Delta: -3 |
| total_artifacts_produced | 8 | 4 | Delta: -4 |
| total_wall_clock_seconds | 0.0398 | 0.0314 | Delta: -0.0084 |
| repeated_operations | 0/4 (0.0) | 0/8 (0.0) | No change |
| unsupported_claims_accepted | N/A — Requires LLM agent workflow execution with scientific review | N/A — Requires LLM agent workflow execution with scientific review | Both N/A — requires LLM agent workflow execution, not measured by this control-plane harness |
| mistaken_rejections | N/A — Requires LLM agent workflow execution with review of declined candidates | N/A — Requires LLM agent workflow execution with review of declined candidates | Both N/A — requires LLM agent workflow execution, not measured by this control-plane harness |
| decision_reversals | N/A — Requires multi-cycle workflow execution tracking | N/A — Requires multi-cycle workflow execution tracking | Both N/A — requires LLM agent workflow execution, not measured by this control-plane harness |
| expensive_work_avoided | N/A — Requires cost-instrumented workflow with early-termination tracking | N/A — Requires cost-instrumented workflow with early-termination tracking | Both N/A — requires LLM agent workflow execution, not measured by this control-plane harness |

## Per-Fixture Comparison

### EVAL-001: No genetic support — target lacks GWAS/genetic evidence

- **Category**: no_genetic_support
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.009s, Stage 0 0.005s
- **Invocations**: baseline 2, Stage 0 1
- **State transitions**: baseline 5, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)
- Different CLI command paths: baseline uses control-plane commands (hypothesis adopt, validate check); Stage 0 uses triage workstream commands (manufacturing assess-stage0)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_yet_applicable, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-001-r1'])

### EVAL-002: Negative pocket conformation — unfavorable druggability

- **Category**: negative_pocket_conformation
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.0046s, Stage 0 0.0039s
- **Invocations**: baseline 0, Stage 0 1
- **State transitions**: baseline 5, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_yet_applicable, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-002-r1'])

### EVAL-003: Positive model geometry — favorable structural confidence

- **Category**: positive_model_geometry
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.0038s, Stage 0 0.0039s
- **Invocations**: baseline 0, Stage 0 1
- **State transitions**: baseline 5, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_yet_applicable, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-003-r1'])

### EVAL-004: Modality mismatch — antibody target with small-molecule deliverables

- **Category**: modality_mismatch
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.0035s, Stage 0 0.0036s
- **Invocations**: baseline 0, Stage 0 1
- **State transitions**: baseline 5, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_yet_applicable, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-004-r1'])

### EVAL-005: Absent entity inputs — missing required compound and target identifiers

- **Category**: absent_entity_inputs
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.0031s, Stage 0 0.0035s
- **Invocations**: baseline 0, Stage 0 1
- **State transitions**: baseline 4, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_yet_applicable, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-005-r1'])

### EVAL-006: Tool failure — run fails with infrastructure error

- **Category**: tool_failure
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.004s, Stage 0 0.0038s
- **Invocations**: baseline 0, Stage 0 1
- **State transitions**: baseline 9, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_yet_applicable, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-006-r1'])

### EVAL-007: Disputed citation — finding references retracted paper

- **Category**: disputed_citation
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.0056s, Stage 0 0.0039s
- **Invocations**: baseline 2, Stage 0 1
- **State transitions**: baseline 1, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)
- Different CLI command paths: baseline uses control-plane commands (hypothesis adopt, validate check); Stage 0 uses triage workstream commands (manufacturing assess-stage0)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_yet_applicable, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-007-r1'])

### EVAL-008: Bounded-review exhaustion — work order cycles through revision requests

- **Category**: bounded_review_exhaustion
- **Completed**: baseline=True, Stage 0=True
- **Wall clock**: baseline 0.0062s, Stage 0 0.0038s
- **Invocations**: baseline 0, Stage 0 1
- **State transitions**: baseline 14, Stage 0 1

**Behavioral differences**:
- Stage 0 adds triage evaluation with manufacturing feasibility assessment (not present in baseline)

**Stage 0 observations**:
- [manufacturing] evidence_status=not_assessed, execution_outcome=unknown
- Stage 0 disposition: (pending lead review)
- Shortlisted: True (shortlist: ['IC-EVAL-008-r1'])

## Declined Candidate Follow-up

The following fixtures represent concepts likely to be declined in a real workflow.  This comparison checks whether Stage 0's handling of these candidates differs from the baseline's, exposing potential selection bias.

### EVAL-001

**Stage 0 disposition**: (pending lead review)
**Evidence statuses**: not_yet_applicable

**Comparison**: Baseline: work order reaches submitted state; validation fails on deliverables_exist because no genomics artifacts exist.  Stage 0: concept is evaluated through triage (disposition: (pending lead review)).  Stage 0 evaluates the concept's manufacturing feasibility rather than checking for pre-existing artifacts — a structurally different assessment path that does not auto-terminate the concept for lacking genetic evidence.

### EVAL-002

**Stage 0 disposition**: (pending lead review)
**Evidence statuses**: not_yet_applicable

**Comparison**: Baseline: pocket analysis artifact has unfavorable metrics (druggability score 0.12); fpocket.single_conformation relay fires.  Stage 0: concept is evaluated through triage (disposition: (pending lead review)).  Stage 0 assesses manufacturing feasibility independently of the pocket druggability findings — the unfavorable pocket score does not auto-terminate the concept, consistent with the no-automatic-veto design principle.

## Resource Budget — Stage 0 Characteristics

Stage 0 dispatches workstreams per concept, subject to budget controls.  The following characteristics were observed during this comparison run — recorded as versioned policy per #82 acceptance criteria, not as assumed constants.

| Characteristic | Value |
|----------------|-------|
| total_wall_clock_seconds | 0.0314 |
| total_workstream_invocations | 8 |
| total_state_transitions | 8 |
| workstreams_per_concept | 1 (manufacturing) — differentiation and structure screening require additional configuration not present in the evaluation fixtures |
| budget_controls_available | max_wall_clock_seconds, max_concepts, max_workstream_invocations (all unbounded in this run) |

## Regression Criteria

Stage 0 must not regress the baseline properties established in Phase 1:

- All evaluation fixtures complete without unexpected errors
- Stage 0 workstreams invoke real CLI commands, not mocks

## Provenance

All fixtures are synthetic, clearly labeled as such in their definitions.  No real program data or patient data is used.
Fixture provenance is documented in `applications/DDE/eval/fixtures/definitions.py`.
Baseline data is from `applications/DDE/eval/baseline/baseline-report.json` (Phase 1 output, frozen — not regenerated or modified).

