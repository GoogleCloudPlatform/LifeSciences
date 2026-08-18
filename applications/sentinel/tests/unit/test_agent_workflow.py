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

"""Unit tests for Sentinel ADK v2 Workflow DAG."""

import pytest
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.runners import InMemoryRunner
from google.adk.workflow import START, JoinNode, Workflow
from google.genai import types

from app.agent import (
    _MAX_REVIEW_ITERATIONS,
    _load_custom_rules,
    app,
    critic_panel,
    decide_intake_route,
    decide_review_loop,
    dedupe_critic,
    direct_responder,
    editorial_reviewer,
    gap_critic,
    join_critics,
    join_reviewers,
    legal_reviewer,
    medical_reviewer,
    regulatory_reviewer,
    reviewer_panel,
    root_agent,
    rules_reviewer,
    severity_critic,
    submitter_advocate,
)


def test_workflow_graph_structure() -> None:
    """Verify that root_agent compiles into an ADK v2 Workflow DAG with expected nodes and edges."""
    assert isinstance(root_agent, Workflow)
    assert root_agent.name == "sentinel"
    assert app.root_agent == root_agent

    graph = root_agent.graph
    assert graph is not None

    node_names = {node.name for node in graph.nodes}
    expected_nodes = {
        "__START__",
        "_load_custom_rules",
        "intake",
        "decide_intake_route",
        "direct_responder",
        "medical_reviewer",
        "legal_reviewer",
        "regulatory_reviewer",
        "editorial_reviewer",
        "submitter_advocate",
        "rules_reviewer",
        "join_reviewers",
        "dedupe_critic",
        "severity_critic",
        "gap_critic",
        "join_critics",
        "critic_merger",
        "decide_review_loop",
        "synthesizer",
    }
    assert expected_nodes.issubset(node_names)
    assert isinstance(join_reviewers, JoinNode)
    assert isinstance(join_critics, JoinNode)
    assert direct_responder is not None
    assert callable(decide_intake_route)


def test_reviewer_and_critic_panels() -> None:
    """Verify reviewer and critic panel tuples."""
    assert reviewer_panel == (
        medical_reviewer,
        legal_reviewer,
        regulatory_reviewer,
        editorial_reviewer,
        submitter_advocate,
        rules_reviewer,
    )
    assert critic_panel == (
        dedupe_critic,
        severity_critic,
        gap_critic,
    )


def test_agent_model_configuration() -> None:
    """Verify that agents use Gemini models with automatic retry options configured."""
    from google.adk.models.google_llm import Gemini

    from app.agent import _MODEL, intake_agent, synthesizer_agent

    assert isinstance(_MODEL, Gemini)
    assert _MODEL.model == "gemini-3.7-flash"
    retry_options = _MODEL.retry_options
    assert retry_options is not None
    assert retry_options.attempts is not None
    assert retry_options.attempts >= 3
    status_codes = retry_options.http_status_codes or []
    assert 429 in status_codes
    assert 500 in status_codes
    assert 503 in status_codes
    assert intake_agent.model == _MODEL
    assert synthesizer_agent.model == _MODEL


class MockContext:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state if state is not None else {}


def test_decide_intake_route_non_promotional_flag() -> None:
    """When is_promotional is False, route to 'direct_response'."""
    ctx = MockContext(
        state={
            "intake_findings": {
                "is_promotional": False,
                "promotional_intent": "General question",
                "items": [],
            }
        }
    )
    event = decide_intake_route(ctx, node_input="test_input")
    assert isinstance(event, Event)
    assert event.actions.route == "direct_response"
    assert event.output == "test_input"


def test_decide_intake_route_probe_a_inquiry() -> None:
    """When promotional_intent indicates informational inquiry, route to 'direct_response'."""
    ctx = MockContext(
        state={
            "intake_findings": {
                "promotional_intent": "The submission has no promotional intent; it is an informational inquiry",
                "items": [],
            }
        }
    )
    event = decide_intake_route(ctx, node_input="test_input")
    assert isinstance(event, Event)
    assert event.actions.route == "direct_response"


def test_decide_intake_route_greeting_empty_items() -> None:
    """When submission is a greeting with no reviewable items, route to 'direct_response'."""
    ctx = MockContext(
        state={
            "intake_findings": {
                "promotional_intent": "greeting / conversational",
                "items": [],
            }
        }
    )
    event = decide_intake_route(ctx, node_input="test_input")
    assert isinstance(event, Event)
    assert event.actions.route == "direct_response"


def test_decide_intake_route_promotional_submission() -> None:
    """When input has promotional intent and items, route to 'review'."""
    ctx = MockContext(
        state={
            "intake_findings": {
                "is_promotional": True,
                "promotional_intent": "Persuade HCPs to prescribe DrugX",
                "items": [
                    {
                        "item_id": "C1",
                        "kind": "product_claim",
                        "text": "DrugX works",
                    }
                ],
            }
        }
    )
    event = decide_intake_route(ctx, node_input="test_input")
    assert isinstance(event, Event)
    assert event.actions.route == "review"


def test_decide_review_loop_iteration_branch() -> None:
    """When iteration < max and critic recommends another pass, route to 'iterate'."""
    ctx = MockContext(
        state={
            "review_iteration_count": 0,
            "critic_review": {"iteration_recommendation": "another_pass_would_help"},
        }
    )
    event = decide_review_loop(ctx, node_input="test_input")
    assert isinstance(event, Event)
    assert event.actions.route == "iterate"
    assert event.output == "test_input"
    assert ctx.state["review_iteration_count"] == 1


def test_decide_review_loop_max_iteration_cap() -> None:
    """When iteration count reaches _MAX_REVIEW_ITERATIONS, route to 'synthesize' regardless of recommendation."""
    ctx = MockContext(
        state={
            "review_iteration_count": 1,
            "critic_review": {"iteration_recommendation": "another_pass_would_help"},
        }
    )
    event = decide_review_loop(ctx, node_input="test_input")
    assert isinstance(event, Event)
    assert event.actions.route == "synthesize"
    assert event.output == "test_input"
    assert ctx.state["review_iteration_count"] == 2


def test_decide_review_loop_converged_branch() -> None:
    """When critic recommends converged, route to 'synthesize' immediately on first pass."""
    ctx = MockContext(
        state={
            "review_iteration_count": 0,
            "critic_review": {"iteration_recommendation": "reviewers_have_converged"},
        }
    )
    event = decide_review_loop(ctx, node_input="test_input")
    assert isinstance(event, Event)
    assert event.actions.route == "synthesize"
    assert ctx.state["review_iteration_count"] == 1


@pytest.mark.asyncio
async def test_load_custom_rules_passthrough() -> None:
    """Verify _load_custom_rules gracefully passes through input even if no artifact is attached."""

    class DummyContext:
        def __init__(self) -> None:
            self.state = {}

        async def list_artifacts(self) -> list[str]:
            return []

    ctx = DummyContext()
    result = await _load_custom_rules(ctx, "test_prompt")
    assert result == "test_prompt"


@pytest.mark.asyncio
async def test_simulated_workflow_execution() -> None:
    """Execute a simulated Sentinel workflow graph with mock node functions via InMemoryRunner."""
    call_counts = {}

    def track(name: str) -> None:
        call_counts[name] = call_counts.get(name, 0) + 1

    async def mock_load_rules(ctx: Context, node_input: object) -> object:
        track("load_rules")
        return node_input

    async def mock_intake(ctx: Context, node_input: object) -> dict:
        track("intake")
        ctx.state["intake_findings"] = {
            "is_promotional": True,
            "promotional_intent": "Promote therapeutic efficacy",
            "items": [{"item_id": "C1", "kind": "product_claim", "text": "Claim"}],
        }
        return ctx.state["intake_findings"]

    def mock_decide_intake(ctx: Context, node_input: object) -> Event:
        track("decide_intake")
        return decide_intake_route(ctx, node_input)

    async def mock_direct_responder(ctx: Context, node_input: object) -> dict:
        track("direct_responder")
        return {"response": "direct"}

    async def mock_med(ctx: Context, node_input: object) -> dict:
        track("med")
        return {"med": 1}

    async def mock_leg(ctx: Context, node_input: object) -> dict:
        track("leg")
        return {"leg": 1}

    async def mock_reg(ctx: Context, node_input: object) -> dict:
        track("reg")
        return {"reg": 1}

    async def mock_ed(ctx: Context, node_input: object) -> dict:
        track("ed")
        return {"ed": 1}

    async def mock_adv(ctx: Context, node_input: object) -> dict:
        track("adv")
        return {"adv": 1}

    async def mock_rules(ctx: Context, node_input: object) -> dict:
        track("rules")
        return {"rules": 1}

    async def mock_dedupe(ctx: Context, node_input: object) -> dict:
        track("dedupe")
        return {"dedupe": 1}

    async def mock_severity(ctx: Context, node_input: object) -> dict:
        track("severity")
        return {"severity": 1}

    async def mock_gap(ctx: Context, node_input: object) -> dict:
        track("gap")
        return {"gap": 1}

    async def mock_merger(ctx: Context, node_input: object) -> dict:
        track("merger")
        count = call_counts.get("merger", 0)
        rec = "another_pass_would_help" if count == 1 else "reviewers_have_converged"
        ctx.state["critic_review"] = {"iteration_recommendation": rec}
        return {"merger": count}

    def mock_decide_loop(ctx: Context, node_input: object) -> Event:
        track("decide_loop")
        iteration = ctx.state.get("review_iteration_count", 0) + 1
        ctx.state["review_iteration_count"] = iteration
        critic_review = ctx.state.get("critic_review") or {}
        rec = critic_review.get("iteration_recommendation", "")
        if iteration < _MAX_REVIEW_ITERATIONS and rec == "another_pass_would_help":
            return Event(output=node_input, route="iterate")  # ty: ignore[pydantic-discarded-extra-argument]
        return Event(output=node_input, route="synthesize")  # ty: ignore[pydantic-discarded-extra-argument]

    async def mock_synth(ctx: Context, node_input: object) -> dict:
        track("synth")
        return {"report": "complete"}

    sim_reviewers = (
        mock_med,
        mock_leg,
        mock_reg,
        mock_ed,
        mock_adv,
        mock_rules,
    )
    sim_join_r = JoinNode(name="sim_join_r")
    sim_critics = (mock_dedupe, mock_severity, mock_gap)
    sim_join_c = JoinNode(name="sim_join_c")

    edges = [
        (START, mock_load_rules),
        (mock_load_rules, mock_intake),
        (mock_intake, mock_decide_intake),
        (
            mock_decide_intake,
            {"review": sim_reviewers, "direct_response": mock_direct_responder},
        ),
        (sim_reviewers, sim_join_r),
        (sim_join_r, sim_critics),
        (sim_critics, sim_join_c),
        (sim_join_c, mock_merger),
        (mock_merger, mock_decide_loop),
        (
            mock_decide_loop,
            {"iterate": sim_reviewers, "synthesize": mock_synth},
        ),
    ]

    sim_workflow = Workflow(name="sim_sentinel", edges=edges)
    sim_app = App(name="sim_app", root_agent=sim_workflow)
    runner = InMemoryRunner(app=sim_app)

    session = await runner.session_service.create_session(
        app_name="sim_app", user_id="test_user"
    )

    message = types.Content(
        role="user", parts=[types.Part.from_text(text="Review promo piece")]
    )

    events = []
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=message,
    ):
        events.append(event)

    assert len(events) > 0
    # Verified: intake ran 1x, decide_intake ran 1x, reviewers/critics/merger ran 2x, synth ran 1x
    assert call_counts["load_rules"] == 1
    assert call_counts["intake"] == 1
    assert call_counts["decide_intake"] == 1
    assert "direct_responder" not in call_counts
    assert call_counts["med"] == 2
    assert call_counts["dedupe"] == 2
    assert call_counts["merger"] == 2
    assert call_counts["decide_loop"] == 2
    assert call_counts["synth"] == 1


@pytest.mark.asyncio
async def test_simulated_workflow_short_circuit_execution() -> None:
    """Execute a simulated Sentinel workflow graph where non-promotional intake short-circuits to direct responder."""
    call_counts = {}

    def track(name: str) -> None:
        call_counts[name] = call_counts.get(name, 0) + 1

    async def mock_load_rules(ctx: Context, node_input: object) -> object:
        track("load_rules")
        return node_input

    async def mock_intake(ctx: Context, node_input: object) -> dict:
        track("intake")
        ctx.state["intake_findings"] = {
            "is_promotional": False,
            "promotional_intent": "The submission has no promotional intent; it is an informational inquiry",
            "items": [],
        }
        return ctx.state["intake_findings"]

    def mock_decide_intake(ctx: Context, node_input: object) -> Event:
        track("decide_intake")
        return decide_intake_route(ctx, node_input)

    async def mock_direct_responder(ctx: Context, node_input: object) -> dict:
        track("direct_responder")
        return {"response": "An MLR review is..."}

    async def mock_reviewer(ctx: Context, node_input: object) -> dict:
        track("reviewer")
        return {"review": "done"}

    edges = [
        (START, mock_load_rules),
        (mock_load_rules, mock_intake),
        (mock_intake, mock_decide_intake),
        (
            mock_decide_intake,
            {"review": mock_reviewer, "direct_response": mock_direct_responder},
        ),
    ]

    sim_workflow = Workflow(name="sim_short_circuit", edges=edges)
    sim_app = App(name="sim_app_sc", root_agent=sim_workflow)
    runner = InMemoryRunner(app=sim_app)

    session = await runner.session_service.create_session(
        app_name="sim_app_sc", user_id="test_user"
    )

    message = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text="In one short sentence: what is an MLR review in pharmaceutical marketing?"
            )
        ],
    )

    events = []
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=message,
    ):
        events.append(event)

    assert len(events) > 0
    # Verified: intake ran 1x, decide_intake ran 1x, direct_responder ran 1x, reviewers were NOT called
    assert call_counts["load_rules"] == 1
    assert call_counts["intake"] == 1
    assert call_counts["decide_intake"] == 1
    assert call_counts["direct_responder"] == 1
    assert "reviewer" not in call_counts
