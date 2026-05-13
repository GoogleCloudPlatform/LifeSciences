# Pharma on Gemini Enterprise

A collection of [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) custom agents targeting pharma, healthcare, and life-sciences workflows. Each agent in this directory deploys to [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) using the [Agent Development Kit (ADK)](https://github.com/google/adk-python) and registers with Gemini Enterprise as a custom agent.

## Agents

| Agent | Description |
| --- | --- |
| [Model Garden Agent](model-garden-on-gemini-enterprise) | Deploy third-party models from Vertex AI Model Garden (Anthropic Claude) to Gemini Enterprise. Bundles a GE file-attachment shim plus optional [Web Grounding for Enterprise](https://cloud.google.com/vertex-ai/generative-ai/docs/grounding/web-grounding-enterprise) search and an [Agent Runtime Code Execution](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/code-execution/overview) sandbox for analyzing CSV / Excel / JSON / Parquet attachments. |
| [PaperBanana Agent](paperbanana-on-gemini-enterprise) | Lite ADK port of Google Research's [PaperVizAgent](https://github.com/google-research/papervizagent) (Apache-2.0; originally published as PaperBanana). Attach a research paper PDF and chat about what figure you want — the agent runs an ADK `SequentialAgent` + `LoopAgent` plan / stylize / render / critique / refine pipeline using Gemini 3 + Nano Banana Pro at 4K. Follow-up turns iterate on the result in edit mode. |
| [BioCompass Agent](biocompass-on-gemini-enterprise) | Biomedical literature research agent for pharma R&D, medical affairs, and clinical / HEOR. Light PubMed lookups + PubTator3 entity analysis + a deep-research `SequentialAgent[ParallelAgent → Synth → Critic loop]` over PubMed + Europe PMC + bioRxiv/medRxiv + ClinicalTrials.gov, plus Nano Banana Pro for concept visualization and a `SkillToolset` shipping six pharma methodology skills (PICO, PRISMA, MoA, target dossier, competitive scan, PV signal scan). |

## Getting started

Pick an agent above and follow the `README.md` inside its directory. Each one is self-contained: own `.env`, own `requirements.txt`, own deploy command.
