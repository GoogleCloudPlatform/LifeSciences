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

"""Unit tests for AlphaGenome scoring, GCS parsing, ClinVar, gnomAD, GWAS Catalog, and PharmGKB tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools._http import client as http_client
from app.tools.alphagenome_scoring import (
    ALPHAGENOME_TRACKS,
    build_alphagenome_atlas_url,
    classify_avi_tier,
    compute_avi_score,
    parse_and_score_alphagenome_output,
    resolve_variant_coordinates,
)
from app.tools.clinvar import search_clinvar
from app.tools.gcs_bucket import (
    MAX_DOWNLOAD_BYTES,
    import_alphagenome_from_gcs,
    list_alphagenome_bucket_files,
    save_dossier_to_gcs,
)
from app.tools.gnomad import query_gene_constraint, query_gnomad_variant
from app.tools.gwas_catalog import query_gwas_catalog
from app.tools.pharmgkb import query_pharmgkb_variant


# ---------------------------------------------------------------------------
# 1. AlphaGenome Scoring & Math Unit Tests
# ---------------------------------------------------------------------------
class TestAlphaGenomeScoring:
    def test_all_18_tracks_defined(self):
        assert len(ALPHAGENOME_TRACKS) == 18
        assert "MERGED_SPLICING" in ALPHAGENOME_TRACKS
        assert "MAX_ABS_RNA_SEQ" in ALPHAGENOME_TRACKS
        assert "MAX_ABS_DNASE" in ALPHAGENOME_TRACKS
        assert "MAX_ABS_CHIP_TF" in ALPHAGENOME_TRACKS
        assert "ALPHAMISSENSE" in ALPHAGENOME_TRACKS
        assert "PHASTCONS_470_WAY" in ALPHAGENOME_TRACKS

    def test_classify_avi_tier(self):
        assert classify_avi_tier(25.0) == "High Impact"
        assert classify_avi_tier(20.0) == "High Impact"
        assert classify_avi_tier(19.99) == "Moderate-High Impact"
        assert classify_avi_tier(15.0) == "Moderate-High Impact"
        assert classify_avi_tier(14.99) == "Moderate Impact"
        assert classify_avi_tier(10.0) == "Moderate Impact"
        assert classify_avi_tier(9.99) == "Low / Baseline Impact"
        assert classify_avi_tier(0.0) == "Low / Baseline Impact"

    def test_compute_avi_score_calculation(self):
        # Tail quantile 0.01 (top 1.0%) -> Phred = -10 * log10(0.01) = 20.0 (High Impact)
        res = compute_avi_score(quantile=0.01)
        assert res["quantile"] == 0.01
        assert res["top_percentile"] == 1.0
        assert abs(res["phred"] - 20.0) < 0.01
        assert res["impact_tier"] == "High Impact"
        assert res["raw_score"] is None

        # Phred 30 -> Tail quantile 0.001 (top 0.1%)
        res30 = compute_avi_score(phred=30.0)
        assert res30["phred"] == 30.0
        assert abs(res30["quantile"] - 0.001) < 1e-6
        assert abs(res30["top_percentile"] - 0.1) < 1e-4
        assert res30["impact_tier"] == "High Impact"

    def test_build_alphagenome_atlas_url(self):
        res = build_alphagenome_atlas_url(
            chromosome="chr1", position=109274968, ref="G", alt="T"
        )
        assert (
            res["variant_url"]
            == "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109274968:G%3ET&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
        )
        assert (
            res["locus_url"]
            == "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109273968-109275968&m=locus&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
        )

        # Case-insensitive chr prefix handling (e.g. "CHR1" or "Chr1")
        res_upper = build_alphagenome_atlas_url(
            chromosome="CHR1", position=109274968, ref="G", alt="T"
        )
        assert (
            res_upper["variant_url"]
            == "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109274968:G%3ET&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
        )

    def test_parse_and_score_alphagenome_output_json(self):
        sample_json = json.dumps(
            {
                "variant": "chr1:109274968:G>T",
                "chromosome": "1",
                "position": 109274968,
                "ref": "G",
                "alt": "T",
                "MAX_ABS_CHIP_TF": 0.92,
                "MAX_ABS_DNASE": 0.85,
                "avi_quantile": 0.005,
            }
        )
        res = parse_and_score_alphagenome_output(sample_json)
        assert res["status"] == "success"
        assert len(res["variants"]) == 1
        var = res["variants"][0]
        assert var["chromosome"] == "1"
        assert var["position"] == 109274968
        assert var["avi_score"]["impact_tier"] == "High Impact"
        assert "atlas_links" in var

    def test_parse_and_score_alphagenome_output_tsv(self):
        sample_tsv = (
            "variant\tavi_raw\tavi_quantile\tavi_phred\n"
            "chr10:94781859:C>T\t0.965\t0.0002\t.\n"
            "chr1:11796321:G>A\t0.582\t0.0876\tNA\n"
        )
        res = parse_and_score_alphagenome_output(sample_tsv)
        assert res["status"] == "success"
        assert len(res["variants"]) == 2
        assert res["variants"][0]["avi_score"]["impact_tier"] == "High Impact"
        assert res["variants"][1]["avi_score"]["impact_tier"] == "Moderate Impact"

    def test_parse_and_score_alphagenome_output_vcf(self):
        sample_vcf = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr1\t109274968\trs12740374\tG\tT\t.\tPASS\tGENE=CELSR2;AVI_RAW=0.884;AVI_Q=0.0018;AVI_PHRED=27.45\n"
            "chr11\t5227002\trs334\tT\tA\t.\tPASS\tGENE=HBB\n"
            "chr1\tINVALID_POS\t.\tA\tC\t.\tPASS\tGENE=BAD\n"
        )
        res = parse_and_score_alphagenome_output(sample_vcf)
        assert res["status"] == "success"
        assert res["format"] == "vcf"
        assert len(res["variants"]) == 2
        var = res["variants"][0]
        assert var["chromosome"] == "1"
        assert var["position"] == 109274968
        assert var["rsid"] == "rs12740374"
        assert var["gene_symbol"] == "CELSR2"
        assert var["avi_score"]["phred"] == 27.45
        assert var["avi_score"]["impact_tier"] == "High Impact"
        # Unscored VCF row does not fabricate 0.0 AlphaGenome scores
        unscored = res["variants"][1]
        assert unscored["avi_score"] is None
        assert unscored["scoring_source"] == "atlas_link_only"

    def test_parse_and_score_rejects_non_genomic_csv(self):
        bad_csv = "name,age,city\nAlice,30,Seattle\n"
        res = parse_and_score_alphagenome_output(bad_csv)
        assert res["status"] == "error"
        assert res["variants"] == []

    def test_parse_and_score_alphagenome_output_jsonl(self):
        jsonl_payload = "\n".join(
            [
                json.dumps(
                    {
                        "chromosome": "1",
                        "position": 109274968,
                        "ref": "G",
                        "alt": "T",
                        "rsid": "rs12740374",
                        "gene_symbol": "CELSR2",
                        "avi_quantile": 0.0018,
                        "MAX_ABS_CHIP_TF": 0.94,
                    }
                ),
                json.dumps(
                    {
                        "chromosome": "11",
                        "position": 5227002,
                        "ref": "T",
                        "alt": "A",
                        "rsid": "rs334",
                        "gene_symbol": "HBB",
                        "avi_phred": 33.98,
                    }
                ),
            ]
        )
        res = parse_and_score_alphagenome_output(jsonl_payload)
        assert res["status"] == "success"
        assert res["format"] == "jsonl"
        assert len(res["variants"]) == 2
        assert res["variants"][0]["rsid"] == "rs12740374"
        assert res["variants"][1]["avi_score"]["impact_tier"] == "High Impact"

    def test_parse_and_score_rejects_file_paths_with_guidance(self):
        for path_str in (
            "samples/sample_alphagenome_variants.vcf",
            "gs://my-bucket/samples/sample_alphagenome_variants.vcf",
        ):
            res = parse_and_score_alphagenome_output(path_str)
            assert res["status"] == "error"
            assert "import_alphagenome_from_gcs" in res["message"]
            assert res["variants"] == []

    @pytest.mark.asyncio
    async def test_resolve_variant_coordinates_already_formatted(self):
        with patch(
            "app.tools.alphagenome_scoring.fetch_json",
            new_callable=AsyncMock,
            side_effect=RuntimeError("offline"),
        ):
            res = await resolve_variant_coordinates("chr1:109274968:G>T")
        assert res["status"] == "success"
        assert res["chromosome"] in ("1", "chr1")
        assert res["position"] == 109274968
        assert res["ref"] == "G"
        assert res["alt"] == "T"
        assert res["avi_score"] is None
        assert res["scoring_source"] == "atlas_link_only"
        assert "q=chr1:109274968:G%3ET&m=variant" in res["atlas_links"]["variant_url"]

    @pytest.mark.asyncio
    async def test_resolve_variant_coordinates_rsid_vep_mock(self):
        mock_vep_response = [
            {
                "id": "rs7412",
                "seq_region_name": "19",
                "start": 44908822,
                "allele_string": "C/T",
                "most_severe_consequence": "missense_variant",
                "transcript_consequences": [
                    {
                        "gene_symbol": "APOE",
                        "transcript_id": "ENST00000485628",
                        "biotype": "retained_intron",
                    },
                    {
                        "gene_symbol": "APOE",
                        "transcript_id": "ENST00000252486",
                        "biotype": "protein_coding",
                        "hgvsc": "ENST00000252486.9:c.526C>T",
                        "hgvsp": "ENSP00000252486.3:p.Arg176Cys",
                        "amino_acids": "R/C",
                        "protein_start": 176,
                        "impact": "MODERATE",
                        "sift_score": 0.01,
                        "sift_prediction": "deleterious",
                        "polyphen_score": 0.96,
                        "polyphen_prediction": "probably_damaging",
                    },
                ],
            }
        ]
        with patch(
            "app.tools.alphagenome_scoring.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_vep_response
            res = await resolve_variant_coordinates("rs7412")
            assert res["status"] == "success"
            assert res["source"] == "ensembl_vep"
            assert res["scoring_source"] == "atlas_link_only"
            assert res["chromosome"] == "19"
            assert res["position"] == 44908822
            assert res["ref"] == "C"
            assert res["alt"] == "T"
            assert res["gene_symbol"] == "APOE"
            assert res["transcript_id"] == "ENST00000252486"
            assert res["hgvsp"] == "ENSP00000252486.3:p.Arg176Cys"
            assert res["avi_score"] is None
            assert res["multimodal_tracks"] == {}

    @pytest.mark.asyncio
    async def test_resolve_variant_coordinates_rs12740374_selects_celsr2_over_flanking_psrc1(
        self,
    ):
        mock_vep_response = [
            {
                "id": "rs12740374",
                "seq_region_name": "1",
                "start": 109274968,
                "allele_string": "G/T",
                "most_severe_consequence": "3_prime_UTR_variant",
                "transcript_consequences": [
                    {
                        "gene_symbol": "PSRC1",
                        "transcript_id": "ENST00000369909",
                        "mane_select": "NM_001032291.3",
                        "mane": ["MANE_Select"],
                        "canonical": 1,
                        "biotype": "protein_coding",
                        "distance": 4588,
                        "consequence_terms": ["downstream_gene_variant"],
                        "impact": "MODIFIER",
                        "variant_allele": "T",
                    },
                    {
                        "gene_symbol": "CELSR2",
                        "transcript_id": "ENST00000271332",
                        "mane_select": "NM_001408.3",
                        "mane": ["MANE_Select"],
                        "canonical": 1,
                        "biotype": "protein_coding",
                        "consequence_terms": ["3_prime_UTR_variant"],
                        "hgvsc": "ENST00000271332.4:c.*919G>T",
                        "impact": "MODIFIER",
                        "variant_allele": "T",
                    },
                    {
                        "gene_symbol": "PSRC1",
                        "transcript_id": "ENST00001144514",
                        "biotype": "protein_coding",
                        "distance": 659,
                        "consequence_terms": ["upstream_gene_variant"],
                        "impact": "MODIFIER",
                        "variant_allele": "T",
                    },
                ],
            }
        ]
        with patch(
            "app.tools.alphagenome_scoring.fetch_json",
            new_callable=AsyncMock,
            return_value=mock_vep_response,
        ):
            res = await resolve_variant_coordinates("rs12740374")

        assert res["status"] == "success"
        assert res["gene_symbol"] == "CELSR2"
        assert res["transcript_id"] == "ENST00000271332"
        assert res["mane_select"] == "NM_001408.3"
        assert res["consequence"] == "3_prime_UTR_variant"
        assert res["most_severe_consequence"] == "3_prime_UTR_variant"
        assert res["hgvsc"] == "ENST00000271332.4:c.*919G>T"
        assert res["avi_score"] is None
        assert res["scoring_source"] == "atlas_link_only"

    @pytest.mark.asyncio
    async def test_resolve_variant_coordinates_rsid_not_found(self):
        with patch(
            "app.tools.alphagenome_scoring.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = []
            res = await resolve_variant_coordinates("rs99999999999")
            assert res["status"] == "error"
            assert "not found in Ensembl VEP" in res["message"]


# ---------------------------------------------------------------------------
# 2. ClinVar Database Tool Tests
# ---------------------------------------------------------------------------
class TestClinVarTool:
    @pytest.mark.asyncio
    async def test_search_clinvar_success(self):
        mock_esearch_resp = {"esearchresult": {"idlist": ["13914"]}}
        mock_esummary_resp = {
            "result": {
                "uids": ["13914"],
                "13914": {
                    "title": "NM_000518.5(HBB):c.20A>T (p.Glu7Val)",
                    "germline_classification": {
                        "description": "Pathogenic",
                        "review_status": "practice guideline",
                        "gold_stars": 4,
                        "trait_set": [{"trait_name": "Sickle cell disease"}],
                    },
                    "canonical_spdi": "NC_000011.10:5227001:T:A",
                },
            }
        }

        with patch(
            "app.tools.clinvar.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = [mock_esearch_resp, mock_esummary_resp]

            res = await search_clinvar("rs334")
            assert res["status"] == "success"
            assert res["citation"] == "ClinVar (v.2024–06-03) (Landrum et al. 2018)"
            assert res["clinical_significance"] == "Pathogenic"
            assert res["gold_stars"] == 4
            assert res["review_status"] == "practice guideline"
            assert res["records"][0]["associated_traits"] == ["Sickle cell disease"]

    @pytest.mark.asyncio
    async def test_search_clinvar_not_found(self):
        mock_esearch_resp = {"esearchresult": {"idlist": []}}
        with patch(
            "app.tools.clinvar.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_esearch_resp

            res = await search_clinvar("nonexistent_variant")
            assert res["status"] == "success"
            assert res["count"] == 0
            assert len(res["records"]) == 0
            assert res["citation"] == "ClinVar (v.2024–06-03) (Landrum et al. 2018)"

    @pytest.mark.asyncio
    async def test_search_clinvar_handles_null_classifications(self):
        mock_esearch_resp = {"esearchresult": {"idlist": ["99999"]}}
        mock_esummary_resp = {
            "result": {
                "uids": ["99999"],
                "99999": {
                    "title": "NM_004985.5(KRAS):c.35G>A (p.Gly12Asp)",
                    "germline_classification": None,
                    "clinical_impact_classification": None,
                    "trait_set": [{"trait_name": "Somatic neoplasm"}],
                    "genes": [{"symbol": "KRAS"}],
                },
            }
        }
        with patch(
            "app.tools.clinvar.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = [mock_esearch_resp, mock_esummary_resp]
            res = await search_clinvar("rs121913529")
            assert res["status"] == "success"
            assert res["clinical_significance"] == "Unknown"
            assert res["gold_stars"] == 0
            assert res["records"][0]["clinical_impact"] is None
            assert res["records"][0]["associated_traits"] == ["Somatic neoplasm"]

    @pytest.mark.asyncio
    async def test_search_clinvar_rs334_ranks_canonical_15333_above_compound_haplotypes(
        self,
    ):
        mock_esearch_resp = {
            "esearchresult": {
                "count": "3",
                "idlist": ["446748", "15175", "15333"],
            }
        }
        mock_esummary_resp = {
            "result": {
                "uids": ["446748", "15175", "15333"],
                "446748": {
                    "title": "NM_000518.4(HBB):c.[205C>T;20A>T]",
                    "obj_type": "Haplotype",
                    "variation_set": [{}, {}],
                    "germline_classification": {
                        "description": "other",
                        "review_status": "no assertion criteria provided",
                        "trait_set": [{"trait_name": "Hemoglobin Jamaica Plain"}],
                    },
                    "genes": [{"symbol": "HBB"}],
                },
                "15175": {
                    "title": "NM_000518.4(HBB):c.20A>C (p.Glu7Ala)",
                    "obj_type": "single nucleotide variant",
                    "variation_set": [{"canonical_spdi": "NC_000011.10:5227001:T:G"}],
                    "germline_classification": {
                        "description": "Likely benign",
                        "review_status": "criteria provided, single submitter",
                        "trait_set": [{"trait_name": "Hemoglobin G-Makassar"}],
                    },
                    "genes": [{"symbol": "HBB"}],
                },
                "15333": {
                    "title": "NM_000518.5(HBB):c.20A>T (p.Glu7Val)",
                    "obj_type": "single nucleotide variant",
                    "variation_set": [{"canonical_spdi": "NC_000011.10:5227001:T:A"}],
                    "germline_classification": {
                        "description": "Pathogenic",
                        "review_status": "criteria provided, multiple submitters, no conflicts",
                        "trait_set": [{"trait_name": "Sickle cell disease"}],
                    },
                    "genes": [{"symbol": "HBB"}],
                },
            }
        }
        with patch(
            "app.tools.clinvar.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = [mock_esearch_resp, mock_esummary_resp]
            res = await search_clinvar("chr11:5227002:T>A")

        esearch_call_params = mock_fetch.await_args_list[0].kwargs["params"]
        assert esearch_call_params["retmax"] == 50
        assert esearch_call_params["term"] == "11[CHR] AND 5227002[CHRPOS38]"
        assert res["top_clinvar_id"] == "15333"
        assert res["clinical_significance"] == "Pathogenic"
        assert res["gold_stars"] == 2
        assert res["records"][0]["is_compound_haplotype"] is False
        assert res["records"][-1]["clinvar_id"] in ("446748", "15175")


# ---------------------------------------------------------------------------
# 3. gnomAD Database Tool Tests
# ---------------------------------------------------------------------------
class TestGnomadTool:
    @pytest.mark.asyncio
    async def test_query_gnomad_variant_ba1_tier(self):
        mock_graphql_resp = {
            "data": {
                "variant": {
                    "rsids": ["rs12740374"],
                    "joint": {
                        "ac": 22800,
                        "an": 100000,
                        "homozygote_count": 2500,
                        "faf95": {"popmax": 0.245, "popmax_population": "afr"},
                        "populations": [
                            {
                                "id": "afr",
                                "ac": 820,
                                "an": 10000,
                                "homozygote_count": 40,
                            },
                            {
                                "id": "afr_XX",
                                "ac": 410,
                                "an": 5000,
                                "homozygote_count": 20,
                            },
                        ],
                    },
                    "exome": {
                        "ac": 1000,
                        "an": 5000,
                        "af": 0.2,
                        "homozygote_count": 100,
                    },
                    "genome": {
                        "ac": 21800,
                        "an": 95000,
                        "af": 0.22947,
                        "homozygote_count": 2400,
                    },
                }
            }
        }
        with patch("app.tools.gnomad.post_json", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_graphql_resp

            res = await query_gnomad_variant("CHR1", 109274968, "g", "t")
            sent_query = mock_post.await_args.kwargs["json_data"]["query"]
            joint_block = sent_query.split("joint {")[1].split("exome {")[0]
            assert "\n          af\n" not in joint_block
            assert "faf95" in joint_block
            assert res["status"] == "success"
            assert res["variant_id"] == "1-109274968-G-T"
            assert res["rsids"] == ["rs12740374"]
            assert res["citation"] == "gnomAD (v4) (Karczewski et al. 2020)"
            assert res["allele_frequency"] == 0.228
            assert res["joint_ac"] == 22800
            assert res["joint_an"] == 100000
            assert res["homozygotes"] == 2500
            assert res["faf95"]["popmax"] == 0.245
            assert "afr" in res["populations"]
            assert "afr_XX" not in res["populations"]
            assert "BA1" in res["acmg_frequency_tier"]

    @pytest.mark.asyncio
    async def test_query_gene_constraint(self):
        mock_graphql_resp = {
            "data": {
                "gene": {
                    "gene_id": "ENSG00000143126",
                    "symbol": "CELSR2",
                    "gnomad_constraint": {
                        "pli": 0.9999,
                        "oe_lof": 0.322,
                        "oe_lof_lower": 0.269,
                        "oe_lof_upper": 0.387,
                        "oe_mis": 0.852,
                        "mis_z": 3.955,
                        "syn_z": -0.738,
                    },
                }
            }
        }
        with patch("app.tools.gnomad.post_json", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_graphql_resp

            res = await query_gene_constraint("CELSR2")
            sent_query = mock_post.await_args.kwargs["json_data"]["query"]
            assert "mis_z" in sent_query
            assert "syn_z" in sent_query
            assert "z_mis" not in sent_query
            assert "z_syn" not in sent_query
            assert res["status"] == "success"
            assert res["pli"] == 0.9999
            assert res["loeuf"] == 0.387
            assert res["mis_z"] == 3.955
            assert res["syn_z"] == -0.738
            assert res["is_lof_intolerant"] is True
            assert res["citation"] == "gnomAD (v4) (Karczewski et al. 2020)"


# ---------------------------------------------------------------------------
# 4. GWAS Catalog Tool Tests
# ---------------------------------------------------------------------------
class TestGwasCatalogTool:
    @pytest.mark.asyncio
    async def test_query_gwas_catalog_v2(self):
        mock_resp = {
            "_embedded": {
                "associations": [
                    {
                        "pvalue": 1.2e-180,
                        "pvalueDescription": None,
                        "orPerCopyNum": None,
                        "betaNum": 0.15,
                        "betaUnit": "mg/dL",
                        "betaDirection": "increase",
                        "strongestAlleles": [{"riskAlleleName": "rs12740374-G"}],
                        "_embedded": {
                            "efoTraits": [
                                {
                                    "trait": "low density lipoprotein cholesterol measurement",
                                    "shortForm": "EFO_0004611",
                                }
                            ]
                        },
                    }
                ]
            }
        }
        with patch(
            "app.tools.gwas_catalog.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = mock_resp

            res = await query_gwas_catalog("rs12740374")
            assert res["status"] == "success"
            assert res["citation"] == "GWAS Catalog (v.1.0) (Sollis et al. 2023)"
            assert len(res["associations"]) == 1
            assoc = res["associations"][0]
            assert assoc["trait"] == "low density lipoprotein cholesterol measurement"
            assert assoc["p_value"] == 1.2e-180
            assert assoc["risk_allele"] == "rs12740374-G"


# ---------------------------------------------------------------------------
# 5. PharmGKB / CPIC Tool Tests
# ---------------------------------------------------------------------------
class TestPharmGkbTool:
    @pytest.mark.asyncio
    async def test_query_pharmgkb_curated_vip_cyp2c19(self):
        with patch(
            "app.tools.pharmgkb.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = [
                {"drugname": "clopidogrel", "cpiclevel": "A"},
            ]
            res = await query_pharmgkb_variant("rs4244285", gene_symbol="CYP2C19")
            mock_fetch.assert_awaited_once_with(
                "https://api.cpicpgx.org/v1/pair_view",
                params={"genesymbol": "eq.CYP2C19"},
            )
            assert res["status"] == "success"
            assert res["citation"] == "PharmGKB (Whirl-Carrillo et al. 2012, 2021)"
            assert res["star_allele"] == "*2"
            assert res["evidence_level"] == "1A"
            assert "clopidogrel" in res["affected_drugs"]
            assert "prasugrel" in res["cpic_guideline"].lower()

    @pytest.mark.asyncio
    async def test_query_pharmgkb_curated_vip_cyp2d6(self):
        with patch(
            "app.tools.pharmgkb.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = [
                {"drugname": "codeine", "cpiclevel": "A"},
            ]
            res = await query_pharmgkb_variant("rs3892097", gene_symbol="CYP2D6")
            mock_fetch.assert_awaited_once_with(
                "https://api.cpicpgx.org/v1/pair_view",
                params={"genesymbol": "eq.CYP2D6"},
            )
            assert res["status"] == "success"
            assert res["citation"] == "PharmGKB (Whirl-Carrillo et al. 2012, 2021)"
            assert res["star_allele"] == "*4"
            assert res["evidence_level"] == "1A"
            assert "codeine" in res["affected_drugs"]

    @pytest.mark.asyncio
    async def test_query_pharmgkb_gene_fallback_leaves_evidence_level_none(self):
        with patch(
            "app.tools.pharmgkb.fetch_json", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = [
                [{"symbol": "CYP2C19", "clinpgxid": "PA124"}],
                [{"drugname": "clopidogrel", "cpiclevel": "A"}],
                [{"name": "*1", "clinicalfunctionalstatus": "Normal function"}],
            ]
            res = await query_pharmgkb_variant("rs99999999", gene_symbol="CYP2C19")
            assert res["status"] == "success"
            assert res["is_cpic_gene"] is True
            assert res["cpic_gene_has_guidelines"] is True
            assert res["pharmgkb_evidence_level"] is None
            assert res["evidence_level"] is None


# ---------------------------------------------------------------------------
# 6. GCS Bucket Tool Tests
# ---------------------------------------------------------------------------
class TestGcsBucketTool:
    def test_list_alphagenome_bucket_files_mock(self):
        with patch("google.cloud.storage.Client") as mock_storage_client:
            mock_client = MagicMock()
            mock_bucket = MagicMock()
            mock_blob1 = MagicMock()
            mock_blob1.name = "run1/alphagenome_scores.json"
            mock_blob1.size = 1024
            mock_blob1.updated = MagicMock(isoformat=lambda: "2026-10-06T12:00:00Z")
            mock_blob1.content_type = "application/json"

            mock_client.list_blobs.return_value = [mock_blob1]
            mock_client.bucket.return_value = mock_bucket
            mock_storage_client.return_value = mock_client

            res = list_alphagenome_bucket_files("test-bucket")
            assert res["status"] == "success"
            assert res["bucket"] == "test-bucket"
            assert len(res["files"]) == 1
            assert res["files"][0]["name"] == "run1/alphagenome_scores.json"

    def test_import_alphagenome_from_gcs_json_mock(self):
        with patch("google.cloud.storage.Client") as mock_storage_client:
            mock_client = MagicMock()
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.size = 256
            mock_blob.download_as_bytes.return_value = json.dumps(
                {
                    "chromosome": "1",
                    "position": 109274968,
                    "ref": "G",
                    "alt": "T",
                    "avi_quantile": 0.0018,
                    "MAX_ABS_CHIP_TF": 0.94,
                }
            ).encode("utf-8")

            mock_bucket.blob.return_value = mock_blob
            mock_client.bucket.return_value = mock_bucket
            mock_storage_client.return_value = mock_client

            res = import_alphagenome_from_gcs("gs://test-bucket/scores.json")
            assert res["status"] == "success"
            assert res["format"] == "json"
            assert len(res["variants"]) == 1
            assert res["variants"][0]["avi_score"]["impact_tier"] == "High Impact"

    def test_import_alphagenome_from_gcs_rejects_oversized_blob(self):
        with patch("google.cloud.storage.Client") as mock_storage_client:
            mock_client = MagicMock()
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.size = MAX_DOWNLOAD_BYTES + 1024

            mock_bucket.blob.return_value = mock_blob
            mock_client.bucket.return_value = mock_bucket
            mock_storage_client.return_value = mock_client

            res = import_alphagenome_from_gcs("gs://test-bucket/huge.vcf.gz")
            assert res["status"] == "error"
            assert "exceeds maximum allowed limit" in res["message"]

    def test_save_dossier_to_gcs_mock(self):
        with patch("google.cloud.storage.Client") as mock_storage_client:
            mock_client = MagicMock()
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_bucket.blob.return_value = mock_blob
            mock_client.bucket.return_value = mock_bucket
            mock_storage_client.return_value = mock_client

            res = save_dossier_to_gcs(
                dossier_markdown="# Variant Dossier\nDetails",
                variant_label="chr1:109274977:G>T (SORT1)",
                metadata={"gene": "SORT1"},
                gcs_bucket="gs://test-dossier-bucket/custom-prefix",
            )
            assert res["status"] == "success"
            assert res["bucket"] == "test-dossier-bucket"
            assert res["dossier_gcs_uri"].startswith(
                "gs://test-dossier-bucket/custom-prefix/"
            )
            assert res["dossier_gcs_uri"].endswith(
                "_chr1_109274977_G_T_SORT1_dossier.md"
            )
            assert res["metadata_gcs_uri"].endswith(
                "_chr1_109274977_G_T_SORT1_metadata.json"
            )
            assert mock_blob.upload_from_string.call_count == 2

    def test_save_dossier_to_gcs_skipped_when_no_bucket(self, monkeypatch):
        monkeypatch.delenv("ALPHAGENOME_GCS_BUCKET", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        res = save_dossier_to_gcs(dossier_markdown="# Variant Dossier", gcs_bucket="")
        assert res["status"] == "skipped"

    def test_import_alphagenome_from_gcs_relative_blob_path(self, monkeypatch):
        monkeypatch.setenv("ALPHAGENOME_GCS_BUCKET", "default-alphagenome-bucket")
        sample_vcf = (
            b"##fileformat=VCFv4.2\n"
            b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            b"chr1\t109274968\trs12740374\tG\tT\t.\tPASS\tGENE=SORT1;AVI_PHRED=27.45\n"
        )
        with patch("google.cloud.storage.Client") as mock_storage_client:
            mock_client = MagicMock()
            mock_bucket = MagicMock()
            mock_blob = MagicMock()
            mock_blob.size = len(sample_vcf)
            mock_blob.download_as_bytes.return_value = sample_vcf
            mock_bucket.blob.return_value = mock_blob
            mock_client.bucket.return_value = mock_bucket
            mock_storage_client.return_value = mock_client

            res = import_alphagenome_from_gcs("samples/sample_alphagenome_variants.vcf")
            assert res["status"] == "success"
            assert (
                res["source_gcs_uri"]
                == "gs://default-alphagenome-bucket/samples/sample_alphagenome_variants.vcf"
            )
            mock_client.bucket.assert_called_once_with("default-alphagenome-bucket")
            mock_bucket.blob.assert_called_once_with(
                "samples/sample_alphagenome_variants.vcf"
            )
            assert len(res["variants"]) == 1

    def test_import_alphagenome_from_gcs_rejects_http_and_traversal(self, monkeypatch):
        monkeypatch.setenv("ALPHAGENOME_GCS_BUCKET", "default-alphagenome-bucket")
        http_res = import_alphagenome_from_gcs("https://example.com/variants.vcf")
        assert http_res["status"] == "error"
        assert "HTTP(S) URLs are not supported" in http_res["message"]

        trav_res = import_alphagenome_from_gcs("../outside/variants.vcf")
        assert trav_res["status"] == "error"
        assert "Invalid GCS blob path" in trav_res["message"]

    @pytest.mark.asyncio
    async def test_http_client_reinitializes_when_closed(self):
        c1 = http_client()
        await c1.aclose()
        assert c1.is_closed
        c2 = http_client()
        assert c2 is not c1
        assert not c2.is_closed
        await c2.aclose()


# ---------------------------------------------------------------------------
# 7. Formatting & Visual Chart Artifact Tests
# ---------------------------------------------------------------------------
class TestFormattingAndCharts:
    def test_strip_biological_math_delimiters(self):
        from app.app_utils.formatting import strip_biological_math_delimiters

        raw = (
            "Creates a hepatic $CEBPA/CEBPB$ binding site marked by "
            "$H3K27ac, H3K4me1$ near $SORT1$, while keeping `$PRESERVED_CODE$` "
            "and pricing between $100 and $200 intact."
        )
        cleaned = strip_biological_math_delimiters(raw)
        assert "`CEBPA/CEBPB`" in cleaned
        assert "`H3K27ac, H3K4me1`" in cleaned
        assert "`SORT1`" in cleaned
        assert "$CEBPA/CEBPB$" not in cleaned
        assert "$H3K27ac, H3K4me1$" not in cleaned
        assert "`$PRESERVED_CODE$`" in cleaned
        assert "$100 and $200" in cleaned

    def test_normalize_alphagenome_atlas_urls(self):
        from app.app_utils.formatting import normalize_alphagenome_atlas_urls

        raw = (
            "- Variant: [AlphaGenome Atlas Variant View](https://deepmind.google.com/science/alphagenome/atlas?variant=chr1:109274968:G%3ET)\n"
            "- Raw arrow: https://deepmind.google.com/science/alphagenome/atlas?variant=1:109274968:G>T.\n"
            "- Locus: [AlphaGenome Atlas Locus View](https://deepmind.google.com/science/alphagenome/atlas?locus=chr1:109273968-109275968)\n"
            "- Canonical: https://deepmind.google.com/science/alphagenome/atlas?q=chr11:5227002:T%3EA&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
        )
        cleaned = normalize_alphagenome_atlas_urls(raw)
        expected_var = "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109274968:G%3ET&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
        expected_loc = "https://deepmind.google.com/science/alphagenome/atlas?q=chr1:109273968-109275968&m=locus&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
        assert f"[AlphaGenome Atlas Variant View]({expected_var})" in cleaned
        assert f"Raw arrow: {expected_var}." in cleaned
        assert f"[AlphaGenome Atlas Locus View]({expected_loc})" in cleaned
        assert "?variant=" not in cleaned
        assert "?locus=" not in cleaned
        assert (
            "https://deepmind.google.com/science/alphagenome/atlas?q=chr11:5227002:T%3EA&m=variant&lItems=avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
            in cleaned
        )

    def test_ensure_block_newlines_prevents_glued_doi_headings(self):
        from app.app_utils.formatting import ensure_block_newlines

        part_a = ensure_block_newlines(
            "Source: https://doi.org/10.1038/s41586-020-2308-7"
        )
        part_b = ensure_block_newlines("### Pharmacogenomics (PGx) Evaluation")
        combined = part_a + part_b
        assert "2308-7###" not in combined
        assert "https://doi.org/10.1038/s41586-020-2308-7\n\n" in combined

    def test_pad_and_sanitize_llm_response_callback(self):
        from google.adk.models import LlmResponse
        from google.genai import types

        from app.app_utils.formatting import pad_and_sanitize_llm_response

        # Partial streaming chunk must not be mutated
        partial_resp = LlmResponse(
            partial=True,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text="$SORT1$")]
            ),
        )
        pad_and_sanitize_llm_response(MagicMock(), partial_resp)
        assert partial_resp.content.parts[0].text == "$SORT1$"

        # Final turn with thought + visible text
        thought_part = types.Part(text="Thinking about $SORT1$...", thought=True)
        visible_part = types.Part.from_text(text=" hepatic $CEBPA/CEBPB$ at $H3K27ac$ ")
        final_resp = LlmResponse(
            partial=False,
            content=types.Content(role="model", parts=[thought_part, visible_part]),
        )
        pad_and_sanitize_llm_response(MagicMock(), final_resp)
        assert final_resp.content.parts[0].text == "Thinking about $SORT1$..."
        assert final_resp.content.parts[1].text == (
            "\n\nhepatic `CEBPA/CEBPB` at `H3K27ac`\n\n"
        )

    @pytest.mark.asyncio
    async def test_chart_png_renderers_and_tool_artifact_saving(self):
        from app.tools.charts import (
            build_alphagenome_modality_chart_png,
            build_avi_phred_comparison_chart_png,
            build_gnomad_ancestry_chart_png,
            render_alphagenome_modality_chart,
            render_gnomad_ancestry_chart,
        )

        ag_png = build_alphagenome_modality_chart_png(
            "rs12740374",
            {"MAX_ABS_CHIP_TF": 0.94, "MAX_ABS_ATAC": 0.89, "MAX_ABS_DNASE": 0.84},
            avi_phred=27.45,
            impact_tier="High Impact",
        )
        assert ag_png is not None and ag_png.startswith(b"\x89PNG\r\n\x1a\n")

        avi_png = build_avi_phred_comparison_chart_png(
            [
                {
                    "rsid": "rs12740374",
                    "gene_symbol": "SORT1",
                    "avi_score": {"phred": 27.45},
                },
                {
                    "rsid": "rs334",
                    "gene_symbol": "HBB",
                    "avi_score": {"phred": 33.98},
                },
            ]
        )
        assert avi_png is not None and avi_png.startswith(b"\x89PNG\r\n\x1a\n")

        gn_png = build_gnomad_ancestry_chart_png(
            "rs12740374",
            {"afr": {"af": 0.28}, "eur": {"af": 0.22}, "nfe": {"af": 0.21}},
            global_af=0.228,
        )
        assert gn_png is not None and gn_png.startswith(b"\x89PNG\r\n\x1a\n")

        mock_tool_ctx = MagicMock()
        mock_tool_ctx.save_artifact = AsyncMock(return_value=0)
        res_ag = await render_alphagenome_modality_chart(
            variant_label="rs12740374",
            modalities={"MAX_ABS_CHIP_TF": 0.94},
            avi_phred=27.45,
            impact_tier="High Impact",
            tool_context=mock_tool_ctx,
        )
        assert res_ag["status"] == "success"
        assert res_ag["filename"] == "alphagenome_modalities_rs12740374.png"

        res_gn = await render_gnomad_ancestry_chart(
            variant_label="rs12740374",
            populations={"AFR": 0.28, "NFE": 0.21},
            global_af=0.228,
            tool_context=mock_tool_ctx,
        )
        assert res_gn["status"] == "success"
        assert res_gn["filename"] == "gnomad_ancestry_af_rs12740374.png"
        assert mock_tool_ctx.save_artifact.await_count == 2
