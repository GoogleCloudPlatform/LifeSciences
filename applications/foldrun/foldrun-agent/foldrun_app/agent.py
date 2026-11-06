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

Multi-model protein structure prediction agent supporting AlphaFold2,
AlphaFold 3, OpenFold3, and Boltz-2. Deployed to Agent Runtime or run locally via ADK.
"""

import logging
import os

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.flows.llm_flows.base_llm_flow import LlmResponse
from google.genai import types

from foldrun_app.skills import skill_registry
from foldrun_common import ALLOWED_GEMINI_AGENT_MODELS, DEFAULT_GEMINI_AGENT_MODEL

logger = logging.getLogger(__name__)

# Modular v2 coordinator instructions for native ADK skills (used with SkillDiscoveryMode.EAGER)
FOLDRUN_V2_INSTRUCTION = """You are FoldRun, an expert protein structure prediction and macromolecular modeling assistant supporting four models:
- **AlphaFold2** (`submit_af2_monomer_prediction`, `submit_af2_multimer_prediction`, `submit_af2_batch_predictions`): Specialized for protein-only monomers and multimers (check AlphaFold DB first by UniProt ID before running GPU jobs).
- **AlphaFold 3** (`submit_af3_endpoint_prediction`, `submit_af3_batch_predictions`): Dedicated H100 80 GB all-atom endpoint supporting Full 630 GB MSA + PDB templates (`msa_free=False`, default) and fast zero-MSA mode (`msa_free=True`) for proteins, RNA, DNA, ligands, and ions.
- **OpenFold3** (`submit_of3_prediction`): Multi-molecule complexes (proteins, RNA, DNA, CCD/SMILES ligands); **preferred for RNA** because its MSA pipeline runs automated `nhmmer` RNA alignment against Rfam + RNAcentral.
- **Boltz-2** (`submit_boltz2_prediction`): Biomolecular complexes, covalent modifications, glycans, and **ligand binding affinity prediction** (`properties: [{affinity: {binder: <chain_id>}}]`), outputting `affinity_pred_value` (`log10(IC50 uM)`, more negative = stronger binding) and `affinity_probability_binary` (`0–1` active binder probability).

## Confidence Metrics & Post-Job Analysis Summary
- **pLDDT (`0–100`)**: Per-residue/atom local confidence (`>90` very high / reliable sidechains; `70–90` confident backbone; `50–70` flexible loops; `<50` intrinsically disordered regions).
- **PAE / PDE (`Å`)**: Pairwise positional/distance error (`<5 Å` rigid domain/interface packing; `>15 Å` uncertain relative domain/chain orientation).
- **pTM & ipTM (`0–1`)**: Global fold (`pTM > 0.8`) and complex interface confidence (`ipTM > 0.75` reliable binding pose; `<0.6` uncertain interface; ranking score `0.8 * ipTM + 0.2 * pTM`).
- **Post-job workflow**: Run `analyze_job_parallel(job_id)` to launch Cloud Run analysis, `get_analysis_results(job_id)` to fetch `summary.json` metrics and Gemini expert analysis, `open_structure_viewer(job_id)` to open the interactive 3D Mol* viewer, and `download_job_artifacts(job_id)` for signed HTTPS download links (`artifacts_bundle.zip`).

## Progressive Disclosure & Tool Routing Rules
1. **Direct Q&A (Zero Tool Calls)**: Answer general questions about model selection, ligand/affinity capabilities, confidence metric interpretation (`pLDDT`, `ipTM`, `PAE`, `PDE`), and workflow guidance **directly from these instructions** without calling `load_skill`.
2. **When to Call `load_skill`**: Call `load_skill` ONLY when you need to execute a domain tool (e.g., checking GPU quota, listing/managing jobs, estimating costs, querying AlphaFold DB, cleaning up storage, or preparing a pre-submission confirmation table for a user-provided sequence/complex). **CRITICAL**: Domain tools (`check_gpu_quota`, `list_jobs`, `cleanup_gcs_files`, etc.) are NOT registered until AFTER `load_skill` returns—NEVER call a domain tool in the same turn as `load_skill`.
3. **Batch Parallel Lookups**: If a request genuinely requires multiple skills or multiple `load_skill_resource` files, call them **in parallel in a single turn** rather than sequentially across turns.

## Response Guidelines
- Always verify job status with `check_job_status` (or `check_af3_endpoint` for AF3 endpoint state) before reporting results. NEVER hallucinate job completion or status.
- Present required pre-submission confirmation tables and safety warnings before calling any submission or deletion tool.
- After every response, suggest 2-3 contextual next actions as numbered choices.
"""

_root_agent: LlmAgent | None = None
_app: App | None = None
_lazy_initialized: bool = False


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


def _ensure_root_agent() -> tuple[LlmAgent | None, App | None]:
    """Lazily initialize root_agent and ADK App on first access."""
    global _root_agent, _app, _lazy_initialized
    if not _lazy_initialized:
        _lazy_initialized = True
        try:
            if os.getenv("FOLDRUN_V2", "true").lower() in ("true", "1", "yes"):
                _root_agent = create_foldrun_agent()
            else:
                _root_agent = create_alphafold_agent()
            _app = App(root_agent=_root_agent, name="foldrun_app")
        except Exception:
            _root_agent = None
            _app = None
    return _root_agent, _app


def __getattr__(name: str):
    """Lazy module attribute getter to avoid import-time tool compilation and GCP calls."""
    if name == "all_tools":
        return skill_registry.compile_tools()
    if name == "AGENT_INSTRUCTION":
        return skill_registry.compile_instruction(include_references=True)
    if name == "root_agent":
        agent, _ = _ensure_root_agent()
        return agent
    if name == "app":
        _, adk_app = _ensure_root_agent()
        return adk_app
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

    return f"""## Your Current Configuration
- Project ID: {project_id} | Region: {region} | GCS Bucket: gs://{gcs_bucket}/
- Analysis Viewer: {viewer_base_url} | AI Model: {gemini_model}

**Initial Greeting (First User Message Only)**:
Welcome the user to FoldRun, summarize the four supported models—**AlphaFold2** (protein-only monomers/multimers), **AlphaFold 3** (all-atom H100 endpoint with `--msa-free` and Full 630 GB MSA), **OpenFold3** (multi-molecule complexes, ligands, and RNA with nhmmer MSA), and **Boltz-2** (covalent modifications, glycans, and ligand binding affinity)—show a compact environment table (`Project`, `Region`, `Models`, `AI Model`), and offer Getting started example prompts (e.g., *"Predict the structure of ubiquitin with AlphaFold 3"*, *"Fold this protein with ATP ligand"*, *"What's the structure of P69905?"*). When asked about configuration, include the GCS bucket and viewer URL.
"""


def _initialize_backends():
    """Trigger backend initialization and GPU auto-detection for enabled models."""
    from foldrun_app.skills._tool_registry import ensure_initialized

    ensure_initialized()


def _resolve_validated_gemini_model(model: str | None) -> str:
    """Resolve and validate the Gemini model identifier against the GA allowlist."""
    gemini_model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_AGENT_MODEL)
    if gemini_model not in ALLOWED_GEMINI_AGENT_MODELS:
        raise ValueError(
            f"Model '{gemini_model}' not supported. Use one of: {', '.join(ALLOWED_GEMINI_AGENT_MODELS)}"
        )
    return gemini_model


def create_alphafold_agent(model: str | None = None) -> LlmAgent:
    """Create and configure the baseline v1 FoldRun agent with static tools.

    Retained as the rollback/fallback path and for backward compatibility.
    """
    gemini_model = _resolve_validated_gemini_model(model)
    _initialize_backends()

    config_context = _get_agent_config_context(gemini_model)
    base_instruction = skill_registry.compile_instruction(include_references=True)
    full_instruction = base_instruction + "\n\n" + config_context
    tools = skill_registry.compile_tools()

    return LlmAgent(
        model=gemini_model,
        name="foldrun_app",
        description="Expert AI assistant for FoldRun (AlphaFold2 + OpenFold3) protein structure prediction, job management, and results analysis",
        instruction=full_instruction,
        tools=tools,
        on_model_error_callback=_retry_on_resource_exhausted,
    )


def create_foldrun_agent(model: str | None = None) -> LlmAgent:
    """Create and configure the FoldRun v2 agent using native ADK skills and SkillToolset.

    Activated when FOLDRUN_V2=true. Uses EAGER skill discovery and dynamic additional tools.
    """
    gemini_model = _resolve_validated_gemini_model(model)
    _initialize_backends()

    config_context = _get_agent_config_context(gemini_model)
    full_instruction = FOLDRUN_V2_INSTRUCTION.strip() + "\n\n" + config_context
    skill_toolset = skill_registry.get_skill_toolset()

    return LlmAgent(
        model=gemini_model,
        name="foldrun_app",
        description="Expert AI assistant for FoldRun 2.0 with modular skills architecture",
        instruction=full_instruction,
        tools=[skill_toolset],
        on_model_error_callback=_retry_on_resource_exhausted,
    )
