import datetime
import logging
from typing import List, Optional

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.genai import types
from pydantic import BaseModel, Field

from ...config import config
from ...state_keys import StateKeys

logger = logging.getLogger(__name__)

class Feedback(BaseModel):
    grade: str = Field(
        ..., description="The grade of the research. Must be either 'pass' or 'fail'."
    )
    critique: str = Field(
        ..., description="Detailed critique of what is missing or needs improvement."
    )
    follow_up_directions: List[str] = Field(
        default_factory=list,
        description="List of specific directions for gathering missing information (e.g., 'Find more recent trials on X', 'Search for patents related to Y').",
    )

research_evaluator = LlmAgent(
    model=Gemini(model=config.critic_model),
    name="research_evaluator",
    description="Critically evaluates gathered research data and suggests follow-up actions.",
    instruction=f"""
    You are a Research Quality Assurance Expert.
    
    **Goal:** Evaluate if the gathered data is sufficient to answer the Research Plan: {{+{StateKeys.RESEARCH_PLAN}}}
    
    **Gathered Data Summary:**
    - PubMed Results: {{+{StateKeys.PUBMED_RESULTS}}}
    - Patents Results: {{+{StateKeys.PATENTS_RESULTS}}}
    - Clinical Trials: {{+{StateKeys.CLINICAL_TRIALS_RESULTS}}}
    - Web Research: {{+{StateKeys.WEB_RESEARCH_RESULTS}}}
    
    **CRITICAL RULES:**
    1. Assess if the *combined* information from all sources is sufficient to comprehensively address the Research Plan.
    2. Check for:
       - **Relevance:** Is the data actually about the topic?
       - **Completeness:** Are there major gaps? (e.g., asked for patents but found none, asked for recent news but only found old stuff).
       - **Depth:** Is there enough detail?
    
    3. **Grading:**
       - **PASS:** If the data is sufficient to write a high-quality report.
       - **FAIL:** If critical information is missing or the results are irrelevant/empty.
    
    4. **Follow-up:**
       - If FAIL, provide specific, actionable directions on what to search for next.
       - Focus on *missing* information. E.g., "Search specifically for side effects of Drug X", "Find recent patents from 2024".
    
    Current date: {datetime.datetime.now().strftime("%Y-%m-%d")}
    Your response must be a single, raw JSON object validating against the 'Feedback' schema.
    """,
    output_schema=Feedback,
    output_key="research_evaluation",
)
