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

"""SEC EDGAR tools for financial due diligence on US-listed acquisition targets.

Uses the free SEC EDGAR APIs (data.sec.gov / efts.sec.gov). The SEC requires a
descriptive User-Agent; set EDGAR_USER_AGENT in the environment.
"""

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BASE_DELAY = 1.0
_MAX_DELAY = 16.0

_USER_AGENT = os.environ.get("EDGAR_USER_AGENT")
if not _USER_AGENT or not _USER_AGENT.strip():
    _error_msg = (
        "EDGAR_USER_AGENT environment variable is not set. "
        "The SEC requires a descriptive User-Agent header in the format: "
        "'Sample Company Name AdminContact@<sample company domain>.com'. "
        "Set EDGAR_USER_AGENT in your environment or .env file."
    )
    logger.error(_error_msg)
    raise ValueError(_error_msg)

_HEADERS = {"User-Agent": _USER_AGENT.strip(), "Accept-Encoding": "gzip, deflate"}

_TICKERS_CACHE: list[dict[str, Any]] | None = None

# XBRL concepts most relevant to biotech/pharma diligence (cash runway, burn,
# pipeline spend). Keyed by taxonomy.
_KEY_CONCEPTS = {
    "us-gaap": [
        "CashAndCashEquivalentsAtCarryingValue",
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "ResearchAndDevelopmentExpense",
        "GeneralAndAdministrativeExpense",
        "OperatingIncomeLoss",
        "NetIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "Assets",
        "Liabilities",
        "StockholdersEquity",
        "LongTermDebt",
    ],
    "dei": ["EntityCommonStockSharesOutstanding", "EntityPublicFloat"],
}


async def _get_json(url: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(headers=_HEADERS, timeout=30.0) as client:
        for attempt in range(_MAX_RETRIES + 1):
            resp = await client.get(url, params=params)
            if resp.status_code == 429 and attempt < _MAX_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                delay = None
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except (ValueError, TypeError):
                        delay = None
                if delay is None or delay <= 0:
                    delay = min(_BASE_DELAY * (2**attempt), _MAX_DELAY)
                logger.warning(
                    "SEC EDGAR API rate limited (429) for %s. Retrying in %.2fs (attempt %d/%d)...",
                    url,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()


async def _load_tickers() -> list[dict[str, Any]]:
    global _TICKERS_CACHE
    if _TICKERS_CACHE is None:
        data = await _get_json("https://www.sec.gov/files/company_tickers.json")
        _TICKERS_CACHE = list(data.values())
    return _TICKERS_CACHE


def _cik10(cik: int | str) -> str:
    return str(cik).lstrip("0").zfill(10) if str(cik).strip() else ""


async def edgar_find_company(query: str) -> dict:
    """Find a US-listed company's SEC identifiers (CIK, ticker) by name or ticker.

    Use this first to resolve a company before calling other EDGAR tools.

    Args:
        query: Company name (or part of it) or stock ticker, e.g. "Mersana"
            or "MRSN".

    Returns:
        dict with a "matches" list of {cik, ticker, name}; empty list if the
        company is not SEC-registered (i.e. likely private or foreign-listed).
    """
    q = query.strip().lower()
    tickers = await _load_tickers()
    matches = []
    for row in tickers:
        if q == row["ticker"].lower() or q in row["title"].lower():
            matches.append(
                {
                    "cik": _cik10(row["cik_str"]),
                    "ticker": row["ticker"],
                    "name": row["title"],
                }
            )
        if len(matches) >= 8:
            break
    return {"matches": matches}


async def edgar_recent_filings(
    cik: str, forms: str = "10-K,10-Q,8-K,S-1,DEF 14A"
) -> dict:
    """List a company's most recent SEC filings with links to the documents.

    Args:
        cik: 10-digit CIK from edgar_find_company, e.g. "0001442836".
        forms: Comma-separated form types to include. Defaults to the forms
            most useful for M&A diligence (10-K, 10-Q, 8-K, S-1, DEF 14A).

    Returns:
        dict with company metadata (name, sic description, fiscal year end)
        and a "filings" list of {form, filed, description, url} (up to 25).
    """
    wanted = {f.strip().upper() for f in forms.split(",") if f.strip()}
    data = await _get_json(f"https://data.sec.gov/submissions/CIK{_cik10(cik)}.json")
    recent = data.get("filings", {}).get("recent", {})
    filings = []
    for form, filed, accession, primary, desc in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
        recent.get("primaryDocDescription", []),
    ):
        if wanted and form.upper() not in wanted:
            continue
        acc = accession.replace("-", "")
        filings.append(
            {
                "form": form,
                "filed": filed,
                "description": desc,
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary}",
            }
        )
        if len(filings) >= 25:
            break
    return {
        "name": data.get("name"),
        "industry": data.get("sicDescription"),
        "fiscal_year_end": data.get("fiscalYearEnd"),
        "exchanges": data.get("exchanges"),
        "filings": filings,
    }


async def edgar_key_financials(cik: str) -> dict:
    """Pull key XBRL financial facts for a company: cash, revenue, R&D spend,
    operating loss, net loss, operating cash flow, balance-sheet totals and
    shares outstanding — the inputs for burn-rate and cash-runway analysis.

    Args:
        cik: 10-digit CIK from edgar_find_company.

    Returns:
        dict mapping concept name to its most recent annual and quarterly
        reported values: {concept: {"unit": ..., "recent": [{val, end, fy,
        fp, form, filed}, ...]}}.
    """
    data = await _get_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_cik10(cik)}.json"
    )
    out: dict[str, Any] = {"entity": data.get("entityName"), "facts": {}}
    facts = data.get("facts", {})
    for taxonomy, concepts in _KEY_CONCEPTS.items():
        for concept in concepts:
            node = facts.get(taxonomy, {}).get(concept)
            if not node:
                continue
            units = node.get("units", {})
            if not units:
                continue
            unit_name, values = next(iter(units.items()))
            values = sorted(values, key=lambda v: v.get("end", ""), reverse=True)
            seen_periods = set()
            recent = []
            for v in values:
                key = (v.get("end"), v.get("fp"))
                if key in seen_periods:
                    continue
                seen_periods.add(key)
                recent.append(
                    {
                        "val": v.get("val"),
                        "end": v.get("end"),
                        "start": v.get("start"),
                        "fy": v.get("fy"),
                        "fp": v.get("fp"),
                        "form": v.get("form"),
                    }
                )
                if len(recent) >= 6:
                    break
            out["facts"][concept] = {"unit": unit_name, "recent": recent}
    return out


async def edgar_full_text_search(
    query: str, forms: str = "", date_from: str = ""
) -> dict:
    """Full-text search across recent SEC filings (2001+). Useful for finding
    which companies discuss a technology, target, or licensing deal — e.g.
    '"antibody-drug conjugate" AND "topoisomerase"'.

    Args:
        query: Search query. Use double quotes for exact phrases.
        forms: Optional comma-separated form filter, e.g. "10-K,8-K".
        date_from: Optional earliest filing date, "YYYY-MM-DD".

    Returns:
        dict with total hit count and "hits" list of
        {company, cik, form, filed, snippet_url}.
    """
    params: dict[str, str] = {"q": query}
    if forms:
        params["forms"] = forms
    if date_from:
        params["dateRange"] = "custom"
        params["startdt"] = date_from
        params["enddt"] = "2099-12-31"
    data = await _get_json("https://efts.sec.gov/LATEST/search-index", params=params)
    hits = []
    for h in data.get("hits", {}).get("hits", [])[:15]:
        src = h.get("_source", {})
        acc_doc = h.get("_id", "")
        hits.append(
            {
                "company": (src.get("display_names") or [""])[0],
                "form": (src.get("root_forms") or [""])[0],
                "filed": src.get("file_date"),
                "document_id": acc_doc,
            }
        )
    return {
        "total": data.get("hits", {}).get("total", {}).get("value", 0),
        "hits": hits,
    }
