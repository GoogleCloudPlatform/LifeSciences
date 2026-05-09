# PaperBanana on Gemini Enterprise (lite)

A small Google ADK agent that brings a *lite* version of [PaperBanana](https://github.com/dwzhu-pku/PaperBanana) — the reference-driven multi-agent framework for automated academic illustration — to [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) via [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview).

A user attaches a paper PDF in the GE composer and chats about what figure they want; the pipeline plans → stylizes → renders → critiques → refines a publication-style diagram. Follow-up turns ("use a softer palette", "make the encoder boxes bigger") iterate on the result *in edit mode* — the visualizer feeds the prior render back in, so refinements are local rather than re-renders.

![PaperBanana on Gemini Enterprise generating a methodology figure](docs/images/01-paperbanana-in-ge.png)

> **This is a lite demo, not the full system.** Please refer back to the [upstream PaperBanana repo](https://github.com/dwzhu-pku/PaperBanana) for the complete framework — Retriever (few-shot retrieval over PaperBananaBench), statistical-plot mode (matplotlib code-execution), the multi-candidate parallel pipeline, and the high-resolution Polish stage. **If you find the technique useful, please cite the PaperBanana paper (BibTeX below) and contribute to the upstream project.**

## Attribution & credits

This agent is a derivative work of [PaperBanana](https://github.com/dwzhu-pku/PaperBanana) (Apache-2.0), which itself derives from Google Research's [PaperVizAgent](https://github.com/google-research/papervizagent). All four system prompts in [`prompts.py`](prompts.py) are adapted verbatim from the corresponding PaperBanana files (with attribution headers in-file calling out the modifications), and [`style_guide.md`](style_guide.md) is a verbatim copy of PaperBanana's `neurips2025_diagram_style_guide.md`. See [`NOTICE`](NOTICE) for the full Apache-2.0 attribution.

**PaperBanana authors:** Dawei Zhu, Rui Meng, Yale Song, Xiyu Wei, Sujian Li, Tomas Pfister, Jinsung Yoon.

```bibtex
@article{zhu2026paperbanana,
  title={PaperBanana: Automating Academic Illustration for AI Scientists},
  author={Zhu, Dawei and Meng, Rui and Song, Yale and Wei, Xiyu and
          Li, Sujian and Pfister, Tomas and Yoon, Jinsung},
  journal={arXiv preprint arXiv:2601.23265},
  year={2026}
}
```

**Patent / commercial-use notice (reproduced from upstream):**

> Our goal is simply to benefit the community, so currently we have no plans to use it for commercial purposes. The core methodology was developed during my internship at Google, and patents have been filed for these specific workflows by Google. While this doesn't impact open-source research efforts, it restricts third-party commercial applications using similar logic.

This Gemini Enterprise port inherits the same intent: **open-source research only, not for third-party commercial deployment.** This is not an officially supported Google product.

## How it works

The pipeline is wired with ADK's workflow agents (`SequentialAgent` + `LoopAgent`) and exposed to the conversational root agent as a single `AgentTool` — that shape keeps Gemini Enterprise's "render only the first model event of a turn" constraint happy (the same constraint the [model_garden_agent](../../model-garden-on-gemini-enterprise/model_garden_agent/README.md) had to design around).

```
root_agent (Gemini 3, conversational)
  │   before_model_callback : reattach uploaded PDF (GE strips file bytes)
  └── tool: PaperBananaPipeline   (AgentTool)
        SequentialAgent
          │── PrepInputs       stage tool args + previous-turn snapshot in state
          │── Planner          LlmAgent  → state["description"]
          │── Stylist          LlmAgent + style_guide  → state["styled_description"]
          │── LoopAgent(N=3)
          │     │── Visualizer   gemini-3-pro-image-preview
          │     │                  (multimodal: prior image as edit input)
          │     │                  saves figure_{turn_id}_v{round}.png as artifact
          │     │── Critic       LlmAgent (vision)  → JSON {critic_suggestions, revised_description}
          │     `── CriticDecision  parses verdict; escalates loop on
          │                          "no changes needed", otherwise rolls
          │                          revised_description into state for next round
          `── Finalize         emits a summary referencing the saved figure
```

Mapping to PaperBanana:

| PaperBanana agent | This port | Notes |
| --- | --- | --- |
| Retriever | _skipped_ | No PaperBananaBench shipped; few-shot examples → upgrade hook in §"Extending" |
| Planner | `PaperBananaPlanner` | `DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT` ported verbatim |
| Stylist | `PaperBananaStylist` | `DIAGRAM_STYLIST_AGENT_SYSTEM_PROMPT` + `neurips2025_diagram_style_guide.md` ported verbatim |
| Visualizer | `PaperBananaVisualizer` | Uses `gemini-3-pro-image-preview` natively — feeds the prior round's image back in for true edit-mode refinement (PaperBanana's text-only re-render works here too but loses continuity) |
| Critic | `PaperBananaCritic` + `PaperBananaCriticDecision` | `DIAGRAM_CRITIC_AGENT_SYSTEM_PROMPT` ported; same JSON schema |
| Polish (2K/4K upscale) | **native, on by default** | Nano Banana Pro generates at 4K natively (`ImageConfig(image_size="4K")`); set `IMAGE_SIZE=2K` or `1K` in `.env` for faster iteration |

## Prerequisites

1. A Google Cloud project with billing enabled
2. A [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) subscription (for the GE registration step — local testing works without)
3. [Vertex AI API](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com) and [Discovery Engine API](https://console.cloud.google.com/apis/library/discoveryengine.googleapis.com) enabled
4. [Cloud Resource Manager API](https://console.developers.google.com/apis/api/cloudresourcemanager.googleapis.com/overview) enabled
5. Access to the `gemini-3.1-pro-preview` and `gemini-3-pro-image-preview` models (both served from the `global` endpoint)
6. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed
7. Python 3.10+

## Setup

```bash
# From the parent directory: applications/pharma-on-gemini-enterprise/paperbanana-on-gemini-enterprise/
uv venv --python python3.13 .venv
uv pip install --python .venv/bin/python --index-url https://pypi.org/simple \
    -r paperbanana_agent/requirements.txt

cp paperbanana_agent/.env.example paperbanana_agent/.env
# then fill in GOOGLE_CLOUD_PROJECT in .env

gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

`.env` (this file is **gitignored**):

```
GOOGLE_GENAI_USE_VERTEXAI=1
GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
GOOGLE_CLOUD_LOCATION=us-central1
MODEL_LOCATION=global
PLANNER_MODEL_NAME=gemini-3.1-pro-preview
IMAGE_MODEL_NAME=gemini-3-pro-image-preview
IMAGE_SIZE=4K            # Nano Banana Pro: 1K, 2K, or 4K
MAX_CRITIC_ROUNDS=3
```

## Project structure

```
paperbanana_agent/
├── __init__.py        # from . import agent  (ADK convention)
├── agent.py           # root LlmAgent + Sequential / LoopAgent pipeline
├── prompts.py         # planner / stylist / critic / visualizer system prompts (ported from PaperBanana)
├── style_guide.md     # NeurIPS-style guide (verbatim copy from PaperBanana)
├── requirements.txt   # google-adk, google-genai
├── NOTICE             # Apache-2.0 attribution
├── .env.example       # env vars template; .env itself is gitignored
├── docs/              # README assets
└── README.md          # this file
```

## Test locally

```bash
.venv/bin/adk web .
```

Open the URL `adk web` prints (default `http://localhost:8000`), pick `paperbanana_agent`, attach a paper PDF in the composer, and ask:

> *"Generate a methodology overview diagram with a clear left-to-right flow."*

Watch the **Events** tab on the right to see the pipeline fire (PrepInputs → Planner → Stylist → Visualizer → Critic → CriticDecision × N → Finalize). On the next turn, ask for a refinement:

> *"Use a softer pastel palette and add a 'frozen' snowflake icon on the encoder."*

The Visualizer picks up the prior figure as edit input (via `_S_PREV_TURN_IMAGE` state) and returns a refined render rather than starting from scratch.

## Deploy to Agent Engine

From the parent directory containing `paperbanana_agent/`:

```bash
.venv/bin/adk deploy agent_engine \
    --project=$PROJECT_ID \
    --region=us-central1 \
    --display_name="PaperBanana on Gemini Enterprise" \
    paperbanana_agent
```

On success the CLI prints something like:

```
✅ Created agent engine: projects/PROJECT_NUMBER/locations/us-central1/reasoningEngines/RESOURCE_ID
```

Capture that resource name — you'll need it to register the agent with Gemini Enterprise.

> **Tip — keep the deploy payload small.** `adk deploy` stages everything inside the agent dir, so a stray `.adk/session.db` from local `adk web` testing (the local session/artifact store, often tens of MB) will trip the 8 MB request-size limit. The bundled [`.gitignore`](../.gitignore) covers `.adk/`, `__pycache__/`, `.venv/`, `.env`, and `*_tmp*/`. If you hit `400 INVALID_ARGUMENT: Request payload size exceeds the limit`, check for those.

### Updating an existing deployment

To redeploy in place (same engine, new code), pass `--agent_engine_id`:

```bash
.venv/bin/adk deploy agent_engine \
    --project=$PROJECT_ID \
    --region=us-central1 \
    --agent_engine_id=$RESOURCE_ID \
    --display_name="PaperBanana on Gemini Enterprise" \
    paperbanana_agent
```

GE registration (below) survives the redeploy — the registration points at the engine ID, not a specific code revision.

## Register the agent with Gemini Enterprise

There are two paths: the Cloud Console (clicky) and the Discovery Engine REST API (scriptable). Both end up creating an `agents/` resource under your GE app. **You only need to do this once** — subsequent code redeploys flow through automatically.

### Option A — Programmatic (recommended)

Three short shell snippets. Replace the placeholders, then paste in order.

**0. Find your GE app ID**

```bash
TOK=$(gcloud auth application-default print-access-token)
PROJECT_ID="YOUR_PROJECT_ID"

curl -sS -H "Authorization: Bearer $TOK" \
     -H "X-Goog-User-Project: $PROJECT_ID" \
  "https://global-discoveryengine.googleapis.com/v1alpha/projects/$PROJECT_ID/locations/global/collections/default_collection/engines" \
  | python3 -c 'import json,sys; [print(e["name"], "|", e.get("displayName","")) for e in json.load(sys.stdin).get("engines",[])]'
```

The trailing path segment of each engine name is your `APP_ID` (e.g. `agenspace-yourname`).

> **Use ADC, not the gcloud token.** `gcloud auth print-access-token` (without `application-default`) is bound to the gcloud client ID and gets rejected by the Discovery Engine API with a CBA warning. Always use `gcloud auth application-default print-access-token` for these calls.

**1. Register the agent**

```bash
APP_ID="YOUR_GE_APP_ID"                  # from step 0
ENGINE_RESOURCE="projects/PROJECT_NUMBER/locations/us-central1/reasoningEngines/RESOURCE_ID"
                                          # from `adk deploy` output

curl -sS -X POST \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: $PROJECT_ID" \
  "https://global-discoveryengine.googleapis.com/v1alpha/projects/$PROJECT_ID/locations/global/collections/default_collection/engines/$APP_ID/assistants/default_assistant/agents" \
  -d "{
    \"displayName\": \"PaperBanana on Gemini Enterprise\",
    \"description\": \"Generate publication-style figures from an attached research paper. Lite ADK port of PaperBanana.\",
    \"adkAgentDefinition\": {
      \"provisionedReasoningEngine\": {
        \"reasoningEngine\": \"$ENGINE_RESOURCE\"
      }
    }
  }"
```

The response is the created agent resource. Its `name` ends with `agents/AGENT_ID` — keep that ID if you want to update or delete the registration later.

**2. Manage the registration**

```bash
AGENT_ID="..."   # from the response above
URL="https://global-discoveryengine.googleapis.com/v1alpha/projects/$PROJECT_ID/locations/global/collections/default_collection/engines/$APP_ID/assistants/default_assistant/agents/$AGENT_ID"

# Inspect
curl -sS -H "Authorization: Bearer $TOK" -H "X-Goog-User-Project: $PROJECT_ID" "$URL"

# Delete
curl -sS -X DELETE -H "Authorization: Bearer $TOK" -H "X-Goog-User-Project: $PROJECT_ID" "$URL"
```

There's no dedicated `gcloud agents register` command yet — the Discovery Engine REST API is the supported scriptable path. See the [official docs](https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent) for the full schema (icons, OAuth tool authorizations, etc.).

### Option B — Cloud Console (UI)

1. Open the [Gemini Enterprise admin console](https://admin.google.com), navigate to **Apps > Gemini Enterprise > Agents**, and click **+ Add agent**.
2. In the "Add an agent" dialog, select **Custom agent via Agent Engine**.
3. Paste the Agent Engine resource name from the deploy output (`projects/.../reasoningEngines/...`).
4. Set the display name and description.
5. Configure agent authorization (or click **Skip** — the bundled agent does not call OAuth-protected resources).
6. Save. The agent appears in the GE sidebar under **From your organization**.

This is the same procedure as the [model_garden_agent's GE registration walkthrough](../../model-garden-on-gemini-enterprise/model_garden_agent/README.md#add-the-agent-to-gemini-enterprise).

### Verify

Open Gemini Enterprise, pick **PaperBanana on Gemini Enterprise** from the sidebar, attach a paper PDF in the composer, and ask for a figure. You should see a result like the screenshot at the top of this README. Note: the pipeline takes ~30–60 seconds per turn (planner + stylist + N × visualizer + N × critic) — GE shows a single spinner for the duration.

## Extending

A few principled next steps if you want to push this beyond a lite demo:

- **Re-add the Retriever.** Download [PaperBananaBench](https://huggingface.co/datasets/dwzhu/PaperBananaBench) (or curate your own reference figure pool), index it with embeddings, and add a `retrieve_examples` `FunctionTool` invoked before the Planner. PaperBanana's [`agents/retriever_agent.py`](https://github.com/dwzhu-pku/PaperBanana/blob/main/agents/retriever_agent.py) is the reference implementation.
- **Add statistical-plot mode.** PaperBanana's plot path generates matplotlib code instead of an image; mount the optional code-execution sandbox from the [model_garden_agent](../../model-garden-on-gemini-enterprise/model_garden_agent/README.md#optional-code-execution) to run that code inside the Vertex AI sandbox.
- **Tweak the resolution / aspect ratio.** Visualizer renders at 4K by default (`IMAGE_SIZE=4K`); pass `2K` or `1K` for faster iteration. To pin an aspect ratio, add `aspect_ratio="16:9"` (or `"4:3"`, `"1:1"`, etc.) to the `ImageConfig` in `_build_visualizer_request` — Nano Banana Pro will respect it.
- **Parallel candidates.** PaperBanana fans out 5–20 candidates per query and lets the user pick. Wrap `paperbanana_pipeline` in a `ParallelAgent` and emit a gallery in the Finalize step.

## Disclaimer

This is not an officially supported Google product. See [NOTICE](NOTICE) for the full attribution stack and the patent / non-commercial intent reproduced from upstream PaperBanana. Use for research and demo purposes only.
