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

"""Tests for AF2 hardware configuration and GPU tier auto-selection."""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "ALPHAFOLD_COMPONENTS_IMAGE": "test-af2-image:latest",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestAF2GPURecommendation:
    """Test GPU auto-selection tiers for monomers and multimers.

    After the L4 removal (fix #47), A100 is the minimum auto-selected GPU.
    L4 can still be requested explicitly but is never auto-selected.
    """

    # --- Monomer thresholds ---

    def test_monomer_small_gets_a100(self):
        """Monomers <=1500 residues get A100 40GB."""
        from foldrun_app.models.af2.base import AF2Tool

        assert AF2Tool._recommend_gpu(100, is_multimer=False) == "A100"
        assert AF2Tool._recommend_gpu(500, is_multimer=False) == "A100"
        assert AF2Tool._recommend_gpu(1500, is_multimer=False) == "A100"

    def test_monomer_large_gets_a100_80gb(self):
        """Monomers >1500 residues get A100 80GB."""
        from foldrun_app.models.af2.base import AF2Tool

        assert AF2Tool._recommend_gpu(1501, is_multimer=False) == "A100_80GB"
        assert AF2Tool._recommend_gpu(3000, is_multimer=False) == "A100_80GB"

    def test_monomer_never_gets_l4_auto(self):
        """L4 is never auto-selected for any monomer size."""
        from foldrun_app.models.af2.base import AF2Tool

        for length in [10, 100, 500, 1000, 1500]:
            assert AF2Tool._recommend_gpu(length, is_multimer=False) != "L4"

    # --- Multimer thresholds ---

    def test_multimer_small_gets_a100(self):
        """Multimers <1000 total residues get A100 40GB."""
        from foldrun_app.models.af2.base import AF2Tool

        assert AF2Tool._recommend_gpu(999, is_multimer=True) == "A100"
        assert AF2Tool._recommend_gpu(500, is_multimer=True) == "A100"

    def test_multimer_large_gets_a100_80gb(self):
        """Multimers >=1000 total residues get A100 80GB."""
        from foldrun_app.models.af2.base import AF2Tool

        assert AF2Tool._recommend_gpu(1000, is_multimer=True) == "A100_80GB"
        assert AF2Tool._recommend_gpu(2000, is_multimer=True) == "A100_80GB"

    def test_multimer_never_gets_l4_auto(self):
        """L4 is never auto-selected for any multimer size."""
        from foldrun_app.models.af2.base import AF2Tool

        for length in [100, 500, 999]:
            assert AF2Tool._recommend_gpu(length, is_multimer=True) != "L4"


class TestAF2HardwareConfig:
    """Test hardware config dictionary generation."""

    def test_auto_gpu_small_monomer(self):
        """auto GPU for small monomer resolves to A100."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config(
            "auto", seq_length=500, is_multimer=False
        )
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"
        assert config["predict_machine"] == "a2-highgpu-1g"

    def test_auto_gpu_large_monomer(self):
        """auto GPU for large monomer resolves to A100 80GB."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config(
            "auto", seq_length=2000, is_multimer=False
        )
        assert config["predict_accel"] == "NVIDIA_A100_80GB"
        assert config["predict_machine"] == "a2-ultragpu-1g"

    def test_explicit_a100(self):
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100")
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"
        assert config["predict_machine"] == "a2-highgpu-1g"

    def test_explicit_a100_80gb(self):
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100_80GB")
        assert config["predict_accel"] == "NVIDIA_A100_80GB"
        assert config["predict_machine"] == "a2-ultragpu-1g"

    def test_explicit_l4_still_works(self):
        """Users can still request L4 explicitly, even though it's not auto-selected."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("L4")
        assert config["predict_accel"] == "NVIDIA_L4"
        assert config["predict_machine"] == "g2-standard-12"

    def test_relax_uses_a100_when_predict_is_a100(self):
        """A100 predict tier uses A100 for relax (not L4 — slow to provision)."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100")
        assert config["relax_accel"] == "NVIDIA_TESLA_A100"
        assert config["relax_machine"] == "a2-highgpu-1g"

    def test_relax_downgraded_to_a100_when_predict_is_a100_80gb(self):
        """A100 80GB predict downgrades relax to A100 40GB for cost savings."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100_80GB")
        assert config["relax_accel"] == "NVIDIA_TESLA_A100"
        assert config["relax_machine"] == "a2-highgpu-1g"

    def test_multi_gpu_a100(self):
        """Multi-GPU A100 selects correct multi-GPU machine type."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100", num_gpus=4)
        assert config["predict_machine"] == "a2-highgpu-4g"
        assert config["predict_count"] == 4

    def test_multi_gpu_a100_80gb(self):
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100_80GB", num_gpus=2)
        assert config["predict_machine"] == "a2-ultragpu-2g"
        assert config["predict_count"] == 2

    def test_data_pipeline_always_cpu(self):
        """Data pipeline (MSA) uses CPU machine regardless of GPU tier."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        for gpu in ["L4", "A100", "A100_80GB"]:
            config = tool._get_hardware_config(gpu)
            assert config["data_pipeline"] == "c2-standard-16"

    def test_mmseqs2_adds_gpu_dp_config(self):
        """MMseqs2 MSA method adds GPU data pipeline config."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100", msa_method="mmseqs2")
        assert config["msa_method"] == "mmseqs2"
        assert "dp_machine" in config
        assert "dp_accel" in config
        assert "dp_accel_count" in config

    def test_jackhmmer_no_gpu_dp_config(self):
        """Jackhmmer MSA method does not add GPU data pipeline config."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100", msa_method="jackhmmer")
        assert config["msa_method"] == "jackhmmer"
        assert "dp_machine" not in config

    def test_auto_msa_method_defaults_to_jackhmmer(self):
        """auto msa_method resolves to jackhmmer (not mmseqs2)."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100", msa_method="auto")
        assert config["msa_method"] == "jackhmmer"

    def test_relax_gpu_override(self):
        """Explicit relax_gpu_type overrides the default relax config."""
        from foldrun_app.models.af2.base import AF2Tool

        tool = AF2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100", relax_gpu_type="L4")
        assert config["relax_accel"] == "NVIDIA_L4"
        assert config["relax_machine"] == "g2-standard-12"

    def test_unsupported_gpu_upgrades(self):
        """Requesting an unsupported GPU auto-upgrades along the path L4→A100→A100_80GB."""
        from foldrun_app.models.af2.base import AF2Tool
        from foldrun_app.models.af2.config import Config

        with patch.object(Config, "supported_gpus", new_callable=lambda: property(lambda self: ["A100"])):
            tool = AF2Tool({"name": "test", "description": "test"})
            config = tool._get_hardware_config("L4")
            assert config["predict_accel"] == "NVIDIA_TESLA_A100"
