# Argus — Life Sciences M&A Due-Diligence Agent

Argus is a multi-agent [Google ADK](https://github.com/google/adk-python) application that helps pharma/biotech corporate-development teams evaluate acquisitions. It runs in three modes:

1. **Quick question** — a fast, sourced answer about a company, asset, trial, regulatory status, or financials via specialized sub-agents.
2. **Detailed whitepaper** — an investment-grade acquisition assessment of a named target, delivered as a downloadable **PDF** with code-generated data **charts** (matplotlib) and an AI-generated **conceptual infographic** (Nano Banana Pro / `gemini-3-pro-image`), plus an inline executive summary and optional 16:9 executive overview slides.
3. **Target screening** — a ranked shortlist of candidate acquisition targets from an acquirer's thesis (therapeutic area, modality, stage, budget).

It draws on primary data sources: **Google Cloud Agent Registry Science Skills** (`openfda_database`, `clinical_trials_database`, `chembl_database`, `opentargets_database`, `uniprot_database`, `pubmed_database`, `literature_search_europepmc`) loaded dynamically via `GCPSkillRegistry`; **SEC EDGAR** for company filings and financial metrics (cash, burn, runway); **Google Search** grounding for current news and deal comps; and three local domain methodology skills (`diligence-playbook`, `target-screening`, `whitepaper-template`).

## Architecture

```
root_agent "argus" (ADK v2 Workflow DAG)
│
├── (START -> coordinator_agent)  [Coordinator]
│     ├── sub_agents (delegated directly):
│     │     regulatory_scientific_analyst · clinical_analyst ·
│     │     financial_analyst · market_analyst
│     ├── tools: coordinator_skill_toolset, web_search, generate_slide
│     └── routing tool: launch_deep_diligence (sets route="deep_report" & session state)
│
└── (coordinator_agent -> deep_report_pipeline)  [Workflow DAG on route="deep_report"]
      ├── (START -> parallel_analysts): 6 analysts run concurrently in parallel
      │     scientific_pos · competitive · clinical_regulatory ·
      │     commercial · financial_deal · ip_fto
      │     (each writes compact findings to session state via output_key)
      │
      ├── (parallel_analysts -> join_analyst_findings): JoinNode barrier
      │     synchronizes and merges all 6 analyst findings
      │
      └── (join_analyst_findings -> report_synthesizer):
            synthesizes findings, builds charts (matplotlib) + infographic (Nano Banana Pro), generates PDF
```

### Design Notes

- **Workflow DAG & Parallel Fan-out:** The root agent is an ADK v2 `Workflow` DAG starting with `coordinator_agent`. When a full whitepaper is requested, `launch_deep_diligence` sets `tool_context.actions.route = "deep_report"` along with target metadata in session state, transitioning execution to `deep_report_pipeline`. The deep report runs six analysts concurrently in parallel (`parallel_analysts`), each writing a compact findings brief to session state via `output_key`. A `JoinNode` (`join_analyst_findings`) synchronizes completion of all parallel branches before passing execution to `report_synthesizer` (charts, infographic, PDF).
- **Dynamic Agent Registry Science Skills:** Specialist sub-agents receive a targeted subset of science skills from Google Cloud Agent Registry via ADK's native `GCPSkillRegistry` / `ScopedGCPSkillRegistry` (e.g. `private-openfda-database`, `private-clinical-trials-database`, `private-chembl-database`, `private-opentargets-database`, `private-uniprot-database`, `private-pubmed-database`, `private-literature-search-europepmc`), allowing the agent to run sandboxed Python scripts via ADK's code executor to directly query scientific APIs with strict rate limits and schemas.
- **Local Domain Methodology Skills:** Three methodology skills (`diligence-playbook`, `target-screening`, `whitepaper-template`) reside locally in `app/skills/` and are loaded on-demand via `SkillToolset` (progressive disclosure).
- **Sub-Agent Delegation:** The coordinator delegates focused domain inquiries directly to 4 specialist `sub_agents` (regulatory/scientific, clinical, financial, market).
- **Built-in Tool Isolation:** Gemini's `google_search` cannot share an agent with other tools, so it lives in a dedicated `search_agent` exposed everywhere as the `web_search` `AgentTool`.
- **Global Model Endpoint:** `gemini-3.x` models are served on the Agent Platform **global** endpoint; the agent forces `GOOGLE_CLOUD_LOCATION=global` at startup so it works when deployed to a regional Agent Runtime.
- **Visuals Split by Trust & Overview Slides:** Real numbers go in code-generated matplotlib charts (`bar_chart`, `line_chart`, `horizontal_bar_chart`); generative images (`make_infographic`, `generate_slide`) are conceptual only and captioned "Illustrative". Overview slides are generated as 16:9 PNG presentation artifacts via `generate_slide`.

---

## Prerequisites

1. A Google Cloud project with billing enabled
2. A [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) subscription (for Gemini Enterprise registration)
3. [Agent Platform API](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com) and [Agent Registry API](https://console.cloud.google.com/apis/library/agentregistry.googleapis.com) enabled
4. [Cloud Resource Manager API](https://console.developers.google.com/apis/api/cloudresourcemanager.googleapis.com/overview) enabled
5. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed with `alpha` components (`gcloud components install alpha`)
6. Python 3.13+ and `uv` installed
7. `google-agents-cli` installed (via `uv tool install google-agents-cli`)
8. **Science skills synced and activated in Google Cloud Agent Registry** (see [Step 4: Sync Science Skills to Agent Registry](#4-sync-science-skills-to-agent-registry))

---

## Setup

### 1. Install Dependencies

Dependencies are automatically managed by `uv`. Synchronize dependencies in your local environment with:

```bash
# From applications/pharma-on-gemini-enterprise/argus-on-gemini-enterprise/
uv sync
```

### 2. Authenticate with Google Cloud

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

Ensure `gcloud` alpha components are installed (required for Agent Registry operations):

```bash
gcloud components install alpha
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env` and set `GOOGLE_CLOUD_PROJECT` and `EDGAR_USER_AGENT`:

```bash
cp .env.example .env
```

`.env` (gitignored):

```
GOOGLE_GENAI_USE_ENTERPRISE=1
GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
GOOGLE_CLOUD_LOCATION=global
AGENT_REGISTRY_LOCATION=global

ARGUS_MODEL=gemini-3.7-flash
ARGUS_IMAGE_MODEL=gemini-3-pro-image
ARGUS_IMAGE_LOCATION=global
ARGUS_SHOW_THOUGHTS=1

# --- SEC EDGAR Financials ---
EDGAR_USER_AGENT= # Sample Company Name AdminContact@<sample company domain>.com
```

### 4. Sync Science Skills to Agent Registry

Argus queries primary life-sciences databases (openFDA, ClinicalTrials.gov, ChEMBL, Open Targets, UniProt, PubMed, Europe PMC) dynamically via Google Cloud Agent Registry skills. These skills must be synced and activated in your GCP project's Agent Registry before using Argus.

Use the shared [`sync_skills_to_registry.py`](../shared/scripts/sync_skills_to_registry.py) script to clone [Google DeepMind Science Skills](https://github.com/google-deepmind/science-skills), package the skills into zip archives, and register/activate them:

```bash
# Run from applications/pharma-on-gemini-enterprise/argus-on-gemini-enterprise/
uv run ../shared/scripts/sync_skills_to_registry.py --project YOUR_PROJECT_ID --location global
```

> **Note:** The script includes incremental change detection and skips uploading skills that are already active and up to date.

**Useful sync options:**
- `--dry-run`: Check which skills need sync without making changes:
  ```bash
  uv run ../shared/scripts/sync_skills_to_registry.py --project YOUR_PROJECT_ID --location global --dry-run
  ```
- `--force`: Force re-upload and update revisions for all skills:
  ```bash
  uv run ../shared/scripts/sync_skills_to_registry.py --project YOUR_PROJECT_ID --location global --force
  ```
- `--activate-all`: Activate all draft skills in the Agent Registry:
  ```bash
  uv run ../shared/scripts/sync_skills_to_registry.py --project YOUR_PROJECT_ID --location global --activate-all
  ```

---

## Run Locally

Launch the ADK web interface:

```bash
uv run adk web app
```

Or test interactively via `agents-cli`:

```bash
agents-cli playground
```

Generated whitepaper PDFs and slides appear in the **Artifacts** tab.

---

## Example Queries

**Quick questions**
- *"What's Insmed's latest reported cash position and roughly how many months of runway does it have?"*
- *"What is brensocatib's mechanism of action, bioactivity, and what indications are approved by the FDA? Cite primary sources."*
- *"Is Summit Therapeutics profitable, and what's its quarterly burn?"*

**Target screening**
- *"Merck faces the Keytruda patent cliff in 2028. Recommend 5 acquisition targets under $15B in oncology/immunology, Phase 3 or commercial stage, that could offset the revenue loss. Give strategic fit and the biggest risk for each."*
- *"Recommend 5 ADC-focused acquisition targets under $2B for a large-cap oncology buyer."*

**Detailed whitepaper (PDF with charts + infographic)**
- *"Produce a full acquisition-assessment whitepaper on Summit Therapeutics (SMMT) for Merck. Frame it around Merck's Keytruda patent-cliff problem: assess whether acquiring ivonescimab — the PD-1/VEGF bispecific that beat Keytruda head-to-head in HARMONI-2 — is a credible way to defend its oncology franchise. Cover scientific probability of success, competitive landscape, clinical and regulatory status, commercial/peak-sales potential, financials with a cash-runway read and comparable-deal valuation, and IP/exclusivity. Give a recommendation with conviction level and generate the PDF."*
- *"Give me a full acquisition whitepaper on Insmed (INSM) for a large-cap respiratory/rare-disease acquirer."*

**Executive overview slides**
- *"Create a 16:9 executive overview slide summarizing the key findings, recommendation, and value drivers for Summit Therapeutics."*

See [`samples/`](samples/) for example generated PDFs.

---

## Testing & Evaluation

Run unit and smoke tests:

```bash
uv run pytest tests/unit
```

Run integration tests:

```bash
uv run pytest tests/integration
```

Run evaluations:

```bash
agents-cli eval generate
agents-cli eval grade
```

---

## Deploy to Agent Runtime

You can deploy the agent directly using `agents-cli deploy`. The CLI will automatically package the agent code in the `app` directory and generate the necessary requirements from `pyproject.toml` and `uv.lock`.

```bash
uv run agents-cli deploy \
    --project=YOUR_PROJECT_ID \
    --region=us-central1 \
    --service-name="Argus — Life Sciences M&A Diligence" \
    --deployment-target=agent_runtime \
    --agent-identity
```

---

## Deploy with Terraform & Cloud Build (Recommended for Production)

For production and CI/CD pipelines, you can provision the Reasoning Engine infrastructure and artifact storage bucket using **Terraform**, then deploy the agent using **Cloud Build** via the shared pipeline (`shared/cloudbuild.yaml`).

### Quick Start with Cloud Build

> **Prerequisite:** You must pre-create a Google Cloud Storage bucket to store the Terraform remote state (e.g. `YOUR_STATE_BUCKET_NAME`). This ensures that the Reasoning Engine instance ID is preserved across builds.

#### Deploy only (Default):
```bash
# Run from applications/pharma-on-gemini-enterprise/argus-on-gemini-enterprise/
gcloud builds submit --config=../shared/cloudbuild.yaml \
    --substitutions=_TF_STATE_BUCKET="YOUR_STATE_BUCKET_NAME",_ENV_VARS="EDGAR_USER_AGENT=Sample Company Name AdminContact@<sample company domain>.com" \
    --project=YOUR_PROJECT_ID
```

#### Deploy and Register with Gemini Enterprise:
To automatically register the agent with Gemini Enterprise, provide your `_GEMINI_ENTERPRISE_APP_ID`:

```bash
# Run from applications/pharma-on-gemini-enterprise/argus-on-gemini-enterprise/
gcloud builds submit --config=../shared/cloudbuild.yaml \
    --substitutions=_TF_STATE_BUCKET="YOUR_STATE_BUCKET_NAME",_GEMINI_ENTERPRISE_APP_ID="projects/YOUR_PROJECT_ID/locations/global/collections/default_collection/engines/YOUR_APP_ID",_ENV_VARS="EDGAR_USER_AGENT=Sample Company Name AdminContact@<sample company domain>.com" \
    --project=YOUR_PROJECT_ID
```

This command will:
1. Run Terraform (using remote GCS state) to create the Reasoning Engine instance, artifact bucket, and IAM roles.
2. Build and package the agent code with `uv`.
3. Deploy the agent to the Reasoning Engine instance created by Terraform using `agents-cli deploy`.
4. (Optional) Register the deployed agent with your Gemini Enterprise App using `agents-cli publish gemini-enterprise`.

#### Configuration Substitutions

| Substitution | Description | Default |
| :--- | :--- | :--- |
| **`_AGENT_DIR`** | The directory of the agent relative to the repository root. | `.` |
| **`_TF_STATE_BUCKET`** | **(Required)** GCS bucket name to store Terraform remote state. | *None* |
| **`_REGION`** | The GCP region to deploy the Reasoning Engine. | `us-central1` |
| **`_LOGS_BUCKET_NAME`** | GCS bucket name to store logs data (optional, BYOB). | *None* |
| **`_ENABLE_TELEMETRY`** | Enable Cloud Observability tracing and auto-logging (`true`/`false`). | `"true"` |
| **`_GEMINI_ENTERPRISE_APP_ID`** | Gemini Enterprise app resource name (optional, triggers registration if set). | *None* |
| **`_ENV_VARS`** | Additional environment variables to set (semicolon-separated). | *None* |

---

## Project Layout

```
argus-on-gemini-enterprise/
├── pyproject.toml              # build configuration & dependencies
├── agents-cli-manifest.yaml    # manifest pointing to app directory
├── Dockerfile                  # container specification
├── deployment_metadata.json    # Agent Runtime deployment metadata
├── README.md                   # main documentation (this file)
├── .env.example                # environment variables template
├── docs/                       # documentation & assets
│   └── images/
│       ├── argus_logo.png
│       └── argus_logo_2k.png
├── terraform/                  # Terraform configuration
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── README.md
│   └── .gitignore
├── tests/                      # Unit, integration, and evaluation tests
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_sandboxed_code_executor.py
│   │   ├── test_skills_loader.py
│   │   └── test_tools.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_agent.py
│   │   └── test_server_e2e.py
│   └── eval/
│       ├── eval_config.yaml
│       ├── response_quality.py
│       └── datasets/
│           ├── basic-dataset.json
│           └── README.md
├── samples/                    # sample whitepaper PDFs
├── slides/                     # HTML overview slides
└── app/                        # Agent source code
    ├── __init__.py
    ├── agent.py                # coordinator + specialists + parallel pipeline (Workflow DAG)
    ├── fast_api_app.py         # FastAPI / A2A / reasoning engine entry point
    ├── prompts.py              # system prompts for each agent
    ├── app_utils/
    │   ├── __init__.py
    │   ├── a2a.py
    │   ├── reasoning_engine_adapter.py
    │   ├── sandboxed_code_executor.py # sandboxed code execution for skills
    │   ├── services.py         # session and GCS/InMemory artifact services
    │   ├── skills_loader.py    # ADK Agent Registry science skills + local playbooks loader
    │   └── typing.py
    ├── tools/
    │   ├── __init__.py
    │   ├── assets.py           # asset:// store for generated charts/images
    │   ├── charts.py           # matplotlib charts (bar, line, horizontal bar)
    │   ├── edgar.py            # SEC EDGAR financial search & XBRL
    │   ├── infographic.py      # Nano Banana Pro generative visuals (infographics & 16:9 slides)
    │   ├── pdf_renderer.py     # markdown -> styled PDF renderer
    │   └── whitepaper.py       # generate_whitepaper_pdf tool
    └── skills/                 # ADK local domain methodology skills
        ├── diligence-playbook/SKILL.md
        ├── target-screening/SKILL.md
        └── whitepaper-template/SKILL.md
```

---

## Caveats

- Output is AI-generated analysis for professional users. Every figure must be verified against primary sources before making investment decisions — the PDF footer and the agent both state this.
- Generative infographics are conceptual, not data — hard numbers live in the code-generated charts.
- EDGAR covers US-listed companies; private/foreign targets fall back to web search with lower data confidence.

## License

Apache 2.0 — see the LICENSE file at the repo root.
