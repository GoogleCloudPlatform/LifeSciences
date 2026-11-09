---
name: job-management
description: Monitors, lists, inspects, retries, and deletes FoldRun prediction jobs, and checks GPU quotas and AlphaFold3 endpoint readiness. Use when the user asks for job status, active or failed jobs, GPU availability, endpoint state, retrying a failed run, or deleting a job.
metadata:
  adk_additional_tools:
    - check_job_status
    - list_jobs
    - get_job_details
    - delete_job
    - check_gpu_quota
    - check_af3_endpoint
---

# Job Management

## CRITICAL: Job Status Verification
**NEVER make claims about job status, completion, or results without FIRST calling `check_job_status` or `list_jobs`.**
1. Always call `check_job_status(job_id=...)` first when asked about job progress or before running analysis on a job.
2. Never invent pLDDT scores, task progress, or completion states from prior context.
3. If `check_job_status` returns `NOT_FOUND` or an error, report that directly—do not assume success or failure.

## Core Operations
- **Check Job Status (`check_job_status`)**: Monitors progress of any AF2, OF3, Boltz-2, or AF3 pipeline job (running, failed, or succeeded). Use when the user asks *"what's the status"* or *"check progress"* (do NOT call `analyze_job` for simple status checks).
- **List Jobs (`list_jobs`)**: Filters jobs by `state` (`"running"`, `"failed"`, `"succeeded"`, `"pending"`, `"cancelled"` — always simple lowercase words, never `PIPELINE_STATE_*`), `job_type`, `gpu_type`, `seq_name`, or sequence length.
  - **Display Analysis Column**: `list_jobs` includes `has_analysis` for succeeded jobs. Always include an **Analysis** column in job tables (`"✓"` if `True`, `"✗"` if `False`, `"-"` if non-succeeded).
- **Inspect Job Details (`get_job_details`)**: Retrieves original FASTA/query input, submission parameters, timing, error details, and per-task `task_configurations` (showing actual `machine_type`, `accelerator_type`, and `strategy` for `data-pipeline`, `predict`, and `relax`).
- **Check GPU Quota (`check_gpu_quota`)**: Displays on-demand and preemptible/spot (`FLEX_START`) quota limits and usage for `L4`, `A100` (40GB), `A100_80GB`, `RTX_PRO_6000` (96GB), and `H100` (80GB). Call proactively before submitting multiple jobs or large batches.

## AlphaFold 3 Dedicated Endpoint Monitoring (`check_af3_endpoint`)
- **Architecture & Cost**: AF3 runs on a Terraform-managed dedicated endpoint (`a3-highgpu-1g`, 1x NVIDIA H100 80GB + 3 TB local NVMe SSD, ~$11.06/hr per active replica). The agent does **not** deploy or undeploy the endpoint.
- **Scale-to-Zero (`min_replica_count=0`)**: Replicas automatically scale down to `0` (`$0.00/hr`) after 20 minutes of inactivity and scale up on demand when a new job arrives. When `active_slots_in_use: 0` and `queued_jobs_waiting: 0` after a batch completes, inform the user that the endpoint will scale to zero after 20 minutes idle.
- **Deployment Stages**:
  - `No deployed model`: Instruct the user to run `./deploy-all.sh <PROJECT_ID> <REGION> --steps infra --enable-af3` and confirm `Custom model serving Nvidia H100 80GB GPUs per region` quota in the endpoint region.
  - `PENDING_NODE_ALLOCATION`: Waiting for H100 capacity (if >10 min, suggest checking H100 quota).
  - `ADDING_NODES_TO_CLUSTER`: H100 host granted; booting container and mounting 3 TB NVMe SSD (~8–12 min). Do not cancel or resubmit.
  - `ACTIVE` / `READY`: Healthy and ready to serve predictions.

## Retry Failed Jobs with Pipeline Caching
- **Retry failed jobs**: When a pipeline job fails (e.g., transient GPU provisioning timeout):
1. Call `get_job_details(job_id=...)` to retrieve the original sequence/query and parameters, and check which task (`data-pipeline`, `predict`, or `relax`) failed.
2. Resubmit using the corresponding submission tool (`submit_af2_monomer_prediction`, `submit_af2_multimer_prediction`, `submit_of3_prediction`, or `submit_boltz2_prediction`) after user confirmation.
3. Pipeline caching (`enable_caching=True`) automatically skips completed steps (e.g., skipping `data-pipeline` and `predict` if only `relax` failed).
- **AMBER Relaxation Fallback (`RELAX_FALLBACK`)**: If logs show `RELAX_FALLBACK`, AMBER minimization fell back to the unrelaxed structure due to disordered regions. The job succeeded and pLDDT/PAE scores are unaffected.

## Delete Jobs (`delete_job`) — Human-in-the-Loop (HITL) Confirmation Policy
**CRITICAL**: Never call `delete_job(..., confirm=True)` without obtaining explicit user approval first:
1. Clearly explain what will be deleted (the target `job_id` and its pipeline metadata/history in Agent Platform Pipelines, while noting GCS output files remain unless cleaned up separately via `cleanup_gcs_files`).
2. Warn the user that deletion is permanent and cannot be undone, and wait for explicit user approval before calling `delete_job(job_id=..., confirm=True)`.
