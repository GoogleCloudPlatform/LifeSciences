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

"""Unit tests for PaperBanana ADK v2 Workflow DAG."""

import json
from typing import Any

import pytest
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.workflow import START, Workflow
from google.genai import types

from app.agent import (
    _IMAGE_MODEL,
    _MAX_CRITIC_ROUNDS,
    _PLANNER_MODEL,
    _S_CAPTION,
    _S_DESCRIPTION,
    _S_IMAGE_NAME,
    _S_IMAGE_VERSION,
    _S_INTENT,
    _S_LAST_VALID_IMAGE,
    _S_LAST_VALID_IMAGE_VERSION,
    _S_LAUNCHED,
    _S_ROUND,
    _S_STYLED,
    _S_TURN_ID,
    _S_VERDICT_RAW,
    _SHOW_THOUGHTS,
    THINKING_PLANNER,
    _build_captioner_request,
    _build_critic_request,
    _captioner_agent,
    _clean_figure_title,
    _critic_agent,
    _extract_intent,
    _format_figure_caption,
    _inject_uploaded_artifacts,
    _is_no_changes,
    _load_paper_part,
    _parse_critic_verdict,
    _planner_agent,
    _save_captioner_output,
    _save_critic_output,
    _save_planner_output,
    _save_stylist_output,
    _save_visualizer_image,
    _strip_procedural_preamble,
    _strip_tool_preamble,
    _stylist_agent,
    _visualizer_agent,
    app,
    coordinator_agent,
    decide_refinement_loop,
    finalize,
    generate_figure,
    paperbanana_pipeline,
    prep_inputs,
    root_agent,
)


def test_workflow_graph_structure() -> None:
    """Verify that root_agent and paperbanana_pipeline compile into ADK v2 Workflow DAGs."""
    assert isinstance(root_agent, Workflow)
    assert root_agent.name == "paperbanana"
    assert app.root_agent == root_agent

    root_nodes = {node.name for node in root_agent.graph.nodes}
    assert {"__START__", "coordinator_agent", "paperbanana_pipeline"}.issubset(
        root_nodes
    )

    assert isinstance(paperbanana_pipeline, Workflow)
    assert paperbanana_pipeline.name == "paperbanana_pipeline"

    pipe_nodes = {node.name for node in paperbanana_pipeline.graph.nodes}
    expected_pipe_nodes = {
        "__START__",
        "prep_inputs",
        "PaperBananaPlanner",
        "PaperBananaStylist",
        "PaperBananaVisualizer",
        "PaperBananaCritic",
        "decide_refinement_loop",
        "PaperBananaCaptioner",
        "finalize",
    }
    assert expected_pipe_nodes.issubset(pipe_nodes)


def test_model_configuration() -> None:
    """Verify that agents use configured models with retry options and thinking planner."""
    assert isinstance(_PLANNER_MODEL, Gemini)
    assert _PLANNER_MODEL.model == "gemini-3.8-flash"
    assert _PLANNER_MODEL.retry_options is not None

    assert isinstance(_IMAGE_MODEL, Gemini)
    assert _IMAGE_MODEL.model == "gemini-3-pro-image"
    assert _IMAGE_MODEL.retry_options is not None

    assert _planner_agent.model == _PLANNER_MODEL
    assert _stylist_agent.model == _PLANNER_MODEL
    assert _critic_agent.model == _PLANNER_MODEL
    assert _captioner_agent.model == _PLANNER_MODEL
    assert _visualizer_agent.model == _IMAGE_MODEL
    assert coordinator_agent.model == _PLANNER_MODEL

    # Thinking planner configuration
    if _SHOW_THOUGHTS:
        assert THINKING_PLANNER is not None
        assert THINKING_PLANNER.thinking_config.include_thoughts is True
        assert _planner_agent.planner == THINKING_PLANNER
        assert _stylist_agent.planner == THINKING_PLANNER
        assert _critic_agent.planner == THINKING_PLANNER
        assert _captioner_agent.planner == THINKING_PLANNER
        assert coordinator_agent.planner == THINKING_PLANNER
    # Visualizer uses image model, which does not support thinking
    assert _visualizer_agent.planner is None


class MockContext:
    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state if state is not None else {}


def test_prep_inputs_staging() -> None:
    """Verify prep_inputs resets state, mints turn_id, snapshots prior image, and extracts intent."""
    ctx = MockContext(
        state={
            "current_image_name": "figure_tx1_v0.png",
            "description": "old desc",
            "styled_description": "old styled",
            "critic_verdict_raw": "old verdict",
            "current_round": 2,
        }
    )
    node_input = "A methodology overview diagram."
    result = prep_inputs(ctx, node_input)  # ty: ignore[invalid-argument-type]

    assert result == node_input
    assert ctx.state.get("previous_turn_image") == "figure_tx1_v0.png"
    assert "current_image_name" not in ctx.state
    assert "description" not in ctx.state
    assert "styled_description" not in ctx.state
    assert "critic_verdict_raw" not in ctx.state
    assert ctx.state.get("current_round") == 0
    assert ctx.state.get("intent") == "A methodology overview diagram."
    assert "turn_id" in ctx.state
    assert len(ctx.state["turn_id"]) == 8


def test_parse_critic_verdict() -> None:
    """Verify JSON parsing from raw critic output, including markdown blocks."""
    # Plain JSON
    raw_json = '{"critic_suggestions": "Make font bigger", "revised_description": "Updated diagram"}'
    ok, suggestions, revised = _parse_critic_verdict(raw_json)
    assert ok is True
    assert suggestions == "Make font bigger"
    assert revised == "Updated diagram"

    # Markdown fence
    markdown_json = '```json\n{"critic_suggestions": "no changes needed", "revised_description": "no changes needed"}\n```'
    ok, suggestions, revised = _parse_critic_verdict(markdown_json)
    assert ok is True
    assert _is_no_changes(suggestions, revised) is True

    # Malformed
    ok, suggestions, revised = _parse_critic_verdict("not a json")
    assert ok is False
    assert suggestions == ""
    assert revised == ""


def test_decide_refinement_loop_routes() -> None:
    """Verify decide_refinement_loop routing behavior."""
    # 1. Refinement requested and round < max
    ctx = MockContext(
        state={
            _S_IMAGE_NAME: "figure_tx1_v0.png",
            "current_round": 1,
            "critic_verdict_raw": json.dumps(
                {
                    "critic_suggestions": "Adjust layout",
                    "revised_description": "New layout",
                }
            ),
        }
    )
    event = decide_refinement_loop(ctx, "in")  # ty: ignore[invalid-argument-type]
    assert isinstance(event, Event)
    assert event.actions.route == "refine"
    assert ctx.state.get("styled_description") == "New layout"

    # 2. No changes needed
    ctx = MockContext(
        state={
            _S_IMAGE_NAME: "figure_tx1_v0.png",
            "current_round": 1,
            "critic_verdict_raw": json.dumps(
                {
                    "critic_suggestions": "No changes needed",
                    "revised_description": "No changes needed",
                }
            ),
        }
    )
    event = decide_refinement_loop(ctx, "in")  # ty: ignore[invalid-argument-type]
    assert isinstance(event, Event)
    assert event.actions.route == "finalize"

    # 3. Max rounds reached
    ctx = MockContext(
        state={
            _S_IMAGE_NAME: "figure_tx1_v0.png",
            "current_round": _MAX_CRITIC_ROUNDS,
            "critic_verdict_raw": json.dumps(
                {"critic_suggestions": "Keep tweaking", "revised_description": "Tweak"}
            ),
        }
    )
    event = decide_refinement_loop(ctx, "in")  # ty: ignore[invalid-argument-type]
    assert isinstance(event, Event)
    assert event.actions.route == "finalize"

    # 4. No image rendered (visualizer failure) -> route directly to finalize_direct
    ctx_no_img = MockContext(
        state={
            "current_round": 1,
            "critic_verdict_raw": json.dumps(
                {
                    "critic_suggestions": "Adjust layout",
                    "revised_description": "New layout",
                }
            ),
        }
    )
    event_no_img = decide_refinement_loop(ctx_no_img, "in")  # ty: ignore[invalid-argument-type]
    assert isinstance(event_no_img, Event)
    assert event_no_img.actions.route == "finalize_direct"


def test_finalize_node() -> None:
    """Verify finalize formatting with caption, fallback intent, and without generated image."""
    # With image and generated caption
    ctx_caption = MockContext(
        state={
            "current_image_name": "figure_abc123_v1.png",
            _S_CAPTION: "**Figure**: Detailed multi-panel scientific workflow diagram.",
            _S_INTENT: "Overview diagram",
        }
    )
    events_caption = list(finalize(ctx_caption, "in"))  # ty: ignore[invalid-argument-type]
    assert len(events_caption) == 2
    img_event, cap_event = events_caption
    assert img_event.actions.artifact_delta == {"figure_abc123_v1.png": 0}
    assert img_event.content is None
    assert "<start_of_user_uploaded_file" not in cap_event.output
    assert (
        cap_event.output
        == "**Figure**: Detailed multi-panel scientific workflow diagram."
    )

    # With image but fallback to intent
    ctx_fallback = MockContext(
        state={
            "current_image_name": "figure_abc123_v1.png",
            _S_INTENT: "Overview diagram",
            "styled_description": "Massive 5000-word prompt description",
        }
    )
    events_fallback = list(finalize(ctx_fallback, "in"))  # ty: ignore[invalid-argument-type]
    assert len(events_fallback) == 2
    img_event_fb, cap_event_fb = events_fallback
    assert img_event_fb.actions.artifact_delta == {"figure_abc123_v1.png": 0}
    assert "<start_of_user_uploaded_file" not in cap_event_fb.output
    assert cap_event_fb.output == "**Figure**: Overview diagram"
    assert "Massive 5000-word prompt description" not in cap_event_fb.output
    assert "Description used:" not in cap_event_fb.output

    # Without image
    ctx_empty = MockContext(state={})
    events_empty = list(finalize(ctx_empty, "in"))  # ty: ignore[invalid-argument-type]
    assert len(events_empty) == 1
    assert "unable to render a figure" in events_empty[0].output


def test_generate_figure_tool() -> None:
    """Verify generate_figure routing tool sets route and updates state."""

    class MockActions:
        def __init__(self) -> None:
            self.route = None

    class MockToolContext:
        def __init__(self) -> None:
            self.actions = MockActions()
            self.state = {}

    tool_ctx = MockToolContext()
    result = generate_figure(intent="Transformer architecture", tool_context=tool_ctx)  # ty: ignore[invalid-argument-type]
    assert result["status"] == "launched"
    assert result["intent"] == "Transformer architecture"
    assert tool_ctx.actions.route == "generate_figure"
    assert tool_ctx.state["intent"] == "Transformer architecture"


@pytest.mark.asyncio
async def test_simulated_pipeline_single_pass() -> None:
    """Simulate paperbanana_pipeline DAG execution with 1-pass convergence."""
    call_counts = {}

    def track(name: str) -> None:
        call_counts[name] = call_counts.get(name, 0) + 1

    def mock_prep(ctx: Context, node_input: Any) -> Any:
        track("prep")
        return prep_inputs(ctx, node_input)

    async def mock_planner(ctx: Context, node_input: Any) -> Any:
        track("planner")
        ctx.state["description"] = "Mock draft description"
        return ctx.state["description"]

    async def mock_stylist(ctx: Context, node_input: Any) -> Any:
        track("stylist")
        ctx.state["styled_description"] = "Mock styled description"
        return ctx.state["styled_description"]

    async def mock_visualizer(ctx: Context, node_input: Any) -> Any:
        track("visualizer")
        round_idx = ctx.state.get("current_round", 0)
        ctx.state["current_image_name"] = f"figure_mock_v{round_idx}.png"
        ctx.state["current_round"] = round_idx + 1
        return ctx.state["current_image_name"]

    async def mock_critic(ctx: Context, node_input: Any) -> Any:
        track("critic")
        # Indicate convergence on first round
        ctx.state["critic_verdict_raw"] = json.dumps(
            {
                "critic_suggestions": "No changes needed",
                "revised_description": "No changes needed",
            }
        )
        return ctx.state["critic_verdict_raw"]

    def mock_decide(ctx: Context, node_input: Any) -> Event:
        track("decide")
        return decide_refinement_loop(ctx, node_input)

    async def mock_captioner(ctx: Context, node_input: Any) -> Any:
        track("captioner")
        ctx.state[_S_CAPTION] = "**Figure**: Overview diagram with three panels."
        return ctx.state[_S_CAPTION]

    def mock_finalize(ctx: Context, node_input: Any) -> Any:
        track("finalize")
        yield from finalize(ctx, node_input)

    edges = [
        (START, mock_prep),
        (mock_prep, mock_planner),
        (mock_planner, mock_stylist),
        (mock_stylist, mock_visualizer),
        (mock_visualizer, mock_critic),
        (mock_critic, mock_decide),
        (mock_decide, {"refine": mock_visualizer, "finalize": mock_captioner}),
        (mock_captioner, mock_finalize),
    ]

    sim_wf = Workflow(name="sim_pipeline", edges=edges)
    sim_app = App(name="sim_app", root_agent=sim_wf)
    runner = InMemoryRunner(app=sim_app)

    session = await runner.session_service.create_session(
        app_name="sim_app", user_id="user1"
    )
    events = []
    async for event in runner.run_async(
        user_id="user1",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text="Generate figure")]
        ),
    ):
        events.append(event)

    assert len(events) > 0
    assert call_counts["prep"] == 1
    assert call_counts["planner"] == 1
    assert call_counts["stylist"] == 1
    assert call_counts["visualizer"] == 1
    assert call_counts["critic"] == 1
    assert call_counts["decide"] == 1
    assert call_counts["captioner"] == 1
    assert call_counts["finalize"] == 1


@pytest.mark.asyncio
async def test_simulated_pipeline_refinement_loop() -> None:
    """Simulate paperbanana_pipeline DAG execution with a 2-pass refinement loop."""
    call_counts = {}

    def track(name: str) -> None:
        call_counts[name] = call_counts.get(name, 0) + 1

    def mock_prep(ctx: Context, node_input: Any) -> Any:
        track("prep")
        return prep_inputs(ctx, node_input)

    async def mock_planner(ctx: Context, node_input: Any) -> Any:
        track("planner")
        ctx.state["description"] = "Initial draft"
        return ctx.state["description"]

    async def mock_stylist(ctx: Context, node_input: Any) -> Any:
        track("stylist")
        ctx.state["styled_description"] = "Initial styled"
        return ctx.state["styled_description"]

    async def mock_visualizer(ctx: Context, node_input: Any) -> Any:
        track("visualizer")
        round_idx = ctx.state.get("current_round", 0)
        ctx.state["current_image_name"] = f"figure_mock_v{round_idx}.png"
        ctx.state["current_round"] = round_idx + 1
        return ctx.state["current_image_name"]

    async def mock_critic(ctx: Context, node_input: Any) -> Any:
        track("critic")
        round_idx = ctx.state.get("current_round", 0)
        if round_idx == 1:
            # Round 1: request revisions
            ctx.state["critic_verdict_raw"] = json.dumps(
                {
                    "critic_suggestions": "Make icons clearer",
                    "revised_description": "Clearer icons styled",
                }
            )
        else:
            # Round 2: converged
            ctx.state["critic_verdict_raw"] = json.dumps(
                {
                    "critic_suggestions": "No changes needed",
                    "revised_description": "No changes needed",
                }
            )
        return ctx.state["critic_verdict_raw"]

    def mock_decide(ctx: Context, node_input: Any) -> Event:
        track("decide")
        return decide_refinement_loop(ctx, node_input)

    async def mock_captioner(ctx: Context, node_input: Any) -> Any:
        track("captioner")
        ctx.state[_S_CAPTION] = "**Figure**: Refined diagram with enhanced icons."
        return ctx.state[_S_CAPTION]

    def mock_finalize(ctx: Context, node_input: Any) -> Any:
        track("finalize")
        yield from finalize(ctx, node_input)

    edges = [
        (START, mock_prep),
        (mock_prep, mock_planner),
        (mock_planner, mock_stylist),
        (mock_stylist, mock_visualizer),
        (mock_visualizer, mock_critic),
        (mock_critic, mock_decide),
        (mock_decide, {"refine": mock_visualizer, "finalize": mock_captioner}),
        (mock_captioner, mock_finalize),
    ]

    sim_wf = Workflow(name="sim_pipeline_loop", edges=edges)
    sim_app = App(name="sim_app_loop", root_agent=sim_wf)
    runner = InMemoryRunner(app=sim_app)

    session = await runner.session_service.create_session(
        app_name="sim_app_loop", user_id="user1"
    )
    events = []
    async for event in runner.run_async(
        user_id="user1",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text="Generate figure")]
        ),
    ):
        events.append(event)

    assert len(events) > 0
    assert call_counts["prep"] == 1
    assert call_counts["planner"] == 1
    assert call_counts["stylist"] == 1
    assert call_counts["visualizer"] == 2  # Ran 2 rounds!
    assert call_counts["critic"] == 2  # Ran 2 rounds!
    assert call_counts["decide"] == 2  # Decided twice
    assert call_counts["captioner"] == 1  # Captioner ran once on finalize
    assert call_counts["finalize"] == 1  # Finalized once

    # Verify that only finalize produced visible model content
    model_content_events = [
        e
        for e in events
        if e.content
        and e.content.role == "model"
        and any(getattr(p, "text", None) for p in e.content.parts)
    ]
    assert len(model_content_events) == 1
    final_text = model_content_events[0].content.parts[0].text or ""
    assert "<start_of_user_uploaded_file" not in final_text
    assert final_text.startswith("**Figure**:")
    assert "Description used:" not in final_text


@pytest.mark.asyncio
async def test_simulated_coordinator_conversational_turn() -> None:
    """Verify that a conversational turn does not trigger the figure pipeline."""
    call_counts = {}

    def track(name: str) -> None:
        call_counts[name] = call_counts.get(name, 0) + 1

    async def mock_coordinator(ctx: Context, node_input: Any) -> Event:
        track("coordinator")
        return Event(
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text="Hello! Please attach a research paper PDF."
                    )
                ],
            )
        )

    def mock_pipeline_step(ctx: Context, node_input: Any) -> Event:
        track("pipeline")
        return Event(output="should not be reached")

    sub_wf = Workflow(name="sub_pipeline", edges=[(START, mock_pipeline_step)])
    root_wf = Workflow(
        name="test_root",
        edges=[
            (START, mock_coordinator),
            (mock_coordinator, {"generate_figure": sub_wf}),
        ],
    )
    runner = InMemoryRunner(app=App(name="test_conv", root_agent=root_wf))
    session = await runner.session_service.create_session(
        app_name="test_conv", user_id="u1"
    )

    events = []
    async for event in runner.run_async(
        user_id="u1",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part.from_text(text="Hi")]),
    ):
        events.append(event)

    assert len(events) > 0
    assert call_counts["coordinator"] == 1
    assert "pipeline" not in call_counts
    assert any(
        "Hello!" in getattr(part, "text", "")
        for event in events
        if event.content and event.content.parts
        for part in event.content.parts
    )


@pytest.mark.asyncio
async def test_save_visualizer_image_strips_binary_blob() -> None:
    """Verify that _save_visualizer_image saves the artifact, strips the
    multi-megabyte binary blob, and sets content=None so that intermediate
    debug text is not displayed in Gemini Enterprise."""
    saved_artifacts: dict[str, types.Part] = {}

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {_S_TURN_ID: "test_turn", _S_ROUND: 0}

        async def save_artifact(self, name: str, part: types.Part) -> None:
            saved_artifacts[name] = part

    cb_ctx = MockCallbackContext()
    # 5MB dummy raw image bytes
    raw_image_data = b"x" * (5 * 1024 * 1024)
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type="image/png",
                        data=raw_image_data,
                    )
                )
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )

    result = await _save_visualizer_image(cb_ctx, llm_resp)  # type: ignore[arg-type]

    # Artifact is saved
    assert "figure_test_turn_v0.png" in saved_artifacts
    assert saved_artifacts["figure_test_turn_v0.png"].inline_data is not None
    assert saved_artifacts["figure_test_turn_v0.png"].inline_data.data == raw_image_data
    # State is updated
    assert cb_ctx.state[_S_IMAGE_NAME] == "figure_test_turn_v0.png"
    assert cb_ctx.state[_S_ROUND] == 1
    # Model response event returned to the runner has content=None to silence GE chat bubbles
    assert result is not None
    assert result.content is None


@pytest.mark.asyncio
async def test_save_planner_output_silences_content() -> None:
    """Verify _save_planner_output stores description into state and silences content."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="Draft visual plan")],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    result = await _save_planner_output(cb_ctx, llm_resp)  # type: ignore[arg-type]
    assert cb_ctx.state.get(_S_DESCRIPTION) == "Draft visual plan"
    assert result is not None
    assert result.content is None


@pytest.mark.asyncio
async def test_save_stylist_output_silences_content() -> None:
    """Verify _save_stylist_output stores styled description into state and silences content."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="NeurIPS styled layout")],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    result = await _save_stylist_output(cb_ctx, llm_resp)  # type: ignore[arg-type]
    assert cb_ctx.state.get(_S_STYLED) == "NeurIPS styled layout"
    assert result is not None
    assert result.content is None


@pytest.mark.asyncio
async def test_save_critic_output_silences_content() -> None:
    """Verify _save_critic_output stores raw JSON verdict into state and silences content."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text='{"critic_suggestions": "looks good"}')],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    result = await _save_critic_output(cb_ctx, llm_resp)  # type: ignore[arg-type]
    assert cb_ctx.state.get(_S_VERDICT_RAW) == '{"critic_suggestions": "looks good"}'
    assert result is not None
    assert result.content is None


@pytest.mark.asyncio
async def test_strip_tool_preamble() -> None:
    """Verify _strip_tool_preamble strips conversational preambles from tool-calling turns."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()

    # Case 1: Response with tool call AND conversational preamble text
    resp_with_tool = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text="I have initiated the generation of a methodology diagram..."
                ),
                types.Part(
                    function_call=types.FunctionCall(
                        name="generate_figure", args={"intent": "overview"}
                    )
                ),
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    res = await _strip_tool_preamble(cb_ctx, resp_with_tool)  # type: ignore[arg-type]
    assert res is not None
    assert res.content is not None
    # Preamble text stripped, function_call preserved
    assert len(res.content.parts) == 1
    assert res.content.parts[0].function_call is not None
    assert getattr(res.content.parts[0], "text", None) is None

    # Case 2: Conversational response without tool calls
    resp_conversational = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="Please attach a research paper in PDF format.")],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    res_conv = await _strip_tool_preamble(cb_ctx, resp_conversational)  # type: ignore[arg-type]
    # Leaves conversational response untouched (returns None)
    assert res_conv is None


@pytest.mark.asyncio
async def test_save_planner_output_preserves_thoughts_and_filters_text() -> None:
    """Verify _save_planner_output keeps thought parts in content while saving only clean draft text in state."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Thinking about layout...", thought=True),
                types.Part(text="Draft visual plan description"),
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    result = await _save_planner_output(cb_ctx, llm_resp)  # type: ignore[arg-type]
    # State has ONLY the non-thought plan text
    assert cb_ctx.state.get(_S_DESCRIPTION) == "Draft visual plan description"
    # Result content has ONLY the thought part
    assert result is not None
    assert result.content is not None
    assert len(result.content.parts) == 1
    assert result.content.parts[0].thought is True
    assert result.content.parts[0].text == "Thinking about layout..."


@pytest.mark.asyncio
async def test_save_stylist_output_preserves_thoughts_and_filters_text() -> None:
    """Verify _save_stylist_output keeps thought parts in content while saving only clean styled text in state."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Thinking about NeurIPS palette...", thought=True),
                types.Part(text="NeurIPS styled layout specification"),
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    result = await _save_stylist_output(cb_ctx, llm_resp)  # type: ignore[arg-type]
    # State has ONLY the styled text
    assert cb_ctx.state.get(_S_STYLED) == "NeurIPS styled layout specification"
    # Result content has ONLY the thought part
    assert result is not None
    assert result.content is not None
    assert len(result.content.parts) == 1
    assert result.content.parts[0].thought is True
    assert result.content.parts[0].text == "Thinking about NeurIPS palette..."


@pytest.mark.asyncio
async def test_save_critic_output_preserves_thoughts_and_filters_text() -> None:
    """Verify _save_critic_output keeps thoughts in content and stores clean JSON verdict in state."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    verdict_json = json.dumps(
        {
            "critic_suggestions": "Adjust font size",
            "revised_description": "Updated layout",
        }
    )
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Critiquing figure quality...", thought=True),
                types.Part(text=verdict_json),
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    result = await _save_critic_output(cb_ctx, llm_resp)  # type: ignore[arg-type]
    # State has clean JSON that can be parsed
    raw_stored = cb_ctx.state.get(_S_VERDICT_RAW, "")
    parsed_ok, sugg, rev = _parse_critic_verdict(raw_stored)
    assert parsed_ok is True
    assert sugg == "Adjust font size"
    assert rev == "Updated layout"
    # Result content has ONLY the thought part
    assert result is not None
    assert result.content is not None
    assert len(result.content.parts) == 1
    assert result.content.parts[0].thought is True
    assert result.content.parts[0].text == "Critiquing figure quality..."


@pytest.mark.asyncio
async def test_strip_tool_preamble_preserves_thoughts_and_tools() -> None:
    """Verify _strip_tool_preamble preserves thought parts and function calls while stripping preamble text."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text="Thinking: User wants a methodology figure...", thought=True
                ),
                types.Part(
                    text="I have initiated the generation of a methodology diagram..."
                ),
                types.Part(
                    function_call=types.FunctionCall(
                        name="generate_figure", args={"intent": "overview"}
                    )
                ),
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    res = await _strip_tool_preamble(cb_ctx, resp)  # type: ignore[arg-type]
    assert res is not None
    assert res.content is not None
    # Preamble text stripped, thought and function_call preserved
    assert len(res.content.parts) == 2
    assert res.content.parts[0].thought is True
    assert res.content.parts[0].text == "Thinking: User wants a methodology figure..."
    assert res.content.parts[1].function_call is not None
    assert res.content.parts[1].function_call.name == "generate_figure"


def test_strip_procedural_preamble() -> None:
    """Verify _strip_procedural_preamble strips assistant prefixes while retaining multiline descriptions."""
    assert (
        _strip_procedural_preamble(
            "I have launched the generation of a publication-style overview diagram."
        )
        == "Overview diagram."
    )
    assert (
        _strip_procedural_preamble(
            "Generating an overview diagram with bullets:\n- Item 1\n- Item 2"
        )
        == "Overview diagram with bullets:\n- Item 1\n- Item 2"
    )
    assert _strip_procedural_preamble("") == ""
    assert (
        _strip_procedural_preamble("Custom architecture diagram")
        == "Custom architecture diagram"
    )


def test_clean_figure_title() -> None:
    """Verify _clean_figure_title strips preambles, truncates, and provides fallbacks."""
    # Common assistant preambles
    assert (
        _clean_figure_title(
            "I have launched the generation of a publication-style methodology overview diagram."
        )
        == "Methodology overview diagram."
    )
    assert (
        _clean_figure_title(
            "I have initiated the generation of an overview diagram of the pipeline."
        )
        == "Overview diagram of the pipeline."
    )
    assert (
        _clean_figure_title(
            "Generating a publication-style flow chart of drug discovery."
        )
        == "Flow chart of drug discovery."
    )
    assert (
        _clean_figure_title("Generating synthetic pathway diagram.")
        == "Synthetic pathway diagram."
    )

    # Empty and multiline
    assert _clean_figure_title("") == "Methodology overview diagram"
    assert (
        _clean_figure_title(
            "AI co-scientist pipeline\n\nPhase 1: Input\nPhase 2: Supervisor"
        )
        == "AI co-scientist pipeline"
    )

    # Full descriptive sentence (123 chars) is NOT truncated
    assert (
        _clean_figure_title(
            "A methodology overview diagram with a clear left-to-right flow "
            "illustrating the AI co-scientist multi-agent architecture."
        )
        == "A methodology overview diagram with a clear left-to-right flow "
        "illustrating the AI co-scientist multi-agent architecture."
    )

    # Preambles with trailing list introduction
    assert (
        _clean_figure_title(
            "I have initiated the generation of a methodology overview diagram "
            "illustrating the AI co-scientist multi-agent workflow in a clear "
            "left-to-right flow, encompassing:\n\n1. Scientist Input & Configuration"
        )
        == "Methodology overview diagram illustrating the AI co-scientist multi-agent "
        "workflow in a clear left-to-right flow."
    )

    # Very long title is preserved in full without length cutoff or ellipsis
    words = ["word"] * 80  # ~400 chars
    long_intent = " ".join(words)
    cleaned_long = _clean_figure_title(long_intent)
    assert cleaned_long == long_intent
    assert not cleaned_long.endswith("...")


def test_generate_figure_tool_cleans_intent_and_sets_launched() -> None:
    """Verify generate_figure cleans verbose intent and sets pipeline_launched state."""

    class MockActions:
        def __init__(self) -> None:
            self.route = None

    class MockToolContext:
        def __init__(self) -> None:
            self.actions = MockActions()
            self.state: dict[str, Any] = {}

    tool_ctx = MockToolContext()
    verbose_intent = "I have launched the generation of a publication-style AI co-scientist pipeline."
    result = generate_figure(intent=verbose_intent, tool_context=tool_ctx)  # ty: ignore[invalid-argument-type]

    assert result["status"] == "launched"
    assert result["intent"] == "AI co-scientist pipeline."
    assert tool_ctx.actions.route == "generate_figure"
    assert tool_ctx.state[_S_INTENT] == "AI co-scientist pipeline."
    assert tool_ctx.state[_S_LAUNCHED] is True


@pytest.mark.asyncio
async def test_save_visualizer_image_suppresses_intermediate_artifact_delta() -> None:
    """Verify _save_visualizer_image records version in state and pops artifact_delta so GE does not show intermediate cards."""

    class MockEventActions:
        def __init__(self) -> None:
            self.artifact_delta: dict[str, int] = {}

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {_S_TURN_ID: "turn42", _S_ROUND: 0}
            self.actions = MockEventActions()

        async def save_artifact(self, name: str, part: types.Part) -> int:
            self.actions.artifact_delta[name] = 1
            return 1

    cb_ctx = MockCallbackContext()
    resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type="image/png",
                        data=b"imagebytes",
                    )
                )
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    result = await _save_visualizer_image(cb_ctx, resp)  # type: ignore[arg-type]

    assert result is not None
    assert result.content is None
    assert cb_ctx.state[_S_IMAGE_NAME] == "figure_turn42_v0.png"
    assert cb_ctx.state[_S_IMAGE_VERSION] == 1
    # Crucial check: artifact_delta was popped so GE will not emit an intermediate image preview card
    assert "figure_turn42_v0.png" not in cb_ctx.actions.artifact_delta


def test_finalize_attaches_single_artifact_delta() -> None:
    """Verify finalize yields artifact_delta event first, followed by caption event."""
    ctx_with_image = MockContext(
        state={
            _S_IMAGE_NAME: "figure_turn42_v2.png",
            _S_IMAGE_VERSION: 3,
            _S_INTENT: "Final publication figure",
        }
    )
    events = list(finalize(ctx_with_image, "in"))  # ty: ignore[invalid-argument-type]
    assert len(events) == 2
    img_event, cap_event = events
    assert img_event.actions is not None
    assert img_event.actions.artifact_delta == {"figure_turn42_v2.png": 3}
    assert img_event.content is None
    assert "**Figure**: Final publication figure" in cap_event.output

    ctx_without_image = MockContext(state={})
    events_no_image = list(finalize(ctx_without_image, "in"))  # ty: ignore[invalid-argument-type]
    assert len(events_no_image) == 1
    assert events_no_image[0].actions.artifact_delta == {}


@pytest.mark.asyncio
async def test_strip_tool_preamble_silences_post_tool_confirmation() -> None:
    """Verify _strip_tool_preamble silences the coordinator's post-tool turn when pipeline was launched."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {_S_LAUNCHED: True}

    cb_ctx = MockCallbackContext()
    post_tool_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text="I have launched the generation of a publication-style methodology diagram..."
                )
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    res = await _strip_tool_preamble(cb_ctx, post_tool_resp)  # type: ignore[arg-type]

    # Post-tool response must be completely silenced so finalize emits the only user message
    assert res is not None
    assert res.content is None
    # _S_LAUNCHED must be consumed and cleared
    assert not cb_ctx.state.get(_S_LAUNCHED)


def test_format_figure_caption() -> None:
    """Verify _format_figure_caption standardizes caption format and strips code fences."""
    # Already formatted with **Figure**:
    assert (
        _format_figure_caption(
            "**Figure**: Overview of the AI co-scientist system architecture."
        )
        == "**Figure**: Overview of the AI co-scientist system architecture."
    )
    # Already formatted with **Figure 1**:
    assert (
        _format_figure_caption(
            "**Figure 1**: System architecture and multi-agent workflow."
        )
        == "**Figure 1**: System architecture and multi-agent workflow."
    )
    # Formatted as Figure: without bold
    assert (
        _format_figure_caption(
            "Figure: System architecture showing left-to-right flow."
        )
        == "**Figure**: System architecture showing left-to-right flow."
    )
    # Formatted as Figure 2: without bold (preserves number and bolds prefix)
    assert (
        _format_figure_caption(
            "Figure 2: System architecture showing left-to-right flow."
        )
        == "**Figure 2**: System architecture showing left-to-right flow."
    )
    # Lowercase **figure**: normalized to canonical title case
    assert (
        _format_figure_caption("**figure**: System architecture overview.")
        == "**Figure**: System architecture overview."
    )
    # Lowercase **figure 3**: normalized to canonical title case
    assert (
        _format_figure_caption("**figure 3**: Detailed block diagram.")
        == "**Figure 3**: Detailed block diagram."
    )
    # Raw description with no figure prefix
    assert (
        _format_figure_caption("A multi-agent workflow for hypothesis generation.")
        == "**Figure**: A multi-agent workflow for hypothesis generation."
    )
    # Wrapped in markdown code fences
    assert (
        _format_figure_caption(
            "```markdown\n**Figure**: Diagram of experimental workflow.\n```"
        )
        == "**Figure**: Diagram of experimental workflow."
    )
    # Empty string
    assert _format_figure_caption("") == ""


@pytest.mark.asyncio
async def test_save_captioner_output_silences_content_and_saves_caption() -> None:
    """Verify _save_captioner_output stores formatted caption in state and silences text content."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text="Overview of the AI co-scientist system architecture with three panels."
                )
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )
    res = await _save_captioner_output(cb_ctx, llm_resp)  # type: ignore[arg-type]

    assert res is not None
    assert res.content is None  # User-visible text silenced so finalize emits it
    assert (
        cb_ctx.state[_S_CAPTION]
        == "**Figure**: Overview of the AI co-scientist system architecture with three panels."
    )


@pytest.mark.asyncio
async def test_save_captioner_output_preserves_thoughts() -> None:
    """Verify _save_captioner_output preserves thought parts when thoughts are enabled."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

    cb_ctx = MockCallbackContext()
    thought_part = types.Part(text="Analyzing image layout...", thought=True)
    text_part = types.Part(text="**Figure**: Flowchart of the drug discovery cycle.")
    llm_resp = LlmResponse(
        content=types.Content(role="model", parts=[thought_part, text_part]),
        finish_reason=types.FinishReason.STOP,
    )
    res = await _save_captioner_output(cb_ctx, llm_resp)  # type: ignore[arg-type]

    assert res is not None
    assert res.content is not None
    assert len(res.content.parts) == 1
    assert getattr(res.content.parts[0], "thought", False) is True
    assert res.content.parts[0].text == "Analyzing image layout..."
    assert (
        cb_ctx.state[_S_CAPTION] == "**Figure**: Flowchart of the drug discovery cycle."
    )


@pytest.mark.asyncio
async def test_build_captioner_request() -> None:
    """Verify _build_captioner_request constructs prompt with image and intent."""
    from google.adk.models.llm_request import LlmRequest

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {
                _S_INTENT: "AI co-scientist pipeline",
                _S_IMAGE_NAME: "figure_test_v1.png",
            }

        async def list_artifacts(self) -> list[str]:
            return ["figure_test_v1.png"]

        async def load_artifact(self, name: str) -> types.Part | None:
            if name == "figure_test_v1.png":
                return types.Part(
                    inline_data=types.Blob(mime_type="image/png", data=b"fake_bytes")
                )
            return None

    cb_ctx = MockCallbackContext()
    llm_req = LlmRequest()
    await _build_captioner_request(cb_ctx, llm_req)  # type: ignore[arg-type]

    assert llm_req.contents is not None
    parts = llm_req.contents[0].parts
    # Should include image part and prompt text part
    assert any(getattr(p, "inline_data", None) for p in parts)
    text_part = next(p for p in parts if getattr(p, "text", None))
    assert "Target Diagram for Captioning" in text_part.text
    assert "AI co-scientist pipeline" in text_part.text
    assert "publication-grade figure caption" in text_part.text


def test_prep_inputs_with_null_input() -> None:
    """Verify prep_inputs handles None node_input, preserving existing intent or defaulting."""
    ctx_with_prior = MockContext(state={_S_INTENT: "Prior detailed intent"})
    res1 = prep_inputs(ctx_with_prior, None)  # ty: ignore[invalid-argument-type]
    assert res1 is None
    assert ctx_with_prior.state.get(_S_INTENT) == "Prior detailed intent"

    ctx_empty = MockContext(state={})
    res2 = prep_inputs(ctx_empty, None)  # ty: ignore[invalid-argument-type]
    assert res2 is None
    assert ctx_empty.state.get(_S_INTENT) == "A clear methodology overview diagram."


def test_prep_inputs_preserves_multiline_intent() -> None:
    """Verify prep_inputs strips procedural preambles but preserves multiline content and bullets."""
    ctx = MockContext(state={})
    multiline = (
        "I have launched the generation of a publication-style methodology diagram:\n"
        "1. Input data ingestion\n"
        "2. Transformer feature processing\n"
        "3. Output classification"
    )
    prep_inputs(ctx, multiline)  # ty: ignore[invalid-argument-type]
    expected = (
        "Methodology diagram:\n"
        "1. Input data ingestion\n"
        "2. Transformer feature processing\n"
        "3. Output classification"
    )
    assert ctx.state.get(_S_INTENT) == expected


def test_generate_figure_preserves_multiline_intent() -> None:
    """Verify generate_figure preserves multiline details in state while returning concise title."""

    class MockActions:
        def __init__(self) -> None:
            self.route = None

    class MockToolContext:
        def __init__(self) -> None:
            self.actions = MockActions()
            self.state: dict[str, Any] = {}

    tool_ctx = MockToolContext()
    multiline_intent = (
        "Generating a publication-style methodology diagram illustrating the workflow:\n"
        "* Sensor input module\n"
        "* Core reasoning graph\n"
        "* Action execution layer"
    )
    result = generate_figure(intent=multiline_intent, tool_context=tool_ctx)  # ty: ignore[invalid-argument-type]

    # Returned dict for UI has concise single-line title
    assert result["status"] == "launched"
    assert result["intent"] == "Methodology diagram illustrating the workflow."
    assert "Sensor input module" not in result["intent"]

    # State retains full multiline specifications for downstream agents
    assert "* Sensor input module" in tool_ctx.state[_S_INTENT]
    assert "* Action execution layer" in tool_ctx.state[_S_INTENT]
    assert tool_ctx.state[_S_INTENT].startswith(
        "Methodology diagram illustrating the workflow:\n"
    )


@pytest.mark.asyncio
async def test_save_visualizer_image_handles_artifact_save_failure() -> None:
    """Verify _save_visualizer_image handles save_artifact exceptions cleanly without corrupting state."""

    class FailingCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {_S_TURN_ID: "turn_err", _S_ROUND: 0}

        async def save_artifact(self, name: str, part: types.Part) -> int:
            raise RuntimeError("Storage quota exceeded")

    cb_ctx = FailingCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type="image/png",
                        data=b"imagebytes",
                    )
                )
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )

    result = await _save_visualizer_image(cb_ctx, llm_resp)  # type: ignore[arg-type]

    assert result is not None
    assert result.content is None
    # State must not contain corrupted image name or version
    assert _S_IMAGE_NAME not in cb_ctx.state
    assert _S_IMAGE_VERSION not in cb_ctx.state
    # Round is advanced
    assert cb_ctx.state[_S_ROUND] == 1


@pytest.mark.asyncio
async def test_save_visualizer_image_clean_mime_type_extension() -> None:
    """Verify _save_visualizer_image strips MIME parameters (e.g. charset) when determining extension."""
    saved_names = []

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {_S_TURN_ID: "turn_mime", _S_ROUND: 0}

        async def save_artifact(self, name: str, part: types.Part) -> int:
            saved_names.append(name)
            assert part.inline_data is not None
            assert part.inline_data.mime_type == "image/png"
            return 1

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type="image/png; charset=utf-8",
                        data=b"imagebytes",
                    )
                )
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )

    result = await _save_visualizer_image(cb_ctx, llm_resp)  # type: ignore[arg-type]
    assert result is not None
    assert saved_names == ["figure_turn_mime_v0.png"]
    assert cb_ctx.state[_S_IMAGE_NAME] == "figure_turn_mime_v0.png"


@pytest.mark.asyncio
async def test_simulated_pipeline_visualizer_failure_routes_to_finalize_direct() -> (
    None
):
    """Verify that when visualizer fails to produce an image, captioner is bypassed and finalize is reached directly."""
    call_counts = {}

    def track(name: str) -> None:
        call_counts[name] = call_counts.get(name, 0) + 1

    def mock_prep(ctx: Context, node_input: Any) -> Any:
        track("prep")
        return prep_inputs(ctx, node_input)

    async def mock_planner(ctx: Context, node_input: Any) -> Any:
        track("planner")
        ctx.state["description"] = "Mock draft description"
        return ctx.state["description"]

    async def mock_stylist(ctx: Context, node_input: Any) -> Any:
        track("stylist")
        ctx.state["styled_description"] = "Mock styled description"
        return ctx.state["styled_description"]

    async def mock_visualizer(ctx: Context, node_input: Any) -> Any:
        track("visualizer")
        # Visualizer fails to save or set image_name
        return None

    async def mock_critic(ctx: Context, node_input: Any) -> Any:
        track("critic")
        return None

    def mock_decide(ctx: Context, node_input: Any) -> Event:
        track("decide")
        return decide_refinement_loop(ctx, node_input)

    async def mock_captioner(ctx: Context, node_input: Any) -> Any:
        track("captioner")
        return "Should not run"

    def mock_finalize(ctx: Context, node_input: Any) -> Any:
        track("finalize")
        yield from finalize(ctx, node_input)

    edges = [
        (START, mock_prep),
        (mock_prep, mock_planner),
        (mock_planner, mock_stylist),
        (mock_stylist, mock_visualizer),
        (mock_visualizer, mock_critic),
        (mock_critic, mock_decide),
        (
            mock_decide,
            {
                "refine": mock_visualizer,
                "finalize": mock_captioner,
                "finalize_direct": mock_finalize,
            },
        ),
        (mock_captioner, mock_finalize),
    ]

    sim_wf = Workflow(name="sim_pipeline_fail", edges=edges)
    sim_app = App(name="sim_app_fail", root_agent=sim_wf)
    runner = InMemoryRunner(app=sim_app)

    session = await runner.session_service.create_session(
        app_name="sim_app_fail", user_id="user1"
    )
    events = []
    async for event in runner.run_async(
        user_id="user1",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text="Generate figure")]
        ),
    ):
        events.append(event)

    assert len(events) > 0
    assert call_counts["prep"] == 1
    assert call_counts["planner"] == 1
    assert call_counts["stylist"] == 1
    assert call_counts["visualizer"] == 1
    assert call_counts["critic"] == 1
    assert call_counts["decide"] == 1
    assert "captioner" not in call_counts  # Captioner bypassed!
    assert call_counts["finalize"] == 1
    final_event = events[-1]
    assert "unable to render a figure" in (final_event.output or "")


@pytest.mark.asyncio
async def test_save_visualizer_image_handles_missing_image_in_refinement_round() -> (
    None
):
    """Verify _save_visualizer_image advances round, clears image state, and silences refusal text when visualizer produces no image."""

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {
                _S_TURN_ID: "turn_ref_fail",
                _S_ROUND: 1,
                _S_IMAGE_NAME: "figure_turn_ref_fail_v0.png",
                _S_IMAGE_VERSION: 1,
            }

        async def save_artifact(self, name: str, part: types.Part) -> int:
            return 2

    cb_ctx = MockCallbackContext()
    # Model returns text refusal instead of image
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="I cannot render this diagram due to safety policy.")
            ],
        ),
        finish_reason=types.FinishReason.SAFETY,
    )

    result = await _save_visualizer_image(cb_ctx, llm_resp)  # type: ignore[arg-type]

    assert result is not None
    assert result.content is None  # Refusal text must be silenced
    assert cb_ctx.state[_S_ROUND] == 2  # Round must advance to prevent infinite loop
    assert _S_IMAGE_NAME not in cb_ctx.state  # Prior round image name cleared
    assert _S_IMAGE_VERSION not in cb_ctx.state

    # Verify decide_refinement_loop routes directly to finalize_direct
    event = decide_refinement_loop(cb_ctx, "in")  # type: ignore[arg-type]
    assert event.actions.route == "finalize_direct"


def test_prep_inputs_preserves_detailed_intent_from_generate_figure() -> None:
    """Verify prep_inputs does not overwrite detailed multiline intent in state with truncated tool status dict."""
    detailed_intent = (
        "Methodology diagram illustrating the workflow:\n"
        "* Sensor input module\n"
        "* Core reasoning graph\n"
        "* Action execution layer"
    )
    ctx = MockContext(state={_S_INTENT: detailed_intent})
    truncated_tool_return = {
        "status": "launched",
        "intent": "Methodology diagram illustrating the workflow.",
        "message": "Generating publication-style figure: Methodology diagram illustrating the workflow.",
    }

    prep_inputs(ctx, truncated_tool_return)  # ty: ignore[invalid-argument-type]

    # Full multiline intent in state must be preserved, not overwritten by truncated clean_title
    assert ctx.state[_S_INTENT] == detailed_intent


def test_format_figure_caption_edge_cases() -> None:
    """Verify _format_figure_caption handles fully bolded strings and period delimiters."""
    # Entire string wrapped in bold
    assert (
        _format_figure_caption("**Figure 1: Overview of the pipeline.**")
        == "**Figure 1**: Overview of the pipeline."
    )
    # Period delimiter after figure number
    assert (
        _format_figure_caption(
            "Figure 2. System architecture showing left-to-right flow."
        )
        == "**Figure 2**: System architecture showing left-to-right flow."
    )


@pytest.mark.asyncio
async def test_save_visualizer_image_sanitizes_unknown_mime_extension() -> None:
    """Verify _save_visualizer_image falls back to .png for unknown image MIME subtypes."""
    saved_names = []

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {_S_TURN_ID: "turn_unknown_mime", _S_ROUND: 0}

        async def save_artifact(self, name: str, part: types.Part) -> int:
            saved_names.append(name)
            return 1

    cb_ctx = MockCallbackContext()
    llm_resp = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type="image/svg+xml",
                        data=b"svgbytes",
                    )
                )
            ],
        ),
        finish_reason=types.FinishReason.STOP,
    )

    result = await _save_visualizer_image(cb_ctx, llm_resp)  # type: ignore[arg-type]
    assert result is not None
    assert saved_names == ["figure_turn_unknown_mime_v0.png"]
    assert cb_ctx.state[_S_IMAGE_NAME] == "figure_turn_unknown_mime_v0.png"


@pytest.mark.asyncio
async def test_end_to_end_paperbanana_pipeline_with_mock_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test real paperbanana_pipeline agents wired together using mock model generators."""

    async def mock_generate_content_async(
        self: Any, llm_request: Any, stream: bool = False
    ) -> Any:
        # Inspect system instruction or request content to return appropriate mock response
        sys_inst = ""
        if llm_request.config and llm_request.config.system_instruction:
            sys_inst = str(llm_request.config.system_instruction)

        if "Target Diagram for Critique:" in str(llm_request.contents):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=json.dumps(
                                {
                                    "critic_suggestions": "No changes needed",
                                    "revised_description": "No changes needed",
                                }
                            )
                        )
                    ],
                )
            )
        elif "Target Diagram for Captioning" in str(llm_request.contents):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text="**Figure**: Publication-grade overview diagram of the methodology."
                        )
                    ],
                )
            )
        elif "Render the following diagram." in str(llm_request.contents):
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="image/png",
                                data=b"fake_rendered_image_bytes",
                            )
                        )
                    ],
                )
            )
        elif "Detailed Description (from planner)" in sys_inst:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="NeurIPS styled diagram specification.")],
                )
            )
        else:
            # Planner agent
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Initial draft diagram plan.")],
                )
            )

    monkeypatch.setattr(Gemini, "generate_content_async", mock_generate_content_async)

    test_app = App(name="e2e_test_app", root_agent=paperbanana_pipeline)
    runner = InMemoryRunner(app=test_app)
    session = await runner.session_service.create_session(
        app_name="e2e_test_app", user_id="user_e2e"
    )

    events = []
    async for event in runner.run_async(
        user_id="user_e2e",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[
                types.Part.from_text(text="Architecture diagram with three stages.")
            ],
        ),
    ):
        events.append(event)

    assert len(events) >= 2
    image_event = events[-2]
    assert image_event.actions is not None
    assert len(image_event.actions.artifact_delta) == 1
    assert image_event.content is None

    final_event = events[-1]
    assert (
        final_event.output
        == "**Figure**: Publication-grade overview diagram of the methodology."
    )


@pytest.mark.asyncio
async def test_build_critic_request_skips_when_no_image() -> None:
    """Verify _build_critic_request returns LlmResponse(content=None) to skip model call if no image exists."""
    from google.adk.models.llm_request import LlmRequest

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {_S_INTENT: "Test Intent"}

        async def load_artifact(self, name: str) -> types.Part | None:
            return None

    cb_ctx = MockCallbackContext()
    llm_req = LlmRequest()
    result = await _build_critic_request(cb_ctx, llm_req)  # type: ignore[arg-type]

    assert result is not None
    assert result.content is None
    assert result.finish_reason == types.FinishReason.STOP


def test_finalize_falls_back_to_last_valid_image() -> None:
    """Verify finalize falls back to _S_LAST_VALID_IMAGE if current image was cleared on failed refinement."""
    ctx = MockContext(
        state={
            _S_LAST_VALID_IMAGE: "figure_turn1_v0.png",
            _S_LAST_VALID_IMAGE_VERSION: 1,
            _S_CAPTION: "**Figure**: Prior valid diagram.",
        }
    )
    events = list(finalize(ctx, "in"))  # ty: ignore[invalid-argument-type]
    assert len(events) == 2
    img_event, cap_event = events
    assert img_event.actions.artifact_delta == {"figure_turn1_v0.png": 1}
    assert cap_event.output == "**Figure**: Prior valid diagram."


def test_format_figure_caption_preserves_trailing_markdown() -> None:
    """Verify _format_figure_caption does not strip closing asterisks from bold/italic markdown."""
    assert (
        _format_figure_caption("Figure 1: Comparison against the **baseline**")
        == "**Figure 1**: Comparison against the **baseline**"
    )
    assert (
        _format_figure_caption("Figure 1: Activity measured in *vivo*.")
        == "**Figure 1**: Activity measured in *vivo*."
    )
    assert (
        _format_figure_caption("**Figure 1: Overview of the **pipeline****")
        == "**Figure 1**: Overview of the **pipeline**"
    )


@pytest.mark.asyncio
async def test_load_paper_part_filters_pdf_extension_candidates() -> None:
    """Verify _load_paper_part prioritizes candidates ending with .pdf and doesn't load image binaries."""
    loaded = []

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

        async def list_artifacts(self) -> list[str]:
            return ["figure_turn1_v0.png", "figure_turn1_v1.png", "paper_upload.pdf"]

        async def load_artifact(self, name: str) -> types.Part | None:
            loaded.append(name)
            if name == "paper_upload.pdf":
                return types.Part(
                    inline_data=types.Blob(
                        mime_type="application/pdf; charset=binary",
                        data=b"pdfdata",
                    )
                )
            return types.Part(
                inline_data=types.Blob(
                    mime_type="image/png",
                    data=b"pngdata",
                )
            )

    cb_ctx = MockCallbackContext()
    part = await _load_paper_part(cb_ctx)  # type: ignore[arg-type]

    assert part is not None
    # Only paper_upload.pdf should have been loaded, skipping png candidates
    assert loaded == ["paper_upload.pdf"]
    assert cb_ctx.state.get("paper_artifact_name") == "paper_upload.pdf"


def test_clean_figure_title_preserves_leading_numbers() -> None:
    """Verify _clean_figure_title preserves leading numbers/digits in the title."""
    assert (
        _clean_figure_title("3D reconstruction pipeline")
        == "3D reconstruction pipeline"
    )
    assert (
        _clean_figure_title("1984 George Orwell multi-agent model")
        == "1984 George Orwell multi-agent model"
    )
    assert (
        _clean_figure_title("- 3D reconstruction pipeline")
        == "3D reconstruction pipeline"
    )
    assert (
        _clean_figure_title("1. 3D reconstruction pipeline")
        == "3D reconstruction pipeline"
    )


def test_extract_intent_handles_scalar_json_and_parts() -> None:
    """Verify _extract_intent handles quoted scalar JSON and falls back to raw text."""

    class MockPart:
        def __init__(self, text: str) -> None:
            self.text = text

    class MockContent:
        def __init__(self, parts: list[Any]) -> None:
            self.parts = parts

    # Quoted string scalar JSON
    content_quoted = MockContent([MockPart('"Methodology overview"')])
    assert _extract_intent(content_quoted) == '"Methodology overview"'

    # Dict JSON
    content_dict = MockContent([MockPart('{"intent": "Overview architecture"}')])
    assert _extract_intent(content_dict) == "Overview architecture"

    # Raw string
    content_raw = MockContent([MockPart("Raw methodology prompt")])
    assert _extract_intent(content_raw) == "Raw methodology prompt"


@pytest.mark.asyncio
async def test_inject_uploaded_artifacts_handles_pdf_with_mime_params() -> None:
    """Verify _inject_uploaded_artifacts sets _S_PAPER_NAME for PDFs with parameterized MIME types."""
    from google.adk.models.llm_request import LlmRequest

    class MockCallbackContext:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {}

        async def list_artifacts(self) -> list[str]:
            return ["my_paper.pdf"]

        async def load_artifact(self, name: str) -> types.Part | None:
            if name == "my_paper.pdf":
                return types.Part(
                    inline_data=types.Blob(
                        mime_type="application/pdf; charset=binary",
                        data=b"pdfbytes",
                    )
                )
            return None

    cb_ctx = MockCallbackContext()
    llm_req = LlmRequest(
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        text="Here is the paper: <start_of_user_uploaded_file: my_paper.pdf>"
                    )
                ],
            )
        ]
    )
    await _inject_uploaded_artifacts(cb_ctx, llm_req)  # type: ignore[arg-type]

    assert cb_ctx.state.get("paper_artifact_name") == "my_paper.pdf"
    assert len(llm_req.contents[0].parts) == 2
    assert llm_req.contents[0].parts[1].inline_data is not None
