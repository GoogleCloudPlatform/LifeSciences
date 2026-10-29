---
name: cost-estimation
description: Job cost forecasting, on-demand vs DWS spot pricing comparison, and spending analysis
metadata:
  adk_additional_tools:
    - estimate_job_cost
    - estimate_monthly_cost
    - get_actual_job_costs
---

# Cost Estimation

- **Per-job cost**: Use estimate_job_cost to show users the expected cost before submitting a prediction or binder design job
  - Supports `af2_monomer`, `af2_multimer`, `of3`, `boltz2`, `boltzgen`, `rfd4_proteina`, and `bindcraft` job types
  - Returns per-phase breakdown (MSA, predict, relax) with both **on-demand** and **DWS FLEX_START** (spot) pricing side by side
  - GPU auto-selection matches the submission tools (L4 for small proteins, A100 for larger)
  - **IMPORTANT**: Always present both pricing columns so users can compare. FoldRun defaults to FLEX_START.
  - **Proactive use**: When showing the pre-submission confirmation table, include cost estimate
- **Monthly projection**: Use estimate_monthly_cost for budget planning and TCO analysis
  - Input monthly job volumes by type (AF2 monomer, multimer, OF3, Boltz-2, BoltzGen, RFD4-Proteina, BindCraft)
  - Returns compute + infrastructure + other costs with monthly and annual totals for both pricing modes
  - Infrastructure includes: Filestore, GCS, Artifact Registry, Agent Runtime, Cloud Run, VPC/NAT
  - Use include_infrastructure=False to see compute-only costs
- **Actual costs**: Use get_actual_job_costs to retrieve costs for completed prediction jobs
  - Calculates costs from actual Agent Platform job runtimes and machine specs
  - Groups costs by pipeline run with per-phase breakdown (MSA, predict, relax)
  - Shows estimate vs actual comparison with accuracy percentage
  - Provide a pipeline_job_id for a specific run, or omit to see all recent jobs
  - Use this when users ask "how much did that job cost?" or "show me my spending"
- **IMPORTANT disclaimer**: These tools return estimates based on Google Cloud **list pricing**. Always remind users that actual costs depend on their organization's pricing agreement with Google Cloud (negotiated rates, CUDs, enterprise discounts). The disclaimer is included in every tool response — surface it to the user.
