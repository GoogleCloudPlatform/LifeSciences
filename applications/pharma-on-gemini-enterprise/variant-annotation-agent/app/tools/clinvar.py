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
import re
from typing import Any

from ._http import fetch_json

_NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")
CLINVAR_CITATION = "ClinVar (v.2024–06-03) (Landrum et al. 2018)"
_COORD_TERM_RE = re.compile(
    r"^(?:chr)?(?P<chrom>[0-9]{1,2}|X|Y|M|MT):(?P<pos>\d+):(?P<ref>[ACGTN]+)>(?P<alt>[ACGTN]+)$",
    re.IGNORECASE,
)
_COMPOUND_HGVS_RE = re.compile(r"c\.\[[^\]]+;[^\]]+\]")

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


async def search_clinvar(
    variant_term: str,
    chromosome: str | None = None,
    position: int | None = None,
    ref: str | None = None,
    alt: str | None = None,
) -> dict[str, Any]:
    """Search ClinVar for clinical significance assertions and evidence.

    Citation: ClinVar (v.2024–06-03) (Landrum et al. 2018)

    Args:
        variant_term: An rsID (e.g. 'rs121913527', 'rs334'), GRCh38 coordinate
                      ('chr11:5227002:T>A'), HGVS notation, or gene:variant term.
        chromosome: Optional GRCh38 chromosome for exact allele disambiguation.
        position: Optional 1-based GRCh38 position.
        ref: Optional GRCh38 reference allele (e.g. 'T').
        alt: Optional GRCh38 alternate allele (e.g. 'A').

    Returns:
        Structured clinical significance, review status, star ratings (0-4),
        associated phenotypes, and database citation, ranked so canonical
        single-variant germline assertions precede compound haplotypes.
    """
    raw_term = variant_term.strip()
    coord_match = _COORD_TERM_RE.match(raw_term)
    if coord_match:
        c_chrom = coord_match.group("chrom").upper()
        if c_chrom == "M":
            c_chrom = "MT"
        c_pos = int(coord_match.group("pos"))
        ref = ref or coord_match.group("ref").upper()
        alt = alt or coord_match.group("alt").upper()
        chromosome = chromosome or c_chrom
        position = position or c_pos
        esearch_term = f"{c_chrom}[CHR] AND {c_pos}[CHRPOS38]"
    else:
        esearch_term = raw_term

    expected_ref = ref.strip().upper() if ref else None
    expected_alt = alt.strip().upper() if alt else None

    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    search_params = {
        "db": "clinvar",
        "term": esearch_term,
        "retmode": "json",
        "retmax": 50,
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

    esearch_body = search_res.get("esearchresult", {})
    id_list = esearch_body.get("idlist", [])
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
    scored_records: list[tuple[tuple[int, int, int, int, int], dict[str, Any]]] = []

    for uid in id_list:
        doc = result_dict.get(uid)
        if not isinstance(doc, dict):
            continue
        germline = doc.get("germline_classification") or {}
        clinical_impact = doc.get("clinical_impact_classification") or {}
        trait_set = germline.get("trait_set") or doc.get("trait_set") or []
        traits = [t.get("trait_name") for t in trait_set if t.get("trait_name")]

        genes = [g.get("symbol") for g in (doc.get("genes") or []) if g.get("symbol")]
        review_status = germline.get("review_status", "no assertion provided")
        gold_stars = (
            int(germline["gold_stars"])
            if isinstance(germline.get("gold_stars"), int)
            else _calculate_gold_stars(review_status)
        )
        variation_set = doc.get("variation_set") or []
        first_var = (
            variation_set[0]
            if variation_set and isinstance(variation_set[0], dict)
            else {}
        )
        variation_loc = first_var.get("variation_loc") or [{}]

        title = str(doc.get("title") or "")
        obj_type = str(doc.get("obj_type") or "").strip()
        is_compound = (
            len(variation_set) > 1
            or obj_type.lower() in {"haplotype", "phase unknown", "genotype"}
            or bool(_COMPOUND_HGVS_RE.search(title))
        )

        canonical_spdi = str(
            doc.get("canonical_spdi") or first_var.get("canonical_spdi") or ""
        ).strip()
        allele_match_state = 0
        if expected_ref and expected_alt and canonical_spdi and not is_compound:
            spdi_parts = canonical_spdi.split(":")
            if len(spdi_parts) == 4:
                spdi_ref = spdi_parts[2].upper()
                spdi_alt = spdi_parts[3].upper()
                if spdi_ref == expected_ref and spdi_alt == expected_alt:
                    allele_match_state = 1
                else:
                    allele_match_state = -1

        germline_desc = germline.get("description") or "Unknown"
        has_meaningful_germline = str(germline_desc).strip().lower() not in {
            "",
            "unknown",
            "not provided",
            "other",
        }

        rank_key = (
            1 if allele_match_state >= 0 else 0,
            1 if allele_match_state == 1 else 0,
            0 if is_compound else 1,
            1 if has_meaningful_germline else 0,
            gold_stars,
        )

        record_payload: dict[str, Any] = {
            "clinvar_id": uid,
            "accession": doc.get("accession"),
            "title": doc.get("title"),
            "record_type": obj_type
            or ("Haplotype" if is_compound else "single variant"),
            "is_compound_haplotype": is_compound,
            "genes": genes,
            "germline_significance": germline_desc,
            "review_status": review_status,
            "gold_stars": gold_stars,
            "last_evaluated": germline.get("last_evaluated"),
            "clinical_impact": clinical_impact.get("description"),
            "associated_traits": traits,
            "canonical_spdi": canonical_spdi or None,
            "variation_id": variation_loc[0].get("id") or uid,
        }
        scored_records.append((rank_key, record_payload))

    scored_records.sort(key=lambda item: item[0], reverse=True)
    records = [rec for _, rec in scored_records[:10]]
    total_hits = int(esearch_body.get("count") or len(scored_records))

    top_record = records[0] if records else {}
    return {
        "status": "success",
        "query": variant_term,
        "count": len(records),
        "total_clinvar_hits": total_hits,
        "clinical_significance": top_record.get("germline_significance", "Unknown"),
        "review_status": top_record.get("review_status"),
        "gold_stars": top_record.get("gold_stars", 0),
        "top_clinvar_id": top_record.get("clinvar_id"),
        "records": records,
        "citation": CLINVAR_CITATION,
    }
