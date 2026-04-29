# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FoldRun A2A Agent Card definition."""

import os
from a2a.types import AgentSkill
from vertexai.preview.reasoning_engines.templates.a2a import create_agent_card

foldrun_skills = [
    AgentSkill(
        id="protein_structure_prediction",
        name="Protein Structure Prediction",
        description=(
            "Submit protein structure prediction jobs on Agent Platform Pipelines "
            "with automatic GPU selection. Supports three models: "
            "AlphaFold2 (protein monomers and multimers), "
            "OpenFold3 (protein + RNA/DNA/ligands, best RNA accuracy via nhmmer MSA), "
            "and Boltz-2 (protein + RNA/DNA/ligands + covalent modifications and glycans, "
            "plus binding affinity prediction)."
        ),
        tags=["Biology", "Protein", "AlphaFold", "OpenFold3", "Boltz-2",
              "Structure Prediction", "Drug Discovery", "Ligand Binding"],
        examples=[
            "Predict the structure of ubiquitin",
            "Submit an AlphaFold2 multimer prediction for chains A and B",
            "Fold this protein with ATP using OpenFold3",
            "Predict a glycoprotein-ligand complex with covalent modifications using Boltz-2",
            "Estimate the binding affinity of this compound against my target protein",
        ],
    ),
    AgentSkill(
        id="job_management",
        name="Job Management",
        description=(
            "List, monitor, and manage protein folding pipeline jobs. "
            "Check GPU quota, track progress, retry failed jobs, and "
            "delete completed jobs."
        ),
        tags=["Jobs", "Monitoring", "GPU", "Pipeline"],
        examples=[
            "List my running jobs",
            "Check the status of job 12345",
            "What GPU quota do I have available?",
        ],
    ),
    AgentSkill(
        id="results_analysis",
        name="Results Analysis & Visualization",
        description=(
            "Analyze prediction quality (pLDDT, PDE, ipTM, ranking score), run parallel "
            "analysis across all diffusion samples, and open interactive 3D structure "
            "viewers for AlphaFold2, OpenFold3, and Boltz-2 results. "
            "For Boltz-2, also reports binding affinity metrics (IC50, pIC50, ΔG) "
            "when affinity prediction was requested."
        ),
        tags=["Analysis", "pLDDT", "PAE", "ipTM", "Affinity", "IC50",
              "Visualization", "3D Viewer", "Gemini"],
        examples=[
            "Analyze the results from my latest job",
            "What's the pLDDT score for job 12345?",
            "Open the 3D viewer for my prediction",
            "What's the predicted binding affinity from my Boltz-2 job?",
        ],
    ),
    AgentSkill(
        id="alphafold_database",
        name="AlphaFold Database Queries",
        description=(
            "Query the AlphaFold Protein Structure Database for existing "
            "predictions, annotations, and variant effects before running "
            "new predictions."
        ),
        tags=["Database", "AlphaFold DB", "UniProt"],
        examples=[
            "Check if there's already a structure for P69905",
            "What annotations exist for human hemoglobin?",
        ],
    ),
]

foldrun_agent_card = create_agent_card(
    agent_name="FoldRun Agent",
    description=(
        "Expert AI assistant for biomolecular structure prediction on Google Cloud. "
        "Supports AlphaFold2 (protein monomers/multimers), OpenFold3 (multi-molecule "
        "complexes with full RNA MSA support), and Boltz-2 (covalent modifications, "
        "glycans, and binding affinity prediction). Handles the full lifecycle: "
        "job submission, monitoring, quality analysis, affinity interpretation, "
        "and 3D visualization — all within your GCP project."
    ),
    skills=foldrun_skills,
    streaming=True,
)
foldrun_agent_card.preferred_transport = "JSONRPC"

agent_url = os.environ.get("AGENT_BASE_URL", "http://localhost:8080")
if agent_url.endswith("/"):
    agent_url = agent_url[:-1]
foldrun_agent_card.url = agent_url
