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

"""Tools-only smoke test. No LLM, no token cost.

Hits the free public APIs (NCBI E-utilities, PubTator3, Europe PMC,
ClinicalTrials.gov) end-to-end and asserts each returns a sensible payload.
Useful as a CI-style check that the network paths, retry decorator, shared
client, and HTML/JSON parsers all still work after a refactor.

Run from the agent project root via pytest:

    uv run pytest tests/integration/test_smoke.py -v
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.tools.biorxiv import search_preprints
from app.tools.clinicaltrials import (
    get_clinical_trial,
    search_clinical_trials,
)
from app.tools.europe_pmc import search_europe_pmc
from app.tools.eutils import (
    advanced_search,
    get_article,
    get_citing_articles,
    search_by_author,
    search_pubmed,
)
from app.tools.pubtator import (
    annotate_articles,
    find_related_entities,
    lookup_entity_id,
)


@pytest.fixture(autouse=True)
async def _cleanup_http_client():
    import app.tools._http

    yield
    if app.tools._http._client is not None:
        await app.tools._http._client.aclose()
        app.tools._http._client = None


def _ok_articles(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload, dict)
        and not payload.get("error")
        and isinstance(payload.get("articles"), list)
        and len(payload["articles"]) > 0
    )


def _ok_studies(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload, dict)
        and not payload.get("error")
        and isinstance(payload.get("studies"), list)
        and len(payload["studies"]) > 0
    )


CASES: list[tuple[str, Callable[[], Awaitable[Any]], Callable[[Any], bool]]] = [
    # --- E-utilities ---
    (
        "search_pubmed: GLP-1 obesity",
        lambda: search_pubmed("GLP-1 AND obesity", max_results=5),
        _ok_articles,
    ),
    (
        "advanced_search: HER2 reviews 2024",
        lambda: advanced_search(
            query="HER2", date_from="2024", publication_types=["review"], max_results=5
        ),
        _ok_articles,
    ),
    (
        "search_by_author: Doudna J",
        lambda: search_by_author("Doudna J", max_results=3),
        _ok_articles,
    ),
    (
        "get_article: PMID 28375731",
        lambda: get_article("28375731"),
        lambda r: (
            isinstance(r, dict) and r.get("article", {}).get("pmid") == "28375731"
        ),
    ),
    (
        "get_citing_articles: PMID 28375731",
        lambda: get_citing_articles("28375731", max_results=3),
        lambda r: isinstance(r, dict) and isinstance(r.get("citing_articles"), list),
    ),
    # --- PubTator3 ---
    (
        "lookup_entity_id: metformin",
        lambda: lookup_entity_id("metformin", concept="chemical", limit=3),
        lambda r: isinstance(r, dict) and (r.get("count") or 0) > 0,
    ),
    (
        "annotate_articles: PMID 32133824",
        lambda: annotate_articles(["32133824"]),
        lambda r: isinstance(r, dict) and (r.get("count") or 0) > 0,
    ),
    (
        "find_related_entities: @CHEMICAL_Metformin treats",
        lambda: find_related_entities(
            "@CHEMICAL_Metformin", relation_type="treat", limit=3
        ),
        lambda r: isinstance(r, dict) and isinstance(r.get("relations"), (list, dict)),
    ),
    # --- Europe PMC ---
    (
        "search_europe_pmc: CRISPR 2024",
        lambda: search_europe_pmc(
            "TITLE:CRISPR AND PUB_YEAR:[2024 TO 2024]", max_results=5
        ),
        _ok_articles,
    ),
    # --- bioRxiv via Europe PMC ---
    (
        "search_preprints: AlphaFold",
        lambda: search_preprints("AlphaFold", max_results=3),
        _ok_articles,
    ),
    # --- ClinicalTrials.gov ---
    (
        "search_clinical_trials: tirzepatide obesity Phase 3",
        lambda: search_clinical_trials(
            intervention="tirzepatide",
            condition="obesity",
            phase="PHASE3",
            max_results=5,
        ),
        _ok_studies,
    ),
    (
        "get_clinical_trial: NCT04267848",
        lambda: get_clinical_trial("NCT04267848"),
        lambda r: (
            isinstance(r, dict) and r.get("study", {}).get("nct_id") == "NCT04267848"
        ),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call, check, label",
    [pytest.param(call, check, label, id=label) for label, call, check in CASES],
)
async def test_smoke(
    call: Callable[[], Awaitable[Any]],
    check: Callable[[Any], bool],
    label: str,
) -> None:
    """Run the public API smoke checks via pytest."""
    try:
        result = await call()
    except Exception as exc:
        raise AssertionError(
            f"Check '{label}' failed because it raised: {exc}"
        ) from exc
    assert check(result), f"Check '{label}' failed. Result payload: {result}"
