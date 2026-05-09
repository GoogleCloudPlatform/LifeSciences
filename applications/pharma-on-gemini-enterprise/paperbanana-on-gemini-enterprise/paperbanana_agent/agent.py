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

A Google ADK agent that mimics a lite version of PaperBanana
(https://github.com/dwzhu-pku/PaperBanana, Apache-2.0). The user attaches a
research paper in the Gemini Enterprise composer and asks for a figure; the
pipeline plans -> stylizes -> renders -> critiques -> refines and returns a
publication-style diagram.

Pipeline (ADK workflow agents):

    SequentialAgent (PaperBananaPipeline, exposed as AgentTool)
      |- _PrepInputsAgent     stages tool args + previous-turn snapshot in state
      |- Planner              LlmAgent -> state["description"]
      |- Stylist              LlmAgent -> state["styled_description"]
      |- LoopAgent(max=N)
      |    |- Visualizer      LlmAgent (gemini-3-pro-image-preview)
      |    |                   saves figure_{turn_id}_v{round}.png as artifact
      |    |- Critic          LlmAgent -> state["critic_verdict_raw"] (JSON)
      |    `-_CriticDecision escalates loop on "no changes" OR rolls
      |                       revised_description into state for the next round
      `-_FinalizeAgent       emits summary text referencing the final artifact

Wrapped as an AgentTool so the conversational root LlmAgent (which is what
GE talks to) emits exactly one final event per turn -- the
model_garden_agent README documents why this matters for GE rendering.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import AsyncGenerator

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import Agent, LlmAgent
from google.adk.agents.loop_agent import LoopAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .prompts import (
    CRITIC_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    STYLIST_SYSTEM_PROMPT_TEMPLATE,
    VISUALIZER_SYSTEM_PROMPT,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Gemini 3.x is only served from the `global` endpoint; Agent Engine itself
# still deploys to a regional endpoint (us-central1) — same pattern the
# model_garden_agent uses for Claude in us-east5.
os.environ['GOOGLE_CLOUD_LOCATION'] = os.getenv('MODEL_LOCATION', 'global')

_PLANNER_MODEL = os.getenv('PLANNER_MODEL_NAME', 'gemini-3.1-pro-preview')
_IMAGE_MODEL = os.getenv('IMAGE_MODEL_NAME', 'gemini-3-pro-image-preview')
_MAX_CRITIC_ROUNDS = int(os.getenv('MAX_CRITIC_ROUNDS', '3'))
# Nano Banana Pro (gemini-3-pro-image-preview) supports 1K, 2K, 4K.
# Default to 4K for publication-quality output; drop to 2K/1K for faster turns.
_IMAGE_SIZE = os.getenv('IMAGE_SIZE', '4K')

_STYLE_GUIDE_PATH = Path(__file__).parent / 'style_guide.md'
_STYLE_GUIDE = _STYLE_GUIDE_PATH.read_text(encoding='utf-8')

# Session-state keys used to pass data between pipeline steps.
_S_INTENT = 'intent'
_S_PAPER_NAME = 'paper_artifact_name'
_S_DESCRIPTION = 'description'
_S_STYLED = 'styled_description'
_S_IMAGE_NAME = 'current_image_name'
_S_ROUND = 'current_round'
_S_VERDICT_RAW = 'critic_verdict_raw'
_S_TURN_ID = 'turn_id'
_S_PREV_TURN_IMAGE = 'previous_turn_image'

# Same marker convention Gemini Enterprise uses to signal an attached file.
_FILE_MARKER_RE = re.compile(
    r'<start_of_user_uploaded_file:\s*(?P<name>[^>]+?)\s*>'
)
_GEMINI_INLINE_MIMES = ('image/', 'application/pdf')


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
    callback_context: CallbackContext, llm_request: LlmRequest,
) -> LlmResponse | None:
  if not llm_request.contents:
    return None
  artifact_keys = set(await callback_context.list_artifacts())

  for content in llm_request.contents:
    if getattr(content, 'role', None) != 'user' or not content.parts:
      continue
    injected: set[str] = set()
    text_parts = [p for p in content.parts if p.text]
    for part in text_parts:
      for match in _FILE_MARKER_RE.finditer(part.text):
        name = match.group('name').strip()
        if name in injected or name not in artifact_keys:
          continue
        artifact = await callback_context.load_artifact(name)
        if artifact is None or artifact.inline_data is None:
          continue
        if not _is_inlineable(artifact.inline_data.mime_type):
          continue
        content.parts.append(types.Part(inline_data=types.Blob(
            mime_type=artifact.inline_data.mime_type,
            data=artifact.inline_data.data,
        )))
        # Remember the most recent paper PDF so the pipeline can find it.
        if artifact.inline_data.mime_type == 'application/pdf':
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
    for candidate in await callback_context.list_artifacts():
      part = await callback_context.load_artifact(candidate)
      if (part and part.inline_data
          and (part.inline_data.mime_type or '').startswith('application/pdf')):
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
# Step 1: stage the AgentTool args + previous-turn snapshot into state.
#
# AgentTool serializes the tool's kwargs and passes them as the inner agent's
# user_content. We extract `intent` here once so downstream LlmAgents can read
# it from session state.
# ---------------------------------------------------------------------------

def _extract_intent_from_user_content(user_content) -> str:
  """AgentTool wraps the function-call args as JSON in user_content; fall
  back to plain text if it doesn't parse."""
  if not user_content or not user_content.parts:
    return ''
  for part in user_content.parts:
    if not part.text:
      continue
    try:
      parsed = json.loads(part.text)
    except json.JSONDecodeError:
      return part.text.strip()
    if isinstance(parsed, dict) and 'intent' in parsed:
      return str(parsed['intent']).strip()
  return ''


class _PrepInputsAgent(BaseAgent):
  """Stages per-turn pipeline inputs into session state.

  Specifically:
    * extracts `intent` from the tool-call args,
    * snapshots the previous turn's final image so the Visualizer's first
      round can use it as edit input (refinement turns start from the prior
      render -- the iterative-improvement technique PaperBanana relies on),
    * mints a per-turn ID so saved-image filenames don't collide across
      turns in chat history,
    * resets per-turn intermediate state.
  """

  async def _run_async_impl(
      self, ctx: InvocationContext,
  ) -> AsyncGenerator[Event, None]:
    state = ctx.session.state

    prev_image = state.get(_S_IMAGE_NAME)
    if prev_image:
      state[_S_PREV_TURN_IMAGE] = prev_image

    state[_S_TURN_ID] = uuid.uuid4().hex[:8]
    state[_S_INTENT] = (
        _extract_intent_from_user_content(ctx.user_content)
        or 'A clear methodology overview diagram.'
    )
    state[_S_ROUND] = 0
    for key in (_S_DESCRIPTION, _S_STYLED, _S_IMAGE_NAME, _S_VERDICT_RAW):
      state.pop(key, None)

    yield Event(author=self.name)


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
    callback_context: CallbackContext, llm_request: LlmRequest,
) -> None:
  """Append the active paper PDF as inline_data to the user message."""
  paper = await _load_paper_part(callback_context)
  if paper is None or paper.inline_data is None or not llm_request.contents:
    return
  for content in llm_request.contents:
    if getattr(content, 'role', None) == 'user' and content.parts:
      content.parts.append(types.Part(inline_data=types.Blob(
          mime_type=paper.inline_data.mime_type,
          data=paper.inline_data.data,
      )))
      return


async def _planner_instruction(ctx: ReadonlyContext) -> str:
  intent = ctx.state.get(_S_INTENT, '')
  return f'{PLANNER_SYSTEM_PROMPT}\n\n## Visual Intent\n{intent}\n'


_planner_agent = LlmAgent(
    name='PaperBananaPlanner',
    model=_PLANNER_MODEL,
    description='Drafts a detailed visual description of the requested figure.',
    instruction=_planner_instruction,
    before_model_callback=_attach_paper_to_request,
    output_key=_S_DESCRIPTION,
)


_STYLIST_PREAMBLE = STYLIST_SYSTEM_PROMPT_TEMPLATE.format(
    style_guide=_STYLE_GUIDE,
)


async def _stylist_instruction(ctx: ReadonlyContext) -> str:
  description = ctx.state.get(_S_DESCRIPTION, '')
  intent = ctx.state.get(_S_INTENT, '')
  return (
      f'{_STYLIST_PREAMBLE}\n\n'
      f'## Detailed Description (from planner)\n{description}\n\n'
      f'## Visual Intent\n{intent}\n'
  )


_stylist_agent = LlmAgent(
    name='PaperBananaStylist',
    model=_PLANNER_MODEL,
    description='Refines the planner draft with NeurIPS-style aesthetic guidance.',
    instruction=_stylist_instruction,
    before_model_callback=_attach_paper_to_request,
    output_key=_S_STYLED,
)


# ---------------------------------------------------------------------------
# Step 4: the LoopAgent body — Visualizer → Critic → CriticDecision.
# ---------------------------------------------------------------------------

async def _build_visualizer_request(
    callback_context: CallbackContext, llm_request: LlmRequest,
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
  description = state.get(_S_STYLED) or state.get(_S_DESCRIPTION) or ''

  parts: list[types.Part] = [types.Part(text=(
      'Render the following diagram. Do not include figure title text in '
      'the image itself.\n\nDetailed description:\n' + description
  ))]

  prior = await _load_image_part(callback_context)
  if prior is None and state.get(_S_ROUND, 0) == 0:
    prev_name = state.get(_S_PREV_TURN_IMAGE)
    if prev_name:
      prior = await callback_context.load_artifact(prev_name)
  if prior is not None and prior.inline_data is not None:
    parts.append(types.Part(text='\nPrevious draft (edit this image):'))
    parts.append(types.Part(inline_data=types.Blob(
        mime_type=prior.inline_data.mime_type,
        data=prior.inline_data.data,
    )))

  llm_request.contents = [types.Content(role='user', parts=parts)]
  if llm_request.config is None:
    llm_request.config = types.GenerateContentConfig()
  llm_request.config.response_modalities = ['IMAGE']
  llm_request.config.image_config = types.ImageConfig(image_size=_IMAGE_SIZE)


async def _save_visualizer_image(
    callback_context: CallbackContext, llm_response: LlmResponse,
) -> LlmResponse | None:
  """Persist the rendered image as a `figure_{turn_id}_v{round}.{ext}`
  artifact and advance round/image state."""
  if not (llm_response and llm_response.content and llm_response.content.parts):
    return None
  for part in llm_response.content.parts:
    blob = part.inline_data
    if not (blob and (blob.mime_type or '').startswith('image/')):
      continue
    state = callback_context.state
    round_idx = int(state.get(_S_ROUND, 0))
    turn_id = state.get(_S_TURN_ID, 'tx')
    ext = blob.mime_type.split('/', 1)[1]
    name = f'figure_{turn_id}_v{round_idx}.{ext}'
    await callback_context.save_artifact(name, types.Part(inline_data=types.Blob(
        mime_type=blob.mime_type, data=blob.data,
    )))
    state[_S_IMAGE_NAME] = name
    state[_S_ROUND] = round_idx + 1
    break
  return None


_visualizer_agent = LlmAgent(
    name='PaperBananaVisualizer',
    model=_IMAGE_MODEL,
    description='Renders the diagram (and edits prior renders on later rounds).',
    instruction=lambda _ctx: VISUALIZER_SYSTEM_PROMPT,
    before_model_callback=_build_visualizer_request,
    after_model_callback=_save_visualizer_image,
)


async def _build_critic_request(
    callback_context: CallbackContext, llm_request: LlmRequest,
) -> None:
  """Hand the critic the rendered image + the description that produced it."""
  state = callback_context.state
  description = state.get(_S_STYLED) or state.get(_S_DESCRIPTION) or ''
  intent = state.get(_S_INTENT, '')

  parts: list[types.Part] = [types.Part(text='Target Diagram for Critique:')]
  image = await _load_image_part(callback_context)
  if image is not None and image.inline_data is not None:
    parts.append(types.Part(inline_data=types.Blob(
        mime_type=image.inline_data.mime_type,
        data=image.inline_data.data,
    )))
  else:
    parts.append(types.Part(text=(
        '\n[SYSTEM NOTICE] No image is available for this round (likely a '
        'visualizer failure). Diagnose the description and propose a '
        'revised, simpler version.'
    )))
  parts.append(types.Part(text=(
      f'\nDetailed Description: {description}\n'
      f'Visual Intent: {intent}\nYour Output:'
  )))

  llm_request.contents = [types.Content(role='user', parts=parts)]


_critic_agent = LlmAgent(
    name='PaperBananaCritic',
    model=_PLANNER_MODEL,
    description='Critiques the rendered diagram and emits a JSON verdict.',
    instruction=lambda _ctx: CRITIC_SYSTEM_PROMPT,
    before_model_callback=_build_critic_request,
    output_key=_S_VERDICT_RAW,
)


def _parse_critic_verdict(raw: str) -> tuple[bool, str, str]:
  """Returns (parsed_ok, critic_suggestions, revised_description)."""
  cleaned = (raw or '').strip()
  if cleaned.startswith('```'):
    cleaned = cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
  try:
    payload = json.loads(cleaned)
  except json.JSONDecodeError:
    return (False, '', '')
  if not isinstance(payload, dict):
    return (False, '', '')
  return (
      True,
      str(payload.get('critic_suggestions', '')).strip(),
      str(payload.get('revised_description', '')).strip(),
  )


def _is_no_changes(suggestions: str, revised: str) -> bool:
  return (
      not suggestions
      or suggestions.lower().startswith('no changes needed')
      or not revised
      or revised.lower().startswith('no changes needed')
  )


class _CriticDecisionAgent(BaseAgent):
  """Acts on the critic's JSON verdict.

  - If parsing failed, yield a no-op event so the loop keeps iterating with
    the existing description (a single bad model output should not silently
    short-circuit refinement; `LoopAgent.max_iterations` bounds the cost).
  - If the critic signalled "no changes needed", set `escalate=True` to exit
    the LoopAgent.
  - Otherwise overwrite `state[styled_description]` with the revised
    description so the next round's Visualizer renders the improved version.
  """

  async def _run_async_impl(
      self, ctx: InvocationContext,
  ) -> AsyncGenerator[Event, None]:
    parsed_ok, suggestions, revised = _parse_critic_verdict(
        ctx.session.state.get(_S_VERDICT_RAW, '')
    )
    if not parsed_ok:
      yield Event(author=self.name)
      return
    if _is_no_changes(suggestions, revised):
      yield Event(author=self.name, actions=EventActions(escalate=True))
      return
    ctx.session.state[_S_STYLED] = revised
    yield Event(author=self.name)


_refinement_loop = LoopAgent(
    name='PaperBananaRefinementLoop',
    sub_agents=[_visualizer_agent, _critic_agent, _CriticDecisionAgent(
        name='PaperBananaCriticDecision',
    )],
    max_iterations=_MAX_CRITIC_ROUNDS,
)


# ---------------------------------------------------------------------------
# Step 5: emit a final summary event referencing the saved image artifact.
# Using the same marker convention Gemini Enterprise uses for user uploads
# means the artifact shows up symmetrically in the chat history.
# ---------------------------------------------------------------------------

class _FinalizeAgent(BaseAgent):
  """Emits the user-visible final event for the AgentTool, referencing the
  saved figure via the `<start_of_user_uploaded_file: NAME>` marker so it
  renders symmetrically with user-uploaded files in GE chat history."""

  async def _run_async_impl(
      self, ctx: InvocationContext,
  ) -> AsyncGenerator[Event, None]:
    state = ctx.session.state
    image_name = state.get(_S_IMAGE_NAME)
    if not image_name:
      text = (
          'I was unable to render a figure this turn. Try rephrasing the '
          'visual intent or attaching a different paper.'
      )
    else:
      text = (
          f'Generated figure: <start_of_user_uploaded_file: {image_name}>\n\n'
          f'Description used:\n{state.get(_S_STYLED, "")}'
      )
    yield Event(
        author=self.name,
        content=types.Content(role='model', parts=[types.Part(text=text)]),
    )


# ---------------------------------------------------------------------------
# Pipeline + AgentTool wrapper.
# ---------------------------------------------------------------------------

paperbanana_pipeline = SequentialAgent(
    name='PaperBananaPipeline',
    description=(
        'Generates or refines a publication-style figure from the attached '
        "research paper. Input: the user's visual intent (e.g. \"a "
        'methodology overview diagram with a clear left-to-right flow"). '
        'Reads the paper PDF from session artifacts. Returns the rendered '
        'figure as a saved artifact and a textual summary that includes '
        'the artifact filename marker.'
    ),
    sub_agents=[
        _PrepInputsAgent(name='PaperBananaPrepInputs'),
        _planner_agent,
        _stylist_agent,
        _refinement_loop,
        _FinalizeAgent(name='PaperBananaFinalize'),
    ],
)

generate_figure_tool = AgentTool(agent=paperbanana_pipeline)


# ---------------------------------------------------------------------------
# Root agent (what GE binds to).
# ---------------------------------------------------------------------------

_ROOT_INSTRUCTION = """\
You help researchers turn an attached paper PDF into a publication-style
figure. On each turn:

1. If the user has attached a PDF and asked for a figure, call the
   `PaperBananaPipeline` tool with `intent` set to a single-sentence
   description of the figure they want -- combine their words with sensible
   defaults (e.g. "a methodology overview diagram with a clear left-to-right
   flow showing the three pretraining stages").

2. The tool returns a summary that includes a
   `<start_of_user_uploaded_file: NAME>` marker referencing the rendered
   figure artifact. Pass that marker through verbatim in your reply so the
   user sees the image, then add a one-line caption.

3. For follow-up refinement requests ("make icons bigger", "use a softer
   palette", "add a legend"), call the tool again with `intent` that combines
   the user's delta with what was previously rendered. The pipeline picks up
   the prior render automatically and edits rather than re-renders from
   scratch.

4. If the user has not attached a paper yet, ask them to attach one in the
   composer.

5. Do not invent papers, citations, or figure content beyond what the
   attached PDF supports.
"""

root_agent = Agent(
    model=_PLANNER_MODEL,
    name='root_agent',
    description=(
        'Generates publication-style figures from an attached research '
        'paper. Lite ADK port of PaperBanana.'
    ),
    instruction=_ROOT_INSTRUCTION,
    before_model_callback=_inject_uploaded_artifacts,
    tools=[generate_figure_tool],
)
