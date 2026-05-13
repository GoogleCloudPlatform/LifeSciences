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

"""Light literature-search specialist — single-source PubMed lookups."""

import os

from google.adk.agents.llm_agent import Agent

from ..tools.eutils import (
    advanced_search,
    get_article,
    get_citing_articles,
    search_by_author,
    search_pubmed,
)

_MODEL = os.getenv('WORKER_MODEL_NAME', 'gemini-3-flash-preview')

literature_search_agent = Agent(
    model=_MODEL,
    name='literature_search_agent',
    description=(
        'Light, fast PubMed specialist. Use for keyword / author / PMID '
        'lookups, citation tracking, and date / journal / MeSH / publication-'
        'type filters via NCBI E-utilities. Best when the user wants a '
        'single quick search rather than a multi-source synthesis.'
    ),
    instruction=(
        'You are a PubMed reference librarian. Pick the right tool for the '
        'query:\n'
        '- `search_pubmed` for free-text keyword queries.\n'
        '- `advanced_search` when the user gives explicit filters (date '
        'range, journal, MeSH, publication type).\n'
        '- `search_by_author` for author-only lookups.\n'
        '- `get_article` for a single PMID lookup.\n'
        '- `get_citing_articles` for forward citation tracking.\n\n'
        'Return a tight summary: title, first-author + et al, journal, year, '
        'PMID, URL, and a 1-2 sentence takeaway. Cap the response at the '
        'top ~10 results unless asked for more. Never fabricate citations '
        '— if a tool returns nothing, say so.'
    ),
    tools=[
        search_pubmed,
        advanced_search,
        search_by_author,
        get_article,
        get_citing_articles,
    ],
)
