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

"""`dde pubmed` -- keyword-based PubMed literature search and full-text retrieval.

Where `litref` asks "does this *known* citation exist?", this tool asks
"what publications match this *query*?" -- the discovery half of the
literature workflow.  It searches PubMed via the NCBI E-utilities and
returns structured records with title, authors, journal, year, abstract,
DOI and MeSH terms.

Three subcommands:

  search    queries PubMed (esearch + efetch) and writes the verbatim
            responses to Layer 0 with a sidecar.
  analyze   reads those responses and produces a summary analysis: year
            distribution, journal distribution, top MeSH terms. No network.
  fulltext  fetches the full-text XML of a PubMed Central article by
            PMCID and writes a structured record with title, authors,
            abstract, body sections, references, DOI and PMID.

A keyword search is inherently non-exhaustive: relevant publications may
use different terminology, be indexed under different MeSH headings, or
not yet be indexed.  The relay `pubmed.search_not_exhaustive` carries
this caveat into the finding so a report cannot present search results as
a complete literature survey.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import click

from ..common import (
    AppState,
    emitter,
    from_option,
    out_option,
    output_options,
    pass_state,
)
from ..core import http, provenance
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.ncbi import api_key_suffix
from ..core.qps import qps_for_host

TOOL = "pubmed"
ARTIFACT_CLASS = "literature"  # same as litref -- both produce literature artifacts

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "pubmed-search"


def _parse_articles(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse PubMed efetch XML into structured article records.

    Uses xml.etree.ElementTree (stdlib only -- no lxml dependency).
    Gracefully handles missing fields; every record gets at least a PMID.
    """
    results: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SchemaError(
            "PubMed efetch response is not valid XML", detail=str(exc)
        ) from exc

    for article_el in root.findall(".//PubmedArticle"):
        citation = article_el.find(".//MedlineCitation")
        if citation is None:
            continue

        pmid_el = citation.find("PMID")
        pmid = pmid_el.text if pmid_el is not None else None
        if not pmid:
            continue

        article = citation.find("Article")
        if article is None:
            results.append(
                {
                    "pmid": pmid,
                    "title": None,
                    "authors": [],
                    "journal": None,
                    "year": None,
                    "abstract": None,
                    "doi": None,
                    "mesh_terms": [],
                }
            )
            continue

        # Title
        title_el = article.find("ArticleTitle")
        title = title_el.text if title_el is not None else None

        # Authors
        authors: list[str] = []
        author_list = article.find("AuthorList")
        if author_list is not None:
            for author_el in author_list.findall("Author"):
                last = author_el.find("LastName")
                initials = author_el.find("Initials")
                if last is not None and last.text:
                    name = last.text
                    if initials is not None and initials.text:
                        name += f" {initials.text}"
                    authors.append(name)
                else:
                    # Collective/corporate author
                    collective = author_el.find("CollectiveName")
                    if collective is not None and collective.text:
                        authors.append(collective.text)

        # Journal
        journal_el = article.find("Journal")
        journal = None
        if journal_el is not None:
            j_title = journal_el.find("ISOAbbreviation")
            if j_title is None:
                j_title = journal_el.find("Title")
            if j_title is not None:
                journal = j_title.text

        # Year -- check multiple locations
        year = None
        pub_date = article.find("Journal/JournalIssue/PubDate")
        if pub_date is not None:
            year_el = pub_date.find("Year")
            if year_el is not None:
                year = year_el.text
            elif pub_date.find("MedlineDate") is not None:
                # MedlineDate is a free-text field like "2024 Jan-Feb"
                md = pub_date.find("MedlineDate").text or ""
                match = re.match(r"(\d{4})", md)
                if match:
                    year = match.group(1)

        # Abstract
        abstract = None
        abstract_el = article.find("Abstract")
        if abstract_el is not None:
            parts = []
            for text_el in abstract_el.findall("AbstractText"):
                label = text_el.get("Label")
                # ElementTree .text doesn't include tail/children text;
                # itertext() collects everything under the element.
                text = "".join(text_el.itertext()).strip()
                if text:
                    if label:
                        parts.append(f"{label}: {text}")
                    else:
                        parts.append(text)
            abstract = " ".join(parts) if parts else None

        # DOI
        doi = None
        for eid in article.findall("ELocationID"):
            if eid.get("EIdType") == "doi" and eid.text:
                doi = eid.text
                break
        if doi is None:
            # Fallback: check ArticleIdList in PubmedData
            pubmed_data = article_el.find("PubmedData")
            if pubmed_data is not None:
                for aid in pubmed_data.findall(".//ArticleId"):
                    if aid.get("IdType") == "doi" and aid.text:
                        doi = aid.text
                        break

        # MeSH terms
        mesh_terms: list[str] = []
        mesh_list = citation.find("MeshHeadingList")
        if mesh_list is not None:
            for heading in mesh_list.findall("MeshHeading"):
                descriptor = heading.find("DescriptorName")
                if descriptor is not None and descriptor.text:
                    mesh_terms.append(descriptor.text)

        results.append(
            {
                "pmid": pmid,
                "title": title,
                "authors": authors,
                "journal": journal,
                "year": year,
                "abstract": abstract,
                "doi": doi,
                "mesh_terms": mesh_terms,
            }
        )

    return results


def _per_term_hit_counts(query: str) -> list[dict[str, Any]]:
    """Run count-only esearch for each individual term in *query*.

    Returns a list of ``{"term": ..., "count": int}`` dicts, one per
    whitespace-delimited token.  Each request uses ``rettype=count`` so
    no article data is fetched — only the total count field.  This lets
    the caller see which term(s) collapsed the combined result set.
    """
    # Split on whitespace; skip PubMed Boolean operators.
    tokens = [t for t in query.split() if t.upper() not in ("AND", "OR", "NOT")]
    if not tokens:
        return []

    qps = qps_for_host("eutils.ncbi.nlm.nih.gov")
    results: list[dict[str, Any]] = []
    for token in tokens:
        count_url = (
            f"{EUTILS_BASE}/esearch.fcgi?db=pubmed"
            f"&term={quote_plus(token)}"
            f"&rettype=count"
            f"&retmode=json" + api_key_suffix()
        )
        try:
            data = http.get_json(count_url, qps=qps, timeout=30.0)
            count = int((data.get("esearchresult") or {}).get("count", 0))
        except Exception:
            count = -1  # failed to retrieve
        results.append({"term": token, "count": count})

    return results


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def pubmed() -> None:
    """PubMed literature search."""


@pubmed.command("search")
@click.argument("query")
@click.option(
    "--max-results",
    default=20,
    type=click.IntRange(1, 100),
    help="Number of results to return (1-100, default 20).",
)
@click.option(
    "--sort",
    type=click.Choice(["relevance", "date"]),
    default="relevance",
    help="Sort order (default: relevance).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    max_results: int,
    sort: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search PubMed for QUERY and write structured results.

    QUERY may use PubMed search syntax: keywords, MeSH terms, Boolean
    operators (AND, OR, NOT), field tags ([Title], [Author], etc.).
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    # Step 1: esearch -- get PMIDs matching the query
    esearch_url = (
        f"{EUTILS_BASE}/esearch.fcgi?db=pubmed"
        f"&term={quote_plus(query)}"
        f"&retmax={max_results}"
        f"&sort={sort}"
        f"&retmode=json" + api_key_suffix()
    )
    esearch_data = http.get_json(
        esearch_url, qps=qps_for_host("eutils.ncbi.nlm.nih.gov"), timeout=60.0
    )

    esearch_result = esearch_data.get("esearchresult")
    if not isinstance(esearch_result, dict):
        raise SchemaError(
            "PubMed esearch response has no esearchresult",
            detail=json.dumps(esearch_data)[:500],
        )

    # Check for query errors
    error_list = esearch_result.get("errorlist")
    if error_list:
        phrases = error_list.get("phrasesnotfound", [])
        fields = error_list.get("fieldsnotfound", [])
        detail_parts = []
        if phrases:
            detail_parts.append(f"phrases not found: {', '.join(phrases)}")
        if fields:
            detail_parts.append(f"fields not found: {', '.join(fields)}")
        # Only refuse if there are no results at all AND there are errors
        if not esearch_result.get("idlist"):
            raise Refusal(
                f"PubMed could not parse the query: {query!r}",
                detail="; ".join(detail_parts) if detail_parts else None,
                remedy="check the query syntax; see PubMed help for valid field tags and operators",
            )

    pmids = esearch_result.get("idlist", [])
    total_found = int(esearch_result.get("count", 0))
    query_translation = esearch_result.get("querytranslation", "")

    # Save verbatim esearch response
    esearch_path = target_dir / f"{slug}.esearch.json"
    esearch_path.write_text(json.dumps(esearch_data, indent=2) + "\n", encoding="utf-8")

    # Step 2: efetch -- get full records for the PMIDs
    articles: list[dict[str, Any]] = []
    efetch_path = target_dir / f"{slug}.efetch.xml"

    # Per-term diagnostics when zero results (populated below).
    per_term_counts: list[dict[str, Any]] | None = None

    if pmids:
        pmid_list = ",".join(pmids)
        efetch_url = (
            f"{EUTILS_BASE}/efetch.fcgi?db=pubmed"
            f"&id={pmid_list}"
            f"&retmode=xml"
            f"&rettype=abstract" + api_key_suffix()
        )
        efetch_bytes = http.get_bytes(
            efetch_url, qps=qps_for_host("eutils.ncbi.nlm.nih.gov"), timeout=60.0
        )
        efetch_path.write_bytes(efetch_bytes)
        articles = _parse_articles(efetch_bytes)
    else:
        # Write an empty XML placeholder so the file exists for analyze
        efetch_path.write_text(
            '<?xml version="1.0" ?>\n<PubmedArticleSet/>\n', encoding="utf-8"
        )
        # Zero results: run per-term diagnostics to help identify
        # whether the query was overconstrained vs genuinely empty.
        per_term_counts = _per_term_hit_counts(query)

    # Step 3: Build structured output artifact
    artifact: dict[str, Any] = {
        "schema": "dde.pubmed-search.v1",
        "query": {
            "terms": query,
            "max_results": max_results,
            "sort": sort,
            "total_found": total_found,
            "query_translation": query_translation,
        },
        "results": articles,
    }
    # Attach per-term diagnostics when the search returned nothing.
    if per_term_counts is not None:
        artifact["per_term_counts"] = per_term_counts
        # Distinguish genuinely empty from overconstrained: if any
        # individual term has hits, the combination was overconstrained.
        any_term_has_hits = any(t["count"] > 0 for t in per_term_counts)
        artifact["zero_result_reason"] = (
            "no_results_query_may_be_overconstrained"
            if any_term_has_hits
            else "no_results"
        )

    artifact_path = target_dir / f"{slug}.pubmed-search.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    # Step 4: Sidecar
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=EUTILS_BASE,
        parameters={
            "query": query,
            "max_results": max_results,
            "sort": sort,
        },
    )
    sidecar.note("source", "PubMed (NCBI/NLM)")
    sidecar.note("licence", "NLM Terms of Service -- abstracts only")
    sidecar.note("n_results", str(len(articles)))
    sidecar.note("total_found", str(total_found))

    sidecar.add_output(esearch_path)
    sidecar.add_output(efetch_path)
    sidecar.add_output(artifact_path)

    meta_path = sidecar.write(target_dir / f"{slug}.meta.json")

    # Emit
    emit.data("query", query)
    emit.data("total_found", total_found)
    emit.data("n_results", len(articles))
    emit.path(esearch_path, role="esearch")
    emit.path(efetch_path, role="efetch")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"PubMed search: {query!r}")
    emit.line(f"  {total_found} total found, {len(articles)} retrieved")
    if articles:
        for a in articles[:5]:
            emit.line(f"  PMID {a['pmid']}: {(a.get('title') or '(no title)')[:70]}")
        if len(articles) > 5:
            emit.line(f"  ... {len(articles) - 5} more in the artifact")
    if per_term_counts is not None:
        emit.line(f"Zero results for {query!r}.")
        emit.line("Per-term hit counts:")
        for entry in per_term_counts:
            count_str = f"{entry['count']:,}" if entry["count"] >= 0 else "error"
            emit.line(f"  {entry['term']:<30s} → {count_str}")
        reason = artifact.get("zero_result_reason", "no_results")
        if reason == "no_results_query_may_be_overconstrained":
            emit.line(
                "Individual terms have hits but the combination does not — "
                "the query may be overconstrained."
            )
    emit.flush()


@pubmed.command("analyze")
@click.argument("query")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    query: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored PubMed search results for QUERY. No network.

    Reads what `search` wrote and produces a summary analysis: total
    results, year distribution, journal distribution, top MeSH terms.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    # Locate the structured artifact
    artifact_path = source_dir / f"{slug}.pubmed-search.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no PubMed search artifact for {query!r} under {source_dir}",
            remedy=f"run `dde pubmed search {query!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "PubMed search artifact")
    if not isinstance(artifact, dict) or "results" not in artifact:
        raise SchemaError(
            "PubMed search artifact has no results array",
            detail=f"keys: {list(artifact.keys()) if isinstance(artifact, dict) else type(artifact).__name__}",
        )

    results = artifact["results"]
    query_meta = artifact.get("query", {})
    total_found = query_meta.get("total_found", 0)

    # Compute distributions
    year_counts: Counter[str] = Counter()
    journal_counts: Counter[str] = Counter()
    mesh_counts: Counter[str] = Counter()

    for r in results:
        year = r.get("year")
        if year:
            year_counts[year] += 1
        journal = r.get("journal")
        if journal:
            journal_counts[journal] += 1
        for term in r.get("mesh_terms", []):
            mesh_counts[term] += 1

    outcome = "results_found" if results else "no_results"

    # Top items (most common first)
    top_journals = journal_counts.most_common(10)
    top_mesh = mesh_counts.most_common(15)
    year_distribution = dict(sorted(year_counts.items()))

    relays: list[dict[str, str]] = []
    if outcome == "results_found":
        relays.append(
            provenance.relay(
                "pubmed.search_not_exhaustive",
                f"PubMed keyword search for {query!r} returned {len(results)} of "
                f"{total_found} results; relevant publications may use different "
                "terminology, be indexed under different MeSH headings, or not "
                "yet be indexed",
            )
        )

    assessment = {
        "outcome": outcome,
        "query": query,
        "total_found": total_found,
        "n_retrieved": len(results),
        "year_distribution": year_distribution,
        "top_journals": [{"journal": j, "count": c} for j, c in top_journals],
        "top_mesh_terms": [{"term": t, "count": c} for t, c in top_mesh],
    }

    metrics = {
        "total_found": total_found,
        "n_retrieved": len(results),
        "n_unique_journals": len(journal_counts),
        "n_unique_mesh_terms": len(mesh_counts),
        "year_range": [
            min(year_counts) if year_counts else None,
            max(year_counts) if year_counts else None,
        ],
    }

    # Locate the meta file written by search for the source reference
    meta_path = source_dir / f"{slug}.meta.json"
    source_ref = (
        state.project().relative(meta_path)
        if meta_path.is_file()
        else state.project().relative(artifact_path)
    )

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.pubmed-search.analysis.json",
        source=source_ref,
        threshold_set="pubmed-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"PubMed search analysis: {query!r}")
    emit.line(f"  Outcome: {outcome}")
    emit.line(f"  {total_found} total found, {len(results)} retrieved")
    if year_distribution:
        emit.line(f"  Year range: {min(year_counts)}-{max(year_counts)}")
    if top_journals:
        emit.line("  Top journals:")
        for j, c in top_journals[:5]:
            emit.line(f"    {j}: {c}")
    if top_mesh:
        emit.line("  Top MeSH terms:")
        for t, c in top_mesh[:5]:
            emit.line(f"    {t}: {c}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


# ---------------------------------------------------------------------------
# Fulltext helpers
# ---------------------------------------------------------------------------


_PMCID_RE = re.compile(r"^PMC\d+$")


def _parse_fulltext_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Parse a PMC efetch JATS XML response into a structured record.

    Extracts title, authors, abstract, body sections, references, DOI,
    and PMID from the JATS/NLM XML format used by PubMed Central.
    Gracefully handles missing fields.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SchemaError(
            "PMC efetch response is not valid XML", detail=str(exc)
        ) from exc

    # The response may be a <pmc-articleset> wrapping one <article>,
    # or just an <article> at the root.
    article = root.find(".//article")
    if article is None:
        # May be the article itself at the root
        if root.tag == "article":
            article = root
        else:
            return {}

    front = article.find("front")
    if front is None:
        return {}

    article_meta = front.find("article-meta")
    if article_meta is None:
        return {}

    # Title
    title = None
    title_group = article_meta.find("title-group")
    if title_group is not None:
        title_el = title_group.find("article-title")
        if title_el is not None:
            title = "".join(title_el.itertext()).strip()

    # Authors
    authors: list[str] = []
    contrib_group = article_meta.find("contrib-group")
    if contrib_group is not None:
        for contrib in contrib_group.findall("contrib"):
            if contrib.get("contrib-type") != "author":
                continue
            name_el = contrib.find("name")
            if name_el is not None:
                surname = name_el.find("surname")
                given = name_el.find("given-names")
                parts = []
                if surname is not None and surname.text:
                    parts.append(surname.text)
                if given is not None and given.text:
                    parts.append(given.text)
                if parts:
                    authors.append(" ".join(parts))
            else:
                # Collab / collective author
                collab = contrib.find("collab")
                if collab is not None and collab.text:
                    authors.append(collab.text.strip())

    # Abstract
    abstract = None
    abstract_el = article_meta.find("abstract")
    if abstract_el is not None:
        parts = []
        for text_el in abstract_el.iter():
            if text_el.text:
                parts.append(text_el.text.strip())
            if text_el.tail:
                parts.append(text_el.tail.strip())
        abstract = " ".join(p for p in parts if p) if parts else None

    # DOI
    doi = None
    for aid in article_meta.findall("article-id"):
        if aid.get("pub-id-type") == "doi" and aid.text:
            doi = aid.text.strip()
            break

    # PMID
    pmid = None
    for aid in article_meta.findall("article-id"):
        if aid.get("pub-id-type") == "pmid" and aid.text:
            pmid = aid.text.strip()
            break

    # Body sections — recursive walk so nested <sec> elements are captured
    def _walk_sections(parent: ET.Element) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for sec in parent.findall("sec"):
            heading = ""
            title_el = sec.find("title")
            if title_el is not None:
                heading = "".join(title_el.itertext()).strip()
            paragraphs = [
                "".join(p.itertext()).strip()
                for p in sec.findall("p")
                if "".join(p.itertext()).strip()
            ]
            if paragraphs:
                sections.append({"heading": heading, "text": "\n\n".join(paragraphs)})
            sections.extend(_walk_sections(sec))
        return sections

    sections: list[dict[str, str]] = []
    body = article.find("body")
    if body is not None:
        sections = _walk_sections(body)
        # If no <sec> elements, grab top-level <p> elements
        if not sections:
            paragraphs: list[str] = []
            for p_el in body.findall("p"):
                text = "".join(p_el.itertext()).strip()
                if text:
                    paragraphs.append(text)
            if paragraphs:
                sections.append(
                    {
                        "heading": "",
                        "text": "\n\n".join(paragraphs),
                    }
                )

    # References
    references: list[str] = []
    back = article.find("back")
    if back is not None:
        ref_list = back.find("ref-list")
        if ref_list is not None:
            for ref_el in ref_list.findall("ref"):
                # Try to get the citation text
                citation = ref_el.find(".//mixed-citation")
                if citation is None:
                    citation = ref_el.find(".//element-citation")
                if citation is not None:
                    ref_text = "".join(citation.itertext()).strip()
                    if ref_text:
                        references.append(ref_text)

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "sections": sections,
        "references": references,
        "doi": doi,
        "pmid": pmid,
    }


# ---------------------------------------------------------------------------
# fulltext subcommand
# ---------------------------------------------------------------------------


@pubmed.command("fulltext")
@click.argument("pmcid")
@out_option
@output_options
@pass_state
def fulltext_cmd(
    state: AppState,
    pmcid: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Fetch full-text article from PubMed Central by PMCID.

    PMCID must be a PubMed Central identifier (e.g. PMC1234567).
    Fetches the JATS XML from NCBI and writes a structured record
    with title, authors, abstract, body sections, references, DOI
    and PMID.
    """
    emit = emitter(as_json, quiet)

    # Validate PMCID format
    if not _PMCID_RE.match(pmcid):
        raise click.UsageError(
            f"Invalid PMCID format: {pmcid!r}. "
            "Expected format: PMC followed by digits (e.g. PMC1234567)."
        )

    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    pmcid_lower = pmcid.lower()

    # Fetch full text from PMC via efetch
    efetch_url = (
        f"{EUTILS_BASE}/efetch.fcgi?db=pmc"
        f"&id={pmcid}"
        f"&rettype=full"
        f"&retmode=xml" + api_key_suffix()
    )
    efetch_bytes = http.get_bytes(
        efetch_url,
        qps=qps_for_host("eutils.ncbi.nlm.nih.gov"),
        timeout=60.0,
    )

    # Save the raw XML
    xml_path = target_dir / f"{pmcid_lower}.fulltext.xml"
    xml_path.write_bytes(efetch_bytes)

    # Parse the XML
    parsed = _parse_fulltext_xml(efetch_bytes)

    # Check if we got meaningful content
    if not parsed or not parsed.get("title"):
        # Article not available in OA subset
        sidecar = provenance.Sidecar(
            tool=TOOL,
            subcommand="fulltext",
            endpoint=EUTILS_BASE,
            parameters={"pmcid": pmcid},
        )
        sidecar.warn(
            f"The article {pmcid} is not available in PubMed Central open "
            "access. The PMCID may be incorrect or the article may not be "
            "in the OA subset.",
            code="pubmed.fulltext_unavailable",
        )
        sidecar.add_output(xml_path)
        meta_path = sidecar.write(target_dir / f"{pmcid_lower}.fulltext.meta.json")

        emit.data("pmcid", pmcid)
        emit.data("available", False)
        emit.path(xml_path, role="xml")
        emit.path(meta_path, role="sidecar")
        emit.line(f"PubMed Central fulltext: {pmcid}")
        emit.line("  Article not available in open access")
        emit.flush()
        return

    # Build the structured artifact
    artifact = {
        "schema": "dde.pubmed-fulltext.v1",
        "pmcid": pmcid,
        "title": parsed["title"],
        "authors": parsed["authors"],
        "abstract": parsed["abstract"],
        "sections": parsed["sections"],
        "references": parsed["references"],
        "doi": parsed["doi"],
        "pmid": parsed["pmid"],
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    artifact_path = target_dir / f"{pmcid_lower}.fulltext.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    # Sidecar
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="fulltext",
        endpoint=EUTILS_BASE,
        parameters={"pmcid": pmcid},
    )
    sidecar.note("source", "PubMed Central (NCBI/NLM)")
    sidecar.note("licence", "NLM Terms of Service — PMC Open Access")
    sidecar.note("n_sections", str(len(parsed["sections"])))
    sidecar.note("n_references", str(len(parsed["references"])))

    sidecar.add_output(xml_path)
    sidecar.add_output(artifact_path)

    meta_path = sidecar.write(target_dir / f"{pmcid_lower}.fulltext.meta.json")

    # Emit
    emit.data("pmcid", pmcid)
    emit.data("available", True)
    emit.data("title", parsed["title"])
    emit.data("n_authors", len(parsed["authors"]))
    emit.data("n_sections", len(parsed["sections"]))
    emit.data("n_references", len(parsed["references"]))
    emit.path(xml_path, role="xml")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"PubMed Central fulltext: {pmcid}")
    emit.line(f"  Title: {(parsed['title'] or '(no title)')[:80]}")
    emit.line(f"  Authors: {len(parsed['authors'])}")
    emit.line(f"  Sections: {len(parsed['sections'])}")
    emit.line(f"  References: {len(parsed['references'])}")
    if parsed["doi"]:
        emit.line(f"  DOI: {parsed['doi']}")
    if parsed["pmid"]:
        emit.line(f"  PMID: {parsed['pmid']}")
    emit.flush()
