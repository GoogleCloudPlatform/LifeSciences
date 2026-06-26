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

"""PubTator3 client + ADK FunctionTool wrappers (FREE).

PubTator3 supplies pre-extracted biomedical entities (genes, diseases,
chemicals, species, mutations, cell lines) for the entire PubMed corpus and
exposes a relations endpoint for entity-pair queries (drug-treats-disease,
gene-interacts-gene, ...).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from . import _http

_BASE_URL = 'https://www.ncbi.nlm.nih.gov/research/pubtator3-api'
_RATE_LIMIT_S = 0.34  # 3 req/sec
_last_request: float = 0.0


async def _rate_limit() -> None:
  global _last_request
  loop = asyncio.get_event_loop()
  elapsed = loop.time() - _last_request
  if elapsed < _RATE_LIMIT_S:
    await asyncio.sleep(_RATE_LIMIT_S - elapsed)
  _last_request = loop.time()


def _err(e: Exception, ctx: str) -> dict[str, Any]:
  return {'error': True, 'message': str(e), 'context': ctx}


_VALID_CONCEPTS = ('gene', 'disease', 'chemical', 'species', 'mutation')
_VALID_RELATIONS = (
    'treat', 'cause', 'cotreat', 'convert', 'compare', 'interact',
    'associate', 'positive_correlate', 'negative_correlate', 'prevent',
    'inhibit', 'stimulate', 'drug_interact',
)
_VALID_TARGETS = ('gene', 'disease', 'chemical', 'variant')


async def annotate_articles(
    pmids: list[str], full_text: bool = False,
) -> dict[str, Any]:
  """Get PubTator3 entity annotations for one or more PubMed articles (FREE).

  Returns the full BioC-JSON document including extracted genes, diseases,
  chemicals, species, mutations, and cell lines.

  Args:
    pmids: PubMed IDs, e.g. ["32133824", "34170578"]. 1-100 per call.
    full_text: If True, annotate full text where available (PMC subset only).
  """
  if not pmids:
    return {'error': True, 'message': 'pmids cannot be empty'}
  await _rate_limit()
  try:
    params = {'pmids': ','.join(pmids)}
    if full_text:
      params['full'] = 'true'
    result = await _http.get_json(
        f'{_BASE_URL}/publications/export/biocjson', params=params,
    )
    if isinstance(result, dict) and 'PubTator3' in result:
      docs = result['PubTator3']
    elif isinstance(result, list):
      docs = result
    else:
      docs = [result]
    return {'documents': docs, 'count': len(docs)}
  except httpx.HTTPError as e:
    return _err(e, f'annotate_articles: {pmids[:3]}...')


async def lookup_entity_id(
    query: str, concept: str | None = None, limit: int = 5,
) -> dict[str, Any]:
  """Resolve free text to a PubTator3 entity ID (FREE).

  Converts e.g. "metformin" -> "@CHEMICAL_Metformin" so it can be used with
  `find_related_entities`.

  Args:
    query: Free text, e.g. "metformin", "BRCA1", "type 2 diabetes".
    concept: Optional filter from {"gene", "disease", "chemical", "species",
      "mutation"}.
    limit: Max IDs (default 5).
  """
  await _rate_limit()
  try:
    params: dict[str, Any] = {'query': query, 'limit': limit}
    if concept:
      if concept.lower() not in _VALID_CONCEPTS:
        return {'error': True,
                'message': f'concept must be one of {_VALID_CONCEPTS}'}
      params['concept'] = concept.lower()
    results = await _http.get_json(
        f'{_BASE_URL}/entity/autocomplete/', params=params,
    )
    return {
        'query': query, 'concept': concept,
        'results': results,
        'count': len(results) if isinstance(results, list) else 1,
    }
  except httpx.HTTPError as e:
    return _err(e, f'lookup_entity_id: {query}')


async def find_related_entities(
    entity_id: str,
    relation_type: str | None = None,
    target_type: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
  """Find entities related to a given entity via PubTator3 relations (FREE).

  Examples: drugs that treat a disease, genes that interact, chemicals that
  inhibit a target.

  Args:
    entity_id: Must start with "@" (e.g., "@CHEMICAL_Metformin"). Get one
      from `lookup_entity_id`.
    relation_type: Optional from {"treat", "cause", "interact", "associate",
      "prevent", "inhibit", "stimulate", "drug_interact",
      "positive_correlate", "negative_correlate", "cotreat", "convert",
      "compare"}.
    target_type: Optional from {"gene", "disease", "chemical", "variant"}.
    limit: Max relations (default 10).
  """
  if not entity_id.startswith('@'):
    return {
        'error': True,
        'message': "entity_id must start with '@' — call lookup_entity_id first",
    }
  await _rate_limit()
  try:
    params: dict[str, Any] = {'e1': entity_id, 'limit': limit}
    if relation_type:
      if relation_type.lower() not in _VALID_RELATIONS:
        return {'error': True,
                'message': f'relation_type must be one of {_VALID_RELATIONS}'}
      params['type'] = relation_type.lower()
    if target_type:
      if target_type.lower() not in _VALID_TARGETS:
        return {'error': True,
                'message': f'target_type must be one of {_VALID_TARGETS}'}
      params['e2'] = target_type.lower()
    results = await _http.get_json(f'{_BASE_URL}/relations', params=params)
    return {
        'entity_id': entity_id,
        'relation_type': relation_type,
        'target_type': target_type,
        'relations': results,
        'count': len(results) if isinstance(results, list) else 1,
    }
  except httpx.HTTPError as e:
    return _err(e, f'find_related_entities: {entity_id}')
