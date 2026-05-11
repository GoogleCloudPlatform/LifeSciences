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

"""Agentic-Tx: Single-Agent (Monolithic) Implementation for Therapeutic Discovery.

This module provides a monolithic agent architecture where a single orchestrator
has direct access to all tools from specialized domains, without subagent delegation.
"""

from google.adk.agents import BaseAgent, LlmAgent

from .config import GeminiAgentConfig
from .prompts import AGENTIC_TX_MONOLITHIC_SYSTEM_INSTRUCTION

# Import specialized configs to extract tool-specific parameters
from .subagents.biology_agent.config import BiologyAgentConfig

# Biology Agent Tools
from .subagents.biology_agent.tools import (
    GetGeneDescriptionTool,
    GetProteinInfoTool,
    IdentifyProteinSequenceTool,
    TranslateGeneToProteinTool,
)

# Import all tool classes from subagents
# Chemistry Agent Tools
from .subagents.chemistry_agent.tools import (
    AnalyzeOralBioavailabilityTool,
    ConvertStructureTool,
    GenerateMoleculeImageTool,
    GetMolecularPropertiesTool,
    GetTherapeuticInfoTool,
    LookupCompoundTool,
)
from .subagents.prediction_agent.config import PredictionAgentConfig

# Prediction Agent Tools (from shared library)
from agents_shared.txgemma import ExecuteTaskTool, TaskMetadataLoader, TaskTools
from .subagents.researcher_agent.config import ResearcherAgentConfig

# Researcher Agent Tools
from .subagents.researcher_agent.tools import SearchPubmedTool


def create_agentic_tx_monolithic_agent(
    name: str = "agentic_tx_monolithic", config: GeminiAgentConfig | None = None
) -> BaseAgent:
    """Factory function to create the Agentic-Tx Monolithic Agent.

    This agent provides a single-agent architecture with direct access to all tools
    from four specialized domains:
    - Chemistry: Compound lookup, structure conversion, molecular properties, therapeutic info, bioavailability
    - Biology: Gene descriptions, protein sequences, protein info, sequence identification (BLAST)
    - Literature: PubMed search for scientific papers
    - Prediction: TxGemma-based prediction for 703 therapeutic tasks across 63 categories

    Unlike the multi-agent orchestrator (agent.py), this implementation uses a flat
    tool architecture where one agent directly executes all tools without delegation.

    Args:
        name: Name for the monolithic agent (default: "agentic_tx_monolithic")
        config: GeminiAgentConfig instance. If not provided, uses default configuration.

    Returns:
        BaseAgent: An LlmAgent configured with all therapeutic discovery tools

    Example:
        >>> from agentic_tx import (
        ...     create_agentic_tx_monolithic_agent,
        ... )
        >>> agent = create_agentic_tx_monolithic_agent()
        >>> # Use with ADK
        >>> response = await agent.run(
        ...     "Is aspirin toxic for embryonic development?"
        ... )

    Architecture Comparison:
        Multi-Agent (agent.py):
            User Query → Orchestrator → [Researcher | Chemistry | Biology | Prediction]

        Monolithic (this file):
            User Query → Single Agent with 12+ tools

    Available Tools (12+):
        Chemistry (5): lookup_compound, convert_structure, get_molecular_properties,
                       get_therapeutic_info, analyze_oral_bioavailability
        Biology (4): get_gene_description, translate_gene_to_protein,
                     get_protein_info, identify_protein_sequence
        Literature (1): search_pubmed
        Prediction (2): get_tasks_in_category, execute_task
    """
    if not config:
        config = GeminiAgentConfig()

    # Get ADK model and configuration from base config
    model = config.get_model()
    generate_content_config = config.get_generate_content_config()
    planner = config.get_planner()

    # Instantiate specialized configs to extract tool-specific parameters
    biology_config = BiologyAgentConfig()
    prediction_config = PredictionAgentConfig()
    researcher_config = ResearcherAgentConfig()

    # === Chemistry Tools (5 tools) ===
    lookup_compound_tool = LookupCompoundTool()
    convert_structure_tool = ConvertStructureTool()
    generate_molecule_image_tool = GenerateMoleculeImageTool()
    get_molecular_properties_tool = GetMolecularPropertiesTool()
    get_therapeutic_info_tool = GetTherapeuticInfoTool()
    analyze_oral_bioavailability_tool = AnalyzeOralBioavailabilityTool()

    # === Biology Tools (4 tools) ===
    get_gene_description_tool = GetGeneDescriptionTool(entrez_email=biology_config.entrez_email)
    translate_gene_to_protein_tool = TranslateGeneToProteinTool(entrez_email=biology_config.entrez_email)
    get_protein_info_tool = GetProteinInfoTool(entrez_email=biology_config.entrez_email)
    identify_protein_sequence_tool = IdentifyProteinSequenceTool(entrez_email=biology_config.entrez_email)

    # === Researcher Tools (1 tool) ===
    search_pubmed_tool = SearchPubmedTool(max_results=researcher_config.max_pubmed_results)

    # === Prediction Tools (2 tools + loader) ===
    task_metadata_loader = TaskMetadataLoader()
    task_tools = TaskTools(task_metadata_loader)
    execute_task_tool = ExecuteTaskTool(
        task_metadata_loader=task_metadata_loader,
        txgemma_predict_endpoint=prediction_config.txgemma_predict_endpoint,
    )

    # Create the monolithic LlmAgent with all tools
    agentic_tx_monolithic_agent = LlmAgent(
        model=model,
        name=name,
        description=(
            "Agentic-Tx Monolithic: Expert therapeutic discovery agent with direct access to 12+ specialized tools. "
            "Performs comprehensive drug discovery analysis including molecular analysis, biological insights, "
            "literature research, and predictive modeling. Single-agent architecture for direct tool execution "
            "without subagent delegation."
        ),
        generate_content_config=generate_content_config,
        planner=planner,
        instruction=AGENTIC_TX_MONOLITHIC_SYSTEM_INSTRUCTION,
        tools=[
            # Chemistry tools (5)
            lookup_compound_tool.lookup_compound,
            convert_structure_tool.convert_structure,
            get_molecular_properties_tool.get_molecular_properties,
            get_therapeutic_info_tool.get_therapeutic_info,
            analyze_oral_bioavailability_tool.analyze_oral_bioavailability,
            # Biology tools (4)
            get_gene_description_tool.get_gene_description,
            generate_molecule_image_tool.generate_molecule_image,
            translate_gene_to_protein_tool.translate_gene_to_protein,
            get_protein_info_tool.get_protein_info,
            identify_protein_sequence_tool.identify_protein_sequence,
            # Literature tools (1)
            search_pubmed_tool.search_pubmed,
            # Prediction tools (2)
            task_tools.get_tasks_in_category,
            execute_task_tool.execute_task,
        ],
    )

    return agentic_tx_monolithic_agent
