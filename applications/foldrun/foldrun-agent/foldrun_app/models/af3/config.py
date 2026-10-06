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

"""Configuration management for AlphaFold 3 Agent Platform Endpoint tools."""

import logging
import os

from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)


class AF3Config(CoreConfig):
    """AlphaFold 3-specific configuration class extending CoreConfig."""

    def _validate(self):
        """Validate required environment variables for AF3.

        AF3 on Agent Platform Endpoint uses a managed serving container and does not
        require a Filestore NFS instance or local genetic databases in --msa-free mode.
        """
        required_checks = {
            "GCP_PROJECT_ID": ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"),
            "GCP_REGION": ("GCP_REGION", "GOOGLE_CLOUD_LOCATION"),
            "GCS_BUCKET_NAME": ("GCS_BUCKET_NAME",),
            "AF3_ENDPOINT": (
                "AF3_ENDPOINT",
                "AF3_AGENT_PLATFORM_ENDPOINT",
                "AF3_ENDPOINT_ID",
                "AF3_VERTEX_ENDPOINT",
            ),
        }

        missing = []
        for name, alternatives in required_checks.items():
            if not any(os.getenv(var) for var in alternatives):
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    @property
    def endpoint_id(self) -> str:
        """Agent Platform Endpoint resource name or ID."""
        for key in (
            "AF3_ENDPOINT",
            "AF3_AGENT_PLATFORM_ENDPOINT",
            "AF3_ENDPOINT_ID",
            "AF3_VERTEX_ENDPOINT",
        ):
            val = os.getenv(key, "").strip()
            if val:
                return val
        return ""

    @property
    def endpoint_location(self) -> str:
        """GCP location where the Agent Platform Endpoint is hosted."""
        return os.getenv("AF3_ENDPOINT_LOCATION") or self.region or "us-central1"

    @property
    def default_msa_free(self) -> bool:
        """Whether to run predictions in zero-MSA / --msa-free mode by default."""
        return os.getenv("AF3_DEFAULT_MSA_FREE", "false").lower() in ("true", "1", "yes")

    @property
    def timeout_seconds(self) -> int:
        """Timeout for Agent Platform Endpoint predict calls."""
        return int(os.getenv("AF3_TIMEOUT_SECONDS", "1800"))

    @property
    def model_id(self) -> str:
        """Agent Platform Model resource name or ID for AlphaFold 3."""
        return os.getenv("AF3_MODEL_ID", "")

    @property
    def machine_type(self) -> str:
        """Default machine type for AF3 endpoint deployment (a3-highgpu-1g provides 3 TB local NVMe SSD for the 630 GB MSA bundle)."""
        return os.getenv("AF3_MACHINE_TYPE", "a3-highgpu-1g")

    @property
    def accelerator_type(self) -> str:
        """Default accelerator type for AF3 endpoint deployment."""
        return os.getenv("AF3_ACCELERATOR_TYPE", "NVIDIA_H100_80GB")

    @property
    def accelerator_count(self) -> int:
        """Default accelerator count for AF3 endpoint deployment."""
        return int(os.getenv("AF3_ACCELERATOR_COUNT", "1"))

    @property
    def reservation_affinity_type(self) -> str:
        """Optional GCE reservation affinity type ('NO_RESERVATION', 'ANY_RESERVATION', 'SPECIFIC_RESERVATION')."""
        return os.getenv("AF3_RESERVATION_AFFINITY_TYPE", "").strip()

    @property
    def reservation_names(self) -> list[str]:
        """Optional list of GCE reservation resource names for SPECIFIC_RESERVATION."""
        raw = os.getenv("AF3_RESERVATION_NAMES", "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def auto_fallback_gpu(self) -> bool:
        """Whether to automatically fall back to lower GPU tiers (H100 -> A100_80GB -> A100 -> L4) on stockout/quota/reservation errors."""
        return os.getenv("AF3_AUTO_FALLBACK_GPU", "true").lower() in ("true", "1", "yes")

    @property
    def viewer_url(self) -> str:
        """Cloud Run viewer service URL."""
        return os.getenv("FOLDRUN_VIEWER_URL", "")

    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        d = super().to_dict()
        d.pop("filestore_id", None)
        d.pop("supported_gpus", None)
        d.update(
            {
                "endpoint_id": self.endpoint_id,
                "endpoint_location": self.endpoint_location,
                "model_id": self.model_id,
                "machine_type": self.machine_type,
                "accelerator_type": self.accelerator_type,
                "accelerator_count": self.accelerator_count,
                "reservation_affinity_type": self.reservation_affinity_type,
                "reservation_names": self.reservation_names,
                "auto_fallback_gpu": self.auto_fallback_gpu,
                "default_msa_free": self.default_msa_free,
                "timeout_seconds": self.timeout_seconds,
            }
        )
        return d
