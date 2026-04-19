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

"""Tests for BOLTZ2 pipeline compilation and config isolation."""

import json
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
        "FILESTORE_ID": "test-nfs",
        "ALPHAFOLD_COMPONENTS_IMAGE": "af2-image:latest",
        "BOLTZ2_COMPONENTS_IMAGE": "boltz2-image:stable",
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
    """Verify AF2 and BOLTZ2 get their own config module (no cache collision)."""

    def test_af2_then_boltz2_config_isolation(self):
        """Loading AF2 pipeline then BOLTZ2 pipeline uses correct base images."""
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

        # Now load BOLTZ2 pipeline (in same process)
        from foldrun_app.models.boltz2.utils.pipeline_utils import load_vertex_pipeline as load_boltz2

        boltz2_pipeline = load_boltz2(enable_flex_start=False)

        # Verify BOLTZ2's config was used
        from foldrun_app.models.boltz2.pipeline import config as boltz2_config

        assert boltz2_config.BOLTZ2_COMPONENTS_IMAGE == "boltz2-image:stable"

        # Both pipelines should be callable
        assert callable(af2_pipeline)
        assert callable(boltz2_pipeline)

    def test_boltz2_then_af2_config_isolation(self):
        """Loading BOLTZ2 pipeline then AF2 pipeline uses correct base images."""
        for key in list(sys.modules.keys()):
            if "pipeline.pipelines" in key or key == "config":
                del sys.modules[key]

        # Load BOLTZ2 first
        from foldrun_app.models.boltz2.utils.pipeline_utils import load_vertex_pipeline as load_boltz2

        boltz2_pipeline = load_boltz2(enable_flex_start=True)

        from foldrun_app.models.boltz2.pipeline import config as boltz2_config

        assert boltz2_config.BOLTZ2_COMPONENTS_IMAGE == "boltz2-image:stable"

        # Then AF2
        from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline as load_af2

        af2_pipeline = load_af2(enable_flex_start=True, msa_method="jackhmmer")

        from foldrun_app.models.af2.pipeline import config as af2_config

        assert af2_config.ALPHAFOLD_COMPONENTS_IMAGE == "af2-image:latest"


class TestBOLTZ2PipelineCompilation:
    """Actually compile the BOLTZ2 pipeline to YAML and verify structure."""

    def test_pipeline_compiles_to_yaml(self):
        """BOLTZ2 pipeline compiles to valid YAML without error."""
        for key in list(sys.modules.keys()):
            if key == "config":
                del sys.modules[key]

        from foldrun_app.models.boltz2.utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(enable_flex_start=True)

        # Compile to YAML
        output_path = os.path.join(tempfile.gettempdir(), "boltz2_test_pipeline.yaml")
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

        from foldrun_app.models.boltz2.utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(enable_flex_start=False)

        output_path = os.path.join(tempfile.gettempdir(), "boltz2_test_pipeline2.yaml")
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

        from foldrun_app.models.boltz2.utils.pipeline_utils import load_vertex_pipeline

        pipeline = load_vertex_pipeline(enable_flex_start=False)

        output_path = os.path.join(tempfile.gettempdir(), "boltz2_test_pipeline3.yaml")
        from kfp import compiler

        compiler.Compiler().compile(
            pipeline_func=pipeline,
            package_path=output_path,
        )

        with open(output_path) as f:
            pipeline_spec = f.read()

        assert "relax" not in pipeline_spec.lower()
        os.remove(output_path)


class TestBOLTZ2PredictComponentArgs:
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
            "boltz2",
            "pipeline",
            "components",
            "predict.py",
        )
        with open(path) as f:
            return f.read()

    def test_uses_run_openfold_cli(self):
        """Predict component uses boltz predict CLI entrypoint."""
        source = self._read_predict_source()
        assert '"boltz"' in source
        assert '"predict"' in source

    def test_passes_query_yaml(self):
        """Predict passes the local query YAML path as the positional input to boltz predict."""
        source = self._read_predict_source()
        assert "query_local" in source  # the local copy of the query YAML

    def test_passes_inference_ckpt_path(self):
        """Predict passes --cache for NFS weights."""
        source = self._read_predict_source()
        assert "--cache=" in source

    def test_no_ccd_path_flag(self):
        """BOLTZ2 resolves CCD internally — no --ccd_path flag."""
        source = self._read_predict_source()
        assert "--ccd_path" not in source

    def test_disables_msa_server(self):
        """Predict doesn't pass --use_msa_server anymore."""
        source = self._read_predict_source()
        assert "--use_msa_server" not in source

    def test_disables_templates(self):
        """Predict doesn't pass --use_templates."""
        source = self._read_predict_source()
        assert "--use_templates" not in source

    def test_copies_query_to_patched_query(self):
        """Predict copies query YAML to a local patched_query.yaml for deterministic stem naming."""
        source = self._read_predict_source()
        assert "patched_query.yaml" in source
        assert "shutil.copy2" in source

    def test_no_yaml_seeds_patching(self):
        """Seeds are passed via --seed CLI flag, NOT by patching a 'seeds' field in the YAML."""
        source = self._read_predict_source()
        assert '["seeds"]' not in source  # No YAML mutation

    def test_num_model_seeds_is_one(self):
        """Boltz doesn't use --num_model_seeds."""
        source = self._read_predict_source()
        assert '"--num_model_seeds=1"' not in source

    def test_passes_num_diffusion_samples(self):
        """Predict passes --diffusion_samples to BOLTZ2 CLI."""
        source = self._read_predict_source()
        assert "--diffusion_samples=" in source

    def test_passes_seed_flag(self):
        """Predict passes --seed CLI flag (correct Boltz-2 mechanism for seed control)."""
        source = self._read_predict_source()
        assert "--seed=" in source

    def test_passes_output_dir(self):
        """Predict passes --out_dir flag."""
        source = self._read_predict_source()
        assert "--out_dir=" in source

    def test_outputs_cif(self):
        """Predict outputs .cif format (not .pdb)."""
        source = self._read_predict_source()
        assert ".cif" in source
        assert ".pdb" not in source

    def test_outputs_confidence_json(self):
        """Predict captures confidence json."""
        source = self._read_predict_source()
        assert "confidence_" in source

    def test_searches_boltz_results_wrapper_dir(self):
        """Predict searches under boltz_results_patched_query/ output wrapper."""
        source = self._read_predict_source()
        assert "boltz_results_patched_query" in source

    def test_passes_override_flag(self):
        """Predict passes --override so re-runs don't skip existing outputs."""
        source = self._read_predict_source()
        assert '"--override"' in source
