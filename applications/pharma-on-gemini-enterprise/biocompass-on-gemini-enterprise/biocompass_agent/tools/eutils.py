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

"""NCBI E-utilities client + ADK FunctionTool wrappers (FREE).

Hits PubMed via the public E-utilities REST API. An optional `PUBMED_API_KEY`
env var raises the rate limit from 3 -> 10 req/sec.
"""

from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from . import _http

_EUTILS_BASE_URL = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils'
_DEFAULT_API_KEY = os.environ.get('PUBMED_API_KEY')

# NCBI rate limit: 3 req/sec without key, 10 req/sec with key.
def _sleep_for(api_key: str | None) -> float:
  return 0.11 if api_key else 0.34


def _format_error(error: Exception, context: str) -> dict[str, Any]:
  return {'error': True, 'message': str(error), 'context': context}


# ---------------------------------------------------------------------------
# Low-level HTTP impl.
# ---------------------------------------------------------------------------

async def _esearch(
    query: str, max_results: int, sort: str, api_key: str | None,
) -> dict[str, Any]:
  await asyncio.sleep(_sleep_for(api_key))
  params = {
      'db': 'pubmed',
      'term': query,
      'retmode': 'json',
      'retmax': str(min(max_results, 100)),
      'sort': sort,
  }
  if api_key:
    params['api_key'] = api_key
  return await _http.get_json(f'{_EUTILS_BASE_URL}/esearch.fcgi', params=params)


async def _efetch(pmids: list[str], api_key: str | None) -> str:
  await asyncio.sleep(_sleep_for(api_key))
  params = {
      'db': 'pubmed',
      'id': ','.join(pmids),
      'rettype': 'abstract',
      'retmode': 'xml',
  }
  if api_key:
    params['api_key'] = api_key
  return await _http.get_text(f'{_EUTILS_BASE_URL}/efetch.fcgi', params=params)


async def _elink(
    pmid: str, linkname: str, api_key: str | None,
) -> dict[str, Any]:
  await asyncio.sleep(_sleep_for(api_key))
  params = {
      'dbfrom': 'pubmed',
      'db': 'pubmed',
      'id': pmid,
      'linkname': linkname,
      'retmode': 'json',
  }
  if api_key:
    params['api_key'] = api_key
  return await _http.get_json(f'{_EUTILS_BASE_URL}/elink.fcgi', params=params)


def _parse_pubmed_xml(xml_text: str) -> list[dict[str, Any]]:
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError:
    return []
  return [
      a for node in root.findall('.//PubmedArticle')
      if (a := _parse_article(node)) is not None
  ]


def _parse_article(node: ET.Element) -> dict[str, Any] | None:
  pmid_node = node.find('.//PMID')
  if pmid_node is None or not (pmid_node.text or '').strip():
    return None
  article: dict[str, Any] = {'pmid': pmid_node.text.strip()}

  title_node = node.find('.//ArticleTitle')
  if title_node is not None:
    article['title'] = ''.join(title_node.itertext()).strip()

  abstract_parts: list[str] = []
  for ab in node.findall('.//AbstractText'):
    text = ''.join(ab.itertext()).strip()
    if not text:
      continue
    label = ab.get('Label')
    abstract_parts.append(f'{label}: {text}' if label else text)
  if abstract_parts:
    article['abstract'] = ' '.join(abstract_parts)

  authors: list[str] = []
  author_list = node.find('.//AuthorList')
  if author_list is not None:
    for author in author_list.findall('Author'):
      ln = author.findtext('LastName', default='').strip()
      init = author.findtext('Initials', default='').strip()
      if ln:
        authors.append(f'{ln} {init}'.strip())
  article['authors'] = authors[:10]

  journal = node.findtext('.//Journal/Title', default='').strip()
  if journal:
    article['journal'] = journal

  pub_date = node.find('.//Journal/JournalIssue/PubDate')
  if pub_date is not None:
    year = pub_date.findtext('Year', default='').strip()
    if not year:
      medline = pub_date.findtext('MedlineDate', default='')
      m = re.search(r'\d{4}', medline)
      if m:
        year = m.group()
    if year:
      article['year'] = year

  article['pubmed_url'] = f'https://pubmed.ncbi.nlm.nih.gov/{article["pmid"]}/'
  return article


async def _fetch_articles(
    pmids: list[str], api_key: str | None,
) -> list[dict[str, Any]]:
  if not pmids:
    return []
  return _parse_pubmed_xml(await _efetch(pmids, api_key))


# ---------------------------------------------------------------------------
# ADK FunctionTool wrappers — these are what the LLM sees. Keep docstrings
# precise; ADK feeds the docstring + signature into the tool schema.
# ---------------------------------------------------------------------------

async def search_pubmed(
    query: str, max_results: int = 10, sort: str = 'relevance',
) -> dict[str, Any]:
  """Search PubMed via NCBI E-utilities (FREE).

  Use for free-text queries. Supports PubMed query syntax with field tags.

  Args:
    query: PubMed query, e.g. "GLP-1 agonists AND HbA1c", "CRISPR[Title]",
      "cancer immunotherapy AND 2023:2025[PDAT]".
    max_results: 1-100 (default 10).
    sort: "relevance" or "date".

  Returns:
    `{query, total_results, returned_results, articles: [{pmid, title,
    abstract, authors, journal, year, pubmed_url}, ...]}`.
  """
  api_key = _DEFAULT_API_KEY
  try:
    search = await _esearch(query, max_results, sort, api_key)
    pmids = search.get('esearchresult', {}).get('idlist', [])
    total = int(search.get('esearchresult', {}).get('count', 0))
    articles = await _fetch_articles(pmids, api_key)
    return {
        'query': query,
        'total_results': total,
        'returned_results': len(articles),
        'articles': articles,
    }
  except httpx.HTTPError as e:
    return _format_error(e, f'search_pubmed: {query}')


async def search_by_author(
    author_name: str, max_results: int = 10,
) -> dict[str, Any]:
  """Search PubMed by author (FREE).

  Args:
    author_name: e.g. "Doudna J", "Topol EJ".
    max_results: 1-100 (default 10).
  """
  return await search_pubmed(
      f'{author_name}[Author]', max_results=max_results, sort='date',
  )


async def get_article(pmid: str) -> dict[str, Any]:
  """Fetch a single PubMed article by PMID (FREE).

  Args:
    pmid: PubMed ID, e.g. "28375731".

  Returns:
    `{article: {pmid, title, abstract, ...}}` or `{error, message}`.
  """
  try:
    articles = await _fetch_articles([pmid], _DEFAULT_API_KEY)
    if articles:
      return {'article': articles[0]}
    return {'error': True, 'message': f'Article not found: {pmid}'}
  except httpx.HTTPError as e:
    return _format_error(e, f'get_article: {pmid}')


_PUB_TYPE_MAP = {
    'review': 'Review[PT]',
    'clinical trial': 'Clinical Trial[PT]',
    'meta-analysis': 'Meta-Analysis[PT]',
    'randomized controlled trial': 'Randomized Controlled Trial[PT]',
    'systematic review': 'Systematic Review[PT]',
    'case reports': 'Case Reports[PT]',
    'editorial': 'Editorial[PT]',
    'letter': 'Letter[PT]',
}


async def advanced_search(
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    publication_types: list[str] | None = None,
    mesh_terms: list[str] | None = None,
    journal: str | None = None,
    title_only: bool = False,
    max_results: int = 10,
    sort: str = 'relevance',
) -> dict[str, Any]:
  """PubMed advanced search with date / pub-type / MeSH / journal filters (FREE).

  Args:
    query: Base query (optional if other filters supplied).
    date_from: "YYYY" or "YYYY/MM/DD".
    date_to: "YYYY" or "YYYY/MM/DD".
    publication_types: from {"review", "clinical trial", "meta-analysis",
      "randomized controlled trial", "systematic review", "case reports",
      "editorial", "letter"}.
    mesh_terms: e.g. ["Diabetes Mellitus, Type 2", "Metformin"].
    journal: Journal name or NLM abbreviation.
    title_only: If True, restrict the base query to titles.
    max_results: 1-100 (default 10).
    sort: "relevance" or "date".
  """
  parts: list[str] = []
  if query:
    parts.append(f'({query}[Title])' if title_only else f'({query})')
  if date_from and date_to:
    parts.append(f'({date_from}:{date_to}[PDAT])')
  elif date_from:
    parts.append(f'({date_from}:3000[PDAT])')
  elif date_to:
    parts.append(f'(1800:{date_to}[PDAT])')
  if publication_types:
    pts = [_PUB_TYPE_MAP[p.lower()] for p in publication_types
           if p.lower() in _PUB_TYPE_MAP]
    if pts:
      parts.append(f'({" OR ".join(pts)})')
  if mesh_terms:
    parts.append('(' + ' AND '.join(f'"{m}"[MeSH]' for m in mesh_terms) + ')')
  if journal:
    parts.append(f'("{journal}"[Journal])')
  if not parts:
    return {'error': True, 'message': 'At least one filter required'}
  return await search_pubmed(' AND '.join(parts), max_results, sort)


async def get_citing_articles(
    pmid: str, max_results: int = 20,
) -> dict[str, Any]:
  """Find articles that cite a given PMID (FREE forward citation search).

  Args:
    pmid: PubMed ID.
    max_results: Max citing articles (default 20).
  """
  api_key = _DEFAULT_API_KEY
  try:
    data = await _elink(pmid, 'pubmed_pubmed_citedin', api_key)
    linksets = data.get('linksets', [])
    if not linksets:
      return {'pmid': pmid, 'citing_articles': [], 'total_results': 0}
    citing_pmids: list[str] = []
    for db in linksets[0].get('linksetdbs', []):
      if db.get('linkname') == 'pubmed_pubmed_citedin':
        citing_pmids = [str(x) for x in db.get('links', [])[:max_results]]
        break
    articles = await _fetch_articles(citing_pmids, api_key)
    return {
        'pmid': pmid,
        'total_results': len(articles),
        'citing_articles': articles,
    }
  except httpx.HTTPError as e:
    return _format_error(e, f'get_citing_articles: {pmid}')
