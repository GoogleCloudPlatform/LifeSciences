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

"""FoldRun cost estimation engine.

Calculates per-job and monthly costs using GCP pricing for the specific
machine types and GPUs used by FoldRun pipelines. Prices can be fetched
live from the Cloud Billing Catalog API or fall back to verified defaults.

Verified against Cloud Billing Catalog API on 2026-04-15.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GCP service IDs (Cloud Billing Catalog)
# ---------------------------------------------------------------------------
COMPUTE_ENGINE_SERVICE = "services/6F81-5844-456A"
FILESTORE_SERVICE = "services/D97E-AB26-5D95"
CLOUD_STORAGE_SERVICE = "services/95FF-2EF5-5EA1"

# ---------------------------------------------------------------------------
# Default prices — verified 2026-04-15 via Cloud Billing Catalog API
# All prices are per-hour unless noted. Region: us-central1.
# ---------------------------------------------------------------------------
DEFAULT_PRICES = {
    # --- On-demand (per hour) ---
    "c2_core": 0.033982,
    "c2_ram_gib": 0.004555,
    "g2_core": 0.024988,
    "g2_ram_gib": 0.002927,
    "a2_core": 0.031611,
    "a2_ram_gib": 0.004237,
    "nvidia_l4": 0.560040,
    "nvidia_a100": 2.933908,
    "nvidia_a100_80gb": 1.846198,
    # --- Spot / preemptible (per hour) ---
    "c2_core_spot": 0.011080,
    "c2_ram_gib_spot": 0.001478,
    "g2_core_spot": 0.010000,
    "g2_ram_gib_spot": 0.001171,
    "a2_core_spot": 0.015810,
    "a2_ram_gib_spot": 0.002118,
    "nvidia_l4_spot": 0.223100,
    "nvidia_a100_spot": 1.434100,
    "nvidia_a100_80gb_spot": 1.964000,
    # --- Storage (per GiB-month) ---
    "filestore_basic_ssd_gib_mo": 0.300000,
    "gcs_standard_gib_mo": 0.020000,
}

# ---------------------------------------------------------------------------
# Machine type specifications
# ---------------------------------------------------------------------------
MACHINE_SPECS = {
    "c2-standard-16": {"core_key": "c2", "vcpus": 16, "ram_gib": 64, "gpu_key": None, "gpu_count": 0},
    "g2-standard-12": {"core_key": "g2", "vcpus": 12, "ram_gib": 48, "gpu_key": None, "gpu_count": 0},
    "a2-highgpu-1g": {"core_key": "a2", "vcpus": 12, "ram_gib": 85, "gpu_key": None, "gpu_count": 0},
    "a2-ultragpu-1g": {"core_key": "a2", "vcpus": 12, "ram_gib": 170, "gpu_key": None, "gpu_count": 0},
}

GPU_PRICE_KEYS = {
    "L4": "nvidia_l4",
    "A100": "nvidia_a100",
    "A100_80GB": "nvidia_a100_80gb",
}

# Map Vertex AI accelerator type enum to our price key prefix
VERTEX_GPU_TO_PRICE_KEY = {
    "NVIDIA_L4": "nvidia_l4",
    "NVIDIA_TESLA_A100": "nvidia_a100",
    "NVIDIA_A100_80GB": "nvidia_a100_80gb",
}

# ---------------------------------------------------------------------------
# Duration model — piecewise-linear interpolation on sequence length
#
# Each entry is a list of (residue_count, msa_min, predict_per_task_min,
# relax_per_task_min) anchor points.  Durations are linearly interpolated
# between anchors; below the first or above the last anchor the nearest
# value is used (clamped).
#
# "predict_per_task_min" is per individual predict task:
#   - AF2 monomer:  5 tasks  (5 models × 1 seed)
#   - AF2 multimer: 25 tasks (5 models × num_predictions_per_model, default 5)
#   - OF3:          num_seeds tasks, each running num_diffusion_samples serially
#
# Anchors derived from production benchmarks and FoldRun documentation.
# ---------------------------------------------------------------------------
DURATION_ANCHORS = {
    "af2_monomer": {
        # Predict/relax have a high baseline (~16-17 min on L4 even for tiny
        # proteins) due to model loading, feature prep, and AMBER overhead.
        # Anchors calibrated against production actuals (76 residues:
        # MSA=34.4 min, predict=17.0 min, relax=4.5 min).
        "L4": [
            #  residues, msa, predict/task, relax/task
            (50,   30,  16,  4),
            (100,  33,  17,  4.5),
            (300,  37,  19,  5),
            (500,  42,  22,  6),
            (750,  48,  28,  8),
            (1000, 55,  35,  10),
            (1500, 65,  45, 13),
        ],
        "A100": [
            (50,   30,   8,  3),
            (100,  33,   9,  3.5),
            (300,  37,  11,  4),
            (500,  42,  14,  5),
            (1000, 55,  20,  6),
            (1500, 65,  28,  8),
        ],
        "A100_80GB": [
            (50,   30,   6,  3),
            (100,  33,   7,  3),
            (300,  37,   9,  3.5),
            (500,  42,  12,  4),
            (1000, 55,  17,  5),
            (2000, 70,  25,  6),
        ],
    },
    "af2_multimer": {
        # predict_per_task is per individual prediction (of 25 total by default)
        "A100": [
            (300,  35,  15,  5),
            (1000, 45,  22,  8),
            (2000, 55,  35, 12),
            (3000, 70,  50, 18),
        ],
        "A100_80GB": [
            (300,  35,  12,  4),
            (1000, 45,  18,  6),
            (2000, 55,  28,  8),
            (3000, 70,  40, 12),
        ],
    },
    "of3": {
        # predict_per_task is per seed (each seed runs num_diffusion_samples)
        "A100": [
            (100,  10,   1.5, 0),
            (300,  13,   2.5, 0),
            (600,  15,   5,   0),
            (1000, 18,   8,   0),
            (1500, 20,  12,   0),
            (2000, 25,  18,   0),
        ],
        "A100_80GB": [
            (100,  10,   1,   0),
            (300,  13,   2,   0),
            (600,  15,   4,   0),
            (1000, 18,   6,   0),
            (1500, 20,  10,   0),
            (2000, 25,  14,   0),
        ],
    },
}

# Number of predict tasks per job type
# AF2 monomer:  5 models × 1 seed = 5 tasks
# AF2 multimer: 5 models × num_predictions_per_model (default 5) = 25 tasks
# OF3:          num_seeds tasks (each runs num_diffusion_samples serially)
DEFAULT_PREDICT_TASKS = {
    "af2_monomer": 5,
    "af2_multimer": 25,
    "of3": 5,
}

# Infrastructure monthly costs (always-on, independent of job volume)
INFRASTRUCTURE_MONTHLY = {
    "filestore_2_5tb_basic_ssd": {"description": "Filestore (2.5 TB Basic SSD)", "cost": 768.00},
    "gcs_storage": {"description": "Cloud Storage (~1 TB backups + results)", "cost": 25.00},
    "artifact_registry": {"description": "Artifact Registry", "cost": 5.00},
    "agent_engine": {"description": "Agent Engine", "cost": 50.00},
    "cloud_run_viewer": {"description": "Cloud Run (viewer)", "cost": 20.00},
    "vpc_nat": {"description": "VPC / NAT", "cost": 45.00},
}

# Other monthly costs that scale loosely with usage
OTHER_MONTHLY = {
    "gemini_api": {"description": "Gemini API (~1,300 calls/mo)", "cost": 50.00},
    "logging_monitoring": {"description": "Cloud Logging / Monitoring", "cost": 100.00},
    "egress": {"description": "Egress (downloading results)", "cost": 40.00},
    "db_refresh": {"description": "Database re-downloads (amortized)", "cost": 17.00},
}


def _interpolate_durations(
    job_type: str, gpu_type: str, sequence_length: int,
) -> tuple:
    """Interpolate (msa_min, predict_per_task_min, relax_per_task_min) from anchors.

    Uses piecewise linear interpolation between anchor points. Values are
    clamped at the first/last anchor for out-of-range sequence lengths.
    """
    anchors = DURATION_ANCHORS.get(job_type, {}).get(gpu_type)
    if not anchors:
        # Fall back to first available GPU for this job type
        available = DURATION_ANCHORS.get(job_type, {})
        gpu_type = next(iter(available), "A100")
        anchors = available.get(gpu_type, [(300, 35, 15, 5)])

    # Clamp below first anchor
    if sequence_length <= anchors[0][0]:
        return anchors[0][1], anchors[0][2], anchors[0][3]

    # Clamp above last anchor
    if sequence_length >= anchors[-1][0]:
        return anchors[-1][1], anchors[-1][2], anchors[-1][3]

    # Find surrounding anchors and interpolate
    for i in range(len(anchors) - 1):
        lo_len, lo_msa, lo_pred, lo_relax = anchors[i]
        hi_len, hi_msa, hi_pred, hi_relax = anchors[i + 1]
        if lo_len <= sequence_length <= hi_len:
            t = (sequence_length - lo_len) / (hi_len - lo_len)
            msa = lo_msa + t * (hi_msa - lo_msa)
            pred = lo_pred + t * (hi_pred - lo_pred)
            relax = lo_relax + t * (hi_relax - lo_relax)
            return round(msa, 1), round(pred, 1), round(relax, 1)

    # Shouldn't reach here, but fall back to last anchor
    return anchors[-1][1], anchors[-1][2], anchors[-1][3]


def _auto_select_gpu(job_type: str, sequence_length: int) -> str:
    """Auto-select GPU based on job type and sequence length.

    Mirrors the logic in FoldRun's submission tools.
    """
    if job_type == "of3":
        return "A100" if sequence_length <= 2000 else "A100_80GB"
    elif job_type == "af2_multimer":
        return "A100" if sequence_length < 1000 else "A100_80GB"
    else:  # af2_monomer
        if sequence_length < 500:
            return "L4"
        elif sequence_length <= 1500:
            return "A100"
        return "A100_80GB"


def _relax_gpu(predict_gpu: str) -> str:
    """Determine relax GPU (downgraded from predict GPU)."""
    return {
        "A100_80GB": "A100",
        "A100": "L4",
        "L4": "L4",
    }.get(predict_gpu, "L4")


def _machine_for_gpu(gpu_type: str) -> str:
    """Return the machine type paired with a GPU."""
    if gpu_type == "L4":
        return "g2-standard-12"
    return "a2-highgpu-1g"


def _hourly_rate(machine_type: str, gpu_type: Optional[str], gpu_count: int,
                 spot: bool, prices: dict) -> float:
    """Calculate the hourly rate for a machine + GPU combo."""
    spec = MACHINE_SPECS[machine_type]
    suffix = "_spot" if spot else ""
    core_price = prices.get(f"{spec['core_key']}_core{suffix}", 0)
    ram_price = prices.get(f"{spec['core_key']}_ram_gib{suffix}", 0)
    vm_cost = core_price * spec["vcpus"] + ram_price * spec["ram_gib"]

    gpu_cost = 0.0
    if gpu_type and gpu_count > 0:
        gpu_price_key = GPU_PRICE_KEYS.get(gpu_type, "")
        if gpu_price_key:
            gpu_cost = prices.get(f"{gpu_price_key}{suffix}", 0) * gpu_count

    return vm_cost + gpu_cost


def fetch_live_prices(region: str = "us-central1") -> Optional[dict]:
    """Fetch live prices from the Cloud Billing Catalog API.

    Returns a price dict in the same format as DEFAULT_PRICES, or None on failure.
    """
    try:
        from google.cloud import billing_v1
        client = billing_v1.CloudCatalogClient()

        prices = {}
        for sku in client.list_skus(parent=COMPUTE_ENGINE_SERVICE):
            if region not in sku.service_regions:
                continue
            desc = sku.description.lower()
            if not sku.pricing_info:
                continue
            expr = sku.pricing_info[0].pricing_expression
            if not expr.tiered_rates:
                continue
            rate = expr.tiered_rates[-1]
            price = int(rate.unit_price.units) + rate.unit_price.nanos / 1e9

            key = _match_sku(desc)
            if key:
                prices[key] = price

        # Fetch Filestore prices
        for sku in client.list_skus(parent=FILESTORE_SERVICE):
            if region not in sku.service_regions:
                continue
            desc = sku.description.lower()
            if "basic" in desc and "ssd" in desc and "capacity" in desc:
                if not sku.pricing_info:
                    continue
                expr = sku.pricing_info[0].pricing_expression
                if expr.tiered_rates:
                    rate = expr.tiered_rates[-1]
                    prices["filestore_basic_ssd_gib_mo"] = (
                        int(rate.unit_price.units) + rate.unit_price.nanos / 1e9
                    )

        if len(prices) >= 10:
            logger.info(f"Fetched {len(prices)} live prices for {region}")
            return prices
        logger.warning(f"Only found {len(prices)} prices, falling back to defaults")
        return None

    except Exception as e:
        logger.info(f"Live pricing unavailable ({e}), using defaults")
        return None


def _match_sku(desc: str) -> Optional[str]:
    """Match a SKU description to a price key."""
    is_spot = "spot" in desc or "preemptible" in desc
    is_commitment = "commitment" in desc
    is_sole = "sole" in desc
    is_vws = "virtual workstation" in desc

    if is_commitment or is_sole or is_vws:
        return None

    suffix = "_spot" if is_spot else ""

    if "compute optimized" in desc:
        if "core" in desc:
            return f"c2_core{suffix}"
        if "ram" in desc:
            return f"c2_ram_gib{suffix}"
    elif "g2 instance core" in desc:
        return f"g2_core{suffix}"
    elif "g2 instance ram" in desc:
        return f"g2_ram_gib{suffix}"
    elif "a2 instance core" in desc:
        return f"a2_core{suffix}"
    elif "a2 instance ram" in desc:
        return f"a2_ram_gib{suffix}"
    elif "nvidia l4 gpu" in desc:
        return f"nvidia_l4{suffix}"
    elif "nvidia tesla a100 80gb gpu" in desc:
        return f"nvidia_a100_80gb{suffix}"
    elif "nvidia tesla a100 gpu" in desc:
        return f"nvidia_a100{suffix}"
    return None


# Session-level cache
_cached_prices = None


def get_prices(region: str = "us-central1") -> dict:
    """Get pricing data, using cached live prices if available."""
    global _cached_prices
    if _cached_prices is not None:
        return _cached_prices

    live = fetch_live_prices(region)
    if live:
        merged = {**DEFAULT_PRICES, **live}
        _cached_prices = merged
        return merged

    _cached_prices = DEFAULT_PRICES.copy()
    return _cached_prices


DISCLAIMER = (
    "These are estimates based on Google Cloud list pricing, not actuals. "
    "Actual costs will vary based on sequence complexity, runtime, and your "
    "organization's pricing agreement with Google Cloud. Check your contract "
    "or billing console for negotiated rates, committed-use discounts, or "
    "enterprise pricing that may apply."
)


def _estimate_phases(
    job_type: str,
    gpu_type: str,
    sequence_length: int,
    num_predictions: int,
    num_diffusion_samples: int,
    spot: bool,
    prices: dict,
) -> list:
    """Build the per-phase cost breakdown for a single pricing mode.

    Args:
        job_type: "af2_monomer", "af2_multimer", or "of3"
        gpu_type: Resolved GPU type
        sequence_length: Residue/token count (drives duration interpolation)
        num_predictions: Total predict tasks (5 for monomer, 25 for multimer,
            num_seeds for OF3)
        num_diffusion_samples: OF3 only — samples per seed (run serially)
        spot: Use spot pricing
        prices: Price dict
    """
    msa_min, predict_per_task_min, relax_per_task_min = _interpolate_durations(
        job_type, gpu_type, sequence_length,
    )

    phases = []

    # Phase 1: MSA / Data Pipeline (always CPU)
    msa_machine = "c2-standard-16"
    msa_rate = _hourly_rate(msa_machine, None, 0, spot, prices)
    msa_cost = (msa_min / 60) * msa_rate
    phases.append({
        "phase": "MSA / Data Pipeline",
        "machine_type": msa_machine,
        "gpu": "None (CPU, Jackhmmer)",
        "duration_minutes": round(msa_min, 1),
        "hourly_rate": round(msa_rate, 4),
        "cost": round(msa_cost, 2),
    })

    # Phase 2: Predict
    predict_machine = _machine_for_gpu(gpu_type)
    predict_rate = _hourly_rate(predict_machine, gpu_type, 1, spot, prices)

    if job_type == "of3":
        # OF3: each seed task runs num_diffusion_samples sequentially
        predict_total_min = predict_per_task_min * num_diffusion_samples * num_predictions
        predict_desc = f"{num_predictions} seeds x {num_diffusion_samples} samples"
    elif job_type == "af2_multimer":
        predict_total_min = predict_per_task_min * num_predictions
        predict_desc = f"5 models x {num_predictions // 5} seeds = {num_predictions} predictions"
    else:
        predict_total_min = predict_per_task_min * num_predictions
        predict_desc = f"{num_predictions} models"

    predict_cost = (predict_total_min / 60) * predict_rate
    phases.append({
        "phase": "Predict",
        "machine_type": predict_machine,
        "gpu": f"{gpu_type} x 1",
        "duration_minutes": round(predict_total_min, 1),
        "detail": predict_desc,
        "hourly_rate": round(predict_rate, 4),
        "cost": round(predict_cost, 2),
    })

    # Phase 3: Relax (AF2 only — OF3 has no relax step)
    if job_type.startswith("af2") and relax_per_task_min > 0:
        relax_gpu = _relax_gpu(gpu_type)
        relax_machine = _machine_for_gpu(relax_gpu)
        relax_rate = _hourly_rate(relax_machine, relax_gpu, 1, spot, prices)
        relax_total_min = relax_per_task_min * num_predictions
        relax_cost = (relax_total_min / 60) * relax_rate
        phases.append({
            "phase": "Relax",
            "machine_type": relax_machine,
            "gpu": f"{relax_gpu} x 1",
            "duration_minutes": round(relax_total_min, 1),
            "detail": f"{num_predictions} structures",
            "hourly_rate": round(relax_rate, 4),
            "cost": round(relax_cost, 2),
        })

    return phases


def estimate_single_job(
    job_type: str,
    gpu_type: str = "auto",
    sequence_length: int = 300,
    num_predictions_per_model: int = 5,
    num_diffusion_samples: int = 5,
    region: str = "us-central1",
) -> dict:
    """Estimate the cost of a single FoldRun prediction job.

    Returns both on-demand and DWS FLEX_START (spot/preemptible) pricing
    side by side so users can compare. Durations are interpolated based on
    sequence length for continuous cost scaling.

    Args:
        job_type: "af2_monomer", "af2_multimer", or "of3"
        gpu_type: "L4", "A100", "A100_80GB", or "auto"
        sequence_length: Residue/token count (drives duration estimation
            and GPU auto-selection — larger proteins take longer)
        num_predictions_per_model: For AF2 multimer, number of predictions
            per model (default 5, giving 5 models x 5 = 25 total). For AF2
            monomer, always 1 (5 models x 1 = 5 total). For OF3, this is
            the number of seeds (default 5).
        num_diffusion_samples: OF3 only — diffusion samples per seed
            (default 5, run serially on the same GPU). Ignored for AF2.
        region: GCP region

    Returns:
        Dict with per-phase breakdown for both pricing modes, totals, and metadata.
    """
    prices = get_prices(region)

    if gpu_type == "auto":
        gpu_type = _auto_select_gpu(job_type, sequence_length)

    # Determine total number of predict (and relax) tasks
    if job_type == "af2_monomer":
        num_predictions = 5  # always 5 models × 1 seed
    elif job_type == "af2_multimer":
        num_predictions = 5 * num_predictions_per_model  # 5 models × N seeds
    else:  # of3
        num_predictions = num_predictions_per_model  # num_seeds

    # Build phases for both pricing modes
    spot_phases = _estimate_phases(
        job_type, gpu_type, sequence_length, num_predictions,
        num_diffusion_samples, True, prices,
    )
    ondemand_phases = _estimate_phases(
        job_type, gpu_type, sequence_length, num_predictions,
        num_diffusion_samples, False, prices,
    )

    spot_total = round(sum(p["cost"] for p in spot_phases), 2)
    ondemand_total = round(sum(p["cost"] for p in ondemand_phases), 2)
    total_minutes = round(sum(p["duration_minutes"] for p in spot_phases), 1)
    savings_pct = round((1 - spot_total / ondemand_total) * 100) if ondemand_total > 0 else 0

    return {
        "job_type": job_type,
        "gpu_type": gpu_type,
        "sequence_length": sequence_length,
        "num_predict_tasks": num_predictions,
        "num_diffusion_samples": num_diffusion_samples if job_type == "of3" else None,
        "region": region,
        "total_duration_minutes": total_minutes,
        "on_demand": {
            "phases": ondemand_phases,
            "estimated_total": ondemand_total,
        },
        "dws_flex_start": {
            "phases": spot_phases,
            "estimated_total": spot_total,
        },
        "flex_start_savings_pct": savings_pct,
        "default_mode": "dws_flex_start",
        "disclaimer": DISCLAIMER,
    }


def _compute_items_for_mode(
    job_configs: list,
    spot: bool,
    region: str,
    prices: dict,
) -> tuple:
    """Calculate compute line items for a single pricing mode."""
    items = []
    total = 0.0

    for job_type, count, seq_len in job_configs:
        if count <= 0:
            continue

        gpu_type = _auto_select_gpu(job_type, seq_len)
        num_predictions = DEFAULT_PREDICT_TASKS.get(job_type, 5)
        phases = _estimate_phases(
            job_type, gpu_type, seq_len, num_predictions, 5, spot, prices
        )
        per_job = round(sum(p["cost"] for p in phases), 2)
        monthly = round(per_job * count, 2)
        total += monthly
        items.append({
            "job_type": job_type,
            "jobs_per_month": count,
            "estimated_cost_per_job": per_job,
            "gpu_type": gpu_type,
            "num_predict_tasks": num_predictions,
            "estimated_monthly": monthly,
        })

    return items, round(total, 2)


def estimate_monthly(
    af2_monomer_jobs: int = 0,
    af2_multimer_jobs: int = 0,
    of3_jobs: int = 0,
    avg_sequence_length: int = 300,
    include_infrastructure: bool = True,
    region: str = "us-central1",
) -> dict:
    """Estimate monthly and annual FoldRun costs.

    Returns both on-demand and DWS FLEX_START (spot/preemptible) estimates
    side by side. Infrastructure and other costs are the same in both modes.

    Uses default prediction counts:
    - AF2 monomer: 5 models x 1 seed = 5 predictions per job
    - AF2 multimer: 5 models x 5 seeds = 25 predictions per job
    - OF3: 5 seeds x 5 diffusion samples = 25 structures per job

    Args:
        af2_monomer_jobs: Monthly AF2 monomer job count
        af2_multimer_jobs: Monthly AF2 multimer job count
        of3_jobs: Monthly OF3 job count
        avg_sequence_length: Average sequence length for cost estimation
            (drives duration interpolation and GPU auto-selection)
        include_infrastructure: Include always-on infrastructure costs (default True)
        region: GCP region

    Returns:
        Dict with compute (both modes), infrastructure, other breakdowns,
        monthly and annual totals for both modes.
    """
    prices = get_prices(region)

    job_configs = [
        ("af2_monomer", af2_monomer_jobs, avg_sequence_length),
        ("af2_multimer", af2_multimer_jobs, avg_sequence_length),
        ("of3", of3_jobs, avg_sequence_length),
    ]

    spot_items, spot_compute = _compute_items_for_mode(
        job_configs, True, region, prices,
    )
    od_items, od_compute = _compute_items_for_mode(
        job_configs, False, region, prices,
    )

    # Infrastructure (same regardless of pricing mode)
    infra_total = 0.0
    infra_items = []
    if include_infrastructure:
        for key, item in INFRASTRUCTURE_MONTHLY.items():
            infra_total += item["cost"]
            infra_items.append({
                "item": item["description"],
                "monthly_cost": item["cost"],
            })

    # Other costs (same regardless of pricing mode)
    other_total = sum(item["cost"] for item in OTHER_MONTHLY.values())
    other_items = [
        {"item": item["description"], "monthly_cost": item["cost"]}
        for item in OTHER_MONTHLY.values()
    ]

    fixed_monthly = infra_total + other_total

    spot_monthly = spot_compute + fixed_monthly
    od_monthly = od_compute + fixed_monthly
    savings_pct = round((1 - spot_monthly / od_monthly) * 100) if od_monthly > 0 else 0

    return {
        "region": region,
        "on_demand": {
            "compute_items": od_items,
            "estimated_compute_monthly": od_compute,
            "estimated_monthly_total": round(od_monthly, 2),
            "estimated_annual_total": round(od_monthly * 12, 2),
        },
        "dws_flex_start": {
            "compute_items": spot_items,
            "estimated_compute_monthly": spot_compute,
            "estimated_monthly_total": round(spot_monthly, 2),
            "estimated_annual_total": round(spot_monthly * 12, 2),
        },
        "infrastructure": {
            "items": infra_items,
            "monthly_total": round(infra_total, 2),
        },
        "other": {
            "items": other_items,
            "monthly_total": round(other_total, 2),
        },
        "flex_start_savings_pct": savings_pct,
        "default_mode": "dws_flex_start",
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Actual cost retrieval from Vertex AI job history
# ---------------------------------------------------------------------------

def _job_hourly_rate(machine_type: str, gpu_enum: Optional[str], gpu_count: int,
                     is_spot: bool, prices: dict) -> float:
    """Calculate hourly rate for a Vertex AI custom job's hardware config.

    Args:
        machine_type: GCE machine type (e.g. "g2-standard-12")
        gpu_enum: Vertex AI accelerator type enum (e.g. "NVIDIA_L4") or None
        gpu_count: Number of GPUs attached
        is_spot: Whether the job used FLEX_START (spot) scheduling
        prices: Price dict from get_prices()
    """
    spec = MACHINE_SPECS.get(machine_type)
    if not spec:
        logger.warning(f"Unknown machine type {machine_type}, cannot price")
        return 0.0

    suffix = "_spot" if is_spot else ""
    core_price = prices.get(f"{spec['core_key']}_core{suffix}", 0)
    ram_price = prices.get(f"{spec['core_key']}_ram_gib{suffix}", 0)
    vm_cost = core_price * spec["vcpus"] + ram_price * spec["ram_gib"]

    gpu_cost = 0.0
    if gpu_enum and gpu_count > 0:
        gpu_price_key = VERTEX_GPU_TO_PRICE_KEY.get(gpu_enum)
        if gpu_price_key:
            gpu_cost = prices.get(f"{gpu_price_key}{suffix}", 0) * gpu_count

    return vm_cost + gpu_cost


def get_actual_costs(
    project_id: str,
    region: str = "us-central1",
    pipeline_job_id: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Retrieve actual costs for completed Vertex AI jobs.

    Fetches custom job metadata from the Vertex AI API, calculates costs
    from actual runtimes and machine specs, and groups results by pipeline run.

    Args:
        project_id: GCP project ID
        region: GCP region
        pipeline_job_id: Optional pipeline job ID to filter for a specific run.
            If None, returns costs for all recent jobs.
        limit: Max jobs to retrieve (default 50)

    Returns:
        Dict with pipeline runs, per-phase actual costs, and totals.
    """
    from google.cloud import aiplatform_v1

    prices = get_prices(region)

    client = aiplatform_v1.JobServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )
    parent = f"projects/{project_id}/locations/{region}"

    # Build filter for FoldRun jobs
    filter_str = 'labels.submitted_by="foldrun-agent"'
    if pipeline_job_id:
        filter_str += (
            f' AND labels.vertex-ai-pipelines-run-billing-id="{pipeline_job_id}"'
        )

    request = aiplatform_v1.ListCustomJobsRequest(
        parent=parent,
        filter=filter_str,
        page_size=min(limit, 100),
    )

    # Collect all succeeded jobs
    all_jobs = []
    for job in client.list_custom_jobs(request=request):
        if job.state != aiplatform_v1.JobState.JOB_STATE_SUCCEEDED:
            continue
        # Skip Vertex AI pipeline orchestrator jobs (not actual compute)
        if job.display_name.startswith("caip_pipelines_"):
            continue
        all_jobs.append(job)
        if len(all_jobs) >= limit:
            break

    # Group by pipeline billing ID
    pipelines: dict = {}
    for job in all_jobs:
        labels = dict(job.labels) if job.labels else {}
        billing_id = labels.get(
            "vertex-ai-pipelines-run-billing-id", "unknown"
        )

        if billing_id not in pipelines:
            pipelines[billing_id] = {
                "model_type": labels.get("model_type", "unknown"),
                "job_type": labels.get("job_type", "unknown"),
                "seq_name": labels.get("seq_name", "unknown"),
                "seq_len": labels.get("seq_len", "unknown"),
                "gpu_type": labels.get("gpu_type", "unknown"),
                "tasks": [],
            }

        # Extract hardware config from worker pool spec
        machine_type = "unknown"
        gpu_enum = None
        gpu_count = 0
        is_spot = False

        if job.job_spec and job.job_spec.worker_pool_specs:
            pool = job.job_spec.worker_pool_specs[0]
            if pool.machine_spec:
                machine_type = pool.machine_spec.machine_type
                accel = pool.machine_spec.accelerator_type
                if accel and accel.name != "ACCELERATOR_TYPE_UNSPECIFIED":
                    gpu_enum = accel.name
                gpu_count = pool.machine_spec.accelerator_count

        if job.job_spec and job.job_spec.scheduling:
            strategy = job.job_spec.scheduling.strategy
            if strategy and strategy.name == "FLEX_START":
                is_spot = True

        # Calculate actual duration and cost
        start_time = job.create_time
        end_time = job.end_time
        duration_min = 0.0
        if start_time and end_time:
            duration_min = (end_time - start_time).total_seconds() / 60

        hourly_rate_od = _job_hourly_rate(
            machine_type, gpu_enum, gpu_count, False, prices
        )
        hourly_rate_spot = _job_hourly_rate(
            machine_type, gpu_enum, gpu_count, True, prices
        )
        actual_rate = hourly_rate_spot if is_spot else hourly_rate_od
        actual_cost = (duration_min / 60) * actual_rate

        gpu_str = f"{gpu_enum} x{gpu_count}" if gpu_enum else "None (CPU)"

        pipelines[billing_id]["tasks"].append({
            "phase": job.display_name,
            "machine_type": machine_type,
            "gpu": gpu_str,
            "scheduling": "FLEX_START" if is_spot else "ON_DEMAND",
            "actual_duration_minutes": round(duration_min, 1),
            "hourly_rate": round(actual_rate, 4),
            "actual_cost": round(actual_cost, 2),
            "on_demand_cost": round((duration_min / 60) * hourly_rate_od, 2),
            "flex_start_cost": round((duration_min / 60) * hourly_rate_spot, 2),
        })

    # Build pipeline run summaries
    runs = []
    grand_total_actual = 0.0
    grand_total_od = 0.0
    grand_total_spot = 0.0

    for billing_id, pipeline in sorted(
        pipelines.items(), key=lambda x: x[0]
    ):
        tasks = pipeline["tasks"]
        run_actual = sum(t["actual_cost"] for t in tasks)
        run_od = sum(t["on_demand_cost"] for t in tasks)
        run_spot = sum(t["flex_start_cost"] for t in tasks)
        total_min = sum(t["actual_duration_minutes"] for t in tasks)

        grand_total_actual += run_actual
        grand_total_od += run_od
        grand_total_spot += run_spot

        # Compare against estimate if we have enough metadata
        estimate_comparison = None
        model = pipeline["model_type"]
        seq_len_str = pipeline["seq_len"]
        if seq_len_str and seq_len_str.isdigit():
            seq_len = int(seq_len_str)
            jt = None
            if model == "alphafold2":
                jt = (
                    "af2_monomer"
                    if pipeline["job_type"] == "monomer"
                    else "af2_multimer"
                )
            elif model == "openfold3":
                jt = "of3"

            if jt:
                est = estimate_single_job(jt, "auto", seq_len)
                est_spot = est["dws_flex_start"]["estimated_total"]
                est_od = est["on_demand"]["estimated_total"]
                estimate_comparison = {
                    "estimated_on_demand": est_od,
                    "estimated_flex_start": est_spot,
                    "actual_total": round(run_actual, 2),
                    "estimate_accuracy_pct": round(
                        (1 - abs(run_spot - est_spot) / est_spot) * 100
                    ) if est_spot > 0 else None,
                }

        runs.append({
            "pipeline_id": billing_id,
            "model": model,
            "job_type": pipeline["job_type"],
            "sequence_name": pipeline["seq_name"],
            "sequence_length": pipeline["seq_len"],
            "gpu_type": pipeline["gpu_type"],
            "tasks": tasks,
            "actual_total_cost": round(run_actual, 2),
            "on_demand_equivalent": round(run_od, 2),
            "flex_start_equivalent": round(run_spot, 2),
            "total_duration_minutes": round(total_min, 1),
            "estimate_comparison": estimate_comparison,
        })

    return {
        "project_id": project_id,
        "region": region,
        "pipeline_runs": runs,
        "summary": {
            "total_runs": len(runs),
            "total_tasks": sum(len(r["tasks"]) for r in runs),
            "total_actual_cost": round(grand_total_actual, 2),
            "total_on_demand_equivalent": round(grand_total_od, 2),
            "total_flex_start_equivalent": round(grand_total_spot, 2),
        },
        "disclaimer": DISCLAIMER,
    }
