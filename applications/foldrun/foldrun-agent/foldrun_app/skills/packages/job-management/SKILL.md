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
    - deploy_af3_endpoint
    - undeploy_af3_endpoint
---

# Job Management

- **AlphaFold 3 Endpoint Lifecycle (Cost Optimization)**:
  - Unlike Vertex AI Pipelines (AF2/OF3/Boltz-2) which are ephemeral, an active Vertex AI Endpoint with a deployed model incurs continuous GPU billing (~$1.01/hr on L4).
  - An endpoint with 0 deployed models costs **$0.00/hr**.
  - **Check Status & Cost**: Use `check_af3_endpoint` to see whether the endpoint is dormant ($0/hr) or active ($1.01/hr).
  - **Spin Up GPU**: Use `deploy_af3_endpoint` to deploy the AF3 model onto an L4 GPU (`g2-standard-16`) on demand when starting an AF3 prediction session. Provisioning takes ~5–8 minutes.
  - **Tear Down to $0/hr**: Use `undeploy_af3_endpoint` immediately when the prediction session is finished to release GPU hardware and revert idle costs to $0.00/hr.
  - **PROACTIVE GUIDANCE**: When an AF3 prediction completes, ask the user if they have further AF3 jobs to run or if they would like to undeploy the endpoint to avoid ongoing charges.
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
