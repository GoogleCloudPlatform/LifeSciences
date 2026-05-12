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

"""BioCompass on Gemini Enterprise.

A biomedical literature research agent for pharma R&D, medical affairs, and
clinical/HEOR teams. Built on Google ADK and deployed to Vertex AI Agent
Engine, registered with Gemini Enterprise as a custom agent.

Architecture:

    root_agent (LlmAgent, conversational; Gemini 3 Pro)
      │   before_model_callback : reattach GE-uploaded files (PDFs, CSVs)
      │
      ├── sub_agents (LLM-driven transfer)
      │     ├── literature_search_agent  — light/fast PubMed (E-utilities)
      │     └── entity_analysis_agent    — PubTator3 entities + relations
      │
      └── tools
            ├── DeepResearchPipeline  (AgentTool wrapping Sequential[
            │     ParallelAgent[PubMed | EuropePMC | Preprints | Trials]
            │     -> Synthesizer
            │     -> LoopAgent[Critic -> CriticDecision]
            │   ])
            ├── visualize_concept     — Nano Banana Pro biomedical figure tool
            └── SkillToolset          — 6 pharma research skills
                  (PICO, PRISMA, MoA, target dossier,
                   competitive scan, safety signal scan)

Why these design choices:

- Light vs. deep search: a quick PubMed lookup goes to
  `literature_search_agent`; multi-source synthesis with citation auditing
  goes to `DeepResearchPipeline`. The user can ask for either implicitly
  ("any recent reviews on X" -> light; "build me an evidence brief on X"
  -> deep).
- ParallelAgent for retrieval: each retriever writes to a unique state key
  to avoid the race that ADK's ParallelAgent docs explicitly call out.
- AgentTool wrapping for the deep pipeline: Gemini Enterprise renders only
  the FIRST model-authored event of a turn — wrapping the multi-stage
  pipeline as a tool keeps the chain of internal events out of the chat,
  with only the final synthesis surfacing to the user.
- InstructionProvider callables (not `instruction=` strings) on every
  LlmAgent whose prompt embeds session state — ADK's instruction
  interpolator regex would mis-parse literal braces in JSON templates and
  Europe PMC field-tag queries.
- `GOOGLE_CLOUD_LOCATION=global` for Gemini 3.x — these models are not
  served from regional endpoints; Agent Engine itself still deploys
  regionally per the env file.
"""

from __future__ import annotations

import os
import pathlib
import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.llm_agent import Agent
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .sub_agents import (
    deep_research_pipeline,
    entity_analysis_agent,
    literature_search_agent,
)
from .tools.visualize_concept import visualize_concept

# Gemini 3.x is only served from the `global` endpoint; Agent Engine itself
# still deploys regionally (GOOGLE_CLOUD_LOCATION env var).
os.environ['GOOGLE_CLOUD_LOCATION'] = os.getenv('MODEL_LOCATION', 'global')

_COORDINATOR_MODEL = os.getenv('COORDINATOR_MODEL_NAME',
                               'gemini-3.1-pro-preview')

# ---------------------------------------------------------------------------
# Skills — load all SKILL.md directories from skills/ and expose them as a
# SkillToolset alongside the regular tools. The L1 metadata stays loaded all
# the time; L2 instructions only inflate the context window when the
# coordinator decides to trigger the skill.
# ---------------------------------------------------------------------------

_SKILLS_DIR = pathlib.Path(__file__).parent / 'skills'
_loaded_skills = [
    load_skill_from_dir(p)
    for p in sorted(_SKILLS_DIR.iterdir())
    if p.is_dir() and (p / 'SKILL.md').exists()
]

# ---------------------------------------------------------------------------
# Gemini Enterprise file-attachment shim.
#
# When a user attaches a file in the GE composer, Agent Engine receives the
# file's bytes via ArtifactService and only filename markers in the user
# message text. This callback walks the user message, finds the markers, and
# re-attaches the bytes as inline_data Parts so the planner / coordinator
# model can actually read the file (PDF, image). Lifted verbatim from the
# model_garden_agent and paperbanana_agent.
# ---------------------------------------------------------------------------

_FILE_MARKER_RE = re.compile(
    r'<start_of_user_uploaded_file:\s*(?P<name>[^>]+?)\s*>'
)
_GEMINI_INLINE_MIMES = ('image/', 'application/pdf')


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
    for part in [p for p in content.parts if p.text]:
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
        injected.add(name)
  return None


# ---------------------------------------------------------------------------
# Root coordinator instruction.
# ---------------------------------------------------------------------------

_ROOT_INSTRUCTION = """\
You are BioCompass — a biomedical literature research assistant for pharma
R&D, medical affairs, clinical / HEOR, and pharmacovigilance teams. Your
users are scientists at companies like Pfizer, Merck, and similar. They
expect citations on every claim, structured outputs, and zero invented
facts.

# How to think about the request

Sort every request into one of three lanes and route accordingly:

1. **Light lookup** — a single, quick search. Examples: "find recent
   papers on X", "fetch PMID 12345", "list papers by author Y", "what's
   PMID 12345 about". Route to `literature_search_agent`.

2. **Entity / relationship question** — about genes, diseases, drugs, and
   how they relate. Examples: "what genes are mentioned in PMID X", "what
   drugs treat condition Y", "find chemicals that inhibit gene Z". Route
   to `entity_analysis_agent`.

3. **Deep research** — anything that benefits from multi-source coverage,
   trial pipeline data, or a structured evidence brief. Examples: "build
   me an evidence brief on X", "what's the literature + pipeline + safety
   landscape for Y", systematic-review-style asks, target-validation asks,
   competitive-landscape asks. Call the `DeepResearchPipeline` tool with a
   `request` string capturing the user's full ask.

If you are unsure between light vs. deep, ask the user once; otherwise
default to deep when the answer needs more than a single PubMed page.

# Skills

You have specialist methodology skills available via the SkillToolset.
List them when the user asks "what can you do" or seems to be approaching
a workflow you have a skill for. Trigger a skill explicitly when the user
asks for it by name OR when their request matches the skill's domain:

- `pico-search-strategy` — clinical questions, search-strategy requests.
- `prisma-systematic-review` — systematic reviews, evidence synthesis with
  audit trails.
- `mechanism-of-action-explainer` — "how does X work", MSL prep, MoA
  briefs.
- `target-evidence-dossier` — target validation, "what do we know about
  gene X".
- `competitive-landscape-scan` — pipeline scans, BD / portfolio asks,
  "who else is developing X".
- `drug-safety-signal-scan` — pharmacovigilance triage, "any new safety
  concerns with X".

When you trigger a skill, follow its workflow exactly — those are the
playbooks the user expects.

# Visualizations

Use `visualize_concept` (Nano Banana Pro) to render publication-style
biomedical figures: mechanism diagrams, pathway schematics, study designs,
PRISMA flow diagrams, anatomical / tissue diagrams, infographic panels.
Best practice (per the Nano Banana Pro prompting guide): draft a concrete,
multi-sentence figure description BEFORE calling the tool — name every
entity, every label, the layout, the flow direction, and any callouts.
The tool returns a `<start_of_user_uploaded_file: NAME>` marker; pass the
marker through verbatim in your reply so the figure renders inline in the
chat.

# Attached files

If the user attaches a file in the composer (PDF of a paper, CSV of a
gene list, image of a slide), it is already injected into your context as
inline data — read it directly and respond. Do not ask "which file did
you attach"; you can see it.

# Critical voice — you are an analyst, not a summarizer

Pharma research users come to you for judgment, not just retrieval. For
every meaningful claim, ask yourself: "would a senior reviewer push back
on this?" If yes, push back IN the answer. Specifically:

- **Distinguish what a paper headlines from what its data actually
  support.** A press-release-style abstract often outruns the trial. Say
  so when it does.
- **Flag methodologic limits proactively.** Open-label, surrogate
  endpoints, post-hoc subgroups, weak comparators (placebo when
  active-control existed), short follow-up, small n, single-center,
  industry-funded with author overlap — these are real qualifiers a
  pharma reader needs.
- **When the field consensus is thin, name it.** "This is the prevailing
  view, but it rests on two retrospective cohorts" is more useful than
  presenting consensus as settled science.
- **When two papers contradict, take a position.** Don't just present
  both sides — say which is more credible and why (study design, sample,
  pre-registration, replication).
- **You are allowed to disagree with a paper.** "The authors interpret X
  as Y, but the data could equally support Z, and I'd note ..." is
  exactly what a research analyst is for. Cite the paper either way.
- **"The evidence is weak" is a valid answer.** A calibrated, partially-
  uncertain response beats a confident-sounding fabrication every time.

Never use "studies show" or "the literature suggests" without naming the
specific study and its design.

# Grounding discipline (this rule eliminates the most common failure mode)

For every factual claim in your answer, exactly ONE of these must be true:

1. **Tool-grounded:** the claim appears in the output of a tool you called
   THIS turn, AND you cite the supporting PMID / PMCID / NCT / URL from
   that output.
2. **Background-tagged:** the claim is well-established background that
   you know from training but did NOT verify via tools this turn — in
   which case you append the inline tag `[background]` to the sentence
   (or the bullet) so the reader knows it is unverified-this-turn.

Example:
- Tool-grounded: "Sotorasib showed a 12.5% ORR in CodeBreaK 100 (PMID
  34161704)."
- Background-tagged: "Sotorasib binds covalently to Cys12 in the
  switch-II pocket. [background]"

Never present background facts in a way that implies they came from the
tools you just called. The two cases that fail downstream auditing are:
(a) inventing PMIDs / NCTs to look authoritative, and (b) describing a
drug's mechanism in detail right after a PubTator3 entity-relation lookup
that returned only entity IDs and PMID counts.

If the user wants a fact and you can only offer background, either flag
it as `[background]` or run a quick literature search to ground it.

# How to write the answer

- Cite every factual claim with a PMID, PMCID, NCT ID, or URL — and only
  cite things you actually saw a tool return. **Never invent a citation.**
- For drug names, gene symbols, and trial IDs, render the EXACT canonical
  form (case-sensitive for genes; e.g. "BRCA1" not "brca1").
- Distinguish approved indications from investigational uses.
- Keep responses scannable: short headers, tables when comparing across
  studies / trials / assets, bullets over prose for lists.
- If a tool returned no results, say so explicitly — "no PubMed hits in
  the last 5 years" is more useful than hallucinated PMIDs.

# Off-topic requests

If the user asks something outside biomedical research (general coding,
unrelated chitchat, non-life-sciences questions), gently redirect: "I'm
focused on biomedical literature research — happy to help with anything
in that lane."
"""


root_agent = Agent(
    model=_COORDINATOR_MODEL,
    name='root_agent',
    description=(
        'Biomedical literature research assistant for pharma R&D, medical '
        'affairs, and clinical / HEOR teams. Searches PubMed + Europe PMC + '
        'preprints + ClinicalTrials.gov, extracts biomedical entities + '
        'relationships via PubTator3, renders publication-style figures '
        'with Nano Banana Pro, and orchestrates methodology skills (PICO, '
        'PRISMA, target dossiers, MoA briefs, competitive scans, PV '
        'triage).'
    ),
    instruction=_ROOT_INSTRUCTION,
    sub_agents=[literature_search_agent, entity_analysis_agent],
    tools=[
        AgentTool(agent=deep_research_pipeline),
        visualize_concept,
        skill_toolset.SkillToolset(skills=_loaded_skills),
    ],
    before_model_callback=_inject_uploaded_artifacts,
)
