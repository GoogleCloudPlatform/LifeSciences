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

"""NCBI ClinVar integration for clinical variant significance assertions.

Citation:
ClinVar (v.2024–06-03) (Landrum et al. 2018)
Landrum MJ, et al. ClinVar: improving access to variant interpretations and supporting evidence.
Nucleic Acids Res. 2018 Jan 4;46(D1):D1062-D1067.
"""

from __future__ import annotations

import os
from typing import Any

from ._http import fetch_json

_NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")
CLINVAR_CITATION = "ClinVar (v.2024–06-03) (Landrum et al. 2018)"

# Mapping review status string to ClinVar Gold Star rating (0 to 4 stars)
_REVIEW_STATUS_STARS: dict[str, int] = {
    "practice guideline": 4,
    "reviewed by expert panel": 3,
    "criteria provided, multiple submitters, no conflicts": 2,
    "criteria provided, conflicting classifications": 1,
    "criteria provided, single submitter": 1,
    "no assertion criteria provided": 0,
    "no assertion provided": 0,
    "no classification provided": 0,
}


def _calculate_gold_stars(review_status: str | None) -> int:
    """Calculates ClinVar gold star rating (0-4) from review status string."""
    if not review_status:
        return 0
    clean = review_status.strip().lower()
    return _REVIEW_STATUS_STARS.get(clean, 0)


async def search_clinvar(variant_term: str) -> dict[str, Any]:
    """Search ClinVar for clinical significance assertions and evidence.

    Citation: ClinVar (v.2024–06-03) (Landrum et al. 2018)

    Args:
        variant_term: An rsID (e.g. 'rs121913527', 'rs334'), HGVS notation,
                      or gene:variant term (e.g. 'PTEN R130Q').

    Returns:
        Structured clinical significance, review status, star ratings (0-4),
        associated phenotypes, and database citation.
    """
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    search_params = {
        "db": "clinvar",
        "term": variant_term,
        "retmode": "json",
        "retmax": 5,
    }
    if _NCBI_API_KEY:
        search_params["api_key"] = _NCBI_API_KEY

    try:
        search_res = await fetch_json(search_url, params=search_params)
    except Exception as e:
        return {
            "status": "error",
            "message": f"ClinVar esearch failed: {e}",
            "citation": CLINVAR_CITATION,
        }

    id_list = search_res.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return {
            "status": "success",
            "message": f"No ClinVar entries found for '{variant_term}'",
            "count": 0,
            "clinical_significance": None,
            "review_status": None,
            "gold_stars": 0,
            "hits": [],
            "records": [],
            "citation": CLINVAR_CITATION,
        }

    summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    summary_params = {
        "db": "clinvar",
        "id": ",".join(id_list),
        "retmode": "json",
    }
    if _NCBI_API_KEY:
        summary_params["api_key"] = _NCBI_API_KEY

    try:
        sum_res = await fetch_json(summary_url, params=summary_params)
    except Exception as e:
        return {
            "status": "error",
            "message": f"ClinVar esummary failed: {e}",
            "citation": CLINVAR_CITATION,
        }

    result_dict = sum_res.get("result", {})
    records = []

    for uid in id_list:
        doc = result_dict.get(uid)
        if not doc:
            continue
        germline = doc.get("germline_classification") or {}
        clinical_impact = doc.get("clinical_impact_classification") or {}
        trait_set = germline.get("trait_set") or doc.get("trait_set") or []
        traits = [t.get("trait_name") for t in trait_set if t.get("trait_name")]

        genes = [g.get("symbol") for g in (doc.get("genes") or []) if g.get("symbol")]
        review_status = germline.get("review_status", "no assertion provided")
        gold_stars = _calculate_gold_stars(review_status)
        variation_set = doc.get("variation_set") or [{}]
        variation_loc = variation_set[0].get("variation_loc") or [{}]

        records.append(
            {
                "clinvar_id": uid,
                "title": doc.get("title"),
                "genes": genes,
                "germline_significance": germline.get("description", "Unknown"),
                "review_status": review_status,
                "gold_stars": gold_stars,
                "last_evaluated": germline.get("last_evaluated"),
                "clinical_impact": clinical_impact.get("description"),
                "associated_traits": traits,
                "variation_id": variation_loc[0].get("id"),
            }
        )

    top_record = records[0] if records else {}
    return {
        "status": "success",
        "query": variant_term,
        "count": len(records),
        "clinical_significance": top_record.get("germline_significance", "Unknown"),
        "review_status": top_record.get("review_status"),
        "gold_stars": top_record.get("gold_stars", 0),
        "records": records,
        "citation": CLINVAR_CITATION,
    }
