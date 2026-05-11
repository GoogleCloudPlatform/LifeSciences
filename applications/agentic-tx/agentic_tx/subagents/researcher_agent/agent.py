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

"""Researcher Agent (Information Agent) for Agentic-Tx.

Factory function to create a LlmAgent that specializes in scientific literature
and information retrieval.
"""

from google.adk.agents import BaseAgent, LlmAgent

from .config import ResearcherAgentConfig
from .prompts import RESEARCHER_AGENT_SYSTEM_INSTRUCTION
from .tools import SearchPubmedTool


def create_researcher_agent(name: str = "researcher_agent", config: ResearcherAgentConfig | None = None) -> BaseAgent:
    """Factory function to create a Researcher Agent (Information Agent).

    This agent specializes in:
    - Searching scientific literature (PubMed)
    - Retrieving general knowledge (Wikipedia) - TODO: Not yet implemented
    - Fetching gene/protein information (WikiCrow) - TODO: Not yet implemented
    - Performing web searches - TODO: Not yet implemented
    - Fetching and parsing web pages - TODO: Not yet implemented

    Args:
        name: Name for the agent (default: "researcher_agent")
        config: ResearcherAgentConfig instance. If not provided, uses default configuration.

    Returns:
        BaseAgent: An LlmAgent configured as a research specialist

    Example:
        >>> from agentic_tx.subagents.researcher_agent import (
        ...     create_researcher_agent,
        ... )
        >>> agent = create_researcher_agent()
        >>> # Use with ADK
        >>> response = await agent.run(
        ...     "What is known about aspirin and cardiovascular protection?"
        ... )
    """
    if not config:
        config = ResearcherAgentConfig()

    # Get ADK model and configuration
    model = config.get_model()
    generate_content_config = config.get_generate_content_config()
    planner = config.get_planner()

    # Create tools instance
    search_pubmed_tool = SearchPubmedTool(max_results=config.max_pubmed_results)

    # Create the LlmAgent
    researcher_agent = LlmAgent(
        model=model,
        name=name,
        description=(
            "Scientific literature and information retrieval specialist. "
            "Searches PubMed for biomedical literature, retrieves gene/protein information, "
            "and performs web searches to support therapeutic discovery research."
        ),
        generate_content_config=generate_content_config,
        planner=planner,
        instruction=config.get_instruction_prompt(RESEARCHER_AGENT_SYSTEM_INSTRUCTION),
        tools=[search_pubmed_tool.search_pubmed],
    )

    return researcher_agent
