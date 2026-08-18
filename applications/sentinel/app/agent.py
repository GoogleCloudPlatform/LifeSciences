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

Pipeline shape (ADK v2 Workflow DAG)::

    START
        ↓
    _load_custom_rules (FunctionNode: loads rules artifact into state)
        ↓
    intake (LlmAgent: ContentInventory)
        ↓
    decide_intake_route (FunctionNode: routes "review" vs "direct_response")
        ├── route="direct_response" ──► direct_responder (LlmAgent: direct answer/greeting)
        └── route="review" ──► reviewer_panel (Parallel: 6 reviewers)
                                    ├── medical_reviewer
                                    ├── legal_reviewer
                                    ├── regulatory_reviewer
                                    ├── editorial_reviewer
                                    ├── submitter_advocate     # argues for the submission
                                    └── rules_reviewer         # custom rules reviewer
                                    ↓
                                join_reviewers (JoinNode barrier)
                                    ↓
                                critic_panel (Parallel: 3 critics)
                                    ├── dedupe_critic
                                    ├── severity_critic
                                    └── gap_critic
                                    ↓
                                join_critics (JoinNode barrier)
                                    ↓
                                critic_merger (LlmAgent: CriticAssessment)
                                    ↓
                                decide_review_loop (FunctionNode: routes "iterate" vs "synthesize")
                                    ├── route="iterate" ──► reviewer_panel (up to _MAX_REVIEW_ITERATIONS)
                                    └── route="synthesize" ──► synthesizer (LlmAgent: FinalReport)

Each stage uses ``gemini-3.7-flash`` with structured
``output_schema`` and writes to session state via ``output_key``. The
workflow iterates the reviewer panel + critic panel + merger up
to twice; the loop router directs execution based on the merger's
iteration recommendation and iteration budget. Non-promotional queries
short-circuit at intake to direct_responder.

Run from the parent directory with::

    adk web .
"""

from __future__ import annotations

import os
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.models.google_llm import Gemini
from google.adk.workflow import START, JoinNode, Workflow
from google.genai import types

from app import prompts
from app.schemas import (
    ContentInventory,
    CriticAssessment,
    DedupeCriticOutput,
    FinalReport,
    GapCriticOutput,
    ReviewerOutput,
    SeverityCriticOutput,
    SubmitterDefenseBrief,
)

# Pin the model endpoint region before any ADK / genai client is constructed.
# _MODEL defaults to a global-endpoint model, so without this the agent 404s on
# every path that does not go through terraform (local `adk web`, the Cloud Run
# deployment, the test suite). app_utils/services.py already assumes this pin.
os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("MODEL_LOCATION", "global")

# Retry configuration for model calls to handle transient 429 RESOURCE_EXHAUSTED
# and 5xx server errors across both agent and genai client layers.
_MODEL_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=int(os.getenv("MODEL_RETRY_ATTEMPTS", "5")),
    initial_delay=float(os.getenv("MODEL_RETRY_INITIAL_DELAY", "1.0")),
    max_delay=float(os.getenv("MODEL_RETRY_MAX_DELAY", "60.0")),
    exp_base=float(os.getenv("MODEL_RETRY_EXP_BASE", "2.0")),
    jitter=float(os.getenv("MODEL_RETRY_JITTER", "1.0")),
    http_status_codes=[408, 429, 500, 502, 503, 504],
)

# All sub-agents share the same model configured with automatic retries.
_MODEL = Gemini(
    model=os.getenv("SENTINEL_MODEL", "gemini-3.7-flash"),
    retry_options=_MODEL_RETRY_OPTIONS,
)

# Lift the per-response token cap so reviewers/critics/synthesizer aren't
# truncated mid-finding on dense submissions. 65535 is the upper bound the
# Gemini API accepts; the model will return whatever it actually produces.
_GENERATE_CONFIG = types.GenerateContentConfig(max_output_tokens=65535)

# Hard cap on review iterations. Each iteration runs the full reviewer
# panel + critic panel + merger, so the cost budget per cap
# is ~iterations * (6 reviewers + 3 critics + merger).
_MAX_REVIEW_ITERATIONS = 2


async def _load_custom_rules(ctx: Context, node_input: Any) -> Any:
    """Pull a user-uploaded rules artifact into ``state['custom_rules']``.

    Looks for any artifact whose filename contains ``rules`` and ends in
    ``.txt`` or ``.md``; first match wins. If none is found, leaves state
    unset — the ``rules_reviewer`` prompt's ``{custom_rules?}`` template
    handles the empty case explicitly (no-rules-file branch).

    Runs once at the start of the Sentinel pipeline via the entry node.
    """
    try:
        artifact_keys = await ctx.list_artifacts()
        for key in artifact_keys:
            lower = key.lower()
            if "rules" in lower and (lower.endswith(".txt") or lower.endswith(".md")):
                part = await ctx.load_artifact(key)
                blob = getattr(part, "inline_data", None) if part else None
                if blob and blob.data:
                    ctx.state["custom_rules"] = blob.data.decode(
                        "utf-8", errors="replace"
                    )
                    break
    except Exception:
        pass
    return node_input


# ---------------------------------------------------------------------------
# Intake & Routing
# ---------------------------------------------------------------------------

intake_agent = LlmAgent(
    name="intake",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
    description=(
        "Catalogues every reviewable element in the submitted content "
        "into a structured inventory."
    ),
    instruction=prompts.INTAKE,
    output_schema=ContentInventory,
    output_key="intake_findings",
)


def decide_intake_route(ctx: Context, node_input: Any) -> Event:
    """Decides whether to route to the full reviewer panel or short-circuit to direct_responder.

    Routes to 'direct_response' if the input has no promotional intent (e.g. general
    questions, greetings, informational inquiries). Otherwise routes to 'review'.
    """
    intake = ctx.state.get("intake_findings")
    if intake is None and isinstance(node_input, (dict, ContentInventory)):
        intake = node_input

    is_promotional: bool | None = None
    promotional_intent: str = ""
    items: list[Any] = []

    if isinstance(intake, dict):
        is_promotional = intake.get("is_promotional")
        promotional_intent = str(intake.get("promotional_intent", ""))
        items = intake.get("items", [])
    elif isinstance(intake, ContentInventory):
        is_promotional = getattr(intake, "is_promotional", None)
        promotional_intent = str(getattr(intake, "promotional_intent", ""))
        items = getattr(intake, "items", [])

    if is_promotional is False:
        return Event(output=node_input, route="direct_response")  # ty: ignore[pydantic-discarded-extra-argument]

    intent_lower = promotional_intent.lower().strip()
    non_promo_signals = (
        "no promotional intent",
        "not promotional",
        "non-promotional",
        "informational inquiry",
        "informational query",
        "informational question",
        "general question",
        "general inquiry",
        "conversational",
        "greeting",
        "no persuasive goal",
        "not a promotional",
        "not marketing",
    )

    if any(signal in intent_lower for signal in non_promo_signals):
        return Event(output=node_input, route="direct_response")  # ty: ignore[pydantic-discarded-extra-argument]

    if (intent_lower in ("none", "n/a", "none.", "n/a.") or not intent_lower) and len(
        items
    ) == 0:
        return Event(output=node_input, route="direct_response")  # ty: ignore[pydantic-discarded-extra-argument]

    return Event(output=node_input, route="review")  # ty: ignore[pydantic-discarded-extra-argument]


direct_responder = LlmAgent(
    name="direct_responder",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
    description=(
        "Provides a direct, helpful response to non-promotional inquiries, "
        "general questions, or greetings without running the full MLR review pipeline."
    ),
    instruction=prompts.DIRECT_RESPONDER,
)


# ---------------------------------------------------------------------------
# Reviewer panel (6 in parallel: 5 critical lenses + submitter advocate)
# ---------------------------------------------------------------------------

medical_reviewer = LlmAgent(
    name="medical_reviewer",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
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
    generate_content_config=_GENERATE_CONFIG,
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
    generate_content_config=_GENERATE_CONFIG,
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
    generate_content_config=_GENERATE_CONFIG,
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
    generate_content_config=_GENERATE_CONFIG,
    description=(
        "Argues for the submission. Produces a defense brief the critic "
        "panel weighs when calibrating severity."
    ),
    instruction=prompts.SUBMITTER_ADVOCATE,
    output_schema=SubmitterDefenseBrief,
    output_key="submitter_defense",
)


rules_reviewer = LlmAgent(
    name="rules_reviewer",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
    description=(
        "Reviews the submission against a user-supplied rules file (brand "
        "voice, internal SOPs, market-specific restrictions, etc.). Emits "
        "findings with lens='custom' that flow into the same dedupe / "
        "severity / synthesizer pipeline as the standard MLR reviewers."
    ),
    instruction=prompts.RULES_REVIEWER,
    output_schema=ReviewerOutput,
    output_key="rules_findings",
)


reviewer_panel = (
    medical_reviewer,
    legal_reviewer,
    regulatory_reviewer,
    editorial_reviewer,
    submitter_advocate,
    rules_reviewer,
)

join_reviewers = JoinNode(
    name="join_reviewers",
    description=(
        "Synchronizes and merges findings from all parallel reviewer lenses "
        "and submitter advocate."
    ),
)


# ---------------------------------------------------------------------------
# Critic panel (3 in parallel) + merger
# ---------------------------------------------------------------------------

dedupe_critic = LlmAgent(
    name="dedupe_critic",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
    description=(
        "Identifies duplicate findings across lenses and surfaces cross-lens themes."
    ),
    instruction=prompts.DEDUPE_CRITIC,
    output_schema=DedupeCriticOutput,
    output_key="dedupe_critic_output",
)


severity_critic = LlmAgent(
    name="severity_critic",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
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
    generate_content_config=_GENERATE_CONFIG,
    description=(
        "Surfaces issues the reviewer panel missed and proposes net-new "
        "findings to fill the gaps."
    ),
    instruction=prompts.GAP_CRITIC,
    output_schema=GapCriticOutput,
    output_key="gap_critic_output",
)


critic_panel = (dedupe_critic, severity_critic, gap_critic)

join_critics = JoinNode(
    name="join_critics",
    description="Synchronizes completion of all specialist critic agents.",
)


critic_merger = LlmAgent(
    name="critic_merger",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
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
# Review loop routing
# ---------------------------------------------------------------------------


def decide_review_loop(ctx: Context, node_input: Any) -> Event:
    """Decides whether to iterate the review loop again based on the
    critic merger's iteration_recommendation and the iteration cap.
    """
    iteration = ctx.state.get("review_iteration_count", 0) + 1
    ctx.state["review_iteration_count"] = iteration

    critic_review = ctx.state.get("critic_review")
    recommendation = ""
    if isinstance(critic_review, dict):
        recommendation = critic_review.get("iteration_recommendation", "")
    elif hasattr(critic_review, "iteration_recommendation"):
        recommendation = critic_review.iteration_recommendation

    if (
        iteration < _MAX_REVIEW_ITERATIONS
        and recommendation == "another_pass_would_help"
    ):
        return Event(output=node_input, route="iterate")  # ty: ignore[pydantic-discarded-extra-argument]
    return Event(output=node_input, route="synthesize")  # ty: ignore[pydantic-discarded-extra-argument]


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

synthesizer_agent = LlmAgent(
    name="synthesizer",
    model=_MODEL,
    generate_content_config=_GENERATE_CONFIG,
    description=(
        "Produces the final consolidated MLR-style report from intake, "
        "reviewers, advocate, and critic outputs."
    ),
    instruction=prompts.SYNTHESIZER,
    output_schema=FinalReport,
    output_key="final_report",
)


# ---------------------------------------------------------------------------
# Root pipeline (Workflow DAG)
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="sentinel",
    description=(
        "Sentinel: agentic MLR-style review of promotional pharmaceutical "
        "content. Catalogues the submission, runs an iterative review loop "
        "(four critical lenses + a submitter advocate + a custom-rules "
        "reviewer, with a three-way critic panel) and synthesises a "
        "discussion-oriented report aimed at the brand team."
    ),
    edges=[
        (START, _load_custom_rules),
        (_load_custom_rules, intake_agent),
        (intake_agent, decide_intake_route),
        (
            decide_intake_route,
            {"review": reviewer_panel, "direct_response": direct_responder},
        ),
        (reviewer_panel, join_reviewers),
        (join_reviewers, critic_panel),
        (critic_panel, join_critics),
        (join_critics, critic_merger),
        (critic_merger, decide_review_loop),
        (
            decide_review_loop,
            {"iterate": reviewer_panel, "synthesize": synthesizer_agent},
        ),
    ],
)

app = App(root_agent=root_agent, name="app")
