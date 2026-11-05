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

"""Shared Agent Platform utilities used across all model plugins and platform tools."""

import json
import logging
import re
from typing import Any

from google.api_core import exceptions as google_exceptions
from google.cloud import aiplatform_v1 as vertex_ai

logger = logging.getLogger(__name__)

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FULL_RESOURCE_PATTERN = re.compile(r"^projects/([^/]+)/locations/([^/]+)/pipelineJobs/(.*)$")
_PROJECT_NUMBERS: dict[str, str] = {}


def _is_valid_proto_timestamp(dt: Any) -> bool:
    """Return True if dt is a non-null timestamp after the unset protobuf epoch (year > 1970)."""
    if not dt:
        return False
    year = getattr(dt, "year", None)
    if isinstance(year, int) and year <= 1970:
        return False
    return True


def _format_proto_timestamp(dt: Any) -> str | None:
    """Format a protobuf/datetime timestamp as ISO-8601, ignoring unset epoch (<= 1970)."""
    if not _is_valid_proto_timestamp(dt):
        return None
    return dt.isoformat()


def _convert_proto_to_dict(obj: Any) -> Any:
    """Recursively convert protobuf objects to native Python types for JSON serialization."""
    if obj is None:
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, (list, tuple)):
        return [_convert_proto_to_dict(item) for item in obj]

    if isinstance(obj, dict) or hasattr(obj, "items"):
        return {str(key): _convert_proto_to_dict(value) for key, value in obj.items()}

    if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        try:
            return [_convert_proto_to_dict(item) for item in obj]
        except TypeError:
            pass

    if hasattr(obj, "to_dict"):
        return obj.to_dict()

    try:
        return str(obj)
    except Exception:
        return None


def _resolve_pipeline_job_target(
    job_id: str, project_id: str, region: str
) -> tuple[str, str | None]:
    """Validate job_id and return (canonical_resource_name, caller_project_number)."""
    if not isinstance(job_id, str) or not job_id.strip() or "\x00" in job_id:
        raise ValueError("Invalid job_id: must be a non-empty string without null bytes")

    expected_prefix = f"projects/{project_id}/locations/{region}/pipelineJobs/"
    if job_id.startswith("projects/"):
        match = _FULL_RESOURCE_PATTERN.match(job_id)
        if not match:
            raise ValueError(
                f"Cross-project or cross-region pipeline job access denied: '{job_id}' does not match expected prefix '{expected_prefix}'"
            )
        proj, loc, short_id = match.groups()
        if loc != region:
            raise ValueError(
                f"Cross-project or cross-region pipeline job access denied: '{job_id}' does not match expected prefix '{expected_prefix}'"
            )
        caller_project_number: str | None = None
        if proj == project_id:
            pass
        elif proj.isdigit():
            known_number = _PROJECT_NUMBERS.get(project_id)
            if known_number is not None and proj != known_number:
                raise ValueError(
                    f"Cross-project or cross-region pipeline job access denied: '{job_id}' does not match project '{project_id}'"
                )
            caller_project_number = proj
        else:
            raise ValueError(
                f"Cross-project or cross-region pipeline job access denied: '{job_id}' does not match expected prefix '{expected_prefix}'"
            )

        if not _JOB_ID_PATTERN.match(short_id):
            raise ValueError(
                f"Malformed pipeline job resource name: invalid short job ID '{short_id}'"
            )
        return f"{expected_prefix}{short_id}", caller_project_number

    if not _JOB_ID_PATTERN.match(job_id):
        raise ValueError(f"Malformed pipeline job ID: '{job_id}'")
    return f"{expected_prefix}{job_id}", None


def resolve_pipeline_job_name(job_id: str, project_id: str, region: str) -> str:
    """Validate a pipeline job ID and return its canonical project resource name.

    Accepts either a full resource name (projects/{project_id_or_number}/locations/{region}/pipelineJobs/{id})
    or a short job name/ID. Always canonicalizes the returned resource name to
    projects/{project_id}/locations/{region}/pipelineJobs/{id} so outgoing RPCs
    never target another project or region.

    Raises:
        ValueError: If the ID is malformed or targets another project/region.
    """
    full_name, _ = _resolve_pipeline_job_target(job_id, project_id, region)
    return full_name


def extract_short_job_id(job_id: str, project_id: str, region: str) -> str:
    """Validate a pipeline job ID (short ID or full resource name) and return its short ID."""
    return resolve_pipeline_job_name(job_id, project_id, region).rsplit("/", 1)[-1]


def get_pipeline_job(job_id: str, project_id: str, region: str) -> vertex_ai.PipelineJob:
    """Get a Agent Platform pipeline job by ID.

    Accepts either a full resource name (projects/.../pipelineJobs/...)
    or a short job name/ID and constructs the full name automatically.
    Falls back to querying by display_name when the ID is not a direct resource name.

    Args:
        job_id: Full resource name or short job name/ID.
        project_id: GCP project ID.
        region: GCP region (e.g. 'us-central1').

    Returns:
        PipelineJob proto object.
    """
    full_name, caller_project_number = _resolve_pipeline_job_target(job_id, project_id, region)
    raw_id = full_name.rsplit("/", 1)[-1]

    client = vertex_ai.PipelineServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    try:
        request = vertex_ai.GetPipelineJobRequest(name=full_name)
        job = client.get_pipeline_job(request=request)
        job_res_name = getattr(job, "name", None)
        if isinstance(job_res_name, str):
            res_match = _FULL_RESOURCE_PATTERN.match(job_res_name)
            if res_match:
                actual_proj = res_match.group(1)
                if actual_proj.isdigit():
                    _PROJECT_NUMBERS[project_id] = actual_proj
                if (
                    caller_project_number is not None
                    and actual_proj != project_id
                    and actual_proj != caller_project_number
                ):
                    raise ValueError(
                        f"Cross-project or cross-region pipeline job access denied: '{job_id}' does not match project '{project_id}'"
                    )
        return job
    except (google_exceptions.NotFound, google_exceptions.InvalidArgument) as orig_exc:
        if job_id.startswith("projects/"):
            raise orig_exc
        parent = f"projects/{project_id}/locations/{region}"
        try:
            list_req = vertex_ai.ListPipelineJobsRequest(
                parent=parent,
                filter=f'display_name="{raw_id}"',
                order_by="create_time desc",
                page_size=10,
            )
            matches = list(client.list_pipeline_jobs(request=list_req))
            if matches:
                return matches[0]
        except Exception:
            pass
        raise orig_exc


def list_pipeline_jobs(
    project_id: str, region: str, filter_str: str | None = None, page_size: int = 100
) -> list[vertex_ai.PipelineJob]:
    """List pipeline jobs with optional filter."""
    client = vertex_ai.PipelineServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    request = vertex_ai.ListPipelineJobsRequest(
        parent=f"projects/{project_id}/locations/{region}", filter=filter_str, page_size=page_size
    )

    jobs = list(client.list_pipeline_jobs(request=request))
    logger.info(f"Found {len(jobs)} pipeline jobs")
    return jobs


def get_job_status(job: vertex_ai.PipelineJob) -> dict[str, Any]:
    """Extract status information from pipeline job."""
    create_ts = getattr(job, "create_time", None)
    start_ts = getattr(job, "start_time", None)
    end_ts = getattr(job, "end_time", None)

    status = {
        "job_id": job.name,
        "display_name": job.display_name,
        "state": job.state.name,
        "create_time": _format_proto_timestamp(create_ts),
        "start_time": _format_proto_timestamp(start_ts),
        "end_time": _format_proto_timestamp(end_ts),
        "labels": dict(job.labels) if job.labels else {},
        "pipeline_spec": job.pipeline_spec.get("pipelineInfo", {}).get("name", "unknown")
        if job.pipeline_spec
        else "unknown",
    }

    if (
        start_ts is not None
        and end_ts is not None
        and _is_valid_proto_timestamp(end_ts)
        and _is_valid_proto_timestamp(start_ts)
    ):
        duration = end_ts - start_ts
        status["duration_seconds"] = duration.total_seconds()

    if hasattr(job, "error") and job.error:
        status["error_message"] = job.error.message

    return status


def get_task_details(job: vertex_ai.PipelineJob) -> dict[str, Any]:
    """Extract task execution details from pipeline job."""
    if not hasattr(job, "job_detail") or not job.job_detail:
        return {"tasks": {}, "task_list": [], "predictions": []}

    tasks = {}
    task_list = []
    predictions = []
    job_display_name = getattr(job, "display_name", "") or ""

    for task in job.job_detail.task_details:
        task_info = {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "state": task.state.name if hasattr(task, "state") else "UNKNOWN",
            "create_time": _format_proto_timestamp(getattr(task, "create_time", None)),
            "start_time": _format_proto_timestamp(getattr(task, "start_time", None)),
            "end_time": _format_proto_timestamp(getattr(task, "end_time", None)),
        }

        custom_job_name = None
        if hasattr(task, "executor_detail") and task.executor_detail:
            cjd = getattr(task.executor_detail, "custom_job_detail", None)
            if cjd and getattr(cjd, "job", ""):
                custom_job_name = str(cjd.job)
            if not custom_job_name:
                cd = getattr(task.executor_detail, "container_detail", None)
                if cd and getattr(cd, "main_job", ""):
                    custom_job_name = str(cd.main_job)

        if hasattr(task, "execution") and task.execution:
            execution_metadata = {}
            if hasattr(task.execution, "metadata") and task.execution.metadata:
                execution_metadata = _convert_proto_to_dict(task.execution.metadata)
            task_info["execution_metadata"] = execution_metadata
            if not custom_job_name and "main_job" in execution_metadata:
                custom_job_name = str(execution_metadata["main_job"])
            elif not custom_job_name:
                gcp_res_raw = execution_metadata.get(
                    "output:gcp_resources"
                ) or execution_metadata.get("gcp_resources")
                if gcp_res_raw:
                    try:
                        resources = json.loads(gcp_res_raw)
                        for res in resources.get("resources", []):
                            if res.get("resourceType") == "CustomJob":
                                custom_job_name = res.get("resourceUri")
                                break
                    except Exception:
                        pass

        if custom_job_name:
            task_info["custom_job_name"] = custom_job_name
            task_info["custom_job_id"] = custom_job_name.split("/")[-1]

        if hasattr(task, "error") and task.error:
            task_info["error"] = {
                "message": task.error.message if hasattr(task.error, "message") else None,
                "code": task.error.code if hasattr(task.error, "code") else None,
            }

        tasks[task.task_name] = task_info

        tname = task.task_name
        is_wrapper = (
            tname.startswith("for-loop-")
            or tname.startswith("condition-")
            or (job_display_name and tname == job_display_name)
            or "-gw-" in tname
            or "-inference-pipeline" in tname
        )
        if not is_wrapper:
            task_list.append(task_info)

        if task.task_name == "predict" and hasattr(task, "outputs"):
            if "raw_prediction" in task.outputs:
                raw_pred = task.outputs["raw_prediction"]
                if hasattr(raw_pred, "artifacts") and len(raw_pred.artifacts) > 0:
                    artifact = raw_pred.artifacts[0]
                    if hasattr(artifact, "metadata"):
                        predictions.append(
                            {
                                "task_id": task.task_id,
                                "model_name": task.execution.metadata.get(
                                    "input:model_name", "unknown"
                                )
                                if hasattr(task, "execution")
                                else "unknown",
                                "ranking_confidence": artifact.metadata.get(
                                    "ranking_confidence", 0
                                ),
                                "uri": artifact.uri if hasattr(artifact, "uri") else None,
                            }
                        )

    predictions.sort(key=lambda x: float(x.get("ranking_confidence") or 0.0), reverse=True)

    return {
        "tasks": tasks,
        "task_list": task_list,
        "predictions": predictions,
        "best_prediction": predictions[0] if predictions else None,
    }


def list_artifacts(
    project_id: str, region: str, pipeline_context: str, artifact_filter: str | None = None
) -> list[vertex_ai.Artifact]:
    """List artifacts from a pipeline run."""
    client = vertex_ai.MetadataServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    parent = f"projects/{project_id}/locations/{region}/metadataStores/default"
    filter_str = f'in_context("{pipeline_context}")'
    if artifact_filter:
        filter_str += f" AND {artifact_filter}"

    request = vertex_ai.ListArtifactsRequest(parent=parent, filter=filter_str)

    artifacts = list(client.list_artifacts(request=request))
    logger.info(f"Found {len(artifacts)} artifacts")
    return artifacts
