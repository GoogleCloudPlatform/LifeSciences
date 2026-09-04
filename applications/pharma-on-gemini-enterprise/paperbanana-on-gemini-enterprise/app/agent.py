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

"""PaperBanana on Gemini Enterprise (lite).

A Google ADK agent that brings a lite version of PaperBanana
(https://github.com/dwzhu-pku/PaperBanana, Apache-2.0) to Gemini Enterprise.
The user attaches a research paper in the Gemini Enterprise composer and asks
for a figure; the pipeline plans -> stylizes -> renders -> critiques -> refines
and returns a publication-style diagram.

Pipeline shape (ADK v2 Workflow DAGs):

    root_agent "paperbanana" (Workflow DAG)
      │
      ├── (START -> coordinator_agent)  [Conversational Coordinator]
      │     - injects attached paper PDF into context
      │     - answers greetings/clarifications conversationally
      │     - routing tool: generate_figure (sets route="generate_figure")
      │
      └── (coordinator_agent -> paperbanana_pipeline)  [Pipeline Workflow DAG]
            ├── (START -> prep_inputs): mints turn_id, snapshots prior image, resets round state
            ├── (prep_inputs -> planner_agent): drafts detailed visual description
            ├── (planner_agent -> stylist_agent): refines with NeurIPS aesthetic guidance
            ├── (stylist_agent -> visualizer_agent): renders diagram (gemini-3-pro-image)
            ├── (visualizer_agent -> critic_agent): critiques diagram, emits JSON verdict
            ├── (critic_agent -> decide_refinement_loop):
            │     ├── route="refine" ──► visualizer_agent (up to _MAX_CRITIC_ROUNDS)
            │     ├── route="finalize" ──► captioner_agent
            │     └── route="finalize_direct" ──► finalize (if no image rendered)
            ├── (captioner_agent -> finalize): inspects rendered diagram and drafts publication caption
            └── finalize: emits clean Figure caption and attaches final image artifact_delta
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from google.adk.agents import Agent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.context import Context
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.planners import BuiltInPlanner
from google.adk.tools import ToolContext
from google.adk.workflow import START, Workflow
from google.genai import types

from .prompts import (
    CAPTIONER_SYSTEM_PROMPT,
    CRITIC_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    STYLIST_SYSTEM_PROMPT_TEMPLATE,
    VISUALIZER_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Gemini 3.x is only served from the `global` endpoint; Agent Engine itself
# still deploys to a regional endpoint (us-central1) — same pattern the
# model_garden_agent uses for Claude in us-east5.
os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("MODEL_LOCATION", "global")

_MAX_CRITIC_ROUNDS = int(os.getenv("MAX_CRITIC_ROUNDS", "3"))
# Nano Banana Pro (gemini-3-pro-image) supports 1K, 2K, 4K.
# Default to 4K for publication-quality output; drop to 2K/1K for faster turns.
_IMAGE_SIZE = os.getenv("IMAGE_SIZE", "4K")

# Retry configuration for model calls to handle transient 429 RESOURCE_EXHAUSTED
# and 5xx server errors across both agent and genai client layers.
_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=int(os.getenv("MODEL_RETRY_ATTEMPTS", "5")),
    initial_delay=float(os.getenv("MODEL_RETRY_INITIAL_DELAY", "1.0")),
    max_delay=float(os.getenv("MODEL_RETRY_MAX_DELAY", "60.0")),
    exp_base=float(os.getenv("MODEL_RETRY_EXP_BASE", "2.0")),
    jitter=float(os.getenv("MODEL_RETRY_JITTER", "1.0")),
    http_status_codes=[408, 429, 500, 502, 503, 504],
)
_HTTP_OPTIONS = types.HttpOptions(retry_options=_RETRY_OPTIONS)

_PLANNER_MODEL = Gemini(
    model=os.getenv("PLANNER_MODEL_NAME", "gemini-3.8-flash"),
    retry_options=_RETRY_OPTIONS,
)
_IMAGE_MODEL = Gemini(
    model=os.getenv("IMAGE_MODEL_NAME", "gemini-3-pro-image"),
    retry_options=_RETRY_OPTIONS,
)

# Surface the model's reasoning as thought summaries so they show up in `adk web`
# (and in Agent Engine traces). Off by default in the API; include_thoughts=True
# asks Gemini to return a chain of thought alongside the answer.
# Toggle with SHOW_THOUGHTS=0.
_SHOW_THOUGHTS = os.environ.get("SHOW_THOUGHTS", "1") not in ("0", "false", "")
THINKING_PLANNER = (
    BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True))
    if _SHOW_THOUGHTS
    else None
)

_STYLE_GUIDE_PATH = Path(__file__).parent / "style_guide.md"
_STYLE_GUIDE = _STYLE_GUIDE_PATH.read_text(encoding="utf-8")

# Session-state keys used to pass data between pipeline steps.
_S_INTENT = "intent"
_S_PAPER_NAME = "paper_artifact_name"
_S_DESCRIPTION = "description"
_S_STYLED = "styled_description"
_S_IMAGE_NAME = "current_image_name"
_S_IMAGE_VERSION = "current_image_version"
_S_ROUND = "current_round"
_S_VERDICT_RAW = "critic_verdict_raw"
_S_TURN_ID = "turn_id"
_S_PREV_TURN_IMAGE = "previous_turn_image"
_S_LAUNCHED = "pipeline_launched"
_S_CAPTION = "figure_caption"
_S_LAST_VALID_IMAGE = "last_valid_image"
_S_LAST_VALID_IMAGE_VERSION = "last_valid_image_version"

# Same marker convention Gemini Enterprise uses to signal an attached file.
_FILE_MARKER_RE = re.compile(r"<start_of_user_uploaded_file:\s*(?P<name>[^>]+?)\s*>")
_GEMINI_INLINE_MIMES = ("image/", "application/pdf")


# ---------------------------------------------------------------------------
# Root-agent callback: re-attach GE-uploaded files.
#
# Gemini Enterprise strips the bytes from user-attached files and only forwards
# filename markers (`<start_of_user_uploaded_file: NAME>`) in the user message;
# the actual blobs live in the ArtifactService. This callback resolves the
# markers back into inline_data Parts so the model can read them. Lifted
# verbatim from the model_garden_agent.
# ---------------------------------------------------------------------------


def _is_inlineable(mime: str | None) -> bool:
    return bool(mime) and any(mime.startswith(p) for p in _GEMINI_INLINE_MIMES)


async def _inject_uploaded_artifacts(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    if not llm_request.contents:
        return None
    artifact_keys = set(await callback_context.list_artifacts())

    for content in llm_request.contents:
        if getattr(content, "role", None) != "user" or not content.parts:
            continue
        injected: set[str] = set()
        text_parts = [p for p in content.parts if p.text]
        for part in text_parts:
            if part.text is None:
                continue
            for match in _FILE_MARKER_RE.finditer(part.text):
                name = match.group("name").strip()
                if name in injected or name not in artifact_keys:
                    continue
                artifact = await callback_context.load_artifact(name)
                if artifact is None or artifact.inline_data is None:
                    continue
                if not _is_inlineable(artifact.inline_data.mime_type):
                    continue
                content.parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=artifact.inline_data.mime_type,
                            data=artifact.inline_data.data,
                        )
                    )
                )
                # Remember the most recent paper PDF so the pipeline can find it.
                if (
                    (artifact.inline_data.mime_type or "")
                    .lower()
                    .startswith("application/pdf")
                ):
                    callback_context.state[_S_PAPER_NAME] = name
                injected.add(name)
    return None


# ---------------------------------------------------------------------------
# Helpers shared by the visualizer / critic to read the active paper PDF and
# the most recent rendered image as inline_data Parts.
# ---------------------------------------------------------------------------


async def _load_paper_part(
    callback_context: CallbackContext,
) -> types.Part | None:
    name = callback_context.state.get(_S_PAPER_NAME)
    if not name:
        # Fallback: pick the first PDF we can find among the session's artifacts.
        candidates = await callback_context.list_artifacts()
        pdf_candidates = [c for c in candidates if c.lower().endswith(".pdf")]
        for candidate in pdf_candidates or candidates:
            part = await callback_context.load_artifact(candidate)
            if (
                part
                and part.inline_data
                and (part.inline_data.mime_type or "")
                .lower()
                .startswith("application/pdf")
            ):
                callback_context.state[_S_PAPER_NAME] = candidate
                return part
        return None
    return await callback_context.load_artifact(name)


async def _load_image_part(
    callback_context: CallbackContext,
) -> types.Part | None:
    name = callback_context.state.get(_S_IMAGE_NAME)
    if not name:
        return None
    return await callback_context.load_artifact(name)


# ---------------------------------------------------------------------------
# Step 1: Stage pipeline inputs into state.
# ---------------------------------------------------------------------------


def _extract_intent(node_input: Any) -> str:
    """Extracts intent string from node_input or user content."""
    if isinstance(node_input, str):
        try:
            parsed = json.loads(node_input)
            if isinstance(parsed, dict) and "intent" in parsed:
                return str(parsed["intent"]).strip()
        except json.JSONDecodeError:
            pass
        return node_input.strip()
    if isinstance(node_input, dict) and "intent" in node_input:
        return str(node_input["intent"]).strip()
    if hasattr(node_input, "parts"):
        for part in node_input.parts:
            text = getattr(part, "text", None)
            if not text:
                continue
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "intent" in parsed:
                    return str(parsed["intent"]).strip()
            except json.JSONDecodeError:
                pass
            return text.strip()
    return ""


_PROCEDURAL_PREFIXES = (
    "I have launched the generation of a publication-style ",
    "I have launched the generation of an ",
    "I have launched the generation of a ",
    "I have launched the generation of ",
    "I have initiated the generation of a publication-style ",
    "I have initiated the generation of an ",
    "I have initiated the generation of a ",
    "I have initiated the generation of ",
    "Generating a publication-style ",
    "Generating an ",
    "Generating a ",
    "Generating ",
    "Generation of a ",
    "Generation of an ",
    "Generation of ",
    "Here is a publication-style ",
    "Here is an ",
    "Here is a ",
    "Here is ",
)


def _strip_procedural_preamble(text: str) -> str:
    """Strips common assistant preambles (e.g. 'Generating a...', 'I have initiated...')
    while preserving the full multiline description and bullet points."""
    cleaned = (text or "").strip()
    for prefix in _PROCEDURAL_PREFIXES:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix) :].strip()
            if cleaned:
                cleaned = cleaned[0].upper() + cleaned[1:]
            break
    return cleaned


def _clean_figure_title(raw_intent: str) -> str:
    """Extract a concise title or caption from intent, stripping procedural preambles."""
    cleaned = _strip_procedural_preamble(raw_intent)
    if not cleaned:
        return "Methodology overview diagram"
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return "Methodology overview diagram"

    # Find the lead descriptive line before bullet lists or headers
    lead_line = ""
    for line in lines:
        if line.startswith(("#", "-", "*")) or re.match(r"^\d+[\.\)]\s+", line):
            continue
        lead_line = line
        break
    if not lead_line:
        lead_line = re.sub(r"^(?:[#\-\*]+|\d+[\.\)])\s*", "", lines[0]).strip()

    lead_line = _strip_procedural_preamble(lead_line)

    has_trailing_colon = bool(
        re.search(
            r"[,;]?\s*(encompassing|including|depicting the following|depicting|as follows)\s*:?$|:\s*$",
            lead_line,
            flags=re.IGNORECASE,
        )
    )
    lead_line = re.sub(
        r"[,;]?\s*(encompassing|including|depicting the following|depicting|as follows)\s*:?$",
        "",
        lead_line,
        flags=re.IGNORECASE,
    ).rstrip(" :;,")
    if has_trailing_colon and lead_line and not lead_line.endswith((".", "!", "?")):
        lead_line += "."

    return lead_line or "Methodology overview diagram"


def _pop_state_key(state: Any, key: str) -> None:
    """Safely removes a key from session state whether it is a dict or custom mapping."""
    if hasattr(state, "pop"):
        state.pop(key, None)
    else:
        state[key] = None


def prep_inputs(ctx: Context, node_input: Any) -> Any:
    """Stages per-turn pipeline inputs into session state.

    Specifically:
      * snapshots the previous turn's final image into _S_PREV_TURN_IMAGE so
        the Visualizer's first round can use it as edit input,
      * mints a per-turn ID so saved-image filenames don't collide across turns,
      * ensures _S_INTENT is populated (from existing state or node_input),
      * resets per-turn intermediate state and round counter.
    """
    state = ctx.state

    prev_image = state.get(_S_IMAGE_NAME)
    if prev_image:
        state[_S_PREV_TURN_IMAGE] = prev_image

    state[_S_TURN_ID] = uuid.uuid4().hex[:8]
    if not state.get(_S_INTENT):
        extracted_intent = _extract_intent(node_input)
        state[_S_INTENT] = (
            _strip_procedural_preamble(extracted_intent)
            if extracted_intent
            else "A clear methodology overview diagram."
        )
    elif extracted_intent := _extract_intent(node_input):
        # Only override existing state intent if node_input is not a truncated tool status dict
        if not (
            isinstance(node_input, dict) and node_input.get("status") == "launched"
        ):
            state[_S_INTENT] = _strip_procedural_preamble(extracted_intent)

    state[_S_ROUND] = 0
    state[_S_IMAGE_VERSION] = 0
    state[_S_LAUNCHED] = False
    for key in (
        _S_DESCRIPTION,
        _S_STYLED,
        _S_IMAGE_NAME,
        _S_VERDICT_RAW,
        _S_CAPTION,
        _S_LAST_VALID_IMAGE,
        _S_LAST_VALID_IMAGE_VERSION,
    ):
        _pop_state_key(state, key)

    return node_input


# ---------------------------------------------------------------------------
# Step 2 & 3: Planner and Stylist (text-only LlmAgents).
#
# Both LlmAgents use an InstructionProvider (callable returning a str) instead
# of a static `instruction=` template -- the prompts borrowed from PaperBanana
# contain embedded LaTeX (e.g. `\mathcal{L}`) and JSON-schema examples with
# literal braces. ADK's instruction interpolator regex (`{+[^{}]*}+` in
# adk/utils/instructions_utils.py) would greedily match those braces and try
# to look up `L`, `"critic_suggestions"`, etc. as session-state variables.
# Doubling braces (`{{L}}`) does NOT escape -- the var-name extraction is
# `lstrip('{').rstrip('}')`. The provider returns a fully-built string with
# state already substituted, so the regex never runs.
# ---------------------------------------------------------------------------


async def _attach_paper_to_request(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Append the active paper PDF as inline_data to the user message."""
    paper = await _load_paper_part(callback_context)
    if paper is None or paper.inline_data is None or not llm_request.contents:
        return
    for content in llm_request.contents:
        if getattr(content, "role", None) == "user" and content.parts:
            content.parts.append(
                types.Part(
                    inline_data=types.Blob(
                        mime_type=paper.inline_data.mime_type,
                        data=paper.inline_data.data,
                    )
                )
            )
            return


async def _planner_instruction(ctx: ReadonlyContext) -> str:
    intent = ctx.state.get(_S_INTENT, "")
    return f"{PLANNER_SYSTEM_PROMPT}\n\n## Visual Intent\n{intent}\n"


def _make_state_saver_callback(
    state_key: str,
    transform: Any = None,
):
    """Creates an after_model_callback that stores model text output in session
    state under state_key and silences user-visible text so intermediate content
    is not displayed in Gemini Enterprise, while preserving thoughts if enabled."""

    async def _callback(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        text = ""
        thought_parts = []
        if llm_response and llm_response.content and llm_response.content.parts:
            text_parts = []
            for p in llm_response.content.parts:
                if getattr(p, "thought", False):
                    thought_parts.append(p)
                elif p.text:
                    text_parts.append(p.text)
            text = "".join(text_parts).strip()

        if text:
            val = transform(text) if transform is not None else text
            if val:
                callback_context.state[state_key] = val

        content = (
            types.Content(role="model", parts=thought_parts) if thought_parts else None
        )
        return LlmResponse(
            content=content,
            finish_reason=(
                llm_response.finish_reason if llm_response else types.FinishReason.STOP
            ),
            usage_metadata=llm_response.usage_metadata if llm_response else None,
        )

    return _callback


_save_planner_output = _make_state_saver_callback(_S_DESCRIPTION)


_planner_agent = LlmAgent(
    name="PaperBananaPlanner",
    model=_PLANNER_MODEL,
    planner=THINKING_PLANNER,
    description="Drafts a detailed visual description of the requested figure.",
    instruction=_planner_instruction,
    before_model_callback=_attach_paper_to_request,
    after_model_callback=_save_planner_output,
)


_STYLIST_PREAMBLE = STYLIST_SYSTEM_PROMPT_TEMPLATE.format(
    style_guide=_STYLE_GUIDE,
)


async def _stylist_instruction(ctx: ReadonlyContext) -> str:
    description = ctx.state.get(_S_DESCRIPTION, "")
    intent = ctx.state.get(_S_INTENT, "")
    return (
        f"{_STYLIST_PREAMBLE}\n\n"
        f"## Detailed Description (from planner)\n{description}\n\n"
        f"## Visual Intent\n{intent}\n"
    )


_save_stylist_output = _make_state_saver_callback(_S_STYLED)


_stylist_agent = LlmAgent(
    name="PaperBananaStylist",
    model=_PLANNER_MODEL,
    planner=THINKING_PLANNER,
    description="Refines the planner draft with NeurIPS-style aesthetic guidance.",
    instruction=_stylist_instruction,
    before_model_callback=_attach_paper_to_request,
    after_model_callback=_save_stylist_output,
)


# ---------------------------------------------------------------------------
# Step 4: the LoopAgent body — Visualizer → Critic → CriticDecision.
# ---------------------------------------------------------------------------


async def _build_visualizer_request(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Build the visualizer's prompt: description + prior image (if any).

    Prior image source priority on round 0:
      1. `current_image_name` from this turn  (set on rounds 1+)
      2. `previous_turn_image` snapshotted by `_PrepInputs` from the prior turn
    Subsequent rounds always use `current_image_name`.

    Also pins `response_modalities=['IMAGE']` so the model returns inline image
    bytes instead of text.
    """
    state = callback_context.state
    description = state.get(_S_STYLED) or state.get(_S_DESCRIPTION) or ""

    parts: list[types.Part] = [
        types.Part(
            text=(
                "Render the following diagram. Do not include figure title text in "
                "the image itself.\n\nDetailed description:\n" + description
            )
        )
    ]

    prior = await _load_image_part(callback_context)
    if prior is None and state.get(_S_ROUND, 0) == 0:
        prev_name = state.get(_S_PREV_TURN_IMAGE)
        if prev_name:
            prior = await callback_context.load_artifact(prev_name)
    if prior is not None and prior.inline_data is not None:
        parts.append(types.Part(text="\nPrevious draft (edit this image):"))
        parts.append(
            types.Part(
                inline_data=types.Blob(
                    mime_type=prior.inline_data.mime_type,
                    data=prior.inline_data.data,
                )
            )
        )

    llm_request.contents = [types.Content(role="user", parts=parts)]
    if llm_request.config is None:
        llm_request.config = types.GenerateContentConfig(http_options=_HTTP_OPTIONS)
    elif llm_request.config.http_options is None:
        llm_request.config.http_options = _HTTP_OPTIONS
    elif llm_request.config.http_options.retry_options is None:
        llm_request.config.http_options.retry_options = _RETRY_OPTIONS
    llm_request.config.response_modalities = ["IMAGE"]
    llm_request.config.image_config = types.ImageConfig(image_size=_IMAGE_SIZE)


_ALLOWED_IMAGE_EXTS = ("png", "jpeg", "jpg", "webp", "gif")


async def _save_visualizer_image(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Persist the rendered image as a `figure_{turn_id}_v{round}.{ext}`
    artifact and advance round/image state.

    Returns an altered LlmResponse with content=None so that intermediate
    artifact confirmation messages are not streamed to Gemini Enterprise,
    and multi-megabyte image binaries are not retained in session events.
    """
    state = callback_context.state
    round_idx = int(state.get(_S_ROUND, 0))
    state[_S_ROUND] = round_idx + 1

    image_part = None
    if llm_response and llm_response.content and llm_response.content.parts:
        for part in llm_response.content.parts:
            blob = part.inline_data
            if blob and (blob.mime_type or "").startswith("image/"):
                image_part = part
                break

    if not image_part or not image_part.inline_data:
        logger.warning("Visualizer round %d produced no image", round_idx)
        _pop_state_key(state, _S_IMAGE_NAME)
        _pop_state_key(state, _S_IMAGE_VERSION)
        return LlmResponse(
            content=None,
            finish_reason=(
                llm_response.finish_reason if llm_response else types.FinishReason.STOP
            ),
            usage_metadata=llm_response.usage_metadata if llm_response else None,
        )

    blob = image_part.inline_data
    turn_id = state.get(_S_TURN_ID, "tx")
    clean_mime = (blob.mime_type or "image/png").split(";")[0].strip().lower()
    raw_ext = clean_mime.split("/", 1)[1] if "/" in clean_mime else "png"
    ext = raw_ext if raw_ext in _ALLOWED_IMAGE_EXTS else "png"
    name = f"figure_{turn_id}_v{round_idx}.{ext}"
    try:
        version = await callback_context.save_artifact(
            name,
            types.Part(
                inline_data=types.Blob(
                    mime_type=clean_mime,
                    data=blob.data,
                )
            ),
        )
        state[_S_IMAGE_VERSION] = version
        state[_S_IMAGE_NAME] = name
        state[_S_LAST_VALID_IMAGE] = name
        state[_S_LAST_VALID_IMAGE_VERSION] = version
        # Intermediate images should NOT be broadcast as artifact cards in GE.
        # Suppress artifact_delta on the event so GE only renders the final figure.
        if hasattr(callback_context, "actions") and hasattr(
            callback_context.actions, "artifact_delta"
        ):
            callback_context.actions.artifact_delta.pop(name, None)
    except Exception as exc:
        logger.warning("Failed to save artifact %s: %s", name, exc)
        _pop_state_key(state, _S_IMAGE_NAME)
        _pop_state_key(state, _S_IMAGE_VERSION)

    return LlmResponse(
        content=None,
        finish_reason=llm_response.finish_reason or types.FinishReason.STOP,
        usage_metadata=llm_response.usage_metadata,
    )


_visualizer_agent = LlmAgent(
    name="PaperBananaVisualizer",
    model=_IMAGE_MODEL,
    description="Renders the diagram (and edits prior renders on later rounds).",
    instruction=lambda _ctx: VISUALIZER_SYSTEM_PROMPT,
    before_model_callback=_build_visualizer_request,
    after_model_callback=_save_visualizer_image,
)


async def _build_critic_request(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Hand the critic the rendered image + the description that produced it."""
    image = await _load_image_part(callback_context)
    if image is None or image.inline_data is None:
        logger.info("No image available for critique, skipping critic LLM call")
        return LlmResponse(
            content=None,
            finish_reason=types.FinishReason.STOP,
        )

    state = callback_context.state
    description = state.get(_S_STYLED) or state.get(_S_DESCRIPTION) or ""
    intent = state.get(_S_INTENT, "")

    parts: list[types.Part] = [types.Part(text="Target Diagram for Critique:")]
    parts.append(
        types.Part(
            inline_data=types.Blob(
                mime_type=image.inline_data.mime_type,
                data=image.inline_data.data,
            )
        )
    )
    parts.append(
        types.Part(
            text=(
                f"\nDetailed Description: {description}\n"
                f"Visual Intent: {intent}\nYour Output:"
            )
        )
    )

    llm_request.contents = [types.Content(role="user", parts=parts)]
    return None


_save_critic_output = _make_state_saver_callback(_S_VERDICT_RAW)


_critic_agent = LlmAgent(
    name="PaperBananaCritic",
    model=_PLANNER_MODEL,
    planner=THINKING_PLANNER,
    description="Critiques the rendered diagram and emits a JSON verdict.",
    instruction=lambda _ctx: CRITIC_SYSTEM_PROMPT,
    before_model_callback=_build_critic_request,
    after_model_callback=_save_critic_output,
)


def _parse_critic_verdict(raw: str) -> tuple[bool, str, str]:
    """Returns (parsed_ok, critic_suggestions, revised_description)."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return (False, "", "")
    if not isinstance(payload, dict):
        return (False, "", "")
    return (
        True,
        str(payload.get("critic_suggestions", "")).strip(),
        str(payload.get("revised_description", "")).strip(),
    )


def _is_no_changes(suggestions: str, revised: str) -> bool:
    return (
        not suggestions
        or suggestions.lower().startswith("no changes needed")
        or not revised
        or revised.lower().startswith("no changes needed")
    )


def decide_refinement_loop(ctx: Context, node_input: Any) -> Event:
    """Acts on the critic's JSON verdict and decides whether to iterate.

    - If no image was rendered, bypass captioning and route directly to 'finalize_direct'.
    - If the critic signaled "no changes needed" or max iterations reached,
      route to 'finalize'.
    - If parsing failed, route to 'finalize' if max iterations reached,
      otherwise route to 'refine' to retry without losing existing state.
    - Otherwise overwrite state[_S_STYLED] with the revised description so
      the next round's Visualizer renders the improved version and route
      to 'refine'.
    """
    state = ctx.state
    if not state.get(_S_IMAGE_NAME):
        return Event(output=node_input, route="finalize_direct")

    round_idx = int(state.get(_S_ROUND, 0))
    raw_verdict = state.get(_S_VERDICT_RAW, "")
    parsed_ok, suggestions, revised = _parse_critic_verdict(raw_verdict)

    if not parsed_ok:
        if round_idx >= _MAX_CRITIC_ROUNDS:
            return Event(output=node_input, route="finalize")
        return Event(output=node_input, route="refine")

    if _is_no_changes(suggestions, revised) or round_idx >= _MAX_CRITIC_ROUNDS:
        return Event(output=node_input, route="finalize")

    state[_S_STYLED] = revised
    return Event(output=node_input, route="refine")


# ---------------------------------------------------------------------------
# Step 5: Captioner (LlmAgent) — inspects the final rendered diagram and
# drafts a publication-grade caption grounded in the actual image.
# ---------------------------------------------------------------------------


def _format_figure_caption(raw_text: str) -> str:
    """Ensures caption text is formatted as '**Figure**: ...' or '**Figure N**: ...' without code fences."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    # Strip wrapping bold markers if the entire string was bolded (e.g. "**Figure 1: text**")
    if (
        cleaned.startswith("**")
        and cleaned.endswith("**")
        and not re.match(r"^\*\*Figure(?:\s*\d+)?\*\*\s*[:.]", cleaned, re.IGNORECASE)
    ):
        cleaned = cleaned[2:-2].strip()

    match = re.match(
        r"^\*{0,2}Figure(?:\s*(\d+))?\*{0,2}\s*[:.]\s*(.*)$",
        cleaned,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        num, rest = match.group(1), match.group(2).strip()
        prefix = f"**Figure {num}**:" if num else "**Figure**:"
        return f"{prefix} {rest}"

    # If formatted as '**Figure**' or '**Figure N**' without colon
    match_no_colon = re.match(
        r"^\*{0,2}Figure(?:\s*(\d+))?\*{0,2}\s+(.*)$",
        cleaned,
        re.IGNORECASE | re.DOTALL,
    )
    if match_no_colon and match_no_colon.group(2).strip():
        num, rest = match_no_colon.group(1), match_no_colon.group(2).strip()
        rest = rest.lstrip(":. ")
        prefix = f"**Figure {num}**:" if num else "**Figure**:"
        return f"{prefix} {rest}"

    return f"**Figure**: {cleaned.lstrip(':*. ').strip()}"


async def _build_captioner_request(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Supplies the captioner with the rendered image, paper PDF, and visual intent."""
    state = callback_context.state
    intent = state.get(_S_INTENT, "")
    parts: list[types.Part] = []

    # Attach paper PDF if available
    paper = await _load_paper_part(callback_context)
    if paper is not None and paper.inline_data is not None:
        parts.append(paper)

    # Attach rendered image
    image = await _load_image_part(callback_context)
    if image is not None and image.inline_data is not None:
        parts.append(
            types.Part(
                inline_data=types.Blob(
                    mime_type=image.inline_data.mime_type,
                    data=image.inline_data.data,
                )
            )
        )
        prompt_lines = [
            "Target Diagram for Captioning (inspect the image above carefully):",
        ]
    else:
        prompt_lines = [
            "[SYSTEM NOTICE] No rendered image is available in state.",
        ]

    if intent:
        prompt_lines.append(f"Intended Diagram Topic: {intent}")
    prompt_lines.append(
        "Write a publication-grade figure caption starting directly with '**Figure**: '."
    )
    parts.append(types.Part(text="\n\n".join(prompt_lines)))

    llm_request.contents = [types.Content(role="user", parts=parts)]


_save_captioner_output = _make_state_saver_callback(
    _S_CAPTION, transform=_format_figure_caption
)


_captioner_agent = LlmAgent(
    name="PaperBananaCaptioner",
    model=_PLANNER_MODEL,
    planner=THINKING_PLANNER,
    description="Inspects the rendered diagram and writes a publication-grade figure caption.",
    instruction=lambda _ctx: CAPTIONER_SYSTEM_PROMPT,
    before_model_callback=_build_captioner_request,
    after_model_callback=_save_captioner_output,
)


# ---------------------------------------------------------------------------
# Step 6: Emit final summary referencing the saved image artifact.
# ---------------------------------------------------------------------------


def finalize(ctx: Context, node_input: Any) -> Iterator[Event]:
    """Emits the user-visible final events for the pipeline.

    Yields:
      1. An artifact_delta event for the accepted image so Gemini Enterprise
         renders the image preview card first.
      2. A model content event with the publication figure caption below the image.
    """
    state = ctx.state
    image_name = state.get(_S_IMAGE_NAME) or state.get(_S_LAST_VALID_IMAGE)
    if not image_name:
        text = (
            "I was unable to render a figure this turn. Try rephrasing the "
            "visual intent or attaching a different paper."
        )
        yield Event(
            output=text,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
        )
        return

    version = int(
        state.get(_S_IMAGE_VERSION) or state.get(_S_LAST_VALID_IMAGE_VERSION) or 0
    )
    # 1. Emit the image artifact card first
    yield Event(
        actions=EventActions(artifact_delta={image_name: version}),
    )

    # 2. Emit the publication figure caption below the image
    caption = state.get(_S_CAPTION)
    if caption:
        text = caption
    else:
        title = _clean_figure_title(state.get(_S_INTENT, ""))
        text = f"**Figure**: {title}"

    yield Event(
        output=text,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


# ---------------------------------------------------------------------------
# Pipeline Workflow DAG
# ---------------------------------------------------------------------------

paperbanana_pipeline = Workflow(
    name="paperbanana_pipeline",
    description=(
        "Generates or refines a publication-style figure from the attached "
        "research paper. Input: the user's visual intent (e.g. \"a "
        'methodology overview diagram with a clear left-to-right flow"). '
        "Reads the paper PDF from session artifacts. Returns the rendered "
        "figure as a saved artifact along with a publication-grade figure caption."
    ),
    edges=[
        (START, prep_inputs),
        (prep_inputs, _planner_agent),
        (_planner_agent, _stylist_agent),
        (_stylist_agent, _visualizer_agent),
        (_visualizer_agent, _critic_agent),
        (_critic_agent, decide_refinement_loop),
        (
            decide_refinement_loop,
            {
                "refine": _visualizer_agent,
                "finalize": _captioner_agent,
                "finalize_direct": finalize,
            },
        ),
        (_captioner_agent, finalize),
    ],
)


# ---------------------------------------------------------------------------
# Root Coordinator & Root Workflow DAG
# ---------------------------------------------------------------------------


def generate_figure(intent: str, tool_context: ToolContext) -> dict[str, str]:
    """Generates or refines a publication-style figure from the attached paper.

    Args:
        intent: A clear description of the figure to generate or the visual
            refinement to apply to the previous diagram.

    Returns:
        Status dictionary confirming figure generation launch.
    """
    cleaned_intent = _strip_procedural_preamble(intent)
    clean_title = _clean_figure_title(intent)
    tool_context.actions.route = "generate_figure"
    tool_context.state[_S_INTENT] = cleaned_intent or clean_title
    tool_context.state[_S_LAUNCHED] = True
    return {
        "status": "launched",
        "intent": clean_title,
        "message": f"Generating publication-style figure: {clean_title}",
    }


_ROOT_INSTRUCTION = """\
You help researchers turn an attached paper PDF into a publication-style
figure. On each turn:

1. If the user has attached a PDF and asked for a figure, call the
   `generate_figure` tool with `intent` set to a single-sentence
   description of the figure they want -- combine their words with sensible
   defaults (e.g. "a methodology overview diagram with a clear left-to-right
   flow showing the three pretraining stages").

2. For follow-up refinement requests ("make icons bigger", "use a softer
   palette", "add a legend"), call `generate_figure` again with `intent`
   that combines the user's delta with what was previously rendered. The
   pipeline picks up the prior render automatically and edits rather than
   re-renders from scratch.

3. If the user has not attached a paper yet or is asking a general question,
   answer conversationally and guide them to attach a paper in the composer.

4. Do not invent papers, citations, or figure content beyond what the
   attached PDF supports.

5. When calling `generate_figure`, invoke the tool directly without emitting
   conversational preambles, procedural summaries, or intermediate explanations.
   The figure generation pipeline will provide the final visual output and summary.
"""


async def _strip_tool_preamble(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Strips conversational preamble text from model responses that invoke tools,
    and silences post-tool confirmation turns when routing to the pipeline."""
    if not (llm_response and llm_response.content and llm_response.content.parts):
        return None
    # If invoking generate_figure, preserve function_call and thoughts, strip text
    if any(getattr(p, "function_call", None) for p in llm_response.content.parts):
        llm_response.content.parts = [
            p
            for p in llm_response.content.parts
            if getattr(p, "function_call", None) or getattr(p, "thought", False)
        ]
        return llm_response
    # If the pipeline was launched in this turn, silence the post-tool conversational turn
    # so the coordinator does not emit a duplicate status summary before finalize runs
    if callback_context.state.get(_S_LAUNCHED):
        callback_context.state[_S_LAUNCHED] = False
        return LlmResponse(
            content=None,
            finish_reason=llm_response.finish_reason or types.FinishReason.STOP,
            usage_metadata=llm_response.usage_metadata,
        )
    return None


coordinator_agent = Agent(
    model=_PLANNER_MODEL,
    planner=THINKING_PLANNER,
    name="coordinator_agent",
    description=(
        "PaperBanana coordinator: conversational lead that scopes figure requests "
        "and launches the publication diagram pipeline."
    ),
    instruction=_ROOT_INSTRUCTION,
    before_model_callback=_inject_uploaded_artifacts,
    after_model_callback=_strip_tool_preamble,
    tools=[generate_figure],
)

root_agent = Workflow(
    name="paperbanana",
    description=(
        "PaperBanana: publication-style diagram generation and refinement "
        "from research papers on Gemini Enterprise."
    ),
    edges=[
        (START, coordinator_agent),
        (coordinator_agent, {"generate_figure": paperbanana_pipeline}),
    ],
)

app = App(root_agent=root_agent, name="app")
