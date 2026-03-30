# FoldRun Development Roadmap

## High-Level Agent Interaction Flow

The agent acts as the intelligent router, input builder, and results interpreter across AlphaFold2, OpenFold3, and Boltz-2. The interaction lifecycle follows four main phases:

### 1. Intent Parsing & Routing
When the user makes a request, the agent analyzes the biological entities involved:
*   **AlphaFold2 (Default):** Selected for simple protein monomers or protein-protein multimers (FASTA format). It remains the fastest and most cost-effective option for standard proteins.
*   **OpenFold3 (AF3 open-source):** Selected when the user introduces ligands, DNA, RNA, or ions, OR explicitly asks for it.
*   **Boltz-2:** Selected when the user introduces complex molecules OR explicitly asks for the Boltz model.

### 2. Interactive Input Generation
This is where the UX diverges significantly based on the routed model:
*   **AF2:** The agent uses its existing tools to validate the FASTA sequence and prepare a simple job submission.
*   **OpenFold3:** The agent engages the user to gather missing information (e.g., "You mentioned ATP, what is the SMILES string?"). It uses a new `BuildOpenFold3Definition` tool to construct the complex **JSON** required by OF3.
*   **Boltz-2:** Similar interactive gathering, but the agent uses a `BuildBoltzDefinition` tool to construct the strictly formatted **YAML** configuration required by the Boltz CLI.

### 3. Execution & Asynchronous Monitoring
*   The agent uses model-specific submission tools (e.g., `SubmitBoltzJob`, `SubmitOpenFold3Job`) to trigger the corresponding Vertex AI Pipeline.
*   It dynamically assigns the correct hardware (e.g., T4/L4 GPUs for AF2, A100/H100s for the diffusion models).
*   The agent monitors the Vertex AI job asynchronously and notifies the user upon completion.

### 4. Analysis & Result Presentation
*   Once complete, the agent triggers a model-specific Cloud Run analysis job.
*   **AF2:** Parses `ranking_debug.json` and `pae.json` to explain protein confidence (pLDDT).
*   **OF3 / Boltz-2:** Parses complex outputs (like `confidence.npz` or `summary_confidences.json`) to explain interface confidence (ipTM) and ligand-specific error metrics (PAE).
*   **Conversational Interaction (PyMolAI-style):** The agent will provide a conversational interface to the 3D viewer, allowing users to "talk to their protein" (e.g., "Highlight the active site", "Show me the residues interacting with the ligand", "Color by hydrophobicity"). This bridges the gap between static analysis and interactive structural exploration.
*   The agent returns a conversational biological insight and provides a link to the **model-specific 3D Viewer** configured to render that model's specific output format (PDB vs. mmCIF).

---

## Assessment & Next Steps

These plans provide a robust and comprehensive architectural blueprint to start the multi-model integration. They clearly define what infrastructure is shared (NFS Filestore, core genetic DBs, Agent Engine) and what must be strictly isolated (Results buckets, Vertex Pipelines, Inference Containers, Downstream Viewers).

### Implementation Challenges to Address

As we move into implementation, we will need to tackle a few specific engineering challenges that the high-level plan abstracts:

1.  **SMILES Validation:** Before submitting an expensive A100 Vertex job, the agent (or a lightweight Cloud Run function it calls) should validate the user-provided SMILES strings using something like RDKit. If a SMILES string is invalid, the agent should catch it interactively.
2.  **Payload Sizes:** Large protein complexes will result in massive JSON/YAML definition files. The agent's `Build*Definition` tools should write these directly to GCS and pass the `gs://` URI to the Vertex Pipeline, rather than trying to pass massive strings through the Gemini context window.
3.  **GPU Quotas:** Diffusion models (OF3/Boltz) require high-end GPUs (A100s/H100s). We must ensure the GCP project has the necessary quotas, and the agent should be programmed to gracefully explain Vertex AI "Quota Exceeded" errors to the user if they occur.
4.  **Viewer Complexity & Conversational Interface:** Updating the 3D viewers (Mol*) to handle mmCIF files and map custom confidence metrics onto non-protein entities is challenging. Additionally, building a real-time, bi-directional event bridge between the Gemini Agent and the web-based 3D viewer (to enable PyMolAI-like "talk to your protein" features) will require WebSocket or similar real-time communication infrastructure.

### Immediate Next Steps
1. Update **Terraform** to provision the separate results buckets and new Cloud Run services.
2. Update **Cloud Batch** scripts to download the CCD, Rfam, and model weights.
3. Build **Docker containers** for the new OpenFold3 and Boltz-2 inference pipelines.
4. Develop the **Vertex AI Pipeline** definitions (`@dsl.pipeline`) for the new models.
5. Create the new **Agent FunctionTools** to handle the conversational input generation (JSON for OF3, YAML for Boltz) and job submission.

### Cost Estimation & Reporting (Planned)

Per-prediction cost estimation has been removed from the agent instruction pending a validated estimation model. The following items are planned:

1. **Per-prediction cost model:** Build an accurate cost calculator based on actual billing data (GPU type × runtime × machine type). Currently the runtime varies too much by sequence length, MSA depth, and model count to give reliable upfront estimates.
2. **Post-job cost reporting:** After a job completes, calculate actual cost from Vertex AI job metadata (start/end time, machine type, GPU). More useful than estimates because it's ground truth.
3. **Agent integration:** Once validated, re-add cost estimates to the agent's submission confirmation table and provide actual cost in job status/results responses.
4. **Standing infrastructure costs:** Surface monthly Filestore, Agent Engine, and GCS costs separately from per-prediction costs so customers understand the baseline vs. usage-based spend.
5. **Budget alerts:** Integrate with Cloud Billing budgets to warn users when prediction spend approaches configured thresholds.

**Dependencies:**
- Billing export or Cloud Monitoring cost data for actual GPU-hour pricing
- Sufficient job history to validate estimates across protein sizes and GPU types
- Cost estimation libraries are already in the codebase (untouched), ready to wire up once the model is validated

---

## Reference Links
*   **OpenFold (OpenFold3 efforts):** [https://github.com/aqlaboratory/openfold](https://github.com/aqlaboratory/openfold)
*   **Boltz-1:** [https://github.com/jwohlwend/boltz](https://github.com/jwohlwend/boltz) (Reference repository for Boltz models)
*   **PyMolAI:** [https://github.com/ravishar313/PyMolAI](https://github.com/ravishar313/PyMolAI)
