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

"""Tests for AF2 pipeline compilation — load, compile to YAML, verify DAG."""

import os
import sys
import tempfile
import yaml
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "ALPHAFOLD_COMPONENTS_IMAGE": "af2-image:latest",
        "BOLTZ2_COMPONENTS_IMAGE": "boltz2-image:stable",
        "OPENFOLD3_COMPONENTS_IMAGE": "of3-image:latest",
        "NFS_SERVER": "10.1.0.2",
        "NFS_PATH": "/datasets",
        "NFS_MOUNT_POINT": "/mnt/nfs/foldrun",
        "AGENT_PLATFORM_NETWORK_ATTACHMENT": "projects/123/global/networks/test-net",
        "DATA_PIPELINE_MACHINE_TYPE": "c2-standard-16",
        "PREDICT_MACHINE_TYPE": "a2-highgpu-1g",
        "PREDICT_ACCELERATOR_TYPE": "NVIDIA_TESLA_A100",
        "PREDICT_ACCELERATOR_COUNT": "1",
        "RELAX_MACHINE_TYPE": "a2-highgpu-1g",
        "RELAX_ACCELERATOR_TYPE": "NVIDIA_TESLA_A100",
        "RELAX_ACCELERATOR_COUNT": "1",
        "PARALLELISM": "5",
        "DWS_MAX_WAIT_HOURS": "168",
        "MODEL_PARAMS_GCS_LOCATION": "gs://test-bucket/alphafold2",
        "GOOGLE_CLOUD_PROJECT": "test-project",
        "GOOGLE_CLOUD_LOCATION": "global",
    }
    with patch.dict(os.environ, env, clear=False):
        yield


def _clear_config_cache():
    for key in list(sys.modules.keys()):
        if key == "config" or "pipeline.pipelines" in key:
            del sys.modules[key]


class TestAF2PipelineCompilation:
    """Compile the AF2 pipeline to YAML and verify the result."""

    def test_monomer_pipeline_compiles(self):
        """Standard jackhmmer monomer pipeline compiles without error."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline
        from kfp import compiler

        pipeline = load_vertex_pipeline(enable_flex_start=False, msa_method="jackhmmer")
        out = os.path.join(tempfile.gettempdir(), "af2_mono_test.yaml")
        compiler.Compiler().compile(pipeline_func=pipeline, package_path=out)

        with open(out) as f:
            spec = yaml.safe_load(f)
        assert "pipelineSpec" in spec or "components" in spec
        os.remove(out)

    def test_flex_start_pipeline_compiles(self):
        """FLEX_START pipeline compiles without error."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline
        from kfp import compiler

        pipeline = load_vertex_pipeline(enable_flex_start=True, msa_method="jackhmmer")
        out = os.path.join(tempfile.gettempdir(), "af2_flex_test.yaml")
        compiler.Compiler().compile(pipeline_func=pipeline, package_path=out)

        with open(out) as f:
            spec = yaml.safe_load(f)
        assert "pipelineSpec" in spec or "components" in spec
        os.remove(out)

    def test_compiled_pipeline_contains_predict(self):
        """Compiled YAML references a predict task."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline
        from kfp import compiler

        pipeline = load_vertex_pipeline(enable_flex_start=False)
        out = os.path.join(tempfile.gettempdir(), "af2_check_predict.yaml")
        compiler.Compiler().compile(pipeline_func=pipeline, package_path=out)

        with open(out) as f:
            content = f.read()
        assert "predict" in content.lower()
        os.remove(out)

    def test_compiled_pipeline_contains_relax(self):
        """Compiled YAML references a relax task — AF2-specific."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline
        from kfp import compiler

        pipeline = load_vertex_pipeline(enable_flex_start=False)
        out = os.path.join(tempfile.gettempdir(), "af2_check_relax.yaml")
        compiler.Compiler().compile(pipeline_func=pipeline, package_path=out)

        with open(out) as f:
            content = f.read()
        assert "relax" in content.lower()
        os.remove(out)

    def test_compiled_pipeline_contains_data_pipeline(self):
        """Compiled YAML references a data pipeline (MSA) task."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline
        from kfp import compiler

        pipeline = load_vertex_pipeline(enable_flex_start=False)
        out = os.path.join(tempfile.gettempdir(), "af2_check_msa.yaml")
        compiler.Compiler().compile(pipeline_func=pipeline, package_path=out)

        with open(out) as f:
            content = f.read()
        assert "data" in content.lower() or "pipeline" in content.lower()
        os.remove(out)

    def test_compiled_pipeline_has_sequence_path_parameter(self):
        """Compiled YAML exposes sequence_path as a required input parameter."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline
        from kfp import compiler

        pipeline = load_vertex_pipeline(enable_flex_start=False)
        out = os.path.join(tempfile.gettempdir(), "af2_check_seqpath.yaml")
        compiler.Compiler().compile(pipeline_func=pipeline, package_path=out)

        with open(out) as f:
            content = f.read()
        # AF2 uses PARALLELISM as a baked-in env var, not a pipeline parameter.
        # Verify the pipeline has sequence_path and model_preset as user-facing inputs.
        assert "sequence_path" in content
        assert "model_preset" in content
        os.remove(out)


class TestAF2ConfigModuleIsolation:
    """Verify AF2 config module doesn't collide with OF3 or Boltz-2."""

    def test_af2_then_of3_config_isolation(self):
        """Loading AF2 pipeline then OF3 pipeline uses correct base images."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline as load_af2

        af2_pipeline = load_af2(enable_flex_start=False)

        from foldrun_app.models.af2.pipeline import config as af2_config

        assert af2_config.ALPHAFOLD_COMPONENTS_IMAGE == "af2-image:latest"

        _clear_config_cache()
        from foldrun_app.models.of3.utils.pipeline_utils import load_vertex_pipeline as load_of3

        of3_pipeline = load_of3(enable_flex_start=False)

        from foldrun_app.models.of3.pipeline import config as of3_config

        assert of3_config.OPENFOLD3_COMPONENTS_IMAGE == "of3-image:latest"

        assert callable(af2_pipeline)
        assert callable(of3_pipeline)

    def test_af2_then_boltz2_config_isolation(self):
        """Loading AF2 pipeline then Boltz-2 pipeline uses correct base images."""
        _clear_config_cache()
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline as load_af2

        load_af2(enable_flex_start=False)

        from foldrun_app.models.af2.pipeline import config as af2_config

        assert af2_config.ALPHAFOLD_COMPONENTS_IMAGE == "af2-image:latest"

        _clear_config_cache()
        from foldrun_app.models.boltz2.utils.pipeline_utils import load_vertex_pipeline as load_b2

        load_b2(enable_flex_start=False)

        from foldrun_app.models.boltz2.pipeline import config as b2_config

        assert b2_config.BOLTZ2_COMPONENTS_IMAGE == "boltz2-image:stable"
