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

from google.cloud import aiplatform_v1 as vertex_ai


def get_pipeline_job(job_id: str, project_id: str, region: str) -> vertex_ai.PipelineJob:
    """Get a Agent Platform pipeline job by ID.

    Accepts either a full resource name (projects/.../pipelineJobs/...)
    or a short job name/ID and constructs the full name automatically.

    Args:
        job_id: Full resource name or short job name/ID.
        project_id: GCP project ID.
        region: GCP region (e.g. 'us-central1').

    Returns:
        PipelineJob proto object.
    """
    client = vertex_ai.PipelineServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    if not job_id.startswith("projects/"):
        job_id = f"projects/{project_id}/locations/{region}/pipelineJobs/{job_id}"

    request = vertex_ai.GetPipelineJobRequest(name=job_id)
    return client.get_pipeline_job(request=request)
