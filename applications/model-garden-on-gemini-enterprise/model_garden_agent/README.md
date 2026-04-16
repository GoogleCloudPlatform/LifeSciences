# Deploy Third-Party & Open Models to Vertex AI Agent Engine with ADK

Deploy AI agents powered by third-party models (Anthropic Claude) and open-source models (Google Gemma 4) to [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) using the [Agent Development Kit (ADK)](https://github.com/google/adk-python).

## Prerequisites

1. A Google Cloud project with billing enabled
2. [Vertex AI API](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com) enabled
3. [Cloud Resource Manager API](https://console.developers.google.com/apis/api/cloudresourcemanager.googleapis.com/overview) enabled
4. For Claude: enable Anthropic models in [Vertex AI Model Garden](https://console.cloud.google.com/vertex-ai/model-garden)
5. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed
6. Python 3.9+

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

os.environ['GOOGLE_CLOUD_LOCATION'] = 'us-east5'

root_agent = Agent(
    model='claude-sonnet-4-6',
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='Answer user questions to the best of your knowledge',
)
```

### Gemma 4 (Google Open Source)

[Gemma 4](https://ai.google.dev/gemma/docs) is Google's open-source model family. You can use it directly via the Gemini API with no region workarounds needed.

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
GOOGLE_CLOUD_LOCATION=us-east5
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

## Monitor

View deployed agents in the [Agent Engine Console](https://console.cloud.google.com/vertex-ai/agents/agent-engines).
