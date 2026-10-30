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

"""Tests for RFD4-Proteina pipeline source, steps, and ParallelFor design execution."""

import os


class TestRFD4ProteinaPipelineSource:
    """Inspect RFD4-Proteina pipeline source for structural correctness."""

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
            "rfd4_proteina",
            "pipeline",
            "pipelines",
            "rfd4_proteina_inference_pipeline.py",
        )
        with open(pipeline_path) as f:
            return f.read()

    def test_pipeline_has_two_steps(self):
        """Pipeline has configure_seeds and predict steps."""
        source = self._read_pipeline_source()
        assert "ConfigureSeedsRFD4Proteina" in source
        assert "PredictRFD4Proteina" in source

    def test_no_msa_step(self):
        """RFD4-Proteina generative design does not use CPU MSA pipeline."""
        source = self._read_pipeline_source()
        assert "MSAPipeline" not in source
        assert "jackhmmer" not in source

    def test_parallel_for_over_seeds(self):
        """RFD4-Proteina uses ParallelFor over seed configs."""
        source = self._read_pipeline_source()
        assert "dsl.ParallelFor" in source
        assert "seed_configs" in source
        assert 'name="seed-predict"' in source

    def test_campaign_scaling_todo_documented(self):
        """Pipeline documents TODO for aggregate/filter reducer."""
        source = self._read_pipeline_source()
        assert "TODO(campaign-scaling)" in source

    def test_predict_component_uses_rfd4_generate_and_no_silent_fallback(self):
        """Predict component runs rfd4-generate with check=True and raises FileNotFoundError on empty output."""
        predict_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "..",
            "foldrun_app",
            "models",
            "rfd4_proteina",
            "pipeline",
            "components",
            "predict.py",
        )
        with open(predict_path) as f:
            source = f.read()
        assert '"rfd4-generate"' in source
        assert "subprocess.run(cmd, cwd=output_dir, check=True)" in source
        assert "raise FileNotFoundError" in source
        assert "# RFD4-Proteina output" not in source
