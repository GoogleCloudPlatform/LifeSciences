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

"""Deep-research pipeline: parallel multi-source retrieval -> synth -> critic loop.

Architecture (the canonical ADK fan-out / gather pattern):

    SequentialAgent (DeepResearchPipeline, exposed as AgentTool)
      |- ParallelAgent (RetrievalSwarm)
      |   |- PubMedRetriever       -> state["pubmed_hits"]    (markdown)
      |   |- EuropePmcRetriever    -> state["europe_pmc_hits"](markdown)
      |   |- PreprintRetriever     -> state["preprint_hits"]  (markdown)
      |   `- TrialsRetriever       -> state["trials_hits"]    (markdown)
      |- Synthesizer               -> state["draft_synthesis"]
      `- LoopAgent (max=N)
           |- Critic               -> state["critic_verdict"] (Pydantic dict)
           `- _CriticDecision      escalates loop on "no changes" OR rolls
                                   the revised synthesis into state for
                                   the next round.

Each ParallelAgent sub-agent writes to a unique state key — required to
avoid the race condition that ADK's ParallelAgent docs explicitly call out.

The Critic uses Pydantic `output_schema` so the verdict is structurally
guaranteed (eliminates the regex JSON parser that the earlier draft of
this file used). Retrievers were intentionally NOT given output_schema —
ADK's LlmAgent disables tool-calling whenever output_schema is set, and
the retrievers MUST call their tools. They emit free markdown instead, and
the synthesizer reads markdown summaries (also simpler than parsing JSON).

The whole pipeline is wrapped as an `AgentTool` on the root coordinator so
Gemini Enterprise's "render only the first model event of the turn"
constraint sees a single tool-call result rather than the chain of internal
events.
"""

from __future__ import annotations

import json
import os
from typing import AsyncGenerator

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.loop_agent import LoopAgent
from google.adk.agents.parallel_agent import ParallelAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.tools.agent_tool import AgentTool
from pydantic import BaseModel, Field

from ..tools.biorxiv import search_preprints
from ..tools.clinicaltrials import search_clinical_trials
from ..tools.eutils import advanced_search, search_pubmed
from ..tools.europe_pmc import (
    get_europe_pmc_fulltext,
    search_europe_pmc,
)

_COORDINATOR_MODEL = os.getenv('COORDINATOR_MODEL_NAME', 'gemini-3.1-pro-preview')
_WORKER_MODEL = os.getenv('WORKER_MODEL_NAME', 'gemini-3-flash-preview')
_MAX_CRITIC_ROUNDS = int(os.getenv('MAX_CRITIC_ROUNDS', '2'))

# Session-state keys.
_S_REQUEST = 'research_request'
_S_PUBMED = 'pubmed_hits'
_S_EUROPE_PMC = 'europe_pmc_hits'
_S_PREPRINTS = 'preprint_hits'
_S_TRIALS = 'trials_hits'
_S_DRAFT = 'draft_synthesis'
_S_VERDICT = 'critic_verdict'

_NO_CHANGES = 'no changes needed'


# ---------------------------------------------------------------------------
# Stage 1 — parse the AgentTool call args into the research request.
#
# The root coordinator invokes this pipeline as an AgentTool with one arg:
# `request`. AgentTool serializes that into the inner agent's user_content
# as JSON. We extract it once into state so all downstream agents can read
# the request from a stable key without re-parsing.
# ---------------------------------------------------------------------------

def _extract_request(user_content) -> str:
  if not user_content or not user_content.parts:
    return ''
  for part in user_content.parts:
    if not part.text:
      continue
    try:
      payload = json.loads(part.text)
    except json.JSONDecodeError:
      return part.text.strip()
    if isinstance(payload, dict) and 'request' in payload:
      return str(payload['request']).strip()
  return ''


class _PrepRequest(BaseAgent):
  """Stages the research request and resets per-turn state."""

  async def _run_async_impl(
      self, ctx: InvocationContext,
  ) -> AsyncGenerator[Event, None]:
    state = ctx.session.state
    state[_S_REQUEST] = (
        _extract_request(ctx.user_content)
        or state.get(_S_REQUEST)
        or 'No specific request supplied; perform a general literature scan.'
    )
    for k in (_S_PUBMED, _S_EUROPE_PMC, _S_PREPRINTS, _S_TRIALS,
              _S_DRAFT, _S_VERDICT):
      state.pop(k, None)
    yield Event(author=self.name)


# ---------------------------------------------------------------------------
# Stage 2 — parallel retrievers. Each is a small LlmAgent with one or two
# tools, an output_key, and an instruction that tells it to translate the
# research request into the right query for its source. The InstructionProvider
# variant is used because the prompts reference {state[research_request]}, and
# ADK's plain `instruction=` template path cannot interpolate state values
# safely when the surrounding text could contain literal braces.
#
# Retrievers emit short MARKDOWN summaries (not JSON) — they always call
# their tools, and ADK's LlmAgent disables tool-calling whenever
# `output_schema` is set, so a structured-output approach isn't possible
# here. Markdown is also what the downstream synthesizer reads most
# naturally.
# ---------------------------------------------------------------------------

def _retriever_instruction(
    source: str, tool_hint: str, query_guide: str,
):
  async def _provider(ctx: ReadonlyContext) -> str:
    request = ctx.state.get(_S_REQUEST, '')
    return (
        f'You are a {source} retrieval specialist for a biomedical research '
        'team.\n\n'
        f'Research request:\n{request}\n\n'
        f'Translate that request into a high-recall {source} query and call '
        f'{tool_hint}. {query_guide}\n\n'
        'After the tool call, return a CONCISE MARKDOWN summary in this '
        'shape (no preamble, no closing remarks — the synthesizer reads '
        'this verbatim):\n\n'
        f'### {source}\n'
        '- Query used: `<the literal query string you passed>`\n'
        '- Hit count: <integer>\n'
        '- Top results:\n'
        '  - **<title>** — <first author> et al., <year>. <PMID/PMCID/NCT>. '
        '<one-sentence relevance>\n'
        '  - ... (5-10 of the most relevant results)\n\n'
        'If the tool errored or returned 0 results, write a one-line note '
        'and stop. Never invent results.'
    )
  return _provider


_pubmed_retriever = LlmAgent(
    name='PubMedRetriever',
    model=_WORKER_MODEL,
    description='Retrieves the top relevant PubMed articles via NCBI E-utilities.',
    instruction=_retriever_instruction(
        source='PubMed',
        tool_hint='`search_pubmed` (or `advanced_search` if the request '
                  'specifies dates / publication type / MeSH / journal)',
        query_guide=(
            'Use PubMed query syntax with field tags ([Title], [MeSH], '
            '[PDAT], [PT]). Aim for ~25 results.'
        ),
    ),
    tools=[search_pubmed, advanced_search],
    output_key=_S_PUBMED,
)

_europe_pmc_retriever = LlmAgent(
    name='EuropePmcRetriever',
    model=_WORKER_MODEL,
    description='Retrieves articles from Europe PMC (broader than PubMed; full text where open access).',
    instruction=_retriever_instruction(
        source='Europe PMC',
        tool_hint='`search_europe_pmc`',
        query_guide=(
            'Europe PMC supports field tags TITLE:, ABS:, AUTH:, KW:, '
            'JOURNAL:, MESH:, PUB_YEAR:[YYYY TO YYYY]. Pass '
            '`open_access_only=True` only when the request specifically '
            'wants downstream full-text access. Aim for ~25 results.'
        ),
    ),
    tools=[search_europe_pmc, get_europe_pmc_fulltext],
    output_key=_S_EUROPE_PMC,
)

_preprint_retriever = LlmAgent(
    name='PreprintRetriever',
    model=_WORKER_MODEL,
    description='Retrieves bioRxiv / medRxiv preprints — surfaces the latest results.',
    instruction=_retriever_instruction(
        source='preprint (bioRxiv / medRxiv)',
        tool_hint='`search_preprints`',
        query_guide=(
            'Pass `server="biorxiv"` for life-science preprints, '
            '`"medrxiv"` for clinical, or `"all"` (default) when unsure. '
            'Aim for ~15 results.'
        ),
    ),
    tools=[search_preprints],
    output_key=_S_PREPRINTS,
)

_trials_retriever = LlmAgent(
    name='TrialsRetriever',
    model=_WORKER_MODEL,
    description='Retrieves matching clinical trials from ClinicalTrials.gov.',
    instruction=_retriever_instruction(
        source='ClinicalTrials.gov',
        tool_hint='`search_clinical_trials`',
        query_guide=(
            'Decompose the request into the right field filters: '
            '`condition` for the indication, `intervention` for the drug / '
            'modality, `sponsor` if a company is named, `phase` and '
            '`status` if specified. Aim for ~20 results.'
        ),
    ),
    tools=[search_clinical_trials],
    output_key=_S_TRIALS,
)

_retrieval_swarm = ParallelAgent(
    name='RetrievalSwarm',
    description='Runs PubMed + Europe PMC + preprint + ClinicalTrials.gov queries concurrently.',
    sub_agents=[
        _pubmed_retriever,
        _europe_pmc_retriever,
        _preprint_retriever,
        _trials_retriever,
    ],
)


# ---------------------------------------------------------------------------
# Stage 3 — synthesizer. Reads all four retrieval state keys (markdown) and
# writes a structured evidence brief.
# ---------------------------------------------------------------------------

async def _synthesizer_instruction(ctx: ReadonlyContext) -> str:
  request = ctx.state.get(_S_REQUEST, '')
  pubmed = ctx.state.get(_S_PUBMED, '')
  europe_pmc = ctx.state.get(_S_EUROPE_PMC, '')
  preprints = ctx.state.get(_S_PREPRINTS, '')
  trials = ctx.state.get(_S_TRIALS, '')
  return (
      'You are the synthesis lead for a biomedical research team. The four '
      'retrievers below each ran one query in parallel. Build a single '
      'evidence brief.\n\n'
      f'## Original research request\n{request}\n\n'
      f'## PubMed retriever output\n{pubmed}\n\n'
      f'## Europe PMC retriever output\n{europe_pmc}\n\n'
      f'## Preprint retriever output\n{preprints}\n\n'
      f'## ClinicalTrials.gov retriever output\n{trials}\n\n'
      '## Your output (Markdown)\n'
      '1. **Headline** — one paragraph answering the request directly.\n'
      '2. **Key findings** — 4-8 bullets, each citing PMID / PMCID / NCT '
      'IDs in parentheses. De-duplicate articles that appear in multiple '
      'retrievers (PubMed and Europe PMC overlap heavily).\n'
      '3. **Pipeline / trial landscape** — short table or bulleted list of '
      'the most relevant trials (NCT, sponsor, phase, status).\n'
      '4. **Preprint signal** — note any preprint that materially changes '
      'the picture vs. peer-reviewed evidence; otherwise say "no '
      'preprints add new signal".\n'
      '5. **Critical assessment** — YOUR view, not a summary. 2-4 '
      'substantive bullets, e.g.:\n'
      '   - Where a paper headlines a result its data only weakly support '
      '(e.g. surrogate endpoint, post-hoc subgroup, single-arm Phase 2 '
      'sold as efficacy).\n'
      '   - Where a trial design (open-label, soft endpoint, short '
      'follow-up, low-bar comparator) caps how much weight the result '
      'deserves.\n'
      '   - Where conflicts of interest plausibly color the interpretation '
      '(industry-funded with author overlap, etc.).\n'
      '   - Where you DISAGREE with the field consensus and why, or where '
      'two papers contradict each other and which side is more credible.\n'
      '   This is not optional. Pure summarization is a defect.\n'
      '6. **Gaps & caveats** — what the literature does NOT settle, '
      'limitations of the evidence base as a whole.\n\n'
      'Cite every claim with a PMID / PMCID / NCT that ACTUALLY appears in '
      'the retriever payloads above. Do NOT invent citations — if you want '
      'to make a point but cannot find supporting evidence in the '
      'retriever output, say so explicitly ("no retrieved evidence speaks '
      'to X") rather than fabricating an ID. The critic will audit every '
      'citation and bounce the draft if any are not in the payloads.\n\n'
      'If a retriever errored or returned nothing, say so explicitly under '
      '"Gaps & caveats" instead of pretending it returned data.'
  )


_synthesizer = LlmAgent(
    name='Synthesizer',
    model=_COORDINATOR_MODEL,
    description='Merges parallel retrieval results into a single evidence brief.',
    instruction=_synthesizer_instruction,
    output_key=_S_DRAFT,
)


# ---------------------------------------------------------------------------
# Stage 4 — critic loop with structured output.
#
# The critic emits a CriticVerdict (Pydantic) via output_schema. ADK
# guarantees the saved state value matches the schema (or raises), which
# eliminates the regex-stripping JSON parser the earlier draft used. The
# CriticDecision agent reads the dict directly and decides whether to
# escalate (exit the loop) or roll the revised synthesis forward.
# ---------------------------------------------------------------------------


class CriticVerdict(BaseModel):
  """Structured verdict from the deep-research critic."""

  citation_audit: list[str] = Field(
      default_factory=list,
      description=(
          'List of every PMID / PMCID / NCT in the draft that you CANNOT '
          'find verbatim in the retriever payloads above. Each entry is '
          'one unsupported citation, e.g. "PMID 12345678 cited for X but '
          'not in PubMed retriever output". Empty list means every cited '
          'ID checks out. Be thorough — the synthesizer is known to '
          'occasionally emit plausible-but-invented IDs, and a citation '
          'that does not appear in the retriever payloads is, by '
          'definition, fabricated. Scan parenthetical citations, table '
          'cells, and references.'
      ),
  )
  critic_suggestions: str = Field(
      description=(
          'One paragraph on what to fix in the draft, OR the literal string '
          f'"{_NO_CHANGES}" if the draft is publication-ready. NOTE: if '
          'citation_audit is non-empty, this MUST describe how to remove '
          'or correct the unsupported citations — never "no changes needed" '
          'while citation_audit has entries.'
      )
  )
  revised_synthesis: str = Field(
      description=(
          'The full revised draft as Markdown, OR the literal string '
          f'"{_NO_CHANGES}". If citation_audit is non-empty, the revised '
          'draft must remove or replace the unsupported citations (prefer '
          'removing the claim entirely over swapping in another fabricated '
          'ID).'
      )
  )


_CRITIC_SYSTEM = (
    'You are the senior reviewer for a biomedical research team. Read the '
    'research request and the synthesizer\'s draft. Score the draft on, '
    'in priority order:\n'
    '  1. CITATION FAITHFULNESS (highest priority): every PMID, PMCID, '
    'and NCT in the draft MUST appear verbatim in one of the four '
    'retriever payloads. Walk through them one by one. Anything not in '
    'the payloads is a fabrication and goes in citation_audit. Even one '
    'fabricated citation poisons the brief — do not let any through.\n'
    '  2. completeness: does it answer the request, or are key facets '
    'missing?\n'
    '  3. balance: clinical trials AND mechanism papers AND preprints '
    'all represented when relevant.\n'
    '  4. CRITICAL ENGAGEMENT: does the draft engage with the literature '
    'critically — flagging methodologic limits, distinguishing what '
    "papers headline from what their data support, naming where the "
    'field\'s consensus is thin, calling out conflicts of interest — '
    'or does it just summarize? Pure summarization is a defect for a '
    'pharma research reader; push back if the draft is too deferential.\n'
    '  5. usefulness for a pharma R&D / medical-affairs / clinical '
    'reader.\n'
    '\n'
    'Emit a CriticVerdict. If citation_audit is non-empty, you MUST '
    'request changes (cannot say "no changes needed"). If citations all '
    'check out and the draft is publication-ready on the other criteria, '
    f'set both string fields to "{_NO_CHANGES}".'
)


async def _critic_instruction(ctx: ReadonlyContext) -> str:
  request = ctx.state.get(_S_REQUEST, '')
  draft = ctx.state.get(_S_DRAFT, '')
  pubmed = ctx.state.get(_S_PUBMED, '')
  europe_pmc = ctx.state.get(_S_EUROPE_PMC, '')
  preprints = ctx.state.get(_S_PREPRINTS, '')
  trials = ctx.state.get(_S_TRIALS, '')
  return (
      f'{_CRITIC_SYSTEM}\n'
      f'## Research request\n{request}\n\n'
      f'## Current draft\n{draft}\n\n'
      f'## Retriever payloads (use to verify citations)\n'
      f'### PubMed\n{pubmed}\n\n'
      f'### Europe PMC\n{europe_pmc}\n\n'
      f'### Preprints\n{preprints}\n\n'
      f'### Trials\n{trials}\n'
  )


_critic = LlmAgent(
    name='Critic',
    model=_COORDINATOR_MODEL,
    description='Scores the draft for completeness, faithfulness, and balance.',
    instruction=_critic_instruction,
    output_schema=CriticVerdict,
    output_key=_S_VERDICT,
)


def _is_no_changes(value: str) -> bool:
  return not value or value.strip().lower().startswith('no changes')


class _CriticDecision(BaseAgent):
  """Acts on the critic's structured verdict.

  Decision priority:
    1. citation_audit non-empty -> ALWAYS iterate (override any "no changes"
       signal). The revised draft must drop / fix the unsupported citations.
    2. "no changes needed" in either string field -> escalate to break the
       LoopAgent.
    3. Otherwise -> write the revised draft into state and iterate.
    4. Missing/malformed verdict (shouldn't happen with output_schema) ->
       yield a no-op; bounded by LoopAgent.max_iterations.
  """

  async def _run_async_impl(
      self, ctx: InvocationContext,
  ) -> AsyncGenerator[Event, None]:
    raw = ctx.session.state.get(_S_VERDICT)
    # ADK serializes output_schema results as a dict in state.
    if isinstance(raw, str):
      try:
        raw = json.loads(raw)
      except json.JSONDecodeError:
        yield Event(author=self.name)
        return
    if not isinstance(raw, dict):
      yield Event(author=self.name)
      return

    audit = raw.get('citation_audit') or []
    suggestions = str(raw.get('critic_suggestions', '')).strip()
    revised = str(raw.get('revised_synthesis', '')).strip()

    # Citation issues are a hard "must iterate" signal — fabricated IDs
    # poison the brief regardless of any other strengths.
    if audit:
      if revised and not _is_no_changes(revised):
        ctx.session.state[_S_DRAFT] = revised
      yield Event(author=self.name)
      return

    if _is_no_changes(suggestions) or _is_no_changes(revised):
      yield Event(author=self.name, actions=EventActions(escalate=True))
      return

    ctx.session.state[_S_DRAFT] = revised
    yield Event(author=self.name)


_critic_loop = LoopAgent(
    name='CriticLoop',
    description='Iteratively refines the synthesis until publication-ready.',
    sub_agents=[_critic, _CriticDecision(name='CriticDecision')],
    max_iterations=_MAX_CRITIC_ROUNDS,
)


# ---------------------------------------------------------------------------
# Final stage — emit the polished draft as the single user-visible event so
# Gemini Enterprise's "first event only" rendering rule shows the answer.
# ---------------------------------------------------------------------------

class _Finalize(BaseAgent):

  async def _run_async_impl(
      self, ctx: InvocationContext,
  ) -> AsyncGenerator[Event, None]:
    from google.genai import types
    draft = ctx.session.state.get(_S_DRAFT) or (
        'The pipeline produced no draft synthesis. Try rephrasing the '
        'research request.'
    )
    yield Event(
        author=self.name,
        content=types.Content(
            role='model', parts=[types.Part(text=draft)],
        ),
    )


# ---------------------------------------------------------------------------
# The pipeline + AgentTool wrapper exported to the root coordinator.
# ---------------------------------------------------------------------------

deep_research_pipeline = SequentialAgent(
    name='DeepResearchPipeline',
    description=(
        'Multi-source biomedical deep research. Runs a parallel sweep of '
        'PubMed + Europe PMC + bioRxiv/medRxiv preprints + ClinicalTrials.gov, '
        "synthesizes the results into a cited evidence brief, and runs a "
        'critic-driven refinement loop. Use for any request that benefits '
        'from broader coverage than a single PubMed search — systematic '
        'reviews, target landscape scans, mechanism + trial pipeline '
        'rollups, etc. Input: `request` (the natural-language research '
        'question). Returns the evidence brief as Markdown text.'
    ),
    sub_agents=[
        _PrepRequest(name='PrepRequest'),
        _retrieval_swarm,
        _synthesizer,
        _critic_loop,
        _Finalize(name='Finalize'),
    ],
)

deep_research_tool = AgentTool(agent=deep_research_pipeline)
