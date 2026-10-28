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

"""Tests for BOLTZ2 hardware configuration and GPU tiers."""

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
        "BOLTZ2_COMPONENTS_IMAGE": "test-boltz2-image:stable",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


class TestBOLTZ2GPURecommendation:
    """Test GPU auto-selection tiers (A100 only, no L4)."""

    def test_small_input_gets_a100(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        assert BOLTZ2Tool._recommend_gpu(500) == "A100"

    def test_medium_input_gets_a100(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        assert BOLTZ2Tool._recommend_gpu(2000) == "A100"

    def test_large_input_gets_a100_80gb(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        assert BOLTZ2Tool._recommend_gpu(2001) == "A100_80GB"

    def test_very_large_input_gets_a100_80gb(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        assert BOLTZ2Tool._recommend_gpu(5000) == "A100_80GB"

    def test_no_l4_option(self):
        """BOLTZ2 should never recommend L4."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        # Even tiny inputs get A100
        assert BOLTZ2Tool._recommend_gpu(10) == "A100"
        assert BOLTZ2Tool._recommend_gpu(100) == "A100"


class TestBOLTZ2HardwareConfig:
    """Test hardware config generation."""

    def test_auto_gpu_selection(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("auto", num_tokens=500)
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"

    def test_explicit_a100_80gb(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100_80GB")
        assert config["predict_accel"] == "NVIDIA_A100_80GB"
        assert config["predict_machine"] == "a2-ultragpu-1g"

    def test_no_relax_config(self):
        """BOLTZ2 hardware config should not contain relax fields."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100")
        assert "relax_machine" not in config
        assert "relax_accel" not in config

    def test_msa_machine_present(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100")
        assert config["msa_machine"] == "c2-standard-16"

    def test_multi_gpu(self):
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("A100", num_gpus=4)
        assert config["predict_machine"] == "a2-highgpu-4g"
        assert config["predict_count"] == 4

    def test_unsupported_environment_raises(self):
        """When environment has no compatible GPUs, Boltz-2 must raise ValueError."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        tool.config.set_supported_gpus(["V100"])
        with pytest.raises(ValueError):
            tool._get_hardware_config("auto", num_tokens=500)

    def test_unsupported_environment_only_l4_auto_raises(self):
        """When environment only supports L4, auto and >=40GB requests must raise ValueError instead of downgrading to L4."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        tool.config.set_supported_gpus(["L4"])
        with pytest.raises(ValueError):
            tool._get_hardware_config("auto", num_tokens=500)
        with pytest.raises(ValueError):
            tool._get_hardware_config("A100")

    def test_explicit_unsupported_gpu_raises(self):
        """Explicitly requesting unsupported GPU like V100 must raise ValueError."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        with pytest.raises(ValueError):
            tool._get_hardware_config("V100")

    def test_explicit_l4_supported(self):
        """Explicitly requesting L4 must configure g2-standard-12 and NVIDIA_L4."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config("L4")
        assert config["predict_accel"] == "NVIDIA_L4"
        assert config["predict_machine"] == "g2-standard-12"
        assert config["predict_count"] == 1

        tool.config.set_supported_gpus(["L4"])
        l4_only_config = tool._get_hardware_config("L4")
        assert l4_only_config["predict_accel"] == "NVIDIA_L4"
        assert l4_only_config["predict_machine"] == "g2-standard-12"

    def test_explicit_l4_upgrades_when_l4_not_supported(self):
        """Explicit L4 request upgrades along [L4, A100, A100_80GB] when L4 quota is unavailable."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        tool.config.set_supported_gpus(["A100"])
        config = tool._get_hardware_config("L4")
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"
        assert config["predict_machine"] == "a2-highgpu-1g"

    def test_fallback_skips_unsupported_accelerators(self):
        """Fallback selection should only consider GPUs supported by both environment and Boltz-2."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        tool.config.set_supported_gpus(["L4", "A100"])
        config = tool._get_hardware_config("A100_80GB")
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"

    @pytest.mark.parametrize(
        "raw_gpu,expected_accel,expected_machine",
        [
            ("l4", "NVIDIA_L4", "g2-standard-12"),
            (" l4 ", "NVIDIA_L4", "g2-standard-12"),
            ("a100", "NVIDIA_TESLA_A100", "a2-highgpu-1g"),
            ("a100-80gb", "NVIDIA_A100_80GB", "a2-ultragpu-1g"),
            ("A100-80GB", "NVIDIA_A100_80GB", "a2-ultragpu-1g"),
            (" AUTO ", "NVIDIA_TESLA_A100", "a2-highgpu-1g"),
            ("", "NVIDIA_TESLA_A100", "a2-highgpu-1g"),
        ],
    )
    def test_gpu_type_case_and_hyphen_normalization(
        self, raw_gpu, expected_accel, expected_machine
    ):
        """gpu_type normalizes lowercase, whitespace, empty strings, and hyphenated job labels."""
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        config = tool._get_hardware_config(raw_gpu, num_tokens=500)
        assert config["predict_accel"] == expected_accel
        assert config["predict_machine"] == expected_machine

    def test_explicit_l4_high_token_logs_oom_warning(self, caplog):
        """Resolved L4 with >1000 tokens logs an explicit 24GB VRAM OOM warning."""
        import logging

        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        with caplog.at_level(logging.WARNING, logger="foldrun_app.models.boltz2.base"):
            config = tool._get_hardware_config("l4", num_tokens=1250)

        assert config["predict_accel"] == "NVIDIA_L4"
        assert any("1250 tokens (>1000) on 24GB L4 VRAM" in r.message for r in caplog.records)

    def test_explicit_l4_small_or_upgraded_skips_oom_warning(self, caplog):
        """No 24GB L4 OOM warning when <=1000 tokens or when L4 auto-upgrades to A100."""
        import logging

        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        tool = BOLTZ2Tool({"name": "test", "description": "test"})
        with caplog.at_level(logging.WARNING, logger="foldrun_app.models.boltz2.base"):
            tool._get_hardware_config("L4", num_tokens=1000)
        assert not any("24GB L4 VRAM" in r.message for r in caplog.records)

        caplog.clear()
        tool.config.set_supported_gpus(["A100"])
        with caplog.at_level(logging.WARNING, logger="foldrun_app.models.boltz2.base"):
            upgraded = tool._get_hardware_config("L4", num_tokens=1500)
        assert upgraded["predict_accel"] == "NVIDIA_TESLA_A100"
        assert not any("24GB L4 VRAM" in r.message for r in caplog.records)

    def test_adk_submit_boltz2_docstring_advertises_l4(self):
        """Gemini's ADK FunctionTool schema (derived from submit_boltz2_prediction docstring) advertises L4."""
        from foldrun_app.skills.job_submission.tools import submit_boltz2_prediction

        doc = submit_boltz2_prediction.__doc__ or ""
        assert '"L4"' in doc
        assert "g2-standard-12" in doc


class TestBOLTZ2StartupGPUDetection:
    """Test startup GPU quota auto-detection retains L4 alongside A100 tiers."""

    def test_startup_retains_l4_and_a100(self):
        from foldrun_app.models.boltz2.config import BOLTZ2Config
        from foldrun_app.models.boltz2.startup import _auto_detect_gpus

        config = BOLTZ2Config()
        with patch(
            "foldrun_app.core.hardware.detect_supported_gpus",
            return_value=["L4", "A100", "V100"],
        ):
            _auto_detect_gpus(config)
        assert config.supported_gpus == ["L4", "A100"]
