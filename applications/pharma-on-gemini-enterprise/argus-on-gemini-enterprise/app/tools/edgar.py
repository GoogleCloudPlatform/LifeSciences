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
import json
import logging
import os
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BASE_DELAY = 1.0
_MAX_DELAY = 60.0  # 60 seconds maximum retry delay cap
_RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}

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
_TICKERS_LOCK: asyncio.Lock | None = None


def _get_tickers_lock() -> asyncio.Lock:
    global _TICKERS_LOCK
    if _TICKERS_LOCK is None:
        _TICKERS_LOCK = asyncio.Lock()
    return _TICKERS_LOCK


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


def _parse_retry_after(header_val: str | None) -> float | None:
    if not header_val or not header_val.strip():
        return None
    header_clean = header_val.strip()
    try:
        return float(header_clean)
    except (ValueError, TypeError):
        pass
    try:
        dt = parsedate_to_datetime(header_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (dt - datetime.now(UTC)).total_seconds())
    except Exception:
        return None


def _calculate_backoff(attempt: int) -> float:
    base = _BASE_DELAY * (2**attempt)
    jitter = random.uniform(0, base * 0.25)
    return min(base + jitter, _MAX_DELAY)


async def _get_json(url: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(headers=_HEADERS, timeout=30.0) as client:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.get(url, params=params)
                if resp.status_code in _RETRY_STATUS_CODES and attempt < _MAX_RETRIES:
                    delay = None
                    if resp.status_code in {429, 503}:
                        delay = _parse_retry_after(resp.headers.get("Retry-After"))
                    if delay is None or delay <= 0:
                        delay = _calculate_backoff(attempt)
                    else:
                        delay = min(delay, _MAX_DELAY)
                    logger.warning(
                        "SEC EDGAR API returned HTTP %d for %s. Retrying in %.2fs (attempt %d/%d)...",
                        resp.status_code,
                        url,
                        delay,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue

                resp.raise_for_status()
                try:
                    return resp.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    if attempt < _MAX_RETRIES:
                        delay = _calculate_backoff(attempt)
                        logger.warning(
                            "SEC EDGAR API JSON decode error (%s) for %s. Retrying in %.2fs (attempt %d/%d)...",
                            type(exc).__name__,
                            url,
                            delay,
                            attempt + 1,
                            _MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.error(
                        "SEC EDGAR API JSON decode error (%s) for %s after %d retries.",
                        type(exc).__name__,
                        url,
                        _MAX_RETRIES,
                    )
                    raise
            except httpx.RequestError as exc:
                if attempt < _MAX_RETRIES:
                    delay = _calculate_backoff(attempt)
                    logger.warning(
                        "SEC EDGAR API request error (%s) for %s. Retrying in %.2fs (attempt %d/%d)...",
                        type(exc).__name__,
                        url,
                        delay,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "SEC EDGAR API request error (%s) for %s after %d retries.",
                    type(exc).__name__,
                    url,
                    _MAX_RETRIES,
                )
                raise
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "SEC EDGAR API HTTP error %s for %s after %d retries.",
                    exc.response.status_code if exc.response is not None else "unknown",
                    url,
                    attempt,
                )
                raise


async def _load_tickers() -> list[dict[str, Any]]:
    global _TICKERS_CACHE
    if _TICKERS_CACHE is not None:
        return _TICKERS_CACHE
    async with _get_tickers_lock():
        if _TICKERS_CACHE is not None:
            return _TICKERS_CACHE
        try:
            data = await _get_json("https://www.sec.gov/files/company_tickers.json")
            if isinstance(data, dict):
                _TICKERS_CACHE = list(data.values())
            elif isinstance(data, list):
                _TICKERS_CACHE = data
            else:
                _TICKERS_CACHE = []
            return _TICKERS_CACHE
        except Exception as exc:
            logger.error("Failed to load SEC company tickers: %s", exc)
            return []


def _cik10(cik: int | str | None) -> str:
    if cik is None:
        return ""
    s = str(cik).strip()
    if s.upper().startswith("CIK"):
        s = s[3:].strip()
    if not s or not s.isdigit():
        return ""
    return s.lstrip("0").zfill(10)


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
    try:
        q = query.strip().lower()
        if not q:
            return {"matches": []}
        tickers = await _load_tickers()
        if not tickers:
            if _TICKERS_CACHE is None:
                return {
                    "matches": [],
                    "error": "Failed to load SEC company ticker directory from SEC EDGAR.",
                }
            return {"matches": []}
        matches = []
        for row in tickers:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "")
            title = str(row.get("title") or "")
            if q == ticker.lower() or q in title.lower():
                matches.append(
                    {
                        "cik": _cik10(row.get("cik_str")),
                        "ticker": ticker,
                        "name": title,
                    }
                )
            if len(matches) >= 8:
                break
        return {"matches": matches}
    except Exception as exc:
        logger.error("Error in edgar_find_company for query '%s': %s", query, exc)
        return {
            "matches": [],
            "error": f"SEC company lookup failed: {exc}",
        }


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
    try:
        wanted = (
            {f.strip().upper() for f in forms.split(",") if f.strip()}
            if isinstance(forms, str) and forms.strip()
            else set()
        )
        cik_clean = _cik10(cik)
        if not cik_clean:
            return {
                "name": None,
                "industry": None,
                "fiscal_year_end": None,
                "exchanges": None,
                "filings": [],
                "error": f"Invalid or empty CIK provided: {cik}",
            }
        data = await _get_json(f"https://data.sec.gov/submissions/CIK{cik_clean}.json")
        if not isinstance(data, dict):
            return {
                "name": None,
                "industry": None,
                "fiscal_year_end": None,
                "exchanges": None,
                "filings": [],
                "error": f"Unexpected response from SEC submissions API for CIK {cik}",
            }
        filings_data = (
            data.get("filings") if isinstance(data.get("filings"), dict) else {}
        )
        recent = (
            filings_data.get("recent")
            if isinstance(filings_data.get("recent"), dict)
            else {}
        )
        filings = []
        try:
            cik_int = int(str(cik_clean).lstrip("0") or "0")
        except ValueError:
            cik_int = 0
        for form, filed, accession, primary, desc in zip(
            recent.get("form") or [],
            recent.get("filingDate") or [],
            recent.get("accessionNumber") or [],
            recent.get("primaryDocument") or [],
            recent.get("primaryDocDescription") or [],
        ):
            if not form:
                continue
            if wanted and str(form).upper() not in wanted:
                continue
            acc = str(accession or "").replace("-", "")
            filings.append(
                {
                    "form": form,
                    "filed": filed,
                    "description": desc,
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{primary or ''}",
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
    except Exception as exc:
        logger.error("Error in edgar_recent_filings for CIK '%s': %s", cik, exc)
        return {
            "name": None,
            "industry": None,
            "fiscal_year_end": None,
            "exchanges": None,
            "filings": [],
            "error": f"Failed to retrieve SEC filings for CIK {cik}: {exc}",
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
    try:
        cik_clean = _cik10(cik)
        if not cik_clean:
            return {
                "entity": None,
                "facts": {},
                "error": f"Invalid or empty CIK provided: {cik}",
            }
        data = await _get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_clean}.json"
        )
        if not isinstance(data, dict):
            return {
                "entity": None,
                "facts": {},
                "error": f"Unexpected response from SEC XBRL API for CIK {cik}",
            }
        out: dict[str, Any] = {"entity": data.get("entityName"), "facts": {}}
        facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
        for taxonomy, concepts in _KEY_CONCEPTS.items():
            for concept in concepts:
                tax_dict = (
                    facts.get(taxonomy) if isinstance(facts.get(taxonomy), dict) else {}
                )
                node = tax_dict.get(concept)
                if not node or not isinstance(node, dict):
                    continue
                units = node.get("units") if isinstance(node.get("units"), dict) else {}
                if not units:
                    continue
                unit_name, values = next(iter(units.items()))
                if not isinstance(values, list):
                    continue
                values = sorted(
                    values,
                    key=lambda v: (v.get("end") or "") if isinstance(v, dict) else "",
                    reverse=True,
                )
                seen_periods = set()
                recent = []
                for v in values:
                    if not isinstance(v, dict):
                        continue
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
    except Exception as exc:
        logger.error("Error in edgar_key_financials for CIK '%s': %s", cik, exc)
        return {
            "entity": None,
            "facts": {},
            "error": f"Failed to retrieve SEC XBRL financial facts for CIK {cik}: {exc}",
        }


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
        {company, form, filed, document_id}.
    """
    try:
        params: dict[str, str] = {"q": query}
        if forms:
            params["forms"] = forms
        if date_from:
            params["dateRange"] = "custom"
            params["startdt"] = date_from
            params["enddt"] = "2099-12-31"
        data = await _get_json(
            "https://efts.sec.gov/LATEST/search-index", params=params
        )
        if not isinstance(data, dict):
            return {
                "total": 0,
                "hits": [],
                "error": "Unexpected response from SEC full-text search API",
            }
        hits_data = data.get("hits") or {}
        hits_list = hits_data.get("hits") or []
        total_data = hits_data.get("total") or {}
        if isinstance(total_data, dict):
            total_count = total_data.get("value", 0)
        elif isinstance(total_data, (int, float)):
            total_count = int(total_data)
        else:
            total_count = 0
        hits = []
        for h in hits_list[:15]:
            if not isinstance(h, dict):
                continue
            src = h.get("_source") or {}
            acc_doc = h.get("_id", "")
            display_names = src.get("display_names")
            company = (
                display_names[0]
                if isinstance(display_names, list) and display_names
                else str(display_names or "")
            )
            root_forms = src.get("root_forms")
            form = (
                root_forms[0]
                if isinstance(root_forms, list) and root_forms
                else str(root_forms or "")
            )
            hits.append(
                {
                    "company": company,
                    "form": form,
                    "filed": src.get("file_date"),
                    "document_id": acc_doc,
                }
            )
        return {
            "total": total_count,
            "hits": hits,
        }
    except Exception as exc:
        logger.error("Error in edgar_full_text_search for query '%s': %s", query, exc)
        return {
            "total": 0,
            "hits": [],
            "error": f"SEC full-text search failed: {exc}",
        }
