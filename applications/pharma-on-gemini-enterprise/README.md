# Pharma on Gemini Enterprise

A collection of [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) custom agents targeting pharma, healthcare, and life-sciences workflows. Each agent in this directory deploys to [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) using the [Agent Development Kit (ADK)](https://github.com/google/adk-python) and registers with Gemini Enterprise as a custom agent.

## Agents

| Agent | Description |
| --- | --- |
| [Model Garden Agent](model-garden-on-gemini-enterprise) | Deploy third-party models from Vertex AI Model Garden (Anthropic Claude) to Gemini Enterprise. Bundles a GE file-attachment shim plus optional [Web Grounding for Enterprise](https://cloud.google.com/vertex-ai/generative-ai/docs/grounding/web-grounding-enterprise) search and an [Agent Runtime Code Execution](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/code-execution/overview) sandbox for analyzing CSV / Excel / JSON / Parquet attachments. |

## Getting started

Pick an agent above and follow the `README.md` inside its directory. Each one is self-contained: own `.env`, own `requirements.txt`, own deploy command.
