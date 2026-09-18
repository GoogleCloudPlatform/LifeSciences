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

"""Modular instructions for job management skill."""

JOB_MANAGEMENT_INSTRUCTION = """### Job Management
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
- **Delete jobs**: Use delete_job to remove pipeline jobs from Agent Platform
  - **CRITICAL - Human-in-the-Loop (HITL) Confirmation Policy**:
    1. NEVER invoke `delete_job(..., confirm=true)` without obtaining explicit user approval first.
    2. Before deleting any job, clearly explain what will be deleted (the target job ID and its pipeline metadata/history in Agent Platform).
    3. Explicitly warn the user that deletion is permanent and cannot be undone.
    4. Ask the user for explicit confirmation and wait for their approval before calling `delete_job(..., confirm=true)`.
  - Requires confirm=true as a safety check
  - WARNING: This action is permanent and cannot be undone
  - Only deletes job metadata from Agent Platform, NOT the GCS output files
  - Use this to clean up failed or unnecessary jobs

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
"""
