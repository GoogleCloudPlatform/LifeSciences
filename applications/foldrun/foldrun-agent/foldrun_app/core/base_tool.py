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

"""Model-agnostic base tool class."""

import logging
import re
from typing import Any, Dict

from google.cloud import aiplatform as vertex_ai
from google.cloud import storage

from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)

# Shared clients — initialized once, reused by all BaseTool instances
_vertex_initialized = False
_shared_storage_client = None


def _ensure_clients(config: CoreConfig):
    """Initialize Vertex AI SDK and GCS client once, reuse thereafter."""
    global _vertex_initialized, _shared_storage_client

    if not _vertex_initialized:
        vertex_ai.init(
            project=config.project_id,
            location=config.region,
            staging_bucket=f"gs://{config.bucket_name}/staging",
        )
        _vertex_initialized = True
        logger.info("Vertex AI SDK initialized (shared)")

    if _shared_storage_client is None:
        _shared_storage_client = storage.Client(project=config.project_id)
        logger.info("GCS client initialized (shared)")


class BaseTool:
    """Model-agnostic base class for FoldRun tools."""

    def __init__(self, tool_config: Dict[str, Any], config: CoreConfig):
        """
        Initialize base tool with configuration.

        Args:
            tool_config: Tool configuration dictionary
            config: Global configuration object
        """
        self.tool_config = tool_config
        self.name = tool_config.get("name")
        self.description = tool_config.get("description")

        self.config = config

        # Initialize shared clients (no-op after first call)
        _ensure_clients(self.config)

        # Use the shared GCS client
        self.storage_client = _shared_storage_client

    @staticmethod
    def gcs_console_url(gs_uri: str) -> str:
        """Convert a gs:// URI to a Cloud Console Storage browser link.

        gs://bucket/path/to/file → https://console.cloud.google.com/storage/browser/bucket/path/to/file
        """
        if not gs_uri or not gs_uri.startswith("gs://"):
            return gs_uri
        path = gs_uri[len("gs://") :]
        return f"https://console.cloud.google.com/storage/browser/{path}"

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the tool - must be implemented by subclasses.

        Args:
            arguments: Tool arguments

        Returns:
            Result dictionary
        """
        raise NotImplementedError("Subclasses must implement run()")

    def _upload_to_gcs(self, local_path: str, gcs_path: str) -> str:
        """
        Upload file to GCS.

        Args:
            local_path: Local file path
            gcs_path: GCS path (gs://bucket/path)

        Returns:
            GCS path
        """
        bucket_name = gcs_path.replace("gs://", "").split("/")[0]
        blob_path = "/".join(gcs_path.replace("gs://", "").split("/")[1:])

        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_filename(local_path)

        logger.info(f"Uploaded {local_path} to {gcs_path}")
        return gcs_path

    def _download_from_gcs(self, gcs_path: str, local_path: str) -> str:
        """
        Download file from GCS.

        Args:
            gcs_path: GCS path (gs://bucket/path)
            local_path: Local file path

        Returns:
            Local path
        """
        bucket_name = gcs_path.replace("gs://", "").split("/")[0]
        blob_path = "/".join(gcs_path.replace("gs://", "").split("/")[1:])

        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(local_path)

        logger.info(f"Downloaded {gcs_path} to {local_path}")
        return local_path


    def _clean_label(self, value: str) -> str:
        """
        Clean label value for GCP compliance.

        GCP label values allow lowercase letters, numeric characters, underscores,
        and dashes. Preserving dashes is important so that job names stored as labels
        can be used to reconstruct GCS paths without lossy conversion.

        Args:
            value: Label value

        Returns:
            Cleaned label value (max 63 chars, lowercase alphanumeric + dash + underscore)
        """
        cleaned = re.sub(r"[^a-z0-9_\-]", "_", str(value).lower())
        if not cleaned[0].isalnum():
            cleaned = "x" + cleaned
        return cleaned[:63]
