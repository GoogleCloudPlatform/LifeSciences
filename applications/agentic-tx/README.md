# Agentic TX

AI agents for therapeutic discovery and molecular property prediction using Google ADK and TxGemma.

## Overview

Agentic TX is a multi-agent system built on Google's Agent Development Kit (ADK) that coordinates
specialized sub-agents for comprehensive drug discovery analysis. It combines scientific literature
research, molecular analysis, biological insights, and predictive modeling powered by TxGemma.

## Architecture

```
User Query
    |
Orchestrator Agent (LlmAgent)
    |
+------------+------------+------------+--------------+
| Researcher | Chemistry  | Biology    | Prediction   |
| Agent      | Agent      | Agent      | Agent        |
+------------+------------+------------+--------------+
```

### Sub-Agents

- **Researcher Agent** - PubMed literature search and scientific information retrieval
- **Chemistry Agent** - Compound lookup, structure conversion, molecular properties, therapeutic info, oral bioavailability analysis, molecule image generation (RDKit)
- **Biology Agent** - Gene descriptions, gene-to-protein translation, protein info, BLAST protein sequence identification (BioPython/NCBI)
- **Prediction Agent** - TxGemma-based prediction across 703 therapeutic tasks in 63 categories (safety screening, ADME/PK profiling, efficacy prediction)

### Alternative: Monolithic Agent

A single-agent variant (`agent_monolithic.py`) provides the same tools without sub-agent delegation,
useful for simpler deployments or debugging.

## Setup

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your project ID, TxGemma endpoint, etc.

# Install dependencies
uv sync
```

### Prerequisites

- Google Cloud project with Vertex AI enabled
- TxGemma Predict model deployed to a Vertex AI endpoint
- Gemini model access (gemini-3-flash-preview or similar)

## Configuration

The configuration system uses Pydantic Settings with hierarchical environment variable fallback:

- `GEMINI_*` - Base model configuration (model name, temperature, thinking level)
- `PREDICTION_AGENT_*` - Prediction agent overrides (TxGemma endpoint)
- `BIOLOGY_AGENT_*` - Biology agent overrides (Entrez email)
- `RESEARCHER_AGENT_*` - Researcher agent overrides (PubMed settings)

See `.env.example` for all available settings and `.env.deploy` for deployment configuration.

## Deployment

Use the included deployment CLI (`deploy/`) for Agent Engine and Agentspace deployment:

```bash
# Deploy to Vertex AI Agent Engine
python -m agents_deploy deploy --env-file .env.deploy
```

## Project Structure

```
agentic_tx/           # Core agent package
  agent.py            # Multi-agent orchestrator
  agent_monolithic.py # Single-agent variant
  config.py           # Pydantic Settings configuration
  prompts.py          # System instructions
  adk_app.py          # ADK entry point
  adk/                # Custom ADK utilities (Gemini 3 support)
  subagents/          # Specialized sub-agents
    biology_agent/
    chemistry_agent/
    prediction_agent/
    researcher_agent/
agents_shared/        # Shared libraries
  txgemma/            # TxGemma task metadata and execution
deploy/               # Deployment CLI tooling
```

## License

Apache 2.0 - See [LICENSE](../../LICENSE)

## Credits

Originally developed by Brandt Beal and Nick Losiern at Google.
