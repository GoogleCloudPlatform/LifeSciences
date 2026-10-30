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

"""Tests for BOLTZGEN hardware configuration and GPU recommendation."""

import os
from unittest.mock import patch

import pytest

from foldrun_app.models.boltzgen.base import BOLTZGENTool
from foldrun_app.models.boltzgen.config import BOLTZGENConfig


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "BOLTZGEN_COMPONENTS_IMAGE": "test-boltzgen-image:stable",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class DummyBOLTZGENTool(BOLTZGENTool):
    """Concrete subclass for testing BOLTZGENTool."""

    def run(self, arguments):
        return {}


@pytest.fixture
def boltzgen_tool():
    config = BOLTZGENConfig()
    tool_config = {"name": "test_tool", "type": "BOLTZGENTool"}
    return DummyBOLTZGENTool(tool_config, config)


class TestBOLTZGENHardware:
    """Tests for GPU recommendation and hardware selection in BoltzGen."""

    def test_recommend_gpu_small_complex(self):
        assert BOLTZGENTool._recommend_gpu(500) == "A100"
        assert BOLTZGENTool._recommend_gpu(1500) == "A100"

    def test_recommend_gpu_large_complex(self):
        assert BOLTZGENTool._recommend_gpu(1501) == "A100_80GB"
        assert BOLTZGENTool._recommend_gpu(3000) == "A100_80GB"

    def test_get_hardware_config_auto_small(self, boltzgen_tool):
        hw = boltzgen_tool._get_hardware_config("auto", num_tokens=500)
        assert hw["gpu_type"] == "A100"
        assert hw["predict_machine"] == "a2-highgpu-1g"
        assert hw["predict_accel"] == "NVIDIA_TESLA_A100"
        assert hw["predict_count"] == 1

    def test_get_hardware_config_auto_large(self, boltzgen_tool):
        hw = boltzgen_tool._get_hardware_config("auto", num_tokens=2000)
        assert hw["gpu_type"] == "A100_80GB"
        assert hw["predict_machine"] == "a2-ultragpu-1g"
        assert hw["predict_accel"] == "NVIDIA_A100_80GB"
        assert hw["predict_count"] == 1

    def test_get_hardware_config_explicit_a100(self, boltzgen_tool):
        hw = boltzgen_tool._get_hardware_config("A100")
        assert hw["predict_accel"] == "NVIDIA_TESLA_A100"
        assert hw["predict_machine"] == "a2-highgpu-1g"

    def test_get_hardware_config_explicit_a100_80gb(self, boltzgen_tool):
        hw = boltzgen_tool._get_hardware_config("A100_80GB")
        assert hw["predict_accel"] == "NVIDIA_A100_80GB"
        assert hw["predict_machine"] == "a2-ultragpu-1g"

    def test_get_hardware_config_explicit_l4(self, boltzgen_tool):
        hw = boltzgen_tool._get_hardware_config("L4")
        assert hw["predict_accel"] == "NVIDIA_L4"
        assert hw["predict_machine"] == "g2-standard-12"

    def test_get_hardware_config_unsupported_gpu(self, boltzgen_tool):
        with pytest.raises(ValueError, match="Unsupported GPU type for BoltzGen: 'V100'"):
            boltzgen_tool._get_hardware_config("V100")
