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

"""Tests for AF2Tool._setup_compile_env environment isolation and thread safety (CWE-362)."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


class TestAF2CompileEnvIsolation:
    """Verify _setup_compile_env cleans stale MMSEQS2_* vars and isolates concurrent threads."""

    def _make_tool(self, mock_env_vars):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.base import AF2Tool
            from foldrun_app.models.af2.config import Config

            tool_config = {"name": "test_tool", "description": "test"}
            config = Config()
            return AF2Tool(tool_config, config)

    def test_jackhmmer_cleans_stale_mmseqs2_env_vars(self, mock_env_vars):
        """A jackhmmer request after an mmseqs2 request must remove stale MMSEQS2_* overrides."""
        tool = self._make_tool(mock_env_vars)
        hw_mmseqs2 = tool._get_hardware_config("L4", msa_method="mmseqs2")
        hw_jackhmmer = tool._get_hardware_config("L4", msa_method="jackhmmer")

        # Request 1: mmseqs2 sets MMSEQS2_* overrides in os.environ
        tool._setup_compile_env(hw_mmseqs2, "10.0.0.1", "projects/123/global/networks/default")
        assert os.environ.get("MMSEQS2_DATA_PIPELINE_MACHINE_TYPE") == "g2-standard-12"
        assert os.environ.get("MMSEQS2_ACCELERATOR_TYPE") == "NVIDIA_L4"
        assert os.environ.get("MMSEQS2_ACCELERATOR_COUNT") == "1"

        # Request 2: jackhmmer immediately follows WITHOUT manual os.environ cleanup
        tool._setup_compile_env(hw_jackhmmer, "10.0.0.1", "projects/123/global/networks/default")
        assert "MMSEQS2_DATA_PIPELINE_MACHINE_TYPE" not in os.environ
        assert "MMSEQS2_ACCELERATOR_TYPE" not in os.environ
        assert "MMSEQS2_ACCELERATOR_COUNT" not in os.environ

    def test_compile_env_context_manager_thread_safety_and_isolation(self, mock_env_vars):
        """Concurrent requests using _setup_compile_env context manager must not race on os.environ."""
        tool = self._make_tool(mock_env_vars)
        hw_mmseqs2 = tool._get_hardware_config("A100", msa_method="mmseqs2")
        hw_jackhmmer = tool._get_hardware_config("L4", msa_method="jackhmmer")

        def worker(use_mmseqs2: bool) -> dict[str, str | None]:
            hw = hw_mmseqs2 if use_mmseqs2 else hw_jackhmmer
            with tool._setup_compile_env(hw, "10.0.0.1", "projects/123/global/networks/default"):
                time.sleep(0.02)
                return {
                    "predict_accel": os.environ.get("PREDICT_ACCELERATOR_TYPE"),
                    "mmseqs2_accel": os.environ.get("MMSEQS2_ACCELERATOR_TYPE"),
                    "mmseqs2_machine": os.environ.get("MMSEQS2_DATA_PIPELINE_MACHINE_TYPE"),
                }

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker, i % 2 == 0) for i in range(8)]
            results = [f.result() for f in futures]

        for idx, res in enumerate(results):
            if idx % 2 == 0:
                assert res["predict_accel"] == "NVIDIA_TESLA_A100"
                assert res["mmseqs2_accel"] == "NVIDIA_L4"
                assert res["mmseqs2_machine"] == "g2-standard-12"
            else:
                assert res["predict_accel"] == "NVIDIA_L4"
                assert res["mmseqs2_accel"] is None
                assert res["mmseqs2_machine"] is None

    def test_compile_env_reentrant_lock(self, mock_env_vars):
        """AF2Tool._compile_env_lock must be a re-entrant lock supporting nested contexts."""
        tool = self._make_tool(mock_env_vars)
        assert hasattr(tool, "_compile_env_lock")
        assert isinstance(tool._compile_env_lock, type(threading.RLock()))

        hw_mmseqs2 = tool._get_hardware_config("L4", msa_method="mmseqs2")
        hw_jackhmmer = tool._get_hardware_config("L4", msa_method="jackhmmer")

        with tool._setup_compile_env(
            hw_mmseqs2, "10.0.0.1", "projects/123/global/networks/default"
        ):
            assert os.environ.get("MMSEQS2_ACCELERATOR_TYPE") == "NVIDIA_L4"
            with tool._setup_compile_env(
                hw_jackhmmer, "10.0.0.1", "projects/123/global/networks/default"
            ):
                assert "MMSEQS2_ACCELERATOR_TYPE" not in os.environ
