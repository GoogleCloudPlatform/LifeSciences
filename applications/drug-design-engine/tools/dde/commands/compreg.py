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

"""`dde compreg` — does the compound exist?

Confirms that a PubChem CID, ChEMBL ID, or compound name resolves to a
real compound record and surfaces the canonical name and SMILES for
cross-checking.  The compound equivalent of `litref` for literature
identifiers.

Two phases:

  resolve  queries PubChem PUG REST and/or ChEMBL REST and writes the
           verbatim responses to Layer 0 with a sidecar.
  analyze  reads those responses and returns resolved / ambiguous /
           not_found.  No network.

**The tool never ranks and picks.**  More than one match on a name
query is ambiguous — a first-class outcome and a hard stop, not a
`resolved` with a warning stapled to a chosen record.  A silent wrong
answer is worse than a loud missing one.

Resolution by unique identifier — PubChem CID or ChEMBL ID — may
return `resolved`.  Resolution by name returns every match, and a
single match by name is still flagged with
`compreg.name_match_not_unique_identifier`, because a name search may
miss synonyms and cannot prove uniqueness.
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
from ..core.qps import qps_for_host

TOOL = "compreg"
ARTIFACT_CLASS = "compounds"

PUBCHEM_API = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

CHEMBL_API = "https://www.ebi.ac.uk/chembl/api/data"

PUBCHEM_PROPERTIES = (
    "CanonicalSMILES,IUPACName,MolecularFormula,MolecularWeight,InChI,InChIKey"
)

CHEMBL_RE = re.compile(r"^CHEMBL\d+$", re.IGNORECASE)
CID_RE = re.compile(r"^\d+$")


def _classify(identifier: str) -> tuple[str, str]:
    """Return (kind, normalised) for an identifier string.

    Kinds: chembl | cid | name.  Only the first two are unique
    identifiers; ``name`` triggers the every-match contract.
    """
    text = identifier.strip()
    if CHEMBL_RE.match(text):
        return "chembl", text.upper()
    if CID_RE.match(text):
        return "cid", text
    return "name", text.lower()


def _slug(kind: str, value: str) -> str:
    """Derive a filesystem-safe slug from (kind, normalised value).

    Slug scheme avoids collision with SMILES-derived slugs in
    ``compound.py``: ``cid-2244``, ``chembl-chembl25``,
    ``name-palbociclib``.
    """
    if kind == "cid":
        base = f"cid-{value}"
    elif kind == "chembl":
        base = f"chembl-{value.lower()}"
    else:
        base = f"name-{value}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-").lower()
    return slug[:80] or "compound"


# ---------------------------------------------------------------------------
# Source queries.  Each returns (url, verbatim_bytes).
# ---------------------------------------------------------------------------


def _pubchem_by_cid(cid: str) -> tuple[str, bytes]:
    """Fetch PubChem compound properties by CID.

    A 404 here is a *result*, not a failure: it is the registry saying
    this CID does not exist.  It is tolerated and recorded as an
    explicit negative artifact, so ``analyze`` reads "not found" from
    disk rather than inferring it from a missing file.
    """
    url = (
        f"{PUBCHEM_API}/compound/cid/{quote(cid, safe='')}"
        f"/property/{PUBCHEM_PROPERTIES}/JSON"
    )
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    if response.status_code == 404:
        return url, json.dumps(
            {"_not_found": True, "cid": cid, "http_status": 404},
            indent=2,
        ).encode("utf-8")
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("PubChem did not return JSON", detail=str(exc)) from exc
    return url, body


def _pubchem_by_name(name: str) -> tuple[str, bytes]:
    """Fetch PubChem compound properties by name.

    PubChem's name endpoint returns a single best match or 404.
    """
    url = (
        f"{PUBCHEM_API}/compound/name/{quote(name, safe='')}"
        f"/property/{PUBCHEM_PROPERTIES}/JSON"
    )
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    if response.status_code == 404:
        return url, json.dumps(
            {"_not_found": True, "name": name, "http_status": 404},
            indent=2,
        ).encode("utf-8")
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("PubChem did not return JSON", detail=str(exc)) from exc
    return url, body


def _chembl_by_id(chembl_id: str) -> tuple[str, bytes]:
    """Fetch a ChEMBL molecule by ID.

    A 404 here is a result: the ChEMBL ID does not exist.  ChEMBL
    returns an empty body on 404, so we synthesise a negative artifact.
    """
    url = f"{CHEMBL_API}/molecule/{quote(chembl_id, safe='')}.json"
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("www.ebi.ac.uk"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    if response.status_code == 404:
        return url, json.dumps(
            {"_not_found": True, "chembl_id": chembl_id, "http_status": 404},
            indent=2,
        ).encode("utf-8")
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("ChEMBL did not return JSON", detail=str(exc)) from exc
    return url, body


def _chembl_by_name(name: str) -> tuple[str, bytes]:
    """Search ChEMBL molecules by name.

    Returns a list of matching molecules; may be empty.
    """
    url = f"{CHEMBL_API}/molecule/search.json?" + urlencode({"q": name})
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("www.ebi.ac.uk"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    if response.status_code == 404:
        return url, json.dumps(
            {"_not_found": True, "name": name, "http_status": 404},
            indent=2,
        ).encode("utf-8")
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("ChEMBL did not return JSON", detail=str(exc)) from exc
    return url, body


# ---------------------------------------------------------------------------
# Match extraction (phase 2 — pure, reads bytes already on disk)
# ---------------------------------------------------------------------------


def _pubchem_matches(payload: Any) -> list[dict[str, Any]]:
    """Extract compound records from a stored PubChem property response."""
    if isinstance(payload, dict) and payload.get("_not_found"):
        return []
    if not isinstance(payload, dict) or "PropertyTable" not in payload:
        raise SchemaError("PubChem payload has no PropertyTable")
    props_list = payload["PropertyTable"].get("Properties") or []
    records: list[dict[str, Any]] = []
    for p in props_list:
        if not isinstance(p, dict):
            continue
        records.append(
            {
                "source": "pubchem",
                "cid": p.get("CID"),
                "canonical_name": p.get("IUPACName"),
                # PubChem returns ConnectivitySMILES, not CanonicalSMILES,
                # despite the URL parameter name.
                "canonical_smiles": (
                    p.get("ConnectivitySMILES") or p.get("CanonicalSMILES")
                ),
                "molecular_formula": p.get("MolecularFormula"),
                "molecular_weight": p.get("MolecularWeight"),
                "inchi": p.get("InChI"),
                "inchi_key": p.get("InChIKey"),
            }
        )
    return records


def _chembl_matches(payload: Any) -> list[dict[str, Any]]:
    """Extract compound records from a stored ChEMBL response.

    Handles both single-molecule (by ID) and search (by name) responses.
    """
    if isinstance(payload, dict) and payload.get("_not_found"):
        return []
    if not isinstance(payload, dict):
        raise SchemaError("ChEMBL payload is not an object")

    # Search response: has 'molecules' key.
    if "molecules" in payload:
        molecules = payload["molecules"] or []
    # Single molecule response: has 'molecule_chembl_id'.
    elif "molecule_chembl_id" in payload:
        molecules = [payload]
    else:
        raise SchemaError(
            "ChEMBL payload has neither molecules list nor molecule_chembl_id"
        )

    records: list[dict[str, Any]] = []
    for mol in molecules:
        if not isinstance(mol, dict):
            continue
        structures = mol.get("molecule_structures") or {}
        properties = mol.get("molecule_properties") or {}
        records.append(
            {
                "source": "chembl",
                "chembl_id": mol.get("molecule_chembl_id"),
                "canonical_name": mol.get("pref_name"),
                "canonical_smiles": structures.get("canonical_smiles"),
                "molecular_formula": properties.get("full_molformula"),
                "molecular_weight": properties.get("full_mwt"),
                "molecule_type": mol.get("molecule_type"),
                "max_phase": mol.get("max_phase"),
            }
        )
    return records


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def compreg() -> None:
    """Compound-registry identity resolution."""


@compreg.command("resolve")
@click.argument("identifier")
@click.option(
    "--source",
    type=click.Choice(["auto", "pubchem", "chembl"]),
    default="auto",
    help="Which registry to query.  'auto' dispatches identifiers by their "
    "own type and sends a bare name to both.",
)
@out_option
@output_options
@click.option("--name", default=None, help="Human-readable label for metadata.")
@pass_state
def resolve_cmd(
    state: AppState,
    identifier: str,
    source: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
    name: str | None,
) -> None:
    """Query the registries for IDENTIFIER and write the raw responses.

    IDENTIFIER may be a PubChem CID (all digits), a ChEMBL ID
    (CHEMBL followed by digits), or a compound name.  Identifiers are
    looked up; names are searched, and every match is kept.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    kind, value = _classify(identifier)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="resolve",
        endpoint="pubchem + chembl",
        parameters={
            "identifier": identifier,
            "identifier_kind": kind,
            "normalised": value,
            "source": source,
        },
    )
    sidecar.note("is_unique_identifier", kind != "name")
    if name:
        sidecar.note("compound_name", name)

    slug = _slug(kind, value)
    written: list[tuple[str, Path]] = []

    def save(registry: str, url: str, body: bytes) -> None:
        path = target_dir / f"{slug}.registry-{registry}.json"
        path.write_bytes(body)
        sidecar.add_output(path)
        sidecar.note(f"{registry}_query_url", url)
        written.append((registry, path))

    if kind == "cid":
        if source == "chembl":
            raise UsageError(
                f"{value} is a PubChem CID; --source chembl cannot resolve it",
                remedy="use --source pubchem or --source auto",
            )
        url, body = _pubchem_by_cid(value)
        save("pubchem", url, body)

    elif kind == "chembl":
        if source == "pubchem":
            raise UsageError(
                f"{value} is a ChEMBL ID; --source pubchem cannot resolve it",
                remedy="use --source chembl or --source auto",
            )
        url, body = _chembl_by_id(value)
        save("chembl", url, body)

    else:  # name — query every registry the caller allowed, keep every match
        if source in {"auto", "pubchem"}:
            url, body = _pubchem_by_name(value)
            save("pubchem", url, body)
        if source in {"auto", "chembl"}:
            url, body = _chembl_by_name(value)
            save("chembl", url, body)

    meta_path = sidecar.write(target_dir / f"{slug}.meta.json")

    emit.data("identifier", identifier)
    emit.data("identifier_kind", kind)
    emit.data("normalised", value)
    for registry, path in written:
        emit.path(path, role=f"registry-{registry}")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@compreg.command("analyze")
@click.argument("identifier")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    identifier: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Report resolved / ambiguous / not_found for a resolved IDENTIFIER.

    Reads only what ``resolve`` wrote.  This command never reads a
    compound's content to judge whether it has the claimed activity —
    that is the specialist's job, and a tool that guessed would recreate
    the failure it exists to catch.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    kind, value = _classify(identifier)
    slug = _slug(kind, value)

    # Locate the resolve sidecar.
    meta_path = source_dir / f"{slug}.meta.json"
    if not meta_path.is_file():
        raise ArtifactError(
            f"no resolved compound for {identifier!r} under {source_dir}",
            remedy=f"run `dde compreg resolve {identifier!r}` first",
        )
    meta = provenance.read_json(meta_path, "compreg sidecar")

    thresholds = load_thresholds(state, "compreg")

    # Collect matches from all stored response files.
    matches: list[dict[str, Any]] = []
    sources_queried: list[str] = []
    for registry, extractor in (
        ("pubchem", _pubchem_matches),
        ("chembl", _chembl_matches),
    ):
        path = source_dir / f"{slug}.registry-{registry}.json"
        if not path.is_file():
            continue
        sources_queried.append(registry)
        found = extractor(provenance.read_json(path, f"{registry} response"))
        matches.extend(found)

    # Validate that at least one registry was actually queried.
    # If ALL response files are missing, we have no data to base a
    # conclusion on — reporting "not_found" would certify absence
    # when no database was actually consulted.
    if not sources_queried:
        raise ArtifactError(
            f"no registry response files found for {identifier!r}",
            remedy=f"run 'dde compreg resolve {identifier}' first",
        )

    n_matches = len(matches)

    if n_matches == 0:
        outcome = "not_found"
    elif n_matches == 1:
        outcome = "resolved"
    else:
        outcome = "ambiguous"

    relays: list[dict[str, str]] = []

    if outcome == "resolved":
        match = matches[0]
        resolved_id = match.get("cid") or match.get("chembl_id") or value
        relays.append(
            provenance.relay(
                "compreg.resolved_not_verified",
                f"a compound record exists for {value!r} "
                f"({match.get('source')}: {resolved_id}), but this tool "
                "confirmed only that the identifier maps to a real record "
                "— it did not verify biological activity, mechanism, or "
                "therapeutic indication",
            )
        )
        if kind == "name":
            relays.append(
                provenance.relay(
                    "compreg.name_match_not_unique_identifier",
                    f"{value!r} matched exactly one record, but it was "
                    "matched by name, not by a unique registry identifier; "
                    "a name search may miss synonyms and cannot prove "
                    "uniqueness — obtain the CID or ChEMBL ID and re-resolve",
                )
            )

    # Collect upstream relays from the resolve sidecar.
    seen_codes: set[str] = {r["code"] for r in relays}
    for r in meta.get("mandatory_relays", []) or []:
        if r["code"] not in seen_codes:
            relays.append(r)
            seen_codes.add(r["code"])

    # Build assessment.
    assessment: dict[str, Any] = {
        "outcome": outcome,
        "identifier": identifier,
        "identifier_kind": kind,
        "resolved_by": "identifier" if kind != "name" else "name",
        "sources_queried": sources_queried,
        "n_matches": n_matches,
    }

    if outcome == "resolved":
        match = matches[0]
        assessment["source_db"] = match.get("source")
        assessment["canonical_name"] = match.get("canonical_name")
        assessment["canonical_smiles"] = match.get("canonical_smiles")
        assessment["molecular_formula"] = match.get("molecular_formula")
        assessment["molecular_weight"] = match.get("molecular_weight")

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
    emit.line(
        f"{identifier} ({kind}) -> {outcome.upper()}  "
        f"[{n_matches} match(es) from {', '.join(sources_queried)}]"
    )
    if outcome == "resolved":
        match = matches[0]
        emit.line(f"  name:    {match.get('canonical_name')}")
        emit.line(f"  SMILES:  {match.get('canonical_smiles')}")
        emit.line(f"  formula: {match.get('molecular_formula')}")
    elif outcome == "ambiguous":
        for m in matches[:5]:
            label = m.get("cid") or m.get("chembl_id") or "?"
            emit.line(f"  {m.get('source')} {label}: {m.get('canonical_name')}")
        if len(matches) > 5:
            emit.line(f"  … {len(matches) - 5} more in the analysis artifact")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()

    # `ambiguous` is a refusal, not a failure: the analysis artifact was
    # written and its path has already been printed above.  It exits
    # non-zero so that a specialist chaining `dde compreg analyze X &&
    # …` cannot proceed on a compound the tool declined to resolve.
    # `not_found` exits 0 by contrast — "this record does not exist" is
    # a completed analysis and is itself the finding, whereas "which of
    # these did you mean" is an unanswered question.
    if outcome == "ambiguous":
        raise Refusal(
            f"{value!r} matches {n_matches} records; this tool will not "
            "choose between them",
            detail="; ".join(
                f"{m.get('source')} "
                f"{m.get('cid') or m.get('chembl_id') or '?'}: "
                f"{m.get('canonical_name')}"
                for m in matches[:8]
            )
            + (" …" if len(matches) > 8 else ""),
            remedy=(
                "supply a unique identifier — PubChem CID or ChEMBL ID. "
                "Do not pick a candidate from the list: they are listed "
                "because choosing among them is what produces a clean "
                "audit trail for the wrong compound"
            ),
        )
