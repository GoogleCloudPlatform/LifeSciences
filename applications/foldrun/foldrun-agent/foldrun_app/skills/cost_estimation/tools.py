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

from foldrun_app.skills.cost_estimation.pricing import (
    estimate_monthly,
    estimate_single_job,
    get_actual_costs,
)


def estimate_job_cost(
    job_type: str,
    gpu_type: str = "auto",
    sequence_length: int = 300,
    num_predictions_per_model: int | None = None,
    num_diffusion_samples: int = 5,
    region: str | None = None,
) -> dict:
    """Estimate On-Demand and DWS FLEX_START costs for a single FoldRun prediction job.

    Args:
        job_type: One of "af2_monomer", "af2_multimer", "af3", "of3", or "boltz2".
        gpu_type: "auto" (recommended), "L4", "A100", "A100_80GB", or "H100".
        sequence_length: Total residues (AF2) or tokens (AF3/OF3/Boltz-2).
        num_predictions_per_model: Predictions per model (AF2 multimer default 5) or seeds (OF3/Boltz-2 default 5, AF3 default 1).
        num_diffusion_samples: Diffusion samples per seed for AF3/OF3/Boltz-2 (default 5).
        region: GCP region (default: us-central1).
    """
    resolved_region = region if (region and region.lower() != "global") else "us-central1"
    return estimate_single_job(
        job_type=job_type,
        gpu_type=gpu_type,
        sequence_length=sequence_length,
        num_predictions_per_model=num_predictions_per_model,
        num_diffusion_samples=num_diffusion_samples,
        region=resolved_region,
    )


def estimate_monthly_cost(
    af2_monomer_jobs: int = 0,
    af2_multimer_jobs: int = 0,
    af3_jobs: int = 0,
    of3_jobs: int = 0,
    boltz2_jobs: int = 0,
    avg_sequence_length: int = 300,
    include_infrastructure: bool = True,
    region: str | None = None,
) -> dict:
    """Estimate monthly and annual FoldRun compute and infrastructure costs (On-Demand vs DWS FLEX_START).

    Args:
        af2_monomer_jobs: Monthly AF2 monomer jobs.
        af2_multimer_jobs: Monthly AF2 multimer jobs.
        af3_jobs: Monthly AlphaFold3 jobs.
        of3_jobs: Monthly OpenFold3 jobs.
        boltz2_jobs: Monthly Boltz-2 jobs.
        avg_sequence_length: Average sequence length in residues/tokens.
        include_infrastructure: Include always-on Filestore/GCS/Agent Runtime costs (default True).
        region: GCP region (default: us-central1).
    """
    resolved_region = region if (region and region.lower() != "global") else "us-central1"
    return estimate_monthly(
        af2_monomer_jobs=af2_monomer_jobs,
        af2_multimer_jobs=af2_multimer_jobs,
        af3_jobs=af3_jobs,
        of3_jobs=of3_jobs,
        boltz2_jobs=boltz2_jobs,
        avg_sequence_length=avg_sequence_length,
        include_infrastructure=include_infrastructure,
        region=resolved_region,
    )


def get_actual_job_costs(
    pipeline_job_id: str | None = None,
    limit: int = 50,
) -> dict:
    """Retrieve actual compute costs for completed FoldRun jobs from runtime durations and machine specs.

    Args:
        pipeline_job_id: Optional pipeline billing ID for a specific run (omit for recent jobs).
        limit: Maximum number of jobs to retrieve (default 50).
    """
    import os
    import re

    if pipeline_job_id is not None:
        if not re.fullmatch(r"^[a-zA-Z0-9_-]+$", pipeline_job_id):
            return {"error": f"Invalid pipeline_job_id format: {pipeline_job_id!r}"}

    project_id = (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT_ID")
    )
    raw_cloud_loc = os.getenv("GOOGLE_CLOUD_LOCATION") or ""
    region = (
        os.getenv("GCP_REGION")
        or (raw_cloud_loc if raw_cloud_loc.lower() != "global" else "")
        or "us-central1"
    )

    if not project_id:
        return {"error": "GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT not configured"}

    return get_actual_costs(
        project_id=project_id,
        region=region,
        pipeline_job_id=pipeline_job_id,
        limit=limit,
    )
