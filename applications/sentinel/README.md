# Sentinel: Pharma Ad Content Checker & Compliance Agent

Sentinel is a content analysis and compliance starter application designed to support pharmaceutical Medical, Legal, and Regulatory (MLR) affairs and marketing teams. Leveraging Google Gemini AI and the [Google Agent Development Kit (ADK)](https://github.com/google/adk-python), Sentinel automates the review process for promotional pharmaceutical content (video, infographics, imagery, copy, and documents), identifying potential compliance issues, verifying claim substantiation, checking fair balance, and evaluating adherence to custom brand rules and SOPs.

Sentinel offers two complementary modalities:
1. **Agentic MLR Workflow & A2A Agent (`app/`)**: A multi-agent ADK v2 `Workflow` DAG running a comprehensive review pipeline (intake, 6-reviewer parallel panel, 3-critic calibration panel, review loop decider, and synthesizer). It exposes native A2A (Agent-to-Agent) protocol endpoints, allowing Sentinel to be deployed to **Agent Runtime** and integrated as a compliance verification step within broader life-sciences agent workflows or registered directly in [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise).
2. **Interactive Web Application (`frontend/` + `api/`)**: A dedicated single-page React frontend and FastAPI REST backend providing human-in-the-loop inspection, video timestamp markers, bounding-box annotations on infographics, and Google Cloud Storage integration.

---

## Agent Architecture (`app/`)

The Sentinel agent in `app/` is structured as an **ADK v2 Workflow DAG** orchestrating specialized LLM agents across parallel panels and an iterative critique loop:

```
root_agent "sentinel" (ADK v2 Workflow DAG)
│
├── START ──► _load_custom_rules (FunctionNode)
│               Loads user-uploaded brand/SOP rules artifact into session state
│
├── _load_custom_rules ──► intake (LlmAgent: ContentInventory)
│                            Catalogues every reviewable element into a structured inventory
│
├── intake ──► decide_intake_route (FunctionNode router)
│                ├── route="direct_response" ──► direct_responder
│                │                                 Directly responds to non-promotional questions, greetings, or inquiries
│                └── route="review"          ──► reviewer_panel (Parallel: 6 reviewers run concurrently)
│                                                  ├── medical_reviewer      (Clinical lens: accuracy, dosing, efficacy, safety, fair balance)
│                                                  ├── legal_reviewer        (Legal lens: claim substantiation, comparative claims, citations)
│                                                  ├── regulatory_reviewer   (Regulatory lens: indication scope, off-label, ISI, PI consistency)
│                                                  ├── editorial_reviewer    (Editorial lens: clarity, tone, accessibility, typography)
│                                                  ├── submitter_advocate    (Argues for the submission, produces a defense brief)
│                                                  └── rules_reviewer        (Custom rules lens: evaluates against uploaded brand SOPs)
│
├── reviewer_panel ──► join_reviewers (JoinNode barrier)
│                        Synchronizes and merges findings across all 6 reviewer lenses
│
├── join_reviewers ──► critic_panel (Parallel: 3 critics run concurrently)
│                        ├── dedupe_critic     (Identifies duplicate findings across lenses and cross-lens themes)
│                        ├── severity_critic   (Calibrates severity & confidence, weighing submitter defense brief)
│                        └── gap_critic        (Surfaces missed compliance risks and proposes net-new findings)
│
├── critic_panel ──► join_critics (JoinNode barrier)
│                      Synchronizes completion of all 3 critic agents
│
├── join_critics ──► critic_merger (LlmAgent: CriticAssessment)
│                      Consolidates critic outputs and recommends whether another review pass is needed
│
├── critic_merger ──► decide_review_loop (FunctionNode router)
│                       ├── route="iterate"    ──► loops back to reviewer_panel (capped at 2 iterations)
│                       └── route="synthesize" ──► synthesizer
│
└── decide_review_loop ──► synthesizer (LlmAgent: FinalReport)
                             Produces the final consolidated MLR report with categorized findings and action items
```

### Architectural Highlights

- **Intake Short-Circuit Gate:** Non-promotional utterances (such as general questions, greetings, or conversational queries) are detected by `intake` and routed via `decide_intake_route` directly to `direct_responder`, avoiding unnecessary 13-node MLR pipeline execution.

- **Parallel Reviewer Fan-Out:** The intake inventory is analyzed concurrently by 6 specialized reviewers. In addition to standard Medical, Legal, Regulatory, and Editorial reviewers, the **Submitter Advocate** argues in favor of the submission, ensuring balanced criticism and preventing excessive false positives.
- **Custom Rules Injection:** If a user uploads custom brand rules or SOPs (as a `.txt` or `.md` artifact), `_load_custom_rules` automatically injects them into session state for the `rules_reviewer` to check market-specific restrictions, tone guidelines, or corporate policies.
- **Three-Way Critic Panel & Calibration:** Rather than accepting raw reviewer comments, a parallel critic panel (`dedupe_critic`, `severity_critic`, `gap_critic`) deduplicates overlapping findings across lenses, cross-examines reviewer severity ratings against the submitter's defense, and identifies blind spots.
- **Iterative Review Loop:** The `critic_merger` evaluates overall findings and issues an iteration recommendation. `decide_review_loop` dynamically iterates the reviewer and critic panels (up to `_MAX_REVIEW_ITERATIONS = 2`) before passing execution to the synthesizer.
- **A2A Interoperability:** Implements the Agent-to-Agent protocol (`/a2a/app/`) and JSON-RPC 2.0 streaming, making Sentinel callable by other orchestrators or multi-agent pipelines as a modular compliance checker.

---

## Prerequisites

1. Google Cloud Project with billing enabled
2. Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) package manager
3. `google-agents-cli` installed:
   ```bash
   uv tool install google-agents-cli
   ```
4. `gcloud` CLI installed and authenticated
5. Node.js 20+ and npm (only needed for the React frontend webapp)

---

## Setup & Local Development

### 1. Install Dependencies

Dependencies are managed with `uv`. Synchronize the virtual environment from the `applications/sentinel/` directory:

```bash
uv sync --all-extras
```

### 2. Google Cloud Authentication & Environment Configuration

Authenticate with Google Cloud:

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

Copy `.env.example` to `.env` and configure your settings:

```bash
cp .env.example .env
```

`.env` configuration:
```env
# Agent Platform Configuration (Recommended)
GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_ENTERPRISE=True

# Models
GEMINI_MODEL_FAST=gemini-3.7-flash
GEMINI_MODEL_POWERFUL=gemini-3.7-flash

# GCS Storage (for image/video storage)
GCS_BUCKET_NAME=YOUR_BUCKET_NAME
```

### 3. Run the Agent Locally

You have several ways to interact with and test the Sentinel agent locally:

* **Interactive CLI Testing (`agents-cli playground`)**:
  ```bash
  agents-cli playground
  ```
* **ADK Web UI**:
  ```bash
  uv run adk web app
  ```
* **Run the Agent FastAPI & A2A Server**:
  ```bash
  uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port 8000 --reload
  ```
  The server exposes:
  - A2A JSON-RPC endpoint: `http://localhost:8000/a2a/app/`
  - A2A Agent Card: `http://localhost:8000/a2a/app/.well-known/agent-card.json`
  - SSE Stream: `http://localhost:8000/run_sse`
  - Feedback endpoint: `http://localhost:8000/feedback`

### 4. Run the Full-Stack Web Application Locally

To run the interactive UI alongside the standalone REST API:

* **Start the Backend API**:
  ```bash
  uv run python -m api.main
  ```
  API runs at `http://localhost:8000`.

* **Start the React Frontend**:
  ```bash
  cd frontend
  npm install
  npm run dev
  ```
  Frontend runs at `http://localhost:5173`.

---

## Testing & Evaluation

### Unit and Integration Tests

Run unit tests verifying the ADK v2 Workflow DAG, nodes, reviewer/critic panels, and loop routing:

```bash
uv run pytest tests/unit
```

Run integration tests (verifies streaming responses, A2A endpoints, and Agent Card generation):

```bash
uv run pytest tests/integration
```

### Evaluation (Quality Flywheel)

Sentinel includes an evaluation framework configured in `tests/eval/eval_config.yaml` using LLM-as-a-judge metrics in `tests/eval/response_quality.py`:

```bash
# 1. Synthesize multi-turn scenarios (optional)
agents-cli eval dataset synthesize

# 2. Run agent on eval datasets to produce traces
agents-cli eval generate

# 3. Grade the traces against quality and compliance metrics
agents-cli eval grade
```

---

## Deploying Sentinel Agent to Agent Runtime

Sentinel is configured for [Google Cloud Agent Runtime](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime) via `agents-cli-manifest.yaml`.

### Direct Deployment via `agents-cli`

You can deploy the agent directly using `agents-cli deploy`. The CLI packages the `app/` directory and generates deployment requirements:

```bash
uv run agents-cli deploy \
    --project=YOUR_PROJECT_ID \
    --region=us-central1 \
    --service-name="Sentinel Agent" \
    --deployment-target=agent_runtime \
    --agent-identity
```

---

## Deploy with Terraform & Cloud Build (Recommended for Production)

For production environments and CI/CD pipelines, Sentinel includes Terraform configuration (`terraform/`) invoking the shared `agent-infrastructure` module, and can be deployed via the shared Cloud Build pipeline (`shared/cloudbuild.yaml`).

### Quick Start with Cloud Build

> **Prerequisite:** Pre-create a Google Cloud Storage bucket for Terraform remote state (e.g. `YOUR_STATE_BUCKET_NAME`).

#### Deploy Agent Infrastructure & Runtime:
```bash
# Run from applications/sentinel/
gcloud builds submit --config=../pharma-on-gemini-enterprise/shared/cloudbuild.yaml \
    --substitutions=_TF_STATE_BUCKET="YOUR_STATE_BUCKET_NAME" \
    --project=YOUR_PROJECT_ID
```

This automated pipeline:
1. Provisions the Reasoning Engine instance, logs bucket, and IAM roles via Terraform (using remote GCS state).
2. Packages the agent source code (`app/`) with `uv`.
3. Deploys the agent to the Agent Runtime instance using `agents-cli deploy`.

#### Configuration Substitutions

| Substitution | Description | Default |
| :--- | :--- | :--- |
| **`_AGENT_DIR`** | Path to the agent directory relative to repo root. | `.` |
| **`_TF_STATE_BUCKET`** | **(Required)** GCS bucket name for Terraform remote state. | *None* |
| **`_REGION`** | Google Cloud region for Agent Runtime deployment. | `us-central1` |
| **`_LOGS_BUCKET_NAME`** | Optional GCS bucket for runtime telemetry and logs. | *None* |
| **`_ENABLE_TELEMETRY`** | Enable Cloud Observability tracing and logging (`true`/`false`). | `"true"` |
| **`_GEMINI_ENTERPRISE_APP_ID`** | Gemini Enterprise application resource name for registration. | *None* |
| **`_ENV_VARS`** | Additional environment variables (semicolon-separated). | *None* |

---

## A2A (Agent-to-Agent) Integration & Multi-Agent Workflows

Sentinel is designed as a standalone compliance agent and as a **downstream A2A service** for other agents and composite workflows.

### A2A Endpoints

When running (`app/fast_api_app.py`), Sentinel exposes standard A2A endpoints:
- **Agent Card**: `GET /a2a/app/.well-known/agent-card.json`
- **JSON-RPC Endpoint**: `POST /a2a/app/` (supports `message/send`, `message/sendStreaming`, task lifecycle methods)

### Invoking Sentinel from Another Agent Workflow

Other agents (e.g. content generation agents, regulatory dossier builders) can invoke Sentinel over A2A:

```python
from a2a.client import A2AClient
from a2a.types import Message, Part, Role, TextPart

# Connect to deployed Sentinel A2A endpoint
client = A2AClient(base_url="https://YOUR_SENTINEL_ENDPOINT/a2a/app")

# Send promotional copy or asset for compliance check
response = await client.send_message(
    Message(
        role=Role.USER,
        parts=[
            TextPart(
                text=(
                    "Review the following draft claim for OncoBoost (gemcitabine combo): "
                    "'OncoBoost delivers unmatched 95% survival with zero serious adverse events.' "
                    "Provide a full MLR risk breakdown."
                )
            )
        ],
    )
)
```

---

## Docker Deployment

Sentinel provides two Docker container options:

1. **`Dockerfile` (Agent & A2A Server)**:
   Builds the lightweight Python container running `app.fast_api_app:app` on port 8080:
   ```bash
   docker build -f Dockerfile -t sentinel-agent .
   docker run -p 8080:8080 --env-file .env sentinel-agent
   ```

2. **`Dockerfile.webapp` (Full-Stack Web Application)**:
   Multi-stage build compiling the React frontend into static assets served by FastAPI `api.main:app`:
   ```bash
   docker build -f Dockerfile.webapp -t sentinel-webapp .
   ```

   * **Run with AI Studio (API Key)**

      ```bash
      docker run -p 8080:8080 --env-file .env sentinel-webapp
      ```

   * **Run with Agent Platform**

      You need to provide your Google Cloud credentials to the container.

      ```bash
      # Authenticate locally first
      gcloud auth application-default login

      # Run container with mounted credentials and environment file
      # We use --user to ensure the container can read the mounted credentials
      docker run -p 8080:8080 \
      --user $(id -u):$(id -g) \
      -v ~/.config/gcloud/application_default_credentials.json:/app/gcp_creds.json \
      -e GOOGLE_APPLICATION_CREDENTIALS=/app/gcp_creds.json \
      --env-file .env \
      sentinel-webapp
      ```

---

## Cloud Run Deployment for Full-Stack Web Application

Follow these steps to deploy the full-stack interactive web application (`Dockerfile.webapp`) to Google Cloud Run with Identity-Aware Proxy (IAP):

### 1. Set Environment Variables
```bash
export PROJECT_ID=[YOUR_PROJECT_ID]
export REGION=us-central1
export BUCKET_NAME=sentinel-images-$PROJECT_ID

gcloud config set project $PROJECT_ID
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
```

### 2. Enable Required APIs
```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  iap.googleapis.com \
  storage.googleapis.com \
  cloudresourcemanager.googleapis.com
```

### 3. Create Artifact Registry Repository & Push Image
```bash
gcloud artifacts repositories create docker-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="Docker repository"

gcloud auth configure-docker $REGION-docker.pkg.dev

export IMAGE_URI=$REGION-docker.pkg.dev/$PROJECT_ID/docker-repo/sentinel:latest
docker build -f Dockerfile.webapp -t $IMAGE_URI .
docker push $IMAGE_URI
```

### 4. Create Storage Bucket and Service Account
```bash
gcloud storage buckets create gs://$BUCKET_NAME --location=$REGION

export SA_NAME=sentinel-sa
export SA_EMAIL=$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com

gcloud iam service-accounts create $SA_NAME --display-name="Sentinel Application Service Account"

gcloud storage buckets add-iam-policy-binding gs://$BUCKET_NAME \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.objectUser"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/aiplatform.user"
```

### 5. Deploy to Cloud Run with IAP
```bash
gcloud beta run deploy sentinel \
  --image $IMAGE_URI \
  --region $REGION \
  --no-allow-unauthenticated \
  --iap \
  --service-account $SA_EMAIL \
  --set-env-vars="GOOGLE_GENAI_USE_ENTERPRISE=true,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,GCS_BUCKET_NAME=$BUCKET_NAME"

gcloud run services add-iam-policy-binding sentinel \
  --region $REGION \
  --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

gcloud beta iap web add-iam-policy-binding \
  --resource-type=cloud-run \
  --service=sentinel \
  --region=$REGION \
  --member="user:[USER_EMAIL]" \
  --role="roles/iap.httpsResourceAccessor"
```

---

## Project Structure

```
sentinel/
├── agents-cli-manifest.yaml   # Manifest pointing to app/ directory for agents-cli
├── pyproject.toml             # Python project dependencies, metadata, and tool configs
├── uv.lock                    # Locked dependency tree
├── Dockerfile                 # Container image for ADK agent / A2A server (app.fast_api_app:app)
├── Dockerfile.webapp          # Multi-stage build for full-stack webapp (frontend + api.main:app)
├── README.md                  # Main documentation (this file)
├── .env.example               # Environment variable template
│
├── app/                       # ADK v2 Multi-Agent Workflow & A2A Server
│   ├── __init__.py
│   ├── agent.py               # Root Workflow DAG, parallel reviewer/critic panels, loop routing
│   ├── fast_api_app.py        # FastAPI server with A2A protocol & Reasoning Engine adapter
│   ├── prompts.py             # Reviewer, critic, and synthesizer prompt templates
│   ├── prompts/               # Markdown prompt definitions for each agent role
│   │   ├── intake.md
│   │   ├── medical_reviewer.md
│   │   ├── legal_reviewer.md
│   │   ├── regulatory_reviewer.md
│   │   ├── editorial_reviewer.md
│   │   ├── submitter_advocate.md
│   │   ├── rules_reviewer.md
│   │   ├── dedupe_critic.md
│   │   ├── severity_critic.md
│   │   ├── gap_critic.md
│   │   ├── critic_merger.md
│   │   ├── loop_decider.md
│   │   └── synthesizer.md
│   ├── schemas.py             # Pydantic schemas (ContentInventory, ReviewerOutput, FinalReport)
│   └── app_utils/             # Utility modules
│       ├── __init__.py
│       ├── a2a.py             # A2A route attachment and Agent Card generator
│       ├── reasoning_engine_adapter.py # Reasoning Engine compatibility adapter
│       ├── services.py        # Session and artifact service providers
│       └── typing.py          # Feedback schemas and data models
│
├── api/                       # Standalone REST API for the interactive webapp
│   ├── __init__.py
│   ├── main.py                # Web application API entry point
│   ├── config.py              # Configuration management
│   ├── dependencies.py        # Dependency injection
│   ├── models/                # Pydantic request/response models
│   │   └── schemas.py
│   ├── routes/                # API route handlers (analysis, storage, health)
│   │   ├── analysis.py
│   │   ├── health.py
│   │   └── storage.py
│   └── services/              # Analyzer services, Gemini client, prompt templates
│       ├── analyzer_service.py
│       ├── gemini_client.py
│       └── prompts.py
│
├── frontend/                  # React + TypeScript single-page application
│   ├── src/                   # React components, viewer, types
│   ├── public/                # Static assets
│   ├── index.html             # HTML entry point
│   ├── package.json           # Node.js dependencies
│   └── vite.config.ts         # Vite build configuration
│
├── terraform/                 # Terraform configuration for Agent Runtime
│   ├── main.tf                # Invokes shared agent-infrastructure module
│   ├── variables.tf           # Project, region, and logs bucket variables
│   ├── outputs.tf             # Reasoning engine IDs, display name, agent identity
│   └── .gitignore
│
└── tests/                     # Unit, integration, and evaluation test suites
    ├── __init__.py
    ├── unit/                  # Unit tests (Workflow graph structure, loop router, panels)
    │   └── test_agent_workflow.py
    ├── integration/           # Integration & E2E tests (A2A server, SSE stream, agent runner)
    │   ├── test_agent.py
    │   └── test_server_e2e.py
    ├── legacy/                # Tests for the legacy FastAPI web app (api/), with their conftest
    └── eval/                  # ADK agent evaluation framework
        ├── eval_config.yaml   # Metric configuration (custom_response_quality, agent_turn_count)
        ├── response_quality.py # LLM-as-judge evaluation functions
        └── datasets/          # Evaluation datasets and guidelines
            └── README.md
```

---

## API Endpoints Reference

### Agent & A2A Server (`app/fast_api_app.py`)
- `GET /a2a/app/.well-known/agent-card.json` — Returns A2A Agent Card with capabilities and skills metadata.
- `POST /a2a/app/` — A2A JSON-RPC 2.0 endpoint (`message/send`, `message/sendStreaming`).
- `POST /run_sse` — ADK Server-Sent Events (SSE) streaming endpoint.
- `POST /feedback` — Logs user feedback to Google Cloud Logging.

### Full-Stack Web Application API (`api/main.py`)
- `GET /health` — Health check endpoint.
- `POST /api/v1/analyze` — Analyze video (YouTube URL) or image (URL).
- `POST /api/v1/analyze/upload` — Analyze uploaded image file.
- `GET /api/v1/storage/list` — List files in Google Cloud Storage bucket.
- `POST /api/v1/storage/upload` — Upload file to Google Cloud Storage.
- `GET /api/v1/storage/file/{path}` — Retrieve/stream file from Google Cloud Storage.

---

## Usage

### Analyze a YouTube Video (Web App)

1. Select "YouTube Video" from the content type dropdown
2. Paste the YouTube URL
3. Optionally adjust the frame rate (lower = fewer tokens used)
4. Click "Analyze"

### Analyze an Image / Infographic (Web App)

1. Select "Image URL" or "Upload Image"
2. Provide the image URL or select a file
3. Click "Analyze"
4. Click on numbered markers to see issue details

---

## Important Disclaimers

**Operational and Educational Support Only:**
Sentinel code is a content analysis starter and developer accelerator for administrative and operational support only. It is not intended for any medical purpose (diagnosis, treatment, prevention, or alleviation of disease). All outputs from Sentinel should be considered preliminary and require independent verification through established internal MLR processes and regulatory methodologies. This code has not undergone software validation (CSV) or penetration testing for regulated production environments.

> [!IMPORTANT]
> **A Note for Developers and Administrators:**
> By default, Agent Platform may collect data to improve service quality. Data collection and logging are **only disabled** if the user explicitly disables **Agent Platform data caching** within Google Cloud project settings.
> For technical details, refer to the official [Gemini Enterprise Agent Platform and zero data retention documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/zero-data-retention).

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

Apache 2.0 — see the LICENSE file in the repository root.
