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

"""Tests for AF2 pipeline source structure."""

import os


def _read_pipeline_source():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "foldrun_app",
        "models",
        "af2",
        "pipeline",
        "pipelines",
        "alphafold_inference_pipeline.py",
    )
    with open(os.path.normpath(path)) as f:
        return f.read()


def _read_component(name: str) -> str:
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "foldrun_app",
        "models",
        "af2",
        "pipeline",
        "components",
        f"{name}.py",
    )
    with open(os.path.normpath(path)) as f:
        return f.read()


class TestAF2PipelineSource:
    """Inspect AF2 pipeline source for structural correctness."""

    def test_pipeline_has_configure_step(self):
        assert "ConfigureRunOp" in _read_pipeline_source()

    def test_pipeline_has_data_pipeline_step(self):
        assert "data_pipeline" in _read_pipeline_source()

    def test_pipeline_has_predict_step(self):
        src = _read_pipeline_source()
        assert "PredictOp" in src or "predict" in src.lower()

    def test_pipeline_has_relax_step(self):
        """AF2 has AMBER relaxation — unlike OF3 and Boltz-2."""
        src = _read_pipeline_source()
        assert "RelaxOp" in src or "relax" in src.lower()

    def test_pipeline_uses_parallel_for(self):
        """Predict/relax tasks use ParallelFor for concurrent model seeds."""
        assert "dsl.ParallelFor" in _read_pipeline_source()

    def test_pipeline_has_retry_policies(self):
        src = _read_pipeline_source()
        assert src.count(".set_retry(") >= 2

    def test_pipeline_nfs_mounts(self):
        """AF2 uses NFS mounts for the data pipeline step.
        Predict/relax use custom training jobs which handle NFS differently."""
        assert _read_pipeline_source().count("nfs_mounts=") >= 1

    def test_pipeline_accepts_model_preset(self):
        """Pipeline accepts model_preset parameter (monomer/multimer)."""
        assert "model_preset" in _read_pipeline_source()

    def test_pipeline_accepts_is_run_relax(self):
        """Pipeline accepts is_run_relax parameter to toggle AMBER."""
        assert "is_run_relax" in _read_pipeline_source()

    def test_pipeline_accepts_use_small_bfd(self):
        """Pipeline accepts use_small_bfd for database selection."""
        assert "use_small_bfd" in _read_pipeline_source()

    def test_pipeline_flex_start_on_gpu_tasks(self):
        """FLEX_START strategy is applied to GPU tasks (predict/relax), not CPU data pipeline."""
        src = _read_pipeline_source()
        assert "strategy" in src
        assert '"STANDARD"' in src  # CPU data pipeline always STANDARD

    def test_mmseqs2_path_in_pipeline(self):
        """Pipeline has a code path for mmseqs2 GPU-accelerated MSA."""
        src = _read_pipeline_source()
        assert "mmseqs2" in src

    def test_pipeline_parallelism_limit(self):
        """Pipeline applies a parallelism limit to prevent GPU over-scheduling."""
        src = _read_pipeline_source()
        assert "PARALLELISM" in src or "parallelism" in src.lower()


class TestAF2ComponentSources:
    """Verify key component CLI args and structure."""

    def test_predict_relax_uses_alphafold_utils(self):
        """predict_relax delegates to the alphafold_utils module."""
        src = _read_component("predict_relax")
        assert "alphafold_utils" in src
        assert "predict_relax" in src

    def test_predict_relax_handles_run_relax_flag(self):
        """predict_relax respects the is_run_relax parameter."""
        src = _read_component("predict_relax")
        assert "is_run_relax" in src
        assert 'run_relax=(is_run_relax == "relax")' in src

    def test_predict_relax_outputs_three_artifacts(self):
        """predict_relax produces raw_predictions, unrelaxed_proteins, relaxed_proteins."""
        src = _read_component("predict_relax")
        assert "raw_predictions" in src
        assert "unrelaxed_proteins" in src
        assert "relaxed_proteins" in src

    def test_predict_relax_sets_memory_env_vars(self):
        """predict_relax sets TF and XLA memory flags for GPU."""
        src = _read_component("predict_relax")
        assert "TF_FORCE_UNIFIED_MEMORY" in src
        assert "XLA_PYTHON_CLIENT_MEM_FRACTION" in src

    def test_data_pipeline_uses_jackhmmer(self):
        """data_pipeline component uses Jackhmmer for protein MSA."""
        src = _read_component("data_pipeline")
        assert "jackhmmer" in src.lower()

    def test_data_pipeline_uses_hhblits(self):
        """data_pipeline uses HHblits for BFD search."""
        src = _read_component("data_pipeline")
        assert "hhblits" in src.lower() or "hhsearch" in src.lower() or "bfd" in src.lower()

    def test_configure_run_sets_model_preset(self):
        """configure_run uses model_preset to select monomer vs multimer models."""
        src = _read_component("configure_run")
        assert "model_preset" in src
        assert "monomer" in src
        assert "multimer" in src

    def test_version_component_exists(self):
        """version.py component tracks AF2 component image version."""
        src = _read_component("version")
        assert len(src) > 0
