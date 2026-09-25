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

"""`dde litref` — does the cited record exist?

The narrowest tool in the set, and the one aimed most directly at the
failure mode the pilot is testing. It takes a citation and answers
exactly one question: *is there a real record here?* It does **not**
read the record and it does **not** decide whether the record supports
the claim it was cited for. A tool that guessed at support would
reintroduce the fabrication it exists to catch, one layer down.

Two phases:

  resolve  queries Europe PMC and/or ClinicalTrials.gov and writes the
           verbatim responses to Layer 0 with a sidecar.
  analyze  reads those responses and returns resolved / ambiguous /
           not_found. No network.

**The tool never ranks and picks.** This is the whole design, and it
comes from a live case. `query.titles=PALOMA-3` on ClinicalTrials.gov
returns two real trials and puts the wrong one first:

    NCT05388669  Janssen, lazertinib + subcutaneous amivantamab (NSCLC)
    NCT01942135  Pfizer,  palbociclib + fulvestrant (breast) — the real one

NCT01942135's `acronym` field is null; the name PALOMA-3 appears only
inside its title prose, so acronym matching cannot separate them. The
same search on Europe PMC returns 24 papers spanning both drugs.

A resolver that took the top hit would not merely fail to verify a
breast-cancer claim. It would hand the specialist an NSCLC trial to
check it against, the specialist would "verify" it, and the audit trail
would look clean. A silent wrong answer is worse than a loud missing
one, so more than one match is `ambiguous`: a first-class outcome and a
hard stop, never a `resolved` with a warning stapled to a chosen record.

Resolution by unique identifier — NCT, PMID, PMCID, DOI — may return
`resolved`. Resolution by name returns every match, and a single match
by name is still flagged, because a title search cannot prove the name
is unique: a trial that mentions the name only in its description is
invisible to it, exactly as NCT01942135's null acronym was.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

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
from ..core.errors import ArtifactError, Refusal, SchemaError, UsageError
from ..core.paths import confine_path, sanitize_slug
from ..core.qps import qps_for_host

EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CTGOV_BASE = "https://clinicaltrials.gov/api/v2/studies"

CTGOV_FIELDS = (
    "NCTId,BriefTitle,OfficialTitle,Acronym,LeadSponsorName,OverallStatus,"
    "Phase,StartDate,CompletionDate,Condition,InterventionName"
)

NCT_RE = re.compile(r"^NCT\d{8}$", re.IGNORECASE)
PMID_RE = re.compile(r"^(?:PMID[:\s]*)?(\d{1,8})$", re.IGNORECASE)
PMCID_RE = re.compile(r"^(PMC\d+)$", re.IGNORECASE)
DOI_RE = re.compile(
    r"^(?:doi[:\s]*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", re.IGNORECASE
)


def _classify(citation: str) -> tuple[str, str]:
    """Return (kind, normalised) for a citation string.

    Kinds: nct | pmid | pmcid | doi | name. Only the first four are
    unique identifiers; `name` triggers the every-match contract.

    A bare number is read as a PMID only at 7 or more digits. `2015` is
    a year and `NCT01942135` is caught earlier; accepting short numerals
    as PMIDs would turn an author-year citation into a confident lookup
    of an unrelated paper.
    """
    text = citation.strip()
    if NCT_RE.match(text):
        return "nct", text.upper()
    if PMCID_RE.match(text):
        return "pmcid", text.upper()
    doi = DOI_RE.match(text)
    if doi:
        return "doi", doi.group(1).rstrip(".,;")
    pmid = PMID_RE.match(text)
    if pmid and (len(pmid.group(1)) >= 7 or text.upper().startswith("PMID")):
        return "pmid", pmid.group(1)
    return "name", text


def _slug(kind: str, value: str) -> str:
    base = f"{kind}-{value}" if kind != "name" else value
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-").lower()
    return slug[:80] or "citation"


# ---------------------------------------------------------------------------
# Source queries. Each returns (url, verbatim_bytes).
# ---------------------------------------------------------------------------


def _fetch(url: str, qps: float, what: str) -> tuple[str, bytes]:
    """GET a payload and keep the exact bytes, which are what get hashed."""
    response = http.request("GET", url, qps=qps, timeout=60.0)
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(f"{what} did not return JSON", detail=str(exc)) from exc
    return url, body


def _epmc_query(query: str, page_size: int, result_type: str) -> tuple[str, bytes]:
    url = f"{EPMC_SEARCH}?" + urlencode(
        {
            "query": query,
            "format": "json",
            "resultType": result_type,
            "pageSize": page_size,
        }
    )
    return _fetch(url, qps_for_host("www.ebi.ac.uk"), "Europe PMC")


def _ctgov_search(name: str, page_size: int) -> tuple[str, bytes]:
    url = f"{CTGOV_BASE}?" + urlencode(
        {
            "query.titles": name,
            "pageSize": page_size,
            "countTotal": "true",
            "fields": CTGOV_FIELDS,
        }
    )
    return _fetch(url, qps_for_host("clinicaltrials.gov"), "ClinicalTrials.gov")


def _ctgov_by_id(nct: str) -> tuple[str, bytes]:
    """Fetch one trial by NCT number.

    A 404 here is a *result*, not a failure: it is the registry saying
    this NCT number does not exist, which for a fabricated citation is
    the finding. It is tolerated and recorded as an explicit negative
    artifact, so `analyze` reads "not found" from disk rather than
    inferring it from a missing file.
    """
    url = f"{CTGOV_BASE}/{quote(nct)}?" + urlencode({"fields": CTGOV_FIELDS})
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("clinicaltrials.gov"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    if response.status_code == 404:
        return url, json.dumps(
            {"_not_found": True, "nctId": nct, "http_status": 404}, indent=2
        ).encode("utf-8")
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "ClinicalTrials.gov did not return JSON", detail=str(exc)
        ) from exc
    return url, body


# ---------------------------------------------------------------------------
# Match extraction (phase 2 — pure, reads bytes already on disk)
# ---------------------------------------------------------------------------


def _epmc_matches(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(payload, dict) or "resultList" not in payload:
        raise SchemaError("Europe PMC payload has no resultList")
    results = payload["resultList"].get("result") or []
    matches = [
        {
            "source": "europepmc",
            "id": r.get("id"),
            "pmid": r.get("pmid"),
            "doi": r.get("doi"),
            "title": r.get("title"),
            "journal": (r.get("journalInfo") or {}).get("journal", {}).get("title")
            if isinstance(r.get("journalInfo"), dict)
            else None,
            "year": r.get("pubYear"),
        }
        for r in results
        if isinstance(r, dict)
    ]
    return matches, int(payload.get("hitCount", len(matches)))


def _ctgov_matches(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(payload, dict):
        raise SchemaError("ClinicalTrials.gov payload is not an object")
    if "studies" in payload:
        studies = payload.get("studies") or []
        total = payload.get("totalCount")
    elif "protocolSection" in payload:  # single-study endpoint
        studies = [payload]
        total = 1
    elif payload.get("_not_found"):
        return [], 0
    else:
        raise SchemaError(
            "ClinicalTrials.gov payload has neither studies nor protocolSection"
        )

    matches = []
    for study in studies:
        section = study.get("protocolSection") or {}
        ident = section.get("identificationModule") or {}
        status = section.get("statusModule") or {}
        sponsor = (section.get("sponsorCollaboratorsModule") or {}).get(
            "leadSponsor"
        ) or {}
        matches.append(
            {
                "source": "clinicaltrials.gov",
                "id": ident.get("nctId"),
                "title": ident.get("briefTitle"),
                "acronym": ident.get("acronym"),
                "sponsor": sponsor.get("name"),
                "status": status.get("overallStatus"),
                "phase": (section.get("designModule") or {}).get("phases"),
            }
        )
    return matches, int(total if total is not None else len(matches))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def litref() -> None:
    """Resolve a cited paper or trial to a real record — or fail."""


@litref.command("resolve")
@click.argument("citation")
@click.option(
    "--source",
    type=click.Choice(["auto", "literature", "trials"]),
    default="auto",
    help="Which registry to query. 'auto' dispatches identifiers by their own "
    "type and sends a bare name to both.",
)
@out_option
@output_options
@pass_state
def resolve_cmd(
    state: AppState,
    citation: str,
    source: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Query the registries for CITATION and write the raw responses.

    CITATION may be an NCT number, a PMID, a PMCID, a DOI, or a name
    such as a trial acronym. Identifiers are looked up; names are
    searched, and every match is kept.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir("literature", out)

    kind, value = _classify(citation)
    thresholds = load_thresholds(state, "litref")
    page_size = int(thresholds.get("max_matches_recorded"))

    sidecar = provenance.Sidecar(
        tool="litref",
        subcommand="resolve",
        endpoint="europepmc + clinicaltrials.gov",
        parameters={
            "citation": citation,
            "identifier_kind": kind,
            "normalised": value,
            "source": source,
            "page_size": page_size,
        },
    )
    sidecar.note("is_unique_identifier", kind != "name")

    slug = _slug(kind, value)
    written: list[tuple[str, Path]] = []

    def save(role: str, url: str, body: bytes) -> None:
        path = target_dir / f"{slug}.{role}.json"
        path.write_bytes(body)
        sidecar.add_output(path)
        sidecar.note(f"{role}_query_url", url)
        written.append((role, path))

    if kind == "nct":
        if source == "literature":
            raise UsageError(
                f"{value} is an NCT number; --source literature cannot resolve it",
                remedy="use --source trials or --source auto",
            )
        url, body = _ctgov_by_id(value)
        save("trials", url, body)

    elif kind in {"pmid", "pmcid", "doi"}:
        if source == "trials":
            raise UsageError(
                f"{value} is a {kind.upper()}; --source trials cannot resolve it",
                remedy="use --source literature or --source auto",
            )
        query = {
            "pmid": f"EXT_ID:{value} AND SRC:MED",
            "pmcid": f'PMCID:"{value}"',
            "doi": f'DOI:"{value}"',
        }[kind]
        url, body = _epmc_query(query, page_size, "core")
        save("literature", url, body)

    else:  # name — query every registry the caller allowed, keep every match
        if source in {"auto", "trials"}:
            url, body = _ctgov_search(value, page_size)
            save("trials", url, body)
        if source in {"auto", "literature"}:
            url, body = _epmc_query(f'TITLE:"{value}"', page_size, "lite")
            save("literature", url, body)

    meta_path = sidecar.write(target_dir / f"{slug}.meta.json")

    emit.data("citation", citation)
    emit.data("identifier_kind", kind)
    emit.data("normalised", value)
    for role, path in written:
        emit.path(path, role=role)
    emit.path(meta_path, role="sidecar")
    emit.flush()


@litref.command("analyze")
@click.argument("citation")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    citation: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Report resolved / ambiguous / not_found for a resolved CITATION.

    Reads only what `resolve` wrote. This command never reads a record's
    contents to judge whether it supports the claim — that is the
    specialist's job, and a tool that guessed would recreate the failure
    it exists to catch.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir("literature", from_dir)
    target_dir = state.project().artifact_dir("literature", out)

    meta_path, slug = _locate_meta(state, citation, source_dir)
    meta = provenance.read_json(meta_path, "litref sidecar")
    params = meta.get("parameters") or {}
    kind = params.get("identifier_kind", "name")
    value = params.get("normalised", citation)

    thresholds = load_thresholds(state, "litref")
    cap = int(thresholds.get("max_matches_recorded"))

    matches: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    for role, extractor in (("trials", _ctgov_matches), ("literature", _epmc_matches)):
        path = source_dir / f"{slug}.{role}.json"
        if not path.is_file():
            continue
        found, total = extractor(provenance.read_json(path, f"{role} response"))
        matches.extend(found)
        totals[role] = total

    if not totals:
        raise ArtifactError(
            f"no response files found for {slug!r} — run 'dde litref resolve' first",
            detail=(
                f"expected at least one of: {slug}.trials.json, "
                f"{slug}.literature.json in {source_dir}"
            ),
            remedy="run 'dde litref resolve' to fetch response files before analyzing",
        )

    total_hits = sum(totals.values())
    truncated = total_hits > len(matches)

    if total_hits == 0:
        outcome = "not_found"
    elif total_hits == 1:
        outcome = "resolved"
    else:
        outcome = "ambiguous"

    relays: list[dict[str, str]] = []
    warnings: list[str] = []

    if outcome == "resolved":
        relays.append(
            provenance.relay(
                "litref.resolved_not_verified",
                f"a record exists for {value!r} ({matches[0].get('id')}), but this "
                "tool did not read it and has not checked that it says what the "
                "claim says",
            )
        )
        if kind == "name":
            relays.append(
                provenance.relay(
                    "litref.name_match_not_unique_identifier",
                    f"{value!r} matched exactly one record, but it was matched by "
                    "title text, not by a unique identifier; a record carrying the "
                    "name only in its description or abstract would not appear here",
                )
            )
    elif outcome == "ambiguous":
        listed = "; ".join(
            f"{m.get('id')} {str(m.get('title'))[:60]}" for m in matches[:4]
        )
        relays.append(
            provenance.relay(
                "litref.ambiguous_name",
                f"{value!r} matches {total_hits} records "
                f"({', '.join(f'{k}: {v}' for k, v in totals.items())}): {listed}"
                + (" …" if len(matches) > 4 else ""),
            )
        )

    if truncated:
        warnings.append(
            f"{total_hits} records matched but only {len(matches)} were recorded "
            f"(max_matches_recorded={cap}); the match list is a sample, not a census"
        )

    assessment = {
        "outcome": outcome,
        "citation": citation,
        "identifier_kind": kind,
        "resolved_by": "identifier" if kind != "name" else "name",
        "n_matches": total_hits,
        "matches_recorded": len(matches),
        "per_source_hits": totals,
        "warnings": warnings,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.analysis.json",
        source=state.project().relative(meta_path),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        metrics={"matches": matches},
        assessment=assessment,
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        unresolved=thresholds.unresolved(),
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("matches", matches)
    emit.data("relays", relays)
    emit.line(f"{citation} ({kind}) -> {outcome.upper()}  [{total_hits} match(es)]")
    for match in matches[:5]:
        emit.line(f"  {match.get('source')} {match.get('id')}: {match.get('title')}")
    if len(matches) > 5:
        emit.line(f"  … {len(matches) - 5} more in the analysis artifact")
    for warning in warnings:
        emit.line(f"warning: {warning}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()

    # `ambiguous` is a refusal, not a failure: the analysis artifact was
    # written and its path has already been printed above. It exits
    # non-zero so that a specialist chaining `dde litref analyze X &&
    # …` cannot proceed on a citation the tool declined to resolve.
    # `not_found` exits 0 by contrast — "this record does not exist" is
    # a completed analysis and is itself the finding, whereas "which of
    # these 26 did you mean" is an unanswered question.
    if outcome == "ambiguous":
        raise Refusal(
            f"{value!r} matches {total_hits} records; this tool will not choose "
            "between them",
            detail=", ".join(str(m.get("id")) for m in matches[:8])
            + (" …" if len(matches) > 8 else ""),
            remedy=(
                "supply a unique identifier — NCT number, PMID or DOI. Do not "
                "pick a candidate from the list: they are listed because "
                "choosing among them is what produces a clean audit trail for "
                "the wrong record"
            ),
        )


def _locate_meta(state: AppState, citation: str, target_dir: Path) -> tuple[Path, str]:
    # A DOI contains a slash and looks exactly like a relative path, so
    # the path branch is gated on the artifact suffix rather than on
    # punctuation. `10.1056/NEJMoa1505270` is a citation, not a file.
    if citation.endswith(".meta.json"):
        candidate = Path(citation)
        path = (
            candidate if candidate.is_absolute() else state.project().root / candidate
        )
        confined = confine_path(state.project().root, path)
        if confined is None:
            raise ArtifactError(f"litref sidecar path escapes project root: {path}")
        path = confined
        if not path.is_file():
            raise ArtifactError(f"litref sidecar not found: {path}")
        return path, path.name[: -len(".meta.json")]

    kind, value = _classify(citation)
    slug = sanitize_slug(_slug(kind, value))
    path = target_dir / f"{slug}.meta.json"
    if not path.is_file():
        raise ArtifactError(
            f"no resolved citation for {citation!r} under {target_dir}",
            remedy=f"run `dde litref resolve {citation!r}` first",
        )
    return path, slug
