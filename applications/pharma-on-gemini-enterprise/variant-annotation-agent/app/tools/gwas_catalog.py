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

"""NHGRI-EBI GWAS Catalog integration for complex disease and quantitative trait associations.

Citation:
GWAS Catalog (v.1.0) (Sollis et al. 2023)
Sollis E, et al. The NHGRI-EBI GWAS Catalog: knowledgebase and summary statistics.
Nucleic Acids Res. 2023 Jan 6;51(D1):D977-D985.
"""

from __future__ import annotations

from typing import Any

from ._http import fetch_json

GWAS_CATALOG_CITATION = "GWAS Catalog (v.1.0) (Sollis et al. 2023)"
_GWAS_V2_BASE_URL = "https://www.ebi.ac.uk/gwas/rest/api/v2"


async def query_gwas_catalog(
    rs_id: str,
    max_results: int = 15,
) -> dict[str, Any]:
    """Queries NHGRI-EBI GWAS Catalog for trait associations, p-values, risk alleles, and odds ratios/betas.

    Citation: GWAS Catalog (v.1.0) (Sollis et al. 2023)

    Args:
        rs_id: Canonical dbSNP identifier (e.g. 'rs12740374', 'rs334').
        max_results: Maximum number of trait associations to return (default 15).

    Returns:
        Structured trait associations including reported trait, EFO trait terms,
        p-value, beta / odds ratio, risk allele, study accession, and publication PMID.
    """
    clean_rsid = rs_id.strip().lower()
    if not clean_rsid.startswith("rs"):
        return {
            "status": "error",
            "message": f"GWAS Catalog query requires an rsID; received '{rs_id}'.",
            "citation": GWAS_CATALOG_CITATION,
        }

    url = f"{_GWAS_V2_BASE_URL}/associations"
    params = {"rs_id": clean_rsid, "size": max_results, "page": 0}

    try:
        resp = await fetch_json(url, params=params)
    except Exception as e:
        return {
            "status": "error",
            "message": f"GWAS Catalog API request failed for '{clean_rsid}': {e}",
            "citation": GWAS_CATALOG_CITATION,
        }

    raw_assocs = resp.get("_embedded", {}).get("associations", [])
    if not raw_assocs:
        return {
            "status": "success",
            "rs_id": clean_rsid,
            "count": 0,
            "message": f"No genome-wide significant associations found for '{clean_rsid}'.",
            "associations": [],
            "citation": GWAS_CATALOG_CITATION,
        }

    associations = []
    for item in raw_assocs:
        # Extract EFO traits (support both efo_traits and _embedded.efoTraits)
        raw_efos = (
            item.get("efo_traits") or item.get("_embedded", {}).get("efoTraits") or []
        )
        efo_list = []
        for efo in raw_efos:
            label = efo.get("efo_trait") or efo.get("trait")
            efo_id = efo.get("efo_id") or efo.get("uri") or efo.get("shortForm")
            if label:
                efo_list.append({"trait": label, "efo_id": efo_id})

        reported_traits = item.get("reported_trait") or []
        if isinstance(reported_traits, str):
            reported_traits = [reported_traits]

        p_val = (
            item.get("p_value")
            if item.get("p_value") is not None
            else item.get("pvalue")
        )
        beta = item.get("beta") if item.get("beta") is not None else item.get("betaNum")
        ci_lower = item.get("ci_lower")
        ci_upper = item.get("ci_upper")

        strongest_alleles = item.get("strongestAlleles") or []
        risk_allele = (
            item.get("snp_effect_allele")
            or item.get("snp_allele")
            or (
                strongest_alleles[0].get("riskAlleleName")
                if strongest_alleles
                else None
            )
        )
        risk_freq = item.get("risk_frequency") or item.get("riskFrequency")

        primary_trait = (
            ", ".join(reported_traits)
            if reported_traits
            else (efo_list[0]["trait"] if efo_list else "Unspecified Trait")
        )

        associations.append(
            {
                "association_id": item.get("association_id"),
                "study_accession": item.get("accession_id"),
                "pubmed_id": item.get("pubmed_id"),
                "first_author": item.get("first_author"),
                "trait": primary_trait,
                "reported_trait": primary_trait,
                "efo_traits": efo_list,
                "p_value": p_val,
                "beta": beta,
                "ci_95": f"[{ci_lower}, {ci_upper}]" if ci_lower and ci_upper else None,
                "risk_allele": risk_allele,
                "risk_frequency": risk_freq,
                "mapped_genes": item.get("mapped_genes", []),
            }
        )

    return {
        "status": "success",
        "rs_id": clean_rsid,
        "count": len(associations),
        "associations": associations,
        "citation": GWAS_CATALOG_CITATION,
    }
