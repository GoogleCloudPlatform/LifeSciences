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

"""Unit tests for AF2 pipeline_utils module (Finding 4.29 - CWE-427)."""

import importlib
import os
import sys
import threading
from unittest.mock import patch

from foldrun_app.models.af2.utils import pipeline_utils


class TestAF2PipelineUtilsSysPathIsolation:
    """Verify load_vertex_pipeline prevents cross-model config pollution."""

    def test_load_vertex_pipeline_prevents_cross_model_config_pollution(self, tmp_path):
        """When another model prepends its directory to sys.path after AF2 was loaded,
        re-calling AF2's load_vertex_pipeline must move AF2's pipeline directory back
        to sys.path[0] so `import config` resolves to AF2's config, not the other model's."""
        competing_dir = tmp_path / "competing_model_pipeline"
        competing_dir.mkdir()
        (competing_dir / "config.py").write_text("MODEL_NAME = 'COMPETING_POLLUTED'\n")

        af2_pipeline_dir = os.path.abspath(
            os.path.join(os.path.dirname(pipeline_utils.__file__), "..", "pipeline")
        )

        orig_sys_path = list(sys.path)
        orig_config_mod = sys.modules.get("config")
        try:
            with patch.dict(
                os.environ,
                {"ALPHAFOLD_COMPONENTS_IMAGE": "gcr.io/test-project/af2:latest"},
                clear=False,
            ):
                # Ensure AF2 pipeline module is importable first
                if af2_pipeline_dir not in sys.path:
                    sys.path.insert(0, af2_pipeline_dir)
                af2_pipeline_mod = importlib.import_module(
                    "foldrun_app.models.af2.pipeline.pipelines.alphafold_inference_pipeline"
                )

                # Now simulate another model prepending its pipeline directory to sys.path[0]
                sys.path.insert(0, str(competing_dir))
                assert sys.path[0] == str(competing_dir)
                assert af2_pipeline_dir in sys.path

                observed = {}

                def fake_create_pipeline(strategy: str, msa_method: str):
                    import config

                    observed["sys_path_0"] = sys.path[0]
                    observed["config_file"] = os.path.abspath(config.__file__)
                    return "mock_pipeline"

                with patch.object(
                    af2_pipeline_mod,
                    "create_alphafold_inference_pipeline",
                    side_effect=fake_create_pipeline,
                ):
                    result = pipeline_utils.load_vertex_pipeline()

                assert result == "mock_pipeline"
                assert observed["sys_path_0"] == af2_pipeline_dir
                assert os.path.dirname(observed["config_file"]) == af2_pipeline_dir
                assert sys.path.count(af2_pipeline_dir) == 1
        finally:
            sys.path[:] = orig_sys_path
            if orig_config_mod is not None:
                sys.modules["config"] = orig_config_mod
            else:
                sys.modules.pop("config", None)

    def test_load_vertex_pipeline_uses_lock(self):
        """Verify load_vertex_pipeline protects global sys.path/sys.modules mutation with a lock."""
        assert hasattr(pipeline_utils, "_PIPELINE_LOAD_LOCK")
        assert isinstance(pipeline_utils._PIPELINE_LOAD_LOCK, type(threading.Lock()))

        af2_pipeline_dir = os.path.abspath(
            os.path.join(os.path.dirname(pipeline_utils.__file__), "..", "pipeline")
        )
        orig_sys_path = list(sys.path)
        orig_config_mod = sys.modules.get("config")
        try:
            with patch.dict(
                os.environ,
                {"ALPHAFOLD_COMPONENTS_IMAGE": "gcr.io/test-project/af2:latest"},
                clear=False,
            ):
                if af2_pipeline_dir not in sys.path:
                    sys.path.insert(0, af2_pipeline_dir)
                af2_pipeline_mod = importlib.import_module(
                    "foldrun_app.models.af2.pipeline.pipelines.alphafold_inference_pipeline"
                )

                lock_held_during_create = []

                def fake_create_pipeline(strategy: str, msa_method: str):
                    lock_held_during_create.append(pipeline_utils._PIPELINE_LOAD_LOCK.locked())
                    return "mock_pipeline"

                with patch.object(
                    af2_pipeline_mod,
                    "create_alphafold_inference_pipeline",
                    side_effect=fake_create_pipeline,
                ):
                    pipeline_utils.load_vertex_pipeline()

                assert lock_held_during_create == [True]
                assert not pipeline_utils._PIPELINE_LOAD_LOCK.locked()
        finally:
            sys.path[:] = orig_sys_path
            if orig_config_mod is not None:
                sys.modules["config"] = orig_config_mod
            else:
                sys.modules.pop("config", None)
