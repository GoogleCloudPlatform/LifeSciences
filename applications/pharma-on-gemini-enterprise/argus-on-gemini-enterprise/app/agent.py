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

"""Argus - a life-sciences M&A due-diligence agent built on Google ADK.

Architecture:

    root_agent "argus" (ADK v2 Workflow DAG)
      │
      ├── (START -> coordinator_agent)  [Coordinator]
      │     ├── sub_agents (delegated directly):
      │     │     regulatory_scientific_analyst · clinical_analyst ·
      │     │     financial_analyst · market_analyst
      │     ├── tools: coordinator_skill_toolset, web_search, generate_slide
      │     └── routing tool: launch_deep_diligence (sets route="deep_report" & session state)
      │
      └── (coordinator_agent -> deep_report_pipeline)  [Workflow DAG on route="deep_report"]
            ├── (START -> parallel_analysts): 6 analysts run concurrently in parallel
            │     scientific_pos · competitive · clinical_regulatory ·
            │     commercial · financial_deal · ip_fto
            │     (each writes compact findings to session state via output_key)
            │
            ├── (parallel_analysts -> join_analyst_findings): JoinNode barrier
            │     synchronizes and merges all 6 analyst findings
            │
            └── (join_analyst_findings -> report_synthesizer):
                  synthesizes findings, builds charts + infographic, generates PDF

Design notes:
- Workflow DAG orchestration (ADK v2): The root agent is a Workflow DAG starting
  with `coordinator_agent`. When a full whitepaper is requested, `launch_deep_diligence`
  sets `tool_context.actions.route = "deep_report"` along with target metadata in session
  state, transitioning execution to `deep_report_pipeline`.
- Parallel fan-out & JoinNode: The deep report runs six analysts concurrently in
  parallel (`parallel_analysts`), each writing a compact findings brief to session state
  via `output_key`. A `JoinNode` (`join_analyst_findings`) synchronizes completion of all
  parallel branches before passing execution to `report_synthesizer` (charts, infographic, PDF).
  This finishes well within the Gemini Enterprise ~300s response window.
- Sub-agent delegation: The coordinator delegates focused domain inquiries directly to 4
  specialist `sub_agents` (regulatory/scientific, clinical, financial, market).
- Gemini built-in tools (google_search) cannot be combined with other tools in
  the same agent, so search is isolated in `search_agent` and shared everywhere
  as the `web_search` AgentTool.
- Domain methodology skills (diligence playbook, target screening, whitepaper template)
  are loaded locally; primary science skills (openFDA, ClinicalTrials.gov, ChEMBL,
  Open Targets, UniProt, PubMed, Europe PMC) are loaded from the shared GCS bucket.
"""

import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.google_llm import Gemini
from google.adk.planners import BuiltInPlanner
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin
from google.adk.tools import ToolContext, google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.workflow import START, JoinNode, Workflow
from google.genai import types

from . import prompts
from .app_utils.skills_loader import create_agent_skill_toolset
from .tools.charts import bar_chart, horizontal_bar_chart, line_chart
from .tools.edgar import (
    edgar_find_company,
    edgar_full_text_search,
    edgar_key_financials,
    edgar_recent_filings,
)
from .tools.infographic import generate_slide, make_infographic
from .tools.whitepaper import generate_whitepaper_pdf

MODEL = Gemini(
    model=os.environ.get("ARGUS_MODEL", "gemini-3.7-flash"),
    retry_options=types.HttpRetryOptions(
        attempts=3,
        http_status_codes=[429, 500, 503],
    ),
)


# Surface the model's reasoning as thought summaries so they show up in `adk web`
# (and in Agent Engine traces). Off by default in the API; include_thoughts=True
# asks Gemini to return a summarized chain of thought alongside the answer.
# Toggle with ARGUS_SHOW_THOUGHTS=0.
_SHOW_THOUGHTS = os.environ.get("ARGUS_SHOW_THOUGHTS", "1") not in ("0", "false", "")
THINKING_PLANNER = (
    BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True))
    if _SHOW_THOUGHTS
    else None
)


# --- Skills: targeted ADK progressive-disclosure toolsets per specialist lane ---

coordinator_skill_toolset = create_agent_skill_toolset(
    local_skill_names=["diligence-playbook", "target-screening"],
)

regulatory_scientific_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=[
        "private-openfda-database",
        "private-chembl-database",
        "private-opentargets-database",
        "private-uniprot-database",
    ],
    local_skill_names=["diligence-playbook"],
)

clinical_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=[
        "private-clinical-trials-database",
        "private-openfda-database",
        "private-pubmed-database",
    ],
    local_skill_names=["diligence-playbook"],
)

market_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=[
        "private-openfda-database",
        "private-clinical-trials-database",
    ],
    local_skill_names=["diligence-playbook"],
)

scientific_pos_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=[
        "private-opentargets-database",
        "private-chembl-database",
        "private-uniprot-database",
        "private-literature-search-europepmc",
    ],
)

competitive_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=[
        "private-clinical-trials-database",
        "private-chembl-database",
    ],
)

clinical_regulatory_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=[
        "private-clinical-trials-database",
        "private-openfda-database",
    ],
)

commercial_skill_toolset = create_agent_skill_toolset(
    science_skill_ids=[
        "private-openfda-database",
        "private-clinical-trials-database",
    ],
)

synthesizer_skill_toolset = create_agent_skill_toolset(
    local_skill_names=["whitepaper-template", "diligence-playbook"],
)


# --- Web search, isolated then shared as a tool (built-in tool constraint) ---

search_agent = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="search_agent",
    description="Runs Google Search for current life-sciences facts and news.",
    instruction=prompts.SEARCH_AGENT_INSTRUCTION,
    tools=[google_search],
)
web_search = AgentTool(agent=search_agent)


# --- Trigger tool: launches the deep diligence workflow DAG from coordinator ---


def launch_deep_diligence(
    target_company: str,
    acquirer_thesis: str,
    tool_context: ToolContext,
) -> dict[str, str]:
    """Launches the comprehensive multi-analyst due diligence whitepaper pipeline for a target.

    Args:
        target_company: Name of the target biotech/pharma company or asset.
        acquirer_thesis: Acquirer strategic thesis, scope, or key areas of focus.

    Returns:
        dict with status confirmation.
    """
    tool_context.actions.route = "deep_report"
    tool_context.state["target_name"] = target_company
    tool_context.state["acquirer_thesis"] = acquirer_thesis
    return {
        "status": "launched",
        "target_company": target_company,
        "acquirer_thesis": acquirer_thesis,
        "message": (
            f"Initiating deep diligence research pipeline for {target_company}. "
            "Six specialized analysts are conducting parallel research."
        ),
    }


# --- Specialist analysts (attached directly to the coordinator as sub-agents) ---

regulatory_scientific_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="regulatory_scientific_analyst",
    description=(
        "Assesses the science and regulatory posture of a company/asset/target: "
        "mechanism, novelty, platform, IP, FDA reviews, labels, and designations. "
        "Uses openFDA, ChEMBL, Open Targets, and UniProt scientific databases."
    ),
    instruction=prompts.REGULATORY_SCIENTIFIC_INSTRUCTION,
    tools=[regulatory_scientific_skill_toolset, web_search],
)

clinical_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="clinical_analyst",
    description=(
        "Assesses a target's clinical pipeline and evidence: trial stage/design, "
        "endpoints, efficacy/safety signals, holds/CRLs, catalysts. Uses "
        "ClinicalTrials.gov, openFDA, and PubMed."
    ),
    instruction=prompts.CLINICAL_INSTRUCTION,
    tools=[clinical_skill_toolset, web_search],
)

financial_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="financial_analyst",
    description=(
        "Assesses a target's financial health from SEC filings: cash, burn, "
        "cash runway, R&D/G&A, debt, dilution. Best for US-listed companies."
    ),
    instruction=prompts.FINANCIAL_INSTRUCTION,
    tools=[
        edgar_find_company,
        edgar_recent_filings,
        edgar_key_financials,
        edgar_full_text_search,
        web_search,
    ],
)

market_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="market_analyst",
    description=(
        "Assesses commercial opportunity: addressable population, competitive "
        "landscape, pricing/reimbursement, peak-sales framing, and comparable "
        "M&A transactions."
    ),
    instruction=prompts.MARKET_INSTRUCTION,
    tools=[market_skill_toolset, web_search],
)


# --- Deep-report workflow DAG: parallel analysts -> JoinNode -> synthesizer -> PDF ---

scientific_pos_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="scientific_pos_analyst",
    description="Scientific probability-of-success analyst.",
    instruction=prompts.SCIENTIFIC_POS_INSTRUCTION,
    tools=[scientific_pos_skill_toolset, web_search],
    output_key="findings_scientific_pos",
)

competitive_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="competitive_analyst",
    description="Competitive-landscape analyst.",
    instruction=prompts.COMPETITIVE_INSTRUCTION,
    tools=[competitive_skill_toolset, web_search],
    output_key="findings_competitive",
)

clinical_regulatory_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="clinical_regulatory_analyst",
    description="Clinical & regulatory analyst.",
    instruction=prompts.CLINICAL_REGULATORY_INSTRUCTION,
    tools=[clinical_regulatory_skill_toolset, web_search],
    output_key="findings_clinical_regulatory",
)

commercial_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="commercial_analyst",
    description="Commercial & market analyst.",
    instruction=prompts.COMMERCIAL_INSTRUCTION_DEEP,
    tools=[commercial_skill_toolset, web_search],
    output_key="findings_commercial",
)

financial_deal_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="financial_deal_analyst",
    description="Financial & deal/valuation analyst.",
    instruction=prompts.FINANCIAL_DEAL_INSTRUCTION,
    tools=[
        edgar_find_company,
        edgar_recent_filings,
        edgar_key_financials,
        edgar_full_text_search,
        web_search,
    ],
    output_key="findings_financial_deal",
)

ip_fto_analyst = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="ip_fto_analyst",
    description="IP & exclusivity analyst.",
    instruction=prompts.IP_FTO_INSTRUCTION,
    tools=[edgar_full_text_search, web_search],
    output_key="findings_ip_fto",
)

join_analyst_findings = JoinNode(
    name="join_analyst_findings",
    description="Synchronizes and merges findings from all six parallel diligence analysts.",
)

report_synthesizer = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="report_synthesizer",
    description="Synthesizes analyst findings into a whitepaper PDF with visuals.",
    instruction=prompts.REPORT_SYNTHESIZER_INSTRUCTION,
    tools=[
        synthesizer_skill_toolset,
        bar_chart,
        line_chart,
        horizontal_bar_chart,
        make_infographic,
        generate_whitepaper_pdf,
    ],
)

parallel_analysts = (
    scientific_pos_analyst,
    competitive_analyst,
    clinical_regulatory_analyst,
    commercial_analyst,
    financial_deal_analyst,
    ip_fto_analyst,
)

deep_report_pipeline = Workflow(
    name="deep_report_pipeline",
    description=(
        "Full acquisition-assessment whitepaper pipeline: runs six analysts in parallel, "
        "merges findings via JoinNode, then authors a whitepaper with charts and a conceptual "
        "infographic and saves it as a downloadable PDF."
    ),
    edges=[
        (START, parallel_analysts),
        (parallel_analysts, join_analyst_findings),
        (join_analyst_findings, report_synthesizer),
    ],
)


# --- Coordinator & Root Workflow ---

coordinator_agent = Agent(
    model=MODEL,
    planner=THINKING_PLANNER,
    name="coordinator",
    description=(
        "Argus coordinator: a life-sciences (pharma/biotech) M&A due-diligence lead. "
        "Answers quick questions about potential acquisitions, drives target screening, "
        "and scopes full whitepaper assessments."
    ),
    instruction=prompts.COORDINATOR_INSTRUCTION,
    sub_agents=[
        regulatory_scientific_analyst,
        clinical_analyst,
        financial_analyst,
        market_analyst,
    ],
    tools=[
        coordinator_skill_toolset,
        web_search,
        launch_deep_diligence,
        generate_slide,
    ],
)

root_agent = Workflow(
    name="argus",
    description=(
        "Argus: a life-sciences (pharma/biotech) M&A due-diligence agent. Answers "
        "quick questions about a potential acquisition, produces investment-grade "
        "whitepapers on a named target, and recommends acquisition targets given "
        "a thesis. Draws on primary regulatory, scientific, clinical, and financial "
        "databases (openFDA, ClinicalTrials.gov, ChEMBL, Open Targets, SEC EDGAR, and web search)."
    ),
    edges=[
        (START, coordinator_agent),
        (coordinator_agent, {"deep_report": deep_report_pipeline}),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
    plugins=[
        GlobalInstructionPlugin(
            global_instruction=prompts.get_temporal_grounding_instruction,
            name="argus_temporal_grounding",
        )
    ],
)
