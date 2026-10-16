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

"""Evaluation harness for AlphaGenome Variant Annotation Agent across benchmark variants."""

import json
from pathlib import Path

import pytest

from app.tools.alphagenome_scoring import compute_avi_score

BENCHMARKS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "samples"
    / "sample_alphagenome_outputs.json"
)


@pytest.fixture
def benchmark_data():
    with open(BENCHMARKS_PATH, encoding="utf-8") as f:
        return json.load(f)["benchmark_variants"]


def test_eval_benchmark_variants_structure(benchmark_data):
    """Verify all benchmark variants have required multi-database annotations and citations."""
    expected_citations = {
        "ClinVar (v.2024–06-03) (Landrum et al. 2018)",
        "gnomAD (v4) (Karczewski et al. 2020)",
        "GWAS Catalog (v.1.0) (Sollis et al. 2023)",
        "PharmGKB (Whirl-Carrillo et al. 2012, 2021)",
    }

    variant_ids = {v["variant_id"] for v in benchmark_data}
    assert "rs12740374" in variant_ids
    assert "rs334" in variant_ids
    assert "rs4244285" in variant_ids

    for var in benchmark_data:
        assert "alphagenome_prediction" in var
        pred = var["alphagenome_prediction"]
        assert "Impact" in pred["impact_tier"] or pred["impact_tier"] in (
            "High",
            "Moderate-High",
            "Moderate",
            "Low",
        )
        assert pred["avi_phred"] >= 0.0

        # Check citations
        citations = {
            var["clinvar_annotation"]["citation"],
            var["gnomad_v4"]["citation"],
            var["gwas_catalog"]["citation"],
            var["pharmgkb"]["citation"],
        }
        assert expected_citations == citations


def test_eval_avi_phred_calibration(benchmark_data):
    """Verify mathematical fidelity of AVI Phred calculation across benchmarks."""
    for var in benchmark_data:
        pred = var["alphagenome_prediction"]
        q = pred["avi_quantile"]
        expected_phred = pred["avi_phred"]

        computed = compute_avi_score(quantile=q)
        assert abs(computed["phred"] - expected_phred) < 0.05
        assert pred["impact_tier"] in computed["impact_tier"]
