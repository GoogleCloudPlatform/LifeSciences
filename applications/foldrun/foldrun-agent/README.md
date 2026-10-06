# FoldRun Agent

AI-powered biomolecular structure prediction assistant supporting **AlphaFold 2**, **AlphaFold 3**, **OpenFold 3**, and **Boltz-2** via a conversational interface powered by Google ADK and Gemini.

## Overview

![FoldRun Architecture](../img/foldrun-architecture.png)

FoldRun Agent is a modular, stateful conversational agent for biomolecular structure prediction. It manages the full prediction lifecycle through natural language:

- Submit structure predictions across **AlphaFold 2** (monomers/multimers), **AlphaFold 3** (all-atom proteins, multimers, dsDNA, RNA, CCD ligands, SMILES inhibitors, and metal ions via a 4-stage KFP pipeline + managed H100 endpoint), **OpenFold 3**, and **Boltz-2**
- Coordinate replica-aware FIFO queueing and automatic 20-minute idle shutdown (`$0.00/hr` when idle) on the AlphaFold 3 H100 endpoint
- Monitor job progress across all 4 stages in Vertex AI Pipelines
- Run parallel structural analysis and multimodal **Gemini 3.1 Pro** (`gemini-3.1-pro-preview`) Expert Analysis
- Visualize results in the interactive 3D viewer and generate 60-minute V4 Signed URLs for complete job artifact bundles (`artifacts_bundle.zip`)
- Query the AlphaFold Database for existing structures
- Estimate and track compute costs

Built with **Google ADK** using **native Skills** — all skills run directly within the agent process, no external server dependency.

## Model Selection Guide

| Need | Model |
|------|-------|
| All-atom complex (protein, multimer, dsDNA, RNA, CCD ligand, SMILES drug, metal ions) with fastest turnaround | **AlphaFold 3** (`submit_af3_endpoint_prediction` / `submit_af3_batch_predictions` — ~3–6 min Full-MSA or ~58s `--msa-free` on warm H100) |
| Protein-only (single chain, classic AMBER relaxation) | **AlphaFold 2** monomer |
| Protein complex (multiple chains, 25-model ensemble) | **AlphaFold 2** multimer |
| Protein + RNA, DNA, or small-molecule ligand (open-weights Apache 2.0 pipeline) | **OpenFold 3** (has full RNA MSA via `nhmmer`) |
| Covalent modifications, glycans, or IC50/ΔG binding affinity | **Boltz-2** |

## Features

- **4-Model Plugin Architecture**: AF2 (`L4`/`A100`), AF3 (`H100_80GB` with auto-fallback to `A100_80GB`/`A100`/`L4` and GCE reservation auto-detection), OF3 (`A100`), and Boltz-2 (`A100`)
- **4-Stage AF3 KFP Pipeline**: Every AF3 prediction runs as an observable 4-stage KFP `PipelineJob` (*1. Provision & Queue AF3 Endpoint $\rightarrow$ 2. Run AF3 Inference (H100) $\rightarrow$ 3. Report AF3 Endpoint Available $\rightarrow$ 4. Process AF3 Results & Expert Analysis*) so the H100 replica is freed immediately after inference while log harvesting, plotting, and Gemini 3.1 Pro analysis run on CPU
- **Up to 36 Native Tools across 10 Skill Packages**: Dynamically loaded based on configured models
- **Multimodal Expert Analysis**: `gemini-3.1-pro-preview` inspects per-residue pLDDT and PAE plots alongside chain-pair ipTM/PAE matrices to generate an 8-section structural biology report
- **Interactive Viewer & Signed Downloads**: Combined 3D viewer with pLDDT confidence coloring, PAE/PDE/ipTM matrices, and V4 signed URLs (`download_job_artifacts`)

## Prerequisites

- Python 3.11+
- Google Cloud Project with Agent Platform enabled
- Application Default Credentials (`gcloud auth application-default login`)
- GCS bucket for pipeline artifacts and results
- Filestore instance (NFS) for AF2/OF3/Boltz-2 genetic databases (AF3 uses a 630 GB NVMe SSD database bundle on `a3-highgpu-1g`)

## Quick Start

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env: set GCP_PROJECT_ID, GCS_BUCKET_NAME, FILESTORE_ID, ALPHAFOLD_COMPONENTS_IMAGE, AF3_ENDPOINT

# Run interactive CLI
uv run python foldrun_app/cli.py

# Run ADK web UI
uv run adk web .
```

## Project Structure

```
foldrun-agent/
├── foldrun_app/
│   ├── agent.py                    # Agent definition, skill registration, instructions
│   ├── core/                       # Shared infrastructure (model-agnostic)
│   │   ├── base_tool.py            # BaseTool: GCS, Agent Platform, NFS helpers
│   │   ├── config.py               # CoreConfig: project, region, NFS, GCS
│   │   ├── download_artifacts.py   # V4 Signed URL & ZIP bundle generator (all 4 models)
│   │   ├── hardware.py             # GPU quota + GCE reservation detection & tier fallback
│   │   ├── model_registry.py       # Plugin registry (register_model / list_models)
│   │   └── pipeline_utils.py       # Shared KFP compilation utilities
│   ├── models/
│   │   ├── af2/                    # AlphaFold2 plugin
│   │   │   ├── config.py           # AF2Config (L4/A100/A100_80GB tiers, relax)
│   │   │   ├── base.py             # AF2Tool base class
│   │   │   ├── startup.py          # Singleton config + GPU detection
│   │   │   ├── pipeline/           # KFP: Configure → Data → Predict → Relax
│   │   │   ├── tools/              # 19 JSON-defined + 1 dynamic tool (af2_analyze_job_deep)
│   │   │   └── utils/              # FASTA validation, pipeline utils
│   │   ├── af3/                    # AlphaFold 3 plugin (4-stage KFP + Managed H100 Endpoint)
│   │   │   ├── config.py           # AF3Config (endpoint ID, H100/A100/L4 auto-fallback, reservations)
│   │   │   ├── base.py             # AF3Tool base class
│   │   │   ├── startup.py          # Singleton config + tool configs
│   │   │   ├── pipeline.py         # 4-stage KFP DAG (Queue → Inference → Release → Expert Analysis) + Idle Watchdog
│   │   │   ├── tools/              # 7 tools: submit, submit_batch, check/deploy/undeploy_endpoint, get_results, open_viewer
│   │   │   └── utils/              # Input converter (FASTA/AF3 JSON), metrics, multimodal viewer_artifacts
│   │   ├── of3/                    # OpenFold3 plugin
│   │   │   ├── config.py           # OF3Config (A100/A100_80GB only, no relax)
│   │   │   ├── base.py             # OF3Tool base class
│   │   │   ├── startup.py          # Singleton config + GPU detection (filter L4)
│   │   │   ├── pipeline/           # KFP: ConfigureSeeds → MSA(protein+RNA) → ParallelFor[Predict]
│   │   │   ├── tools/              # 4 tools: submit, analyze, get_results, open_viewer
│   │   │   └── utils/              # Input converter (FASTA → OF3 JSON)
│   │   └── boltz2/                 # Boltz-2 plugin
│   │       ├── config.py           # BOLTZ2Config (A100/A100_80GB, unified cache path)
│   │       ├── base.py             # BOLTZ2Tool base class
│   │       ├── startup.py          # Singleton config + GPU detection (A100+ only)
│   │       ├── pipeline/           # KFP: ConfigureSeeds → MSA(protein) → ParallelFor[Predict]
│   │       ├── tools/              # 4 tools: submit, analyze, get_results, open_viewer
│   │       └── utils/              # Input converter (FASTA → Boltz-2 YAML v1)
│   └── skills/                     # ADK skill wrappers & SKILL.md packages
│       ├── _tool_registry.py       # Singleton registry: initializes all model tools
│       ├── job_submission/         # submit_af2_*, submit_af3_*, submit_of3_prediction, submit_boltz2_prediction
│       ├── job_management/         # check_job_status, list_jobs, get_job_details, delete_job, check_gpu_quota, AF3 endpoint tools
│       ├── results_analysis/       # AF2 + AF3 + OF3 + Boltz-2 analysis, V4 signed downloads (download_job_artifacts)
│       ├── visualization/          # AF2 + AF3 + OF3 + Boltz-2 structure viewer tools
│       ├── database_queries/       # AlphaFold DB (prediction, summary, annotations)
│       ├── storage_management/     # GCS cleanup, orphaned file detection
│       ├── cost_estimation/        # Per-job and monthly cost estimation
│       ├── genetic_databases/      # Database download management
│       └── infrastructure/         # Infrastructure health check and setup
├── databases.yaml                  # Database manifest (af2, of3, boltz modes)
├── scripts/setup_data.py           # CLI for database downloads via Cloud Batch
├── tests/
│   ├── unit/                       # 667+ unit tests (no GCP credentials needed)
│   └── integration/                # Integration tests (requires ADC + Gemini API)
├── .env.example                    # Example environment configuration
└── pyproject.toml
```

## Available Skills & Tools

Tool count depends on which models are configured:

| Scope | Count | Condition |
|-------|-------|-----------|
| AF2 + Core (always loaded) | **21** | `ALPHAFOLD_COMPONENTS_IMAGE` set (includes `download_job_artifacts`) |
| AlphaFold 3 | **+7** | `AF3_ENDPOINT` set |
| OpenFold 3 | **+4** | `OPENFOLD3_COMPONENTS_IMAGE` set |
| Boltz-2 | **+4** | `BOLTZ2_COMPONENTS_IMAGE` set |
| **Maximum total** | **36** | All four models configured |

### AF2 & Shared Core Tools (21)

| Category | Tools |
|----------|-------|
| Job Submission (3) | `submit_af2_monomer_prediction`, `submit_af2_multimer_prediction`, `submit_af2_batch_predictions` |
| Job Management (5) | `check_job_status`, `list_jobs`, `get_job_details`, `delete_job`, `check_gpu_quota` |
| Results, Analysis & Downloads (6) | `download_job_artifacts`, `get_prediction_results`, `analyze_prediction_quality`, `af2_analyze_job_parallel`, `af2_get_analysis_results`, `analyze_job` |
| Database Queries (3) | `query_alphafold_db_prediction`, `query_alphafold_db_summary`, `query_alphafold_db_annotations` |
| Storage Management (2) | `cleanup_gcs_files`, `find_orphaned_gcs_files` |
| Visualization (1) | `open_structure_viewer` |
| Cost Estimation (3) | `estimate_job_cost`, `estimate_monthly_cost`, `get_actual_job_costs` |

### AlphaFold 3 Tools (7)

| Tool | Description |
|------|-------------|
| `submit_af3_endpoint_prediction` | Submit single AF3 complex via 4-stage KFP PipelineJob (`msa_free=False` Full 630 GB MSA default, or `msa_free=True` zero-MSA) |
| `submit_af3_batch_predictions` | Submit multiple AF3 targets in batch as async 4-stage KFP PipelineJobs with FIFO H100 replica slot queueing |
| `check_af3_endpoint` | Inspect health, active deployed models, replica count, and hourly cost state of the AF3 Endpoint |
| `deploy_af3_endpoint` | Deploy or scale AF3 model on the Endpoint (`a3-highgpu-1g` H100 80GB default, auto-fallback to A100/L4, GCE reservation auto-detection) |
| `undeploy_af3_endpoint` | Undeploy AF3 models from the Endpoint, immediately reverting GPU cost to `$0.00/hr` |
| `get_af3_results` | Retrieve completed AF3 prediction metrics (`ranking_score`, `ptm`, `iptm`, `mean_plddt`, `mean_pae`, mmCIF URI) |
| `open_af3_structure_viewer` | Open the FoldRun 3D viewer for an AF3 job (renders all-atom mmCIF + Gemini 3.1 Pro Expert Analysis) |

### OpenFold 3 Tools (4)

| Tool | Description |
|------|-------------|
| `submit_of3_prediction` | Submit OF3 job (FASTA auto-converted to OF3 JSON; full RNA MSA via `nhmmer`) |
| `of3_analyze_job_parallel` | Trigger parallel analysis via Cloud Run Job |
| `of3_get_analysis_results` | Retrieve analysis results (pLDDT, PDE, ipTM, Gemini interpretation) |
| `open_of3_structure_viewer` | Open 3D viewer for OF3 results |

### Boltz-2 Tools (4)

| Tool | Description |
|------|-------------|
| `submit_boltz2_prediction` | Submit Boltz-2 job (FASTA auto-converted to YAML v1; supports covalent mods, glycans) |
| `boltz2_analyze_job_parallel` | Trigger parallel analysis; includes affinity parsing if requested |
| `boltz2_get_analysis_results` | Retrieve results (`confidence_score`, `pTM`, `ipTM`, `IC50`/`pIC50`/`ΔG` if affinity enabled) |
| `open_boltz2_structure_viewer` | Open 3D viewer for Boltz-2 results |

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `GCP_PROJECT_ID` | GCP project ID |
| `GCP_REGION` | GCP region (e.g. `us-central1`) |
| `GCS_BUCKET_NAME` | GCS bucket for pipeline artifacts and results |
| `FILESTORE_ID` | Filestore instance ID (NFS for AF2/OF3/Boltz-2 databases) |
| `ALPHAFOLD_COMPONENTS_IMAGE` | AF2 pipeline container image |
| `GEMINI_MODEL` | Gemini model (default: `gemini-3.8-flash`) |

### Optional — AlphaFold 3

| Variable | Description |
|----------|-------------|
| `AF3_ENDPOINT` | Vertex AI Endpoint resource name or ID (enables AF3 tools) |
| `AF3_MODEL_ID` | Vertex AI Model Registry resource name or ID for AlphaFold 3 |
| `AF3_MACHINE_TYPE` | Endpoint machine type (default: `a3-highgpu-1g` with 3 TB local NVMe SSD) |
| `AF3_ACCELERATOR_TYPE` | Endpoint GPU type (default: `NVIDIA_H100_80GB`) |
| `AF3_AUTO_FALLBACK_GPU` | Automatically fall back across `H100 -> A100_80GB -> A100 -> L4` on quota/stockout errors (default: `true`) |
| `AF3_RESERVATION_AFFINITY_TYPE` | Optional GCE reservation affinity (`ANY_RESERVATION`, `SPECIFIC_RESERVATION`, `NO_RESERVATION`) |
| `AF3_RESERVATION_NAMES` | Optional comma-separated GCE reservation names |
| `AF3_DEFAULT_MSA_FREE` | Default `msa_free` mode (`false` = Full 630 GB MSA by default) |

### Optional — OpenFold3 & Boltz-2

| Variable | Description |
|----------|-------------|
| `OPENFOLD3_COMPONENTS_IMAGE` | OF3 pipeline container image (enables OF3 tools) |
| `BOLTZ2_COMPONENTS_IMAGE` | Boltz-2 pipeline container image (enables Boltz-2 tools) |
| `BOLTZ2_CACHE_PATH` | NFS-relative path to Boltz-2 cache dir containing `boltz2_conf.ckpt` and `mols/` (default: `boltz2/cache`) |

### Optional — Viewer & Analysis

| Variable | Description |
|----------|-------------|
| `ANALYSIS_JOB_NAME` | Cloud Run Job name (default: `foldrun-analysis-job`) |
| `FOLDRUN_VIEWER_URL` | FoldRun viewer Cloud Run URL (shared by all 4 models) |
| `GEMINI_ANALYSIS_MODEL` | Gemini model for expert analysis (default: `gemini-3.1-pro-preview`) |
| `PIPELINES_SA_EMAIL` | Service account email for Agent Platform Pipeline submissions |

## GPU Requirements by Model

| Model | Minimum | Recommended | Notes |
|-------|---------|-------------|-------|
| **AlphaFold 3** | L4 (24 GB, `--msa-free` only) | **H100 80GB (`a3-highgpu-1g`)** | `a3-highgpu-1g` provides 3 TB local NVMe SSD for the 630 GB MSA bundle (~3–6m per standard target); auto-falls back to `A100_80GB` / `A100` / `L4` |
| **AlphaFold 2** | L4 (24 GB) | A100 (40 GB) | A100 auto-selected for faster DWS provisioning; A100_80GB for >1,500 residues |
| **OpenFold 3** | A100 (40 GB) | A100_80GB | No L4 support; diffusion-based |
| **Boltz-2** | A100 (40 GB) | A100_80GB | No L4 support; diffusion-based |

## Running Tests

```bash
# Unit tests (no GCP credentials needed)
uv run pytest tests/unit/ -v

# Run AlphaFold 3 model & 4-stage pipeline tests
uv run pytest tests/unit/models/af3/ -v

# Integration tests (requires ADC + Gemini API access)
uv run pytest tests/integration/ -v -m integration
```

## License

Apache 2.0
