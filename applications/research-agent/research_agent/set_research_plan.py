# Copyright 2025 Google LLC
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

"""Tool for setting research plan and execution flags in state."""

import logging
from google.adk.tools import ToolContext
from .state_keys import StateKeys
from .config import config

logger = logging.getLogger(__name__)


async def set_research_plan(
    research_plan: str,
    pubmed_question: str = "",
    pubmed_run: bool = False,
    patents_question: str = "",
    patents_run: bool = False,
    clinical_trials_question: str = "",
    clinical_trials_run: bool = False,
    web_research_question: str = "",
    web_research_run: bool = False,
    refinement_iterations: int = 2,
    tool_context: ToolContext = None,
) -> dict:
    """
    Set the research plan with specific questions for each data source.

    This tool must be called after user approves the research plan and before
    transferring to the parallel_data_gatherer. It stores source-specific
    questions and boolean flags that control which data sources to query.

    Args:
        research_plan: Overall research plan text (for reference/synthesis)
        pubmed_question: Specific question for PubMed article search (e.g., "Find 1000 articles on Eliquis")
        pubmed_run: Whether to search PubMed/PMC articles
        patents_question: Specific question for patent search (e.g., "Find 1000 patents on anticoagulants")
        patents_run: Whether to search patents
        clinical_trials_question: Specific question for clinical trials (e.g., "Find trials for Eliquis")
        clinical_trials_run: Whether to search ClinicalTrials.gov
        web_research_question: Specific question for web research (e.g., "Research market trends for anticoagulants")
        web_research_run: Whether to perform web research
        refinement_iterations: Max number of deep search refinement loops (default: 2, set to 0 to disable)
        tool_context: The tool context

    Returns:
        Dictionary with confirmation message

    Example:
        set_research_plan(
            research_plan="Find patents and articles on anticoagulants",
            pubmed_question="Find 1000 recent PubMed articles on Eliquis",
            pubmed_run=True,
            patents_question="Find 1000 recent patents on anticoagulants",
            patents_run=True,
            clinical_trials_question="",
            clinical_trials_run=False,
            web_research_question="",
            web_research_run=False,
            refinement_iterations=2
        )
    """
    logger.info(f"Setting research plan with flags: pubmed={pubmed_run}, patents={patents_run}, trials={clinical_trials_run}, web={web_research_run}, refinement={refinement_iterations}")

    # Store the overall research plan
    tool_context.state[StateKeys.RESEARCH_PLAN] = research_plan
    
    # Store Refinement Settings
    tool_context.state[StateKeys.RESEARCH_PLAN_REFINEMENT_ITERATIONS] = refinement_iterations
    tool_context.state[StateKeys.REFINEMENT_LOOP_COUNT] = 0  # Reset counter

    # Store PubMed-specific plan
    tool_context.state[StateKeys.RESEARCH_PLAN_PUBMED_QUESTION] = pubmed_question
    tool_context.state[StateKeys.RESEARCH_PLAN_PUBMED_RUN] = pubmed_run

    # Store Patents-specific plan
    tool_context.state[StateKeys.RESEARCH_PLAN_PATENTS_QUESTION] = patents_question
    tool_context.state[StateKeys.RESEARCH_PLAN_PATENTS_RUN] = patents_run

    # Store Clinical Trials-specific plan
    tool_context.state[StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_QUESTION] = clinical_trials_question
    tool_context.state[StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_RUN] = clinical_trials_run

    # Store Web Research-specific plan
    tool_context.state[StateKeys.RESEARCH_PLAN_WEB_RESEARCH_QUESTION] = web_research_question
    tool_context.state[StateKeys.RESEARCH_PLAN_WEB_RESEARCH_RUN] = web_research_run

    # Build summary of what will run
    sources = []
    if pubmed_run:
        sources.append(f"PubMed ({pubmed_question[:50]}...)" if len(pubmed_question) > 50 else f"PubMed ({pubmed_question})")
    if patents_run:
        sources.append(f"Patents ({patents_question[:50]}...)" if len(patents_question) > 50 else f"Patents ({patents_question})")
    if clinical_trials_run:
        sources.append(f"Clinical Trials ({clinical_trials_question[:50]}...)" if len(clinical_trials_question) > 50 else f"Clinical Trials ({clinical_trials_question})")
    if web_research_run:
        sources.append(f"Web Research ({web_research_question[:50]}...)" if len(web_research_question) > 50 else f"Web Research ({web_research_question})")

    sources_str = ", ".join(sources) if sources else "None"

    return {
        "status": "success",
        "message": f"✓ Research plan set successfully.\n\nData sources enabled:\n{chr(10).join('- ' + s for s in sources) if sources else '- None'}\n\nRefinement: {refinement_iterations} deep search iterations",
        "research_plan": research_plan,
        "enabled_sources": sources,
        "refinement_iterations": refinement_iterations
    }
