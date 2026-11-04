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

"""Unit tests for FoldRun cost estimation across Boltz-2, OF3, and AF2 GPU tiers."""

from unittest.mock import patch

import pytest

from foldrun_app.core.hardware import AF3_HARDWARE_TIER_LADDER
from foldrun_app.core.kfp_tools import KFPModelTool
from foldrun_app.models.af2.base import AF2Tool
from foldrun_app.models.boltz2.base import BOLTZ2Tool
from foldrun_app.models.of3.base import OF3Tool
from foldrun_app.skills.cost_estimation.pricing import (
    DEFAULT_PRICES,
    DURATION_ANCHORS,
    _auto_select_gpu,
    _interpolate_durations,
    _machine_for_gpu,
    _normalize_gpu_type,
    _relax_gpu,
    estimate_single_job,
)
from foldrun_app.skills.cost_estimation.tools import estimate_job_cost


@pytest.fixture(autouse=True)
def mock_default_prices():
    """Avoid network calls to Cloud Billing Catalog API in unit tests."""
    with patch(
        "foldrun_app.skills.cost_estimation.pricing.get_prices",
        return_value=DEFAULT_PRICES.copy(),
    ):
        yield


class TestBoltz2L4CostEstimation:
    """Verify Boltz-2 explicit L4 duration anchors and pricing."""

    def test_boltz2_has_explicit_l4_duration_anchors(self):
        """DURATION_ANCHORS['boltz2'] defines L4 after A100 tiers."""
        boltz2_anchors = DURATION_ANCHORS["boltz2"]
        assert "L4" in boltz2_anchors
        assert next(iter(boltz2_anchors)) == "A100"

    @pytest.mark.parametrize("seq_len", [100, 300, 600, 1000, 1500, 2000])
    def test_boltz2_l4_predict_duration_exceeds_a100(self, seq_len):
        """Boltz-2 L4 (--no_kernels --write_full_pde) takes longer than A100 across all lengths."""
        l4_est = estimate_job_cost(
            job_type="boltz2",
            gpu_type="L4",
            sequence_length=seq_len,
            num_predictions_per_model=2,
            num_diffusion_samples=5,
        )
        a100_est = estimate_job_cost(
            job_type="boltz2",
            gpu_type="A100",
            sequence_length=seq_len,
            num_predictions_per_model=2,
            num_diffusion_samples=5,
        )

        l4_predict = next(p for p in l4_est["on_demand"]["phases"] if p["phase"] == "Predict")
        a100_predict = next(p for p in a100_est["on_demand"]["phases"] if p["phase"] == "Predict")

        assert l4_est["gpu_type"] == "L4"
        assert l4_est["num_diffusion_samples"] == 5
        assert l4_predict["machine_type"] == "g2-standard-12"
        assert l4_predict["gpu"] == "L4 x 1"
        assert l4_predict["duration_minutes"] > a100_predict["duration_minutes"]
        assert l4_predict["hourly_rate"] < a100_predict["hourly_rate"]

    def test_boltz2_gpu_type_normalization_in_cost_estimation(self):
        """Lowercase and hyphenated GPU strings normalize cleanly in estimate_single_job."""
        l4_lower = estimate_single_job("boltz2", gpu_type="l4", sequence_length=300)
        assert l4_lower["gpu_type"] == "L4"

        a100_80 = estimate_single_job("boltz2", gpu_type="a100-80gb", sequence_length=2500)
        assert a100_80["gpu_type"] == "A100_80GB"
        predict_phase = next(p for p in a100_80["on_demand"]["phases"] if p["phase"] == "Predict")
        assert predict_phase["machine_type"] == "a2-ultragpu-1g"

    def test_unknown_gpu_fallback_prefers_a100_not_l4(self):
        """Unrecognized GPU keys in _interpolate_durations fall back to A100 anchors."""
        assert _interpolate_durations("boltz2", "UNKNOWN_GPU", 300) == _interpolate_durations(
            "boltz2", "A100", 300
        )


class TestOF3L4UpgradeInCostEstimation:
    """Verify OF3 cost estimation mirrors OF3Tool._get_hardware_config L4 upgrade."""

    def test_of3_l4_upgrades_to_a100_for_standard_inputs(self):
        est = estimate_job_cost(job_type="of3", gpu_type="l4", sequence_length=500)
        assert est["gpu_type"] == "A100"
        predict_phase = next(p for p in est["on_demand"]["phases"] if p["phase"] == "Predict")
        assert predict_phase["machine_type"] == "a2-highgpu-1g"
        assert predict_phase["gpu"] == "A100 x 1"

    def test_of3_l4_upgrades_to_a100_80gb_for_large_inputs(self):
        est = estimate_job_cost(job_type="of3", gpu_type="L4", sequence_length=2500)
        assert est["gpu_type"] == "A100_80GB"
        predict_phase = next(p for p in est["on_demand"]["phases"] if p["phase"] == "Predict")
        assert predict_phase["machine_type"] == "a2-ultragpu-1g"
        assert predict_phase["gpu"] == "A100_80GB x 1"

    def test_machine_for_gpu_tiers(self):
        assert _machine_for_gpu("L4") == "g2-standard-12"
        assert _machine_for_gpu("A100") == "a2-highgpu-1g"
        assert _machine_for_gpu("A100_80GB") == "a2-ultragpu-1g"
        assert _machine_for_gpu("H100") == "a3-highgpu-1g"


class TestPricingSubmissionNonDriftContract:
    """Guarantee pricing GPU selection and machine specs stay identical to submission tools."""

    @pytest.mark.parametrize(
        "seq_len",
        [50, 999, 1000, 1500, 1501, 2000, 2001, 2200, 2201, 3000],
    )
    def test_auto_select_gpu_matches_submission_tools(self, seq_len):
        assert _auto_select_gpu("af2_monomer", seq_len) == AF2Tool._recommend_gpu(
            seq_len, is_multimer=False
        )
        assert _auto_select_gpu("af2_multimer", seq_len) == AF2Tool._recommend_gpu(
            seq_len, is_multimer=True
        )
        assert _auto_select_gpu("of3", seq_len) == OF3Tool._recommend_gpu(seq_len)
        assert _auto_select_gpu("boltz2", seq_len) == BOLTZ2Tool._recommend_gpu(seq_len)

        expected_af3_top = AF3_HARDWARE_TIER_LADDER[0]["tier"]
        if expected_af3_top.startswith("H100"):
            expected_af3_top = "H100"
        assert _auto_select_gpu("af3", seq_len) == expected_af3_top

    @pytest.mark.parametrize("gpu_tier", ["L4", "A100", "A100_80GB", "H100", "UNKNOWN"])
    def test_relax_gpu_matches_kfp_model_tool_map(self, gpu_tier):
        assert _relax_gpu(gpu_tier) == KFPModelTool.RELAX_GPU_MAP.get(gpu_tier, "A100")

    def test_machine_for_gpu_matches_canonical_specs(self):
        for gpu_tier, (machine_type, _, _) in KFPModelTool.GPU_SPECS.items():
            assert _machine_for_gpu(gpu_tier) == machine_type
        assert _machine_for_gpu("H100") == AF3_HARDWARE_TIER_LADDER[0]["machine_type"]

    @pytest.mark.parametrize(
        "raw_gpu",
        [None, "", "auto", "AUTO", "l4", "a100-80gb", " A100 "],
    )
    def test_normalize_gpu_type_matches_kfp_model_tool(self, raw_gpu):
        assert _normalize_gpu_type(raw_gpu) == KFPModelTool._normalize_gpu_type(raw_gpu)
