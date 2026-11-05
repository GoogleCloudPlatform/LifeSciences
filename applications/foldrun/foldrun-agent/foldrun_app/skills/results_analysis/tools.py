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

from foldrun_app.core.dispatch import (
    dispatch_analyze_job_parallel,
    dispatch_get_analysis_results,
)
from foldrun_app.skills._tool_registry import get_tool


def get_prediction_results(
    job_id: str,
    output_dir: str | None = None,
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
    job_id: str | None = None,
    model_index: int | None = None,
    raw_prediction_path: str | None = None,
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
    Trigger parallel structure analysis via Cloud Run Jobs across FoldRun models.

    Automatically detects the job's model backend (AlphaFold2, OpenFold3, Boltz-2,
    or AlphaFold 3) from pipeline job metadata and routes to the matching analyzer:
    - AlphaFold2 / OpenFold3 / Boltz-2: launches parallel Cloud Run Job tasks
      generating pLDDT plots, PAE/PDE heatmaps, ipTM matrices, and Gemini expert analysis.
    - AlphaFold 3: retrieves inline confidence metrics and rankings directly from GCS.

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job name (required)
        top_n: Number of top predictions to return in summary for AF2 (default: 10)
        overwrite: Overwrite existing analysis (default: False)

    Returns execution details (use get_analysis_results to retrieve results) or inline AF3 metrics.
    """
    return dispatch_analyze_job_parallel(
        job_id,
        top_n=top_n,
        overwrite=overwrite,
        get_tool_fn=get_tool,
    )


def get_analysis_results(
    job_id: str,
    wait: bool = False,
    timeout: int = 10,
    poll_interval: int = 2,
) -> dict:
    """
    Retrieve structure analysis results and confidence metrics for any FoldRun job.

    Automatically detects the job's model backend (AlphaFold2, OpenFold3, Boltz-2,
    or AlphaFold 3) and returns aggregated rankings, confidence scores (pLDDT, pTM,
    ipTM, PAE/PDE, affinity where applicable), Gemini expert analysis, viewer URL,
    and V4 Signed URLs in `downloads`.

    IMPORTANT: By default, this returns immediately (wait=False) with current status.
    Set wait=True only if you want to poll for completion (max 10s to avoid blocking).

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job name
        wait: Poll until analysis completes (default: False - returns immediately)
        timeout: Max wait time in seconds (default: 10 - very short to avoid blocking)
        poll_interval: Seconds between polls (default: 2)

    Returns summary statistics, rankings, and recommendations, or status if not complete.
    """
    return dispatch_get_analysis_results(
        job_id,
        wait=wait,
        timeout=timeout,
        poll_interval=poll_interval,
        get_tool_fn=get_tool,
    )


def analyze_job(job_id: str, detail_level: str = "summary") -> dict:
    """
    Perform analysis of any FoldRun pipeline job with configurable detail level.

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
        job_id: The pipeline job ID (e.g., 'alphafold2-inference-pipeline-20251110082144')
        detail_level: Level of detail - 'summary' (quick overview, default) or 'detailed' (with Cloud Logging error logs - fetches top 5 ERROR logs per failed task)

    Returns:
        Job analysis tailored to the job's current state and detail level
    """
    return get_tool("analyze_job_deep").run(
        {
            "job_id": job_id,
            "detail_level": detail_level,
        }
    )


def download_job_artifacts(
    job_id: str,
    include_raw: bool = False,
    expiration_minutes: int = 60,
) -> dict:
    """Generate secure, time-limited HTTPS download links (V4 Signed URLs) for a FoldRun job.

    Packages and signs a compact ZIP archive (`artifacts_bundle.zip`) containing the
    job's predicted structures (.cif / .pdb), diagnostic plots (.png), Gemini expert
    analysis report (.md), input sequence/query, and summary metrics, as well as
    individual signed download URLs for the top-ranked structure and plots.
    Supports AlphaFold2, OpenFold3, Boltz-2, and AlphaFold 3 jobs without requiring
    end-user GCS bucket permissions.

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job ID (required).
        include_raw: Include signed URLs for large raw matrices (.pkl, _confidences.json) (default: False).
        expiration_minutes: Signed URL validity duration in minutes (default: 60).

    Returns:
        Dictionary with `downloads` containing `artifacts_bundle_signed_url`,
        `best_structure_signed_url`, `summary_json_signed_url`, and `top_predictions`.
    """
    from foldrun_app.core.download_utils import download_job_artifacts_for_job
    from foldrun_app.core.startup_utils import get_core_config

    cfg = get_core_config()
    return download_job_artifacts_for_job(
        job_id=job_id,
        project_id=cfg.project_id,
        region=cfg.region,
        bucket_name=cfg.bucket_name,
        include_raw=include_raw,
        expiration_minutes=expiration_minutes,
    )
