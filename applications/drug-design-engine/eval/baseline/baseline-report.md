# DDE Evaluation Baseline Report

**Evaluation version**: 1.0
**Run timestamp**: 2026-09-08T15:07:57Z
**Total fixtures**: 8
**Completed**: 8/8

## Aggregate Metrics

| Metric | Value | Denominator | Rate |
|--------|-------|-------------|------|
| fixtures_completed | 8 | 8 | 1.0 |
| cli_success_rate | 4 | 4 | 1.0 |
| cli_failure_rate | 0 | 4 | 0.0 |
| total_state_transitions | 48 | - | - |
| total_relay_codes_fired | 4 | - | - |
| total_artifacts_produced | 8 | - | - |
| total_wall_clock_seconds | 0.0398 | - | - |
| repeated_operations | 0 | 4 | 0.0 |
| unsupported_claims_accepted | N/A | N/A | Requires LLM agent workflow execution with scientific review |
| mistaken_rejections | N/A | N/A | Requires LLM agent workflow execution with review of declined candidates |
| decision_reversals | N/A | N/A | Requires multi-cycle workflow execution tracking |
| expensive_work_avoided | N/A | N/A | Requires cost-instrumented workflow with early-termination tracking |

## Per-Fixture Results

### No genetic support — target lacks GWAS/genetic evidence

- **ID**: EVAL-001
- **Category**: no_genetic_support
- **Completed**: True
- **Wall clock**: 0.009s
- **CLI invocations**: 2 (2 ok, 0 failed)
- **State transitions**: 5
- **Relay codes**: hypothesis.adopted_not_generated
- **Artifacts produced**: 2
- **Repeated operations**: 0

**Validation checks**:

| Check | Result |
|-------|--------|
| deliverables_exist | fail |
| report_headings | pass |
| paths_resolve | pass |
| provenance_valid | pass |
| analysis_citations | pass |
| relay_coverage | pass |
| version_policy | skip |
| findings_integrity | pass |

**Observations**:
- Hypothesis adopted successfully
- Work order WO-001 created in state 'proposed'
- Final work order state: submitted. No genomics artifacts exist — this is the expected baseline behavior for no-genetic-support scenarios.

### Negative pocket conformation — unfavorable druggability

- **ID**: EVAL-002
- **Category**: negative_pocket_conformation
- **Completed**: True
- **Wall clock**: 0.0046s
- **CLI invocations**: 0 (0 ok, 0 failed)
- **State transitions**: 5
- **Relay codes**: fpocket.single_conformation
- **Artifacts produced**: 2
- **Repeated operations**: 0

**Observations**:
- Relay code 'fpocket.single_conformation' present in pocket sidecar
- Pocket druggability score 0.12 (unfavorable). Single conformation relay fired. Work order state: submitted.

### Positive model geometry — favorable structural confidence

- **ID**: EVAL-003
- **Category**: positive_model_geometry
- **Completed**: True
- **Wall clock**: 0.0038s
- **CLI invocations**: 0 (0 ok, 0 failed)
- **State transitions**: 5
- **Relay codes**: none
- **Artifacts produced**: 2
- **Repeated operations**: 0

**Observations**:
- Structure confidence: binding region pLDDT=91.7 (>70 = high confidence)
- Work order state: submitted. Favorable geometry baseline.

### Modality mismatch — antibody target with small-molecule deliverables

- **ID**: EVAL-004
- **Category**: modality_mismatch
- **Completed**: True
- **Wall clock**: 0.0035s
- **CLI invocations**: 0 (0 ok, 0 failed)
- **State transitions**: 5
- **Relay codes**: none
- **Artifacts produced**: 0
- **Repeated operations**: 0

**Observations**:
- Modality mismatch caught: False. Work order state: submitted. Context modality='antibody', deliverables target small-molecule artifact classes (docking, compounds, descriptors). Current workflow has no modality-consistency gate.

### Absent entity inputs — missing required compound and target identifiers

- **ID**: EVAL-005
- **Category**: absent_entity_inputs
- **Completed**: True
- **Wall clock**: 0.0031s
- **CLI invocations**: 0 (0 ok, 0 failed)
- **State transitions**: 4
- **Relay codes**: none
- **Artifacts produced**: 0
- **Repeated operations**: 0

**Observations**:
- Work order accepted with 4 empty entity fields: ['target', 'compound_smiles', 'compound_inchikey', 'pubchem_cid']. Current workflow has no context-content validation. Work order state: in_progress.

### Tool failure — run fails with infrastructure error

- **ID**: EVAL-006
- **Category**: tool_failure
- **Completed**: True
- **Wall clock**: 0.004s
- **CLI invocations**: 0 (0 ok, 0 failed)
- **State transitions**: 9
- **Relay codes**: none
- **Artifacts produced**: 0
- **Repeated operations**: 0

**Observations**:
- Run RUN-006 state: failed. Failure class: transient_infrastructure. Work order state: in_progress. Recovery path available: WO can accept a new run.
- Recovery run RUN-007 created (attempt 2). State machine allows retry after infrastructure failure.

### Disputed citation — finding references retracted paper

- **ID**: EVAL-007
- **Category**: disputed_citation
- **Completed**: True
- **Wall clock**: 0.0056s
- **CLI invocations**: 2 (2 ok, 0 failed)
- **State transitions**: 1
- **Relay codes**: hypothesis.adopted_not_generated, hypothesis.unranked_set
- **Artifacts produced**: 2
- **Repeated operations**: 0

**Observations**:
- Hypothesis with disputed citation adopted successfully
- Analysis completed. Relay codes from adoption should carry forward to analysis.
- Disputed citation scenario: current workflow does not have a retraction-check gate.  The citation passes through unchanged.  Future workflow improvements may add citation verification.

### Bounded-review exhaustion — work order cycles through revision requests

- **ID**: EVAL-008
- **Category**: bounded_review_exhaustion
- **Completed**: True
- **Wall clock**: 0.0062s
- **CLI invocations**: 0 (0 ok, 0 failed)
- **State transitions**: 14
- **Relay codes**: none
- **Artifacts produced**: 0
- **Repeated operations**: 0

**Observations**:
- Completed 3 validation-failure recovery cycles. State machine allows unlimited cycles (no circuit breaker). Final state: submitted. Transitions per cycle: 3 (submitted->validation_failed->in_progress->submitted).

## Regression Criteria

This baseline establishes the following measurable properties of the current workflow. Future workflow changes (tracker #73) must not regress these without explicit justification:

1. **Fixture completion rate**: 8/8 fixtures complete without unexpected errors
2. **CLI reliability**: 4/4 expected CLI invocations succeed
3. **State machine integrity**: All state transitions follow the declared transition graph; illegal transitions produce exit 9
4. **Relay propagation**: Mandatory relays fire when conditions are met and carry forward through analysis
5. **Validation mechanical checks**: Validation catches missing provenance, absent deliverables, and schema violations

## Resource Budget (Evaluation Policy v1.0)

| Resource | Budget | Baseline Actual |
|----------|--------|-----------------|
| Wall clock (all fixtures) | 60s | 0.0398s |
| CLI invocations | 200 | 4 |
| State transitions | 100 | 48 |

## Provenance

All fixtures are synthetic, clearly labeled as such in their definitions.
No real program data or patient data is used.
Fixture provenance is documented in `applications/DDE/eval/fixtures/definitions.py`.

