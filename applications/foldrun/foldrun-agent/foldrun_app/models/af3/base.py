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
import re
from typing import Any

from google.cloud import aiplatform as vertex_ai

from foldrun_app.core.base_tool import BaseTool

from .config import AF3Config

logger = logging.getLogger(__name__)

AF3_GCS_PREFIX_DIR = "af3_predictions"
MODEL_CIF_FILENAME = "{job_id}_model.cif"
SUMMARY_CONFIDENCES_FILENAME = "{job_id}_summary_confidences.json"
JOB_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class AF3Tool(BaseTool):
    """Base class for AlphaFold 3 tools invoking Agent Platform Prediction Endpoint."""

    def __init__(self, tool_config: dict[str, Any], config: AF3Config | None = None):
        super().__init__(tool_config, config or AF3Config())

    def get_endpoint(self, endpoint_id: str | None = None) -> vertex_ai.Endpoint:
        """Resolve and instantiate an Agent Platform Endpoint object.

        Supports auto-discovery when `endpoint_id` or `AF3_ENDPOINT` is set to `'auto'`.

        Args:
            endpoint_id: Optional endpoint ID or full resource name override.

        Returns:
            Instantiated vertex_ai.Endpoint.
        """
        target = (endpoint_id or self.config.endpoint_id or "").strip()
        if not target:
            raise ValueError("No AlphaFold 3 Endpoint ID specified or configured in AF3_ENDPOINT.")

        if target.lower() == "auto":
            endpoints = vertex_ai.Endpoint.list(
                project=self.config.project_id,
                location=self.config.endpoint_location,
            )
            af3_endpoints = [
                ep
                for ep in endpoints
                if any(
                    tok in (getattr(ep, "display_name", "") or "").lower()
                    for tok in ("alphafold", "af3")
                )
            ]
            candidates = af3_endpoints or endpoints
            if not candidates:
                raise ValueError(
                    f"No AlphaFold 3 Endpoint found in {self.config.project_id}/{self.config.endpoint_location}."
                )
            # Prefer an endpoint with active deployed models
            candidates.sort(
                key=lambda ep: (
                    len(getattr(ep, "deployed_models", []) or []),
                    getattr(ep, "update_time", ""),
                ),
                reverse=True,
            )
            return candidates[0]

        return vertex_ai.Endpoint(
            endpoint_name=target,
            project=self.config.project_id,
            location=self.config.endpoint_location,
        )

    def get_model(self, model_id: str | None = None) -> vertex_ai.Model:
        """Resolve and instantiate an Agent Platform Model object.

        Auto-discovers the latest registered AlphaFold 3 model in Vertex AI Model Registry
        when `model_id` and `AF3_MODEL_ID` are omitted or set to `'auto'`.

        Args:
            model_id: Optional model ID or full resource name override.

        Returns:
            Instantiated vertex_ai.Model.
        """
        target = (model_id or self.config.model_id or "").strip()
        if not target or target.lower() == "auto":
            models = vertex_ai.Model.list(
                project=self.config.project_id,
                location=self.config.endpoint_location,
            )
            af3_models = [
                m
                for m in models
                if any(
                    tok
                    in (getattr(m, "display_name", "") or getattr(m, "resource_name", "")).lower()
                    for tok in ("alphafold3", "alphafold-3", "af3")
                )
            ]
            if not af3_models:
                raise ValueError(
                    "No AlphaFold 3 Model ID specified in AF3_MODEL_ID and no matching model found in Vertex AI Model Registry."
                )
            return af3_models[0]

        return vertex_ai.Model(
            model_name=target,
            project=self.config.project_id,
            location=self.config.endpoint_location,
        )

    @staticmethod
    def validate_job_name(job_name: str) -> str:
        """Validate job_name against strict allowlist pattern to prevent path traversal."""
        if not isinstance(job_name, str) or not JOB_NAME_PATTERN.fullmatch(job_name):
            raise ValueError(
                f"Invalid job_name '{job_name}': only alphanumeric characters, "
                "underscores, and hyphens are allowed."
            )
        return job_name

    def get_job_gcs_prefix(self, job_name: str) -> str:
        """Return the full GCS URI prefix for a given job name."""
        safe_name = self.validate_job_name(job_name)
        return f"gs://{self.config.bucket_name}/{AF3_GCS_PREFIX_DIR}/{safe_name}"

    def get_job_blob_prefix(self, job_name: str) -> str:
        """Return the relative GCS blob path prefix for a given job name."""
        safe_name = self.validate_job_name(job_name)
        return f"{AF3_GCS_PREFIX_DIR}/{safe_name}"

    def get_cif_blob_path(self, job_name: str) -> str:
        """Return the relative GCS blob path for the predicted mmCIF structure."""
        safe_name = self.validate_job_name(job_name)
        return (
            f"{self.get_job_blob_prefix(safe_name)}/{MODEL_CIF_FILENAME.format(job_id=safe_name)}"
        )

    def get_conf_blob_path(self, job_name: str) -> str:
        """Return the relative GCS blob path for summary confidences JSON."""
        safe_name = self.validate_job_name(job_name)
        return f"{self.get_job_blob_prefix(safe_name)}/{SUMMARY_CONFIDENCES_FILENAME.format(job_id=safe_name)}"
