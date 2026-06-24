# Pharma on Gemini Enterprise

A collection of [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) custom agents targeting pharma, healthcare, and life-sciences workflows. Each agent in this directory deploys to [Agent Runtime](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime) using the [Agent Development Kit (ADK)](https://github.com/google/adk-python) and registers with Gemini Enterprise as a custom agent.

## Agents

| Agent | Description |
| --- | --- |
| [Model Garden Agent](model-garden-on-gemini-enterprise) | Deploy third-party models from Agent Platform Model Garden (Anthropic Claude) to Gemini Enterprise. Bundles a GE file-attachment shim plus optional [Web Grounding for Enterprise](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/grounding/web-grounding-enterprise) search and an [Agent Runtime Code Execution](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview) sandbox for analyzing CSV / Excel / JSON / Parquet attachments. |
| [PaperBanana Agent](paperbanana-on-gemini-enterprise) | Lite ADK port of Google Research's [PaperVizAgent](https://github.com/google-research/papervizagent) (Apache-2.0; originally published as PaperBanana). Attach a research paper PDF and chat about what figure you want — the agent runs an ADK `SequentialAgent` + `LoopAgent` plan / stylize / render / critique / refine pipeline using Gemini 3 + Nano Banana Pro at 4K. Follow-up turns iterate on the result in edit mode. |
| [BioCompass Agent](biocompass-on-gemini-enterprise) | Biomedical literature research agent for pharma R&D, medical affairs, and clinical / HEOR. Light PubMed lookups + PubTator3 entity analysis + a deep-research `SequentialAgent[ParallelAgent → Synth → Critic loop]` over PubMed + Europe PMC + bioRxiv/medRxiv + ClinicalTrials.gov, plus Nano Banana Pro for concept visualization and a `SkillToolset` shipping six pharma methodology skills (PICO, PRISMA, MoA, target dossier, competitive scan, PV signal scan). |

## Getting started

Pick an agent above and follow the `README.md` inside its directory. Each one has its own `.env` and `pyproject.toml`, uses `uv` for dependency management, and can be deployed locally or via the shared Cloud Build/Terraform pipeline.

## CI/CD Deployment Service Account

When deploying using the shared Cloud Build pipeline (`shared/cloudbuild.yaml`), the build should be executed using a custom service account. This service account must be granted the following minimal predefined IAM roles in the target Google Cloud project:

1.  **Vertex AI Admin (`roles/aiplatform.admin`)**: Required to create and update the Agent Runtime instance.
2.  **Storage Object Admin (`roles/storage.objectAdmin`)**: Required on the GCS bucket used for the Terraform state (`_TF_STATE_BUCKET`) and any staging buckets used by ADK during deployment.
3.  **Storage Admin (`roles/storage.admin`)**: Required to create and manage the GCS bucket used for logs data.
4.  **Project IAM Admin (`roles/resourcemanager.projectIamAdmin`)**: Required to allow Terraform to dynamically bind roles (`aiplatform.expressUser`, `telemetry.writer`, etc.) to the Agent Identity principal.
    *   *Security Option:* If your organization's security policy restricts the `projectIamAdmin` role, you can remove the IAM resource block from the Terraform configuration and pre-provision these roles manually for the agent identity.
5.  **Gemini Enterprise Admin (`roles/discoveryengine.agentspaceAdmin`)**: (Optional) Only required if you provide `_GEMINI_ENTERPRISE_APP_ID` to register the agent with your Gemini Enterprise App.
6.  **Cloud Build Logging**: `roles/logging.logWriter` (to write build logs).

### Setup Script

You can use the following `gcloud` commands to create the service account and apply the required roles. Replace `PROJECT_ID`, `SA_NAME`, and `STATE_BUCKET` with your values:

```bash
PROJECT_ID="YOUR_PROJECT_ID"
SA_NAME="ge-agent-deployer"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# 1. Create the Service Account
gcloud iam service-accounts create "${SA_NAME}" \
    --description="Service account for Cloud Build to deploy Gemini Enterprise Agent" \
    --display-name="Gemini Enterprise Agent Deployer" \
    --project="${PROJECT_ID}"

# 2. Grant Vertex AI Admin
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.admin"

# 3. Grant Storage Object Admin (Project-level for staging, bucket-level is preferred for state)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.objectAdmin"

# 4 Grant Storage Admin on the bucket used for logs data.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.admin"

# 5. Grant Project IAM Admin
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/resourcemanager.projectIamAdmin"

# 6. [Optional] Grant Gemini Enterprise Admin (Only needed if registering with Gemini Enterprise)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/discoveryengine.agentspaceAdmin"

# 7. Grant Logging Log Writer
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/logging.logWriter"
```


### Example deployment command:

Run this from the parent `applications/pharma-on-gemini-enterprise/` directory:

```bash
gcloud builds submit --config=shared/cloudbuild.yaml \
    --substitutions=_AGENT_DIR="biocompass-on-gemini-enterprise",_TF_STATE_BUCKET="YOUR_STATE_BUCKET_NAME" \
    --service-account="projects/YOUR_PROJECT_ID/serviceAccounts/ge-agent-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --project=YOUR_PROJECT_ID
```
