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

"""Agent Platform utilities for pipeline management."""

import logging
from typing import Any, Dict, List, Optional

from google.cloud import aiplatform_v1 as vertex_ai

logger = logging.getLogger(__name__)


def _convert_proto_to_dict(obj: Any) -> Any:
    """
    Recursively convert protobuf objects to native Python types for JSON serialization.

    Args:
        obj: Any object that might contain protobuf types

    Returns:
        JSON-serializable Python object
    """
    # Handle None
    if obj is None:
        return None

    # Handle basic types
    if isinstance(obj, (str, int, float, bool)):
        return obj

    # Handle lists
    if isinstance(obj, (list, tuple)):
        return [_convert_proto_to_dict(item) for item in obj]

    # Handle dicts and MapComposite (protobuf map)
    if isinstance(obj, dict) or hasattr(obj, "items"):
        return {str(key): _convert_proto_to_dict(value) for key, value in obj.items()}

    # Handle protobuf repeated fields (sequences)
    if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        try:
            return [_convert_proto_to_dict(item) for item in obj]
        except TypeError:
            pass

    # Try to convert to dict if it has a to_dict method
    if hasattr(obj, "to_dict"):
        return obj.to_dict()

    # For other objects, try to convert to string
    try:
        return str(obj)
    except Exception:
        return None


# Re-exported from core for backward compatibility — all model plugins should
# import from foldrun_app.core.vertex_utils directly.
from foldrun_app.core.vertex_utils import get_pipeline_job as get_pipeline_job  # noqa: F401


def list_pipeline_jobs(
    project_id: str, region: str, filter_str: Optional[str] = None, page_size: int = 100
) -> List[vertex_ai.PipelineJob]:
    """
    List pipeline jobs with optional filter.

    Args:
        project_id: GCP project ID
        region: GCP region
        filter_str: Filter string (e.g., 'state="PIPELINE_STATE_RUNNING"')
        page_size: Number of results per page

    Returns:
        List of PipelineJob objects
    """
    client = vertex_ai.PipelineServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    request = vertex_ai.ListPipelineJobsRequest(
        parent=f"projects/{project_id}/locations/{region}", filter=filter_str, page_size=page_size
    )

    jobs = list(client.list_pipeline_jobs(request=request))
    logger.info(f"Found {len(jobs)} pipeline jobs")
    return jobs


def get_job_status(job: vertex_ai.PipelineJob) -> Dict[str, Any]:
    """
    Extract status information from pipeline job.

    Args:
        job: PipelineJob object

    Returns:
        Status dictionary
    """
    status = {
        "job_id": job.name,
        "display_name": job.display_name,
        "state": job.state.name,
        "create_time": job.create_time.isoformat() if job.create_time else None,
        "start_time": job.start_time.isoformat() if job.start_time else None,
        "end_time": job.end_time.isoformat() if job.end_time else None,
        "labels": dict(job.labels) if job.labels else {},
        "pipeline_spec": job.pipeline_spec.get("pipelineInfo", {}).get("name", "unknown")
        if job.pipeline_spec
        else "unknown",
    }

    # Calculate duration if job is complete
    if job.end_time and job.start_time:
        duration = job.end_time - job.start_time
        status["duration_seconds"] = duration.total_seconds()

    if hasattr(job, "error") and job.error:
        status["error_message"] = job.error.message

    return status


def get_task_details(job: vertex_ai.PipelineJob) -> Dict[str, Any]:
    """
    Extract task execution details from pipeline job.

    Args:
        job: PipelineJob object

    Returns:
        Task details dictionary including execution metadata like GPU type and flex_start settings
    """
    if not hasattr(job, "job_detail") or not job.job_detail:
        return {"tasks": {}, "predictions": []}

    tasks = {}
    predictions = []

    for task in job.job_detail.task_details:
        task_info = {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "state": task.state.name if hasattr(task, "state") else "UNKNOWN",
            "create_time": task.create_time.isoformat()
            if hasattr(task, "create_time") and task.create_time
            else None,
            "start_time": task.start_time.isoformat()
            if hasattr(task, "start_time") and task.start_time
            else None,
            "end_time": task.end_time.isoformat()
            if hasattr(task, "end_time") and task.end_time
            else None,
        }

        # Extract execution metadata (GPU type, flex_start, etc.)
        if hasattr(task, "execution") and task.execution:
            execution_metadata = {}
            if hasattr(task.execution, "metadata") and task.execution.metadata:
                # Convert protobuf MapComposite to native dict
                execution_metadata = _convert_proto_to_dict(task.execution.metadata)
            task_info["execution_metadata"] = execution_metadata

        # Extract error information if task failed
        if hasattr(task, "error") and task.error:
            task_info["error"] = {
                "message": task.error.message if hasattr(task.error, "message") else None,
                "code": task.error.code if hasattr(task.error, "code") else None,
            }

        tasks[task.task_name] = task_info

        # Extract prediction metrics
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

    # Sort predictions by ranking confidence
    predictions.sort(key=lambda x: x["ranking_confidence"], reverse=True)

    return {
        "tasks": tasks,
        "predictions": predictions,
        "best_prediction": predictions[0] if predictions else None,
    }


def list_artifacts(
    project_id: str, region: str, pipeline_context: str, artifact_filter: Optional[str] = None
) -> List[vertex_ai.Artifact]:
    """
    List artifacts from a pipeline run.

    Args:
        project_id: GCP project ID
        region: GCP region
        pipeline_context: Pipeline run context name
        artifact_filter: Optional filter string

    Returns:
        List of Artifact objects
    """
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
