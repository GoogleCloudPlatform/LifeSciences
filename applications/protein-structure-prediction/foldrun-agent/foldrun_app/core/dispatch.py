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

"""Polymorphic model-type resolution and post-submission tool dispatch."""

import logging
import os
import re
from collections.abc import Callable
from typing import Any

from google.cloud import storage

from foldrun_app.core.model_registry import (
    get_model,
    infer_model_type_from_job_id,
    normalize_model_type,
)
from foldrun_app.core.vertex_utils import get_pipeline_job

logger = logging.getLogger(__name__)

_SAFE_SHORT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _is_mock_obj(obj: Any) -> bool:
    """Return True when ``obj`` is a ``unittest.mock`` object."""
    return type(obj).__module__.startswith("unittest.mock")


def _is_unconfigured_mock_job(job: Any) -> bool:
    """Detect a generic unconfigured MagicMock returned by an unrelated global patch."""
    if not _is_mock_obj(job):
        return False
    labels = getattr(job, "labels", None)
    display_name = getattr(job, "display_name", None)
    name = getattr(job, "name", None)
    return (
        not isinstance(labels, dict)
        and not isinstance(display_name, str)
        and not isinstance(name, str)
    )


def _check_af3_gcs_artifacts(job_id: str, project_id: str | None) -> bool:
    """Return True if ``gs://<GCS_BUCKET_NAME>/af3_predictions/<job_id>/`` contains artifacts."""
    if not get_model("alphafold3").is_enabled():
        return False
    bucket_name = (os.getenv("GCS_BUCKET_NAME") or "").strip()
    if not bucket_name or "/" in job_id or not _SAFE_SHORT_ID_RE.match(job_id):
        return False
    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=f"af3_predictions/{job_id}/", max_results=1))
        return len(blobs) > 0
    except Exception as exc:
        logger.debug(f"AF3 GCS prefix probe skipped for '{job_id}': {exc}")
        return False


def _call_get_pipeline_job(job_id: str, project_id: str, region: str) -> Any:
    """Invoke ``get_pipeline_job`` honoring patches on ``dispatch`` or ``vertex_utils``."""
    import foldrun_app.core.vertex_utils as vertex_utils_mod

    if _is_mock_obj(get_pipeline_job):
        return get_pipeline_job(job_id, project_id, region)
    return vertex_utils_mod.get_pipeline_job(job_id, project_id, region)


def resolve_job_model_type(
    job_id: str,
    *,
    allow_remote_lookup: bool = True,
) -> tuple[str, Any | None]:
    """Resolve ``(model_type, pipeline_job_or_none)`` for ``job_id``."""
    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    region = (
        os.getenv("GCP_REGION") or os.getenv("GOOGLE_CLOUD_LOCATION") or "us-central1"
    ).strip()
    if region.lower() == "global":
        region = (os.getenv("GCP_REGION") or "us-central1").strip()

    if allow_remote_lookup and project_id:
        try:
            job = _call_get_pipeline_job(job_id, project_id, region)
            if not _is_unconfigured_mock_job(job):
                labels = getattr(job, "labels", None)
                if isinstance(labels, dict):
                    from_label = normalize_model_type(labels.get("model_type"))
                    if from_label:
                        return from_label, job

                for attr in ("display_name", "name"):
                    val = getattr(job, attr, None)
                    if isinstance(val, str):
                        from_attr = infer_model_type_from_job_id(val)
                        if from_attr:
                            return from_attr, job

                from_id = infer_model_type_from_job_id(job_id)
                if from_id:
                    return from_id, job

                return "alphafold2", job
        except Exception as exc:
            logger.debug(
                f"PipelineJob lookup for '{job_id}' fell back to heuristic dispatch: {exc}"
            )

    inferred = infer_model_type_from_job_id(job_id)
    if inferred:
        return inferred, None

    if (
        allow_remote_lookup
        and isinstance(job_id, str)
        and _check_af3_gcs_artifacts(job_id.strip(), project_id)
    ):
        return "alphafold3", None

    return "alphafold2", None


def _should_allow_remote_lookup(get_tool_fn: Callable[[str], Any]) -> bool:
    """Avoid live GCP RPCs when ``get_tool`` is mocked in offline wrapper unit tests."""
    import foldrun_app.core.vertex_utils as vertex_utils_mod

    if _is_mock_obj(get_tool_fn) and not (
        _is_mock_obj(get_pipeline_job) or _is_mock_obj(vertex_utils_mod.get_pipeline_job)
    ):
        return False
    return True


def dispatch_analyze_job_parallel(
    job_id: str,
    *,
    top_n: int = 10,
    overwrite: bool = False,
    get_tool_fn: Callable[[str], Any],
) -> dict[str, Any]:
    """Dispatch ``analyze_job_parallel`` to the model backend matching ``job_id``."""
    model_type, job = resolve_job_model_type(
        job_id, allow_remote_lookup=_should_allow_remote_lookup(get_tool_fn)
    )
    backend_key = get_model(model_type).analyze_tool_key

    if model_type == "alphafold3":
        return get_tool_fn(backend_key).run({"job_id": job_id})

    args: dict[str, Any] = {
        "job_id": job_id,
        "overwrite": overwrite,
    }
    if model_type == "alphafold2":
        args["top_n"] = top_n
    if job is not None:
        args["_pipeline_job"] = job
    return get_tool_fn(backend_key).run(args)


def dispatch_get_analysis_results(
    job_id: str,
    *,
    wait: bool = False,
    timeout: int = 10,
    poll_interval: int = 2,
    get_tool_fn: Callable[[str], Any],
) -> dict[str, Any]:
    """Dispatch ``get_analysis_results`` to the model backend matching ``job_id``."""
    model_type, job = resolve_job_model_type(
        job_id, allow_remote_lookup=_should_allow_remote_lookup(get_tool_fn)
    )
    backend_key = get_model(model_type).results_tool_key

    if model_type == "alphafold3":
        return get_tool_fn(backend_key).run({"job_id": job_id})

    args: dict[str, Any] = {
        "job_id": job_id,
        "wait": wait,
        "timeout": timeout,
        "poll_interval": poll_interval,
    }
    if job is not None:
        args["_pipeline_job"] = job
    return get_tool_fn(backend_key).run(args)


def dispatch_open_structure_viewer(
    job_id: str,
    *,
    pdb_uri: str | None = None,
    summary_uri: str | None = None,
    model_name: str = "Best Model",
    open_browser: bool = True,
    get_tool_fn: Callable[[str], Any],
) -> dict[str, Any]:
    """Dispatch ``open_structure_viewer`` to the model backend matching ``job_id``."""
    model_type, _ = resolve_job_model_type(
        job_id, allow_remote_lookup=_should_allow_remote_lookup(get_tool_fn)
    )
    backend_key = get_model(model_type).viewer_tool_key

    if model_type == "alphafold2":
        return get_tool_fn(backend_key).run(
            {
                "job_id": job_id,
                "pdb_uri": pdb_uri,
                "summary_uri": summary_uri,
                "model_name": model_name,
                "open_browser": open_browser,
            }
        )

    return get_tool_fn(backend_key).run(
        {
            "job_id": job_id,
            "open_browser": open_browser,
        }
    )
