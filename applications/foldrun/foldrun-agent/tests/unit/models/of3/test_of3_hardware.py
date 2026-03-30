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

"""Tests for OF3 hardware configuration and GPU tiers."""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "OPENFOLD3_COMPONENTS_IMAGE": "test-of3-image:stable",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestOF3GPURecommendation:
    """Test GPU auto-selection tiers (A100 only, no L4)."""

    def test_small_input_gets_a100(self):
        from foldrun_app.models.of3.base import OF3Tool

        assert OF3Tool._recommend_gpu(500) == "A100"

    def test_medium_input_gets_a100(self):
        from foldrun_app.models.of3.base import OF3Tool

        assert OF3Tool._recommend_gpu(2000) == "A100"

    def test_large_input_gets_a100_80gb(self):
        from foldrun_app.models.of3.base import OF3Tool

        assert OF3Tool._recommend_gpu(2001) == "A100_80GB"

    def test_very_large_input_gets_a100_80gb(self):
        from foldrun_app.models.of3.base import OF3Tool

        assert OF3Tool._recommend_gpu(5000) == "A100_80GB"

    def test_no_l4_option(self):
        """OF3 should never recommend L4."""
        from foldrun_app.models.of3.base import OF3Tool

        # Even tiny inputs get A100
        assert OF3Tool._recommend_gpu(10) == "A100"
        assert OF3Tool._recommend_gpu(100) == "A100"


class TestOF3HardwareConfig:
    """Test hardware config generation."""

    def test_auto_gpu_selection(self):
        from foldrun_app.models.of3.base import OF3Tool

        tool = OF3Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("auto", num_tokens=500)
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"

    def test_explicit_a100_80gb(self):
        from foldrun_app.models.of3.base import OF3Tool

        tool = OF3Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100_80GB")
        assert config["predict_accel"] == "NVIDIA_A100_80GB"
        assert config["predict_machine"] == "a2-ultragpu-1g"

    def test_no_relax_config(self):
        """OF3 hardware config should not contain relax fields."""
        from foldrun_app.models.of3.base import OF3Tool

        tool = OF3Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100")
        assert "relax_machine" not in config
        assert "relax_accel" not in config

    def test_msa_machine_present(self):
        from foldrun_app.models.of3.base import OF3Tool

        tool = OF3Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100")
        assert config["msa_machine"] == "c2-standard-16"

    def test_multi_gpu(self):
        from foldrun_app.models.of3.base import OF3Tool

        tool = OF3Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100", num_gpus=4)
        assert config["predict_machine"] == "a2-highgpu-4g"
        assert config["predict_count"] == 4
