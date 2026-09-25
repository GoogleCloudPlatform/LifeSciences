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

"""`dde pubchem` — compound annotation from PubChem and ChEMBL.

Retrieves published annotations about a compound — synonyms, trade
names, pharmacological classifications, drug development status, and
mechanism of action — and classifies the compound as known-drug,
known-compound, or unknown.

Two phases:

  annotate           fetches synonyms, classification, and (when
                     available) ChEMBL drug development data, and
                     writes them to Layer 0 with a sidecar.
  analyze-annotation reads the stored annotation and produces a
                     verdict.  No network.

This is distinct from compreg, which answers "does the compound
exist?".  Annotation answers "what is already known about it?" —
the enrichment step after identity resolution.

Input is a PubChem CID (integer). Resolve a name or structure to a
CID first via `dde compreg resolve`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

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
from ..core.errors import SchemaError
from ..core.paths import sanitize_slug
from ..core.qps import qps_for_host

TOOL = "pubchem"
ARTIFACT_CLASS = "compounds"

PUBCHEM_API = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

CHEMBL_API = "https://www.ebi.ac.uk/chembl/api/data"

SCHEMA = "dde.pubchem-annotation.v1"
FETCH_SCHEMA = "dde.pubchem-compound.v1"

# Maximum synonyms to store — PubChem can return hundreds.
MAX_SYNONYMS = 20


def _slug(cid: int) -> str:
    """Filesystem-safe slug for a CID."""
    return f"cid-{cid}"


# ---------------------------------------------------------------------------
# Phase 1 — network fetches
# ---------------------------------------------------------------------------


def _fetch_synonyms(cid: int) -> tuple[str, bytes]:
    """Fetch synonyms for a CID from PubChem.

    A 404 means the CID does not exist on PubChem.  This is tolerated
    and recorded as a not-found artifact.
    """
    url = f"{PUBCHEM_API}/compound/cid/{cid}/synonyms/JSON"
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
        raise SchemaError(
            "PubChem synonyms did not return JSON", detail=str(exc)
        ) from exc
    return url, body


def _fetch_classification(cid: int) -> tuple[str, bytes]:
    """Fetch pharmacological classification for a CID from PubChem."""
    url = f"{PUBCHEM_API}/compound/cid/{cid}/classification/JSON"
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
        raise SchemaError(
            "PubChem classification did not return JSON",
            detail=str(exc),
        ) from exc
    return url, body


def _fetch_inchikey(cid: int) -> tuple[str, bytes]:
    """Fetch InChIKey for CID-to-ChEMBL cross-reference."""
    url = f"{PUBCHEM_API}/compound/cid/{cid}/property/InChIKey/JSON"
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
        raise SchemaError(
            "PubChem InChIKey did not return JSON",
            detail=str(exc),
        ) from exc
    return url, body


def _fetch_chembl_molecule(inchikey: str) -> tuple[str, bytes]:
    """Look up a ChEMBL molecule record by InChIKey.

    A 404 means ChEMBL does not have this compound — that is fine,
    PubChem-only annotation is valid.
    """
    url = f"{CHEMBL_API}/molecule/{inchikey}.json"
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("www.ebi.ac.uk"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    if response.status_code == 404:
        return url, json.dumps(
            {"_not_found": True, "inchikey": inchikey, "http_status": 404},
            indent=2,
        ).encode("utf-8")
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "ChEMBL molecule did not return JSON", detail=str(exc)
        ) from exc
    return url, body


def _fetch_chembl_mechanism(chembl_id: str) -> tuple[str, bytes]:
    """Fetch mechanism of action for a ChEMBL molecule."""
    url = f"{CHEMBL_API}/mechanism.json?molecule_chembl_id={chembl_id}"
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
        raise SchemaError(
            "ChEMBL mechanism did not return JSON", detail=str(exc)
        ) from exc
    return url, body


# ---------------------------------------------------------------------------
# Data extraction (pure — reads dicts, not network)
# ---------------------------------------------------------------------------


def _extract_synonyms(payload: Any) -> list[str]:
    """Extract top synonyms from stored PubChem synonyms response."""
    if isinstance(payload, dict) and payload.get("_not_found"):
        return []
    if not isinstance(payload, dict):
        return []
    info_list = payload.get("InformationList", {}).get("Information", [])
    for info in info_list:
        synonyms = info.get("Synonym", [])
        if synonyms:
            return synonyms[:MAX_SYNONYMS]
    return []


def _extract_classification(payload: Any) -> tuple[list[str], list[str]]:
    """Extract pharmacological classification and actions.

    Returns (classification_list, actions_list).
    """
    if isinstance(payload, dict) and payload.get("_not_found"):
        return [], []
    if not isinstance(payload, dict):
        return [], []

    classifications: list[str] = []
    actions: list[str] = []

    hierarchies = payload.get("Hierarchies", {}).get("Hierarchy", [])
    for hierarchy in hierarchies:
        source_name = hierarchy.get("SourceName", "")
        nodes = hierarchy.get("Node", [])
        for node in nodes:
            info = node.get("Information", {})
            name = info.get("Name", "")
            if not name:
                continue
            # MeSH Pharmacological Actions
            if "pharmacological" in source_name.lower():
                actions.append(name)
            else:
                classifications.append(name)

    # Deduplicate while preserving order.
    seen_c: set[str] = set()
    dedup_c: list[str] = []
    for c in classifications:
        if c not in seen_c:
            seen_c.add(c)
            dedup_c.append(c)

    seen_a: set[str] = set()
    dedup_a: list[str] = []
    for a in actions:
        if a not in seen_a:
            seen_a.add(a)
            dedup_a.append(a)

    return dedup_c[:20], dedup_a[:20]


def _extract_inchikey(payload: Any) -> str | None:
    """Extract InChIKey from PubChem property response."""
    if isinstance(payload, dict) and payload.get("_not_found"):
        return None
    if not isinstance(payload, dict):
        return None
    props = payload.get("PropertyTable", {}).get("Properties", [])
    for p in props:
        key = p.get("InChIKey")
        if key:
            return key
    return None


def _extract_chembl_data(
    mol_payload: Any,
    moa_payload: Any,
) -> dict[str, Any]:
    """Extract drug status and mechanism data from stored ChEMBL responses."""
    result: dict[str, Any] = {
        "chembl_id": None,
        "max_phase": None,
        "molecule_type": None,
        "mechanisms": [],
    }

    if isinstance(mol_payload, dict) and not mol_payload.get("_not_found"):
        result["chembl_id"] = mol_payload.get("molecule_chembl_id")
        result["max_phase"] = mol_payload.get("max_phase")
        result["molecule_type"] = mol_payload.get("molecule_type")

    if isinstance(moa_payload, dict) and not moa_payload.get("_not_found"):
        mechanisms = moa_payload.get("mechanisms", []) or []
        for mech in mechanisms:
            if not isinstance(mech, dict):
                continue
            result["mechanisms"].append(
                {
                    "target_name": mech.get("target_name"),
                    "target_chembl_id": mech.get("target_chembl_id"),
                    "action_type": mech.get("action_type"),
                    "source": "chembl",
                }
            )

    return result


def _build_artifact(
    cid: int,
    synonyms: list[str],
    classification: list[str],
    actions: list[str],
    chembl_data: dict[str, Any],
) -> dict[str, Any]:
    """Build the structured annotation artifact."""
    # Separate trade names (all-caps or contain (R), (TM), etc.)
    trade_name_re = re.compile(r"[®™]|\([RTM]+\)", re.IGNORECASE)
    trade_names: list[str] = []
    regular_synonyms: list[str] = []
    for s in synonyms:
        if trade_name_re.search(s):
            trade_names.append(s)
        else:
            regular_synonyms.append(s)

    artifact: dict[str, Any] = {
        "schema": SCHEMA,
        "query": {
            "cid": cid,
            "chembl_id": chembl_data.get("chembl_id"),
        },
        "synonyms": {
            "count": len(synonyms),
            "top": regular_synonyms[:MAX_SYNONYMS],
            "trade_names": trade_names[:10],
        },
        "pharmacology": {
            "classification": classification,
            "actions": actions,
        },
        "drug_status": {
            "max_phase": chembl_data.get("max_phase"),
            "molecule_type": chembl_data.get("molecule_type"),
            "source": "chembl" if chembl_data.get("chembl_id") else None,
        },
        "mechanisms": chembl_data.get("mechanisms", []),
    }
    return artifact


# ---------------------------------------------------------------------------
# Classification (extracted for testability — R1 review fix)
# ---------------------------------------------------------------------------


def _classify_annotation(
    annotation: dict[str, Any],
    cid: int,
) -> tuple[str, list[dict[str, str]]]:
    """Classify an annotation artifact and produce guarded relays.

    Returns ``(verdict, relays)`` where *verdict* is one of
    ``"known-drug"``, ``"known-compound"``, or ``"unknown"``, and
    *relays* is a list of relay records that should be attached to the
    analysis.
    """
    drug_status = annotation.get("drug_status", {})
    max_phase = drug_status.get("max_phase")
    synonyms = annotation.get("synonyms", {})
    pharmacology = annotation.get("pharmacology", {})

    has_drug_data = max_phase is not None and max_phase >= 1
    has_annotations = bool(
        synonyms.get("top")
        or pharmacology.get("classification")
        or pharmacology.get("actions")
    )

    if has_drug_data:
        verdict = "known-drug"
    elif has_annotations:
        verdict = "known-compound"
    else:
        verdict = "unknown"

    # Guarded relays.
    relays: list[dict[str, str]] = []

    if verdict in ("known-drug", "known-compound"):
        relays.append(
            provenance.relay(
                "pubchem.annotation_is_not_validation",
                f"CID {cid} has published annotations (synonyms, "
                "pharmacological classification) that are database records, "
                "not in-house validation",
            )
        )

    if has_drug_data:
        relays.append(
            provenance.relay(
                "pubchem.drug_status_is_development_history",
                f"CID {cid} has ChEMBL max_phase={max_phase}; this is "
                "development history, not a current regulatory status for "
                "the indication under study",
            )
        )

    return verdict, relays


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group("pubchem", cls=DDEGroup)
def pubchem() -> None:
    """Compound annotation from PubChem and ChEMBL."""


# ---------------------------------------------------------------------------
# fetch — CID-to-compound-record (SMILES, InChIKey, formula, weight)
# ---------------------------------------------------------------------------

_PROPERTY_FIELDS = (
    "CanonicalSMILES,IsomericSMILES,InChIKey,MolecularFormula,MolecularWeight"
)


def _fetch_properties(cid: int) -> tuple[str, dict[str, Any] | None]:
    """Fetch the property endpoint for *cid*.

    Returns ``(url, props_dict | None)``.  *None* means the CID does
    not exist on PubChem (404).
    """
    url = f"{PUBCHEM_API}/compound/cid/{cid}/property/{_PROPERTY_FIELDS}/JSON"
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"),
        timeout=60.0,
        tolerate_status=(404,),
    )
    if response.status_code == 404:
        return url, None
    body = response.content
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "PubChem property endpoint did not return JSON", detail=str(exc)
        ) from exc
    props_list = payload.get("PropertyTable", {}).get("Properties", [])
    if not props_list:
        return url, None
    return url, props_list[0]


def _fetch_full_record(cid: int) -> tuple[str, dict[str, Any]]:
    """Fetch the full compound record for *cid*.

    Used as a fallback when the property endpoint returns N/A for
    SMILES fields.
    """
    url = f"{PUBCHEM_API}/compound/cid/{cid}/JSON"
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("pubchem.ncbi.nlm.nih.gov"),
        timeout=60.0,
    )
    body = response.content
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "PubChem full record did not return JSON", detail=str(exc)
        ) from exc
    return url, payload


def _extract_smiles_from_full_record(payload: dict[str, Any]) -> dict[str, str]:
    """Parse SMILES from the ``urn`` structure in a full PubChem record.

    Walks ``PC_Compounds[0].props`` looking for entries whose
    ``urn.label`` is ``"SMILES"``.  The ``urn.name`` sub-field
    distinguishes ``"Canonical"`` from ``"Isomeric"``.
    """
    result: dict[str, str] = {}
    compounds = payload.get("PC_Compounds", [])
    if not compounds:
        return result
    props = compounds[0].get("props", [])
    for prop in props:
        urn = prop.get("urn", {})
        if urn.get("label") != "SMILES":
            continue
        name = (urn.get("name") or "").lower()
        value_obj = prop.get("value", {})
        sval = value_obj.get("sval", "")
        if not sval:
            continue
        if "canonical" in name:
            result["canonical"] = sval
        elif "isomeric" in name:
            result["isomeric"] = sval
        else:
            # Fallback: if there is a SMILES entry without a clear
            # canonical/isomeric label, treat it as canonical.
            result.setdefault("canonical", sval)
    return result


def _needs_fallback(props: dict[str, Any]) -> bool:
    """Return True if the property response has N/A or empty SMILES."""
    canonical = props.get("CanonicalSMILES", "")
    isomeric = props.get("IsomericSMILES", "")
    return not canonical or canonical == "N/A" or not isomeric or isomeric == "N/A"


def _build_compound_artifact(
    cid: int,
    canonical_smiles: str,
    isomeric_smiles: str,
    inchikey: str,
    molecular_formula: str,
    molecular_weight: float | None,
    source: str,
) -> dict[str, Any]:
    """Build the structured compound artifact."""
    return {
        "schema": FETCH_SCHEMA,
        "cid": cid,
        "canonical_smiles": canonical_smiles,
        "isomeric_smiles": isomeric_smiles,
        "inchikey": inchikey,
        "molecular_formula": molecular_formula,
        "molecular_weight": molecular_weight,
        "source": source,
        "retrieved": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@pubchem.command("fetch")
@click.argument("cids", type=int, nargs=-1, required=True)
@click.option(
    "--name",
    "slug_override",
    default=None,
    help="Override the output filename slug (default: CID as string).",
)
@out_option
@output_options
@pass_state
def fetch_cmd(
    state: AppState,
    cids: tuple[int, ...],
    slug_override: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Fetch compound data (SMILES, InChIKey, formula, weight) for CID(s).

    CID is one or more PubChem Compound IDs (integers). Resolve a
    compound name or structure to a CID first via `dde compreg resolve`.

    Falls back to the full record endpoint when the property endpoint
    returns N/A for SMILES fields (a known PubChem quirk for some
    compounds).
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    for cid in cids:
        slug = f"{sanitize_slug(slug_override)}-{cid}" if slug_override else str(cid)

        # --- Try the property endpoint first ---
        prop_url, props = _fetch_properties(cid)

        if props is None:
            # CID not found — write a not-found artifact.
            not_found_artifact: dict[str, Any] = {
                "schema": FETCH_SCHEMA,
                "_not_found": True,
                "cid": cid,
            }
            artifact_path = target_dir / f"{slug}.pubchem-compound.artifact.json"
            artifact_path.write_text(
                json.dumps(not_found_artifact, indent=2) + "\n",
                encoding="utf-8",
            )
            sidecar = provenance.Sidecar(
                tool=TOOL,
                subcommand="fetch",
                endpoint=prop_url,
                parameters={"cid": cid},
            )
            sidecar.note("not_found", True)
            sidecar.add_output(artifact_path)
            meta_path = sidecar.write(
                target_dir / f"{slug}.pubchem-compound.meta.json",
            )
            emit.data("cid", cid)
            emit.data("status", "not_found")
            emit.line(f"CID {cid}: not found on PubChem")
            emit.path(artifact_path, role="compound")
            emit.path(meta_path, role="sidecar")
            emit.flush()
            continue

        # --- Check for N/A quirk and fallback ---
        source = "property"
        canonical_smiles = props.get("CanonicalSMILES", "")
        isomeric_smiles = props.get("IsomericSMILES", "")
        inchikey = props.get("InChIKey", "")
        molecular_formula = props.get("MolecularFormula", "")
        molecular_weight: float | None = None
        raw_weight = props.get("MolecularWeight")
        if raw_weight is not None:
            try:
                molecular_weight = float(raw_weight)
            except (TypeError, ValueError):
                pass

        endpoints_used: list[str] = [prop_url]

        if _needs_fallback(props):
            full_url, full_payload = _fetch_full_record(cid)
            endpoints_used.append(full_url)
            smiles = _extract_smiles_from_full_record(full_payload)
            if smiles.get("canonical"):
                canonical_smiles = smiles["canonical"]
            if smiles.get("isomeric"):
                isomeric_smiles = smiles["isomeric"]
            source = "full_record"

        # --- Build and write artifact ---
        artifact = _build_compound_artifact(
            cid=cid,
            canonical_smiles=canonical_smiles,
            isomeric_smiles=isomeric_smiles,
            inchikey=inchikey,
            molecular_formula=molecular_formula,
            molecular_weight=molecular_weight,
            source=source,
        )

        artifact_path = target_dir / f"{slug}.pubchem-compound.artifact.json"
        artifact_path.write_text(
            json.dumps(artifact, indent=2) + "\n",
            encoding="utf-8",
        )

        # --- Sidecar ---
        sidecar = provenance.Sidecar(
            tool=TOOL,
            subcommand="fetch",
            endpoint=", ".join(endpoints_used),
            parameters={"cid": cid},
        )
        sidecar.note("source", source)
        sidecar.note("canonical_smiles", canonical_smiles)
        sidecar.add_output(artifact_path)
        meta_path = sidecar.write(
            target_dir / f"{slug}.pubchem-compound.meta.json",
        )

        # --- Output ---
        emit.data("cid", cid)
        emit.data("canonical_smiles", canonical_smiles)
        emit.data("isomeric_smiles", isomeric_smiles)
        emit.data("inchikey", inchikey)
        emit.data("molecular_formula", molecular_formula)
        emit.data("molecular_weight", molecular_weight)
        emit.data("source", source)
        emit.line(f"CID {cid}: {canonical_smiles}")
        emit.line(f"  InChIKey: {inchikey}")
        emit.line(f"  formula: {molecular_formula}  MW: {molecular_weight}")
        if source == "full_record":
            emit.line("  (SMILES resolved via full record fallback)")
        emit.path(artifact_path, role="compound")
        emit.path(meta_path, role="sidecar")
        emit.flush()


@pubchem.command("annotate")
@click.argument("cid", type=int)
@out_option
@output_options
@pass_state
def annotate_cmd(
    state: AppState,
    cid: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Fetch compound annotation for CID and write to Layer 0.

    CID is a PubChem Compound ID (integer). Resolve a compound name
    or structure to a CID first via `dde compreg resolve`.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = _slug(cid)

    endpoints_used: list[str] = []

    # --- PubChem synonyms ---
    syn_url, syn_body = _fetch_synonyms(cid)
    syn_payload = json.loads(syn_body.decode("utf-8"))
    endpoints_used.append(syn_url)

    # If the CID was not found, write a not-found artifact and exit 0.
    if isinstance(syn_payload, dict) and syn_payload.get("_not_found"):
        not_found_artifact = {
            "schema": SCHEMA,
            "_not_found": True,
            "query": {"cid": cid},
        }
        artifact_path = target_dir / f"{slug}.pubchem-annotation.json"
        artifact_path.write_text(
            json.dumps(not_found_artifact, indent=2) + "\n",
            encoding="utf-8",
        )
        sidecar = provenance.Sidecar(
            tool=TOOL,
            subcommand="annotate",
            endpoint=syn_url,
            parameters={"cid": cid},
        )
        sidecar.note("not_found", True)
        sidecar.add_output(artifact_path)
        meta_path = sidecar.write(target_dir / f"{slug}.pubchem-annotation.meta.json")

        emit.data("cid", cid)
        emit.data("status", "not_found")
        emit.line(f"CID {cid}: not found on PubChem")
        emit.path(artifact_path, role="annotation")
        emit.path(meta_path, role="sidecar")
        emit.flush()
        return

    # --- PubChem classification ---
    cls_url, cls_body = _fetch_classification(cid)
    cls_payload = json.loads(cls_body.decode("utf-8"))
    endpoints_used.append(cls_url)

    # --- PubChem InChIKey (for ChEMBL cross-reference) ---
    ik_url, ik_body = _fetch_inchikey(cid)
    ik_payload = json.loads(ik_body.decode("utf-8"))
    endpoints_used.append(ik_url)

    inchikey = _extract_inchikey(ik_payload)

    # --- ChEMBL enrichment (optional — 404 is fine) ---
    chembl_mol_payload: dict[str, Any] = {"_not_found": True}
    chembl_moa_payload: dict[str, Any] = {"_not_found": True}

    if inchikey:
        mol_url, mol_body = _fetch_chembl_molecule(inchikey)
        chembl_mol_payload = json.loads(mol_body.decode("utf-8"))
        endpoints_used.append(mol_url)

        # If ChEMBL found the molecule, fetch MoA.
        if not chembl_mol_payload.get("_not_found"):
            chembl_id = chembl_mol_payload.get("molecule_chembl_id")
            if chembl_id:
                moa_url, moa_body = _fetch_chembl_mechanism(chembl_id)
                chembl_moa_payload = json.loads(moa_body.decode("utf-8"))
                endpoints_used.append(moa_url)

    # --- Build structured artifact ---
    synonyms = _extract_synonyms(syn_payload)
    classification, actions = _extract_classification(cls_payload)
    chembl_data = _extract_chembl_data(chembl_mol_payload, chembl_moa_payload)

    artifact = _build_artifact(cid, synonyms, classification, actions, chembl_data)

    artifact_path = target_dir / f"{slug}.pubchem-annotation.json"
    artifact_path.write_text(
        json.dumps(artifact, indent=2) + "\n",
        encoding="utf-8",
    )

    # --- Sidecar ---
    compound_name = synonyms[0] if synonyms else f"CID {cid}"
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="annotate",
        endpoint=", ".join(endpoints_used),
        parameters={"cid": cid, "inchikey": inchikey},
    )
    sidecar.note("compound_name", compound_name)
    sidecar.note("display_name", compound_name)
    sidecar.note("chembl_id", chembl_data.get("chembl_id"))
    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(target_dir / f"{slug}.pubchem-annotation.meta.json")

    # --- Output ---
    emit.data("cid", cid)
    emit.data("compound_name", compound_name)
    emit.data("synonym_count", len(synonyms))
    emit.data("chembl_id", chembl_data.get("chembl_id"))
    emit.data("max_phase", chembl_data.get("max_phase"))
    emit.line(f"CID {cid}: {compound_name}")
    if synonyms:
        emit.line(f"  synonyms ({len(synonyms)}): {', '.join(synonyms[:5])}")
    if classification:
        emit.line(f"  classification: {', '.join(classification[:3])}")
    if actions:
        emit.line(f"  actions: {', '.join(actions[:3])}")
    if chembl_data.get("chembl_id"):
        emit.line(
            f"  ChEMBL: {chembl_data['chembl_id']} "
            f"(max_phase={chembl_data.get('max_phase')})"
        )
        if chembl_data.get("mechanisms"):
            for mech in chembl_data["mechanisms"][:3]:
                emit.line(
                    f"    MoA: {mech.get('action_type')} → {mech.get('target_name')}"
                )
    emit.path(artifact_path, role="annotation")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@pubchem.command("analyze-annotation")
@click.argument("cid", type=int)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    cid: int,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Classify a stored compound annotation.  No network.

    Reads the annotation artifact written by `annotate` and produces
    a verdict: known-drug, known-compound, or unknown.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = _slug(cid)

    # Locate the annotation artifact.
    artifact_path = source_dir / f"{slug}.pubchem-annotation.json"
    annotation = provenance.read_json(artifact_path, "pubchem annotation")

    # Locate the sidecar.
    meta_path = source_dir / f"{slug}.pubchem-annotation.meta.json"
    meta = provenance.read_json(meta_path, "pubchem annotation sidecar")

    thresholds = load_thresholds(state, "pubchem-annotation")

    # Handle not-found artifacts.
    if annotation.get("_not_found"):
        assessment: dict[str, Any] = {
            "outcome": "not_found",
            "cid": cid,
        }
        analysis_path = provenance.write_analysis(
            target_dir / f"{slug}.pubchem-annotation.analysis.json",
            source=state.project().relative(meta_path),
            threshold_set=thresholds.tag,
            thresholds_applied=thresholds.applied(),
            metrics={},
            assessment=assessment,
            threshold_sources=thresholds.sources(),
            threshold_provenance=thresholds.provenance,
            unresolved=thresholds.unresolved(),
            mandatory_relays=[],
            suppress_warnings=as_json,
        )
        emit.data("assessment", assessment)
        emit.line(f"CID {cid}: NOT FOUND")
        emit.path(analysis_path, role="analysis")
        emit.flush()
        return

    verdict, relays = _classify_annotation(annotation, cid)

    # Carry upstream relays from the sidecar.
    seen_codes: set[str] = {r["code"] for r in relays}
    for r in meta.get("mandatory_relays", []) or []:
        if r["code"] not in seen_codes:
            relays.append(r)
            seen_codes.add(r["code"])

    # --- Assessment ---
    drug_status = annotation.get("drug_status", {})
    max_phase = drug_status.get("max_phase")
    synonyms = annotation.get("synonyms", {})
    pharmacology = annotation.get("pharmacology", {})
    mechanisms = annotation.get("mechanisms", [])

    assessment = {
        "outcome": verdict,
        "cid": cid,
        "chembl_id": annotation.get("query", {}).get("chembl_id"),
        "max_phase": max_phase,
        "synonym_count": synonyms.get("count", 0),
        "has_classification": bool(pharmacology.get("classification")),
        "has_actions": bool(pharmacology.get("actions")),
        "mechanism_count": len(mechanisms),
    }

    metrics = {
        "synonyms": synonyms,
        "pharmacology": pharmacology,
        "drug_status": drug_status,
        "mechanisms": mechanisms,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.pubchem-annotation.analysis.json",
        source=state.project().relative(meta_path),
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

    # --- Output ---
    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(
        f"CID {cid} -> {verdict.upper()}  "
        f"[synonyms={synonyms.get('count', 0)}, "
        f"max_phase={max_phase}]"
    )
    if verdict == "known-drug":
        emit.line(f"  max_phase: {max_phase}")
        if mechanisms:
            for mech in mechanisms[:3]:
                emit.line(
                    f"  MoA: {mech.get('action_type')} -> {mech.get('target_name')}"
                )
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
