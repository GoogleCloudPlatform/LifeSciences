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

"""Chemistry Agent for Agentic-Tx.

Factory function to create a LlmAgent that specializes in molecular analysis
and chemical property prediction.
"""

from google.adk.agents import BaseAgent, LlmAgent

from .config import ChemistryAgentConfig
from .prompts import CHEMISTRY_AGENT_SYSTEM_INSTRUCTION
from .tools import (
    AnalyzeOralBioavailabilityTool,
    ConvertStructureTool,
    GenerateMoleculeImageTool,
    GetMolecularPropertiesTool,
    GetTherapeuticInfoTool,
    LookupCompoundTool,
)


def create_chemistry_agent(name: str = "chemistry_agent", config: ChemistryAgentConfig | None = None) -> BaseAgent:
    """Factory function to create a Chemistry Agent.

    This agent specializes in:
    - Looking up compounds by name or structure
    - Converting between chemical representations (SMILES, InChI, InChIKey)
    - Retrieving molecular properties
    - Getting therapeutic information from ChEMBL
    - Analyzing oral bioavailability
    - Generating 2D molecular structure images

    Args:
        name: Name for the agent (default: "chemistry_agent")
        config: ChemistryAgentConfig instance. If not provided, uses default configuration.

    Returns:
        BaseAgent: An LlmAgent configured as a chemistry specialist

    Example:
        >>> from agentic_tx.subagents.chemistry_agent import (
        ...     create_chemistry_agent,
        ... )
        >>> agent = create_chemistry_agent()
        >>> # Use with ADK
        >>> response = await agent.run("Get molecular properties for aspirin")
    """
    if not config:
        config = ChemistryAgentConfig()

    # Get ADK model and configuration
    model = config.get_model()
    generate_content_config = config.get_generate_content_config()
    planner = config.get_planner()

    # Create tools instances
    lookup_compound_tool = LookupCompoundTool()
    convert_structure_tool = ConvertStructureTool()
    get_molecular_properties_tool = GetMolecularPropertiesTool()
    get_therapeutic_info_tool = GetTherapeuticInfoTool()
    analyze_oral_bioavailability_tool = AnalyzeOralBioavailabilityTool()
    generate_molecule_image_tool = GenerateMoleculeImageTool()

    # Create the LlmAgent
    chemistry_agent = LlmAgent(
        model=model,
        name=name,
        description=(
            "Expert medicinal chemist specializing in compound analysis and molecular properties. "
            "Looks up compounds by name, converts between chemical representations (SMILES, InChI, InChIKey), "
            "retrieves molecular properties from PubChem, gets therapeutic information from ChEMBL, "
            "analyzes oral bioavailability using Lipinski's Rule of Five, "
            "and generates 2D molecular structure images for visualization, "
            "including grid layouts for comparing multiple molecules."
        ),
        generate_content_config=generate_content_config,
        planner=planner,
        instruction=config.get_instruction_prompt(CHEMISTRY_AGENT_SYSTEM_INSTRUCTION),
        tools=[
            lookup_compound_tool.lookup_compound,
            convert_structure_tool.convert_structure,
            get_molecular_properties_tool.get_molecular_properties,
            get_therapeutic_info_tool.get_therapeutic_info,
            analyze_oral_bioavailability_tool.analyze_oral_bioavailability,
            generate_molecule_image_tool.generate_molecule_image,
            generate_molecule_image_tool.generate_molecule_grid,
        ],
    )

    return chemistry_agent
