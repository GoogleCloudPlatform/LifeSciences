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
    """Trigger parallel Cloud Run structure analysis across AF2, OF3, Boltz-2, or AF3 jobs.

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job name.
        top_n: Number of top predictions for AF2 summary (default 10).
        overwrite: Overwrite existing analysis (default False).
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
    """Retrieve completed structure analysis metrics (pLDDT, pTM/ipTM, PAE/PDE, affinity), expert report, and signed URLs.

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job name.
        wait: Poll until analysis completes (default False).
        timeout: Max wait time in seconds (default 10).
        poll_interval: Seconds between polls (default 2).
    """
    return dispatch_get_analysis_results(
        job_id,
        wait=wait,
        timeout=timeout,
        poll_interval=poll_interval,
        get_tool_fn=get_tool,
    )


def analyze_job(job_id: str, detail_level: str = "summary") -> dict:
    """Diagnose a failed, running, or completed pipeline job with optional Cloud Logging error inspection.

    Args:
        job_id: Pipeline job ID.
        detail_level: "summary" (default) or "detailed" (fetches top 5 Cloud Logging ERROR entries per failed task).
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
    """Generate 60-minute V4 Signed HTTPS download URLs (`artifacts_bundle.zip`, best structure, and plots) for any job.

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job ID.
        include_raw: Include signed URLs for raw .pkl/_confidences.json matrices (default False).
        expiration_minutes: Signed URL validity in minutes (default 60).
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
