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

"""Biology Agent for Agentic-Tx.

Factory function to create a LlmAgent that specializes in gene and protein analysis
using NCBI databases.
"""

from google.adk.agents import BaseAgent, LlmAgent

from .config import BiologyAgentConfig
from .prompts import BIOLOGY_AGENT_SYSTEM_INSTRUCTION
from .tools import (
    GetGeneDescriptionTool,
    GetProteinInfoTool,
    IdentifyProteinSequenceTool,
    TranslateGeneToProteinTool,
)


def create_biology_agent(name: str = "biology_agent", config: BiologyAgentConfig | None = None) -> BaseAgent:
    """Factory function to create a Biology Agent.

    This agent specializes in:
    - Retrieving gene descriptions and information from NCBI Gene
    - Translating genes to protein sequences
    - Getting protein information from NCBI Protein
    - Performing BLAST searches to identify unknown protein sequences
    - Analyzing protein-coding genes

    Args:
        name: Name for the agent (default: "biology_agent")
        config: BiologyAgentConfig instance. If not provided, uses default configuration.

    Returns:
        BaseAgent: An LlmAgent configured as a molecular biology specialist

    Example:
        >>> from agentic_tx.subagents.biology_agent import (
        ...     create_biology_agent,
        ... )
        >>> agent = create_biology_agent()
        >>> # Use with ADK
        >>> response = await agent.run("What is the TP53 gene?")
    """
    if not config:
        config = BiologyAgentConfig()

    # Get ADK model and configuration
    model = config.get_model()
    generate_content_config = config.get_generate_content_config()
    planner = config.get_planner()

    # Create tools instances
    get_gene_description_tool = GetGeneDescriptionTool(entrez_email=config.entrez_email)
    translate_gene_to_protein_tool = TranslateGeneToProteinTool(entrez_email=config.entrez_email)
    get_protein_info_tool = GetProteinInfoTool(entrez_email=config.entrez_email)
    identify_protein_sequence_tool = IdentifyProteinSequenceTool(entrez_email=config.entrez_email)

    # Create the LlmAgent
    biology_agent = LlmAgent(
        model=model,
        name=name,
        description=(
            "Expert molecular biologist specializing in genes and proteins. "
            "Retrieves gene descriptions from NCBI Gene, translates genes to protein sequences, "
            "gets protein information from NCBI Protein database, and performs BLAST searches "
            "to identify unknown protein sequences. Analyzes protein-coding genes and their functions."
        ),
        generate_content_config=generate_content_config,
        planner=planner,
        instruction=config.get_instruction_prompt(BIOLOGY_AGENT_SYSTEM_INSTRUCTION),
        tools=[
            get_gene_description_tool.get_gene_description,
            translate_gene_to_protein_tool.translate_gene_to_protein,
            get_protein_info_tool.get_protein_info,
            identify_protein_sequence_tool.identify_protein_sequence,
        ],
    )

    return biology_agent
