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

"""Tests for OF3 pipeline compilation and config isolation."""

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
        "OPENFOLD3_COMPONENTS_IMAGE": "of3-image:stable",
        "NFS_SERVER": "10.1.0.2",
        "NFS_PATH": "/datasets",
        "NFS_MOUNT_POINT": "/mnt/nfs/foldrun",
        "NETWORK": "projects/123/global/networks/test-net",
        "PREDICT_MACHINE_TYPE": "a2-highgpu-1g",
        "PREDICT_ACCELERATOR_TYPE": "NVIDIA_TESLA_A100",
        "PREDICT_ACCELERATOR_COUNT": "1",
        "GOOGLE_CLOUD_PROJECT": "test-project",
        "GOOGLE_CLOUD_LOCATION": "global",
    }
    with patch.dict(os.environ, env, clear=False):
        yield


class TestConfigModuleIsolation:
    """Verify AF2 and OF3 get their own config module (no cache collision)."""

    def test_af2_then_of3_config_isolation(self):
        """Loading AF2 pipeline then OF3 pipeline uses correct base images."""
        # Clear any cached config and pipeline modules
        for key in list(sys.modules.keys()):
            if "pipeline.pipelines" in key or key == "config":
                del sys.modules[key]

        # Load AF2 pipeline
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline as load_af2

        af2_pipeline = load_af2(enable_flex_start=False)

        # Verify AF2's config was used (check the cached config module)
        # The AF2 component imports should have used af2's config
        from foldrun_app.models.af2.pipeline import config as af2_config

        assert af2_config.ALPHAFOLD_COMPONENTS_IMAGE == "af2-image:latest"

        # Now load OF3 pipeline (in same process)
        from foldrun_app.models.of3.utils.pipeline_utils import load_vertex_pipeline as load_of3

        of3_pipeline = load_of3(enable_flex_start=False)

        # Verify OF3's config was used
        from foldrun_app.models.of3.pipeline import config as of3_config

        assert of3_config.OPENFOLD3_COMPONENTS_IMAGE == "of3-image:stable"

        # Both pipelines should be callable
        assert callable(af2_pipeline)
        assert callable(of3_pipeline)

    def test_of3_then_af2_config_isolation(self):
        """Loading OF3 pipeline then AF2 pipeline uses correct base images."""
        for key in list(sys.modules.keys()):
            if "pipeline.pipelines" in key or key == "config":
                del sys.modules[key]

        # Load OF3 first
        from foldrun_app.models.of3.utils.pipeline_utils import load_vertex_pipeline as load_of3

        of3_pipeline = load_of3(enable_flex_start=True)

        from foldrun_app.models.of3.pipeline import config as of3_config

        assert of3_config.OPENFOLD3_COMPONENTS_IMAGE == "of3-image:stable"

        # Then AF2
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline as load_af2

        af2_pipeline = load_af2(enable_flex_start=True, msa_method="jackhmmer")

        from foldrun_app.models.af2.pipeline import config as af2_config

        assert af2_config.ALPHAFOLD_COMPONENTS_IMAGE == "af2-image:latest"


class TestOF3PipelineCompilation:
    """Actually compile the OF3 pipeline to YAML and verify structure."""

    def test_pipeline_compiles_to_yaml(self):
        """OF3 pipeline compiles to valid YAML without error."""
        for key in list(sys.modules.keys()):
            if key == "config":
                del sys.modules[key]

        from foldrun_app.models.of3.utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(enable_flex_start=True)

        # Compile to YAML
        output_path = os.path.join(tempfile.gettempdir(), "of3_test_pipeline.yaml")
        from kfp import compiler

        compiler.Compiler().compile(
            pipeline_func=pipeline,
            package_path=output_path,
        )

        # Verify valid YAML
        with open(output_path) as f:
            pipeline_yaml = yaml.safe_load(f)

        assert "pipelineSpec" in pipeline_yaml or "components" in pipeline_yaml
        os.remove(output_path)

    def test_compiled_pipeline_has_two_tasks(self):
        """Compiled pipeline has MSA and predict tasks."""
        for key in list(sys.modules.keys()):
            if key == "config":
                del sys.modules[key]

        from foldrun_app.models.of3.utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(enable_flex_start=False)

        output_path = os.path.join(tempfile.gettempdir(), "of3_test_pipeline2.yaml")
        from kfp import compiler

        compiler.Compiler().compile(
            pipeline_func=pipeline,
            package_path=output_path,
        )

        with open(output_path) as f:
            pipeline_yaml = yaml.safe_load(f)

        # Check pipeline spec for task names
        pipeline_spec = yaml.dump(pipeline_yaml)
        assert "predict" in pipeline_spec.lower()
        assert "msa" in pipeline_spec.lower()

        os.remove(output_path)

    def test_compiled_pipeline_no_relax(self):
        """Compiled pipeline YAML contains no relax references."""
        for key in list(sys.modules.keys()):
            if key == "config":
                del sys.modules[key]

        from foldrun_app.models.of3.utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(enable_flex_start=False)

        output_path = os.path.join(tempfile.gettempdir(), "of3_test_pipeline3.yaml")
        from kfp import compiler

        compiler.Compiler().compile(
            pipeline_func=pipeline,
            package_path=output_path,
        )

        with open(output_path) as f:
            pipeline_spec = f.read()

        assert "relax" not in pipeline_spec.lower()
        os.remove(output_path)


class TestOF3PredictComponentArgs:
    """Verify predict component has correct CLI args for run_openfold."""

    @staticmethod
    def _read_predict_source():
        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "..",
            "foldrun_app",
            "models",
            "of3",
            "pipeline",
            "components",
            "predict.py",
        )
        with open(path) as f:
            return f.read()

    def test_uses_run_openfold_cli(self):
        """Predict component uses run_openfold CLI entrypoint (not python -m)."""
        source = self._read_predict_source()
        assert '"run_openfold"' in source
        assert '"predict"' in source

    def test_passes_query_json(self):
        """Predict passes --query_json flag."""
        source = self._read_predict_source()
        assert "--query_json=" in source

    def test_passes_inference_ckpt_path(self):
        """Predict passes --inference_ckpt_path for NFS weights."""
        source = self._read_predict_source()
        assert "--inference_ckpt_path=" in source

    def test_no_ccd_path_flag(self):
        """OF3 resolves CCD internally — no --ccd_path flag."""
        source = self._read_predict_source()
        assert "--ccd_path" not in source

    def test_disables_msa_server(self):
        """Predict uses --use_msa_server=False (precomputed MSAs)."""
        source = self._read_predict_source()
        assert "--use_msa_server=False" in source

    def test_templates_flag_is_dynamic(self):
        """Predict passes --use_templates dynamically (not hardcoded False)."""
        source = self._read_predict_source()
        # Must be a dynamic flag — template support is now configurable
        assert "--use_templates=" in source
        # Should NOT be hardcoded to False (was the old behaviour, now configurable)
        assert '"--use_templates=False"' not in source

    def test_patches_query_json_seeds(self):
        """Predict patches query JSON seeds field for correct output directory naming."""
        source = self._read_predict_source()
        assert "patched_query" in source
        assert '["seeds"]' in source

    def test_num_model_seeds_is_one(self):
        """Each predict task runs with --num_model_seeds=1 (one seed per GPU)."""
        source = self._read_predict_source()
        assert '"--num_model_seeds=1"' in source

    def test_passes_num_diffusion_samples(self):
        """Predict passes --num_diffusion_samples to OF3 CLI."""
        source = self._read_predict_source()
        assert "--num_diffusion_samples=" in source

    def test_no_bare_seed_flag(self):
        """OF3 predict doesn't use --seed (no such flag)."""
        source = self._read_predict_source()
        assert "'--seed=" not in source

    def test_passes_output_dir(self):
        """Predict passes --output_dir flag."""
        source = self._read_predict_source()
        assert "--output_dir=" in source

    def test_outputs_cif(self):
        """Predict outputs .cif format (not .pdb)."""
        source = self._read_predict_source()
        assert ".cif" in source
        assert ".pdb" not in source

    def test_outputs_confidence_json(self):
        """Predict captures confidences_aggregated.json."""
        source = self._read_predict_source()
        assert "confidences_aggregated.json" in source
