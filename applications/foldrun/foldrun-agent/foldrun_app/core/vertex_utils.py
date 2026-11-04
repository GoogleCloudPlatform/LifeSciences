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

"""Shared Agent Platform utilities used across all model plugins (AF2, OF3, Boltz-2)."""

import re

from google.api_core import exceptions as google_exceptions
from google.cloud import aiplatform_v1 as vertex_ai

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FULL_RESOURCE_PATTERN = re.compile(r"^projects/([^/]+)/locations/([^/]+)/pipelineJobs/(.*)$")
_PROJECT_NUMBERS: dict[str, str] = {}


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
