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

"""bioRxiv / medRxiv preprint search via Europe PMC index (FREE).

The bioRxiv native API only supports DOI/date queries, not free-text.
Europe PMC indexes preprints with the same field-tag query syntax it uses for
journal articles, so we reuse it filtered to PPR (preprint) sources.
"""

from __future__ import annotations

from typing import Any

from .europe_pmc import search_europe_pmc


async def search_preprints(
    query: str,
    server: str = 'all',
    max_results: int = 25,
) -> dict[str, Any]:
  """Search bioRxiv / medRxiv preprints via the Europe PMC preprint index (FREE).

  Use when you need the very latest results — preprints often beat the
  peer-reviewed literature by 6-18 months for fast-moving fields.

  Args:
    query: Free-text or Europe PMC field-tag query, e.g. "GLP-1 AND obesity"
      or "TITLE:CRISPR".
    server: "biorxiv", "medrxiv", or "all" (default).
    max_results: 1-100 (default 25).

  Returns:
    `{query, hit_count, articles: [...]}` (same shape as `search_europe_pmc`,
    `source` field will be "PPR").
  """
  src_filter = {
      'biorxiv': 'AND PUBLISHER:"bioRxiv"',
      'medrxiv': 'AND PUBLISHER:"medRxiv"',
      'all': '',
  }.get(server.lower(), '')
  preprint_query = f'({query}) AND SRC:PPR {src_filter}'.strip()
  return await search_europe_pmc(
      preprint_query, max_results=max_results, include_preprints=True,
  )
