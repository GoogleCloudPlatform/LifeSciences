# Sample Applications

This directory contains sample applications. 

Each folder in this directory contains a different sample application.

## Getting Started

1. **Prerequisites:**
    - **Google Cloud Project:** Some applications might run locally with just an API key, but most leverage Google Cloud services like Agent Platform.

2. **Running a sample application:**
    - Navigate to the sample application directory (e.g., `cd sentinel`)
    - Copy the `.env.example` file to `.env` and fill in the required values
    - Install dependencies.
    - Follow the instructions in the applications's `README.md` to run it.
    
## Applications

| Application | Description |
| --- | --- |
| [Drug Design Engine (DDE)](drug-design-engine) | An agentic pre-clinical pharmaceutical R&D system built on Scion for orchestrating multi-agent drug discovery programs. |
| [FoldRun](foldrun) | An agentic interface for protein structure prediction. |
| [Pharma on Gemini Enterprise](pharma-on-gemini-enterprise) | Custom Gemini Enterprise agents for pharma/healthcare/life-sciences workflows, including a Claude (via Agent Platform Model Garden) example with file analysis and grounded web search. |
| [Sentinel](sentinel) | Evaluate whether documents and videos are compliant with medical regulations. |