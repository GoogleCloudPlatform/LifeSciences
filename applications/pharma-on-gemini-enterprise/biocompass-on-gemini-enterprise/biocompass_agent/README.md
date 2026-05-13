# BioCompass on Gemini Enterprise

A biomedical literature research agent built on Google ADK. Designed for pharma R&D, medical affairs, clinical / HEOR, and pharmacovigilance scientists who need fast, citation-grounded answers across the biomedical literature, the trial pipeline, and the entity-relationship graph.

## What it does

- **Light PubMed lookups** — fast keyword / author / PMID / citation queries via NCBI E-utilities.
- **Entity + relationship analysis** — biomedical entity extraction (genes, diseases, chemicals, mutations) and relations (drug-treats-disease, gene-interacts-gene, chemical-inhibits-gene) via PubTator3.
- **Multi-source deep research** — a `SequentialAgent` wraps a `ParallelAgent` fan-out over PubMed + Europe PMC + bioRxiv/medRxiv preprints + ClinicalTrials.gov, then synthesizes the results into a cited evidence brief and runs a critic-driven refinement loop.
- **Concept visualization** — calls Nano Banana Pro (`gemini-3-pro-image-preview`) to render mechanism diagrams, pathway schematics, study designs, PRISMA flow diagrams, and infographic panels at 2K (default) or 4K.
- **Pharma-researcher skills** — six SKILL.md playbooks loaded via ADK's [`SkillToolset`](https://adk.dev/skills/): PICO search-strategy, PRISMA systematic-review, mechanism-of-action explainer, target evidence dossier, competitive landscape scan, and drug safety signal scan.

## Architecture

```
root_agent (LlmAgent, Gemini 3 Pro, conversational)
  │   before_model_callback : reattach GE-uploaded files (PDFs, images)
  │
  ├── sub_agents (LLM-driven transfer; ADK AutoFlow)
  │     ├── literature_search_agent      — light/fast PubMed via E-utilities
  │     └── entity_analysis_agent        — PubTator3 entities + relations
  │
  └── tools
        ├── DeepResearchPipeline (AgentTool wrapping)
        │     SequentialAgent
        │       ├── PrepRequest          — stage tool args into state
        │       ├── ParallelAgent (RetrievalSwarm)
        │       │     ├── PubMedRetriever         → state["pubmed_hits"]
        │       │     ├── EuropePmcRetriever      → state["europe_pmc_hits"]
        │       │     ├── PreprintRetriever       → state["preprint_hits"]
        │       │     └── TrialsRetriever         → state["trials_hits"]
        │       ├── Synthesizer          — merge + cite
        │       ├── LoopAgent (max=N)
        │       │     ├── Critic         — JSON verdict
        │       │     └── CriticDecision — escalate or revise
        │       └── Finalize             — emit single user-visible event
        │
        ├── visualize_concept            — Nano Banana Pro figure tool
        └── SkillToolset                 — 6 SKILL.md playbooks
```

### Why these design choices

- **AgentTool wrapping** — Gemini Enterprise renders only the *first* model-authored event of a turn. Wrapping the multi-stage deep-research pipeline as an `AgentTool` keeps the chain of internal events out of the chat, with only the polished synthesis surfacing to the user (the same constraint shaped [`paperbanana-on-gemini-enterprise`](../../paperbanana-on-gemini-enterprise/)).
- **ParallelAgent state isolation** — each retriever writes to a unique `output_key` (`pubmed_hits`, `europe_pmc_hits`, `preprint_hits`, `trials_hits`) per the [ADK ParallelAgent guidance](https://google.github.io/adk-docs/agents/workflow-agents/parallel-agents/). Same key would race.
- **InstructionProvider callables** — every LlmAgent whose prompt embeds session state uses an `instruction=callable` rather than an `instruction=str`. ADK's instruction interpolator (`{+[^{}]*}+`) would mis-parse literal braces in the JSON / Europe PMC field-tag examples and try to look them up as state vars — the callable returns a fully-built string before the regex runs.
- **Critic generator-loop** — the synthesizer's draft is graded by a critic LLM that returns a JSON verdict; the loop exits early when the critic signals "no changes needed" via `EventActions(escalate=True)`, otherwise the revised draft replaces the prior one for the next round.
- **Skills, not mega-prompts** — methodology playbooks live in `skills/<name>/SKILL.md` so the L1 metadata stays in the coordinator's context but the L2 instructions only inflate the window when a skill is actually triggered.

## Prerequisites

1. A Google Cloud project with billing enabled
2. A [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) subscription (only needed for GE registration; local testing works without)
3. [Vertex AI API](https://console.cloud.google.com/apis/library/aiplatform.googleapis.com) enabled
4. Access to `gemini-3.1-pro-preview`, `gemini-3-flash-preview`, and `gemini-3-pro-image-preview` (all served from the `global` endpoint)
5. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed
6. Python 3.11+

## Setup

```bash
# From: applications/pharma-on-gemini-enterprise/biocompass-on-gemini-enterprise/
uv venv --python python3.13 .venv
uv pip install --python .venv/bin/python --index-url https://pypi.org/simple \
    -r biocompass_agent/requirements.txt

cp biocompass_agent/.env.example biocompass_agent/.env
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
COORDINATOR_MODEL_NAME=gemini-3.1-pro-preview
WORKER_MODEL_NAME=gemini-3-flash-preview
IMAGE_MODEL_NAME=gemini-3-pro-image-preview
IMAGE_SIZE=2K            # 1K, 2K, or 4K
MAX_CRITIC_ROUNDS=2

# Optional: raises NCBI rate limit from 3 -> 10 req/sec.
# PUBMED_API_KEY=
```

Get a free NCBI API key at <https://www.ncbi.nlm.nih.gov/account/settings/> if you'll be running anything heavier than smoke tests.

## Run locally

From the parent directory:

```bash
.venv/bin/adk web biocompass_agent
# UI on http://127.0.0.1:8000  — pick `root_agent` from the dropdown
```

Or CLI:

```bash
.venv/bin/adk run biocompass_agent
```

## Try it

Light lookups (route to `literature_search_agent`):

- *"Find recent randomized trials on tirzepatide for obesity, 2023-2025."*
- *"Get PMID 36652417 and summarize the key results."*
- *"Show me papers that cite PMID 28375731."*

Entity / relationship questions (route to `entity_analysis_agent`):

- *"What genes are mentioned in PMID 36652417?"*
- *"What chemicals are reported to inhibit KRAS?"*
- *"Find the PubTator3 entity ID for pembrolizumab, then list diseases it treats."*

Deep research (calls the `DeepResearchPipeline` tool):

- *"Build me an evidence brief on KRAS G12C inhibitors in NSCLC — include the full pipeline, recent readouts, and any safety signals."*
- *"Pull the literature + trial landscape for GLP-1/GIP dual agonists in MASH."*
- *"What's the current evidence base for using semaglutide in cardiovascular disease prevention?"*

Skills (the agent will trigger the matching SKILL.md):

- *"Use the PICO search-strategy skill to build me a query for: efficacy of CGRP inhibitors vs. triptans for episodic migraine in adults."*
- *"Run a PRISMA systematic-review screen for: anti-amyloid antibodies in early Alzheimer's, last 5 years."*
- *"Give me a target dossier on KEAP1."*
- *"Run a competitive landscape scan on TROP2-directed ADCs."*
- *"Do a safety signal scan on JAK inhibitors over the last 24 months."*
- *"Explain the mechanism of action of mavacamten with a diagram."*

Visualize concepts (calls Nano Banana Pro):

- *"Render a mechanism-of-action diagram for sotorasib showing covalent binding to KRAS G12C and downstream MAPK suppression."*
- *"Generate a PRISMA flow diagram with: 412 PubMed + 89 Europe PMC + 31 trials → 27 duplicates → 505 screened → 478 excluded → 27 full-text → 4 included."*

## Evaluation

The agent ships with a vanilla [ADK eval](https://google.github.io/adk-docs/evaluate/) suite — 11 cases across all coordinator lanes (light lookup, entity, deep research, skill, visualization, refusal, critical-voice push-back, conflicting-literature reconciliation, methodologic critique). Three criteria are configured: `rubric_based_final_response_quality_v1` (6 BioCompass-specific rubrics including citation grounding, takes-position-when-asked, methodologic critique, skill compliance), `rubric_based_tool_use_quality_v1` (4 rubrics covering when to invoke deep-research, skills, visualization, and PubTator3), and `hallucinations_v1` (LLM-judged groundedness against tool output).

Run the full suite:

```bash
.venv/bin/adk eval biocompass_agent \
    biocompass_agent/biocompass.evalset.json \
    --config_file_path biocompass_agent/tests/test_config.json \
    --print_detailed_results
```

Run a subset (the eval framework parallelises cases, so batches of 3-5 are kindest to the judge LLM rate limits):

```bash
.venv/bin/adk eval biocompass_agent \
    biocompass_agent/biocompass.evalset.json:pushback_lecanemab,pushback_select_trial,methodologic_critique_orr \
    --config_file_path biocompass_agent/tests/test_config.json \
    --print_detailed_results
```

Pytest wrapper (for CI):

```bash
.venv/bin/pytest biocompass_agent/tests/test_evals.py -v
```

A tools-only smoke test (no LLM, no token cost) is also included for quick regression checks after refactors:

```bash
.venv/bin/python -m biocompass_agent.tests.test_smoke
```

## Deploy to Vertex AI Agent Engine

From the parent directory containing `biocompass_agent/`:

```bash
.venv/bin/adk deploy agent_engine \
    --project=$PROJECT_ID \
    --region=us-central1 \
    --display_name="BioCompass on Gemini Enterprise" \
    biocompass_agent
```

The CLI prints the deployed agent's resource name on success — `projects/PROJECT_NUMBER/locations/us-central1/reasoningEngines/RESOURCE_ID`. Capture that resource name; you'll need it to register the agent with Gemini Enterprise.

> **Tip — keep the deploy payload small.** `adk deploy` stages everything inside the agent dir, so a stray `.adk/session.db` from local `adk web` testing (the local session/artifact store, often tens of MB) will trip the 8 MB request-size limit. The bundled [`.gitignore`](../.gitignore) covers `.adk/`, `__pycache__/`, `.venv/`, `.env`, and `*_tmp*/`. If you hit `400 INVALID_ARGUMENT: Request payload size exceeds the limit`, check for those.

### Updating an existing deployment

To redeploy in place (same engine, new code), pass `--agent_engine_id`:

```bash
.venv/bin/adk deploy agent_engine \
    --project=$PROJECT_ID \
    --region=us-central1 \
    --agent_engine_id=$RESOURCE_ID \
    --display_name="BioCompass on Gemini Enterprise" \
    biocompass_agent
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
    \"displayName\": \"BioCompass on Gemini Enterprise\",
    \"description\": \"Biomedical literature research assistant for pharma R&D, medical affairs, and clinical / HEOR teams. Searches PubMed + Europe PMC + bioRxiv/medRxiv + ClinicalTrials.gov in parallel, extracts entities + relationships via PubTator3, renders publication-style figures with Nano Banana Pro, and orchestrates pharma methodology skills.\",
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
4. Set the display name (`BioCompass on Gemini Enterprise`) and a description.
5. Configure agent authorization (or click **Skip** — BioCompass calls only public unauthenticated APIs: NCBI E-utilities, PubTator3, Europe PMC, ClinicalTrials.gov v2, and Vertex AI for Gemini / Nano Banana Pro).
6. Save. The agent appears in the GE sidebar under **From your organization**.

This is the same procedure as the [model_garden_agent's GE registration walkthrough](../../model-garden-on-gemini-enterprise/model_garden_agent/README.md#add-the-agent-to-gemini-enterprise).

### Verify

Open Gemini Enterprise, pick **BioCompass on Gemini Enterprise** from the sidebar, and try one of the multi-lane queries from the **Try it** section above (the KRAS G12C target-dossier-plus-evidence-brief-plus-MoA-diagram chain is a good "wow" demo). The deep-research pipeline takes ~60-120 s per turn (parallel retrievers + synthesizer + critic loop) — GE shows a single spinner for the duration.

## Project layout

```
biocompass_agent/
├── __init__.py
├── agent.py                       # root coordinator + GE shim + SkillToolset wiring
├── README.md
├── requirements.txt
├── .env.example
├── sub_agents/
│   ├── __init__.py
│   ├── literature_search.py       # light/fast PubMed (E-utilities)
│   ├── entity_analysis.py         # PubTator3 entities + relations
│   └── deep_research.py           # SequentialAgent[ParallelAgent → Synth → Critic loop]
├── tools/
│   ├── __init__.py
│   ├── eutils.py                  # NCBI E-utilities client + tools
│   ├── pubtator.py                # PubTator3 client + tools
│   ├── europe_pmc.py              # Europe PMC client + tools
│   ├── biorxiv.py                 # bioRxiv/medRxiv via Europe PMC preprint index
│   ├── clinicaltrials.py          # ClinicalTrials.gov v2 API
│   └── visualize_concept.py       # Nano Banana Pro figure tool
└── skills/
    ├── pico-search-strategy/SKILL.md
    ├── prisma-systematic-review/SKILL.md
    ├── mechanism-of-action-explainer/SKILL.md
    ├── target-evidence-dossier/SKILL.md
    ├── competitive-landscape-scan/SKILL.md
    └── drug-safety-signal-scan/SKILL.md
```

## Troubleshooting

- **`google.adk.skills` import error** — the SkillToolset feature requires ADK Python ≥ 1.25.0. Upgrade with `uv pip install --upgrade google-adk`.
- **`gemini-3.1-pro-preview` 404 from a regional endpoint** — Gemini 3.x is only served from the `global` endpoint. The agent sets `GOOGLE_CLOUD_LOCATION=global` in process; if you override it in `.env` to a regional value, you'll get this error. Leave `MODEL_LOCATION=global` and use `GOOGLE_CLOUD_LOCATION=us-central1` only for Agent Engine deployment.
- **Empty PubMed results on a known-good query** — NCBI rate-limits unauthenticated callers to 3 req/sec. Set `PUBMED_API_KEY` in `.env`.
- **ClinicalTrials.gov returns no studies for a sponsor query** — the API is case-sensitive on sponsor names and requires the registered legal entity (e.g. "Pfizer" not "Pfizer Inc."). Try a free-text `query` first to find the canonical sponsor string.
- **The deep-research pipeline finishes but the chat shows nothing** — usually a Gemini Enterprise rendering issue; check that the pipeline's `Finalize` agent is the LAST sub-agent in the SequentialAgent (anything emitting events after it will be the one GE displays).

## Known limitations

- The `mesh_terms` filter on `advanced_search` does not call out to the MeSH browser, so the user must supply terms in canonical form. The `pico-search-strategy` skill walks them through this.
- bioRxiv / medRxiv coverage goes through Europe PMC's preprint index — there is a 1-3 day lag relative to the bioRxiv API.
- ClinicalTrials.gov is US-centric; Asian-Pacific registries (CTRI, ChiCTR, JapicCTI) are not searched. The `competitive-landscape-scan` skill flags this.
- Pharmacovigilance scans are literature-first; FAERS / EudraVigilance / sponsor PSURs are out of scope and require dedicated data systems.

## License

Apache 2.0 — see the LICENSE file at the repo root.

This is not an officially supported Google product. This project is intended for demonstration purposes only — not for use in a production environment.
