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

"""Broad Institute gnomAD v4 integration for population genetics and gene constraint.

Citation:
gnomAD (v4) (Karczewski et al. 2020)
Karczewski KJ, et al. The mutational constraint spectrum quantified from
variation in 141,456 humans. Nature. 2020 May;581(7809):434-443.
"""

from __future__ import annotations

import re
from typing import Any

from ._http import post_json

_GNOMAD_API_URL = "https://gnomad.broadinstitute.org/api"
GNOMAD_CITATION = "gnomAD (v4) (Karczewski et al. 2020)"


def evaluate_acmg_frequency_tier(af: float) -> str:
    """Evaluates ACMG frequency rules (BA1, BS1, PM2_Supporting) based on gnomAD AF."""
    if af >= 0.05:
        return "BA1 (Stand-alone Benign: AF >= 5.0%)"
    elif af >= 0.01:
        return "BS1 (Strong Benign: AF >= 1.0%)"
    elif af <= 0.0001:
        return "PM2_Supporting (Moderate/Supporting Pathogenic: AF <= 0.01% or absent)"
    return "Intermediate Frequency (0.01% < AF < 1.0%)"


async def query_gnomad_variant(
    chromosome: str,
    position: int,
    ref: str,
    alt: str,
    dataset: str = "gnomad_r4",
) -> dict[str, Any]:
    """Query gnomAD population allele frequencies and counts across continental ancestries.

    Citation: gnomAD (v4) (Karczewski et al. 2020)

    Args:
        chromosome: Chromosome with or without 'chr' prefix (e.g. '10', 'chr11', or 'X').
        position: 1-based genomic position on GRCh38.
        ref: Reference allele (e.g. 'A').
        alt: Alternate allele (e.g. 'G').
        dataset: gnomAD dataset ID (default 'gnomad_r4').

    Returns:
        Allele frequency (AF), allele count (AC), allele number (AN),
        homozygote counts, ancestry populations (AFR, AMR, ASJ, EAS, FIN, MID, NFE, SAS),
        and ACMG frequency evaluation.
    """
    clean_chrom = re.sub(r"^chr", "", chromosome.strip(), flags=re.IGNORECASE).upper()
    clean_ref = ref.strip().upper()
    clean_alt = alt.strip().upper()
    variant_id = f"{clean_chrom}-{position}-{clean_ref}-{clean_alt}"

    query = """
    query VariantFrequencies($variantId: String!, $dataset: DatasetId!) {
      variant(variantId: $variantId, dataset: $dataset) {
        variant_id
        rsids
        chrom
        pos
        ref
        alt
        joint {
          ac
          an
          homozygote_count
          hemizygote_count
          faf95 {
            popmax
            popmax_population
          }
          faf99 {
            popmax
            popmax_population
          }
          populations {
            id
            ac
            an
            homozygote_count
          }
        }
        exome {
          ac
          an
          af
          homozygote_count
          faf95 {
            popmax
            popmax_population
          }
          faf99 {
            popmax
            popmax_population
          }
          populations {
            id
            ac
            an
            homozygote_count
          }
        }
        genome {
          ac
          an
          af
          homozygote_count
          faf95 {
            popmax
            popmax_population
          }
          faf99 {
            popmax
            popmax_population
          }
          populations {
            id
            ac
            an
            homozygote_count
          }
        }
      }
    }
    """

    variables = {"variantId": variant_id, "dataset": dataset}

    try:
        resp = await post_json(
            _GNOMAD_API_URL, json_data={"query": query, "variables": variables}
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"gnomAD API error for {variant_id}: {e}",
            "citation": GNOMAD_CITATION,
        }

    data = (resp.get("data") or {}).get("variant")
    if not data:
        errors = resp.get("errors") or []
        if errors:
            err_msgs = "; ".join(str(err.get("message", err)) for err in errors)
            if "not found" not in err_msgs.lower():
                return {
                    "status": "error",
                    "message": f"gnomAD GraphQL error for {variant_id}: {err_msgs}",
                    "citation": GNOMAD_CITATION,
                }
        return {
            "status": "success",
            "variant_id": variant_id,
            "found_in_gnomad": False,
            "allele_frequency": 0.0,
            "allele_count": 0,
            "acmg_frequency_tier": evaluate_acmg_frequency_tier(0.0),
            "note": "Variant not observed in gnomAD v4 (supports ACMG PM2_Supporting)",
            "citation": GNOMAD_CITATION,
        }

    joint = data.get("joint") or {}
    exome = data.get("exome") or {}
    genome = data.get("genome") or {}

    if joint and (joint.get("an") is not None or joint.get("ac") is not None):
        total_ac = joint.get("ac") or 0
        total_an = joint.get("an") or 0
        joint_af = (
            float(joint["af"])
            if joint.get("af") is not None
            else ((total_ac / total_an) if total_an > 0 else 0.0)
        )
        total_homozygotes = joint.get("homozygote_count") or 0
        pop_sources = joint.get("populations") or []
    else:
        total_ac = (exome.get("ac") or 0) + (genome.get("ac") or 0)
        total_an = (exome.get("an") or 0) + (genome.get("an") or 0)
        joint_af = (total_ac / total_an) if total_an > 0 else 0.0
        total_homozygotes = (exome.get("homozygote_count") or 0) + (
            genome.get("homozygote_count") or 0
        )
        pop_sources = (exome.get("populations") or []) + (
            genome.get("populations") or []
        )

    ancestry_dict: dict[str, dict[str, Any]] = {}
    for pop in pop_sources:
        pop_id = str(pop.get("id") or "").strip()
        if not pop_id or "_" in pop_id or pop_id.upper() in {"XX", "XY"}:
            continue
        if pop_id not in ancestry_dict:
            ancestry_dict[pop_id] = {"ac": 0, "an": 0, "homozygotes": 0}
        ancestry_dict[pop_id]["ac"] += pop.get("ac") or 0
        ancestry_dict[pop_id]["an"] += pop.get("an") or 0
        ancestry_dict[pop_id]["homozygotes"] += pop.get("homozygote_count") or 0

    populations_summary = {}
    for pop_id, counts in ancestry_dict.items():
        an = counts["an"]
        populations_summary[pop_id] = {
            "ac": counts["ac"],
            "an": an,
            "af": round(counts["ac"] / an, 7) if an > 0 else 0.0,
            "homozygotes": counts["homozygotes"],
        }

    faf95 = joint.get("faf95") or exome.get("faf95") or genome.get("faf95")
    faf99 = joint.get("faf99") or exome.get("faf99") or genome.get("faf99")

    return {
        "status": "success",
        "variant_id": variant_id,
        "rsids": data.get("rsids") or [],
        "found_in_gnomad": True,
        "joint_af": joint_af,
        "allele_frequency": joint_af,
        "joint_ac": total_ac,
        "allele_count": total_ac,
        "joint_an": total_an,
        "exome_af": exome.get("af"),
        "genome_af": genome.get("af"),
        "homozygotes": total_homozygotes,
        "faf95": faf95,
        "faf99": faf99,
        "acmg_frequency_tier": evaluate_acmg_frequency_tier(joint_af),
        "populations": populations_summary,
        "citation": GNOMAD_CITATION,
    }


async def query_gene_constraint(gene_symbol: str) -> dict[str, Any]:
    """Retrieve gnomAD gene constraint metrics (pLI, LOEUF, missense Z, synonymous Z).

    Citation: gnomAD (v4) (Karczewski et al. 2020)

    Args:
        gene_symbol: Canonical HGNC gene symbol (e.g. 'PTEN', 'HBB', 'CYP2C19').

    Returns:
        pLI, LOEUF, missense Z, synonymous Z, and loss-of-function tolerance interpretation.
    """
    query = """
    query GeneConstraint($gene_symbol: String!) {
      gene(gene_symbol: $gene_symbol, reference_genome: GRCh38) {
        gene_id
        symbol
        gnomad_constraint {
          pli
          oe_lof
          oe_lof_lower
          oe_lof_upper
          oe_mis
          oe_mis_lower
          oe_mis_upper
          lof_z
          mis_z
          syn_z
        }
      }
    }
    """
    try:
        resp = await post_json(
            _GNOMAD_API_URL,
            json_data={
                "query": query,
                "variables": {"gene_symbol": gene_symbol.strip()},
            },
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"gnomAD constraint query failed: {e}",
            "citation": GNOMAD_CITATION,
        }

    gene_data = (resp.get("data") or {}).get("gene")
    if not gene_data:
        errors = resp.get("errors") or []
        if errors:
            err_msgs = "; ".join(str(err.get("message", err)) for err in errors)
            return {
                "status": "error",
                "message": f"gnomAD constraint error for '{gene_symbol}': {err_msgs}",
                "citation": GNOMAD_CITATION,
            }
        return {
            "status": "error",
            "message": f"Gene '{gene_symbol}' not found in gnomAD",
            "citation": GNOMAD_CITATION,
        }

    constraint = gene_data.get("gnomad_constraint") or {}
    pli = (
        constraint.get("pli")
        if constraint.get("pli") is not None
        else constraint.get("pLI")
    )
    loeuf = (
        constraint.get("oe_lof_upper")
        if constraint.get("oe_lof_upper") is not None
        else constraint.get("loeuf")
    )
    mis_z = (
        constraint.get("mis_z")
        if constraint.get("mis_z") is not None
        else constraint.get("z_mis")
    )
    syn_z = (
        constraint.get("syn_z")
        if constraint.get("syn_z") is not None
        else constraint.get("z_syn")
    )

    is_constrained = (pli is not None and pli >= 0.9) or (
        loeuf is not None and loeuf < 0.35
    )

    return {
        "status": "success",
        "symbol": gene_data.get("symbol"),
        "gene_id": gene_data.get("gene_id"),
        "pli": pli,
        "loeuf": loeuf,
        "oe_lof": constraint.get("oe_lof"),
        "oe_mis": constraint.get("oe_mis"),
        "lof_z": constraint.get("lof_z"),
        "mis_z": mis_z,
        "syn_z": syn_z,
        "is_lof_intolerant": is_constrained,
        "interpretation": (
            "Highly constrained against Loss-of-Function (pLI >= 0.9 or LOEUF < 0.35; PVS1 eligible)"
            if is_constrained
            else "Tolerant or moderately constrained to Loss-of-Function"
        ),
        "citation": GNOMAD_CITATION,
    }
