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

"""Live integration tests for public genomic APIs (GWAS Catalog v2, CPIC v1, Ensembl VEP, NCBI ClinVar)."""

import pytest

from app.tools.alphagenome_scoring import resolve_variant_coordinates
from app.tools.clinvar import search_clinvar
from app.tools.gnomad import query_gene_constraint, query_gnomad_variant
from app.tools.gwas_catalog import query_gwas_catalog
from app.tools.pharmgkb import query_pharmgkb_variant


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_gwas_catalog_v2_rs12740374():
    """Verify live EBI GWAS Catalog v2 endpoint returns associations."""
    res = await query_gwas_catalog("rs12740374")
    assert res["status"] == "success"
    assert res["citation"] == "GWAS Catalog (v.1.0) (Sollis et al. 2023)"
    assert len(res["associations"]) > 0
    traits = [a["trait"].lower() for a in res["associations"]]
    assert any("cholesterol" in t or "coronary" in t or "density" in t for t in traits)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_pharmgkb_cpic_cyp2c19():
    """Verify live CPIC API or curated VIP lookup returns clopidogrel guideline."""
    res = await query_pharmgkb_variant("rs4244285", gene_symbol="CYP2C19")
    assert res["status"] == "success"
    assert res["citation"] == "PharmGKB (Whirl-Carrillo et al. 2012, 2021)"
    assert res["star_allele"] == "*2"
    assert "clopidogrel" in res["affected_drugs"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_ensembl_vep_resolution():
    """Verify Ensembl GRCh38 VEP REST API resolves rs334 and rs12740374 MANE transcripts."""
    res = await resolve_variant_coordinates("rs334")
    assert res["status"] == "success"
    assert res["chromosome"] in ("chr11", "11")
    assert res["position"] == 5227002
    assert res["ref"] == "T"
    assert res["alt"] == "A"
    assert res["avi_score"] is None

    res_celsr2 = await resolve_variant_coordinates("rs12740374")
    assert res_celsr2["status"] == "success"
    assert res_celsr2["position"] == 109274968
    assert res_celsr2["gene_symbol"] == "CELSR2"
    assert res_celsr2["transcript_id"] == "ENST00000271332"
    assert res_celsr2["consequence"] == "3_prime_UTR_variant"
    assert res_celsr2["avi_score"] is None

    res_coord = await resolve_variant_coordinates("chr1:109274968:G>T")
    assert res_coord["status"] == "success"
    assert res_coord["gene_symbol"] == "CELSR2"
    assert res_coord["transcript_id"] == "ENST00000271332"
    assert res_coord["avi_score"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_clinvar_rs334_canonical_record_15333():
    """Verify ClinVar returns canonical 2-star Pathogenic single-SNV HbS record 15333 first."""
    res = await search_clinvar(
        "rs334", chromosome="11", position=5227002, ref="T", alt="A"
    )
    assert res["status"] == "success"
    assert res["top_clinvar_id"] == "15333"
    assert "pathogenic" in res["clinical_significance"].lower()
    assert res["gold_stars"] >= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_gnomad_v4_variant_and_constraint():
    """Verify live gnomAD v4 GraphQL queries succeed without 400 Bad Request."""
    var_res = await query_gnomad_variant("1", 109274968, "G", "T")
    assert var_res["status"] == "success"
    assert var_res["found_in_gnomad"] is True
    assert var_res["allele_frequency"] > 0.05
    assert var_res["faf95"] is not None

    cons_res = await query_gene_constraint("CELSR2")
    assert cons_res["status"] == "success"
    assert cons_res["pli"] is not None
    assert cons_res["loeuf"] is not None
    assert cons_res["mis_z"] is not None
