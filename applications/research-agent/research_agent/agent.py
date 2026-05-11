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

"""Main Research Agent that combines BigQuery and web research capabilities."""

import datetime
from pathlib import Path

from google.adk.agents import LlmAgent, SequentialAgent, LoopAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin
from google.genai import types

from .config import config
from .parallel_data_gathering import parallel_data_gathering_agent
from .set_research_plan import set_research_plan
from .sub_agents.display import display_content
from .sub_agents.synthesis import synthesize_research_report
from .sub_agents.refinement.evaluator import research_evaluator
from .sub_agents.refinement.checker import EscalationChecker
from .sub_agents.refinement.executor import plan_refiner


# Load instructions from markdown file
_instructions_path = Path(__file__).parent / "instructions.md"
_instructions = _instructions_path.read_text()

# --- Synthesis Step Agent ---
# Simple agent that just calls the synthesis tool
synthesis_step_agent = LlmAgent(
    model=config.root_model,
    name="synthesis_step",
    description="Synthesizes research findings into comprehensive report",
    instruction="""Call synthesize_research_report with request="Create comprehensive report combining all research findings from the gathered data sources."

After calling the tool, output: "Synthesis complete. Report saved to artifacts."
""",
    tools=[synthesize_research_report],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        response_modalities=["TEXT"],
    ),
)

def notify_synthesis_start(callback_context) -> None:
    """Print status message before synthesis starts."""
    print("\n[System]: Starting research synthesis (this may take a moment)...")
    return None

synthesis_step_agent.before_agent_callback = notify_synthesis_start

# --- Display Step Agent ---
# Simple agent that just calls the display tool
display_step_agent = LlmAgent(
    model=config.root_model,
    name="display_step",
    description="Displays the synthesized report to the user",
    instruction="""Call display_content with request="Show the report from state['synthesis_summary']"

The tool will return the report content. Output it EXACTLY as provided by the tool.
""",
    tools=[display_content],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        response_modalities=["TEXT"],
    ),
)

# --- Deep Research Loop ---
# Iteratively gathers, evaluates, and refines research until satisfactory or limit reached
deep_research_loop = LoopAgent(
    name="deep_research_loop",
    description="Iteratively gathers and evaluates research data.",
    sub_agents=[
        parallel_data_gathering_agent,  # Step 1: Gather data (Parallel)
        research_evaluator,             # Step 2: Evaluate Findings
        EscalationChecker(),            # Step 3: Check (Escalate if Pass)
        plan_refiner                    # Step 4: Refine Research Plan (if Fail)
    ],
    max_iterations=10 # Higher limit here, controlled by EscalationChecker inside
)

# --- Research Workflow (Sequential: deep loop → synthesize → display) ---
research_workflow = SequentialAgent(
    name="research_workflow",
    description="Complete research workflow: deep research loop, synthesis, and display",
    sub_agents=[
        deep_research_loop,     # Gather & Refine
        synthesis_step_agent,   # Synthesize
        display_step_agent,     # Display
    ],
)

# --- Main Root Agent ---
# Note: Current date will be added dynamically in instructions.md via template variables
root_agent = LlmAgent(
    model=Gemini(
        model=config.root_model,
        retry_options=types.HttpRetryOptions(
            attempts=config.max_retry_count,
            exp_base=config.delay_multiplier,
            initial_delay=config.initial_retry_delay
        )
    ),
    name="research_orchestrator",
    instruction=_instructions,  # Load from instructions.md without dynamic date
    sub_agents=[
        research_workflow,  # Sequential workflow
    ],
    tools=[
        set_research_plan,  # Tool to set research plan and flags in state
        display_content,    # Tool to display specific content on user request (e.g., "show patent #5")
    ],
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)

# Wrap agent in App for proper ADK integration
app = App(
    name="research_agent",  # ADK uses this for session management
    root_agent=root_agent,
    plugins=[
        SaveFilesAsArtifactsPlugin()  # Required for local ADK to save attachments
    ]
)
