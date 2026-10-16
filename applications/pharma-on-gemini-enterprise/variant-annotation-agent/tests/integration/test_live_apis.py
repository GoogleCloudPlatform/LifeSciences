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
    """Verify Ensembl GRCh38 VEP REST API resolves rs334 to chromosome 11 coords."""
    res = await resolve_variant_coordinates("rs334")
    assert res["status"] == "success"
    assert res["chromosome"] in ("chr11", "11")
    assert res["position"] == 5227002
    assert res["ref"] == "T"
    assert res["alt"] == "A"
