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

"""Deduplication guard for pipeline job submissions.

Prevents double-submitting a job with the same display name while one is
already running. Applies across all models (AF2, OF3, Boltz-2).
"""

import logging
from typing import Optional

from google.cloud import aiplatform as vertex_ai
from google.cloud.aiplatform_v1.types import pipeline_state

logger = logging.getLogger(__name__)

_RUNNING_STATES = {
    pipeline_state.PipelineState.PIPELINE_STATE_QUEUED,
    pipeline_state.PipelineState.PIPELINE_STATE_PENDING,
    pipeline_state.PipelineState.PIPELINE_STATE_RUNNING,
}


def check_duplicate_job(
    job_name: str,
    project: str,
    location: str,
) -> Optional[dict]:
    """Check if a pipeline job with the same display name is already running.

    Args:
        job_name: The display name to check.
        project: GCP project ID.
        location: GCP region.

    Returns:
        A dict with status/message if a duplicate is found, else None.
    """
    try:
        existing = vertex_ai.PipelineJob.list(
            filter=f'display_name="{job_name}"',
            order_by="create_time desc",
            project=project,
            location=location,
        )
        for job in existing:
            if job.state in _RUNNING_STATES:
                job_id = job.resource_name.split("/")[-1]
                logger.info(f"Duplicate submission blocked: '{job_name}' is already running ({job_id})")
                return {
                    "status": "duplicate",
                    "message": (
                        f"A job named '{job_name}' is already running. "
                        f"To avoid duplicate submissions, please wait for it to complete "
                        f"or use a different job name. "
                        f"Existing job: {job.resource_name}"
                    ),
                    "job_name": job_name,
                    "existing_job_id": job_id,
                    "existing_job_resource": job.resource_name,
                }
    except Exception as e:
        # Dedup check is best-effort — don't block submission on API errors
        logger.warning(f"Dedup check failed for '{job_name}', proceeding: {e}")

    return None
