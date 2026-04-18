# FoldRun Agent

AI-powered protein structure prediction assistant supporting **AlphaFold2**, **OpenFold3**, and **Boltz-2** via a conversational interface powered by Google ADK and Gemini.

## Overview

FoldRun Agent is a modular, stateful conversational agent for biomolecular structure prediction. It manages the full prediction lifecycle through natural language:

- Submit structure predictions (AF2 monomers/multimers, OF3 and Boltz-2 multi-molecule complexes)
- Monitor job progress on Vertex AI Pipelines
- Run parallel structural analysis via Cloud Run Jobs
- Visualize results in the interactive 3D viewer
- Query the AlphaFold Database for existing structures
- Estimate and track compute costs

Built with **Google ADK** using **native FunctionTools** — all tools run directly within the agent process, no external server dependency.

## Model Selection Guide

| Need | Model |
|------|-------|
| Protein-only (single chain) | **AlphaFold2** monomer |
| Protein complex (multiple chains) | **AlphaFold2** multimer |
| Protein + RNA, DNA, or small-molecule ligand | **OpenFold3** (has full RNA MSA via nhmmer) |
| Covalent modifications or glycans | **Boltz-2** (only model supporting these) |
| RNA + covalent mod | **Boltz-2** (no RNA MSA, but necessary for covalent features) |

Boltz-2 is optional — enabled by setting `BOLTZ2_COMPONENTS_IMAGE` in the environment.

## Features

- **Multi-Model**: AF2 (L4/A100), OF3 (A100), Boltz-2 (A100) with automatic GPU tier selection
- **Native FunctionTools**: Up to 30 tools depending on model configuration
- **Conditional Tool Loading**: OF3 and Boltz-2 tools only load when their component images are configured
- **GPU Auto-Detection**: Quota checking and smart GPU selection at startup (per model)
- **Parallel Analysis**: Cloud Run Jobs for fast parallel structural analysis + Gemini expert interpretation
- **Affinity Prediction**: Boltz-2 binding affinity (IC50, pIC50, ΔG) when requested
- **Interactive Viewer**: Combined 3D viewer with pLDDT confidence coloring, ipTM matrix, PDE plots, and affinity display

## Prerequisites

- Python 3.10+
- Google Cloud Project with Vertex AI enabled
- Application Default Credentials (`gcloud auth application-default login`)
- GCS bucket for pipeline artifacts and results
- Filestore instance (NFS) for genetic databases and model weights

## Quick Start

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env: set GCP_PROJECT_ID, GCS_BUCKET_NAME, FILESTORE_ID, ALPHAFOLD_COMPONENTS_IMAGE

# Run interactive CLI
uv run python foldrun_app/cli.py

# Run ADK web UI
uv run adk web .
```

## Project Structure

```
foldrun-agent/
├── foldrun_app/
│   ├── agent.py                    # Agent definition, FunctionTool registration, instructions
│   ├── core/                       # Shared infrastructure (model-agnostic)
│   │   ├── base_tool.py            # BaseTool: GCS, Vertex AI, NFS helpers
│   │   ├── config.py               # CoreConfig: project, region, NFS, GCS
│   │   ├── hardware.py             # GPU quota detection
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
│   │   ├── of3/                    # OpenFold3 plugin
│   │   │   ├── config.py           # OF3Config (A100/A100_80GB only, no relax)
│   │   │   ├── base.py             # OF3Tool base class
│   │   │   ├── startup.py          # Singleton config + GPU detection (filter L4)
│   │   │   ├── pipeline/           # KFP: ConfigureSeeds → MSA(protein+RNA) → ParallelFor[Predict]
│   │   │   ├── tools/              # 4 tools: submit, analyze, get_results, open_viewer
│   │   │   └── utils/              # Input converter (FASTA → OF3 JSON)
│   │   └── boltz2/                 # Boltz-2 plugin (optional)
│   │       ├── config.py           # BOLTZ2Config (A100/A100_80GB, unified cache path)
│   │       ├── base.py             # BOLTZ2Tool base class
│   │       ├── startup.py          # Singleton config + GPU detection (A100+ only)
│   │       ├── pipeline/           # KFP: ConfigureSeeds → MSA(protein) → ParallelFor[Predict]
│   │       ├── tools/              # 4 tools: submit, analyze, get_results, open_viewer
│   │       └── utils/              # Input converter (FASTA → Boltz-2 YAML v1)
│   └── skills/                     # ADK FunctionTool wrappers (thin, domain-organized)
│       ├── _tool_registry.py       # Singleton registry: initializes all model tools
│       ├── job_submission/         # submit_af2_*, submit_of3_prediction, submit_boltz2_prediction
│       ├── job_management/         # check_job_status, list_jobs, get_job_details, delete_job, check_gpu_quota
│       ├── results_analysis/       # AF2 + OF3 + Boltz-2 analysis and results retrieval
│       ├── visualization/          # AF2 + OF3 + Boltz-2 structure viewer tools
│       ├── database_queries/       # AlphaFold DB (prediction, summary, annotations)
│       ├── storage_management/     # GCS cleanup, orphaned file detection
│       ├── cost_estimation/        # Per-job and monthly cost estimation
│       ├── genetic_databases/      # Database download management
│       └── infrastructure/         # Infrastructure health check and setup
├── databases.yaml                  # Database manifest (af2, of3, boltz modes)
├── scripts/setup_data.py           # CLI for database downloads via Cloud Batch
├── tests/
│   ├── unit/                       # 298 unit tests (no GCP credentials needed)
│   └── integration/                # Integration tests (requires ADC + Gemini API)
├── .env.example                    # Example environment configuration
└── pyproject.toml
```

## Available Tools

Tool count depends on which models are configured:

| Scope | Count | Condition |
|-------|-------|-----------|
| AF2 (always loaded) | **20** | `ALPHAFOLD_COMPONENTS_IMAGE` set |
| OF3 (optional) | **+4** | `OPENFOLD3_COMPONENTS_IMAGE` set |
| Boltz-2 (optional) | **+4** | `BOLTZ2_COMPONENTS_IMAGE` set |
| **Maximum total** | **28** | All three models configured |

### AF2 Tools (20)

| Category | Tools |
|----------|-------|
| Job Submission (3) | `submit_af2_monomer_prediction`, `submit_af2_multimer_prediction`, `submit_af2_batch_predictions` |
| Job Management (5) | `check_job_status`, `list_jobs`, `get_job_details`, `delete_job`, `check_gpu_quota` |
| Results & Analysis (5) | `get_prediction_results`, `analyze_prediction_quality`, `analyze_job_parallel`, `get_analysis_results`, `analyze_job` |
| Database Queries (3) | `query_alphafold_db_prediction`, `query_alphafold_db_summary`, `query_alphafold_db_annotations` |
| Storage Management (2) | `cleanup_gcs_files`, `find_orphaned_gcs_files` |
| Visualization (1) | `open_structure_viewer` |
| Cost Estimation (3) | `estimate_job_cost`, `estimate_monthly_cost`, `get_actual_job_costs` |

### OF3 Tools (4, optional)

| Tool | Description |
|------|-------------|
| `submit_of3_prediction` | Submit OF3 job (FASTA auto-converted to OF3 JSON; full RNA MSA via nhmmer) |
| `of3_analyze_job_parallel` | Trigger parallel analysis via Cloud Run Job |
| `of3_get_analysis_results` | Retrieve analysis results (pLDDT, PDE, ipTM, Gemini interpretation) |
| `open_of3_structure_viewer` | Open 3D viewer for OF3 results |

### Boltz-2 Tools (4, optional)

| Tool | Description |
|------|-------------|
| `submit_boltz2_prediction` | Submit Boltz-2 job (FASTA auto-converted to YAML v1; supports covalent mods, glycans) |
| `boltz2_analyze_job_parallel` | Trigger parallel analysis; includes affinity parsing if requested |
| `boltz2_get_analysis_results` | Retrieve results (confidence_score, pTM, ipTM, IC50/pIC50/ΔG if affinity enabled) |
| `open_boltz2_structure_viewer` | Open 3D viewer for Boltz-2 results |

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `GCP_PROJECT_ID` | GCP project ID |
| `GCP_REGION` | GCP region (e.g. `us-central1`) |
| `GCS_BUCKET_NAME` | GCS bucket for pipeline artifacts and results |
| `FILESTORE_ID` | Filestore instance ID (NFS for databases) |
| `ALPHAFOLD_COMPONENTS_IMAGE` | AF2 pipeline container image |
| `GEMINI_MODEL` | Gemini model (default: `gemini-3-flash-preview`) |

### Optional — OpenFold3

| Variable | Description |
|----------|-------------|
| `OPENFOLD3_COMPONENTS_IMAGE` | OF3 pipeline container image (enables OF3 tools) |
| `OF3_ANALYSIS_JOB_NAME` | Cloud Run Job name (default: `of3-analysis-job`) |

### Optional — Boltz-2

| Variable | Description |
|----------|-------------|
| `BOLTZ2_COMPONENTS_IMAGE` | Boltz-2 pipeline container image (enables Boltz-2 tools) |
| `BOLTZ2_ANALYSIS_JOB_NAME` | Cloud Run Job name (default: `boltz2-analysis-job`) |
| `BOLTZ2_CACHE_PATH` | NFS-relative path to Boltz-2 cache dir containing `boltz2_conf.ckpt` and `mols/` (default: `boltz2/cache`) |

### Optional — Viewer & Analysis

| Variable | Description |
|----------|-------------|
| `AF2_VIEWER_URL` | FoldRun viewer Cloud Run URL (shared by all models) |
| `GEMINI_ANALYSIS_MODEL` | Gemini model for expert analysis (default: `gemini-3.1-pro-preview`) |
| `PIPELINES_SA_EMAIL` | Service account email for Vertex AI Pipeline submissions |

## GPU Requirements by Model

| Model | Minimum | Recommended | Notes |
|-------|---------|-------------|-------|
| AF2 | L4 (16 GB) | A100 (40 GB) | L4 for <500 residues; A100 for larger |
| OF3 | A100 (40 GB) | A100_80GB | No L4 support; diffusion-based |
| Boltz-2 | A100 (40 GB) | A100_80GB | No L4 support; diffusion-based |

All models support DWS FLEX_START scheduling for cost-effective GPU provisioning.

## Running Tests

```bash
# Unit tests (no GCP credentials needed)
uv run pytest tests/unit/ -v

# Run specific model tests
uv run pytest tests/unit/models/boltz2/ -v

# Integration tests (requires ADC + Gemini API access)
uv run pytest tests/integration/ -v -m integration
```

## License

Apache 2.0
