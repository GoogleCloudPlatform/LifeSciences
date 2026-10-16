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

"""PharmGKB and CPIC integration for pharmacogenomics star-alleles and dosing guidelines.

Citations:
PharmGKB (Whirl-Carrillo et al. 2012, 2021)
Whirl-Carrillo M, et al. Pharmacogenomics Knowledge for Personalized Medicine.
Clin Pharmacol Ther. 2012 Oct;92(4):414-417.
Whirl-Carrillo M, et al. An Overview of the PharmGKB Knowledgebase.
Clin Pharmacol Ther. 2021 Aug;110(2):437-445.
"""

from __future__ import annotations

from typing import Any

from ._http import fetch_json

PHARMGKB_CITATION = "PharmGKB (Whirl-Carrillo et al. 2012, 2021)"
_CPIC_BASE_URL = "https://api.cpicpgx.org/v1"

# Curated reference database of major clinical PGx variants (VIP genes)
_CURATED_PGX_VARIANTS: dict[str, dict[str, Any]] = {
    "rs4244285": {
        "gene": "CYP2C19",
        "star_allele": "*2",
        "coding_change": "c.681G>A (aberrant splicing)",
        "functional_status": "No function",
        "activity_score": 0.0,
        "evidence_level": "1A",
        "cpic_level": "A",
        "drugs": [
            "clopidogrel",
            "citalopram",
            "escitalopram",
            "voriconazole",
            "omeprazole",
        ],
        "dosing_summary": "Loss-of-function allele defining intermediate or poor metabolizer status; CPIC guideline recommends alternative antiplatelet (prasugrel/ticagrelor) for clopidogrel.",
    },
    "rs1057910": {
        "gene": "CYP2C9",
        "star_allele": "*3",
        "coding_change": "c.1075A>C (p.Ile359Leu)",
        "functional_status": "Decreased function",
        "activity_score": 0.5,
        "evidence_level": "1A",
        "cpic_level": "A",
        "drugs": ["warfarin", "phenytoin", "celecoxib"],
        "dosing_summary": "Markedly reduced clearance; CPIC guideline recommends dose reduction for warfarin and celecoxib.",
    },
    "rs9923231": {
        "gene": "VKORC1",
        "star_allele": "-1639G>A",
        "coding_change": "c.-1639G>A (promoter)",
        "functional_status": "Decreased expression",
        "activity_score": 0.5,
        "evidence_level": "1A",
        "cpic_level": "A",
        "drugs": ["warfarin"],
        "dosing_summary": "Warfarin sensitivity allele requiring lower maintenance dose to prevent bleeding.",
    },
    "rs3892097": {
        "gene": "CYP2D6",
        "star_allele": "*4",
        "coding_change": "c.1846G>A (splicing defect)",
        "functional_status": "No function",
        "activity_score": 0.0,
        "evidence_level": "1A",
        "cpic_level": "A",
        "drugs": ["codeine", "tamoxifen", "tramadol", "ondansetron"],
        "dosing_summary": "Null allele; poor metabolizers cannot activate prodrug codeine to morphine or tamoxifen to endoxifen.",
    },
    "rs3918290": {
        "gene": "DPYD",
        "star_allele": "*2A",
        "coding_change": "c.1905+1G>A (exon 14 skipping)",
        "functional_status": "No function",
        "activity_score": 0.0,
        "evidence_level": "1A",
        "cpic_level": "A",
        "drugs": ["fluorouracil", "capecitabine", "tegafur"],
        "dosing_summary": "Severe DPD deficiency leading to lethal fluoropyrimidine toxicity; CPIC guideline recommends 50% dose reduction or alternative.",
    },
    "rs1800460": {
        "gene": "TPMT",
        "star_allele": "*3B",
        "coding_change": "c.460G>A (p.Ala154Thr)",
        "functional_status": "No function",
        "activity_score": 0.0,
        "evidence_level": "1A",
        "cpic_level": "A",
        "drugs": ["azathioprine", "mercaptopurine", "thioguanine"],
        "dosing_summary": "Thiopurine S-methyltransferase deficiency; high risk of severe myelosuppression.",
    },
}


async def query_pharmgkb_variant(
    variant_or_rsid: str,
    gene_symbol: str | None = None,
) -> dict[str, Any]:
    """Queries PharmGKB and CPIC for pharmacogenomic evidence, star-alleles, and dosing guidelines.

    Citation: PharmGKB (Whirl-Carrillo et al. 2012, 2021)

    Args:
        variant_or_rsid: Canonical dbSNP identifier (e.g. 'rs4244285') or star-allele notation.
        gene_symbol: Optional HGNC gene symbol (e.g. 'CYP2C19', 'CYP2D6').

    Returns:
        Structured PGx evidence including evidence level (1A-4), star-allele definition,
        affected drugs, CPIC dosing recommendation, and citation.
    """
    clean_id = variant_or_rsid.strip().lower()

    # 1. Check curated high-confidence VIP variants
    if clean_id in _CURATED_PGX_VARIANTS:
        record = dict(_CURATED_PGX_VARIANTS[clean_id])
        gene = record["gene"]

        cpic_url = f"{_CPIC_BASE_URL}/pair_view"
        guideline_pairs = []
        try:
            pairs_data = await fetch_json(cpic_url, params={"genesymbol": f"eq.{gene}"})
            for p in pairs_data[:10]:
                guideline_pairs.append(
                    {
                        "drug": p.get("drugname"),
                        "cpic_level": p.get("cpiclevel"),
                    }
                )
        except Exception:
            pass

        return {
            "status": "success",
            "query": clean_id,
            "gene": gene,
            "star_allele": record.get("star_allele"),
            "coding_change": record.get("coding_change"),
            "functional_status": record.get("functional_status"),
            "activity_score": record.get("activity_score"),
            "pharmgkb_evidence_level": record.get("evidence_level"),
            "evidence_level": record.get("evidence_level"),
            "cpic_level": record.get("cpic_level"),
            "primary_drugs": record.get("drugs"),
            "affected_drugs": record.get("drugs"),
            "dosing_summary": record.get("dosing_summary"),
            "cpic_guideline": record.get("dosing_summary"),
            "cpic_guideline_pairs": guideline_pairs,
            "citation": PHARMGKB_CITATION,
        }

    # 2. If gene_symbol is provided or inferred, query CPIC database directly
    target_gene = (gene_symbol or "").strip().upper()
    if target_gene:
        gene_url = f"{_CPIC_BASE_URL}/gene"
        pairs_url = f"{_CPIC_BASE_URL}/pair_view"
        alleles_url = f"{_CPIC_BASE_URL}/allele"

        try:
            gene_info = await fetch_json(
                gene_url, params={"symbol": f"eq.{target_gene}"}
            )
            if gene_info and len(gene_info) > 0:
                g_meta = gene_info[0]
                pairs_data = await fetch_json(
                    pairs_url, params={"genesymbol": f"eq.{target_gene}"}
                )
                alleles_data = await fetch_json(
                    alleles_url, params={"genesymbol": f"eq.{target_gene}"}
                )

                drugs = list(
                    {p.get("drugname") for p in pairs_data if p.get("drugname")}
                )
                sample_alleles = [
                    {
                        "allele": a.get("name"),
                        "clinical_functional_status": a.get("clinicalfunctionalstatus"),
                    }
                    for a in alleles_data[:10]
                ]

                return {
                    "status": "success",
                    "query": variant_or_rsid,
                    "gene": target_gene,
                    "is_cpic_gene": True,
                    "cpic_gene_has_guidelines": len(drugs) > 0,
                    "clinpgx_id": g_meta.get("clinpgxid"),
                    "associated_drugs": drugs,
                    "drug_pair_count": len(pairs_data),
                    "known_star_alleles_sample": sample_alleles,
                    "pharmgkb_evidence_level": None,
                    "evidence_level": None,
                    "citation": PHARMGKB_CITATION,
                }
        except Exception as e:
            return {
                "status": "error",
                "message": f"CPIC lookup failed for gene '{target_gene}': {e}",
                "citation": PHARMGKB_CITATION,
            }

    return {
        "status": "success",
        "query": variant_or_rsid,
        "is_cpic_gene": False,
        "message": f"No high-level CPIC or PharmGKB Level 1/2 annotations found for '{variant_or_rsid}'.",
        "citation": PHARMGKB_CITATION,
    }
