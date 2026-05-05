# Deploy Third-Party & Open Models to Gemini Enterprise via Agent Engine

Deploy AI agents powered by third-party models (Anthropic Claude) and open-source models (Google Gemma 4) to [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) using [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) and the [Agent Development Kit (ADK)](https://github.com/google/adk-python).

This guide walks through the end-to-end flow:
1. [Enable third-party models](#enable-claude-models-in-model-garden) in Vertex AI Model Garden
2. [Build and deploy](#deploy-to-agent-engine) an agent to Agent Engine using ADK
3. [Add the agent to Gemini Enterprise](#add-the-agent-to-gemini-enterprise) so users in your organization can interact with it

## Prerequisites

1. A Google Cloud project with billing enabled
2. A [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) subscription for your Google Workspace organization
3. [Vertex AI API](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com) enabled
4. [Cloud Resource Manager API](https://console.developers.google.com/apis/api/cloudresourcemanager.googleapis.com/overview) enabled
5. For Claude: enable Anthropic models in [Vertex AI Model Garden](https://console.cloud.google.com/vertex-ai/model-garden) (see [Enable Claude Models in Model Garden](#enable-claude-models-in-model-garden) below)
6. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed
7. Python 3.9+

## Enable Claude Models in Model Garden

Before using Claude models, you need to enable them in your Google Cloud project.

**Step 1.** In the Google Cloud Console, search for **Model Garden**.

![Search for Model Garden in the Cloud Console](docs/images/01-search-model-garden.png)

**Step 2.** In Model Garden, search for **Claude** to find the available Anthropic models.

![Search for Claude models in Model Garden](docs/images/02-model-garden-search-claude.png)

**Step 3.** Select a Claude model and fill out the enablement form with your business details.

![Claude model enablement form](docs/images/03-claude-enablement-form.png)

**Step 4.** Review the pricing and accept the terms and agreements, then click **Agree**.

![Review pricing and accept agreements](docs/images/04-claude-pricing-agreement.png)

## Setup

### Install dependencies

```bash
pip install google-adk anthropic[vertex]
```

### Authenticate with Google Cloud

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

## Agent Examples

### Claude (Anthropic via Vertex AI Model Garden)

Anthropic models are available in specific regions (e.g. `us-east5`). Since Agent Engine doesn't support all of those regions, we deploy Agent Engine to `us-central1` and override `GOOGLE_CLOUD_LOCATION` in `agent.py` to route model calls to the correct region.

See [`model_garden_agent/`](model_garden_agent/) for a working example.

```python
import os
from google.adk.agents.llm_agent import Agent

os.environ['GOOGLE_CLOUD_LOCATION'] = os.getenv('MODEL_LOCATION', 'us-east5')

root_agent = Agent(
    model=os.getenv('MODEL_NAME', 'claude-sonnet-4-6'),
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='Answer user questions to the best of your knowledge',
)
```

### Gemma 4 (Google Open Source)

[Gemma 4](https://ai.google.dev/gemma/docs) is Google's open-source model family. You can use it directly via the Gemini API with no region workarounds needed. Can also be deployed through model garden. 

```python
from google.adk.agents import LlmAgent
from google.genai.models import Gemini

root_agent = LlmAgent(
    model=Gemini(model="gemma-4-31b-it"),
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='Answer user questions to the best of your knowledge',
)
```

## Project Structure

```
model_garden_agent/
├── __init__.py        # from . import agent
├── agent.py           # defines root_agent
├── requirements.txt   # google-adk, anthropic[vertex]
└── .env               # GOOGLE_GENAI_USE_VERTEXAI=1, project, location
```

`.env`:
```
GOOGLE_GENAI_USE_VERTEXAI=1
GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
GOOGLE_CLOUD_LOCATION=us-central1
MODEL_NAME=claude-sonnet-4-6
MODEL_LOCATION=us-east5
```

## Test Locally

```bash
adk web model_garden_agent
```

## Deploy to Agent Engine

From the **parent directory** containing `model_garden_agent/`:

```bash
adk deploy agent_engine \
    --project=YOUR_PROJECT_ID \
    --region=us-central1 \
    --display_name="Model Garden Agent" \
    model_garden_agent
```

On success:
```
AgentEngine created. Resource name: projects/123456789/locations/us-central1/reasoningEngines/RESOURCE_ID
```

### Verify in the Console

**Step 1.** In the Cloud Console, search for **Agent Engine**.

![Search for Agent Engine in the Cloud Console](docs/images/05-search-agent-engine.png)

**Step 2.** You should see your deployed **Model Garden Agent** in the Agent Engine console.

![Agent Engine console showing the deployed agent](docs/images/06-agent-engine-deployed.png)

## Query the Deployed Agent

### Python

```python
import vertexai
from vertexai import agent_engines

vertexai.init(project="YOUR_PROJECT_ID", location="us-central1")

agent_engine = agent_engines.get("RESOURCE_ID")
session = agent_engine.create_session(user_id="test-user")
for event in agent_engine.stream_query(
    session_id=session["id"],
    message="Hello!",
    user_id="test-user",
):
    print(event)
```

### REST

```
POST https://us-central1-aiplatform.googleapis.com/v1/projects/YOUR_PROJECT_ID/locations/us-central1/reasoningEngines/RESOURCE_ID:streamQuery
```

## Add the Agent to Gemini Enterprise

Once your agent is deployed to Agent Engine, you can make it available to users in your organization through [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise). This lets users interact with your custom agent alongside Google-made agents like Deep Research and Idea Generation.

### Prerequisites

- A Gemini Enterprise subscription for your Google Workspace organization
- Admin access to the Gemini Enterprise admin console

### Steps

**Step 1.** Open the [Gemini Enterprise admin console](https://admin.google.com), navigate to **Apps > Gemini Enterprise > Agents**, and click **+ Add agent**.

![Gemini Enterprise admin console - Agents list](docs/images/07-gemini-enterprise-agents-admin.png)

**Step 2.** In the "Add an agent" dialog, select **Custom agent via Agent Engine** to connect your deployed Agent Engine agent.

![Choose agent type dialog](docs/images/08-add-agent-type.png)

**Step 3.** Configure agent authorization. You can add OAuth or service account authorizations if your agent needs to access protected resources, or click **Skip** to proceed without authorization.

![Agent authorization configuration](docs/images/09-agent-authorization.png)

**Step 4.** Once created, the agent appears in the Gemini Enterprise agents gallery under **From your organization**, alongside Google-made agents.

![Agents gallery showing custom agents from your organization](docs/images/10-gemini-enterprise-agents-gallery.png)

**Step 5.** Users in your organization can now select the agent from the sidebar and chat with it directly in Gemini Enterprise.

![Chatting with the Anthropic Sonnet agent in Gemini Enterprise](docs/images/11-chat-with-agent.png)

## Monitor

View deployed agents in the [Agent Engine Console](https://console.cloud.google.com/vertex-ai/agents/agent-engines).

## Optional: Code Execution

The agent ships with a stateful Python sandbox integration ([Agent Runtime Code Execution](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/code-execution/overview)) that's **commented out** by default. Enable it to let Claude write and run code (pandas, numpy, matplotlib, scikit-learn, openpyxl, PyPDF2, etc. preinstalled) for analyzing CSV / Excel / JSON / Parquet attachments alongside the PDFs and images Claude already reads natively.

The screenshots below are from a single GE turn where the user attached a research paper PDF and an `.xlsx` and asked for a brief summary plus a dashboard:

![GE composer with PDF and Excel attached](docs/images/12-code-execution-prompt.png)
![Patent summary text](docs/images/13-code-execution-summary.png)
![Generated dashboard with charts](docs/images/14-code-execution-dashboard.png)

### What enabling it gives you
- Claude writes Python in fenced ```python blocks; the sandbox runs them and the output is fed back as `tool_output`. Multi-step analysis works because variables, imports, and loaded DataFrames persist across blocks within a session.
- Attached `.csv`, `.xlsx`, `.xls`, `.json`, `.parquet`, `.tsv` files are pushed into the sandbox's working directory by the agent's `before_model_callback` (ADK only auto-mounts CSV out of the box, so the agent extends that itself).

### Step 1 — create the sandbox (one-time)

Replace `PROJECT_ID`, `PROJECT_NUMBER`, and `ENGINE_ID` with your values. The sandbox is a child resource of your already-deployed Agent Engine:

```bash
PROJECT_ID="YOUR_PROJECT_ID"
PROJECT_NUMBER="YOUR_PROJECT_NUMBER"
ENGINE_ID="YOUR_REASONING_ENGINE_ID"

# Find PROJECT_NUMBER if you don't know it:
# gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'
# Find ENGINE_ID by listing your engines (look for "Model Garden Agent"):
# gcloud ai reasoning-engines list --region=us-central1 --project="$PROJECT_ID"

curl -sS -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{
        "displayName": "model-garden-claude-sandbox",
        "spec": {"codeExecutionEnvironment": {}},
        "ttl": "31536000s"
      }' \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/$PROJECT_ID/locations/us-central1/reasoningEngines/$ENGINE_ID/sandboxEnvironments"
```

The response contains the new sandbox's full resource name. Copy it into `.env`:

```
SANDBOX_RESOURCE_NAME=projects/PROJECT_NUMBER/locations/us-central1/reasoningEngines/ENGINE_ID/sandboxEnvironments/SANDBOX_ID
```

(For production, omit `SANDBOX_RESOURCE_NAME` and set `AGENT_ENGINE_RESOURCE_NAME=projects/.../reasoningEngines/ENGINE_ID` instead so each session gets its own sandbox.)

### Step 2 — grant the sandbox-execute IAM role

The Agent Engine service identity needs `roles/aiplatform.user` to call `sandboxEnvironments.execute`:

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

(Skip if the project already grants this. Takes ~1 min to propagate.)

### Step 3 — uncomment the code execution blocks in `agent.py`

Search for the marker `=== OPTIONAL: Code execution` in [`agent.py`](agent.py). There are five contiguous blocks to uncomment (they're labeled in order): the imports, the helpers + `_PatchedSandboxExecutor` class, the sandbox-routing pass inside the callback, the instruction addendum, and the `code_executor=` argument on `root_agent`.

> **Why patched?** ADK 1.32's `AgentEngineSandboxCodeExecutor` has two bugs in its input-file passthrough — wrong dict key (`contents` vs `content`) and base64-encoding bytes that the SDK forwards verbatim — so attached files arrive empty. The subclass fixes both.
>
> **Why `optimize_data_file=False`?** When `True`, ADK emits a synthetic `Processing input file: NAME` event before Claude responds. Gemini Enterprise renders that placeholder as the final answer and drops every subsequent event in the turn (including Claude's actual response). Routing data files through our own callback avoids the synthetic event entirely.

### Step 4 — test

Local:

```bash
adk web .
```

Then redeploy:

```bash
adk deploy agent_engine \
    --project=$PROJECT_ID \
    --region=us-central1 \
    --agent_engine_id=$ENGINE_ID \
    --display_name="Model Garden Agent" \
    model_garden_agent
```

Attach any CSV/Excel/JSON/Parquet file in the local UI or in Gemini Enterprise and ask for an analysis ("what sheets are in this file?", "plot revenue per month", "build a dashboard").
