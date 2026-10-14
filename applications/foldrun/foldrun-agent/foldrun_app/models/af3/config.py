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
        loc = (os.getenv("AF3_ENDPOINT_LOCATION") or self.region or "us-central1").strip()
        if loc.lower() == "global":
            return self.region if self.region.lower() != "global" else "us-central1"
        return loc

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
    def min_replica_count(self) -> int:
        """Minimum replica count for AF3 endpoint deployment (0 = Gemini platform Scale-to-Zero)."""
        return int(os.getenv("AF3_MIN_REPLICA_COUNT", "0"))

    @property
    def max_replica_count(self) -> int:
        """Maximum replica count for AF3 endpoint deployment."""
        return int(os.getenv("AF3_MAX_REPLICA_COUNT", "1"))

    @property
    def initial_replica_count(self) -> int:
        """Initial replica count when min_replica_count == 0 (Scale-to-Zero)."""
        return int(os.getenv("AF3_INITIAL_REPLICA_COUNT", "1"))

    @property
    def min_scaleup_period_seconds(self) -> int:
        """Scale-to-zero minimum scale-up duration in seconds (default 300s = 5m)."""
        return int(os.getenv("AF3_MIN_SCALEUP_PERIOD_SECONDS", "300"))

    @property
    def idle_scaledown_period_seconds(self) -> int:
        """Scale-to-zero idle scale-down duration in seconds before scaling to 0 (default 1200s = 20m)."""
        return int(os.getenv("AF3_IDLE_SCALEDOWN_PERIOD_SECONDS", "1200"))

    @property
    def capacity_pool(self) -> str:
        """Target capacity pool for AF3 endpoint ('DWS' by default, or 'ANY_RESERVATION', 'NO_RESERVATION', 'SPOT')."""
        raw = os.getenv("AF3_CAPACITY_POOL", "").strip().upper().replace("-", "_")
        if raw in ("ANY_RESERVATION", "RESERVATION", "SHARED_RESERVATION"):
            return "ANY_RESERVATION"
        if raw in ("NO_RESERVATION", "ON_DEMAND", "STANDARD"):
            return "NO_RESERVATION"
        if raw == "SPECIFIC_RESERVATION":
            return "SPECIFIC_RESERVATION"
        if raw == "SPOT":
            return "SPOT"
        if raw in ("DWS", "DWS_FLEX_START", "FLEX_START"):
            return "DWS"
        if os.getenv("AF3_USE_DWS", "true").strip().lower() in ("false", "0", "no", "off"):
            return (
                os.getenv("AF3_RESERVATION_AFFINITY_TYPE", "ANY_RESERVATION").strip().upper()
                or "ANY_RESERVATION"
            )
        return "DWS"

    @property
    def use_dws(self) -> bool:
        """Whether the AF3 endpoint defaults to Dynamic Workload Scheduler (DWS FlexStart)."""
        return self.capacity_pool == "DWS"

    @property
    def reservation_affinity_type(self) -> str:
        """Optional GCE reservation affinity type ('NO_RESERVATION', 'ANY_RESERVATION', 'SPECIFIC_RESERVATION')."""
        explicit = os.getenv("AF3_RESERVATION_AFFINITY_TYPE", "").strip()
        if explicit:
            return explicit
        if self.capacity_pool in ("ANY_RESERVATION", "NO_RESERVATION", "SPECIFIC_RESERVATION"):
            return self.capacity_pool
        return ""

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
                "min_replica_count": self.min_replica_count,
                "max_replica_count": self.max_replica_count,
                "initial_replica_count": self.initial_replica_count,
                "min_scaleup_period_seconds": self.min_scaleup_period_seconds,
                "idle_scaledown_period_seconds": self.idle_scaledown_period_seconds,
                "capacity_pool": self.capacity_pool,
                "use_dws": self.use_dws,
                "reservation_affinity_type": self.reservation_affinity_type,
                "reservation_names": self.reservation_names,
                "auto_fallback_gpu": self.auto_fallback_gpu,
                "default_msa_free": self.default_msa_free,
                "timeout_seconds": self.timeout_seconds,
            }
        )
        return d
