import logging
from typing import Any, Dict, Optional

from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.genai import types

from ...config import config
from ...state_keys import StateKeys

logger = logging.getLogger(__name__)

def get_refiner_instruction(context: InvocationContext) -> str:
    """Generates the dynamic instruction for the plan refiner."""
    state = context.session.state
    
    pubmed = state.get(StateKeys.RESEARCH_PLAN_PUBMED_QUESTION, "None")
    patents = state.get(StateKeys.RESEARCH_PLAN_PATENTS_QUESTION, "None")
    trials = state.get(StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_QUESTION, "None")
    web = state.get(StateKeys.RESEARCH_PLAN_WEB_RESEARCH_QUESTION, "None")
    
    evaluation = state.get(StateKeys.RESEARCH_EVALUATION, {})
    
    return f"""
    You are a Research Strategist.
    
    **Goal:** Update the specific research questions for the next iteration based on the Evaluator's critique.
    
    *** INPUTS ***
    
    1. CURRENT RESEARCH PLAN QUESTIONS:
    - PubMed: {pubmed}
    - Patents: {patents}
    - Clinical Trials: {trials}
    - Web Research: {web}
    
    2. EVALUATION REPORT:
    {evaluation}
    
    *** TASK ***
    1. Analyze the 'critique' and 'follow_up_directions' in the Evaluation Report.
    2. Determine which data source (PubMed, Patents, etc.) is best suited to answer each direction.
    3. **Rewrite** the research question for that source.
    
    **CRITICAL RULES FOR REWRITING:**
    - The new question will **REPLACE** the old one completely.
    - It must be **STANDALONE** and **COMPLETE**.
    - **DO NOT** just ask for the missing part (e.g., "Find safety data").
    - **DO** combine the original topic with the new requirement (e.g., "Find Alzheimer's trials AND safety data").
    - **DO NOT** use placeholders like 'Drug A' or 'Disease X'. You MUST use the actual terms found in the **Current Questions** above.
    - If you lose the original context (e.g., the disease or drug name), the search will FAIL.
    
    **Action:**
    - Call `update_research_questions` with the new, fully formed question strings.
    - Then output "Plan updated."
    """

async def update_research_questions(
    pubmed_update: str = "",
    patents_update: str = "",
    trials_update: str = "",
    web_update: str = "",
    tool_context: ToolContext = None
) -> str:
    """
    Updates the research questions for the next iteration.
    
    Args:
        pubmed_update: New question for PubMed (or empty to keep existing).
        patents_update: New question for Patents (or empty to keep existing).
        trials_update: New question for Clinical Trials (or empty to keep existing).
        web_update: New question for Web Research (or empty to keep existing).
    """
    updates = []
    print("\n" + "="*50)
    print("🔄 REFINEMENT STRATEGY: Updating Research Plan")
    print("="*50)

    if pubmed_update:
        tool_context.state[StateKeys.RESEARCH_PLAN_PUBMED_QUESTION] = pubmed_update
        tool_context.state[StateKeys.RESEARCH_PLAN_PUBMED_RUN] = True
        updates.append("PubMed")
        print(f"   👉 PubMed: {pubmed_update}")
    
    if patents_update:
        tool_context.state[StateKeys.RESEARCH_PLAN_PATENTS_QUESTION] = patents_update
        tool_context.state[StateKeys.RESEARCH_PLAN_PATENTS_RUN] = True
        updates.append("Patents")
        print(f"   👉 Patents: {patents_update}")

    if trials_update:
        tool_context.state[StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_QUESTION] = trials_update
        tool_context.state[StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_RUN] = True
        updates.append("Clinical Trials")
        print(f"   👉 Clinical Trials: {trials_update}")

    if web_update:
        tool_context.state[StateKeys.RESEARCH_PLAN_WEB_RESEARCH_QUESTION] = web_update
        tool_context.state[StateKeys.RESEARCH_PLAN_WEB_RESEARCH_RUN] = True
        updates.append("Web Research")
        print(f"   👉 Web Research: {web_update}")

    print("="*50 + "\n")
    
    # Store history
    history = tool_context.state.get(StateKeys.REFINEMENT_HISTORY, [])
    iteration = tool_context.state.get(StateKeys.REFINEMENT_LOOP_COUNT, 1)
    
    if pubmed_update:
        history.append({"iteration": iteration, "source": "PubMed", "question": pubmed_update})
    if patents_update:
        history.append({"iteration": iteration, "source": "Patents", "question": patents_update})
    if trials_update:
        history.append({"iteration": iteration, "source": "Clinical Trials", "question": trials_update})
    if web_update:
        history.append({"iteration": iteration, "source": "Web Research", "question": web_update})
        
    tool_context.state[StateKeys.REFINEMENT_HISTORY] = history
    
    msg = f"Updated research questions for: {', '.join(updates) if updates else 'None'}"
    logger.info(msg)
    return msg

plan_refiner = LlmAgent(
    model=Gemini(model=config.worker_model),
    name="plan_refiner",
    description="Updates research questions based on evaluation feedback.",
    instruction=get_refiner_instruction, # Use the dynamic function
    tools=[update_research_questions],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)
