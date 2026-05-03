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

The pipeline is a single ``SequentialAgent`` whose stages are:

    intake → ParallelAgent(medical, legal, regulatory, editorial)
           → critic → synthesizer

Each stage writes its structured output to session state under a stable
``output_key`` so downstream stages can reference it via ``{key}``
templating in their instructions.

Run from the parent directory with::

    adk web .
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent

from sentinel_agent import prompts
from sentinel_agent.schemas import (
    ContentInventory,
    CriticAssessment,
    FinalReport,
    ReviewerOutput,
)

# All sub-agents share the same model. ``gemini-3-flash-preview`` is only
# served on the global Vertex endpoint, so callers must set
# ``GOOGLE_CLOUD_LOCATION=global``.
_MODEL = "gemini-3-flash-preview"


intake_agent = LlmAgent(
    name="intake",
    model=_MODEL,
    description=(
        "Catalogues every reviewable element in the submitted content into "
        "a structured inventory."
    ),
    instruction=prompts.INTAKE,
    output_schema=ContentInventory,
    output_key="intake_findings",
)


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


reviewer_panel = ParallelAgent(
    name="reviewer_panel",
    description=(
        "Runs the four MLR review lenses (medical, legal, regulatory, "
        "editorial) in parallel against the same submission."
    ),
    sub_agents=[
        medical_reviewer,
        legal_reviewer,
        regulatory_reviewer,
        editorial_reviewer,
    ],
)


critic_agent = LlmAgent(
    name="critic",
    model=_MODEL,
    description=(
        "Adversarial pass over the reviewer findings: dedupes, calibrates "
        "severity, and surfaces gaps."
    ),
    instruction=prompts.CRITIC,
    output_schema=CriticAssessment,
    output_key="critic_review",
)


synthesizer_agent = LlmAgent(
    name="synthesizer",
    model=_MODEL,
    description=(
        "Produces the final consolidated MLR-style report from intake, "
        "reviewers, and critic outputs."
    ),
    instruction=prompts.SYNTHESIZER,
    output_schema=FinalReport,
    output_key="final_report",
)


root_agent = SequentialAgent(
    name="sentinel",
    description=(
        "Sentinel: agentic MLR-style review of promotional pharmaceutical "
        "content. Cataloges the submission, runs four review lenses in "
        "parallel, applies an adversarial critic pass, and synthesises a "
        "discussion-oriented report."
    ),
    sub_agents=[
        intake_agent,
        reviewer_panel,
        critic_agent,
        synthesizer_agent,
    ],
)
