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

"""Tests for BindCraft hardware configuration and GPU recommendation."""

import os
from unittest.mock import patch

import pytest

from foldrun_app.models.bindcraft.base import BINDCRAFTTool
from foldrun_app.models.bindcraft.config import BINDCRAFTConfig


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "BINDCRAFT_COMPONENTS_IMAGE": "test-bindcraft-image:stable",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class DummyBINDCRAFTTool(BINDCRAFTTool):
    """Concrete subclass for testing BINDCRAFTTool."""

    def run(self, arguments):
        return {}


@pytest.fixture
def bindcraft_tool():
    config = BINDCRAFTConfig()
    tool_config = {"name": "test_tool", "type": "BINDCRAFTTool"}
    return DummyBINDCRAFTTool(tool_config, config)


class TestBINDCRAFTHardware:
    """Tests for GPU recommendation and hardware selection in BindCraft."""

    def test_recommend_gpu_small_complex(self):
        assert BINDCRAFTTool._recommend_gpu(300) == "A100"
        assert BINDCRAFTTool._recommend_gpu(450) == "A100"

    def test_recommend_gpu_large_complex(self):
        assert BINDCRAFTTool._recommend_gpu(451) == "A100_80GB"
        assert BINDCRAFTTool._recommend_gpu(800) == "A100_80GB"

    def test_get_hardware_config_auto_small(self, bindcraft_tool):
        hw = bindcraft_tool._get_hardware_config("auto", num_residues=300)
        assert hw["gpu_type"] == "A100"
        assert hw["predict_machine"] == "a2-highgpu-1g"
        assert hw["predict_accel"] == "NVIDIA_TESLA_A100"
        assert hw["predict_count"] == 1

    def test_get_hardware_config_auto_large(self, bindcraft_tool):
        hw = bindcraft_tool._get_hardware_config("auto", num_residues=600)
        assert hw["gpu_type"] == "A100_80GB"
        assert hw["predict_machine"] == "a2-ultragpu-1g"
        assert hw["predict_accel"] == "NVIDIA_A100_80GB"
        assert hw["predict_count"] == 1

    def test_get_hardware_config_explicit_a100(self, bindcraft_tool):
        hw = bindcraft_tool._get_hardware_config("A100")
        assert hw["predict_accel"] == "NVIDIA_TESLA_A100"
        assert hw["predict_machine"] == "a2-highgpu-1g"

    def test_get_hardware_config_explicit_a100_80gb(self, bindcraft_tool):
        hw = bindcraft_tool._get_hardware_config("A100_80GB")
        assert hw["predict_accel"] == "NVIDIA_A100_80GB"
        assert hw["predict_machine"] == "a2-ultragpu-1g"

    def test_get_hardware_config_explicit_l4(self, bindcraft_tool):
        hw = bindcraft_tool._get_hardware_config("L4")
        assert hw["predict_accel"] == "NVIDIA_L4"
        assert hw["predict_machine"] == "g2-standard-12"

    def test_get_hardware_config_unsupported_gpu(self, bindcraft_tool):
        with pytest.raises(ValueError, match="Unsupported GPU type for BindCraft: 'V100'"):
            bindcraft_tool._get_hardware_config("V100")

    def test_count_tokens_and_gpu_recommendation_lengths_list(self):
        from foldrun_app.models.bindcraft.utils.input_converter import count_tokens

        tokens_80_120 = count_tokens({"starting_pdb": "target.pdb", "lengths": [80, 120]})
        assert tokens_80_120 == 370
        assert BINDCRAFTTool._recommend_gpu(tokens_80_120) == "A100"

        tokens_65_150 = count_tokens({"starting_pdb": "target.pdb", "lengths": [65, 150]})
        assert tokens_65_150 == 400
        assert BINDCRAFTTool._recommend_gpu(tokens_65_150) == "A100"
