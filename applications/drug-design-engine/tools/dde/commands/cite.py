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

"""`dde cite` — citation verification against upstream registries.

Takes a document file, extracts citations from it, resolves each against
upstream sources (CrossRef, Europe PMC, ClinicalTrials.gov), compares
claimed titles against resolved titles, and produces a citation manifest.

Two phases:

  verify   extracts and resolves citations from a document (network).
  analyze  reads the manifest and computes a verdict (offline).

**CRITICAL correctness requirement (§3.3)**: `all_verified` requires
that suspect == 0 AND phantom == 0 AND unverified == 0.  A suspect
title match is NOT verified.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote

import click

from ..common import (
    AppState,
    emitter,
    from_option,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
)
from ..core import http, provenance
from ..core.env import CLI_VERSION
from ..core.errors import ArtifactError, SchemaError
from ..core.qps import qps_for_host

ARTIFACT_CLASS = "literature"

CROSSREF_BASE = "https://api.crossref.org/works"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CTGOV_BASE = "https://clinicaltrials.gov/api/v2/studies"

# ---------------------------------------------------------------------------
# Extraction patterns
# ---------------------------------------------------------------------------

DOI_RE = re.compile(
    r"(?:doi[:\s]*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)", re.IGNORECASE
)
PMID_RE = re.compile(r"(?:PMID[:\s]*)(\d{1,8})", re.IGNORECASE)
PMCID_RE = re.compile(r"(PMC\d+)", re.IGNORECASE)
NCT_RE = re.compile(r"(NCT\d{8})", re.IGNORECASE)
# Title-like: a line of at least 20 chars starting with a capital, ending
# with a period, possibly followed by a journal-style suffix.  Very loose —
# this is a fallback extractor, not a parser.
TITLE_RE = re.compile(
    r"^([A-Z][A-Za-z0-9 ,;:\-–—()/'\"]{18,}\.)\s*$",  # noqa: RUF001 — en-dash/em-dash intentional in char class
    re.MULTILINE,
)


def _classify(raw_id: str) -> tuple[str, str]:
    """Return (kind, normalised) for a raw identifier string."""
    text = raw_id.strip()
    if NCT_RE.fullmatch(text):
        return "nct", text.upper()
    if PMCID_RE.fullmatch(text):
        return "pmcid", text.upper()
    doi = DOI_RE.fullmatch(text)
    if doi:
        return "doi", doi.group(1).rstrip(".,;")
    pmid = PMID_RE.fullmatch(text)
    if pmid:
        return "pmid", pmid.group(1)
    return "title", text


def _slug(stem: str) -> str:
    """Derive a filesystem-safe slug from a filename stem."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower()
    return slug[:80] or "cite"


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------


def _extract_structured(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract from a JSON document with a DDE or Hypex citation array."""
    citations = (
        data.get("citations") or data.get("references") or data.get("evidence") or []
    )
    if not isinstance(citations, list):
        return []
    results: list[dict[str, Any]] = []
    for entry in citations:
        if isinstance(entry, str):
            kind, value = _classify(entry)
            results.append(
                {
                    "raw_id": entry,
                    "kind": kind,
                    "normalised": value,
                    "claimed_title": entry if kind == "title" else None,
                }
            )
        elif isinstance(entry, dict):
            raw = (
                entry.get("lit_id")
                or entry.get("doi")
                or entry.get("pmid")
                or entry.get("pmcid")
                or entry.get("nct")
                or entry.get("id")
                or entry.get("title")
                or ""
            )
            if not raw:
                continue
            kind, value = _classify(str(raw))
            claimed_title = entry.get("title") or (raw if kind == "title" else None)
            results.append(
                {
                    "raw_id": str(raw),
                    "kind": kind,
                    "normalised": value,
                    "claimed_title": claimed_title,
                }
            )
    return results


def _extract_regex(text: str) -> list[dict[str, Any]]:
    """Extract citations from text using regex patterns."""
    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    for pattern, _kind_hint in [
        (DOI_RE, "doi"),
        (PMID_RE, "pmid"),
        (PMCID_RE, "pmcid"),
        (NCT_RE, "nct"),
    ]:
        for match in pattern.finditer(text):
            # Use the full match for classification context (e.g.
            # "PMID:12345678" keeps the prefix so _classify sees it).
            full = match.group(0).rstrip(".,;:\"' ")
            kind, value = _classify(full)
            key = f"{kind}:{value}"
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "raw_id": full,
                    "kind": kind,
                    "normalised": value,
                    "claimed_title": None,
                }
            )

    return results


MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MB


def _extract_citations(file_path: Path) -> tuple[list[dict[str, Any]], str]:
    """Extract citations from a file. Returns (citations, extraction_basis)."""
    size = file_path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ArtifactError(
            f"input file is {size / 1024 / 1024:.0f} MB, limit is "
            f"{MAX_INPUT_BYTES / 1024 / 1024:.0f} MB",
            remedy="pass a smaller file or split it",
        )
    text = file_path.read_text(encoding="utf-8", errors="replace")

    structured_citations: list[dict[str, Any]] = []
    regex_citations: list[dict[str, Any]] = []
    is_structured = False

    # Try structured extraction from JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and any(
            key in data for key in ("citations", "references", "evidence")
        ):
            is_structured = True
            structured_citations = _extract_structured(data)
    except (json.JSONDecodeError, ValueError):
        pass

    # Always try regex extraction too
    regex_citations = _extract_regex(text)

    if structured_citations and not regex_citations:
        return structured_citations, "structured"
    if structured_citations and regex_citations:
        # Merge: structured first, then regex-only discoveries
        seen = {f"{c['kind']}:{c['normalised']}" for c in structured_citations}
        merged = list(structured_citations)
        for c in regex_citations:
            key = f"{c['kind']}:{c['normalised']}"
            if key not in seen:
                merged.append(c)
                seen.add(key)
        if len(merged) > len(structured_citations):
            return merged, "mixed"
        return structured_citations, "structured"
    if regex_citations:
        return regex_citations, "regex-fallback"

    return [], "structured" if is_structured else "regex-fallback"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _title_similarity(claimed: str | None, resolved: str | None) -> float:
    """Compute title similarity using SequenceMatcher."""
    if not claimed or not resolved:
        return 0.0
    return SequenceMatcher(
        None,
        claimed.lower().strip(),
        resolved.lower().strip(),
    ).ratio()


def _resolve_doi(doi: str) -> dict[str, Any]:
    """Resolve a DOI via CrossRef."""
    url = f"{CROSSREF_BASE}/{quote(doi, safe='')}"
    try:
        response = http.request(
            "GET",
            url,
            qps=qps_for_host("api.crossref.org"),
            timeout=60.0,
            tolerate_status=(404,),
        )
        if response.status_code == 404:
            return {"found": False, "source": "crossref", "url": url}
        data = response.json()
        work = data.get("message", {})
        title_parts = work.get("title", [])
        title = title_parts[0] if title_parts else None
        return {
            "found": True,
            "source": "crossref",
            "title": title,
            "url": f"https://doi.org/{doi}",
        }
    except Exception as exc:
        if "phase-2 command" in str(exc) or "PhaseContractError" in type(exc).__name__:
            raise
        return {"found": False, "source": "crossref", "error": str(exc), "url": url}


def _resolve_epmc(query: str, source_label: str) -> dict[str, Any]:
    """Resolve via Europe PMC search."""
    url = (
        f"{EPMC_SEARCH}?query={quote(query, safe='')}"
        f"&format=json&resultType=lite&pageSize=5"
    )
    try:
        response = http.request(
            "GET",
            url,
            qps=qps_for_host("www.ebi.ac.uk"),
            timeout=60.0,
        )
        data = response.json()
        results = (data.get("resultList") or {}).get("result") or []
        if not results:
            return {"found": False, "source": "epmc", "url": url}
        first = results[0]
        return {
            "found": True,
            "source": "epmc",
            "title": first.get("title"),
            "id": first.get("id") or first.get("pmid"),
            "url": url,
        }
    except Exception as exc:
        if "phase-2 command" in str(exc) or "PhaseContractError" in type(exc).__name__:
            raise
        return {"found": False, "source": "epmc", "error": str(exc), "url": url}


def _resolve_ctgov(nct_id: str) -> dict[str, Any]:
    """Resolve an NCT ID via ClinicalTrials.gov."""
    url = f"{CTGOV_BASE}/{quote(nct_id, safe='')}"
    try:
        response = http.request(
            "GET",
            url,
            qps=qps_for_host("clinicaltrials.gov"),
            timeout=60.0,
            tolerate_status=(404,),
        )
        if response.status_code == 404:
            return {"found": False, "source": "ctgov", "url": url}
        data = response.json()
        section = data.get("protocolSection") or {}
        ident = section.get("identificationModule") or {}
        title = ident.get("briefTitle") or ident.get("officialTitle")
        return {
            "found": True,
            "source": "ctgov",
            "title": title,
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
        }
    except Exception as exc:
        if "phase-2 command" in str(exc) or "PhaseContractError" in type(exc).__name__:
            raise
        return {"found": False, "source": "ctgov", "error": str(exc), "url": url}


def _resolve_citation(
    citation: dict[str, Any],
    title_match_tolerance: float,
    title_suspect_floor: float,
) -> dict[str, Any]:
    """Resolve a single citation and classify its status."""
    kind = citation["kind"]
    value = citation["normalised"]
    claimed_title = citation.get("claimed_title")

    # Dispatch to the right resolver
    if kind == "doi":
        result = _resolve_doi(value)
    elif kind == "pmid":
        result = _resolve_epmc(f"EXT_ID:{value} AND SRC:MED", "pmid")
    elif kind == "pmcid":
        result = _resolve_epmc(f'PMCID:"{value}"', "pmcid")
    elif kind == "nct":
        result = _resolve_ctgov(value)
    elif kind == "title":
        # Sanitize double quotes to prevent query injection and
        # malformed Europe PMC queries (HTTP 400 → false network_error).
        sanitized = value.replace('"', " ")
        result = _resolve_epmc(f'TITLE:"{sanitized}"', "title")
    else:
        result = {"found": False, "source": "unknown"}

    error_msg = result.get("error")

    # Determine status
    if error_msg:
        # Network error or timeout
        reason = "timeout" if "timeout" in error_msg.lower() else "network_error"
        return {
            "id": value,
            "raw_id": citation["raw_id"],
            "source": result.get("source", "unknown"),
            "status": "unverified",
            "reason": reason,
            "claimed_title": claimed_title,
            "resolved_title": None,
            "title_similarity": 0.0,
            "verified_via": result.get("url"),
            "url": None,
            "error": error_msg,
        }

    if not result.get("found"):
        return {
            "id": value,
            "raw_id": citation["raw_id"],
            "source": result.get("source", "unknown"),
            "status": "phantom",
            "reason": "not_found",
            "claimed_title": claimed_title,
            "resolved_title": None,
            "title_similarity": 0.0,
            "verified_via": result.get("url"),
            "url": None,
            "error": None,
        }

    # Found — compare titles
    resolved_title = result.get("title")
    similarity = _title_similarity(claimed_title, resolved_title)

    # When no claimed title is available, we cannot do a title comparison.
    # The ID resolved, so treat as verified-by-id.
    if not claimed_title:
        status = "verified"
        reason = "ok"
    elif similarity >= title_match_tolerance:
        status = "verified"
        reason = "ok"
    elif similarity >= title_suspect_floor:
        status = "suspect-title-match"
        reason = "title_mismatch"
    else:
        status = "phantom"
        reason = "title_mismatch"

    return {
        "id": result.get("id") or value,
        "raw_id": citation["raw_id"],
        "source": result.get("source", "unknown"),
        "status": status,
        "reason": reason,
        "claimed_title": claimed_title,
        "resolved_title": resolved_title,
        "title_similarity": round(similarity, 4),
        "verified_via": result.get("url"),
        "url": result.get("url"),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def cite() -> None:
    """Verify citations in a document against upstream registries."""


@cite.command("verify")
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "--tolerance",
    type=float,
    default=None,
    help="Override title_match_tolerance for this invocation.",
)
@out_option
@output_options
@pass_state
def verify_cmd(
    state: AppState,
    file: str,
    tolerance: float | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Extract and verify citations in FILE against upstream registries.

    FILE may be JSON (with a citations/references array or Hypex evidence
    array), Markdown, or plain text. Identifiers (DOI, PMID, PMCID, NCT) are
    resolved against CrossRef, Europe PMC, or ClinicalTrials.gov. Titles are
    compared using SequenceMatcher.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    file_path = Path(file)

    # Load thresholds
    overrides: dict[str, Any] = {}
    if tolerance is not None:
        overrides["title_match_tolerance"] = tolerance
    thresholds = load_thresholds(state, "citation-verification", overrides)
    title_match_tolerance = float(thresholds.get("title_match_tolerance"))
    title_suspect_floor = float(thresholds.get("title_suspect_floor"))

    # Extract citations
    citations, extraction_basis = _extract_citations(file_path)

    # Resolve each citation
    resolved: list[dict[str, Any]] = []
    for citation in citations:
        record = _resolve_citation(citation, title_match_tolerance, title_suspect_floor)
        resolved.append(record)

    # Build summary
    n_verified = sum(1 for r in resolved if r["status"] == "verified")
    n_suspect = sum(1 for r in resolved if r["status"] == "suspect-title-match")
    n_phantom = sum(1 for r in resolved if r["status"] == "phantom")
    n_unverified = sum(1 for r in resolved if r["status"] == "unverified")
    # §3.3 CRITICAL: all_verified requires suspect == 0 too
    all_verified = (
        len(resolved) > 0 and n_suspect == 0 and n_phantom == 0 and n_unverified == 0
    )

    manifest = {
        "schema": "dde.citation-manifest.v1",
        "target_file": file_path.name,
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verifier": f"dde-cite/{CLI_VERSION}",
        "summary": {
            "total": len(resolved),
            "verified": n_verified,
            "suspect": n_suspect,
            "phantom": n_phantom,
            "unverified": n_unverified,
            "all_verified": all_verified,
        },
        "extraction_basis": extraction_basis,
        "citations": resolved,
    }

    # Write outputs
    slug = _slug(file_path.stem)
    manifest_path = target_dir / f"{slug}.citations.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Build sidecar
    endpoints = set()
    for r in resolved:
        src = r.get("source")
        if src == "crossref":
            endpoints.add("crossref")
        elif src == "epmc":
            endpoints.add("europepmc")
        elif src == "ctgov":
            endpoints.add("clinicaltrials.gov")
    endpoint_str = " + ".join(sorted(endpoints)) or "none"

    sidecar = provenance.Sidecar(
        tool="cite",
        subcommand="verify",
        endpoint=endpoint_str,
        parameters={
            "file": file_path.name,
            "extraction_basis": extraction_basis,
            "title_match_tolerance": title_match_tolerance,
            "title_suspect_floor": title_suspect_floor,
        },
    )
    sidecar.add_output(manifest_path)

    # Fire relay codes conditionally
    if n_phantom > 0:
        phantom_ids = [r["raw_id"] for r in resolved if r["status"] == "phantom"]
        sidecar.warn(
            f"{n_phantom} phantom citation(s) found: {', '.join(phantom_ids[:5])}",
            code="cite.phantom_citation",
        )
    if n_suspect > 0:
        suspect_ids = [
            r["raw_id"] for r in resolved if r["status"] == "suspect-title-match"
        ]
        sidecar.warn(
            f"{n_suspect} suspect title match(es): {', '.join(suspect_ids[:5])}",
            code="cite.suspect_title_match",
        )
    if n_unverified > 0:
        unverified_with_network = [
            r
            for r in resolved
            if r["status"] == "unverified"
            and r.get("reason") in ("network_error", "timeout")
        ]
        if unverified_with_network:
            sidecar.warn(
                f"{len(unverified_with_network)} citation(s) unverified due to "
                "network issues",
                code="cite.unresolved_offline",
            )
    if extraction_basis != "structured":
        sidecar.warn(
            f"extraction basis is {extraction_basis!r}: references were "
            "recovered by pattern match, not from document structure",
            code="cite.extraction_incomplete",
        )

    meta_path = sidecar.write(target_dir / f"{slug}.meta.json")

    # Emit output
    emit.data("summary", manifest["summary"])
    emit.data("extraction_basis", extraction_basis)
    emit.data("all_verified", all_verified)
    emit.line(f"Citation verification: {file_path.name}")
    emit.line(
        f"  {len(resolved)} citation(s): {n_verified} verified, "
        f"{n_suspect} suspect, {n_phantom} phantom, {n_unverified} unverified"
    )
    emit.line(f"  all_verified: {all_verified}")
    emit.line(f"  extraction_basis: {extraction_basis}")
    for r in resolved[:10]:
        emit.line(f"  {r['raw_id']}: {r['status']} ({r['reason']})")
    if len(resolved) > 10:
        emit.line(f"  ... {len(resolved) - 10} more in the manifest")
    for relay in sidecar.relays:
        emit.line(f"relay {relay['code']}: {relay['message']}")
    emit.path(manifest_path, role="manifest")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@cite.command("analyze")
@click.argument("artifact", default=None, required=False)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    artifact: str | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Compute a verification verdict from a citation manifest. No network.

    Reads the .citations.json artifact written by `verify` and produces
    an .analysis.json with verdicts and mandatory relays.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Locate the manifest
    if artifact and Path(artifact).suffix == ".json":
        manifest_path = Path(artifact)
        if not manifest_path.is_absolute():
            manifest_path = state.project().root / manifest_path
    else:
        # Find .citations.json in the source dir
        candidates = sorted(source_dir.glob("*.citations.json"))
        if artifact:
            # Filter by slug
            slug = _slug(artifact)
            candidates = [c for c in candidates if c.name.startswith(slug)]
        if not candidates:
            raise ArtifactError(
                f"no citation manifest found under {source_dir}",
                remedy="run `dde cite verify <FILE>` first",
            )
        manifest_path = candidates[0]

    manifest = provenance.read_json(manifest_path, "citation manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "dde.citation-manifest.v1"
    ):
        raise SchemaError(
            "file is not a dde.citation-manifest.v1 artifact",
            detail=f"schema: {manifest.get('schema') if isinstance(manifest, dict) else 'not a dict'}",
        )

    # Load thresholds
    thresholds = load_thresholds(state, "citation-verification")

    # Read summary from manifest
    summary = manifest.get("summary", {})
    citations_list = manifest.get("citations", [])
    extraction_basis = manifest.get("extraction_basis", "unknown")

    total = summary.get("total", len(citations_list))
    n_verified = summary.get("verified", 0)
    n_suspect = summary.get("suspect", 0)
    n_phantom = summary.get("phantom", 0)
    n_unverified = summary.get("unverified", 0)

    # Compute verdict
    if total == 0:
        verdict = "no-citations-found"
    elif n_phantom > 0:
        verdict = "phantom-citations-present"
    elif n_suspect > 0:
        verdict = "suspect-matches-present"
    elif n_unverified > 0:
        verdict = "partially-unresolved"
    else:
        verdict = "all-verified"

    # Fire relay codes
    relays: list[dict[str, str]] = []
    if n_phantom > 0:
        phantom_ids = [
            c["raw_id"] for c in citations_list if c.get("status") == "phantom"
        ]
        relays.append(
            provenance.relay(
                "cite.phantom_citation",
                f"{n_phantom} phantom citation(s): {', '.join(phantom_ids[:5])}",
            )
        )
    if n_suspect > 0:
        suspect_ids = [
            c["raw_id"]
            for c in citations_list
            if c.get("status") == "suspect-title-match"
        ]
        relays.append(
            provenance.relay(
                "cite.suspect_title_match",
                f"{n_suspect} suspect title match(es): {', '.join(suspect_ids[:5])}",
            )
        )
    if n_unverified > 0:
        unverified_net = [
            c
            for c in citations_list
            if c.get("status") == "unverified"
            and c.get("reason") in ("network_error", "timeout")
        ]
        if unverified_net:
            relays.append(
                provenance.relay(
                    "cite.unresolved_offline",
                    f"{len(unverified_net)} citation(s) could not be verified "
                    "due to network issues",
                )
            )
    if extraction_basis != "structured":
        relays.append(
            provenance.relay(
                "cite.extraction_incomplete",
                f"extraction basis was {extraction_basis!r}: references were "
                "recovered by pattern match",
            )
        )
    elif total == 0:
        relays.append(
            provenance.relay(
                "cite.extraction_incomplete",
                "no citations were extracted from the document",
            )
        )

    metrics = {
        "total": total,
        "verified": n_verified,
        "suspect": n_suspect,
        "phantom": n_phantom,
        "unverified": n_unverified,
    }

    assessment = {
        "verdict": verdict,
        "extraction_basis": extraction_basis,
        "target_file": manifest.get("target_file"),
        "all_verified": summary.get("all_verified", False),
    }

    # Derive analysis filename from manifest filename
    analysis_name = manifest_path.name.replace(".citations.json", ".analysis.json")

    analysis_path = provenance.write_analysis(
        target_dir / analysis_name,
        source=state.project().relative(manifest_path),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        metrics=metrics,
        assessment=assessment,
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        unresolved=thresholds.unresolved(),
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("verdict", verdict)
    emit.data("metrics", metrics)
    emit.data("relays", relays)
    emit.line(f"Citation analysis: {manifest_path.name}")
    emit.line(f"  Verdict: {verdict}")
    emit.line(
        f"  {total} citation(s): {n_verified} verified, {n_suspect} suspect, "
        f"{n_phantom} phantom, {n_unverified} unverified"
    )
    for relay in relays:
        emit.line(f"relay {relay['code']}: {relay['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
