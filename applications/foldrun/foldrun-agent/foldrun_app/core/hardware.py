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
from typing import Any

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
    "NVIDIA_H100_GPUS": {
        "gpu_type": "NVIDIA_H100_80GB",
        "friendly_name": "H100 (80GB)",
        "machine_types": ["a3-highgpu-*"],
        "typical_use": "AlphaFold 3 Full-MSA & large all-atom complexes (up to 5000+ tokens)",
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
    "PREEMPTIBLE_NVIDIA_H100_GPUS": {
        "gpu_type": "NVIDIA_H100_80GB",
        "friendly_name": "H100 80GB (Preemptible/Spot)",
        "machine_types": ["a3-highgpu-*"],
        "typical_use": "Cost-effective H100 80GB GPUs for FLEX_START jobs",
    },
}

# Ordered hardware tier fallback ladder for AlphaFold 3 Dedicated Endpoints
AF3_HARDWARE_TIER_LADDER: list[dict[str, Any]] = [
    {
        "tier": "H100_80GB",
        "machine_type": "a3-highgpu-1g",
        "accelerator_type": "NVIDIA_H100_80GB",
        "accelerator_count": 1,
        "hourly_rate": 11.06,
        "vram_gb": 80,
        "quota_metric": "NVIDIA_H100_GPUS",
        "description": "H100 80GB (preferred for Full 630 GB Hyperdisk MSA & large complexes)",
    },
    {
        "tier": "A100_80GB",
        "machine_type": "a2-ultragpu-1g",
        "accelerator_type": "NVIDIA_A100_80GB",
        "accelerator_count": 1,
        "hourly_rate": 5.00,
        "vram_gb": 80,
        "quota_metric": "NVIDIA_A100_80GB_GPUS",
        "description": "A100 80GB (80 GB VRAM fallback when H100 requires a reservation or is out of stock)",
    },
    {
        "tier": "A100_40GB",
        "machine_type": "a2-highgpu-1g",
        "accelerator_type": "NVIDIA_TESLA_A100",
        "accelerator_count": 1,
        "hourly_rate": 3.67,
        "vram_gb": 40,
        "quota_metric": "NVIDIA_A100_GPUS",
        "description": "A100 40GB (standard monomers & complexes <2,000 tokens)",
    },
    {
        "tier": "L4_24GB",
        "machine_type": "g2-standard-16",
        "accelerator_type": "NVIDIA_L4",
        "accelerator_count": 1,
        "hourly_rate": 1.01,
        "vram_gb": 24,
        "quota_metric": "NVIDIA_L4_GPUS",
        "description": "L4 24GB (widely available on-demand without reservation; ideal for --msa-free & <1,200 tokens)",
    },
]


def check_gpu_reservations(project_id: str, region: str) -> list[dict[str, Any]]:
    """Query Compute Engine API for active GPU reservations in the target region.

    Returns an empty list if no reservations exist or if the caller lacks
    `compute.reservations.list` permissions.
    """
    try:
        from google.cloud import compute_v1

        res_client = compute_v1.ReservationsClient()
        reservations: list[dict[str, Any]] = []
        zone_prefix = f"zones/{region}-"
        for scope_key, scoped_list in res_client.aggregated_list(project=project_id):
            if not str(scope_key).startswith(zone_prefix):
                continue
            zone = str(scope_key).split("/", 1)[-1]
            for r in getattr(scoped_list, "reservations", None) or []:
                status = getattr(r, "status", "")
                if status and str(status).upper() != "READY":
                    continue
                sku = getattr(r, "specific_reservation", None)
                if not sku:
                    continue
                total_count = int(getattr(sku, "count", 0) or 0)
                in_use_count = int(getattr(sku, "in_use_count", 0) or 0)
                avail_count = max(0, total_count - in_use_count)
                props = getattr(sku, "instance_properties", None)
                machine_type = getattr(props, "machine_type", "") if props else ""
                accels = []
                for acc in getattr(props, "guest_accelerators", None) or []:
                    acc_type = getattr(acc, "accelerator_type", "")
                    if acc_type:
                        accels.append(str(acc_type).split("/")[-1])
                name = getattr(r, "name", "")
                reservations.append(
                    {
                        "name": name,
                        "full_resource_name": f"projects/{project_id}/zones/{zone}/reservations/{name}",
                        "zone": zone,
                        "machine_type": machine_type,
                        "accelerator_types": accels,
                        "total_count": total_count,
                        "in_use_count": in_use_count,
                        "available_count": avail_count,
                        "specific_reservation_required": bool(
                            getattr(r, "specific_reservation_required", False)
                        ),
                    }
                )
        return reservations
    except Exception as exc:
        logger.debug(f"Could not query GCE reservations in {project_id}/{region}: {exc}")
        return []


def is_capacity_or_reservation_error(exc: Exception) -> bool:
    """Return True if an exception indicates GPU stockout, quota exhaustion, or missing reservation."""
    text = f"{type(exc).__name__} {exc}".upper()
    markers = (
        "RESOURCE_EXHAUSTED",
        "QUOTA",
        "STOCKOUT",
        "ZONE_RESOURCE_POOL_EXHAUSTED",
        "RESERVATION",
        "DOES NOT HAVE ENOUGH RESOURCES",
        "UNAVAILABLE IN",
        "INSUFFICIENT",
        "CAPACITY",
    )
    return any(m in text for m in markers)


def resolve_af3_hardware_plan(
    project_id: str,
    region: str,
    requested_machine_type: str = "a3-highgpu-1g",
    requested_accelerator_type: str = "NVIDIA_H100_80GB",
    requested_accelerator_count: int = 1,
    min_replicas: int = 1,
    reservation_affinity_type: str | None = None,
    reservation_names: list[str] | None = None,
    auto_fallback: bool = True,
) -> dict[str, Any]:
    """Build a reservation-aware hardware deployment plan and fallback chain for AlphaFold 3."""
    reservations = check_gpu_reservations(project_id, region)

    # Match active reservations for the requested machine_type
    matching_reservations = [
        r
        for r in reservations
        if r.get("machine_type") == requested_machine_type
        and int(r.get("available_count", 0)) >= min_replicas
    ]

    resolved_affinity_type = (reservation_affinity_type or "").strip().upper() or None
    resolved_affinity_key: str | None = None
    resolved_affinity_values: list[str] | None = None
    reservation_note = "no active GCE reservation matched (using on-demand capacity)"

    if reservation_names:
        resolved_affinity_type = resolved_affinity_type or "SPECIFIC_RESERVATION"
        resolved_affinity_key = "compute.googleapis.com/reservation-name"
        resolved_affinity_values = list(reservation_names)
        reservation_note = f"explicit reservation(s): {', '.join(resolved_affinity_values)}"
    elif resolved_affinity_type == "NO_RESERVATION":
        reservation_note = "explicit NO_RESERVATION (on-demand only)"
    elif resolved_affinity_type == "ANY_RESERVATION":
        reservation_note = (
            "explicit ANY_RESERVATION (consumes matching open reservations if present)"
        )
    elif matching_reservations:
        specific_matches = [
            r for r in matching_reservations if r.get("specific_reservation_required")
        ]
        if specific_matches:
            chosen = specific_matches[0]
            resolved_affinity_type = "SPECIFIC_RESERVATION"
            resolved_affinity_key = "compute.googleapis.com/reservation-name"
            resolved_affinity_values = [chosen["full_resource_name"]]
            reservation_note = (
                f"auto-attached SPECIFIC_RESERVATION '{chosen['full_resource_name']}' "
                f"({chosen['available_count']}/{chosen['total_count']} available)"
            )
        else:
            chosen = matching_reservations[0]
            resolved_affinity_type = "ANY_RESERVATION"
            reservation_note = (
                f"matched open reservation '{chosen['name']}' in {chosen['zone']} "
                f"({chosen['available_count']}/{chosen['total_count']} available)"
            )

    primary_candidate = {
        "tier": "PRIMARY",
        "machine_type": requested_machine_type,
        "accelerator_type": requested_accelerator_type,
        "accelerator_count": requested_accelerator_count,
        "hourly_rate": (
            11.06
            if ("a3" in requested_machine_type or "H100" in requested_accelerator_type)
            else 5.00
            if ("ultragpu" in requested_machine_type or "80GB" in requested_accelerator_type)
            else 3.67
            if ("a2" in requested_machine_type or "A100" in requested_accelerator_type)
            else 1.01
        ),
        "reservation_affinity_type": resolved_affinity_type,
        "reservation_affinity_key": resolved_affinity_key,
        "reservation_affinity_values": resolved_affinity_values,
    }

    candidates = [primary_candidate]
    if auto_fallback:
        for tier_spec in AF3_HARDWARE_TIER_LADDER:
            if (
                tier_spec["machine_type"] == requested_machine_type
                and tier_spec["accelerator_type"] == requested_accelerator_type
            ):
                continue
            candidates.append(
                {
                    "tier": tier_spec["tier"],
                    "machine_type": tier_spec["machine_type"],
                    "accelerator_type": tier_spec["accelerator_type"],
                    "accelerator_count": tier_spec["accelerator_count"],
                    "hourly_rate": tier_spec["hourly_rate"],
                    "reservation_affinity_type": None,
                    "reservation_affinity_key": None,
                    "reservation_affinity_values": None,
                }
            )

    return {
        "primary": primary_candidate,
        "candidates": candidates,
        "reservations": reservations,
        "matching_reservations": matching_reservations,
        "reservation_note": reservation_note,
    }


def check_gpu_quota(project_id: str, region: str) -> dict[str, Any]:
    """Query Compute Engine API and return GPU quota and active reservation information.

    Args:
        project_id: GCP project ID
        region: GCP region (e.g. 'us-central1')

    Returns:
        Dict with 'on_demand_gpus', 'preemptible_gpus', and 'reservations'.

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
    reservations = check_gpu_reservations(project_id, region)

    return {
        "on_demand_gpus": on_demand,
        "preemptible_gpus": preemptible,
        "reservations": reservations,
    }


def detect_supported_gpus(project_id: str, region: str, include_h100: bool = False) -> list[str]:
    """Detect GPU types with available quota in the given region.

    Args:
        project_id: GCP project ID
        region: GCP region
        include_h100: If True, also include 'H100' when H100 quota > 0.

    Returns:
        Ordered list of GPU type strings (['L4', 'A100', 'A100_80GB'] or with 'H100')
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
        elif "H100" in gpu_type and limit > 0:
            gpu_types_seen.add("H100")
        elif "A100_80GB" in gpu_type and limit > 0:
            gpu_types_seen.add("A100_80GB")
        elif "A100" in gpu_type and limit > 0:
            gpu_types_seen.add("A100")

    tier_order = (
        ["L4", "A100", "A100_80GB", "H100"] if include_h100 else ["L4", "A100", "A100_80GB"]
    )
    ordered = [g for g in tier_order if g in gpu_types_seen]
    return ordered
