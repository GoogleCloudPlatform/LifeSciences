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

"""Parallel data gathering agent for running BigQuery, Clinical Trials, and Web Research concurrently."""

import logging
from typing import Optional
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

logger = logging.getLogger(__name__)

from .config import config
from .state_keys import StateKeys
from .sub_agents.bigquery import query_bigquery_data
from .sub_agents.clinical_trials import query_clinical_trials
from .sub_agents.research import perform_web_research


def after_parallel_completion(callback_context: CallbackContext) -> Optional[types.Content]:
    """Called after all parallel sub-agents complete. Allows SequentialAgent to continue."""
    logger.info("Parallel data gathering complete. All sub-agents finished.")
    return None


def check_pubmed_needed(callback_context: CallbackContext) -> Optional[types.Content]:
    """Skip PubMed agent if disabled."""
    run_pubmed = callback_context.state.get(StateKeys.RESEARCH_PLAN_PUBMED_RUN, True)

    if not run_pubmed:
        return types.Content(
            parts=[types.Part(text="Skipping PubMed - disabled by research plan.")],
            role="model"
        )
    return None


def check_patents_needed(callback_context: CallbackContext) -> Optional[types.Content]:
    """Skip Patents agent if disabled."""
    run_patents = callback_context.state.get(StateKeys.RESEARCH_PLAN_PATENTS_RUN, True)

    if not run_patents:
        return types.Content(
            parts=[types.Part(text="Skipping Patents - disabled by research plan.")],
            role="model"
        )
    return None


def check_clinical_trials_needed(callback_context: CallbackContext) -> Optional[types.Content]:
    """Skip Clinical Trials agent if disabled."""
    run_trials = callback_context.state.get(StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_RUN, True)

    if not run_trials:
        return types.Content(
            parts=[types.Part(text="Skipping Clinical Trials - disabled by research plan.")],
            role="model"
        )
    return None


def check_web_research_needed(callback_context: CallbackContext) -> Optional[types.Content]:
    """Skip Web Research agent if disabled."""
    run_web = callback_context.state.get(StateKeys.RESEARCH_PLAN_WEB_RESEARCH_RUN, True)

    if not run_web:
        return types.Content(
            parts=[types.Part(text="Skipping Web Research - disabled by research plan.")],
            role="model"
        )
    return None


# --- Individual Simple Agents ---

from google.adk.agents.invocation_context import InvocationContext

# --- Dynamic Instruction Functions ---

def get_pubmed_instruction(context: InvocationContext) -> str:
    question = context.session.state.get(StateKeys.RESEARCH_PLAN_PUBMED_QUESTION, "None")
    return f"""
    You are a data gatherer for PubMed/PMC articles via BigQuery.
    **PubMed Question:** {question}
    **Task:** Call query_bigquery_data with the PubMed question above, with run_pubmed=True and run_patents=False.
    
    CRITICAL: Do NOT attempt to transfer control. Just call the tool and then output "Done. Successfully retrieved X records." replacing X with the actual number of results reported by the tool.
    """

def get_patents_instruction(context: InvocationContext) -> str:
    question = context.session.state.get(StateKeys.RESEARCH_PLAN_PATENTS_QUESTION, "None")
    return f"""
    You are a data gatherer for patents via BigQuery.
    **Patents Question:** {question}
    **Task:** Call query_bigquery_data with the Patents question above, with run_pubmed=False and run_patents=True.
    
    CRITICAL: Do NOT attempt to transfer control. Just call the tool and then output "Done. Successfully retrieved X records." replacing X with the actual number of results reported by the tool.
    """

def get_clinical_trials_instruction(context: InvocationContext) -> str:
    question = context.session.state.get(StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_QUESTION, "None")
    return f"""
    You are a data gatherer for clinical trials from ClinicalTrials.gov.
    **Clinical Trials Question:** {question}
    **Task:** Call query_clinical_trials with the specific question above.
    
    CRITICAL: Do NOT attempt to transfer control. Just call the tool and then output "Done. Successfully retrieved X records." replacing X with the actual number of results reported by the tool.
    """

def get_web_research_instruction(context: InvocationContext) -> str:
    question = context.session.state.get(StateKeys.RESEARCH_PLAN_WEB_RESEARCH_QUESTION, "None")
    return f"""
    You are a data gatherer for web research via Google Search.
    **Web Research Question:** {question}
    **Task:** Call perform_web_research with the specific question above.
    
    CRITICAL: Do NOT attempt to transfer control. Just call the tool and then output "Done. Successfully retrieved X records." replacing X with the actual number of results reported by the tool.
    """

# --- Individual Simple Agents ---

# PubMed Agent
pubmed_agent = LlmAgent(
    model=config.root_model,
    name="pubmed_executor",
    description="Executes PubMed search via BigQuery",
    instruction=get_pubmed_instruction,
    tools=[query_bigquery_data],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)
pubmed_agent.before_agent_callback = check_pubmed_needed


# Patents Agent
patents_agent = LlmAgent(
    model=config.root_model,
    name="patents_executor",
    description="Executes Patents search via BigQuery",
    instruction=get_patents_instruction,
    tools=[query_bigquery_data],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)
patents_agent.before_agent_callback = check_patents_needed


# Clinical Trials Agent
clinical_trials_agent = LlmAgent(
    model=config.root_model,
    name="clinical_trials_executor",
    description="Executes Clinical Trials search via API",
    instruction=get_clinical_trials_instruction,
    tools=[query_clinical_trials],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)
clinical_trials_agent.before_agent_callback = check_clinical_trials_needed


# Web Research Agent
web_research_agent = LlmAgent(
    model=config.root_model,
    name="web_research_executor",
    description="Executes Web Research via Google Search",
    instruction=get_web_research_instruction,
    tools=[perform_web_research],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)
web_research_agent.before_agent_callback = check_web_research_needed

# --- Parallel Data Gathering Agent ---

parallel_data_gathering_agent = ParallelAgent(
    name="parallel_data_gatherer",
    description="Runs PubMed, Patents, Clinical Trials, and Web Research in parallel",
    sub_agents=[
        pubmed_agent,
        patents_agent,
        clinical_trials_agent,
        web_research_agent,
    ],
    after_agent_callback=after_parallel_completion,
)
