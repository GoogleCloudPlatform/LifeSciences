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

"""Model-agnostic configuration management."""

import logging
import os
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class CoreConfig:
    """Model-agnostic configuration class for FoldRun tools."""

    def __init__(self, env_path: Optional[str] = None):
        """
        Initialize configuration from environment variables.

        Args:
            env_path: Path to .env file (optional)
        """
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        self._validate()

    def _validate(self):
        """Validate required shared environment variables.

        GCP_PROJECT_ID and GCP_REGION can be provided by Agent Engine as
        GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION respectively.
        """
        required_checks = {
            "GCP_PROJECT_ID": ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"),
            "GCP_REGION": ("GCP_REGION", "GOOGLE_CLOUD_LOCATION"),
            "GCS_BUCKET_NAME": ("GCS_BUCKET_NAME",),
            "FILESTORE_IP": ("FILESTORE_IP",),
            "VERTEX_AI_NETWORK_ATTACHMENT": ("VERTEX_AI_NETWORK_ATTACHMENT",),
        }

        missing = []
        for name, alternatives in required_checks.items():
            if not any(os.getenv(var) for var in alternatives):
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        logger.info("Configuration validation successful")

    @property
    def project_id(self) -> str:
        return os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")

    @property
    def region(self) -> str:
        return os.getenv("GCP_REGION") or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    @property
    def zone(self) -> str:
        return os.getenv("GCP_ZONE", "us-central1-a")

    @property
    def bucket_name(self) -> str:
        return os.getenv("GCS_BUCKET_NAME")

    @property
    def databases_bucket_name(self) -> str:
        """GCS bucket for genetic database backups (separate from pipeline bucket)."""
        return os.getenv("GCS_DATABASES_BUCKET", os.getenv("GCS_BUCKET_NAME"))

    @property
    def filestore_ip(self) -> Optional[str]:
        return os.getenv("FILESTORE_IP")

    @property
    def vertex_ai_network_attachment(self) -> Optional[str]:
        return os.getenv("VERTEX_AI_NETWORK_ATTACHMENT")

    @property
    def nfs_share(self) -> str:
        return os.getenv("NFS_SHARE", "/datasets")

    @property
    def nfs_mount_point(self) -> str:
        return os.getenv("NFS_MOUNT_POINT", "/mnt/nfs/foldrun")

    @property
    def dws_max_wait_hours(self) -> int:
        """Max hours DWS FLEX_START will wait per task for GPU provisioning."""
        return int(os.getenv("DWS_MAX_WAIT_HOURS", "168"))  # 7 days default

    @property
    def supported_gpus(self) -> list:
        """List of supported GPU types."""
        if hasattr(self, "_supported_gpus"):
            return self._supported_gpus

        # Default to all supported types if not explicitly configured
        default_gpus = "L4,A100,A100_80GB"
        supported = os.getenv("AF2_SUPPORTED_GPUS", default_gpus)
        self._supported_gpus = [g.strip() for g in supported.split(",") if g.strip()]
        return self._supported_gpus

    def set_supported_gpus(self, gpus: list):
        """Update supported GPUs list (e.g. after auto-detection)."""
        logger.info(f"Updating supported GPUs to: {gpus}")
        self._supported_gpus = gpus

    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        return {
            "project_id": self.project_id,
            "region": self.region,
            "zone": self.zone,
            "bucket_name": self.bucket_name,
            "supported_gpus": self.supported_gpus,
        }
