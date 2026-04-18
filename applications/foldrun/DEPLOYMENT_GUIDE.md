# FoldRun Deployment Guide

This guide covers infrastructure requirements, step-by-step deployment, validation,
and production access for FoldRun. For a quick-start overview, see [README.md](README.md).

## 1. Infrastructure Requirements and Security

### Prerequisites

**Tools (install on your workstation):**
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) with `beta` component
- [Terraform](https://developer.hashicorp.com/terraform/install) (>= 1.0)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package manager)

**GCP Project:**
- A project with billing enabled
- The deploying user needs `roles/owner` on the project (or the equivalent
  granular roles listed in the README)

**GPU Quota (check before starting):**

| Model | Minimum GPU | Recommended |
|-------|-------------|-------------|
| AlphaFold2 | 1x NVIDIA L4 | 1x NVIDIA A100 40GB |
| OpenFold3 | 1x NVIDIA A100 40GB | 1x NVIDIA A100 80GB |

Check your quota on the [GPU quota page](https://console.cloud.google.com/iam-admin/quotas?filter=gpu).
Request increases early — approvals can take hours.

### Service Accounts and IAM

Terraform creates five dedicated service accounts with least-privilege roles.
You do **not** need to configure these manually — `deploy-all.sh` handles everything.

| Service Account | ID | Purpose | Key Roles |
|---|---|---|---|
| **Agent SA** | `foldrun-agent-sa` | Agent Engine identity | `aiplatform.user`, `batch.jobsEditor`, `run.developer`, `storage.bucketViewer`, `compute.viewer` |
| **Pipelines SA** | `pipelines-sa` | Vertex AI Pipeline jobs | `aiplatform.user`, `artifactregistry.reader` |
| **Batch Compute SA** | `batch-compute-sa` | Cloud Batch download/convert jobs | `batch.agentReporter`, `logging.logWriter`, `storage.bucketViewer` |
| **Build SA** | `foldrun-build-sa` | Cloud Build CI/CD | `aiplatform.user`, `artifactregistry.writer`, `run.developer`, `compute.viewer` |
| **Viewer SA** | `foldrun-viewer-sa` | Cloud Run viewer service | `storage.objectViewer` (bucket-scoped) |
| **Analysis SA** | `foldrun-analysis-sa` | Cloud Run analysis jobs | `aiplatform.user`, `storage.objectAdmin` (bucket-scoped) |

Additionally, Terraform grants:
- **Vertex AI Custom Code SA** (`gcp-sa-aiplatform-cc`): `artifactregistry.reader` on the container repo
- **IAP Service Agent**: `run.invoker` on the viewer service

### Organization Policies

- **External IP access**: The architecture uses **Cloud NAT** for outbound internet
  access from private VMs. No external IPs are assigned to Batch or Pipeline VMs.
- **IAM domain restriction**: If your org enforces `constraints/iam.allowedPolicyMemberDomains`,
  ensure your corporate domain is allowed — needed for IAP-secured viewer access.
- **Filestore**: Uses Private Service Access (VPC peering), so
  `constraints/compute.restrictVpcPeering` must allow the FoldRun VPC.

### Network Architecture

```
┌─────────────────────────────────────────────────────────┐
│  VPC: foldrun-network (10.0.0.0/24)                     │
│                                                         │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Cloud    │  │ Vertex AI    │  │ Cloud Batch VMs   │  │
│  │ NAT      │  │ Pipeline VMs │  │ (DB downloads)    │  │
│  │ (egress) │  │ (GPU)        │  │                   │  │
│  └──────────┘  └──────────────┘  └───────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────┐            │
│  │ Filestore NFS (10.1.0.0/16 peering)     │            │
│  │ 4TB SSD — genetic databases             │            │
│  └──────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
```

## 2. Deployment Steps

All deployment is driven by a single script. No `.env` file is needed — all
configuration is derived from Terraform outputs and passed via CLI arguments.

### Step A: Authenticate and Set Project

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

If you need to **create a new GCP project**:
```bash
gcloud projects create YOUR_PROJECT_ID --organization=YOUR_ORG_ID
BILLING=$(gcloud billing projects describe EXISTING_PROJECT --format='value(billingAccountName)')
gcloud billing projects link YOUR_PROJECT_ID --billing-account="${BILLING#billingAccounts/}"
```

### Step B: Full Deployment (recommended)

```bash
cd applications/foldrun
./deploy-all.sh YOUR_PROJECT_ID
```

This runs three stages sequentially:

| Stage | What it does | Time |
|-------|-------------|------|
| **infra** | Enables 14 GCP APIs, creates Terraform state bucket, provisions VPC, Cloud NAT, Filestore, GCS buckets, Artifact Registry, service accounts, Cloud Run services | ~15 min |
| **build** | Cloud Build: builds 4 container images (AF2 components, OF3 components, viewer, analysis jobs), deploys to Cloud Run, deploys agent to Vertex AI Agent Engine | ~15 min |
| **data** | Interactive prompt for database setup (see below) | 15 min – 4 hrs |

### Step C: Database Setup

The deploy script presents three options for populating the ~500GB of genetic
databases needed for structure prediction:

| Option | Time | When to use |
|--------|------|-------------|
| **1. Download from internet** | 2–4 hours | First-time setup with no existing backups |
| **2. Restore from GCS bucket** | ~15 min | Colleague shared their bucket, or re-hydrating after Filestore rebuild |
| **3. Skip** | 0 min | Deploy the agent now, add databases later |

**Fast setup from a shared bucket** (skip the interactive prompt):
```bash
GCS_SOURCE_BUCKET=source-project-foldrun-gdbs ./deploy-all.sh YOUR_PROJECT_ID
```

Downloads run as Cloud Batch jobs in the background. The agent deploys immediately
but predictions will fail until databases are available on NFS.

### Running Individual Steps

```bash
./deploy-all.sh YOUR_PROJECT_ID --steps infra     # Only Terraform
./deploy-all.sh YOUR_PROJECT_ID --steps build     # Only Cloud Build
./deploy-all.sh YOUR_PROJECT_ID --steps data      # Only database downloads
./deploy-all.sh YOUR_PROJECT_ID --steps data --db uniref90  # Single database
./deploy-all.sh YOUR_PROJECT_ID --steps data --force        # Re-download existing
DOWNLOAD_MODE=full ./deploy-all.sh YOUR_PROJECT_ID          # Full BFD (~272GB extra)
```

### Cross-Project Database Sharing

To restore databases from an existing FoldRun project:

**1. Provision your infrastructure first** (creates the batch-compute-sa):
```bash
./deploy-all.sh YOUR_PROJECT_ID --steps infra
```

**2. The source project owner grants read access:**
```bash
gcloud storage buckets add-iam-policy-binding gs://SOURCE_PROJECT-foldrun-gdbs \
  --member="serviceAccount:batch-compute-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```

**3. Deploy with the source bucket:**
```bash
GCS_SOURCE_BUCKET=SOURCE_PROJECT-foldrun-gdbs ./deploy-all.sh YOUR_PROJECT_ID
```

## 3. What Gets Created

| Resource | Name | Purpose |
|----------|------|---------|
| VPC + Subnet + Cloud NAT | `foldrun-network` | Private network with outbound internet |
| Filestore | `foldrun-nfs` | 2.5TB NFS for genetic databases (Basic SSD) |
| GCS Bucket | `{project}-foldrun-data` | Pipeline outputs, analysis results |
| GCS Bucket | `{project}-foldrun-gdbs` | Genomic database backups |
| Artifact Registry | `foldrun-repo` | Container images |
| Cloud Run Service | `foldrun-viewer` | 3D structure viewer (IAP-secured) |
| Cloud Run Job | `af2-analysis-job` | AF2 parallel analysis + Gemini |
| Cloud Run Job | `of3-analysis-job` | OF3 parallel analysis + Gemini |
| Cloud Run Service | `foldrun-a2a` | A2A protocol proxy (optional) |
| Agent Engine | `FoldRun Assistant` | Deployed Gemini agent |

## 4. Testing and Validation

### Health Check

```bash
./check-status.sh YOUR_PROJECT_ID
```

Expected output:
```
✅ [Terraform] Infrastructure is initialized
✅ [Storage] Bucket created
✅ [Cloud Run] foldrun-viewer service is deployed and active
✅ [Cloud Run] af2-analysis-job is deployed
✅ [Vertex AI] FoldRun Agent Engine is deployed
✅ [Data] Genetic database download is COMPLETE (12/12 folders found)
```

### Unit and Integration Tests

```bash
cd foldrun-agent
uv sync --dev

# Unit tests (no GCP credentials needed)
uv run pytest tests/unit/ -v

# Integration tests (requires gcloud auth application-default login)
uv run pytest tests/integration/ -v -m integration
```

### End-to-End Smoke Test

Run the agent locally against your deployed infrastructure:

```bash
cd foldrun-agent
uv run python -m foldrun_app.cli --query "Predict the structure of ubiquitin"
```

Or use the Agent Engine playground (see Production Access below).

### Monitor Database Downloads

```bash
# List all Batch jobs
gcloud batch jobs list --project=YOUR_PROJECT_ID --location=YOUR_REGION

# Or use the Cloud Console
# https://console.cloud.google.com/batch/jobs?project=YOUR_PROJECT_ID
```

## 5. Production Access

### Agent Engine Playground

The agent is accessible via the Vertex AI Console:

1. Navigate to **Vertex AI > Agents**
2. Select **FoldRun Assistant**
3. Use the **Playground** tab

The Agent Engine ID is printed at the end of deployment and saved in
`foldrun-agent/deployment_metadata.json`.

**Example prompts:**
- "Predict the structure of ubiquitin" (AF2 monomer)
- "Fold this protein with ATP: MQIFVKTLTGKTITL..." (OF3, protein + ligand)
- "What's the structure of P69905?" (checks AlphaFold DB first)

### Structure Viewer

Results are viewed through the IAP-secured Cloud Run viewer:

```bash
# Get the viewer URL
gcloud run services describe foldrun-viewer \
  --region=YOUR_REGION \
  --format='value(status.url)' \
  --project=YOUR_PROJECT_ID
```

Access requires authentication through your organization's IAP configuration.
The `iap_access_domain` Terraform variable controls which domain's users can
access the viewer.

## 5b. A2A Protocol Proxy (Optional)

The A2A (Agent-to-Agent) proxy exposes the FoldRun agent via the [A2A protocol](https://google.github.io/a2a/),
enabling interoperability with other A2A-compatible agents. The proxy is a thin Cloud Run
service that forwards requests to the Agent Engine deployment.

### Deploy the A2A Proxy

The A2A proxy is built and deployed automatically by `deploy-all.sh` via Cloud Build.
To deploy it standalone:

```bash
cd src/foldrun-a2a
bash deploy.sh YOUR_PROJECT_ID
```

The script reads the Agent Engine resource ID from `foldrun-agent/deployment_metadata.json`
(created by the agent deploy step). You can also set it explicitly:

```bash
AGENT_ENGINE_RESOURCE=projects/<num>/locations/YOUR_REGION/reasoningEngines/<id> \
  bash deploy.sh YOUR_PROJECT_ID
```

### Grant Access to the A2A Proxy

The A2A proxy requires authentication. Callers need the `roles/run.invoker` role
on the `foldrun-a2a` Cloud Run service. Grant access to individual users, a Google
Group, or a service account:

```bash
# Grant to a Google Group (recommended for teams)
gcloud run services add-iam-policy-binding foldrun-a2a \
  --region=YOUR_REGION \
  --member="group:my-team@example.com" \
  --role="roles/run.invoker" \
  --project=YOUR_PROJECT_ID

# Grant to an individual user
gcloud run services add-iam-policy-binding foldrun-a2a \
  --region=YOUR_REGION \
  --member="user:alice@example.com" \
  --role="roles/run.invoker" \
  --project=YOUR_PROJECT_ID

# Grant to a service account (for agent-to-agent calls)
gcloud run services add-iam-policy-binding foldrun-a2a \
  --region=YOUR_REGION \
  --member="serviceAccount:other-agent-sa@OTHER_PROJECT.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --project=YOUR_PROJECT_ID
```

Callers authenticate with an identity token scoped to the service URL:
```bash
gcloud auth print-identity-token --audiences=https://YOUR_A2A_URL
```

### Test the A2A Proxy

```bash
cd src/foldrun-a2a

# Check the agent card
curl -H "Authorization: Bearer $(gcloud auth print-identity-token --audiences=https://YOUR_A2A_URL)" \
  https://YOUR_A2A_URL/.well-known/agent.json

# Run the integration test
python test_a2a.py https://YOUR_A2A_URL
```

### Connect via Gemini CLI

Create `~/.gemini/agents/foldrun.md`:

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

Then use it:

```bash
gemini -a foldrun "Predict the structure of ubiquitin"
```

The A2A URL is printed at the end of deployment. You can also retrieve it with:

```bash
gcloud run services describe foldrun-a2a --region=YOUR_REGION --format='value(status.url)'
```

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `401 Unauthorized` or `Reauthentication required` | Expired local credentials | `gcloud auth login && gcloud auth application-default login` |
| `GOOGLE_GENAI_USE_VERTEXAI` errors | Agent trying to use API keys instead of IAM | Ensure `GOOGLE_GENAI_USE_VERTEXAI=true` is set in the agent's env vars (Cloud Build sets this automatically) |
| GPU quota exceeded / no capacity | Insufficient GPU quota or regional availability | Check quota page; enable DWS FLEX_START scheduling (default) to queue for capacity |
| `service account does not exist` | SA created asynchronously after API enablement | Wait 60 seconds and retry — Terraform includes a sleep for this |
| Cloud Build timeout | OF3 components image is large (~15 min first build) | Re-run `./deploy-all.sh --steps build`; subsequent builds use Docker layer caching |
| Terraform backend error | State bucket mismatch | Script runs `terraform init -reconfigure` automatically |
| Predictions fail after deploy | Databases still downloading | Check `gcloud batch jobs list`; wait for downloads to complete |
| Viewer shows 403 | IAP not configured for your domain | Set `iap_access_domain` in Terraform and re-apply |
| A2A proxy returns 403 | Caller lacks `run.invoker` role | Grant `roles/run.invoker` on the `foldrun-a2a` service (see "Grant Access" in section 5b) |
| A2A proxy returns 500 | Agent Engine resource ID misconfigured | Check `AGENT_ENGINE_RESOURCE` env var on the Cloud Run service |

## 7. Local Development

Local development is the **only** scenario that requires a `.env` file.
Deployment via `deploy-all.sh` and Cloud Build does not use `.env`.

```bash
cd foldrun-agent
cp .env.example .env   # Edit with your project settings
uv sync
uv run python -m foldrun_app.cli
```

For the ADK web UI:
```bash
uv run adk web foldrun_app
```

## 8. Estimated Costs

| Component | Estimated Monthly Cost |
|-----------|----------------------|
| Filestore (2.5TB Basic SSD) | ~$770/mo |
| GCS (~500GB databases + results) | ~$15/mo |
| Agent Engine (idle) | ~$0 (pay per query) |
| Cloud Run (viewer + analysis, idle) | ~$0 (scale to zero) |
| GPU predictions (per job) | $2–15 per job (L4: ~$2, A100: ~$8–15) |
| Gemini API (per analysis) | ~$0.01–0.05 per analysis |

The dominant cost is Filestore (~$770/mo). To reduce costs when not in use,
consider deleting the Filestore and re-downloading databases when needed.
Terraform ignores capacity changes after provisioning, so you can resize
via Console or gcloud without drift.
