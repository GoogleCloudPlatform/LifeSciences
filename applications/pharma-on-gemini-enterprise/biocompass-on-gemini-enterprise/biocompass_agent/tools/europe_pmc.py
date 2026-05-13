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

"""Europe PMC RESTful API client + ADK FunctionTool wrappers (FREE).

Europe PMC indexes the same biomedical literature as PubMed PLUS preprints,
patents, and agriculture/life-sciences sources, and exposes free full-text
for the open-access subset (no BigQuery provisioning required, unlike the
PMC OA mirror).

Docs: https://europepmc.org/RestfulWebService
"""

from __future__ import annotations

from typing import Any

import httpx

from . import _http

_BASE_URL = 'https://www.ebi.ac.uk/europepmc/webservices/rest'


def _err(e: Exception, ctx: str) -> dict[str, Any]:
  return {'error': True, 'message': str(e), 'context': ctx}


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
  pmid = record.get('pmid')
  pmcid = record.get('pmcid')
  doi = record.get('doi')
  out: dict[str, Any] = {
      'source': record.get('source'),
      'id': record.get('id'),
      'title': record.get('title'),
      'authors': record.get('authorString'),
      'journal': record.get('journalTitle'),
      'year': record.get('pubYear'),
      'abstract': record.get('abstractText'),
      'has_full_text': record.get('hasTextMinedTerms') == 'Y'
                       or record.get('isOpenAccess') == 'Y',
      'is_open_access': record.get('isOpenAccess') == 'Y',
  }
  if pmid:
    out['pmid'] = pmid
    out['pubmed_url'] = f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'
  if pmcid:
    out['pmcid'] = pmcid
    out['pmc_url'] = f'https://europepmc.org/article/PMC/{pmcid.removeprefix("PMC")}'
  if doi:
    out['doi'] = doi
  return out


async def search_europe_pmc(
    query: str,
    max_results: int = 25,
    open_access_only: bool = False,
    include_preprints: bool = True,
) -> dict[str, Any]:
  """Search Europe PMC across PubMed + preprints + patents (FREE).

  Use when you want broader coverage than PubMed alone (preprints) or when
  you'll want full-text for screening (open-access subset).

  Args:
    query: Europe PMC query, supports field tags like AUTH:, TITLE:, ABS:,
      KW:, JOURNAL:, MESH:, PUB_YEAR:, e.g. "TITLE:CRISPR AND PUB_YEAR:[2023 TO 2025]".
    max_results: 1-100 (default 25).
    open_access_only: If True, restrict to open-access full-text articles.
    include_preprints: If False, exclude bioRxiv/medRxiv/research-square.

  Returns:
    `{query, hit_count, articles: [{source, id, pmid, pmcid, doi, title,
    authors, journal, year, abstract, has_full_text, is_open_access,
    pmc_url, pubmed_url}, ...]}`.
  """
  q = query
  if open_access_only:
    q = f'({q}) AND OPEN_ACCESS:Y'
  if not include_preprints:
    q = f'({q}) AND SRC:MED'
  params = {
      'query': q,
      'format': 'json',
      'pageSize': str(min(max(max_results, 1), 100)),
      'resultType': 'core',
  }
  try:
    data = await _http.get_json(f'{_BASE_URL}/search', params=params)
    result = data.get('resultList', {}).get('result', [])
    return {
        'query': q,
        'hit_count': data.get('hitCount', 0),
        'returned_results': len(result),
        'articles': [_normalize(r) for r in result],
    }
  except httpx.HTTPError as e:
    return _err(e, f'search_europe_pmc: {query}')


async def get_europe_pmc_fulltext(pmcid: str) -> dict[str, Any]:
  """Fetch the open-access full text of an article from Europe PMC (FREE).

  Args:
    pmcid: A PMC identifier with or without the "PMC" prefix (e.g. "PMC10500001"
      or "10500001"). Only works for open-access articles.

  Returns:
    `{pmcid, full_text}` (XML body) or `{error, message}`.
  """
  pmc_norm = pmcid if pmcid.startswith('PMC') else f'PMC{pmcid}'
  try:
    resp = await _http.get_response(
        f'{_BASE_URL}/{pmc_norm}/fullTextXML', timeout=60.0,
    )
    if resp.status_code == 404:
      return {
          'error': True, 'pmcid': pmc_norm,
          'message': 'No open-access full text available.',
      }
    resp.raise_for_status()
    return {'pmcid': pmc_norm, 'full_text': resp.text}
  except httpx.HTTPError as e:
    return _err(e, f'get_europe_pmc_fulltext: {pmc_norm}')
