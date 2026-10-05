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

"""FoldRun Agent - Main agent logic.

Multi-model protein structure prediction agent supporting AlphaFold2 and
OpenFold3. Deployed to Agent Runtime or run locally via ADK.
"""

import logging
import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.flows.llm_flows.base_llm_flow import LlmResponse
from google.genai import types

# Import all skill wrapper functions

logger = logging.getLogger(__name__)

# Load environment variables from agent's .env
agent_env = os.path.join(os.path.dirname(__file__), "../.env")
if os.path.exists(agent_env):
    load_dotenv(agent_env, override=False)

from foldrun_app.skills import skill_registry  # noqa: E402
from foldrun_app.v1_instruction import V1_AGENT_INSTRUCTION  # noqa: E402

# Monolithic v1 instructions retained for fallback / rollback path
AGENT_INSTRUCTION = V1_AGENT_INSTRUCTION

# Modular v2 coordinator instructions for native ADK skills
FOLDRUN_V2_INSTRUCTION = """You are FoldRun, an expert protein structure prediction and macromolecular modeling assistant.
You support AlphaFold2 (proteins), OpenFold3 (complexes, RNA with nhmmer MSA, DNA, ligands), and Boltz-2 (covalent mods, glycans).

You operate via modular ADK skills (agentskills.io standard). Use progressive disclosure to perform tasks:
1. Call `list_skills` to discover available specialized domain skills when needed.
2. Call `load_skill` to load instructions and activate domain-specific tools for the task at hand:
   - `job-submission`: Pre-submission confirmation table, hardware rules, and submitting AF2, OF3, or Boltz-2 jobs.
   - `job-management`: Checking job status, quota inspection, task failure analysis, and safe job deletion.
   - `results-analysis`: Confidence metric evaluation (pLDDT, PAE, ipTM, ranking scores) and Cloud Run parallel analysis.
   - `visualization`: Interactive 3D Mol* structure viewer links.
   - `cost-estimation`: Per-job and monthly GCP infrastructure pricing with DWS FLEX_START spot comparisons.
   - `database-queries`: AlphaFold DB structure and annotation lookups.
   - `storage-management`: Safe two-step GCS hygiene and orphan output cleanup.
3. Call `load_skill_resource` to read deep domain references (e.g., `references/of3-metrics.md`) when analyzing metrics.
4. Execute activated domain tools to perform operations.

## Response Guidelines
- Always verify job status with `check_job_status` before reporting results. NEVER hallucinate job completion or status.
- Present required pre-submission confirmation tables and safety warnings as specified by the relevant skill.
- After every response, suggest 2-3 contextual next actions as numbered choices.
"""


def _retry_on_resource_exhausted(callback_context, llm_request, error):
    """Handle 429 RESOURCE_EXHAUSTED by returning a graceful message instead of crashing."""
    error_str = str(error)
    if "429" not in error_str and "RESOURCE_EXHAUSTED" not in error_str:
        return None  # Not a rate limit error — let it propagate

    logger.warning("429 RESOURCE_EXHAUSTED from Gemini API — returning graceful message")

    # Return LlmResponse (not raw Content) — ADK tracing expects usage_metadata attribute
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text=(
                        "I'm temporarily rate-limited by the Gemini API (429 RESOURCE_EXHAUSTED). "
                        "This is a transient issue — please try your request again in a few seconds."
                    )
                )
            ],
        )
    )


def __getattr__(name: str):
    """Lazy module attribute getter to avoid import-time tool compilation side effects."""
    if name == "all_tools":
        return skill_registry.compile_tools()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _get_agent_config_context(gemini_model: str) -> str:
    """Build configuration context string for agent instructions."""
    project_id = (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT_ID")
        or "Not configured"
    )
    region = (
        os.getenv("GCP_REGION")
        or os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("VERTEX_LOCATION")
        or "us-central1"
    )
    gcs_bucket = (
        os.getenv("GCS_BUCKET_NAME") or os.getenv("VERTEX_STAGING_BUCKET") or "Not configured"
    )
    viewer_base_url = os.getenv("FOLDRUN_VIEWER_URL") or "Not configured"

    return f"""
## Your Current Configuration

**Google Cloud Environment:**
- Project ID: {project_id}
- Region: {region}
- GCS Bucket: gs://{gcs_bucket}/
- AI Model: {gemini_model}

**Services:**
- Analysis Viewer: {viewer_base_url}

**IMPORTANT: Initial Greeting**
When starting a new conversation (first message from user), show the following:

1. A brief welcome and capabilities overview:

"Welcome to FoldRun! I can help you predict 3D structures of proteins, RNA, DNA, and small molecule complexes using three models:

**AlphaFold2** — protein-only predictions (monomer or multimer)
- Best for: single proteins, protein-protein complexes
- Input: FASTA sequence
- Output: PDB structure + pLDDT/PAE confidence scores

**OpenFold3** — multi-molecule predictions with full RNA MSA support
- Best for: drug-target complexes, RNA structures, anything with non-protein components
- Runs nhmmer RNA MSA (Rfam + RNAcentral) for best RNA accuracy
- Input: FASTA (auto-converted) or OF3 JSON (for ligands via SMILES/CCD codes)
- Output: CIF structure + ranking_score/ipTM/pTM confidence scores

**Boltz-2** — multi-molecule predictions with covalent modification and glycan support
- Best for: covalently modified ligands, glycoproteins, or when explicitly requested
- Note: handles RNA/DNA/ligands but without external RNA MSA — OF3 is preferred for RNA
- Input: FASTA (auto-converted to YAML) or native Boltz-2 YAML
- Output: CIF structure + confidence_score/ipTM/pTM confidence scores

**Getting started — try one of these:**
- 'Predict the structure of ubiquitin' (AF2 monomer)
- 'Fold this protein with ATP' (OF3, protein + ligand)
- 'Predict a glycoprotein complex' (Boltz-2, glycan support)
- 'What's the structure of P69905?' (check AlphaFold DB first)

I handle the full lifecycle: submit → monitor → analyze → visualize."

2. Then show the environment:

| Component | Value |
|-----------|-------|
| Project | {project_id} |
| Region | {region} |
| Models | AlphaFold2, OpenFold3, Boltz-2 |
| AI Model | {gemini_model} |

**When users explicitly ask about configuration:**
Show full details including GCS bucket, viewer URL. If any value shows "Not configured", explain that the environment variable isn't set
"""


def _initialize_backends():
    """Trigger eager backend initialization and auto-detection."""
    from foldrun_app.models.af2.startup import get_config

    get_config()

    try:
        from foldrun_app.models.of3.startup import get_config as get_of3_config

        get_of3_config()
    except Exception:
        pass

    try:
        from foldrun_app.models.boltz2.startup import get_config as get_boltz2_config

        get_boltz2_config()
    except Exception:
        pass

    from foldrun_app.skills._tool_registry import ensure_initialized

    ensure_initialized()


def create_alphafold_agent(model: str | None = None) -> Agent:
    """Create and configure the baseline v1 FoldRun agent (AF2 + OF3 + Boltz-2) with static tools.

    Retained as the rollback/fallback path and for backward compatibility.
    """
    gemini_model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    # Validate model choice
    allowed_models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"]
    if gemini_model not in allowed_models:
        raise ValueError(
            f"Model '{gemini_model}' not supported. Use one of: {', '.join(allowed_models)}"
        )

    _initialize_backends()

    config_context = _get_agent_config_context(gemini_model)
    full_instruction = AGENT_INSTRUCTION + "\n\n" + config_context
    tools = skill_registry.compile_tools()

    agent = Agent(
        model=gemini_model,
        name="foldrun_app",
        description="Expert AI assistant for FoldRun (AlphaFold2 + OpenFold3) protein structure prediction, job management, and results analysis",
        instruction=full_instruction,
        tools=tools,
        on_model_error_callback=_retry_on_resource_exhausted,
    )

    return agent


def create_foldrun_agent(model: str | None = None) -> Agent:
    """Create and configure the FoldRun v2 agent using native ADK skills and SkillToolset.

    Activated when FOLDRUN_V2=true. Uses progressive disclosure and dynamic additional tools.
    """
    gemini_model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    # Validate model choice
    allowed_models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"]
    if gemini_model not in allowed_models:
        raise ValueError(
            f"Model '{gemini_model}' not supported. Use one of: {', '.join(allowed_models)}"
        )

    _initialize_backends()

    config_context = _get_agent_config_context(gemini_model)
    full_instruction = FOLDRUN_V2_INSTRUCTION.strip() + "\n\n" + config_context

    skill_toolset = skill_registry.get_skill_toolset()

    agent = Agent(
        model=gemini_model,
        name="foldrun_app",
        description="Expert AI assistant for FoldRun 2.0 with modular skills architecture",
        instruction=full_instruction,
        tools=[skill_toolset],
        on_model_error_callback=_retry_on_resource_exhausted,
    )

    return agent


try:
    if os.getenv("FOLDRUN_V2", "false").lower() in ("true", "1", "yes"):
        root_agent = create_foldrun_agent()
    else:
        root_agent = create_alphafold_agent()
    app = App(root_agent=root_agent, name="foldrun_app")
except Exception:
    root_agent = None
    app = None
