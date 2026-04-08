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

"""Results analysis tool wrappers for ADK FunctionTool."""

from typing import Optional

from foldrun_app.skills._tool_registry import get_tool


def get_prediction_results(
    job_id: str,
    output_dir: Optional[str] = None,
    include_raw_predictions: bool = False,
    download_files: bool = True,
) -> dict:
    """Retrieve and download AlphaFold2 prediction results."""
    return get_tool("af2_get_results").run(
        {
            "job_id": job_id,
            "output_dir": output_dir,
            "include_raw_predictions": include_raw_predictions,
            "download_files": download_files,
        }
    )


def analyze_prediction_quality(
    job_id: Optional[str] = None,
    model_index: Optional[int] = None,
    raw_prediction_path: Optional[str] = None,
    analyze_all: bool = False,
    top_n: int = 5,
) -> dict:
    """
    Analyze quality metrics of AlphaFold2 prediction.

    Supports single prediction analysis or batch comparison of all predictions from a job.
    When analyze_all=True, returns comparison metrics, rankings, and recommendations.
    """
    return get_tool("af2_analyze_quality").run(
        {
            "job_id": job_id,
            "model_index": model_index,
            "raw_prediction_path": raw_prediction_path,
            "analyze_all": analyze_all,
            "top_n": top_n,
        }
    )


def analyze_job_parallel(
    job_id: str,
    top_n: int = 10,
    overwrite: bool = False,
) -> dict:
    """
    Trigger parallel analysis via Cloud Run Jobs (25 tasks simultaneously).

    Uses Cloud Run Jobs for true parallel batch processing of predictions.
    Each task analyzes one prediction independently without HTTP constraints.

    Performance: ~60 seconds for 25 predictions vs ~52 minutes sequential.
    Cost: ~$0.015 per job.

    Args:
        job_id: Job ID (required)
        top_n: Number of top predictions to return in summary (default: 10)
        overwrite: Overwrite existing analysis (default: False)

    Returns execution details. Use get_analysis_results to retrieve results.
    """
    return get_tool("af2_analyze_parallel").run(
        {
            "job_id": job_id,
            "top_n": top_n,
            "overwrite": overwrite,
        }
    )


def get_analysis_results(
    job_id: str,
    wait: bool = False,
    timeout: int = 10,
    poll_interval: int = 2,
) -> dict:
    """
    Get async analysis results from Cloud Run.

    Aggregates individual analysis results from GCS and returns comprehensive summary.

    IMPORTANT: By default, this returns immediately (wait=False) with current status.
    Set wait=True only if you want to poll for completion (max 10s to avoid blocking).

    Args:
        job_id: Job ID
        wait: Poll until analysis completes (default: False - returns immediately)
        timeout: Max wait time in seconds (default: 10 - very short to avoid blocking)
        poll_interval: Seconds between polls (default: 2)

    Returns summary statistics, rankings, and recommendations, or status if not complete.
    """
    return get_tool("af2_get_analysis_results").run(
        {
            "job_id": job_id,
            "wait": wait,
            "timeout": timeout,
            "poll_interval": poll_interval,
        }
    )


def of3_analyze_job_parallel(
    job_id: str,
    overwrite: bool = False,
) -> dict:
    """
    Trigger parallel analysis of OpenFold3 predictions via Cloud Run Jobs.

    Discovers OF3 output samples (CIF + confidence JSON files) from GCS,
    generates pLDDT plots, PDE heatmaps, ipTM matrix, and Gemini expert analysis.

    Args:
        job_id: OpenFold3 pipeline job ID (required)
        overwrite: Overwrite existing analysis (default: False)

    Returns execution details. Use of3_get_analysis_results to retrieve results.
    """
    return get_tool("of3_analyze_parallel").run(
        {
            "job_id": job_id,
            "overwrite": overwrite,
        }
    )


def of3_get_analysis_results(
    job_id: str,
    wait: bool = False,
    timeout: int = 10,
    poll_interval: int = 2,
) -> dict:
    """
    Get OpenFold3 analysis results from Cloud Run.

    Returns ranking_score, pTM, ipTM, chain_pair_iptm, pLDDT metrics,
    and Gemini expert analysis when complete.

    Args:
        job_id: OpenFold3 pipeline job ID
        wait: Poll until analysis completes (default: False)
        timeout: Max wait time in seconds (default: 10)
        poll_interval: Seconds between polls (default: 2)
    """
    return get_tool("of3_get_analysis_results").run(
        {
            "job_id": job_id,
            "wait": wait,
            "timeout": timeout,
            "poll_interval": poll_interval,
        }
    )


def analyze_job(job_id: str, detail_level: str = "summary") -> dict:
    """
    Perform analysis of any AlphaFold job with configurable detail level.

    Returns information tailored to the job's current state:

    **For FAILED jobs:**
    - Error messages and codes from failed tasks
    - Which pipeline tasks failed (e.g., data-pipeline, alphafold-inference)
    - Detailed error logs from Cloud Logging (if detail_level='detailed')
    - Diagnostic guidance with likely causes
    - Recommended troubleshooting actions

    **For SUCCESSFUL jobs:**
    - Job duration and completion time
    - Output summary and next steps

    **For RUNNING/PENDING jobs:**
    - Current progress percentage
    - Which task is currently running
    - Completed vs total tasks

    **For all jobs:**
    - Console URL for detailed investigation
    - Task-by-task breakdown
    - Configuration details

    Args:
        job_id: The AlphaFold pipeline job ID (e.g., 'alphafold-inference-pipeline-20251110082144')
        detail_level: Level of detail - 'summary' (quick overview, default) or 'detailed' (with Cloud Logging error logs - fetches top 5 ERROR logs per failed task)

    Returns:
        Job analysis tailored to the job's current state and detail level
    """
    return get_tool("af2_analyze_job_deep").run(
        {
            "job_id": job_id,
            "detail_level": detail_level,
        }
    )
