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

"""`dde similar` — structure similarity and substructure search.

Searches PubChem and/or ChEMBL for compounds similar to a query
structure (Tanimoto similarity) or containing a query substructure.

Three subcommands, two phases:

  search       Tanimoto similarity search against PubChem and/or ChEMBL,
               written to Layer 0 with a sidecar.
  substructure substructure match search against PubChem and/or ChEMBL,
               written to Layer 0 with a sidecar.
  analyze      reads stored search/substructure results, applies
               thresholds, and produces a verdict.  No network.

Input is a SMILES string.  The SMILES is validated locally via RDKit
before any network call; unparseable SMILES are refused (exit 9).

PubChem similarity and substructure searches are asynchronous: the
initial request returns a ListKey, which must be polled until the
result is ready.  ChEMBL's endpoints are synchronous with pagination.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import quote

import click

from ..common import (
    AppState,
    DDEGroup,
    emitter,
    from_option,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
)
from ..core import http, provenance
from ..core.errors import (
    ArtifactError,
    DependencyError,
    EndpointError,
    EndpointUnavailable,
    Refusal,
)
from ..core.qps import qps_for_host

TOOL = "similar"
ARTIFACT_CLASS = "compounds"

PUBCHEM_API = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

CHEMBL_API = "https://www.ebi.ac.uk/chembl/api/data"

SCHEMA = "dde.similar.v1"


# ---------------------------------------------------------------------------
# RDKit lazy import
# ---------------------------------------------------------------------------


def _require_rdkit():
    """Lazy-import RDKit, raising DependencyError if absent."""
    try:
        from rdkit import Chem

        return Chem
    except ImportError as e:
        raise DependencyError(
            "RDKit is not installed",
            detail="SMILES validation requires RDKit",
            remedy="install rdkit into the tools environment (pip install rdkit-pypi)",
        ) from e


def _validate_smiles(smiles: str) -> str:
    """Validate SMILES via RDKit, returning canonical form.

    Raises Refusal (exit 9) on unparseable SMILES.
    """
    Chem = _require_rdkit()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise Refusal(
            "unparseable SMILES",
            detail=f"RDKit cannot parse: {smiles}",
            remedy="check the SMILES syntax; common issues include unclosed "
            "brackets, invalid atom symbols, and unbalanced parentheses",
        )
    return Chem.MolToSmiles(mol)


# ---------------------------------------------------------------------------
# Filesystem slug
# ---------------------------------------------------------------------------


def _slug(smiles: str) -> str:
    """Derive a deterministic, filesystem-safe slug from canonical SMILES."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", smiles).strip("-").lower()
    if not slug or len(slug) < 3:
        slug = "mol-" + hashlib.sha256(smiles.encode()).hexdigest()[:16]
    return slug[:80]


# ---------------------------------------------------------------------------
# PubChem async polling
# ---------------------------------------------------------------------------


def _poll_pubchem_listkey(
    listkey: str,
    qps: float = qps_for_host("pubchem.ncbi.nlm.nih.gov"),
    timeout: float = 120,
) -> list[int]:
    """Poll PubChem for async result.  Returns CID list or raises."""
    url = f"{PUBCHEM_API}/compound/listkey/{listkey}/cids/JSON"
    start = time.monotonic()
    delay = 1.0
    while time.monotonic() - start < timeout:
        time.sleep(delay)
        resp = http.get_json(url, qps=qps)
        if "IdentifierList" in resp:
            return resp["IdentifierList"]["CID"]
        if "Waiting" in resp:
            delay = min(delay * 1.5, 5.0)
            continue
        # Fault or unexpected response
        raise ArtifactError(
            "PubChem async search failed",
            detail=str(resp),
        )
    raise ArtifactError(
        "PubChem async search timed out",
        detail=f"timeout={timeout}s",
    )


def _fetch_pubchem_properties(cids: list[int]) -> list[dict[str, Any]]:
    """Fetch compound properties for a list of CIDs from PubChem.

    Returns a list of property dicts with CanonicalSMILES, IUPACName,
    MolecularWeight for each CID.
    """
    if not cids:
        return []
    # PubChem allows comma-separated CIDs in a single request (up to ~100).
    cid_str = ",".join(str(c) for c in cids)
    url = (
        f"{PUBCHEM_API}/compound/cid/{cid_str}"
        "/property/CanonicalSMILES,IUPACName,MolecularWeight,InChIKey/JSON"
    )
    resp = http.get_json(url, qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"))
    return resp.get("PropertyTable", {}).get("Properties", [])


# ---------------------------------------------------------------------------
# PubChem similarity / substructure search
# ---------------------------------------------------------------------------


def _pubchem_similarity(
    smiles: str,
    threshold: float,
    max_results: int,
) -> list[dict[str, Any]]:
    """Run a PubChem similarity search (async ListKey pattern).

    PubChem pre-filters hits at the requested *threshold* server-side,
    but the CID-list endpoint does not return per-hit Tanimoto scores.
    Each hit's ``tanimoto`` is set to the query threshold as a guaranteed
    lower bound, and ``tanimoto_is_lower_bound`` is set to ``True`` so
    downstream consumers know the value is not an exact score.
    """
    encoded = quote(smiles, safe="")
    threshold_int = int(threshold * 100)
    url = (
        f"{PUBCHEM_API}/compound/similarity/smiles/{encoded}/JSON"
        f"?Threshold={threshold_int}&MaxRecords={max_results}"
    )
    resp = http.get_json(
        url,
        qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"),
        tolerate_status=(202,),
    )

    listkey = (resp.get("Waiting") or {}).get("ListKey")
    if not listkey:
        # Sometimes PubChem returns results directly for very fast queries
        if "IdentifierList" in resp:
            cids = resp["IdentifierList"]["CID"][:max_results]
        else:
            raise ArtifactError(
                "PubChem similarity: unexpected response",
                detail=str(resp)[:500],
            )
    else:
        cids = _poll_pubchem_listkey(listkey)[:max_results]

    if not cids:
        return []

    # Fetch properties for all CIDs.
    props = _fetch_pubchem_properties(cids)
    props_by_cid = {p["CID"]: p for p in props}

    hits: list[dict[str, Any]] = []
    for cid in cids:
        p = props_by_cid.get(cid, {})
        hits.append(
            {
                "source_db": "pubchem",
                "cid": cid,
                "canonical_smiles": p.get("CanonicalSMILES", ""),
                "iupac_name": p.get("IUPACName", ""),
                "tanimoto": threshold,  # server-side filtered lower bound
                "tanimoto_is_lower_bound": True,
                "molecular_weight": p.get("MolecularWeight"),
                "inchikey": p.get("InChIKey", ""),
            }
        )
    return hits


def _pubchem_substructure(
    smiles: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """Run a PubChem substructure search (async ListKey pattern)."""
    encoded = quote(smiles, safe="")
    url = (
        f"{PUBCHEM_API}/compound/substructure/smiles/{encoded}/JSON"
        f"?MaxRecords={max_results}"
    )
    resp = http.get_json(
        url,
        qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"),
        tolerate_status=(202,),
    )

    listkey = (resp.get("Waiting") or {}).get("ListKey")
    if not listkey:
        if "IdentifierList" in resp:
            cids = resp["IdentifierList"]["CID"][:max_results]
        else:
            raise ArtifactError(
                "PubChem substructure: unexpected response",
                detail=str(resp)[:500],
            )
    else:
        cids = _poll_pubchem_listkey(listkey)[:max_results]

    if not cids:
        return []

    props = _fetch_pubchem_properties(cids)
    props_by_cid = {p["CID"]: p for p in props}

    hits: list[dict[str, Any]] = []
    for cid in cids:
        p = props_by_cid.get(cid, {})
        hits.append(
            {
                "source_db": "pubchem",
                "cid": cid,
                "canonical_smiles": p.get("CanonicalSMILES", ""),
                "iupac_name": p.get("IUPACName", ""),
                "tanimoto": None,
                "molecular_weight": p.get("MolecularWeight"),
                "inchikey": p.get("InChIKey", ""),
            }
        )
    return hits


# ---------------------------------------------------------------------------
# ChEMBL similarity / substructure search
# ---------------------------------------------------------------------------


def _chembl_paginate(url: str, max_results: int) -> list[dict[str, Any]]:
    """Fetch ChEMBL results with pagination.  Returns molecule records."""
    molecules: list[dict[str, Any]] = []
    current_url = url
    while current_url and len(molecules) < max_results:
        resp = http.get_json(current_url, qps=qps_for_host("www.ebi.ac.uk"))
        mols = resp.get("molecules", [])
        if not mols:
            break
        molecules.extend(mols)
        # ChEMBL pagination: follow 'next' URL in 'page_meta'.
        page_meta = resp.get("page_meta", {})
        next_url = page_meta.get("next")
        if next_url:
            # ChEMBL returns relative URLs, need to make absolute.
            if next_url.startswith("/"):
                current_url = f"https://www.ebi.ac.uk{next_url}"
            else:
                current_url = next_url
        else:
            break
    return molecules[:max_results]


def _chembl_similarity(
    smiles: str,
    threshold: float,
    max_results: int,
) -> list[dict[str, Any]]:
    """Run a ChEMBL similarity search (synchronous with pagination)."""
    encoded = quote(smiles, safe="")
    threshold_pct = int(threshold * 100)
    url = f"{CHEMBL_API}/similarity/{encoded}/{threshold_pct}.json"
    molecules = _chembl_paginate(url, max_results)

    hits: list[dict[str, Any]] = []
    for mol in molecules:
        structures = mol.get("molecule_structures") or {}
        similarity = mol.get("similarity")
        hits.append(
            {
                "source_db": "chembl",
                "chembl_id": mol.get("molecule_chembl_id", ""),
                "canonical_smiles": structures.get("canonical_smiles", ""),
                "pref_name": mol.get("pref_name") or "",
                "tanimoto": similarity / 100.0 if similarity is not None else None,
                "molecular_weight": None,
                "inchikey": structures.get("standard_inchi_key", ""),
            }
        )
    return hits


def _chembl_substructure(
    smiles: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """Run a ChEMBL substructure search (synchronous with pagination)."""
    encoded = quote(smiles, safe="")
    url = f"{CHEMBL_API}/substructure/{encoded}.json"
    molecules = _chembl_paginate(url, max_results)

    hits: list[dict[str, Any]] = []
    for mol in molecules:
        structures = mol.get("molecule_structures") or {}
        hits.append(
            {
                "source_db": "chembl",
                "chembl_id": mol.get("molecule_chembl_id", ""),
                "canonical_smiles": structures.get("canonical_smiles", ""),
                "pref_name": mol.get("pref_name") or "",
                "tanimoto": None,
                "molecular_weight": None,
                "inchikey": structures.get("standard_inchi_key", ""),
            }
        )
    return hits


# ---------------------------------------------------------------------------
# Result merging and deduplication
# ---------------------------------------------------------------------------


def _merge_hits(
    hits: list[dict[str, Any]],
    max_results: int,
) -> list[dict[str, Any]]:
    """Deduplicate hits by InChIKey, sort by Tanimoto descending, and cap.

    When a duplicate is found, the entry with the higher (non-None)
    Tanimoto score is kept.  An exact score is preferred over a
    lower-bound score (``tanimoto_is_lower_bound``).
    """
    # Map InChIKey -> index in deduped list.
    seen: dict[str, int] = {}
    deduped: list[dict[str, Any]] = []
    for hit in hits:
        key = hit.get("inchikey", "")
        if key and key in seen:
            # Prefer the entry with the better Tanimoto data.
            existing_idx = seen[key]
            existing = deduped[existing_idx]
            ex_t = existing.get("tanimoto")
            new_t = hit.get("tanimoto")
            # Prefer non-None over None.
            if ex_t is None and new_t is not None:
                deduped[existing_idx] = hit
            # Both non-None: prefer exact score over lower-bound,
            # then higher value.
            elif new_t is not None and ex_t is not None:
                ex_lb = existing.get("tanimoto_is_lower_bound", False)
                new_lb = hit.get("tanimoto_is_lower_bound", False)
                if ex_lb and not new_lb:
                    # Exact beats lower-bound regardless of value.
                    deduped[existing_idx] = hit
                elif not ex_lb and new_lb:
                    pass  # keep existing exact score
                elif new_t > ex_t:
                    deduped[existing_idx] = hit
            continue
        if key:
            seen[key] = len(deduped)
        deduped.append(hit)

    # Sort by Tanimoto descending (None sorts last).
    deduped.sort(
        key=lambda h: (h.get("tanimoto") is None, -(h.get("tanimoto") or 0)),
    )
    return deduped[:max_results]


# ---------------------------------------------------------------------------
# Artifact building
# ---------------------------------------------------------------------------


def _build_artifact(
    smiles: str,
    search_type: str,
    source: str,
    threshold: float | None,
    max_results: int,
    hits: list[dict[str, Any]],
    *,
    search_status: str = "completed",
    failure_reason: str | None = None,
    backend_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the structured dde.similar.v1 artifact.

    ``search_status`` is one of:
      - ``"completed"`` — all requested backends succeeded.
      - ``"partial"`` — some backends succeeded, some failed.
      - ``"failed"`` — all backends failed.

    When ``search_status`` is ``"failed"``, the artifact carries
    ``failure_reason`` and zero hits, and must NOT be interpreted as
    a clean novelty result.
    """
    closest = None
    if hits:
        # Find hit with highest Tanimoto (if any have it).
        scored = [h for h in hits if h.get("tanimoto") is not None]
        if scored:
            best = max(scored, key=lambda h: h["tanimoto"])
            closest = {
                "cid": best.get("cid"),
                "chembl_id": best.get("chembl_id"),
                "name": best.get("iupac_name") or best.get("pref_name") or "",
                "tanimoto": best["tanimoto"],
            }
            if best.get("tanimoto_is_lower_bound"):
                closest["tanimoto_is_lower_bound"] = True

    query: dict[str, Any] = {
        "smiles": smiles,
        "search_type": search_type,
        "source": source,
        "max_results": max_results,
    }
    if threshold is not None:
        query["threshold"] = threshold

    artifact: dict[str, Any] = {
        "schema": SCHEMA,
        "query": query,
        "search_status": search_status,
        "result_count": len(hits),
        "summary": {
            "n_hits": len(hits),
            "closest_match": closest,
        },
        "hits": hits,
    }
    if failure_reason is not None:
        artifact["failure_reason"] = failure_reason
    if backend_results is not None:
        artifact["backend_results"] = backend_results
    return artifact


# ---------------------------------------------------------------------------
# Classification (phase 2 — extracted for testability)
# ---------------------------------------------------------------------------


def _classify_results(
    artifact: dict[str, Any],
    tanimoto_cutoff: float,
    exact_match_cutoff: float,
) -> tuple[str, list[dict[str, str]]]:
    """Classify a stored similarity/substructure result.

    Returns ``(verdict, relays)`` where *verdict* is one of
    ``"exact-match"``, ``"known-compound-found"``, ``"novel"``, or
    ``"indeterminate"``.

    A failed search (``search_status == "failed"``) always produces
    ``"indeterminate"`` — a search that did not execute must NEVER be
    indistinguishable from a clean novelty result (#84).
    """
    search_status = artifact.get("search_status", "completed")
    hits = artifact.get("hits", [])

    # Guarded relay emission.
    relays: list[dict[str, str]] = []

    # ── Failed search → indeterminate ────────────────────────────────
    if search_status == "failed":
        failure_reason = artifact.get("failure_reason", "unknown")
        relays.append(
            provenance.relay(
                "similar.all_backends_failed",
                f"All similarity search backends failed ({failure_reason}). "
                "Novelty assessment is incomplete.",
            )
        )
        # UNCONDITIONAL: always fires.
        relays.append(
            provenance.relay(
                "similar.database_coverage_limited",
                "Search covered PubChem (~116M compounds) and/or ChEMBL "
                "(~2.4M bioactive molecules); compounds with no similar hits "
                "may have close analogs in proprietary collections, patent "
                "literature, or databases not queried",
            )
        )
        return "indeterminate", relays

    # ── Partial search → fire search_incomplete relay ────────────────
    if search_status == "partial":
        failure_reason = artifact.get("failure_reason", "one or more backends failed")
        relays.append(
            provenance.relay(
                "similar.search_incomplete",
                f"Similarity search did not complete ({failure_reason}). "
                "Do not interpret the absence of similar compounds as "
                "confirmed novelty.",
            )
        )

    # ── Normal classification on available hits ──────────────────────
    # Find the highest Tanimoto score across all hits.
    max_tanimoto = 0.0
    has_any_hit = len(hits) > 0
    for hit in hits:
        t = hit.get("tanimoto")
        if t is not None and t > max_tanimoto:
            max_tanimoto = t

    if max_tanimoto >= exact_match_cutoff:
        verdict = "exact-match"
    elif has_any_hit and max_tanimoto >= tanimoto_cutoff:
        verdict = "known-compound-found"
    else:
        verdict = "novel"

    # GUARDED: fires ONLY when at least one hit is returned.
    if has_any_hit:
        relays.append(
            provenance.relay(
                "similar.tanimoto_is_2d_only",
                f"Tanimoto similarity computed from 2D fingerprints for query "
                f"{artifact.get('query', {}).get('smiles', '?')}; max "
                f"Tanimoto={max_tanimoto:.3f} reflects shared substructure "
                "topology, not 3D shape complementarity or biological activity",
            )
        )

    # UNCONDITIONAL: fires on every analysis (justified — see plan doc).
    relays.append(
        provenance.relay(
            "similar.database_coverage_limited",
            "Search covered PubChem (~116M compounds) and/or ChEMBL "
            "(~2.4M bioactive molecules); compounds with no similar hits "
            "may have close analogs in proprietary collections, patent "
            "literature, or databases not queried",
        )
    )

    return verdict, relays


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------


@click.group("similar", cls=DDEGroup)
def similar() -> None:
    """Structure similarity and substructure search."""


@similar.command("search")
@click.argument("smiles")
@click.option(
    "--source",
    type=click.Choice(["pubchem", "chembl", "both"]),
    default="both",
    help="Which database(s) to query (default: both).",
)
@click.option(
    "--threshold",
    type=click.FloatRange(0.0, 1.0),
    default=0.85,
    show_default=True,
    help="Tanimoto similarity threshold (0.0-1.0).",
)
@click.option(
    "--max-results",
    type=click.IntRange(1, 100),
    default=20,
    show_default=True,
    help="Maximum number of results to return (1-100).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    smiles: str,
    source: str,
    threshold: float,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Tanimoto similarity search for SMILES against PubChem/ChEMBL."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Validate SMILES before network call.
    canonical = _validate_smiles(smiles)
    slug = _slug(canonical)

    all_hits: list[dict[str, Any]] = []
    endpoints_used: list[str] = []
    backend_results: list[dict[str, Any]] = []
    backends_attempted = 0
    backends_failed = 0
    failure_reasons: list[str] = []

    if source in ("pubchem", "both"):
        backends_attempted += 1
        try:
            pubchem_hits = _pubchem_similarity(canonical, threshold, max_results)
            all_hits.extend(pubchem_hits)
            endpoints_used.append(f"{PUBCHEM_API}/compound/similarity")
            backend_results.append(
                {
                    "backend": "pubchem",
                    "status": "completed",
                    "hit_count": len(pubchem_hits),
                }
            )
        except (EndpointError, EndpointUnavailable, ArtifactError) as exc:
            backends_failed += 1
            reason = f"pubchem: {exc.message}"
            failure_reasons.append(reason)
            backend_results.append(
                {
                    "backend": "pubchem",
                    "status": "failed",
                    "failure_reason": str(exc.message),
                }
            )

    if source in ("chembl", "both"):
        backends_attempted += 1
        try:
            chembl_hits = _chembl_similarity(canonical, threshold, max_results)
            all_hits.extend(chembl_hits)
            endpoints_used.append(f"{CHEMBL_API}/similarity")
            backend_results.append(
                {
                    "backend": "chembl",
                    "status": "completed",
                    "hit_count": len(chembl_hits),
                }
            )
        except (EndpointError, EndpointUnavailable, ArtifactError) as exc:
            backends_failed += 1
            reason = f"chembl: {exc.message}"
            failure_reasons.append(reason)
            backend_results.append(
                {
                    "backend": "chembl",
                    "status": "failed",
                    "failure_reason": str(exc.message),
                }
            )

    # Determine search status.
    if backends_failed == 0:
        search_status = "completed"
    elif backends_failed < backends_attempted:
        search_status = "partial"
    else:
        search_status = "failed"

    failure_reason = "; ".join(failure_reasons) if failure_reasons else None

    # Merge, deduplicate, sort, cap.
    merged = _merge_hits(all_hits, max_results)

    artifact = _build_artifact(
        canonical,
        "similarity",
        source,
        threshold,
        max_results,
        merged,
        search_status=search_status,
        failure_reason=failure_reason,
        backend_results=backend_results,
    )

    artifact_path = target_dir / f"{slug}.similar-{source}.json"
    artifact_path.write_text(
        json.dumps(artifact, indent=2) + "\n",
        encoding="utf-8",
    )

    # Build sidecar.
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=", ".join(endpoints_used),
        parameters={
            "input_smiles": smiles,
            "canonical_smiles": canonical,
            "source": source,
            "threshold": threshold,
            "max_results": max_results,
        },
    )
    sidecar.note("display_name", canonical)
    sidecar.note("search_type", "similarity")
    sidecar.note("n_hits", len(merged))
    if source in ("pubchem", "both"):
        sidecar.note(
            "pubchem_score_note",
            f"PubChem Tanimoto scores are server-side filtered at "
            f"threshold={threshold}; reported values are guaranteed lower "
            f"bounds, not exact per-hit similarities",
        )
    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(
        target_dir / f"{slug}.similar-{source}.meta.json",
    )

    # Output.
    emit.data("smiles", canonical)
    emit.data("source", source)
    emit.data("threshold", threshold)
    emit.data("n_hits", len(merged))
    closest = artifact["summary"]["closest_match"]
    if closest:
        emit.data("closest_match", closest)
    emit.line(f"similarity search: {canonical}")
    emit.line(f"  source={source}, threshold={threshold}, hits={len(merged)}")
    if closest:
        emit.line(
            f"  closest: {closest.get('name', '?')} "
            f"(tanimoto={closest.get('tanimoto', '?')})"
        )
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@similar.command("substructure")
@click.argument("smiles")
@click.option(
    "--source",
    type=click.Choice(["pubchem", "chembl", "both"]),
    default="both",
    help="Which database(s) to query (default: both).",
)
@click.option(
    "--max-results",
    type=click.IntRange(1, 100),
    default=20,
    show_default=True,
    help="Maximum number of results to return (1-100).",
)
@out_option
@output_options
@pass_state
def substructure_cmd(
    state: AppState,
    smiles: str,
    source: str,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Substructure match search for SMILES against PubChem/ChEMBL."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Validate SMILES before network call.
    canonical = _validate_smiles(smiles)
    slug = _slug(canonical)

    all_hits: list[dict[str, Any]] = []
    endpoints_used: list[str] = []
    backend_results: list[dict[str, Any]] = []
    backends_attempted = 0
    backends_failed = 0
    failure_reasons: list[str] = []

    if source in ("pubchem", "both"):
        backends_attempted += 1
        try:
            pubchem_hits = _pubchem_substructure(canonical, max_results)
            all_hits.extend(pubchem_hits)
            endpoints_used.append(f"{PUBCHEM_API}/compound/substructure")
            backend_results.append(
                {
                    "backend": "pubchem",
                    "status": "completed",
                    "hit_count": len(pubchem_hits),
                }
            )
        except (EndpointError, EndpointUnavailable, ArtifactError) as exc:
            backends_failed += 1
            reason = f"pubchem: {exc.message}"
            failure_reasons.append(reason)
            backend_results.append(
                {
                    "backend": "pubchem",
                    "status": "failed",
                    "failure_reason": str(exc.message),
                }
            )

    if source in ("chembl", "both"):
        backends_attempted += 1
        try:
            chembl_hits = _chembl_substructure(canonical, max_results)
            all_hits.extend(chembl_hits)
            endpoints_used.append(f"{CHEMBL_API}/substructure")
            backend_results.append(
                {
                    "backend": "chembl",
                    "status": "completed",
                    "hit_count": len(chembl_hits),
                }
            )
        except (EndpointError, EndpointUnavailable, ArtifactError) as exc:
            backends_failed += 1
            reason = f"chembl: {exc.message}"
            failure_reasons.append(reason)
            backend_results.append(
                {
                    "backend": "chembl",
                    "status": "failed",
                    "failure_reason": str(exc.message),
                }
            )

    # Determine search status.
    if backends_failed == 0:
        search_status = "completed"
    elif backends_failed < backends_attempted:
        search_status = "partial"
    else:
        search_status = "failed"

    failure_reason = "; ".join(failure_reasons) if failure_reasons else None

    # Merge, deduplicate, cap.
    merged = _merge_hits(all_hits, max_results)

    artifact = _build_artifact(
        canonical,
        "substructure",
        source,
        None,
        max_results,
        merged,
        search_status=search_status,
        failure_reason=failure_reason,
        backend_results=backend_results,
    )

    artifact_path = target_dir / f"{slug}.substruct-{source}.json"
    artifact_path.write_text(
        json.dumps(artifact, indent=2) + "\n",
        encoding="utf-8",
    )

    # Build sidecar.
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="substructure",
        endpoint=", ".join(endpoints_used),
        parameters={
            "input_smiles": smiles,
            "canonical_smiles": canonical,
            "source": source,
            "max_results": max_results,
        },
    )
    sidecar.note("display_name", canonical)
    sidecar.note("search_type", "substructure")
    sidecar.note("n_hits", len(merged))
    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(
        target_dir / f"{slug}.substruct-{source}.meta.json",
    )

    # Output.
    emit.data("smiles", canonical)
    emit.data("source", source)
    emit.data("n_hits", len(merged))
    emit.line(f"substructure search: {canonical}")
    emit.line(f"  source={source}, hits={len(merged)}")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@similar.command("analyze")
@click.argument("smiles")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    smiles: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Classify a stored similarity/substructure search result.  No network.

    Reads the artifact written by `search` or `substructure` and
    produces a verdict: exact-match, known-compound-found, or novel.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Validate SMILES for slug derivation (no network in phase 2).
    canonical = _validate_smiles(smiles)
    slug = _slug(canonical)

    # Look for similarity artifact first, then substructure.
    artifact_path = None
    for pattern in (
        "similar-both",
        "similar-pubchem",
        "similar-chembl",
        "substruct-both",
        "substruct-pubchem",
        "substruct-chembl",
    ):
        candidate = source_dir / f"{slug}.{pattern}.json"
        if candidate.is_file():
            artifact_path = candidate
            break

    if artifact_path is None:
        raise ArtifactError(
            f"no similarity/substructure artifact for {canonical}",
            remedy=f"run `dde similar search '{smiles}'` or "
            f"`dde similar substructure '{smiles}'` first",
        )

    artifact = provenance.read_json(artifact_path, "similarity artifact")
    if artifact.get("schema") != SCHEMA:
        from ..core.errors import SchemaError

        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected {SCHEMA}, got {artifact.get('schema')!r}",
        )

    # Locate sidecar.
    meta_name = artifact_path.name.replace(".json", ".meta.json")
    meta_path = source_dir / meta_name
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        meta = provenance.read_json(meta_path, "similarity sidecar")

    thresholds = load_thresholds(state, "similar-search")
    tanimoto_cutoff = thresholds.get("tanimoto_similarity_cutoff")
    exact_match_cutoff = thresholds.get("exact_match_cutoff")

    verdict, relays = _classify_results(artifact, tanimoto_cutoff, exact_match_cutoff)

    # Carry upstream relays from the sidecar.
    seen_codes: set[str] = {r["code"] for r in relays}
    for r in meta.get("mandatory_relays", []) or []:
        if r["code"] not in seen_codes:
            relays.append(r)
            seen_codes.add(r["code"])

    hits = artifact.get("hits", [])
    max_tanimoto = 0.0
    for hit in hits:
        t = hit.get("tanimoto")
        if t is not None and t > max_tanimoto:
            max_tanimoto = t

    search_status = artifact.get("search_status", "completed")
    assessment: dict[str, Any] = {
        "outcome": verdict,
        "smiles": canonical,
        "search_status": search_status,
        "n_hits": len(hits),
        "max_tanimoto": max_tanimoto,
        "search_type": artifact.get("query", {}).get("search_type"),
        "source": artifact.get("query", {}).get("source"),
    }
    if artifact.get("failure_reason"):
        assessment["failure_reason"] = artifact["failure_reason"]

    metrics = {
        "n_hits": len(hits),
        "max_tanimoto": max_tanimoto,
        "closest_match": artifact.get("summary", {}).get("closest_match"),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.similar.analysis.json",
        source=state.project().relative(meta_path)
        if meta_path.is_file()
        else str(artifact_path),
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

    # Output.
    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(
        f"{canonical} -> {verdict.upper()} "
        f"[hits={len(hits)}, max_tanimoto={max_tanimoto:.3f}]"
    )
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
