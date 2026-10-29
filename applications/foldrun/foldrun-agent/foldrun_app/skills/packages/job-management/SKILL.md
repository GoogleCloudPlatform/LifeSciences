---
name: job-management
description: Management, monitoring, quota checks, and safe deletion of FoldRun prediction jobs
metadata:
  adk_additional_tools:
    - list_jobs
    - get_job_details
    - cancel_job
    - delete_job
    - check_af3_endpoint
---

# Job Management

- **AlphaFold 3 Endpoint Lifecycle & Cost**:
  - Unlike AF2/OF3/Boltz-2 (ephemeral KFP pipelines), AlphaFold 3 runs on a persistent dedicated endpoint (`a3-highgpu-1g`, 1x H100 80GB + 3 TB local NVMe SSD for the 630 GB MSA bundle, ~$11.06/hr per active replica).
  - The endpoint and model deployment are **managed by Terraform** (`--steps infra --enable-af3`). The agent does not deploy, scale, or undeploy the endpoint.
  - The endpoint uses **scale-to-zero** (`min_replica_count=0`, `initial_replica_count=1`): it scales down to 0 replicas ($0.00/hr) after 20 minutes idle and scales back up on the next prediction (the first request after idle waits for the replica to start; the pipeline retries automatically).
  - **Check Status, Replicas & Cost**: Use `check_af3_endpoint` to see whether a model is deployed, its replica settings, scale-to-zero configuration, and queue depth.
  - **No deployed model**: If `check_af3_endpoint` reports no deployed model, tell the user to re-run the FoldRun infrastructure step (`./deploy-all.sh <PROJECT_ID> <REGION> --steps infra --enable-af3`). Do not attempt to deploy from the agent.
  - **Large backlogs**: A single warm H100 replica folds `--msa-free` structures in ~58s and Full-MSA structures in ~4.5 min. For backlogs estimated above ~30 minutes, advise the user to increase `af3_max_replica_count` in Terraform and re-run the infrastructure step.

### Rule: H100 Quota Guidance
When a user is setting up the AlphaFold 3 endpoint (`google/alphafold3@v3_0_4`):
1. **Quota**: H100 serving quota is often restricted. Advise the user to confirm `Custom model serving Nvidia H100 80GB GPUs per region` quota (and the preemptible variant, `aiplatform.googleapis.com/custom_model_serving_preemptible_nvidia_h100_80gb_gpus`, if using spot/DWS) in the endpoint region.
2. **Region Matching**: The quota request must target the region configured for the FoldRun deployment (`AF3_ENDPOINT_LOCATION` / `GOOGLE_CLOUD_REGION`, e.g., `us-central1`).

### Rule: Idle Endpoint Scale-to-Zero Awareness
When the last job in an active AF3 queue finishes:
- Check `check_af3_endpoint`. If `active_slots_in_use: 0` and `queued_jobs_waiting: 0`, inform the user:
  "All jobs in this batch are complete. The endpoint will automatically scale down to 0 replicas ($0.00/hr) after 20 minutes of inactivity."

### Rule: Automatic Batch Duration & Scaling Prompt
Upon submitting a batch of N jobs:
- If Full MSA mode: estimate queue time as `N × 4.5 minutes / active_replicas`.
- If estimated queue time > 30 minutes and `active_replicas == 1`, include:
  "Estimated queue drain time is ~`<M>` min on 1 replica. To parallelize, increase `af3_max_replica_count` in Terraform and re-run the infrastructure step."

### Communicating Deployment Stages to Users
When inspecting an endpoint deployment via `check_af3_endpoint`, interpret the stages clearly:

- **`PENDING_NODE_ALLOCATION`**:
  The request is waiting for GPU capacity. If this persists for >10 minutes, advise the user that the requested pool is congested and suggest checking H100 quota.

- **`ADDING_NODES_TO_CLUSTER`**:
  **Capacity has been secured!** Google Cloud's cluster scheduler has successfully granted the physical `a3-highgpu-1g` node. The host is currently booting, pulling the container, and mounting the 3 TB local NVMe SSD (takes ~8–12 minutes total). Do NOT cancel or resubmit.

- **`ACTIVE` / `READY`**:
  Container health check has passed (`/v1/models/alphafold3:predict`), and replica slots are open to process queued predictions.

### Rule: Endpoint Readiness
- If an AF3 deployment is in progress (e.g., right after the Terraform infrastructure step), the KFP coordinator (`provision_and_queue_af3_endpoint_task`) waits for it to finish (up to ~210 minutes) and fails immediately if the deployment operation errors.
- When the endpoint has scaled to zero, the first prediction triggers scale-up; the pipeline retries until a replica is ready.
- **Best Practice Recommendation**: After a fresh infrastructure deploy, advise the user to wait until `check_af3_endpoint` shows `READY` before submitting a large 25-seed pipeline job.

- **Check GPU quota**: Use check_gpu_quota to view available GPU capacity BEFORE submitting jobs
  - **Auto-Detection**: The server automatically checks quotas at startup and prints a "Project GPU Inventory".
  - **Smart Filtering**: GPUs with 0 quota are automatically removed from the supported list.
  - **Auto-Upgrade**: If you request an unsupported GPU (e.g., L4 when you have no L4 quota), the server will automatically upgrade the job to the next available tier (e.g., A100) without failing.
  - Shows quota limits, current usage, and available capacity for L4, A100 (40GB), and A100 (80GB)
  - Displays both on-demand and preemptible/spot GPU quotas (used by FLEX_START)
  - Provides recommendations based on availability (e.g., if quota is exhausted)
  - **PROACTIVE USE**: Automatically check quotas when users mention submitting multiple jobs or large batches
  - Helps avoid job failures due to insufficient quota
- **List jobs**: Use list_jobs with filters (state, GPU type, sequence length, job name)
  - By default, list_jobs checks for analysis results using an efficient batch query
  - The response includes 'has_analysis' field for succeeded jobs
  - **IMPORTANT - Display Analysis Column**: ALWAYS display the analysis status in job tables
    - Display format: "✓" if has_analysis is True, "✗" if False, or "-" for non-succeeded jobs
    - This column helps users quickly identify which jobs have been analyzed
  - To disable analysis checking (rare), set check_analysis=false
- **Check status**: Use check_job_status to monitor progress of ANY job (running, failed, or succeeded)
  - This tool works for jobs in any state - use it whenever the user asks about job status/progress
  - Shows current state, completed tasks, running tasks, and estimated progress
  - **IMPORTANT**: When user asks "what's the status" or "check progress", use check_job_status (NOT analyze_job)
- **Track progress**: Provide real-time updates on job state
- **Get job details**: Use get_job_details to retrieve complete job metadata including original FASTA sequence
  - Essential for resubmitting failed jobs with different parameters (e.g., upgrading GPU type)
  - Returns: original sequence, all submission parameters, timing info, error details, and per-task configurations
  - **IMPORTANT - Per-Task GPU Configurations**: The response includes task_configurations showing the ACTUAL GPU type used by each pipeline task:
    - AlphaFold jobs use DIFFERENT GPUs for different tasks:
      * **predict** tasks: A100 40GB or A100 80GB (computationally intensive)
      * **relax** tasks: A100 40GB (matches predict tier by default)
      * **data-pipeline** tasks: CPU only (no GPU, sequence alignment)
    - Generative binder design jobs (**BoltzGen**, **RFD4-Proteina**, **BindCraft**) use a 2-stage pipeline without a CPU MSA step:
      * **configure-seeds** task: CPU only (seed and trajectory configuration)
      * **predict** tasks: A100 40GB or A100 80GB (`L4` optional for RFD4-Proteina)
    - Each task has its own: machine_type, accelerator_type, accelerator_count, strategy, max_wait_duration
    - When analyzing failed jobs, check which SPECIFIC task failed and what GPU it was using
    - Example: If relax task fails with "max wait duration reached", check the GPU type — older jobs may still reference L4
  - Use this when a user wants to retry a failed job or modify job settings
- **Retry failed jobs**: When a pipeline job fails (e.g., transient GPU provisioning error):
  1. Use get_job_details to retrieve the original sequence and parameters from the failed job
  2. Resubmit with the SAME sequence and parameters using submit_monomer/multimer_prediction
  3. Pipeline caching (enable_caching=True) automatically skips completed tasks and only re-runs failed ones
  - Example: If only relax failed, the resubmitted job skips data pipeline + predict (cached) and only runs relax
  - Tell the user: "I can retry your failed job — completed steps will be cached so only the failed tasks re-run"
  - If the failure was a transient provisioning error, the retry will likely succeed (tasks now auto-retry 2x with backoff)
  - If the failure was a code/data error, suggest checking get_job_details with detail_level='detailed' first
- **CRITICAL: Job Deletion Safety**
  - NEVER delete a job without explicit user confirmation
  - When asked to delete a job, first explain what will be deleted and what won't (GCS files remain)
  - Always warn that deletion is permanent and cannot be undone
  - Ask the user to confirm before proceeding with deletion
  - Only call delete_job with confirm=true after receiving explicit user approval
- **AMBER Relaxation Fallback**: If a relax task completed successfully but logs show "RELAX_FALLBACK", it means AMBER minimization failed on a disordered structure and the unrelaxed PDB was used instead. The job did NOT fail — results are still valid. When presenting results for such a job, clearly inform the user: "Note: AMBER relaxation failed for [N] structure(s) due to highly disordered regions. Unrelaxed structures were used instead — pLDDT and PAE confidence scores are unaffected. The structures are still suitable for downstream analysis."

## CRITICAL: Job Status Verification
**NEVER make claims about job status, success, or results without FIRST calling check_job_status or list_jobs.**

Before stating that a job has succeeded, failed, or completed:
1. ALWAYS call check_job_status first to get the current state
2. NEVER assume a job is complete based on context or previous information
3. NEVER report pLDDT scores, quality metrics, or results unless you have ACTUAL data from analysis tools
4. If a user asks to analyze results, FIRST check if the job has succeeded before attempting analysis
5. If analysis fails because files don't exist, immediately check the job status - it may still be running

**Example of CORRECT behavior:**
User: "what's the status of job X?"
Agent: [Calls check_job_status for job X]
Agent: "Job X is currently running in the data-pipeline step. It has completed 0 out of 106 tasks so far."

User: "analyze the results from job X"
Agent: [Calls check_job_status for job X first]
Agent: "I see job X is still running in the data-pipeline step. I'll need to wait until the job completes successfully before running analysis."

**Example of INCORRECT behavior (DO NOT DO THIS):**
User: "what's the status of job X?"
Agent: "I see job X is still running. Analysis can only be performed once the job completes successfully." [WRONG - user asked for STATUS, not analysis]

User: "analyze the results from job X"
Agent: "Great news! Job X succeeded with a pLDDT of 89.1..." [WRONG - didn't check status first]
