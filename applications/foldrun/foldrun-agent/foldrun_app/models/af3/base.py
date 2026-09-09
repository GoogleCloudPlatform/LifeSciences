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

"""Base class for AlphaFold3 Vertex AI Endpoint tools."""

import logging
from typing import Any

from google.cloud import aiplatform as vertex_ai

from foldrun_app.core.base_tool import BaseTool

from .config import AF3Config

logger = logging.getLogger(__name__)

AF3_GCS_PREFIX_DIR = "af3_predictions"
MODEL_CIF_FILENAME = "{job_id}_model.cif"
SUMMARY_CONFIDENCES_FILENAME = "{job_id}_summary_confidences.json"


class AF3Tool(BaseTool):
    """Base class for AlphaFold 3 tools invoking Agent Platform Prediction Endpoint."""

    def __init__(self, tool_config: dict[str, Any], config: AF3Config | None = None):
        super().__init__(tool_config, config or AF3Config())

    def get_endpoint(self, endpoint_id: str | None = None) -> vertex_ai.Endpoint:
        """Resolve and instantiate an Agent Platform Endpoint object.

        Args:
            endpoint_id: Optional endpoint ID or full resource name override.

        Returns:
            Instantiated vertex_ai.Endpoint.
        """
        target = endpoint_id or self.config.endpoint_id
        if not target:
            raise ValueError("No AlphaFold 3 Endpoint ID specified or configured in AF3_ENDPOINT.")

        return vertex_ai.Endpoint(
            endpoint_name=target,
            project=self.config.project_id,
            location=self.config.endpoint_location,
        )

    def get_model(self, model_id: str | None = None) -> vertex_ai.Model:
        """Resolve and instantiate an Agent Platform Model object.

        Args:
            model_id: Optional model ID or full resource name override.

        Returns:
            Instantiated vertex_ai.Model.
        """
        target = model_id or self.config.model_id
        if not target:
            raise ValueError("No AlphaFold 3 Model ID specified or configured in AF3_MODEL_ID.")

        return vertex_ai.Model(
            model_name=target,
            project=self.config.project_id,
            location=self.config.endpoint_location,
        )

    def get_job_gcs_prefix(self, job_name: str) -> str:
        """Return the full GCS URI prefix for a given job name."""
        return f"gs://{self.config.bucket_name}/{AF3_GCS_PREFIX_DIR}/{job_name}"

    def get_job_blob_prefix(self, job_name: str) -> str:
        """Return the relative GCS blob path prefix for a given job name."""
        return f"{AF3_GCS_PREFIX_DIR}/{job_name}"

    def get_cif_blob_path(self, job_name: str) -> str:
        """Return the relative GCS blob path for the predicted mmCIF structure."""
        return f"{self.get_job_blob_prefix(job_name)}/{MODEL_CIF_FILENAME.format(job_id=job_name)}"

    def get_conf_blob_path(self, job_name: str) -> str:
        """Return the relative GCS blob path for summary confidences JSON."""
        return f"{self.get_job_blob_prefix(job_name)}/{SUMMARY_CONFIDENCES_FILENAME.format(job_id=job_name)}"
