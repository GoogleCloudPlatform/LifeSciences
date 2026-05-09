# PaperBanana on Gemini Enterprise

A lite ADK port of [PaperBanana](https://github.com/dwzhu-pku/PaperBanana) (Apache-2.0) that brings reference-driven academic figure generation to [Gemini Enterprise](https://cloud.google.com/products/gemini/enterprise) via [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview).

A user attaches a research paper PDF in the GE composer and chats about what figure they want — methodology overview, system diagram, etc. The agent runs an iterative *plan → stylize → render → critique → refine* loop powered by Gemini 3 (planner / stylist / critic) and Nano Banana Pro `gemini-3-pro-image-preview` (visualizer, 4K out of the box), then returns a publication-style diagram. Follow-up turns refine the result *in edit mode* — Nano Banana Pro takes the prior render as input, so refinements are local rather than re-renders.

See [`paperbanana_agent/README.md`](paperbanana_agent/README.md) for setup, deployment, GE registration, and full attribution / citation details.

> This is a **lite** demo. The full reference-driven multi-agent framework — including retrieval over PaperBananaBench, statistical-plot generation, parallel candidate fan-out, and high-resolution polishing — lives in the [upstream PaperBanana repo](https://github.com/dwzhu-pku/PaperBanana). Please cite the [PaperBanana paper](https://huggingface.co/papers/2601.23265) when using this work.
