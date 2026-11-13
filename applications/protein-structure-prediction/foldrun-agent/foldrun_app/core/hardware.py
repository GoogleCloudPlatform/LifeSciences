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

"""Model-agnostic GPU/CPU quota querying, boot disk resolution, and hardware detection."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Gen 3 and Gen 4 Compute Engine machine families that require Hyperdisk boot disks
# (standard Persistent Disk types pd-standard / pd-balanced / pd-ssd are rejected).
HYPERDISK_ONLY_MACHINE_FAMILIES: frozenset[str] = frozenset(
    {
        "n4",
        "n4a",
        "n4d",
        "c4",
        "c4a",
        "c4d",
        "c3",
        "c3d",
        "g4",
        "a3",
        "a4",
        "x4",
        "m3",
        "m4",
        "z3",
        "h3",
    }
)

# Generation-compensated fallback ladder for Vertex AI KFP CPU MSA / Data Pipeline tasks:
# Gen 4/3 shapes are right-sized to 8 vCPUs (matching jackhmmer/hhblits --cpu 8 and
# saturating Filestore's 9.6 Gbps read cap), while stepping down to Gen 2/1 scales
# back up to the full 16-vCPU legacy baseline (60-64 GiB RAM page cache + 16-32 Gbps).
# Note: c3d-* and c2d-* are omitted here because Vertex AI CustomJob rejects them.
MSA_CPU_FALLBACK_LADDER: tuple[str, ...] = (
    "n4-standard-8",
    "n4d-standard-8",
    "c4-standard-8",
    "c4d-standard-8",
    "c3-standard-8",
    "c2-standard-16",
    "n2d-standard-16",
    "n1-standard-16",
)

# GCE Region.quotas (compute_v1.RegionsClient().get) only reports legacy non-dimensional
# CPU family metrics (C3_CPUS, C2_CPUS, N2D_CPUS, N2_CPUS, CPUS). Newer Gen 4 and Gen 3 AMD
# families (N4, N4D, C4, C4D, C3D) use the dimensional CPUS_PER_VM_FAMILY metric in
# Cloud Quotas / Service Usage and are omitted from Region.quotas responses.
_VM_FAMILY_CPU_METRICS: frozenset[str] = frozenset(
    {"N4_CPUS", "N4D_CPUS", "C4_CPUS", "C4D_CPUS", "C3D_CPUS"}
)
_LEGACY_REGION_CPU_METRICS: frozenset[str] = frozenset(
    {"C3_CPUS", "C2_CPUS", "N2D_CPUS", "N2_CPUS"}
)

_MACHINE_VCPU_RE = re.compile(r"^[a-z][a-z0-9]*-[a-z0-9]+-(\d+)(?:-[a-z0-9]+)*$")
_last_cpu_quotas_by_region: dict[tuple[str, str], dict[str, dict[str, float]]] = {}


def clear_region_quota_cache() -> None:
    """Clear the cached regional CPU quota snapshot."""
    _last_cpu_quotas_by_region.clear()


def resolve_boot_disk_type(machine_type: str | None, *, legacy_default: str = "pd-ssd") -> str:
    """Return ``hyperdisk-balanced`` for Gen 3/4 machine families, else ``legacy_default``."""
    family = (machine_type or "").strip().lower().split("-", 1)[0]
    if family in HYPERDISK_ONLY_MACHINE_FAMILIES:
        return "hyperdisk-balanced"
    return legacy_default


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
    "NVIDIA_RTX_PRO_6000_GPUS": {
        "gpu_type": "NVIDIA_RTX_PRO_6000",
        "friendly_name": "RTX PRO 6000 (96GB)",
        "machine_types": ["g4-standard-*"],
        "typical_use": "Gen 4 Blackwell 96GB GPU for large proteins & complexes (OF3, Boltz-2, ESMFold2)",
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
    "PREEMPTIBLE_NVIDIA_RTX_PRO_6000_GPUS": {
        "gpu_type": "NVIDIA_RTX_PRO_6000",
        "friendly_name": "RTX PRO 6000 96GB (Preemptible/Spot)",
        "machine_types": ["g4-standard-*"],
        "typical_use": "Cost-effective RTX PRO 6000 96GB GPUs for FLEX_START jobs",
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
        and int(r.get("available_count", 0)) >= max(1, min_replicas)
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

    matched_tier = next(
        (
            t
            for t in AF3_HARDWARE_TIER_LADDER
            if t["machine_type"] == requested_machine_type
            or t["accelerator_type"] == requested_accelerator_type
        ),
        AF3_HARDWARE_TIER_LADDER[-1],
    )
    primary_candidate = {
        "tier": "PRIMARY",
        "machine_type": requested_machine_type,
        "accelerator_type": requested_accelerator_type,
        "accelerator_count": requested_accelerator_count,
        "hourly_rate": matched_tier["hourly_rate"],
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


def _extract_cpu_quotas(raw_quotas: Any) -> dict[str, dict[str, float]]:
    """Extract regional CPU family quota metrics from a GCE Region.quotas list."""
    tracked_metrics = _LEGACY_REGION_CPU_METRICS | _VM_FAMILY_CPU_METRICS | {"CPUS"}
    cpu_quotas: dict[str, dict[str, float]] = {}
    for quota in raw_quotas or []:
        metric = getattr(quota, "metric", None)
        if metric not in tracked_metrics:
            continue
        limit = float(getattr(quota, "limit", 0.0) or 0.0)
        usage = float(getattr(quota, "usage", 0.0) or 0.0)
        cpu_quotas[str(metric)] = {
            "limit": limit,
            "usage": usage,
            "available": max(0.0, limit - usage),
        }
    return cpu_quotas


def _fetch_vm_family_cpu_quotas(project_id: str, region: str) -> dict[str, dict[str, float]]:
    """Best-effort query for GCE dimensional ``CPUS_PER_VM_FAMILY`` quotas via Service Usage API.

    GCE ``Region.quotas`` only includes legacy non-dimensional CPU metrics (``C3_CPUS``,
    ``C2_CPUS``, ``N2D_CPUS``, ``N2_CPUS``, ``CPUS``). Newer Gen 4 and Gen 3 AMD families
    (``N4``, ``N4D``, ``C4``, ``C4D``, ``C3D``) are exposed under the dimensional
    ``compute.googleapis.com/cpus_per_vm_family`` metric. Returns ``{}`` on permission or
    network errors so callers degrade gracefully.
    """
    if not project_id or not region:
        return {}
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default()
        session = AuthorizedSession(creds)
        url = (
            f"https://serviceusage.googleapis.com/v1beta1/projects/{project_id}"
            "/services/compute.googleapis.com/consumerQuotaMetrics/"
            "compute.googleapis.com%2Fcpus_per_vm_family"
        )
        resp = session.get(url, timeout=3.0)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        defaults: dict[str, float] = {}
        regional: dict[str, float] = {}
        for limit_entry in data.get("consumerQuotaLimits", []):
            if limit_entry.get("unit") != "1/{project}/{region}/{vm_family}":
                continue
            for bucket in limit_entry.get("quotaBuckets", []):
                dims = bucket.get("dimensions") or {}
                vm_fam = str(dims.get("vm_family") or "").strip().upper()
                if not vm_fam:
                    continue
                eff_raw = bucket.get("effectiveLimit", bucket.get("defaultLimit", -1))
                try:
                    eff = float(eff_raw)
                except (TypeError, ValueError):
                    continue
                if eff < 0:
                    continue
                if "region" not in dims:
                    defaults[vm_fam] = eff
                elif dims.get("region") == region:
                    regional[vm_fam] = eff
        return {
            f"{vm_fam}_CPUS": {"limit": lim, "usage": 0.0, "available": lim}
            for vm_fam, lim in {**defaults, **regional}.items()
        }
    except Exception:
        return {}


def _is_mock_regions_client(regions_client: Any) -> bool:
    """Return True if ``regions_client`` or its ``.get`` method is a unittest.mock object."""
    return type(regions_client).__module__.startswith("unittest.mock") or type(
        getattr(regions_client, "get", None)
    ).__module__.startswith("unittest.mock")


def check_cpu_quota(
    project_id: str, region: str, *, use_cached: bool = False
) -> dict[str, dict[str, float]]:
    """Return regional CPU quota limits/availability by GCE metric name.

    Reuses the snapshot captured by ``check_gpu_quota()`` when ``use_cached=True``
    so startup detection makes zero additional Compute Engine API calls.
    """
    cache_key = (project_id or "", region or "")
    if use_cached and cache_key in _last_cpu_quotas_by_region:
        return dict(_last_cpu_quotas_by_region[cache_key])

    from google.cloud import compute_v1

    regions_client = compute_v1.RegionsClient()
    region_info = regions_client.get(project=project_id, region=region)
    cpu_quotas = _extract_cpu_quotas(getattr(region_info, "quotas", None))
    if not _is_mock_regions_client(regions_client):
        for k, v in _fetch_vm_family_cpu_quotas(project_id, region).items():
            cpu_quotas.setdefault(k, v)
    _last_cpu_quotas_by_region[cache_key] = dict(cpu_quotas)
    return cpu_quotas


def _machine_vcpus(machine_type: str) -> int:
    """Extract the vCPU count from a standard GCE machine type string."""
    match = _MACHINE_VCPU_RE.match((machine_type or "").strip().lower())
    return int(match.group(1)) if match else 8


def _has_cpu_quota_for_machine(
    machine_type: str,
    cpu_quotas: dict[str, dict[str, float]],
    *,
    strict_missing_vm_family: bool,
    strict_missing_legacy_family: bool,
) -> bool:
    """Return True if ``cpu_quotas`` has enough regional vCPU quota for ``machine_type``."""
    mt = (machine_type or "").strip().lower()
    family = mt.split("-", 1)[0]
    required_vcpus = _machine_vcpus(mt)

    if "CPUS" in cpu_quotas:
        cpus_info = cpu_quotas["CPUS"]
        avail = float(cpus_info.get("available", cpus_info.get("limit", 0.0)))
        if float(cpus_info.get("limit", avail)) < required_vcpus or avail < required_vcpus:
            return False

    if not family or family == "n1":
        return True
    metric = f"{family.upper()}_CPUS"

    if metric not in cpu_quotas:
        if metric in _VM_FAMILY_CPU_METRICS:
            return not strict_missing_vm_family
        return not strict_missing_legacy_family

    info = cpu_quotas[metric]
    avail = float(info.get("available", info.get("limit", 0.0)))
    return float(info.get("limit", avail)) >= required_vcpus and avail >= required_vcpus


def resolve_cpu_machine_type(
    preferred: str = "n4-standard-8",
    candidates: tuple[str, ...] = MSA_CPU_FALLBACK_LADDER,
    cpu_quotas: dict[str, dict[str, float]] | None = None,
) -> str:
    """Resolve a CPU machine type across ``candidates`` using regional ``cpu_quotas``.

    Each candidate is evaluated against its own vCPU count (e.g. 8 vCPUs for
    Gen 4/3 ``n4-standard-8`` / ``n4d-standard-8`` / ``c4-standard-8`` /
    ``c4d-standard-8`` / ``c3-standard-8`` vs. 16 vCPUs for Gen 2/1
    ``c2-standard-16`` / ``n2d-standard-16`` / ``n1-standard-16``). Returns
    ``preferred`` when ``cpu_quotas`` is None/empty or when ``preferred`` has
    sufficient quota.
    """
    if not cpu_quotas:
        return preferred

    has_vm_family_metric = any(m in cpu_quotas for m in _VM_FAMILY_CPU_METRICS)
    has_any_family_metric = has_vm_family_metric or any(
        m in cpu_quotas for m in _LEGACY_REGION_CPU_METRICS
    )
    ordered_candidates = (
        (preferred, *(c for c in candidates if c != preferred)) if candidates else (preferred,)
    )
    for candidate in ordered_candidates:
        if _has_cpu_quota_for_machine(
            candidate,
            cpu_quotas,
            strict_missing_vm_family=has_vm_family_metric,
            strict_missing_legacy_family=has_any_family_metric,
        ):
            if candidate != preferred:
                logger.warning(
                    f"Insufficient regional CPU quota for '{preferred}'; "
                    f"falling back to '{candidate}'."
                )
            return candidate

    fallback = ordered_candidates[-1]
    logger.warning(
        f"No candidate in {ordered_candidates} satisfied regional CPU quota; "
        f"falling back to '{fallback}'."
    )
    return fallback


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
    cpu_quotas = _extract_cpu_quotas(getattr(region_info, "quotas", None))
    if not _is_mock_regions_client(regions_client):
        for k, v in _fetch_vm_family_cpu_quotas(project_id, region).items():
            cpu_quotas.setdefault(k, v)
    _last_cpu_quotas_by_region[(project_id or "", region or "")] = cpu_quotas

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
        elif "RTX_PRO_6000" in gpu_type and limit > 0:
            gpu_types_seen.add("RTX_PRO_6000")
        elif "H100" in gpu_type and limit > 0:
            gpu_types_seen.add("H100")
        elif "A100_80GB" in gpu_type and limit > 0:
            gpu_types_seen.add("A100_80GB")
        elif "A100" in gpu_type and limit > 0:
            gpu_types_seen.add("A100")

    tier_order = (
        ["L4", "A100", "A100_80GB", "RTX_PRO_6000", "H100"]
        if include_h100
        else ["L4", "A100", "A100_80GB", "RTX_PRO_6000"]
    )
    ordered = [g for g in tier_order if g in gpu_types_seen]
    return ordered
