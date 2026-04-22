<table><tr>
<td width="160" valign="middle"><a href="https://youtu.be/umTLrEF5L7A"><img src="img/foldrun-sticker.svg" alt="FoldRun" width="150"/></a></td>
<td valign="middle"><strong>FoldRun</strong> is an AI-powered orchestration platform for protein structure prediction on Google Cloud. It provides a conversational interface that manages the entire lifecycle — from sequence input to structural validation — using Gemini and Google Agent Engine. Supports multiple structure prediction models (AlphaFold2, OpenFold3, Boltz) via a plugin architecture with shared infrastructure.</td>
</tr></table>

## Features

- **Conversational AI**: Natural language interface powered by Gemini for submitting, monitoring, and analyzing predictions
- **Multi-Model Support**: Plugin architecture for AF2, OpenFold3, Boltz — shared databases, independent pipelines
- **Automated Execution**: Provisions infrastructure and launches pipelines on Vertex AI with optimal compute selection
- **Parallel Analysis**: Cloud Run jobs calculate structural metrics (pLDDT, PAE) and generate expert biological insights using Gemini
- **Interactive Visualization**: Web-based 3D structure viewer with confidence coloring and analysis dashboards
- **Smart Database Management**: YAML-driven downloads via Cloud Batch with GCS-based gap detection — shared databases downloaded once across models

## Supported Models

| Model | Source | Capabilities |
|-------|--------|-------------|
| [AlphaFold 2](https://github.com/google-deepmind/alphafold) | Google DeepMind | Protein monomers and multimers, AMBER relaxation (currently disabled — see note below) |
| [OpenFold 3](https://github.com/aqlaboratory/openfold-3) | AQ Laboratory | Proteins, RNA, DNA, ligands (SMILES/CCD), covalent modifications, glycans |
| [Boltz-2](https://github.com/jwohlwend/boltz) | MIT / jwohlwend | Proteins, RNA, DNA, ligands, covalent modifications, glycans, binding affinity |

> **AF2 AMBER relaxation note:** AMBER relaxation is currently disabled by default (`run_relaxation=false`) due to a CUDA PTX version mismatch between the DeepMind AlphaFold2 container image and the Vertex AI GPU driver. Unrelaxed structures are suitable for most downstream analyses. A fix is in progress — see [PR #61](https://github.com/GoogleCloudPlatform/LifeSciences/pull/61). Relevant upstream context: [AlphaFold Issue #921](https://github.com/google-deepmind/alphafold/issues/921) (breaking change), [AlphaFold Commit a4315dd](https://github.com/google-deepmind/alphafold/commit/a4315dd7b8f9f543ad5f4080af4a42d431ef3b35), and [OpenMM Issue #3585](https://github.com/openmm/openmm/issues/3585).

## Tech Stack

- **Agent**: Google ADK with up to 30 native FunctionTools (AF2 + OF3 + Boltz-2), deployed to Vertex AI Agent Engine
- **A2A**: Agent-to-Agent protocol proxy (Cloud Run) for agent interoperability
- **AI**: Gemini (via Vertex AI)
- **Compute**: Vertex AI Pipelines, Cloud Run, Cloud Batch
- **Storage**: GCS (artifacts/results), Filestore (genetic databases)
- **Infrastructure**: Terraform, Cloud Build
- **Language**: Python 3.10+

## Getting Started

### Prerequisites

**Tools (install on your workstation):**
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) with beta component:
  ```bash
  gcloud components install beta --quiet
  ```
- [Terraform](https://developer.hashicorp.com/terraform/install) (>= 1.0)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package manager)

**GCP Project Requirements:**
- A GCP project with billing enabled
- The deploying user needs these IAM roles on the project:
  - `roles/owner` (simplest — covers all below), OR these granular roles:
  - `roles/editor` — create resources
  - `roles/iam.serviceAccountAdmin` — create service accounts
  - `roles/resourcemanager.projectIamAdmin` — grant IAM roles
  - `roles/artifactregistry.admin` — create Artifact Registry repos
  - `roles/serviceusage.serviceUsageAdmin` — enable APIs

**GPU Quota (check before starting):**
- AF2 minimum: **1x NVIDIA A100 40GB** (L4 no longer auto-selected — slow DWS provisioning)
- AF2 large proteins (>1500 residues): **1x NVIDIA A100 80GB**
- OF3 minimum: **1x NVIDIA A100 40GB** (no L4 support)
- Boltz-2 minimum: **1x NVIDIA A100 40GB** (no L4 support — diffusion model requires ≥40 GB VRAM)
- Check your quota: [GPU quota page](https://console.cloud.google.com/iam-admin/quotas?filter=gpu)
- If you need to request quota increases, do it first — approvals can take hours

### Step 1: Authenticate

```bash
# Login with your Google account
gcloud auth login

# Set Application Default Credentials (needed by the deploy script)
gcloud auth application-default login

# Set your project
gcloud config set project YOUR_PROJECT_ID
```

### Step 2: Deploy

From the `applications/foldrun/` directory:

```bash
cd applications/foldrun

# Deploy everything — no .env setup needed for fresh installs
./deploy-all.sh YOUR_PROJECT_ID
```

> **Note**: You do NOT need to create or edit a `.env` file for deployment.
> The deploy script and Cloud Build handle all configuration automatically.
> The `.env` file is only needed for [local development](#local-development).

The script will:
1. Enable GCP APIs and provision infrastructure (Terraform)
2. Build and deploy containers, viewer, analysis jobs, and agent (Cloud Build)
3. Ask how to set up genomic databases (see below)

**Database setup options** (the script asks interactively):

| Option | Time | Use when |
|--------|------|----------|
| 1. Download from internet | 2-4 hours | First-time setup, no existing backups |
| 2. Restore from GCS bucket | ~15 min | Colleague shared their bucket, or re-hydrating after rebuild |
| 3. Skip | 0 min | Deploy agent now, add databases later |

**Fast setup from a shared GCS bucket** (skip the interactive prompt):
```bash
GCS_SOURCE_BUCKET=source-project-foldrun-gdbs ./deploy-all.sh YOUR_PROJECT_ID
```

### Step 2b: Cross-Project Database Sharing (Optional)

If someone already has FoldRun deployed and wants to share their databases
with you, you can use their bucket as a source to speed up your deployment.
This requires provisioning your infrastructure first so the service account exists.

**1. Provision your infrastructure:**
```bash
./deploy-all.sh YOUR_PROJECT_ID --steps infra
```

**2. They grant your project read access to their databases bucket:**
They run this (replacing THEIR_PROJECT and YOUR_PROJECT_ID):
```bash
gcloud storage buckets add-iam-policy-binding gs://THEIR_PROJECT-foldrun-gdbs \
  --member="serviceAccount:batch-compute-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```

**3. Complete the deployment with the source bucket:**
```bash
GCS_SOURCE_BUCKET=THEIR_PROJECT-foldrun-gdbs ./deploy-all.sh YOUR_PROJECT_ID
```

### Other deploy options
```bash
./deploy-all.sh YOUR_PROJECT_ID --steps infra    # Only Terraform
./deploy-all.sh YOUR_PROJECT_ID --steps build    # Only Cloud Build (all containers)
./deploy-all.sh YOUR_PROJECT_ID --steps data     # Only database downloads
DOWNLOAD_MODE=full ./deploy-all.sh YOUR_PROJECT_ID  # Full BFD database (~272GB)
```

**Targeted rebuilds** — rebuild only what changed (much faster than full build):
```bash
# Rebuild just the OF3 container + redeploy agent (~10 min vs ~25 min full build)
./deploy-all.sh YOUR_PROJECT_ID --steps build --build-target of3

# Redeploy agent only — no container rebuilds (~3 min, e.g. after agent code change)
./deploy-all.sh YOUR_PROJECT_ID --steps build --build-target agent

# Rebuild a single analysis job
./deploy-all.sh YOUR_PROJECT_ID --steps build --build-target of3-analysis

# Rebuild multiple targets (comma-separated)
./deploy-all.sh YOUR_PROJECT_ID --steps build --build-target of3,viewer
```

Available `--build-target` values:

| Target | What rebuilds |
|--------|--------------|
| `all` | Everything (default) |
| `of3` | openfold3-components container + agent |
| `af2` | alphafold-components container + agent |
| `boltz2` | boltz2-components container + agent |
| `viewer` | foldrun-viewer Cloud Run service + agent |
| `agent` | Agent Engine only (no container rebuilds) |
| `of3-analysis` | of3-analysis-job Cloud Run Job only |
| `af2-analysis` | af2-analysis-job Cloud Run Job only |
| `boltz2-analysis` | boltz2-analysis-job Cloud Run Job only |

The agent is automatically redeployed whenever any non-analysis target is included.

**Pinned model versions** — override without editing any files:
```bash
# Upgrade OpenFold3 to a newer release
OF3_VERSION=0.4.0 ./deploy-all.sh YOUR_PROJECT_ID --steps build --build-target of3

# Pin AlphaFold2 to a specific git commit
AF2_VERSION=abc123def ./deploy-all.sh YOUR_PROJECT_ID --steps build --build-target af2

# Upgrade Boltz-2
BOLTZ_VERSION=2.3.0 ./deploy-all.sh YOUR_PROJECT_ID --steps build --build-target boltz2
```

Default versions are defined in `deploy-all.sh` and match the tested, pinned values in each container's `Dockerfile`.

### Step 3: Verify

```bash
# Check all components are healthy
./check-status.sh YOUR_PROJECT_ID
```

Expected output:
```
✅ [Terraform] Infrastructure provisioned
✅ [Cloud Run] foldrun-viewer service is deployed and active
✅ [Cloud Run] af2-analysis-job is deployed
✅ [Cloud Run] of3-analysis-job is deployed
✅ [Cloud Run] boltz2-analysis-job is deployed
✅ [Vertex AI] FoldRun Agent Engine is deployed
✅ [Data] Databases present (12 folders)
   ✅ AF2 core databases (uniref90 etc.)
   ✅ OF3 weights + CCD
   ⚠️  Boltz-2 databases not downloaded (optional)
```

### Step 4: Use the Agent

Open the Agent Engine playground:
```
https://console.cloud.google.com/vertex-ai/agents/locations/YOUR_REGION/agent-engines/YOUR_ENGINE_ID/playground?project=YOUR_PROJECT_ID
```

The engine ID is printed at the end of `deploy-all.sh` and saved in `foldrun-agent/deployment_metadata.json`.

**Try these prompts:**
- "Predict the structure of ubiquitin" (AF2 monomer)
- "Fold this protein with ATP: MQIFVKTLTGKTITL..." (OF3, protein + ligand)
- "Predict a glycoprotein-ligand complex with covalent modifications" (Boltz-2)
- "What's the structure of P69905?" (checks AlphaFold DB first)

**Use via Gemini CLI (A2A):**

The deploy prints the A2A proxy URL. Create `~/.gemini/agents/foldrun.md`:
```markdown
---
kind: remote
name: FoldRun
description: Protein structure prediction agent
agent_card_url: https://YOUR_A2A_URL/.well-known/agent.json
auth:
  type: google-credentials
---
```
Then: `gemini -a foldrun "Predict the structure of ubiquitin"`

### Step 5: Wait for Databases

Database downloads run as Cloud Batch jobs in the background. Structure predictions will fail until the required databases are available on NFS.

- **From GCS restore**: ~15 minutes
- **From internet**: 2-4 hours

Monitor progress:
```bash
gcloud batch jobs list --project=YOUR_PROJECT_ID --location=YOUR_REGION
```

Or check the [Cloud Batch console](https://console.cloud.google.com/batch/jobs).

### What Gets Created

| Resource | Name | Purpose |
|----------|------|---------|
| VPC + Subnet | `foldrun-network` | Private network for Filestore + pipelines |
| Filestore | `foldrun-nfs` | NFS for genetic databases (2.5TB Basic SSD) |
| GCS Bucket | `{project}-foldrun-data` | Pipeline outputs, analysis results |
| GCS Bucket | `{project}-foldrun-gdbs` | Genomic database backups |
| Artifact Registry | `foldrun-repo` | Container images |
| Cloud Run Service | `foldrun-viewer` | 3D structure viewer (AF2 + OF3 + Boltz-2) |
| Cloud Run Job | `af2-analysis-job` | AF2 parallel analysis |
| Cloud Run Job | `of3-analysis-job` | OF3 parallel analysis |
| Cloud Run Job | `boltz2-analysis-job` | Boltz-2 parallel analysis |
| Cloud Run Service | `foldrun-a2a` | A2A protocol proxy for agent interop |
| Service Account | `foldrun-agent-sa` | Agent's GCP identity |
| Agent Engine | `FoldRun Assistant` | Deployed Gemini agent (via Cloud Build) |

### Local Development

```bash
cd foldrun-agent
cp .env.example .env   # Edit with your project settings
uv sync
uv run python foldrun_app/cli.py
```

For `adk web` (interactive UI):
```bash
cd foldrun-agent
uv run adk web foldrun_app
```

### Estimated Costs

| Component | Estimated Monthly Cost |
|-----------|----------------------|
| Filestore (2.5TB Basic SSD) | ~$770/mo |
| GCS (~1TB database backups) | ~$20/mo |
| Artifact Registry (~16GB) | ~$2/mo |
| Agent Engine (idle) | ~$0 (pay per query) |
| Cloud Run (viewer, idle) | ~$0 (scale to zero) |
| AF2 prediction (per job, A100) | ~$8 per job (MSA + 5 seeds predict + relax) |
| OF3 prediction (per job, A100) | ~$13 per job (MSA + 5 seeds predict) |
| Boltz-2 prediction (per job, A100) | ~$13 per job (MSA + 5 seeds predict) |
| Gemini API (per analysis) | ~$0.01-0.05 per analysis |

The dominant cost is Filestore (~$770/mo). Current databases (AF2 reduced + OF3) use ~944 GB of the 2.5 TB provisioned, leaving room for the full BFD database (~272 GB) if needed. BASIC_SSD avoids throughput throttling during concurrent database downloads. Terraform ignores capacity changes after provisioning, so you can resize via Console or gcloud without drift. To stop costs, delete the Filestore instance when not in use and re-download databases when needed.

## Project Structure

```
foldrun/
├── foldrun-agent/              # AI Agent (Google ADK)
│   ├── foldrun_app/
│   │   ├── agent.py            # Agent definition (Gemini + FunctionTools)
│   │   ├── core/               # Shared infrastructure (model-agnostic)
│   │   │   ├── base_tool.py    # BaseTool (GCS, Vertex AI, NFS)
│   │   │   ├── config.py       # GCP project, NFS, GCS config
│   │   │   ├── hardware.py     # GPU quota detection
│   │   │   ├── batch.py        # Cloud Batch job submission
│   │   │   ├── download.py     # YAML-driven database downloader
│   │   │   └── model_registry.py
│   │   ├── models/
│   │   │   ├── af2/            # AlphaFold2 plugin
│   │   │   │   ├── config.py   # AF2Config (image, viewer URL, parallelism)
│   │   │   │   ├── base.py     # AF2Tool (GPU tiers: L4/A100/A100_80GB + relax)
│   │   │   │   ├── pipeline/   # KFP: Configure → Data → ParallelFor[Predict → Relax]
│   │   │   │   ├── tools/      # 19 tools (submit, status, analysis, viewer, DB queries)
│   │   │   │   └── utils/      # FASTA validation, pipeline utils
│   │   │   ├── of3/            # OpenFold3 plugin
│   │   │   │   ├── config.py   # OF3Config (image, params path, viewer URL)
│   │   │   │   ├── base.py     # OF3Tool (GPU tiers: A100/A100_80GB, no relax)
│   │   │   │   ├── pipeline/   # KFP: ConfigureSeeds → MSA+templates → ParallelFor[Predict]
│   │   │   │   ├── tools/      # submit (use_templates=True default), analyze, get_results, open_viewer
│   │   │   │   └── utils/      # Input converter (FASTA→OF3 JSON), pipeline utils
│   │   │   └── boltz2/         # Boltz-2 plugin
│   │   │       ├── config.py   # BOLTZ2Config (image, cache path)
│   │   │       ├── base.py     # BOLTZ2Tool (GPU tiers: A100/A100_80GB only)
│   │   │       ├── pipeline/   # KFP: ConfigureSeeds → MSA(protein) → ParallelFor[Predict]
│   │   │       ├── tools/      # submit, analyze, get_results, open_viewer
│   │   │       └── utils/      # Input converter (FASTA→Boltz-2 YAML), pipeline utils
│   │   └── skills/             # ADK FunctionTool wrappers
│   │       ├── job_submission/  # submit_af2_*, submit_of3_prediction, submit_boltz2_prediction
│   │       ├── job_management/  # status, list, details, delete, GPU quota
│   │       ├── results_analysis/ # AF2 + OF3 + Boltz-2 analysis, results retrieval
│   │       ├── visualization/  # AF2 + OF3 + Boltz-2 viewer tools
│   │       └── _tool_registry.py
│   ├── databases.yaml          # Database manifest (all models)
│   ├── scripts/setup_data.py   # CLI for database downloads
│   └── tests/                  # 298 unit tests
├── src/
│   ├── alphafold-components/    # AF2 pipeline container
│   ├── openfold3-components/    # OF3 pipeline container
│   ├── boltz2-components/       # Boltz-2 pipeline container
│   ├── foldrun-viewer/          # Cloud Run web app (AF2 + OF3 + Boltz-2 3D viewer)
│   ├── foldrun-a2a/             # Cloud Run A2A protocol proxy
│   ├── af2-analysis-job/        # Cloud Run Job (AF2 analysis)
│   ├── of3-analysis-job/        # Cloud Run Job (OF3 analysis)
│   └── boltz2-analysis-job/     # Cloud Run Job (Boltz-2 analysis)
├── terraform/                   # Infrastructure as code
├── cloudbuild.yaml              # CI/CD pipeline
├── deploy-all.sh                # One-command deployment
└── check-status.sh              # Deployment health check
```

## Architecture

![FoldRun Architecture](img/foldrun-architecture.png)

```
                     ┌──────────────────┐
  A2A clients ──→    │  foldrun-a2a     │ ← A2A protocol proxy (Cloud Run)
                     │  (Cloud Run)     │
                     └───────┬──────────┘
                             │ Forwards to
┌──────────────────┐         │
│  foldrun-agent   │ ←───────┘  Conversational AI (Gemini Flash + up to 30 FunctionTools)
│  (Agent Engine)  │
└───────┬──────────┘
        │ Native tool calls
        ├──→ Vertex AI Pipelines  ← AF2 + OF3 + Boltz-2 structure prediction
        ├──→ Cloud Batch          ← Genetic database downloads
        ├──→ Cloud Run Jobs       ← Parallel analysis (AF2 + OF3 + Boltz-2) + Gemini Pro expert analysis
        └──→ Cloud Run Service    ← Interactive 3D structure viewer (AF2 + OF3 + Boltz-2)
```

## Why FoldRun vs ColabFold / Public Servers

Public tools like ColabFold and AlphaFold Server are great for academic research
but don't meet enterprise requirements for drug discovery pipelines:

| | ColabFold / AF Server | FoldRun |
|---|---|---|
| **Data sovereignty** | Sequences sent to external servers | Everything stays in your GCP project — VPC, no egress |
| **MSA computation** | ColabFold MMseqs2 server (external) | Local Jackhmmer/nhmmer on NFS-mounted databases |
| **Audit trail** | None | Full Vertex AI pipeline lineage, Cloud Logging |
| **IP protection** | No control over sequence retention | Your GCS bucket, your retention policies |
| **Regulatory** | Not GxP-compatible | Runs in your compliant GCP org with IAM controls |
| **GPU control** | Shared / queued | Dedicated A100s via DWS, configurable scheduling |
| **Multi-model** | AF2 only (ColabFold) or AF3 only (AF Server) | AF2 + OF3 + Boltz via plugin architecture |
| **Customization** | Fixed parameters | Full control: GPU tier, MSA method, seeds, samples |
| **Scale** | Rate-limited | Parallel seeds across N GPUs, batch submission |
| **Integration** | Web UI only | Conversational AI agent, API, CI/CD, Gemini analysis |

**Bottom line**: FoldRun is built for the pharma/biotech use case where proprietary
sequences (pre-clinical targets, engineered antibodies, novel drug candidates) must
never leave the organization's cloud boundary. Every step — MSA search, structure
prediction, analysis — runs within your GCP project on your infrastructure.

## Genetic Databases

All database definitions live in [`databases.yaml`](foldrun-agent/databases.yaml). The downloader
is model-aware — shared databases (uniref90, mgnify, etc.) are tagged with multiple models and
downloaded once. Installing OF3 after AF2 only downloads the OF3-specific data.

### Managing databases

```bash
cd foldrun-agent

# Check what's downloaded vs missing (per model)
uv run python scripts/setup_data.py --status

# Preview what would be downloaded for OF3
uv run python scripts/setup_data.py --models of3 --dry-run

# Download OF3 data (skips shared DBs already present from AF2)
uv run python scripts/setup_data.py --models of3

# Download Boltz-2 data (weights + CCD mols, plus shared protein MSA databases)
uv run python scripts/setup_data.py --models boltz

# Download AF2 reduced set
uv run python scripts/setup_data.py --models af2 --mode reduced

# Download everything for all models
uv run python scripts/setup_data.py --models af2,of3,boltz

# Re-download a specific database
uv run python scripts/setup_data.py --db uniref90 --force

# List all available databases
uv run python scripts/setup_data.py --list
```

### Database layout on NFS

```
/mnt/nfs/foldrun/
  uniref90/              # Shared (AF2, OF3, Boltz-2)
  mgnify/                # Shared (AF2, OF3, Boltz-2)
  pdb_seqres/            # Shared (AF2, OF3) — also used for OF3 template search
  uniprot/               # Shared (AF2, OF3)
  pdb_mmcif/             # Shared (AF2, OF3) — CIF structures for OF3 template featurization
  alphafold2/params/     # AF2 only
  small_bfd/             # AF2 only
  pdb70/                 # AF2 only
  of3/params/            # OF3 only (~2GB weights)
  of3/ccd/               # OF3 only (~500MB Chemical Component Dictionary)
  of3_msas/              # OF3 runtime — per-job MSA + template alignment files (auto-created)
  rfam/                  # OF3 only (RNA MSA via nhmmer)
  rnacentral/            # OF3 only (RNA MSA via nhmmer)
  boltz2/cache/          # Boltz-2 only — boltz2_conf.ckpt, boltz2_aff.ckpt, mols/ (CCD)
```

Monitor download progress in the [Cloud Batch console](https://console.cloud.google.com/batch/jobs).

### MSA Methods: Jackhmmer (default) vs MMseqs2 (optional)

FoldRun defaults to **Jackhmmer** (CPU-based) for MSA generation. This works
out of the box with the downloaded FASTA databases and produces high-quality
alignments. For most use cases, this is the right choice.

**MMseqs2 GPU-accelerated search** is available as an opt-in for AF2 only.
It requires a one-time index conversion step (~3-4 hours) but can speed up
MSA search significantly for specific workloads.

**When to consider MMseqs2:**
- Predicting **very large proteins** (>1000 residues) where Jackhmmer against
  full BFD takes hours
- **Batch screening** hundreds of sequences where MSA time dominates
- You're using `use_small_bfd=True` (MMseqs2 only works with FASTA databases)

**When Jackhmmer is fine (most cases):**
- Typical proteins (<500 residues) — MSA completes in 15-30 min on CPU
- The GPU predict step is the real bottleneck, not MSA
- You want to avoid the index conversion setup step
- You're using OF3 or Boltz (MMseqs2 not supported for these models)

To enable MMseqs2 for AF2, first build the indexes, then set `msa_method='mmseqs2'`
when submitting predictions. See the agent's help for details.

## Running Tests

```bash
cd foldrun-agent

# Unit tests (no GCP credentials needed)
uv run pytest tests/unit/ -v

# Integration tests (requires ADC and Gemini API access)
uv run pytest tests/integration/ -v -m integration
```

## License

Apache 2.0
