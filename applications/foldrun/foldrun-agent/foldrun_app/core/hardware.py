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

"""Model-agnostic GPU quota querying and hardware detection."""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Map of GCE quota metric names to GPU metadata
_QUOTA_MAPPING = {
    "NVIDIA_L4_GPUS": {
        "gpu_type": "NVIDIA_L4",
        "friendly_name": "L4",
        "machine_types": ["g2-standard-*"],
        "typical_use": "Small-medium proteins (<500 residues), relax tasks",
    },
    "NVIDIA_A100_GPUS": {
        "gpu_type": "NVIDIA_TESLA_A100",
        "friendly_name": "A100 (40GB)",
        "machine_types": ["a2-highgpu-*"],
        "typical_use": "Medium proteins (500-2000 residues), predict tasks",
    },
    "NVIDIA_A100_80GB_GPUS": {
        "gpu_type": "NVIDIA_A100_80GB",
        "friendly_name": "A100 (80GB)",
        "machine_types": ["a2-ultragpu-*"],
        "typical_use": "Large proteins/complexes (>2000 residues)",
    },
    "PREEMPTIBLE_NVIDIA_L4_GPUS": {
        "gpu_type": "NVIDIA_L4",
        "friendly_name": "L4 (Preemptible/Spot)",
        "machine_types": ["g2-standard-*"],
        "typical_use": "Cost-effective L4 GPUs for FLEX_START jobs",
    },
    "PREEMPTIBLE_NVIDIA_A100_GPUS": {
        "gpu_type": "NVIDIA_TESLA_A100",
        "friendly_name": "A100 40GB (Preemptible/Spot)",
        "machine_types": ["a2-highgpu-*"],
        "typical_use": "Cost-effective A100 GPUs for FLEX_START jobs",
    },
    "PREEMPTIBLE_NVIDIA_A100_80GB_GPUS": {
        "gpu_type": "NVIDIA_A100_80GB",
        "friendly_name": "A100 80GB (Preemptible/Spot)",
        "machine_types": ["a2-ultragpu-*"],
        "typical_use": "Cost-effective A100 80GB GPUs for FLEX_START jobs",
    },
}


def check_gpu_quota(project_id: str, region: str) -> Dict[str, Any]:
    """Query Compute Engine API and return GPU quota information.

    Args:
        project_id: GCP project ID
        region: GCP region (e.g. 'us-central1')

    Returns:
        Dict with 'on_demand_gpus' and 'preemptible_gpus' quota dicts.

    Raises:
        Exception: if Compute Engine API call fails
    """
    from google.cloud import compute_v1

    regions_client = compute_v1.RegionsClient()
    region_info = regions_client.get(project=project_id, region=region)

    gpu_quotas = {}
    for quota in region_info.quotas:
        if quota.metric not in _QUOTA_MAPPING:
            continue

        quota_info = _QUOTA_MAPPING[quota.metric]
        usage_pct = (quota.usage / quota.limit * 100) if quota.limit > 0 else 0
        available = quota.limit - quota.usage

        if available <= 0:
            status = "EXHAUSTED"
            status_emoji = "🔴"
        elif usage_pct >= 80:
            status = "HIGH_USAGE"
            status_emoji = "🟡"
        else:
            status = "AVAILABLE"
            status_emoji = "🟢"

        gpu_quotas[quota.metric] = {
            "friendly_name": quota_info["friendly_name"],
            "gpu_type": quota_info["gpu_type"],
            "limit": quota.limit,
            "usage": quota.usage,
            "available": available,
            "usage_percentage": round(usage_pct, 1),
            "status": status,
            "status_emoji": status_emoji,
            "machine_types": quota_info["machine_types"],
            "typical_use": quota_info["typical_use"],
        }

    on_demand = {k: v for k, v in gpu_quotas.items() if not k.startswith("PREEMPTIBLE_")}
    preemptible = {k: v for k, v in gpu_quotas.items() if k.startswith("PREEMPTIBLE_")}

    return {"on_demand_gpus": on_demand, "preemptible_gpus": preemptible}


def detect_supported_gpus(project_id: str, region: str) -> list:
    """Detect GPU types with available quota in the given region.

    Args:
        project_id: GCP project ID
        region: GCP region

    Returns:
        Ordered list of GPU type strings (['L4', 'A100', 'A100_80GB'])
        where quota limit > 0. Returns empty list on error.
    """
    try:
        quota_result = check_gpu_quota(project_id, region)
    except Exception as e:
        raise RuntimeError(f"Failed to retrieve GPU quota information: {e}") from e

    all_quotas = {
        **quota_result.get("on_demand_gpus", {}),
        **quota_result.get("preemptible_gpus", {}),
    }

    gpu_types_seen = set()
    for info in all_quotas.values():
        gpu_type = info["gpu_type"]
        limit = info["limit"]

        if "L4" in gpu_type and limit > 0:
            gpu_types_seen.add("L4")
        elif "A100_80GB" in gpu_type and limit > 0:
            gpu_types_seen.add("A100_80GB")
        elif "A100" in gpu_type and limit > 0:
            gpu_types_seen.add("A100")

    ordered = [g for g in ["L4", "A100", "A100_80GB"] if g in gpu_types_seen]
    return ordered
