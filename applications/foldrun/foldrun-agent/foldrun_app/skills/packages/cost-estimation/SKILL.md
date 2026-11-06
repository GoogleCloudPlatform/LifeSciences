---
name: cost-estimation
description: Forecasts per-job and monthly GCP compute and infrastructure costs across on-demand and DWS FLEX_START spot pricing, and calculates actual spend for completed jobs. Use when the user asks how much a prediction or monthly workload will cost, asks about past job spend, or during pre-submission confirmation.
metadata:
  adk_additional_tools:
    - estimate_job_cost
    - estimate_monthly_cost
    - get_actual_job_costs
---

# Cost Estimation

- **Per-Job Cost Forecast (`estimate_job_cost`)**: Estimates compute cost before submitting a prediction (`af2_monomer`, `af2_multimer`, `of3`, or `boltz2`).
  - Returns per-phase breakdown (MSA, predict, relax) with both **on-demand** and **DWS FLEX_START** (spot) pricing side by side.
  - GPU auto-selection matches the submission tools (`A100` 40GB default, `A100_80GB` for large sequences, or explicit `L4` override).
  - Always present both pricing columns so users can compare (FoldRun defaults to `DWS FLEX_START`).
- **Monthly Budget Projection (`estimate_monthly_cost`)**: Models monthly and annual TCO from expected job volumes plus shared infrastructure (Filestore, GCS, Artifact Registry, Agent Runtime, Cloud Run, VPC/NAT; pass `include_infrastructure=False` for compute-only).
- **Actual Job Spend (`get_actual_job_costs`)**: Calculates actual costs from completed PipelineJob task runtimes and machine specs, including estimate-vs-actual accuracy. Pass `pipeline_job_id` for a single run or omit for recent jobs.
- **Pricing Disclaimer**: Always remind users that estimates use Google Cloud **list pricing** and actual spend depends on their organization's negotiated rates, CUDs, or enterprise discounts.
