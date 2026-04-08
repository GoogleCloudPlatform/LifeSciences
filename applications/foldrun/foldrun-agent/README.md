# FoldRun Agent

AI-powered scientific orchestration assistant, currently equipped with the **AlphaFold2 Module** for protein structure prediction, using Google Agent Development Kit (ADK).

## Overview

FoldRun is designed as a modular, stateful conversational agent for bioinformatics workflows. In its current release, it comes pre-loaded with the **AlphaFold2 capabilities**, providing an interface for:
- Submitting protein structure predictions (monomers and multimers)
- Monitoring job progress and status
- Analyzing prediction quality metrics
- Downloading and visualizing results
- Querying the AlphaFold Database for existing structures
- Downloading genetic databases required for AlphaFold

Built with **Google ADK** using **native FunctionTools** — no external server dependency. All tools run directly within the agent process.

## Features

- **Conversational AI**: Natural language interface powered by Gemini
- **Native FunctionTools**: 24 tools integrated directly via ADK (no external servers)
- **Agent Engine Ready**: Deploy to Vertex AI Agent Engine for production
- **GPU Auto-Detection**: Automatic quota checking and smart GPU selection at startup
- **Quality Analysis**: Automated pLDDT and PAE metric interpretation with parallel analysis
- **Best Practices**: Built-in guidance for hardware selection
- **Database Queries**: Check AlphaFold DB before running expensive predictions
- **Genetic Database Downloads**: Download all required databases to GCS via Vertex AI CustomJobs

## Prerequisites

### Required
- Python 3.10+
- Google Cloud Project with Vertex AI enabled
- Application Default Credentials (`gcloud auth application-default login`)
- GCS bucket for pipeline artifacts and results
- Filestore instance for genetic database storage

### Optional (for deployment)
- Google Cloud SDK (`gcloud`)
- Access to Vertex AI Agent Engine

## Quick Start

### 1. Install Dependencies

We use [UV](https://docs.astral.sh/uv/) for fast, reliable Python package management.

```bash
# Install UV if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install/sync dependencies
uv sync
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and set required variables:
# - Authenticate: gcloud auth application-default login
# - GCP_PROJECT_ID, GCP_REGION
# - GCS_BUCKET_NAME
# - FILESTORE_ID
# - ALPHAFOLD_COMPONENTS_IMAGE
# - GEMINI_MODEL (default: gemini-3-flash-preview)
```

### 3. Run the Agent

#### Interactive Mode

```bash
uv run python foldrun_app/cli.py
```

#### Single Query Mode

```bash
uv run python foldrun_app/cli.py --query "List my recent AlphaFold jobs"
```

#### ADK Web UI

```bash
# Run from the foldrun-agent directory, then select "foldrun_app" in the dropdown
uv run adk web .
```

## Project Structure

```
foldrun-agent/
├── foldrun_app/                          # Main application package
│   ├── agent.py                      # Agent creation, instructions, tool registration
│   ├── __init__.py
│   ├── af2_lib/                      # Core AF2 library
│   │   ├── af2_tool.py               # Base class for all tools
│   │   ├── config.py                 # Configuration from environment
│   │   ├── startup.py                # GPU auto-detection, singleton Config
│   │   ├── tools/                    # Tool implementations (24 tools)
│   │   │   ├── submit_monomer.py
│   │   │   ├── submit_multimer.py
│   │   │   ├── submit_batch.py
│   │   │   ├── job_status.py
│   │   │   ├── list_jobs.py
│   │   │   ├── get_job_details.py
│   │   │   ├── delete_job.py
│   │   │   ├── check_gpu_quota.py
│   │   │   ├── get_results.py
│   │   │   ├── analyze.py
│   │   │   ├── analyze_job.py
│   │   │   ├── analyze_job_deep.py
│   │   │   ├── get_analysis_results.py
│   │   │   ├── cleanup_gcs_files.py
│   │   │   ├── find_orphaned_gcs_files.py
│   │   │   ├── open_viewer.py
│   │   │   ├── visualize.py
│   │   │   ├── alphafold_db_tools.py
│   │   │   ├── download_database.py
│   │   │   ├── download_all_databases.py
│   │   │   ├── check_database_download.py
│   │   │   ├── infra_check.py
│   │   │   └── infra_setup.py
│   │   ├── utils/                    # FASTA handling, pipeline compilation
│   │   ├── vertex_pipeline/          # Vertex AI pipeline components
│   │   └── data/
│   │       └── alphafold_tools.json  # Tool definitions and parameter schemas
│   └── skills/                       # ADK FunctionTool wrappers by domain
│       ├── _tool_registry.py         # Singleton tool instance registry
│       ├── job_submission/           # Submit monomer, multimer, batch
│       ├── job_management/           # Status, list, details, delete, GPU quota
│       ├── results_analysis/         # Quality analysis, parallel analysis, viewer
│       ├── database_queries/         # AlphaFold DB queries
│       ├── storage_management/       # GCS cleanup, orphaned file detection
│       ├── visualization/            # 3D structure viewer
│       ├── genetic_databases/        # Database download management
│       └── infrastructure/           # Infrastructure check and setup
├── foldrun_app/app_utils/                # Agent Engine deployment utilities
│   ├── deploy.py                     # CLI deployment script
│   ├── .requirements.txt             # Frozen dependencies for Agent Engine
│   └── ...
├── tests/                            # Unit and integration tests
├── .env.example                      # Example environment configuration
├── pyproject.toml                    # Dependencies and project metadata
└── README.md                         # This file
```

## Architecture

### Components

1. **ADK Agent** (`foldrun_app/agent.py`)
   - Configured with Gemini model and expert instructions
   - 24 native FunctionTools for all AlphaFold workflows
   - GPU auto-detection at startup

2. **Tool Layer** (`foldrun_app/af2_lib/tools/`)
   - All tools extend `AF2Tool` base class
   - Direct integration with Vertex AI, GCS, and GCP APIs
   - Shared Vertex AI and GCS clients for fast initialization
   - Eagerly initialized via singleton registry at agent startup

3. **Skill Layer** (`foldrun_app/skills/`)
   - Thin wrapper functions around tool instances
   - Organized by domain (job submission, analysis, etc.)
   - Converted to ADK FunctionTools in agent.py

### Data Flow

```
User Query -> ADK Agent -> Native FunctionTools -> Vertex AI / GCS / APIs -> Results
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_GENAI_USE_VERTEXAI` | Must be `true` (Vertex AI required) | `true` |
| `GCP_PROJECT_ID` | GCP project ID | Required |
| `GCP_REGION` | GCP region | `us-central1` |
| `GCP_ZONE` | GCP zone | `us-central1-a` |
| `GCS_BUCKET_NAME` | GCS bucket for artifacts | Required |
| `FILESTORE_ID` | Filestore instance ID | Required |
| `ALPHAFOLD_COMPONENTS_IMAGE` | Container image for pipeline | Required |
| `GEMINI_MODEL` | Gemini model to use | `gemini-3-flash-preview` |
| `AF2_VIEWER_URL` | Analysis viewer Cloud Run URL | Optional |
| `AF2_SUPPORTED_GPUS` | Comma-separated GPU list | Auto-detected |

### Supported Gemini Models

- `gemini-3-flash-preview` (default) - Fast, cost-effective
- `gemini-3-pro-preview` - Most capable model

Both require `GOOGLE_CLOUD_LOCATION=global` (preview models use the global endpoint).

## Available Tools (24)

### Job Submission (3)
| Tool | Description |
|------|-------------|
| `submit_monomer_prediction` | Single-chain protein prediction |
| `submit_multimer_prediction` | Multi-chain protein complex prediction |
| `submit_batch_predictions` | Bulk submission of multiple predictions |

### Job Management (5)
| Tool | Description |
|------|-------------|
| `check_job_status` | Monitor job progress and state |
| `list_jobs` | List and filter jobs with metadata |
| `get_job_details` | Retrieve complete job configuration and FASTA |
| `delete_job` | Remove job from Vertex AI (with safety check) |
| `check_gpu_quota` | View GPU quota limits and availability |

### Results & Analysis (5)
| Tool | Description |
|------|-------------|
| `get_prediction_results` | Download PDB files and rankings |
| `analyze_prediction_quality` | pLDDT and PAE quality metrics |
| `analyze_job_parallel` | Fast parallel analysis via Cloud Run (25 predictions in ~60s) |
| `get_analysis_results` | Retrieve completed parallel analyses |
| `analyze_job` | Comprehensive job analysis (any state) |

### Database Queries (3)
| Tool | Description |
|------|-------------|
| `query_alphafold_db_prediction` | Full prediction from AlphaFold DB |
| `query_alphafold_db_summary` | Quick summary lookup |
| `query_alphafold_db_annotations` | Variant/mutation annotations |

### Storage Management (2)
| Tool | Description |
|------|-------------|
| `cleanup_gcs_files` | Delete GCS files (job-based or bulk) |
| `find_orphaned_gcs_files` | Find GCS files without matching jobs |

### Visualization (1)
| Tool | Description |
|------|-------------|
| `open_structure_viewer` | Interactive 3D structure viewer |

### Genetic Database Management (3)
| Tool | Description |
|------|-------------|
| `download_genetic_database` | Download a single database to GCS |
| `download_all_genetic_databases` | Download all databases in parallel |
| `check_database_download_status` | Check download job status and GCS files |

### Infrastructure (2)
| Tool | Description |
|------|-------------|
| `check_infrastructure` | Check infrastructure readiness (Filestore, APIs, etc.) |
| `setup_infrastructure` | Set up missing infrastructure components |

## Genetic Databases

AlphaFold requires several genetic databases for sequence alignment. Database setup follows a two-tier architecture:

### Tier 1: Download (always required)

Downloads raw databases to NFS (Filestore) and backs up to GCS. Runs on cheap `n1-standard-4` VMs (~$0.14/hr). Sufficient for standard Jackhmmer/HHblits pipelines.

```bash
# Download all databases
./deploy-all.sh --steps data

# Download a single database
./deploy-all.sh --steps data --db uniref90
```

### Tier 2: MMseqs2 Conversion (optional)

Converts FASTA databases to MMseqs2 GPU-indexed format. Runs on `n1-highmem-32` (208 GB RAM) with 2 local NVMe SSDs RAID-0 (750 GB). Only needed for `msa_method='mmseqs2'` GPU-accelerated search.

```bash
# Convert all MMseqs2-indexable databases (uniref90, mgnify, small_bfd)
./deploy-all.sh --steps convert

# Convert a single database
./deploy-all.sh --steps convert --db mgnify
```

### Supported Databases

| Database | Full Mode | Reduced Mode | Approx. Size | MMseqs2 Indexable |
|----------|-----------|--------------|--------------|-------------------|
| AlphaFold Parameters | Yes | Yes | ~4 GB | No |
| BFD | Yes | No | ~1.7 TB | No |
| Small BFD | No | Yes | ~17 GB | Yes |
| MGnify | Yes | Yes | ~64 GB | Yes |
| PDB70 | Yes | Yes | ~56 GB | No |
| PDB mmCIF | Yes | Yes | ~200 GB | No |
| PDB SeqRes | Yes | Yes | ~1 GB | No |
| UniRef30 | Yes | Yes | ~86 GB | No |
| UniRef90 | Yes | Yes | ~58 GB | Yes |
| UniProt | Yes | Yes | ~100 GB | No |

### Download Modes

- **`reduced_dbs`** (default): Small BFD + all databases (~1 TB total). Faster, suitable for most use cases.
- **`full_dbs`**: Full BFD + all databases (~5 TB total). Best prediction quality.

## Best Practices

### Hardware Selection
- **L4 GPUs**: Recommended for proteins <500 residues
- **A100 GPUs**: Recommended for proteins 500-2000 residues
- **A100-80GB**: Required for proteins >2000 residues

### MSA Methods

AlphaFold2 builds Multiple Sequence Alignments (MSAs) by searching genetic databases for homologous sequences. This agent supports two search methods:

#### `msa_method: jackhmmer` (CPU, default)

Uses [JackHMMER](http://hmmer.org/) for iterative profile HMM search. Works out of the box with downloaded FASTA databases — no extra setup required.

- **Databases searched (CPU):**
  - `use_small_bfd: true` — UniRef90, MGnify, Small BFD (all via JackHMMER)
  - `use_small_bfd: false` — UniRef90, MGnify (JackHMMER) + BFD & UniRef30 (HHblits)
- **How it works:**
  1. Searches database with query sequence
  2. Builds a profile HMM (position-specific scoring matrix) from hits
  3. Re-searches database with the profile — finds more distant homologs
  4. Repeats for 3 iterations, each round refining the profile

#### `msa_method: mmseqs2` (GPU-accelerated, optional)

Uses [MMseqs2](https://github.com/soedinglab/MMseqs2) with GPU acceleration. 177x faster than JackHMMER but **requires a one-time database conversion step**.

- **Databases searched (GPU):** UniRef90, MGnify, Small BFD
- **Requires:**
  1. `use_small_bfd: true` (all three databases must be FASTA format)
  2. Pre-built MMseqs2 indexes on Filestore — run `AF2ConvertMMseqs2Tool` or `deploy-all.sh --steps convert` first
- **Conversion details:** Runs `createdb` → `makepaddedseqdb` → `createindex --split 1` on `n1-highmem-32` (208 GB RAM) with 2 local SSDs RAID-0 (750 GB NVMe). Takes ~3-4 hours per database.
- **How it works:**
  1. Pre-built k-mer index rapidly filters the database to candidate sequences (~99% eliminated)
  2. Ungapped alignment further narrows candidates
  3. Gapped Smith-Waterman alignment produces final MSA
  4. Single-pass — no iterative refinement
- **Multimer note:** Template search (HHsearch/hmmsearch) and UniProt pairing (JackHMMER) still run on CPU within the same VM. These are small/fast searches.

#### Comparison

| | JackHMMER (CPU, default) | MMseqs2 (GPU, optional) |
|---|---|---|
| **Speed** | Hours per chain | Minutes per chain |
| **Sensitivity** | Higher (iterative profiles find distant homologs) | Slightly lower (single-pass) |
| **Hardware** | CPU-only (c2-standard-16) | g2-standard-12 + L4 GPU |
| **Database modes** | Both `true` and `false` | `use_small_bfd: true` only |
| **Setup** | None (works with raw downloads) | Requires one-time MMseqs2 index conversion |
| **Best for** | Default pipeline, difficult targets | High-throughput, batch jobs |

For most proteins, both methods produce MSAs of sufficient depth for high-quality predictions. The ColabFold project has demonstrated comparable AlphaFold2 results using MMseqs2 search across a wide range of targets.

#### `msa_method: auto` (default)

Always selects **Jackhmmer** (CPU). MMseqs2 must be explicitly requested via `msa_method: 'mmseqs2'`.

### Tips
1. Query AlphaFold DB first — a structure may already exist
2. Use `small_bfd` for most predictions (faster MSA search)
3. Use L4 GPUs for small proteins, A100 for large
4. Enable FLEX_START (default) to prevent provisioning failures

## Deployment to Agent Engine

The agent can be deployed to Vertex AI Agent Engine for production use. All 24 tools are embedded directly within the agent process.

### Prerequisites

```bash
gcloud auth application-default login
gcloud config set project your-project-id

# Ensure Vertex AI API is enabled
gcloud services enable aiplatform.googleapis.com --project=YOUR_PROJECT_ID
```

### 1. Update Requirements

Regenerate the frozen requirements from the current virtual environment:

```bash
uv pip freeze --python .venv/bin/python | grep -v "^-e " > foldrun_app/app_utils/.requirements.txt
```

### 2. Deploy

```bash
uv run python -m foldrun_app.app_utils.deploy \
  --project your-project-id \
  --location us-central1 \
  --display-name "AlphaFold2_Assistant" \
  --description "Expert AI assistant for AlphaFold2 protein structure prediction"
```

The deploy script will:
- Load environment variables from `.env`
- Package the `foldrun_app/` source directory
- Upload to Agent Engine with all dependencies
- Create or update the agent (auto-detects existing deployments by display name)

### Deploy Options

```bash
# Custom resource limits
uv run python -m foldrun_app.app_utils.deploy \
  --project your-project-id \
  --location us-central1 \
  --min-instances 1 \
  --max-instances 10 \
  --cpu 4 \
  --memory 8Gi

# Additional environment variables (merged with .env)
uv run python -m foldrun_app.app_utils.deploy \
  --project your-project-id \
  --location us-central1 \
  --set-env-vars "GEMINI_MODEL=gemini-3-pro-preview"
```

### 3. Verify

After deployment, the console playground URL is printed. You can also find it at:

```
https://console.cloud.google.com/vertex-ai/agents/locations/us-central1/agent-engines/AGENT_ID/playground?project=YOUR_PROJECT_ID
```

The agent engine ID is saved to `deployment_metadata.json`.

### Delete Deployment

```bash
# Force delete (including existing sessions)
curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/AGENT_RESOURCE_NAME?force=true"
```

## Troubleshooting

### Import Errors

```bash
# Reinstall dependencies
uv sync --reinstall
```

### Agent Engine Deployment Fails

Common causes:
- **Editable installs in requirements**: Ensure `pip freeze` output doesn't contain `-e file:///...` lines. Use the `grep -v "^-e "` filter.
- **Missing APIs**: Enable `aiplatform.googleapis.com`
- **Auth issues**: Run `gcloud auth application-default login`

```bash
# Check build logs
gcloud builds list --project=YOUR_PROJECT_ID --limit=3
gcloud builds log BUILD_ID --project=YOUR_PROJECT_ID
```

### GPU Quota Issues

The agent automatically checks GPU quotas at startup. If no GPUs are available:
1. Check quotas in GCP Console
2. Request quota increase for your region
3. Use FLEX_START scheduling to queue jobs
4. Try a different GPU type (agent auto-upgrades when possible)

## Development

### Running Tests

```bash
# Unit tests (no GCP credentials needed)
uv run pytest tests/unit/ -v

# Integration tests (requires ADC and Gemini API access)
uv run pytest tests/integration/ -v -m integration

# All tests except integration
uv run pytest tests/ -v -m "not integration"
```

See `tests/TESTING.md` for detailed testing documentation.

### Linting

```bash
uv run ruff check foldrun_app/
```

## Resources

- [Google ADK Documentation](https://google.github.io/adk-docs/)
- [Vertex AI Documentation](https://cloud.google.com/vertex-ai/docs)
- [AlphaFold Database](https://alphafold.ebi.ac.uk/)

## Data Handling

FoldRun runs entirely within your own GCP project. Google does not collect or retain your data.

- **Your protein sequences** are uploaded to your GCS bucket (`GCS_BUCKET_NAME`) and passed to Vertex AI Pipelines running in your project. They are not sent to any Google-internal systems.
- **Prediction outputs** (PDB files, confidence scores) are stored in your GCS bucket only.
- **Gemini API calls** use your project's Vertex AI endpoint. You can configure a [zero data retention policy](https://cloud.google.com/vertex-ai/generative-ai/docs/data-governance) for your project if required.
- **No telemetry or analytics** are collected by FoldRun.

> **Note on sensitive sequences:** FoldRun is designed for research use with standard protein sequences (e.g. from UniProt, PDB, or laboratory experiments). If you are working with sequences derived from patient samples, ensure appropriate de-identification has been applied before submission, in accordance with your organization's data governance policies.

## License

Apache 2.0
