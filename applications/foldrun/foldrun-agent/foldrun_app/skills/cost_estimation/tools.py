# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cost estimation tool wrappers for ADK FunctionTool."""

from typing import Optional

from foldrun_app.skills.cost_estimation.pricing import (
    estimate_monthly,
    estimate_single_job,
    get_actual_costs,
)


def estimate_job_cost(
    job_type: str,
    gpu_type: str = "auto",
    sequence_length: int = 300,
    num_predictions_per_model: int = 5,
    num_diffusion_samples: int = 5,
    region: Optional[str] = None,
) -> dict:
    """Estimate the cost of a single FoldRun prediction job.

    Returns a per-phase cost breakdown (MSA, predict, relax) with both on-demand
    and DWS FLEX_START (spot/preemptible) pricing side by side. FoldRun uses
    FLEX_START by default. These are estimates based on Google Cloud list pricing
    — actual costs depend on your organization's pricing agreement with Google.

    Durations scale continuously with sequence length (longer proteins take longer
    to predict and relax). The number of predict/relax tasks depends on job type:
    - AF2 monomer: always 5 tasks (5 models x 1 seed)
    - AF2 multimer: 5 x num_predictions_per_model tasks (default 25)
    - OF3/Boltz-2: num_predictions_per_model seeds, each running num_diffusion_samples

    Args:
        job_type: Type of prediction job. Use "af2_monomer" for single-chain AF2,
            "af2_multimer" for multi-chain AF2, "of3" for OpenFold3 predictions, or
            "boltz2" for Boltz-2 predictions.
        gpu_type: GPU to use. "auto" selects based on sequence length (recommended).
            Can also be "L4", "A100", or "A100_80GB".
        sequence_length: Number of residues (AF2) or tokens (OF3/Boltz-2) in the input.
            For multimers, use the total across all chains. Larger proteins take
            proportionally longer to predict.
        num_predictions_per_model: For AF2 multimer: predictions per model (default
            5, giving 5 models x 5 = 25 total). For OF3/Boltz-2: number of seeds (default 5).
            Ignored for AF2 monomer (always 5 models x 1).
        num_diffusion_samples: OF3/Boltz-2 only — diffusion samples per seed (default 5).
            Ignored for AF2 jobs.
        region: GCP region (default: us-central1).

    Returns:
        Dict with on_demand and dws_flex_start phase breakdowns, estimated totals,
        savings percentage, and pricing disclaimer.
    """
    return estimate_single_job(
        job_type=job_type,
        gpu_type=gpu_type,
        sequence_length=sequence_length,
        num_predictions_per_model=num_predictions_per_model,
        num_diffusion_samples=num_diffusion_samples,
        region=region or "us-central1",
    )


def estimate_monthly_cost(
    af2_monomer_jobs: int = 0,
    af2_multimer_jobs: int = 0,
    of3_jobs: int = 0,
    boltz2_jobs: int = 0,
    avg_sequence_length: int = 300,
    include_infrastructure: bool = True,
    region: Optional[str] = None,
) -> dict:
    """Estimate monthly and annual FoldRun costs for a given workload.

    Returns both on-demand and DWS FLEX_START (spot/preemptible) estimates side
    by side. Infrastructure and other costs are the same in both modes. These are
    estimates based on Google Cloud list pricing — actual costs depend on your
    organization's pricing agreement with Google.

    Uses default prediction counts per job type:
    - AF2 monomer: 5 predictions (5 models x 1 seed)
    - AF2 multimer: 25 predictions (5 models x 5 seeds)
    - OF3/Boltz-2: 5 seeds x 5 diffusion samples = 25 structures

    Calculates total cost across three categories:
    - Compute: GPU prediction costs based on job volumes (varies by pricing mode)
    - Infrastructure: always-on services (Filestore, GCS, Agent Engine, etc.)
    - Other: Gemini API, logging, egress, database refreshes

    Args:
        af2_monomer_jobs: Number of AF2 monomer jobs per month.
        af2_multimer_jobs: Number of AF2 multimer jobs per month.
        of3_jobs: Number of OpenFold3 jobs per month.
        boltz2_jobs: Number of Boltz-2 jobs per month.
        avg_sequence_length: Average sequence length in residues/tokens. Larger
            proteins cost more due to longer predict and relax runtimes.
        include_infrastructure: Include always-on infrastructure costs (default True).
            Set to False to see compute-only costs.
        region: GCP region (default: us-central1).

    Returns:
        Dict with on_demand and dws_flex_start breakdowns, infrastructure, other
        costs, savings percentage, and pricing disclaimer.
    """
    return estimate_monthly(
        af2_monomer_jobs=af2_monomer_jobs,
        af2_multimer_jobs=af2_multimer_jobs,
        of3_jobs=of3_jobs,
        boltz2_jobs=boltz2_jobs,
        avg_sequence_length=avg_sequence_length,
        include_infrastructure=include_infrastructure,
        region=region or "us-central1",
    )


def get_actual_job_costs(
    pipeline_job_id: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Retrieve actual costs for completed FoldRun prediction jobs.

    Queries Vertex AI for completed custom jobs submitted by FoldRun, calculates
    actual costs from real runtimes and machine specs using list pricing, and
    groups results by pipeline run. Also compares actuals against estimates.

    Use this to:
    - See how much a specific prediction actually cost
    - Compare actual vs estimated costs to calibrate trust in the estimator
    - Review historical spending across all FoldRun jobs

    Args:
        pipeline_job_id: Optional pipeline billing ID to get costs for a specific
            prediction run. If not provided, returns costs for all recent jobs.
            This is the numeric ID from the pipeline run (visible in job labels
            or Vertex AI console).
        limit: Maximum number of jobs to retrieve (default 50).

    Returns:
        Dict with pipeline_runs (each containing per-phase actual costs, durations,
        and estimate comparisons), plus a summary with totals. Costs are calculated
        from actual runtimes multiplied by list pricing — actual billed amounts may
        differ based on your pricing agreement.
    """
    import os

    project_id = (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT_ID")
    )
    region = (
        os.getenv("GCP_REGION")
        or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    )

    if not project_id:
        return {"error": "GCP_PROJECT_ID not configured"}

    return get_actual_costs(
        project_id=project_id,
        region=region,
        pipeline_job_id=pipeline_job_id,
        limit=limit,
    )
