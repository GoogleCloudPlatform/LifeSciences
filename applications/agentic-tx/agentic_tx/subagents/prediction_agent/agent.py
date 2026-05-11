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

"""Task Selection Agent for TxGemma.

Factory function to create a Sequential Agent that selects and maps TxGemma tasks.
"""

import json

from collections.abc import AsyncGenerator
from typing import override

from agents_shared.txgemma import ExecuteTaskTool, TaskMetadataLoader, TaskTools

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .config import PredictionAgentConfig
from .prompts import PREDICTION_AGENT_SYSTEM_INSTRUCTION, generate_system_instruction


class TaskMappingAgent(BaseAgent):
    """Custom agent that maps task IDs to full task definitions."""

    def __init__(self, name: str, loader: TaskMetadataLoader) -> None:
        """Initialize with TaskMetadataLoader dependency.

        Args:
            name: Agent name
            loader: TaskMetadataLoader instance
        """
        super().__init__(name=name, description="Maps task IDs to full task definitions")
        self._loader = loader

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        """Map task IDs from previous agent to full definitions.

        Args:
            ctx: Invocation context containing previous agent's output

        Yields:
            Event with task definitions
        """
        print("IN MAPPING AGENT")
        tasks_output_var_name = "tasks_output"
        # Get the previous agent's output
        # previous_output = ctx.session.events[-1]

        if tasks_output_var_name not in ctx.session.state or not ctx.session.state[tasks_output_var_name]:
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                content=types.ModelContent(parts=[types.Part(text="ERROR: No input from previous agent")]),
            )
            return

        try:
            # Try to parse as JSON
            tasks = ctx.session.state.get(tasks_output_var_name, "[]")
            print(tasks)
            print(type(tasks))
            task_ids = json.loads(tasks)

            # Map task IDs to full definitions
            task_definitions = json.dumps([n.model_dump() for n in self._loader.map_to_definitions(task_ids)])

            print(f"Task Defintions: {task_definitions}")
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                content=types.ModelContent(parts=[types.Part(text=task_definitions)]),
            )

        except (json.JSONDecodeError, Exception) as e:
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                content=types.ModelContent(
                    parts=[types.Part(text=f"ERROR: Failed to map task IDs: {e!s}")],
                ),
            )


def create_prediction_agent(name: str = "prediction_agent", config: PredictionAgentConfig | None = None) -> BaseAgent:
    """Factory function to create the Prediction Agent."""
    if not config:
        config = PredictionAgentConfig()

    # Define agent model inference parameters
    model = config.get_model()

    generate_content_config = config.get_generate_content_config()
    planner = config.get_planner()
    # Create metadata loader
    loader = TaskMetadataLoader()

    # Generate system instruction with category list
    instruction = generate_system_instruction(loader.categories)

    # Create tools instance with loader injected
    task_tool = TaskTools(loader)
    execute_task_tool = ExecuteTaskTool(
        task_metadata_loader=loader,
        txgemma_predict_endpoint=config.txgemma_predict_endpoint,
        custom_container=config.txgemma_custom_endpoint,
    )

    # Create first agent: LlmAgent that selects task IDs
    selector_agent = LlmAgent(
        model=model,
        name=f"{name}__selector",
        description="Selects relevant TxGemma task IDs",
        generate_content_config=generate_content_config,
        planner=planner,
        instruction=instruction,
        output_key="tasks_output",
        tools=[task_tool.get_tasks_in_category],
    )

    # Create second agent: TaskMappingAgent that maps IDs to definitions
    mapper_agent = TaskMappingAgent(name=f"{name}__task_mapper", loader=loader)

    # Create Sequential Agent wrapping both agents
    get_tasks_agent = SequentialAgent(
        name="get_tasks_agent",
        description="Selects relevant therepudic tasks based on user queries",
        sub_agents=[selector_agent, mapper_agent],
    )

    prediction_agent = LlmAgent(
        model=model,
        name=f"{name}",
        description="Computational medicinal chemistry assistant specializing in therapeutic property prediction using TxGemma and other models. Routes queries to appropriate prediction tasks from 703 available tasks across 63 therapeutic categories including safety screening (cardiotoxicity, hepatotoxicity, mutagenicity), ADME/PK profiling (CYP metabolism, BBB penetration, bioavailability), and efficacy prediction (binding affinity, clinical outcomes).",
        generate_content_config=generate_content_config,
        planner=planner,
        instruction=config.get_instruction_prompt(PREDICTION_AGENT_SYSTEM_INSTRUCTION),
        tools=[AgentTool(agent=get_tasks_agent), execute_task_tool.execute_task],
    )

    return prediction_agent
