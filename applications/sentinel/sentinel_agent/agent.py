# Copyright 2026 Google LLC
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

"""Sentinel root agent.

Pipeline shape::

    intake
        ↓
    LoopAgent (max_iterations=2, can short-circuit)
        ├── reviewer_panel (Parallel: 5)
        │     ├── medical_reviewer
        │     ├── legal_reviewer
        │     ├── regulatory_reviewer
        │     ├── editorial_reviewer
        │     └── submitter_advocate     # argues for the submission
        ├── critic_panel (Parallel: 3)
        │     ├── dedupe_critic
        │     ├── severity_critic
        │     └── gap_critic
        ├── critic_merger                 # consolidates → CriticAssessment
        └── loop_decider                  # exits loop early via exit_loop tool
        ↓
    synthesizer

Each stage uses ``gemini-3.1-pro-preview`` with structured
``output_schema`` and writes to session state via ``output_key``. The
loop iterates the reviewer panel + critic panel + merger + decider up
to twice; the decider calls ``exit_loop`` when the merger reports the
reviewers have converged.

Run from the parent directory with::

    adk web .
"""

from __future__ import annotations

from google.adk.agents import (
    LlmAgent,
    LoopAgent,
    ParallelAgent,
    SequentialAgent,
)
from google.adk.tools import ToolContext

from sentinel_agent import prompts
from sentinel_agent.schemas import (
    ContentInventory,
    CriticAssessment,
    DedupeCriticOutput,
    FinalReport,
    GapCriticOutput,
    ReviewerOutput,
    SeverityCriticOutput,
    SubmitterDefenseBrief,
)

# All sub-agents share the same model. ``gemini-3.1-pro-preview`` is only
# served on the global Vertex endpoint, so callers must set
# ``GOOGLE_CLOUD_LOCATION=global``.
_MODEL = "gemini-3.1-pro-preview"

# Hard cap on review iterations. Each iteration runs the full reviewer
# panel + critic panel + merger + decider, so the cost budget per cap
# is ~iterations × (5 reviewers + 3 critics + merger + decider).
_MAX_REVIEW_ITERATIONS = 2


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------

intake_agent = LlmAgent(
    name="intake",
    model=_MODEL,
    description=(
        "Catalogues every reviewable element in the submitted content "
        "into a structured inventory."
    ),
    instruction=prompts.INTAKE,
    output_schema=ContentInventory,
    output_key="intake_findings",
)


# ---------------------------------------------------------------------------
# Reviewer panel (5 in parallel: 4 critical lenses + submitter advocate)
# ---------------------------------------------------------------------------

medical_reviewer = LlmAgent(
    name="medical_reviewer",
    model=_MODEL,
    description=(
        "Reviews the submission through a clinical lens: accuracy, dosing, "
        "mechanism, efficacy, safety, fair balance."
    ),
    instruction=prompts.MEDICAL_REVIEWER,
    output_schema=ReviewerOutput,
    output_key="medical_findings",
)


legal_reviewer = LlmAgent(
    name="legal_reviewer",
    model=_MODEL,
    description=(
        "Reviews the submission through a legal lens: claim substantiation, "
        "comparative claims, citations, disclosures, IP."
    ),
    instruction=prompts.LEGAL_REVIEWER,
    output_schema=ReviewerOutput,
    output_key="legal_findings",
)


regulatory_reviewer = LlmAgent(
    name="regulatory_reviewer",
    model=_MODEL,
    description=(
        "Reviews the submission through a regulatory lens: indication scope, "
        "off-label, ISI, PI consistency, fair balance."
    ),
    instruction=prompts.REGULATORY_REVIEWER,
    output_schema=ReviewerOutput,
    output_key="regulatory_findings",
)


editorial_reviewer = LlmAgent(
    name="editorial_reviewer",
    model=_MODEL,
    description=(
        "Reviews the submission through an editorial lens: clarity, "
        "accessibility, tone, visual design, typography."
    ),
    instruction=prompts.EDITORIAL_REVIEWER,
    output_schema=ReviewerOutput,
    output_key="editorial_findings",
)


submitter_advocate = LlmAgent(
    name="submitter_advocate",
    model=_MODEL,
    description=(
        "Argues for the submission. Produces a defense brief the critic "
        "panel weighs when calibrating severity."
    ),
    instruction=prompts.SUBMITTER_ADVOCATE,
    output_schema=SubmitterDefenseBrief,
    output_key="submitter_defense",
)


reviewer_panel = ParallelAgent(
    name="reviewer_panel",
    description=(
        "Runs the four MLR review lenses (medical, legal, regulatory, "
        "editorial) plus a submitter's-advocate defense brief in parallel."
    ),
    sub_agents=[
        medical_reviewer,
        legal_reviewer,
        regulatory_reviewer,
        editorial_reviewer,
        submitter_advocate,
    ],
)


# ---------------------------------------------------------------------------
# Critic panel (3 in parallel) + merger
# ---------------------------------------------------------------------------

dedupe_critic = LlmAgent(
    name="dedupe_critic",
    model=_MODEL,
    description=(
        "Identifies duplicate findings across lenses and surfaces "
        "cross-lens themes."
    ),
    instruction=prompts.DEDUPE_CRITIC,
    output_schema=DedupeCriticOutput,
    output_key="dedupe_critic_output",
)


severity_critic = LlmAgent(
    name="severity_critic",
    model=_MODEL,
    description=(
        "Calibrates severity and confidence on reviewer findings, weighing "
        "the submitter's defense brief."
    ),
    instruction=prompts.SEVERITY_CRITIC,
    output_schema=SeverityCriticOutput,
    output_key="severity_critic_output",
)


gap_critic = LlmAgent(
    name="gap_critic",
    model=_MODEL,
    description=(
        "Surfaces issues the reviewer panel missed and proposes net-new "
        "findings to fill the gaps."
    ),
    instruction=prompts.GAP_CRITIC,
    output_schema=GapCriticOutput,
    output_key="gap_critic_output",
)


critic_panel = ParallelAgent(
    name="critic_panel",
    description=(
        "Runs three specialist critics (dedupe, severity, gap) in parallel "
        "over the reviewer outputs and the submitter defense brief."
    ),
    sub_agents=[dedupe_critic, severity_critic, gap_critic],
)


critic_merger = LlmAgent(
    name="critic_merger",
    model=_MODEL,
    description=(
        "Consolidates the three specialist critic outputs into a single "
        "CriticAssessment for the synthesizer, and recommends whether the "
        "loop should iterate again."
    ),
    instruction=prompts.CRITIC_MERGER,
    output_schema=CriticAssessment,
    output_key="critic_review",
)


# ---------------------------------------------------------------------------
# Loop decider (calls exit_loop when reviewers have converged)
# ---------------------------------------------------------------------------


def exit_loop(tool_context: ToolContext) -> dict:
    """Terminate the surrounding LoopAgent.

    Setting ``actions.escalate = True`` signals the parent LoopAgent to
    stop iterating.
    """
    tool_context.actions.escalate = True
    return {"status": "exiting"}


loop_decider = LlmAgent(
    name="loop_decider",
    model=_MODEL,
    description=(
        "Decides whether to iterate the review loop again based on the "
        "critic merger's iteration_recommendation. Calls exit_loop to stop."
    ),
    instruction=prompts.LOOP_DECIDER,
    tools=[exit_loop],
)


# ---------------------------------------------------------------------------
# Iterative review loop
# ---------------------------------------------------------------------------

iterative_review = LoopAgent(
    name="iterative_review",
    description=(
        "Iterates reviewer panel → critic panel → merger → decider until "
        "the merger reports convergence or the iteration cap is reached."
    ),
    max_iterations=_MAX_REVIEW_ITERATIONS,
    sub_agents=[
        reviewer_panel,
        critic_panel,
        critic_merger,
        loop_decider,
    ],
)


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

synthesizer_agent = LlmAgent(
    name="synthesizer",
    model=_MODEL,
    description=(
        "Produces the final consolidated MLR-style report from intake, "
        "reviewers, advocate, and critic outputs."
    ),
    instruction=prompts.SYNTHESIZER,
    output_schema=FinalReport,
    output_key="final_report",
)


# ---------------------------------------------------------------------------
# Root pipeline
# ---------------------------------------------------------------------------

root_agent = SequentialAgent(
    name="sentinel",
    description=(
        "Sentinel: agentic MLR-style review of promotional pharmaceutical "
        "content. Catalogues the submission, runs an iterative review loop "
        "(four critical lenses + a submitter advocate, with a three-way "
        "critic panel) and synthesises a discussion-oriented report aimed "
        "at the brand team."
    ),
    sub_agents=[
        intake_agent,
        iterative_review,
        synthesizer_agent,
    ],
)
