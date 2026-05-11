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

"""Agentic-Tx: Multi-Agent Orchestrator for Therapeutic Discovery.

This module provides the orchestrator agent that coordinates specialized sub-agents
for comprehensive therapeutic discovery analysis.
"""

from google.adk.agents import BaseAgent, LlmAgent

from .config import GeminiAgentConfig
from .prompts import AGENTIC_TX_SYSTEM_INSTRUCTION
from .subagents.biology_agent import create_biology_agent
from .subagents.chemistry_agent import create_chemistry_agent
from .subagents.prediction_agent import create_prediction_agent
from .subagents.researcher_agent import create_researcher_agent


def create_agentic_tx_agent(name: str = "agentic_tx", config: GeminiAgentConfig | None = None) -> BaseAgent:
    """Factory function to create the Agentic-Tx Orchestrator Agent.

    This agent coordinates four specialized sub-agents to answer therapeutic discovery questions:
    - Researcher Agent: Scientific literature and information retrieval
    - Chemistry Agent: Molecular analysis and chemical properties
    - Biology Agent: Gene and protein analysis
    - Prediction Agent: Therapeutic property prediction (TxGemma)

    The orchestrator delegates to appropriate sub-agents, synthesizes their results,
    and provides comprehensive answers with evidence from multiple domains.

    Args:
        name: Name for the orchestrator agent (default: "agentic_tx")
        config: GeminiAgentConfig instance. If not provided, uses default configuration.

    Returns:
        BaseAgent: An LlmAgent configured as the therapeutic discovery orchestrator

    Example:
        >>> from agentic_tx import create_agentic_tx_agent
        >>> agent = create_agentic_tx_agent()
        >>> # Use with ADK
        >>> response = await agent.run(
        ...     "Is aspirin toxic for embryonic development?"
        ... )

    Architecture:
        User Query
            ↓
        Orchestrator Agent (this agent)
            ↓
        ┌──────────┬──────────┬──────────┬──────────────┐
        │ Researcher│Chemistry│ Biology  │ Prediction   │
        │ Agent    │ Agent   │ Agent    │ Agent        │
        └──────────┴──────────┴──────────┴──────────────┘
    """
    if not config:
        config = GeminiAgentConfig()

    # Get ADK model and configuration
    model = config.get_model()
    generate_content_config = config.get_generate_content_config()
    planner = config.get_planner()

    # from functools import partial

    # from google.adk.agents.callback_context import CallbackContext
    # from google.genai import types

    # def update_ui_status(status_msg: str, callback_context: CallbackContext) -> types.Content | None:
    #     """Updates the status of the execution on the Gemini Enterprise UI.
    #     Args:
    #     callback_context: The callback context.
    #     status_msg: The status message to display on the UI.
    #     """
    #     callback_context.state["ui:status_update"] = status_msg

    # Create sub-agents
    # Each sub-agent uses its own specialized configuration
    researcher_agent = create_researcher_agent()
    chemistry_agent = create_chemistry_agent()
    biology_agent = create_biology_agent()
    prediction_agent = create_prediction_agent()

    # Create the orchestrator LlmAgent
    agentic_tx_agent = LlmAgent(
        model=model,
        name=name,
        description=(
            "Agentic-Tx: Expert therapeutic discovery orchestrator coordinating specialized agents "
            "for comprehensive drug discovery analysis. Combines literature research, molecular analysis, "
            "biological insights, and predictive modeling to answer complex therapeutic questions."
        ),
        generate_content_config=generate_content_config,
        planner=planner,
        instruction=config.get_instruction_prompt(AGENTIC_TX_SYSTEM_INSTRUCTION),
        sub_agents=[
            researcher_agent,
            chemistry_agent,
            biology_agent,
            prediction_agent,
        ],
        # before_agent_callback=partial(update_ui_status, status_msg="Starting code generation."),
    )

    return agentic_tx_agent
