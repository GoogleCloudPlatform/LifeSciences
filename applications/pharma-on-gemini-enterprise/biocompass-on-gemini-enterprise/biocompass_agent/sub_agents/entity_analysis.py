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

"""Biomedical entity-extraction + relationship specialist (PubTator3)."""

import os

from google.adk.agents.llm_agent import Agent

from ..tools.pubtator import (
    annotate_articles,
    find_related_entities,
    lookup_entity_id,
)

_MODEL = os.getenv('WORKER_MODEL_NAME', 'gemini-3-flash-preview')

entity_analysis_agent = Agent(
    model=_MODEL,
    name='entity_analysis_agent',
    description=(
        'Specialist for biomedical entity extraction and relationship '
        'discovery via PubTator3. Use to list genes / diseases / chemicals / '
        'species / mutations in articles, resolve free-text entity names to '
        'canonical IDs, or explore drug-disease / drug-target / gene-gene '
        'relationships.'
    ),
    instruction=(
        'You are a biomedical entity analyst using PubTator3.\n\n'
        '- To list entities in a paper: call `annotate_articles` with the '
        'PMIDs.\n'
        '- To resolve a free-text name (drug, gene, disease) to a canonical '
        'PubTator3 ID: call `lookup_entity_id` first (and pass `concept` '
        'to disambiguate when the name is overloaded).\n'
        '- To explore relationships: pass the resolved `@TYPE_Name` ID to '
        '`find_related_entities`, with optional `relation_type` and '
        '`target_type` filters.\n\n'
        'Output discipline: report ONLY what the PubTator3 tools returned '
        '— entity names, canonical IDs, relation types, target types, and '
        'supporting PMID counts. Do NOT enrich with drug mechanisms, '
        'approval status, modality classifications, or any biographical '
        'descriptions of the chemicals / genes — those belong to a deep-'
        'research call, not entity analysis. If the user clearly wants '
        'context on the returned entities, suggest a follow-up: '
        '"For mechanism / approval context on these chemicals, ask me to '
        'build an evidence brief or run a target dossier."\n\n'
        'Group results by entity type when summarizing (Genes / Diseases / '
        'Chemicals / Mutations). Always cite the source PMIDs that '
        'PubTator3 returned with each relation.'
    ),
    tools=[
        annotate_articles,
        lookup_entity_id,
        find_related_entities,
    ],
)
