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

"""`dde gwas` — GWAS, disease association, and clinical variant lookup.

Three public databases, three phases:

  search          one gene -> disease associations from Open Targets
                  Platform, the NHGRI-EBI GWAS Catalog, or ClinVar
                  clinical significance, written verbatim to Layer 0
                  with a sidecar.
  search-disease  one disease -> associated genes from Open Targets
                  Platform.  Resolves the disease name to an ontology
                  ID, then fetches the top associated target genes.
  analyze         reads stored search results and classifies whether
                  the gene has significant disease associations. No
                  network.

Open Targets uses a GraphQL endpoint. The gene symbol is first resolved
to an Ensembl ID via a search query, then associations are fetched for
that target. The GWAS Catalog uses a REST endpoint queried by gene name.

ClinVar uses NCBI E-utilities (esearch + esummary). The gene symbol is
searched in the clinvar database, returning a list of variant UIDs, then
a single batched esummary call fetches per-variant details including
germline classification, review status, and associated conditions. The
clinical significance field is `germline_classification.description`
(not the older `clinical_significance` field, which returns null/empty).

All three APIs return clean HTTP status codes for errors — unlike gnomAD,
none hides refusals inside 200 bodies — so the shared HTTP client's
retry logic handles transient failures.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

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
from ..core.errors import (
    ArtifactError,
    Refusal,
    SchemaError,
)
from ..core.ncbi import api_key_suffix
from ..core.paths import sanitize_slug
from ..core.qps import qps_for_host

TOOL = "gwas"
ARTIFACT_CLASS = "genomics"  # co-locate with gnomAD data in raw/genomics/

OPENTARGETS_API = "https://api.platform.opentargets.org/api/v4/graphql"

GWAS_CATALOG_API = "https://www.ebi.ac.uk/gwas/rest/api"

# NCBI E-utilities for ClinVar.  When NCBI_API_KEY is set,
# api_key_suffix() appends it and qps_for_host returns 10 req/s;
# without the key the rate falls to 3 req/s (see core/ncbi.py).
CLINVAR_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
CLINVAR_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
# Maximum variants to retrieve per gene. NCBI esearch default retmax is
# 20; 500 covers most genes adequately. Heavily-studied genes (BRCA1,
# TP53) may have more.
CLINVAR_RETMAX = 500

# ACMG clinical significance classifications considered "significant"
# (pathogenic findings) in the analyze phase. These are the curated
# clinical assertions that a variant causes the named condition.
_PATHOGENIC_CLASSIFICATIONS = frozenset(
    {
        "Pathogenic",
        "Likely pathogenic",
        "Pathogenic/Likely pathogenic",
    }
)

# Review-status tiers. A classification's weight depends on its review
# status — a "Pathogenic" call with "no assertion criteria provided" is
# materially weaker than one "reviewed by expert panel".
_STRONG_REVIEW = frozenset(
    {
        "reviewed by expert panel",
        "practice guideline",
    }
)
_MODERATE_REVIEW = frozenset(
    {
        "criteria provided, multiple submitters, no conflicts",
        "criteria provided, single submitter",
        "criteria provided, conflicting classifications",
    }
)

# ClinVar obj_type values (case-insensitive) that indicate locus-overlapping
# structural variants rather than gene-specific coding variants. A large CNV
# that happens to overlap a gene locus inflates the pathogenic count without
# being gene-specific evidence — issue #97.
_LOCUS_OVERLAPPING_OBJ_TYPES = re.compile(
    r"deletion|duplication|copy\s*number|structural\s*variant",
    re.IGNORECASE,
)

# Patterns in variant_title that indicate locus-overlapping variants when
# obj_type is absent (backward compatibility with older artifacts).
_LOCUS_OVERLAPPING_TITLE = re.compile(
    r"(?:"
    r"GRCh\d+/hg\d+"  # chromosomal coordinate prefix
    r"|chr\d+:\d+-\d+"  # explicit chromosomal range
    r"|\d+[pq]\d+"  # cytogenetic band notation
    r"|[Xx][pq]\d+"  # X-chromosome cytogenetic band
    r")"
    r".*"
    r"(?:x\d+|del|dup)?",  # optional copy-number suffix
    re.IGNORECASE,
)


def _classify_variant_type(assoc: dict[str, Any]) -> str:
    """Classify a ClinVar variant as gene_specific or locus_overlapping.

    Uses ``obj_type`` when available (preferred — directly from ClinVar
    esummary), falling back to heuristic parsing of ``variant_title``
    for artifacts created before obj_type was stored.

    Returns ``"gene_specific"`` or ``"locus_overlapping"``.
    """
    obj_type = assoc.get("obj_type", "")
    if obj_type and _LOCUS_OVERLAPPING_OBJ_TYPES.search(obj_type):
        return "locus_overlapping"

    # Fallback: infer from variant_title.
    title = assoc.get("variant_title", "")
    if title and _LOCUS_OVERLAPPING_TITLE.search(title):
        return "locus_overlapping"

    return "gene_specific"


# GraphQL query to resolve gene symbol to Ensembl ID via Open Targets.
_OT_SEARCH_QUERY = """\
query {
  search(queryString: "%s", entityNames: ["target"]) {
    hits { id name }
  }
}"""

# GraphQL query to fetch disease associations for a resolved target.
_OT_ASSOC_QUERY = """\
query {
  target(ensemblId: "%s") {
    associatedDiseases {
      rows {
        disease { id name }
        score
        datatypeScores { id score }
      }
    }
  }
}"""

# GraphQL query to search for a disease by name via Open Targets.
_OT_DISEASE_SEARCH_QUERY = """\
query {
  search(queryString: "%s", entityNames: ["disease"]) {
    hits { id name }
  }
}"""

# GraphQL query to fetch associated targets (genes) for a disease.
# Uses pagination with a size of 500 to capture the most strongly
# associated genes without overwhelming the response.
_OT_DISEASE_TARGETS_QUERY = """\
query {
  disease(efoId: "%s") {
    associatedTargets(page: {index: 0, size: 500}) {
      count
      rows {
        target { id approvedSymbol }
        score
        datatypeScores { id score }
      }
    }
  }
}"""

# Maximum associated targets to fetch per disease query.
_DISEASE_TARGET_PAGE_SIZE = 500


def _graphql_post(url: str, query: str, qps: float) -> dict[str, Any]:
    """POST a GraphQL query and return the parsed JSON payload."""
    body = json.dumps({"query": query})
    response = http.request(
        "POST",
        url,
        qps=qps,
        timeout=60.0,
        headers={"Content-Type": "application/json"},
        data=body.encode("utf-8"),
    )
    try:
        payload = json.loads(response.content.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("endpoint did not return JSON", detail=str(exc)) from exc
    if payload.get("errors"):
        messages = "; ".join(
            str(e.get("message", e)) for e in payload["errors"] if isinstance(e, dict)
        )
        raise Refusal(
            "GraphQL query was declined",
            detail=messages,
            remedy="check the query input and endpoint availability",
        )
    return payload


def _resolve_ensembl_id(symbol: str) -> tuple[str, str]:
    """Resolve a gene symbol to (ensembl_id, resolved_name) via Open Targets.

    Raises Refusal if the gene is not found.
    """
    payload = _graphql_post(
        OPENTARGETS_API,
        _OT_SEARCH_QUERY % symbol.upper(),
        qps_for_host("api.platform.opentargets.org"),
    )
    hits = (payload.get("data") or {}).get("search", {}).get("hits")
    if not hits:
        raise Refusal(
            f"Open Targets has no target record for {symbol!r}",
            remedy="check the gene symbol at platform.opentargets.org",
        )
    # Take the first hit — the search is by exact gene symbol.
    return hits[0]["id"], hits[0].get("name", symbol.upper())


def _resolve_disease_id(disease: str) -> tuple[str, str]:
    """Resolve a disease name to (disease_id, resolved_name) via Open Targets.

    Searches for the disease name and returns the top hit's ID (typically
    an EFO, MONDO, or similar ontology identifier) and canonical name.

    Raises Refusal if no disease matches the query.
    """
    payload = _graphql_post(
        OPENTARGETS_API,
        _OT_DISEASE_SEARCH_QUERY % disease,
        qps_for_host("api.platform.opentargets.org"),
    )
    hits = (payload.get("data") or {}).get("search", {}).get("hits")
    if not hits:
        raise Refusal(
            f"Open Targets has no disease record matching {disease!r}",
            remedy="check the disease name at platform.opentargets.org",
        )
    return hits[0]["id"], hits[0].get("name", disease)


def _fetch_opentargets_disease(disease: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch associated genes from Open Targets for a disease name.

    Two-step: resolve the disease name to an ontology ID, then fetch
    the top associated targets (genes) for that disease.

    Returns (verbatim response bytes, structured artifact dict).
    """
    disease_id, resolved_name = _resolve_disease_id(disease)
    payload = _graphql_post(
        OPENTARGETS_API,
        _OT_DISEASE_TARGETS_QUERY % disease_id,
        qps_for_host("api.platform.opentargets.org"),
    )
    raw = json.dumps(payload, indent=2).encode("utf-8")

    disease_data = (payload.get("data") or {}).get("disease")
    if not disease_data or not disease_data.get("associatedTargets"):
        return raw, _build_disease_artifact(
            disease,
            "opentargets",
            disease_id,
            resolved_name,
            [],
        )

    assoc_targets = disease_data["associatedTargets"]
    total_count = assoc_targets.get("count", 0)
    rows = assoc_targets.get("rows", [])

    associations: list[dict[str, Any]] = []
    for row in rows:
        target = row.get("target") or {}
        datatype_scores: dict[str, float] = {}
        for ds in row.get("datatypeScores", []):
            component = ds.get("id", "")
            datatype_scores[component] = ds.get("score", 0.0)
        associations.append(
            {
                "gene_symbol": target.get("approvedSymbol", ""),
                "ensembl_id": target.get("id", ""),
                "score": row.get("score", 0.0),
                "datatype_scores": datatype_scores,
            }
        )

    # Already sorted by score descending from the API, but enforce it.
    associations.sort(key=lambda a: a["score"], reverse=True)
    artifact = _build_disease_artifact(
        disease,
        "opentargets",
        disease_id,
        resolved_name,
        associations,
        total_count=total_count,
    )
    return raw, artifact


def _fetch_opentargets(symbol: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch disease associations from Open Targets for a gene symbol.

    Returns (verbatim response bytes, structured artifact dict).
    """
    ensembl_id, _resolved_name = _resolve_ensembl_id(symbol)
    payload = _graphql_post(
        OPENTARGETS_API,
        _OT_ASSOC_QUERY % ensembl_id,
        qps_for_host("api.platform.opentargets.org"),
    )
    raw = json.dumps(payload, indent=2).encode("utf-8")

    target_data = (payload.get("data") or {}).get("target")
    if not target_data or not target_data.get("associatedDiseases"):
        # Valid response but no associations — not an error.
        return raw, _build_artifact(symbol, "opentargets", ensembl_id, [])

    rows = target_data["associatedDiseases"].get("rows", [])
    associations = []
    for row in rows:
        disease = row.get("disease") or {}
        datatype_scores = {}
        for ds in row.get("datatypeScores", []):
            component = ds.get("id", "")
            datatype_scores[component] = ds.get("score", 0.0)
        associations.append(
            {
                "source_db": "opentargets",
                "disease_id": disease.get("id", ""),
                "disease_name": disease.get("name", ""),
                "score": row.get("score", 0.0),
                "evidence_count": len(row.get("datatypeScores", [])),
                "datatype_scores": datatype_scores,
            }
        )

    # Sort by score descending for top-disease extraction.
    associations.sort(key=lambda a: a["score"], reverse=True)
    artifact = _build_artifact(symbol, "opentargets", ensembl_id, associations)
    return raw, artifact


def _fetch_gwas_catalog(symbol: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch disease associations from the NHGRI-EBI GWAS Catalog.

    Returns (verbatim response bytes, structured artifact dict).

    NOTE (2026-09-07, issue #47): The ``associations/search/findByGene``
    endpoint was removed from the GWAS Catalog REST API. The API base URL
    still responds, and ``singleNucleotidePolymorphisms/search/findByGene``
    still works, but there is no direct association-by-gene endpoint.
    Reconstructing association data from per-SNP lookups would require
    paginating all SNPs for a gene and issuing a separate request per SNP,
    which is infeasible at scale. This function now detects the 404 and
    raises a clear ``Refusal`` so callers know the source is unavailable.
    """
    url = (
        f"{GWAS_CATALOG_API}/associations/search/findByGene"
        f"?geneName={quote(symbol.upper(), safe='')}"
    )
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("www.ebi.ac.uk"),
        timeout=60.0,
        headers={"Accept": "application/json"},
        tolerate_status=(404,),
    )

    if response.status_code == 404:
        raise Refusal(
            f"GWAS Catalog association-by-gene endpoint returned HTTP 404 "
            f"for {symbol.upper()!r}",
            detail=(
                f"the endpoint {GWAS_CATALOG_API}/associations/search/"
                f"findByGene has been removed from the EBI GWAS Catalog "
                f"REST API (confirmed 2026-09-07)"
            ),
            remedy=(
                "use --source opentargets or --source clinvar instead; "
                "the GWAS Catalog gwas-catalog source is currently "
                "unavailable until the API is updated or a replacement "
                "endpoint is integrated"
            ),
        )

    raw = response.content
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("GWAS Catalog did not return JSON", detail=str(exc)) from exc

    # Navigate the HAL-style _embedded response.
    embedded = payload.get("_embedded", {})
    raw_assocs = embedded.get("associations", [])

    associations = []
    for assoc in raw_assocs:
        # Extract trait names from the nested structure.
        traits = []
        for ea_trait in assoc.get("efoTraits", []):
            trait_name = ea_trait.get("trait")
            if trait_name:
                traits.append(trait_name)
        disease_name = "; ".join(traits) if traits else "Unknown trait"

        # Extract p-value.
        p_mantissa = assoc.get("pvalueMantissa")
        p_exponent = assoc.get("pvalueExponent")
        p_value = None
        if p_mantissa is not None and p_exponent is not None:
            p_value = p_mantissa * (10**p_exponent)

        # Extract OR/beta.
        or_value = assoc.get("orPerCopyNum")
        beta = assoc.get("betaNum")

        # Extract rsIDs from SNPs.
        rs_ids = []
        for snp in assoc.get("snps", []):
            rs_id = snp.get("rsId")
            if rs_id:
                rs_ids.append(rs_id)

        # Study accession from _links if available.
        study_link = (assoc.get("_links") or {}).get("study", {})
        study_href = study_link.get("href", "")
        study_accession = study_href.rstrip("/").split("/")[-1] if study_href else None

        associations.append(
            {
                "source_db": "gwas-catalog",
                "disease_id": "",
                "disease_name": disease_name,
                # NB: score is p-value here (lower = more significant), unlike
                # Open Targets where score is 0-1 (higher = stronger association).
                # The analyze command branches on source to interpret correctly.
                "score": p_value,
                "rs_ids": rs_ids,
                "p_value": p_value,
                "or_per_copy": or_value,
                "beta": beta,
                "study_accession": study_accession,
            }
        )

    # Sort by p-value ascending (most significant first), with None last.
    associations.sort(key=lambda a: (a["p_value"] is None, a["p_value"] or 0))
    artifact = _build_artifact(symbol, "gwas-catalog", None, associations)
    return raw, artifact


def _fetch_clinvar(symbol: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch ClinVar variant classifications for a gene symbol.

    Two-step: esearch to get variant UIDs for the gene, then a single
    batched esummary call to fetch all variant details. The batched call
    is one HTTP request with comma-separated UIDs, not N separate calls.

    Uses `germline_classification.description` for clinical significance
    and `germline_classification.review_status` for evidence quality.
    The older `clinical_significance` field is present but returns
    null/empty — the API has migrated to `germline_classification`.

    Returns (verbatim response bytes, structured artifact dict).
    """
    gene = symbol.upper()

    # Step 1: Search for ClinVar UIDs for this gene.
    search_url = (
        f"{CLINVAR_ESEARCH}?db=clinvar"
        f"&term={quote(gene, safe='')}[gene]"
        f"&retmode=json&retmax={CLINVAR_RETMAX}" + api_key_suffix()
    )
    search_response = http.request(
        "GET",
        search_url,
        qps=qps_for_host("eutils.ncbi.nlm.nih.gov"),
        timeout=60.0,
    )
    try:
        search_data = json.loads(search_response.content.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "ClinVar esearch did not return JSON", detail=str(exc)
        ) from exc

    esearch_result = search_data.get("esearchresult") or {}
    id_list = esearch_result.get("idlist", [])
    if not id_list:
        # No ClinVar entries for this gene — not an error.
        raw = json.dumps(search_data, indent=2).encode("utf-8")
        return raw, _build_artifact(gene, "clinvar", None, [])

    # Detect truncation: esearch reports the total count in `count`.
    total_count = int(esearch_result.get("count", len(id_list)))
    truncated = len(id_list) < total_count

    # Step 2: Batch fetch summaries — one HTTP call for all UIDs.
    ids_param = ",".join(id_list)
    summary_url = (
        f"{CLINVAR_ESUMMARY}?db=clinvar&id={ids_param}&retmode=json" + api_key_suffix()
    )
    summary_response = http.request(
        "GET",
        summary_url,
        qps=qps_for_host("eutils.ncbi.nlm.nih.gov"),
        timeout=120.0,
    )
    try:
        summary_data = json.loads(summary_response.content.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "ClinVar esummary did not return JSON", detail=str(exc)
        ) from exc

    raw = json.dumps(summary_data, indent=2).encode("utf-8")
    result_data = summary_data.get("result", {})
    uids = result_data.get("uids", [])

    associations: list[dict[str, Any]] = []
    for uid in uids:
        entry = result_data.get(uid)
        if not isinstance(entry, dict):
            continue

        # Use germline_classification, NOT the deprecated clinical_significance.
        germline = entry.get("germline_classification") or {}
        classification = germline.get("description", "")
        review_status = germline.get("review_status", "")

        # Extract trait names and cross-references from trait_set.
        traits: list[str] = []
        trait_xrefs: list[dict[str, str]] = []
        for trait in germline.get("trait_set", []):
            name = trait.get("trait_name")
            if name:
                traits.append(name)
            for xref in trait.get("trait_xrefs", []):
                trait_xrefs.append(
                    {
                        "db": xref.get("db_source", ""),
                        "id": xref.get("db_id", ""),
                    }
                )

        disease_name = "; ".join(traits) if traits else ""

        associations.append(
            {
                "source_db": "clinvar",
                "variant_id": uid,
                "variant_title": entry.get("title", ""),
                "obj_type": entry.get("obj_type", ""),
                "disease_name": disease_name,
                "classification": classification,
                "review_status": review_status,
                "trait_xrefs": trait_xrefs,
            }
        )

    # Sort: pathogenic first (by clinical significance tier), then
    # alphabetically by review status within each tier.
    _class_order = {
        "Pathogenic": 0,
        "Pathogenic/Likely pathogenic": 1,
        "Likely pathogenic": 2,
        "Uncertain significance": 3,
        "Conflicting classifications of pathogenicity": 4,
        "Likely benign": 5,
        "Benign/Likely benign": 6,
        "Benign": 7,
    }
    associations.sort(
        key=lambda a: (
            _class_order.get(a.get("classification", ""), 99),
            a.get("review_status", ""),
        )
    )

    artifact = _build_artifact(gene, "clinvar", None, associations)

    # Add ClinVar-specific summary: classification counts.
    class_counts: dict[str, int] = {}
    for assoc in associations:
        cls = assoc.get("classification", "")
        if cls:
            class_counts[cls] = class_counts.get(cls, 0) + 1
    artifact["summary"]["classification_counts"] = class_counts

    # Surface truncation so downstream consumers know the result set
    # may be incomplete for heavily-studied genes.
    if truncated:
        artifact["summary"]["total_clinvar_count"] = total_count
        artifact["summary"]["truncated"] = True
        artifact["summary"]["retmax"] = CLINVAR_RETMAX

    return raw, artifact


def _build_artifact(
    symbol: str,
    source: str,
    ensembl_id: str | None,
    associations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the structured dde.gwas.v1 artifact."""
    top_diseases = []
    seen = set()
    for assoc in associations[:10]:
        name = assoc.get("disease_name", "")
        if name and name not in seen:
            top_diseases.append(name)
            seen.add(name)
        if len(top_diseases) >= 5:
            break

    artifact: dict[str, Any] = {
        "schema": "dde.gwas.v1",
        "query": {
            "gene": symbol.upper(),
            "source": source,
        },
        "summary": {
            "n_associations": len(associations),
            "top_diseases": top_diseases,
        },
        "associations": associations,
    }
    if ensembl_id:
        artifact["query"]["ensembl_id"] = ensembl_id
    return artifact


def _build_disease_artifact(
    disease: str,
    source: str,
    disease_id: str,
    resolved_name: str,
    associations: list[dict[str, Any]],
    *,
    total_count: int | None = None,
) -> dict[str, Any]:
    """Build the structured dde.gwas-disease.v1 artifact."""
    top_genes: list[str] = []
    seen: set[str] = set()
    for assoc in associations[:10]:
        symbol = assoc.get("gene_symbol", "")
        if symbol and symbol not in seen:
            top_genes.append(symbol)
            seen.add(symbol)
        if len(top_genes) >= 5:
            break

    artifact: dict[str, Any] = {
        "schema": "dde.gwas-disease.v1",
        "query": {
            "disease": disease,
            "source": source,
            "disease_id": disease_id,
            "resolved_name": resolved_name,
        },
        "summary": {
            "n_associations": len(associations),
            "top_genes": top_genes,
        },
        "associations": associations,
    }

    # Surface truncation when total exceeds the page size fetched.
    if total_count is not None and total_count > len(associations):
        artifact["summary"]["total_target_count"] = total_count
        artifact["summary"]["truncated"] = True
        artifact["summary"]["page_size"] = _DISEASE_TARGET_PAGE_SIZE

    return artifact


@click.group()
def gwas() -> None:
    """GWAS and disease association lookup."""


@gwas.command("search")
@click.argument("gene")
@click.option(
    "--source",
    type=click.Choice(["opentargets", "gwas-catalog", "clinvar"]),
    default="opentargets",
    help="Which database to query (default: opentargets).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    gene: str,
    source: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search for GWAS / disease associations for GENE."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = sanitize_slug(gene.lower())

    if source == "opentargets":
        endpoint = OPENTARGETS_API
        raw, artifact = _fetch_opentargets(gene)
    elif source == "gwas-catalog":
        endpoint = GWAS_CATALOG_API
        raw, artifact = _fetch_gwas_catalog(gene)
    else:
        endpoint = CLINVAR_ESEARCH
        raw, artifact = _fetch_clinvar(gene)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=endpoint,
        parameters={
            "query_gene": gene,
            "resolved_gene": gene.upper(),
            "source": source,
        },
    )
    sidecar.note("source_db", source)
    sidecar.note("n_associations", artifact["summary"]["n_associations"])

    # ClinVar: warn when results were truncated by retmax.
    if source == "clinvar" and artifact["summary"].get("truncated"):
        total = artifact["summary"]["total_clinvar_count"]
        fetched = artifact["summary"]["n_associations"]
        sidecar.warn(
            f"ClinVar has {total} variants for {gene.upper()} but only "
            f"{fetched} were fetched (retmax={CLINVAR_RETMAX}); the result "
            "set may not include all variants"
        )

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.gwas-{source}.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.gwas-{source}.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.gwas-{source}.meta.json")

    emit.data("gene", gene.upper())
    emit.data("source", source)
    emit.data("n_associations", artifact["summary"]["n_associations"])
    emit.data("top_diseases", artifact["summary"]["top_diseases"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@gwas.command("search-disease")
@click.argument("disease")
@click.option(
    "--source",
    type=click.Choice(["opentargets"]),
    default="opentargets",
    help=(
        "Which database to query (default: opentargets). "
        "Only Open Targets is currently supported for disease-centric "
        "queries; the GWAS Catalog findByDiseaseTrait endpoint requires "
        "exact trait names and returns study-level data without gene "
        "mappings."
    ),
)
@out_option
@output_options
@pass_state
def search_disease_cmd(
    state: AppState,
    disease: str,
    source: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search for genes associated with DISEASE.

    Resolves the disease name to an ontology identifier via Open Targets,
    then fetches the top associated target genes ranked by overall
    association score.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = sanitize_slug(disease.lower()) if disease.strip() else "disease"

    if source == "opentargets":
        endpoint = OPENTARGETS_API
        raw, artifact = _fetch_opentargets_disease(disease)
    else:
        # Future sources would go here.
        raise click.BadParameter(f"unsupported source: {source}")

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search-disease",
        endpoint=endpoint,
        parameters={
            "query_disease": disease,
            "resolved_disease_id": artifact["query"]["disease_id"],
            "resolved_disease_name": artifact["query"]["resolved_name"],
            "source": source,
        },
    )
    sidecar.note("source_db", source)
    sidecar.note("n_associations", artifact["summary"]["n_associations"])

    # Warn when results were truncated by the page size.
    if artifact["summary"].get("truncated"):
        total = artifact["summary"]["total_target_count"]
        fetched = artifact["summary"]["n_associations"]
        sidecar.warn(
            f"Open Targets has {total} gene associations for "
            f"{artifact['query']['resolved_name']!r} but only the top "
            f"{fetched} were fetched (page_size={_DISEASE_TARGET_PAGE_SIZE}); "
            "the result set may not include all associated genes"
        )

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.gwas-disease-{source}.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.gwas-disease-{source}.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.gwas-disease-{source}.meta.json")

    emit.data("disease", disease)
    emit.data("source", source)
    emit.data("disease_id", artifact["query"]["disease_id"])
    emit.data("resolved_name", artifact["query"]["resolved_name"])
    emit.data("n_associations", artifact["summary"]["n_associations"])
    emit.data("top_genes", artifact["summary"]["top_genes"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@gwas.command("analyze")
@click.argument("gene")
@click.option(
    "--source",
    type=click.Choice(["opentargets", "gwas-catalog", "clinvar"]),
    default="opentargets",
    help="Which source to analyze (must match a prior search).",
)
@click.option(
    "--threshold",
    "score_threshold",
    type=float,
    default=None,
    help=(
        "Override the minimum significance threshold. For Open Targets "
        "this is a composite score (0-1, higher = stronger association) "
        "that blends genetic_association, literature, expression, and "
        "other data types — it is NOT a p-value. For GWAS Catalog it is "
        "a p-value (lower = more significant). Default: 0.1 for Open "
        "Targets, 5e-8 for GWAS Catalog."
    ),
)
@click.option(
    "--disease-filter",
    "disease_filter",
    type=str,
    default=None,
    help=(
        "Case-insensitive substring filter. Report whether DISEASE "
        "appears in the associations list and at what score."
    ),
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    gene: str,
    source: str,
    score_threshold: float | None,
    disease_filter: str | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Classify disease associations from a stored GWAS search. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = sanitize_slug(gene.lower())
    artifact_path = source_dir / f"{slug}.gwas-{source}.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no GWAS search artifact for {gene.upper()} (source={source})",
            remedy=f"run `dde gwas search {gene} --source {source}` first",
        )

    artifact = provenance.read_json(artifact_path, "GWAS artifact")
    if artifact.get("schema") != "dde.gwas.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.gwas.v1, got {artifact.get('schema')!r}",
        )

    associations = artifact.get("associations", [])

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    # Apply significance filtering — branched by source.
    if source == "clinvar":
        if score_threshold is not None:
            emit.line(
                "note: --threshold is ignored for --source clinvar; "
                "ClinVar uses categorical ACMG classifications, not a "
                "numeric score"
            )
        significant, metrics, assessment = _analyze_clinvar(
            gene,
            associations,
            add_relay,
        )
        thresholds_applied: dict[str, Any] = {
            "significant_classifications": sorted(_PATHOGENIC_CLASSIFICATIONS),
        }
    else:
        significant, metrics, assessment = _analyze_gwas(
            gene,
            source,
            associations,
            score_threshold,
            add_relay,
        )
        thresholds_applied = {
            "significance_cutoff": metrics["threshold"],
        }

    # --disease-filter: case-insensitive substring match.
    if disease_filter is not None:
        _filter = disease_filter.lower()
        matches = [
            a for a in associations if _filter in (a.get("disease_name") or "").lower()
        ]
        assessment["disease_filter_match"] = bool(matches)
        assessment["disease_filter_details"] = [
            {
                "disease_name": m.get("disease_name", ""),
                "score": m.get("score"),
            }
            for m in matches
        ]
        assessment["disease_filter_query"] = disease_filter

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.gwas-{source}.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set=f"gwas-{source}",
        thresholds_applied=thresholds_applied,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("relays", relays)

    if source == "clinvar":
        verdict = assessment["verdict"]
        n_total = assessment["n_pathogenic"]
        n_gs = assessment.get("pathogenic_gene_specific", n_total)
        n_lo = assessment.get("pathogenic_locus_overlapping", 0)
        emit.line(
            f"{gene.upper()} -> {verdict.upper()} "
            f"(Pathogenic: {n_total} total "
            f"({n_gs} gene-specific, {n_lo} locus-overlapping CNVs))"
        )
        class_counts = metrics.get("classification_counts", {})
        if class_counts:
            parts = [f"{k}={v}" for k, v in class_counts.items()]
            emit.line(f"classifications: {', '.join(parts)}")
        review_breakdown = metrics.get("review_status_breakdown", {})
        if review_breakdown:
            parts = [f"{k}={v}" for k, v in review_breakdown.items()]
            emit.line(f"review status (pathogenic): {', '.join(parts)}")
        top_conditions = assessment.get("top_conditions", [])
        if top_conditions:
            emit.line(f"top conditions: {', '.join(top_conditions[:5])}")
    else:
        verdict = assessment["verdict"]
        emit.line(
            f"{gene.upper()} -> {verdict.upper()} "
            f"({len(significant)}/{len(associations)} above threshold)"
        )
        top_diseases = assessment.get("top_diseases", [])
        if top_diseases:
            emit.line(f"top diseases: {', '.join(top_diseases[:5])}")
        # Show composite score decomposition for Open Targets
        if source == "opentargets":
            for detail in assessment.get("score_details", [])[:5]:
                dt = detail.get("datatype_scores", {})
                parts = ", ".join(f"{k}={v:.2f}" for k, v in sorted(dt.items()))
                emit.line(
                    f"  {detail['disease_name']}: "
                    f"Overall score: {detail['overall_score']:.2f} "
                    f"(composite: {parts})"
                )

    # --disease-filter results
    if disease_filter is not None:
        match = assessment.get("disease_filter_match", False)
        details = assessment.get("disease_filter_details", [])
        if match:
            emit.line(
                f"Disease filter '{disease_filter}': MATCH "
                f"({len(details)} association(s))"
            )
            for d in details[:5]:
                emit.line(f"  {d['disease_name']}: score={d['score']}")
        else:
            emit.line(f"Disease filter '{disease_filter}': NO MATCH")

    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


def _analyze_gwas(
    gene: str,
    source: str,
    associations: list[dict[str, Any]],
    score_threshold: float | None,
    add_relay: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Analyze GWAS associations (Open Targets or GWAS Catalog).

    For Open Targets, extracts the ``genetic_association`` component
    from ``datatype_scores`` and reports it separately so callers can
    distinguish pure genetic evidence from the composite score that
    blends literature, expression, and other data types.

    Returns (significant_associations, metrics, assessment).
    """
    if source == "opentargets":
        # Open Targets score range: 0-1, higher is stronger.
        cutoff = score_threshold if score_threshold is not None else 0.1
        significant = [a for a in associations if (a.get("score") or 0) >= cutoff]
    else:
        # GWAS Catalog: p-value, lower is more significant.
        cutoff = score_threshold if score_threshold is not None else 5e-8
        significant = [
            a
            for a in associations
            if a.get("p_value") is not None and a["p_value"] <= cutoff
        ]

    # Identify top diseases.
    top_diseases: list[str] = []
    seen: set[str] = set()
    for assoc in significant:
        name = assoc.get("disease_name", "")
        if name and name not in seen:
            top_diseases.append(name)
            seen.add(name)
        if len(top_diseases) >= 10:
            break

    verdict = "associations_found" if significant else "no_associations"

    if significant:
        add_relay(
            "gwas.association_not_causation",
            f"{gene.upper()} has {len(significant)} GWAS association(s) above "
            f"the significance threshold; these are statistical correlations "
            "between genetic variants and disease phenotypes, not evidence "
            "of causation or therapeutic mechanism",
        )

    metrics: dict[str, Any] = {
        "total_associations": len(associations),
        "significant_associations": len(significant),
        "threshold": cutoff,
        "source": source,
        "top_diseases": top_diseases,
    }
    assessment: dict[str, Any] = {
        "verdict": verdict,
        "gene": gene.upper(),
        "n_significant": len(significant),
        "top_diseases": top_diseases,
    }

    # --- Open Targets: extract genetic_association component ---
    if source == "opentargets":
        for assoc in associations:
            dt_scores = assoc.get("datatype_scores") or {}
            ga_score = dt_scores.get("genetic_association")
            if ga_score is not None:
                assoc["genetic_association_score"] = ga_score

        # Per-association score decomposition in the assessment
        score_details: list[dict[str, Any]] = []
        composite_passes_genetic_fails = False
        for assoc in significant:
            overall = assoc.get("score", 0.0)
            dt_scores = assoc.get("datatype_scores") or {}
            ga = dt_scores.get("genetic_association", 0.0)
            detail: dict[str, Any] = {
                "disease_name": assoc.get("disease_name", ""),
                "overall_score": overall,
                "genetic_association_score": ga,
                "datatype_scores": dt_scores,
            }
            score_details.append(detail)
            if overall >= cutoff and ga < cutoff:
                composite_passes_genetic_fails = True

        assessment["score_details"] = score_details

        # Fire relay when composite passes but genetic doesn't.
        if composite_passes_genetic_fails:
            add_relay(
                "opentargets.composite_not_genetic",
                f"{gene.upper()} passes the composite score threshold "
                f"({cutoff}) for one or more diseases but fails on "
                f"genetic_association alone; the composite score is "
                "boosted by literature, expression, or other non-genetic "
                "data types",
            )

    return significant, metrics, assessment


def _analyze_clinvar(
    gene: str,
    associations: list[dict[str, Any]],
    add_relay: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Analyze ClinVar variant classifications.

    Classifies by clinical significance tier rather than by numeric
    threshold. Pathogenic and Likely pathogenic are the "significant"
    findings. Review status is surfaced prominently — a classification
    without its review status is incomplete.

    Pathogenic variants are stratified into gene-specific (SNV, indel,
    coding) and locus-overlapping (CNV, structural) categories. The
    verdict uses gene-specific pathogenic count as the primary safety
    signal because a large CNV that overlaps a gene locus inflates the
    pathogenic count without being gene-specific evidence (issue #97).

    Returns (significant_associations, metrics, assessment).
    """
    significant = [
        a
        for a in associations
        if a.get("classification", "") in _PATHOGENIC_CLASSIFICATIONS
    ]

    # Stratify pathogenic variants by variant type.
    pathogenic_gene_specific: list[dict[str, Any]] = []
    pathogenic_locus_overlapping: list[dict[str, Any]] = []
    for assoc in significant:
        vtype = _classify_variant_type(assoc)
        if vtype == "locus_overlapping":
            pathogenic_locus_overlapping.append(assoc)
        else:
            pathogenic_gene_specific.append(assoc)

    # Classification breakdown across all variants.
    class_counts: dict[str, int] = {}
    for assoc in associations:
        cls = assoc.get("classification", "")
        if cls:
            class_counts[cls] = class_counts.get(cls, 0) + 1

    # Review status breakdown for pathogenic/likely pathogenic only.
    review_breakdown: dict[str, int] = {"strong": 0, "moderate": 0, "weak": 0}
    weak_pathogenic: list[dict[str, Any]] = []
    for assoc in significant:
        rs = assoc.get("review_status", "")
        if rs in _STRONG_REVIEW:
            review_breakdown["strong"] += 1
        elif rs in _MODERATE_REVIEW:
            review_breakdown["moderate"] += 1
        else:
            review_breakdown["weak"] += 1
            weak_pathogenic.append(assoc)

    # Identify top conditions from pathogenic variants.
    top_conditions: list[str] = []
    seen: set[str] = set()
    for assoc in significant:
        name = assoc.get("disease_name", "")
        if name and name not in seen:
            top_conditions.append(name)
            seen.add(name)
        if len(top_conditions) >= 10:
            break

    # Verdict uses gene-specific count as primary safety signal:
    # CNV-only pathogenic variants are not gene-specific evidence.
    verdict = (
        "pathogenic_variants_found"
        if pathogenic_gene_specific
        else "no_pathogenic_variants"
    )

    # Relay: curated-assertion epistemic status. Fires only when
    # pathogenic/likely pathogenic variants exist — on a gene with only
    # benign/VUS variants there is no pathogenicity claim to guard.
    if significant:
        add_relay(
            "clinvar.classification_is_curated",
            f"{gene.upper()} has {len(significant)} ClinVar variant(s) "
            f"classified as pathogenic or likely pathogenic; these are "
            "curated clinical assertions that the variant causes the named "
            "condition, not statistical correlations — but variant-level "
            "pathogenicity does not imply the gene is a therapeutic target",
        )

    # Relay: weak review status. Fires only when pathogenic/LP variants
    # have weak review status (e.g. "no assertion criteria provided").
    # Silent when all pathogenic calls have strong/moderate review status.
    if weak_pathogenic:
        add_relay(
            "clinvar.weak_review_status",
            f"{len(weak_pathogenic)} of {len(significant)} pathogenic/likely "
            f"pathogenic variant(s) for {gene.upper()} have weak review "
            "status (no assertion criteria provided or equivalent); do not "
            "cite these classifications without stating their review status",
        )

    # Relay: CNV-dominated pathogenic count (issue #97). Fires when
    # locus-overlapping variants outnumber gene-specific ones, because
    # the headline pathogenic count is then actively misleading about
    # gene-specific risk.
    if pathogenic_locus_overlapping and len(pathogenic_locus_overlapping) > len(
        pathogenic_gene_specific
    ):
        add_relay(
            "clinvar.cnv_not_gene_specific",
            f"{len(pathogenic_locus_overlapping)} of {len(significant)} "
            f"pathogenic/likely pathogenic variant(s) for {gene.upper()} "
            "are locus-overlapping CNVs/structural variants, not "
            f"gene-specific mutations ({len(pathogenic_gene_specific)} "
            "gene-specific); the pathogenic count reflects locus overlap, "
            "not gene-specific evidence",
        )

    metrics = {
        "total_variants": len(associations),
        "pathogenic_total": len(significant),
        "pathogenic_gene_specific": len(pathogenic_gene_specific),
        "pathogenic_locus_overlapping": len(pathogenic_locus_overlapping),
        "classification_counts": class_counts,
        "review_status_breakdown": review_breakdown,
        "source": "clinvar",
        "top_conditions": top_conditions,
    }
    assessment = {
        "verdict": verdict,
        "gene": gene.upper(),
        "n_pathogenic": len(significant),
        "pathogenic_gene_specific": len(pathogenic_gene_specific),
        "pathogenic_locus_overlapping": len(pathogenic_locus_overlapping),
        "n_weak_review": len(weak_pathogenic),
        "top_conditions": top_conditions,
        "classification_counts": class_counts,
        "review_status_breakdown": review_breakdown,
    }
    return significant, metrics, assessment
