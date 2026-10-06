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
                    len(self.get_deployed_models(ep)),
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

    @staticmethod
    def get_deployed_models(endpoint: Any) -> list[Any]:
        """Return deployed models from a Vertex AI Endpoint.

        Note: `google.cloud.aiplatform.Endpoint` exposes deployed models via
        `endpoint.list_models()` (backed by `endpoint._gca_resource.deployed_models`)
        rather than a top-level `.deployed_models` attribute. This helper supports
        both the real Vertex AI SDK `Endpoint` and unit test mocks.
        """
        if "deployed_models" in getattr(endpoint, "__dict__", {}):
            return list(endpoint.deployed_models or [])
        if hasattr(endpoint, "list_models") and callable(endpoint.list_models):
            models = endpoint.list_models()
            if not type(models).__module__.startswith("unittest.mock"):
                try:
                    return list(models)
                except TypeError:
                    pass
        gca = getattr(endpoint, "_gca_resource", None)
        if gca is not None and not type(gca).__module__.startswith("unittest.mock"):
            return list(getattr(gca, "deployed_models", None) or [])
        return []

    def get_active_deploy_operations(self, endpoint: Any) -> list[dict[str, Any]]:
        """Return in-progress DeployModel LROs on the endpoint, if any."""
        if type(endpoint).__module__.startswith("unittest.mock"):
            return []
        resource_name = getattr(endpoint, "resource_name", "")
        if not isinstance(resource_name, str) or not resource_name.startswith("projects/"):
            return []
        try:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            creds, _ = google.auth.default()
            session = AuthorizedSession(creds)
            url = (
                f"https://{self.config.endpoint_location}-aiplatform.googleapis.com/"
                f"v1/{resource_name}/operations"
            )
            resp = session.get(url, timeout=10)
            if not resp.ok:
                return []
            operations = resp.json().get("operations", [])
            active = []
            for op in operations:
                if op.get("done") is True:
                    continue
                meta = op.get("metadata", {})
                if "DeployModel" in meta.get("@type", ""):
                    active.append(
                        {
                            "operation_name": op.get("name"),
                            "deployment_stage": meta.get("deploymentStage", "DEPLOYING"),
                            "create_time": meta.get("genericMetadata", {}).get("createTime"),
                        }
                    )
            return active
        except Exception as e:
            logger.debug(f"Could not list active endpoint operations for {resource_name}: {e}")
            return []

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

    def get_endpoint_console_url(self, endpoint_resource_name: str) -> str:
        """Return the Google Cloud Console URL for the Vertex AI Online Prediction Endpoint."""
        short_id = str(endpoint_resource_name).rstrip("/").split("/")[-1]
        return (
            f"https://console.cloud.google.com/vertex-ai/online-prediction/"
            f"locations/{self.config.endpoint_location}/endpoints/{short_id}"
            f"?project={self.config.project_id}"
        )

    def get_endpoint_logs_url(self, endpoint_resource_name: str) -> str:
        """Return the Cloud Logging URL filtered to the Vertex AI Endpoint container logs."""
        short_id = str(endpoint_resource_name).rstrip("/").split("/")[-1]
        return (
            f"https://console.cloud.google.com/logs/query;"
            f"query=resource.type%3D%22aiplatform.googleapis.com%2FEndpoint%22"
            f"%20resource.labels.endpoint_id%3D%22{short_id}%22;duration=PT1H"
            f"?project={self.config.project_id}"
        )
