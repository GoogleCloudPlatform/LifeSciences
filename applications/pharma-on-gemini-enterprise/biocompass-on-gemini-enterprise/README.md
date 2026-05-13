# BioCompass on Gemini Enterprise

A biomedical literature research agent for pharma R&D, medical affairs, and clinical / HEOR teams. Built on the [Agent Development Kit (ADK)](https://github.com/google/adk-python) and deployed to [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview), registered with [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) as a custom agent.

BioCompass searches across PubMed (NCBI E-utilities), Europe PMC (full-text where open access), bioRxiv / medRxiv preprints, and ClinicalTrials.gov in parallel; extracts biomedical entities + relationships via PubTator3; renders publication-style figures with Nano Banana Pro (`gemini-3-pro-image-preview`); and ships six pharma-research methodology skills via ADK's [SkillToolset](https://adk.dev/skills/) (PICO search-strategy, PRISMA systematic reviews, mechanism-of-action explainers, target evidence dossiers, competitive landscape scans, and drug safety signal scans).

See [`biocompass_agent/README.md`](biocompass_agent/README.md) for architecture, setup, deployment, GE registration, and test queries.
