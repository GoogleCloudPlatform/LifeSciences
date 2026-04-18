# FoldRun: Enterprise Cost Estimate (12-Month Projection)

This document provides estimated costs for a large pharmaceutical or biotech organization migrating protein structure prediction workloads to FoldRun on Google Cloud.

> **Live cost calculator**: FoldRun includes `estimate_job_cost`, `estimate_monthly_cost`, and `get_actual_job_costs` agent tools. Ask the agent: *"Estimate the cost of a 500-residue multimer prediction"* or *"How much did my last job actually cost?"*

> **Important**: All figures in this document are **estimates based on Google Cloud public list pricing**, not actuals. Your organization's actual costs will depend on negotiated pricing agreements, committed-use discounts, sustained-use discounts, and real-world job runtimes. Always consult your billing console or Google Cloud contract for the rates that apply to your account.

**Prices verified**: 2026-04-15 via Cloud Billing Catalog API (us-central1).

## How the Cost Estimator Works

The FoldRun cost estimator models three factors that drive per-job cost:

### 1. Sequence length (protein complexity)

Predict and relax runtimes scale with sequence length. The estimator uses **piecewise-linear interpolation** across calibrated anchor points rather than coarse buckets, so a 300-residue protein gets a different (and more accurate) estimate than a 500-residue one.

Example: AF2 monomer on L4 GPU (DWS FLEX_START)

| Sequence Length | MSA | Predict/task | Relax/task | Est. Total |
|---|---|---|---|---|
| 50 residues | ~30 min | ~16 min | ~4 min | ~$0.80 |
| 100 residues | ~33 min | ~17 min | ~4.5 min | ~$0.87 |
| 300 residues | ~37 min | ~19 min | ~5 min | ~$0.97 |
| 500 residues | ~42 min | ~22 min | ~6 min | ~$1.12 |
| 1000 residues | ~55 min | ~35 min | ~10 min | ~$1.74 |
| 1500 residues | ~65 min | ~45 min | ~13 min | ~$2.22 |

Predict tasks have a **high baseline** (~16 min on L4 even for tiny proteins) due to model loading, feature preparation, and AMBER relaxation overhead. Runtime then scales roughly linearly with residue count.

### 2. Prediction count (monomer vs multimer vs OF3)

The total number of GPU-hours depends heavily on job type:

| Job Type | Models | Seeds/model | Total Predict Tasks | Total Relax Tasks |
|---|---|---|---|---|
| **AF2 monomer** | 5 | 1 (fixed) | **5** | **5** |
| **AF2 multimer** | 5 | 5 (default, configurable 1-25) | **25** | **25** |
| **OF3** | 1 | 5 (default) | **5** (each runs 5 diffusion samples) | None |

A multimer prediction runs **5x more GPU tasks** than a monomer at the same sequence length, making it roughly 5-6x more expensive. The `num_predictions_per_model` parameter controls this for multimers (default 5, can be reduced for screening or increased for high-confidence work).

Example: 400-residue protein on A100 (DWS FLEX_START)

| Job Type | Predict Tasks | Est. Cost |
|---|---|---|
| AF2 monomer | 5 | ~$2.21 |
| AF2 multimer (5 seeds/model) | 25 | ~$13.09 |
| AF2 multimer (1 seed/model) | 5 | ~$2.93 |
| AF2 multimer (10 seeds/model) | 50 | ~$27.68 |

### 3. GPU tier (auto-selected or user-specified)

The GPU is auto-selected based on sequence length, matching the submission tools:

| Sequence Length | AF2 Monomer GPU | AF2 Multimer GPU | OF3 GPU |
|---|---|---|---|
| <500 residues | L4 | A100 | A100 |
| 500-1500 residues | A100 | A100 | A100 |
| >1500 residues | A100 80GB | A100 80GB | A100 80GB |

Larger GPUs cost more per hour but can be required for large proteins that would OOM on smaller GPUs.

### Estimate vs Actual Accuracy

The estimator was calibrated against production job data from Vertex AI. Use `get_actual_job_costs` to compare:

| Metric (76-residue monomer, L4) | Estimated | Actual | Error |
|---|---|---|---|
| MSA duration | 31.6 min | 34.4 min | -8% |
| Predict per task | 16.5 min | 17.0 min | -3% |
| Relax per task | 4.3 min | 4.5 min | -4% |
| Total cost (FLEX_START) | $0.83 | $0.86 | -3% |

## Workload Assumptions

**Organization profile:**
- ~20-30 active drug programs (antibodies, proteins, peptides)
- Each program screening ~50-200 variants/month
- Mix of quick structure checks (AF2) and complex predictions (OF3, protein-ligand)
- Ramp: lighter months 1-3 during migration, full scale months 4-12

**Estimated monthly job volume at steady state:**

| Job Type | Jobs/month | Predict Tasks/job | Rationale |
|---|---|---|---|
| AF2 monomer (L4 GPU) | ~800 | 5 | Variant screening, quick structure checks |
| AF2 multimer (A100) | ~100 | 25 | Protein-protein complexes |
| OF3 predictions (A100) | ~200 | 5 seeds x 5 samples | Protein-ligand complexes, multi-chain |
| Re-runs / parameter sweeps | ~200 | varies | Additional seeds, different MSA methods |

## GCP List Pricing Reference (us-central1)

### Machine hourly rates

| Machine + GPU | On-Demand | DWS FLEX_START (spot) | Savings |
|---|---|---|---|
| c2-standard-16 (CPU, MSA pipeline) | $0.84/hr | $0.27/hr | 68% |
| g2-standard-12 + 1x L4 | $1.00/hr | $0.40/hr | 60% |
| a2-highgpu-1g + 1x A100 (40GB) | $3.67/hr | $1.80/hr | 51% |

### Storage

| Service | List Price |
|---|---|
| Filestore Basic SSD | $0.30/GiB-month |
| Cloud Storage Standard | $0.02/GiB-month |

## Always-On Infrastructure (estimated)

| Component | Est. Monthly | Est. 12-Month |
|---|---|---|
| Filestore (2.5TB BASIC_SSD) | $768 | $9,216 |
| GCS (~1TB backups + growing results) | $25 | $300 |
| Artifact Registry | $5 | $60 |
| Agent Engine | $50 | $600 |
| Cloud Run (viewer) | $20 | $240 |
| VPC / NAT | $45 | $540 |
| **Infrastructure subtotal** | **~$913** | **~$10,956** |

## Per-Job Cost Estimates

### AF2 monomer — L4 GPU, ~300 residues (5 predict + 5 relax tasks)

| Step | Machine | Duration | On-Demand | FLEX_START |
|---|---|---|---|---|
| MSA / Data Pipeline | c2-standard-16 | ~37 min | ~$0.52 | ~$0.17 |
| Predict (5 models) | g2-standard-12 + 1x L4 | ~19 min each | ~$1.58 | ~$0.63 |
| Relax (5 structures) | g2-standard-12 + 1x L4 | ~5 min each | ~$0.42 | ~$0.17 |
| **Total** | | | **~$2.52** | **~$0.97** |

### AF2 multimer — A100 GPU, ~500 residues total (25 predict + 25 relax tasks)

| Step | Machine | Duration | On-Demand | FLEX_START |
|---|---|---|---|---|
| MSA / Data Pipeline | c2-standard-16 | ~38 min | ~$0.53 | ~$0.17 |
| Predict (5 models x 5 seeds) | a2-highgpu-1g + 1x A100 | ~17 min each | ~$25.82 | ~$12.67 |
| Relax (25 structures) | g2-standard-12 + 1x L4 | ~5.5 min each | ~$2.29 | ~$0.92 |
| **Total** | | | **~$28.64** | **~$13.76** |

### OF3 prediction — A100 GPU, ~300 tokens, 5 seeds x 5 samples

| Step | Machine | Duration | On-Demand | FLEX_START |
|---|---|---|---|---|
| MSA Pipeline | c2-standard-16 | ~13 min | ~$0.18 | ~$0.06 |
| Predict (5 seeds x 5 samples) | a2-highgpu-1g + 1x A100 | ~2.5 min/sample | ~$3.82 | ~$1.88 |
| **Total** | | | **~$4.00** | **~$1.94** |

## Monthly Compute Estimates (steady state)

| Job Type | Jobs/mo | Tasks/job | On-Demand | FLEX_START |
|---|---|---|---|---|
| AF2 monomer, L4 (800/mo, ~300 res) | 800 | 5 | ~$1,792 | ~$696 |
| AF2 multimer, A100 (100/mo, ~500 res) | 100 | 25 | ~$2,553 | ~$1,226 |
| OF3, A100 (200/mo, ~300 tokens) | 200 | 5 | ~$802 | ~$388 |
| **Compute subtotal** | | | **~$5,147** | **~$2,310** |

## Other Costs (estimated)

| Item | Est. Monthly | Est. 12-Month |
|---|---|---|
| Gemini API (analysis, ~1,300 calls/mo) | ~$50 | ~$600 |
| Cloud Logging / Monitoring | ~$100 | ~$1,200 |
| Egress (downloading results) | ~$40 | ~$480 |
| Database re-downloads (quarterly refresh) | ~$17 | ~$200 |
| **Other subtotal** | **~$207** | **~$2,480** |

## 12-Month Summary

| Category | On-Demand Monthly | On-Demand Annual | FLEX_START Monthly | FLEX_START Annual |
|---|---|---|---|---|
| Infrastructure (always-on) | $913 | $10,956 | $913 | $10,956 |
| Compute (GPU predictions) | $5,147 | $61,764 | $2,310 | $27,720 |
| Other | $207 | $2,480 | $207 | $2,480 |
| **Total** | **~$6,267** | **~$75,200** | **~$3,430** | **~$41,156** |

FoldRun uses DWS FLEX_START by default, saving ~45% vs on-demand list pricing.

## Cost Optimization Levers

| Lever | Potential Savings |
|---|---|
| **DWS FLEX_START** (spot/preemptible, enabled by default) | ~45-60% on compute (reflected in FLEX_START column above) |
| **Fewer seeds per model** for multimer screening (1 instead of 5) | Up to 80% on multimer compute |
| **CUD reservations** (1yr/3yr committed GPU usage) | Additional 20-40% beyond list pricing |
| **Negotiated enterprise pricing** | Varies — check your Google Cloud contract |
| **Filestore lifecycle** (delete when idle, restore from GCS) | Up to $768/mo during idle periods |
| **Reduced BFD** vs full BFD | Smaller Filestore, faster MSA |

## Scaling Notes

- Costs scale linearly with job volume — 2x jobs = 2x compute cost
- Costs scale with protein size — a 1000-residue protein costs ~2x a 300-residue one
- Multimer jobs cost ~5-6x monomer at the same sequence length (25 vs 5 predict tasks)
- Infrastructure is mostly fixed regardless of job volume
- Multi-region or HA deployments roughly double infrastructure costs
- Additional models (Boltz) share most databases, adding minimal storage cost

## Comparison: Self-Managed vs FoldRun

Beyond infrastructure costs, FoldRun's agentic orchestration layer reduces the operational burden of managing prediction pipelines. Self-managed alternatives typically require:
- Custom pipeline orchestration and monitoring code
- Manual GPU provisioning and job scheduling
- Separate analysis and visualization tooling
- 1-2 FTEs of engineer/scientist time for pipeline maintenance

FoldRun provides all of this through a conversational AI interface backed by 26 native tools, deployed as a managed agent on Vertex AI.
