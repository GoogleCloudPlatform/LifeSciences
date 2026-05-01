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

"""Tests for BOLTZ2 pipeline: source inspection, ParallelFor over seeds, no relax."""

import os


class TestBOLTZ2PipelineSource:
    """Inspect BOLTZ2 pipeline source for correctness."""

    @staticmethod
    def _read_pipeline_source():
        pipeline_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "..",
            "foldrun_app",
            "models",
            "boltz2",
            "pipeline",
            "pipelines",
            "boltz2_inference_pipeline.py",
        )
        with open(pipeline_path) as f:
            return f.read()

    def test_pipeline_has_three_steps(self):
        """Pipeline has configure_seeds, MSA, and predict steps."""
        source = self._read_pipeline_source()
        assert "configure_seeds" in source
        assert "MSAPipelineBOLTZ2" in source
        assert "PredictBOLTZ2" in source


    def test_no_relax_step(self):
        """BOLTZ2 pipeline should NOT have a relax step."""
        source = self._read_pipeline_source()
        assert "relax_protein" not in source
        assert "JobRelaxOp" not in source

    def test_parallel_for_over_seeds(self):
        """BOLTZ2 uses ParallelFor over seed configs."""
        source = self._read_pipeline_source()
        assert "dsl.ParallelFor" in source
        assert "seed_configs" in source
        assert 'name="seed-predict"' in source

    def test_pipeline_has_retry_policies(self):
        """MSA and predict steps should have retry policies."""
        source = self._read_pipeline_source()
        retry_count = source.count(".set_retry(")
        assert retry_count == 2, f"Expected 2 set_retry() calls (MSA, predict), found {retry_count}"

    def test_nfs_mounts_on_msa_and_predict(self):
        """Both MSA and predict steps should have NFS mounts."""
        source = self._read_pipeline_source()
        assert source.count("nfs_mounts=") >= 2

    def test_flex_start_on_predict_only(self):
        """MSA uses STANDARD, predict uses configurable strategy."""
        source = self._read_pipeline_source()
        assert 'strategy="STANDARD"' in source
        assert "strategy=strategy" in source

    def test_passes_num_diffusion_samples(self):
        """Pipeline passes num_diffusion_samples to predict step."""
        source = self._read_pipeline_source()
        assert "num_diffusion_samples" in source

    def test_passes_seed_value(self):
        """Pipeline passes seed_value from ParallelFor to predict."""
        source = self._read_pipeline_source()
        assert "seed_value=seed_config.seed_value" in source

    def test_passes_nfs_cache_path(self):
        """Pipeline passes nfs_cache_path (unified weights+CCD dir) to predict step."""
        source = self._read_pipeline_source()
        assert "nfs_cache_path=nfs_cache_path" in source
