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

"""Tests for OF3 pipeline: source inspection, ParallelFor over seeds, no relax."""

import os


class TestOF3PipelineSource:
    """Inspect OF3 pipeline source for correctness."""

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
            "of3",
            "pipeline",
            "pipelines",
            "of3_inference_pipeline.py",
        )
        with open(pipeline_path) as f:
            return f.read()

    def test_pipeline_has_three_steps(self):
        """Pipeline has configure_seeds, MSA, and predict steps."""
        source = self._read_pipeline_source()
        assert "ConfigureSeedsOF3" in source
        assert "MSAPipelineOF3" in source
        assert "PredictOF3" in source

    def test_no_relax_step(self):
        """OF3 pipeline should NOT have a relax step."""
        source = self._read_pipeline_source()
        assert "relax_protein" not in source
        assert "JobRelaxOp" not in source

    def test_parallel_for_over_seeds(self):
        """OF3 uses ParallelFor over seed configs."""
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

    def test_passes_nfs_params_path(self):
        """Pipeline passes nfs_params_path to predict step."""
        source = self._read_pipeline_source()
        assert "nfs_params_path=nfs_params_path" in source

    def test_pipeline_has_use_templates_parameter(self):
        """Pipeline has use_templates parameter (default True)."""
        source = self._read_pipeline_source()
        assert "use_templates: bool = True" in source

    def test_pipeline_passes_use_templates_to_msa(self):
        """Pipeline passes use_templates to MSA step."""
        source = self._read_pipeline_source()
        assert "use_templates=use_templates" in source

    def test_pipeline_passes_nfs_mmcif_dir_to_predict(self):
        """Pipeline passes nfs_mmcif_dir to predict step for template structures."""
        source = self._read_pipeline_source()
        assert "nfs_mmcif_dir" in source
        assert "PDB_MMCIF_PATH" in source


class TestOF3MSAPipelineSource:
    """Inspect MSA pipeline source for correctness."""

    @staticmethod
    def _read_msa_source():
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
            "msa_pipeline.py",
        )
        with open(path) as f:
            return f.read()

    def test_msa_iterates_queries_chains_format(self):
        """MSA pipeline iterates over queries.*.chains (not old sequences list)."""
        source = self._read_msa_source()
        assert 'query_data.get("queries", {}).items()' in source
        assert '.get("chains", [])' in source
        # Should NOT use old sequences format
        assert 'query_data.get("sequences", [])' not in source

    def test_msa_writes_to_nfs(self):
        """MSA pipeline writes MSA files to NFS-shared directory."""
        source = self._read_msa_source()
        assert "nfs_msa_base" in source
        assert "of3_msas" in source
        assert "uuid" in source

    def test_msa_runs_jackhmmer_uniref90_and_mgnify(self):
        """MSA pipeline runs jackhmmer against uniref90 and mgnify with OF3 expected names."""
        source = self._read_msa_source()
        # Files must use "_hits" suffix — OF3's MSASettings.max_seq_counts filters by basename
        assert "uniref90_hits.sto" in source
        assert "mgnify_hits.sto" in source

    def test_msa_template_search_against_pdb_seqres(self):
        """MSA pipeline runs jackhmmer against pdb_seqres for template alignment."""
        source = self._read_msa_source()
        assert "pdb_seqres.sto" in source
        assert "pdb_seqres_path" in source
        assert "template_alignment_file_path" in source

    def test_msa_template_search_guarded_by_use_templates(self):
        """Template search is only done when use_templates=True."""
        source = self._read_msa_source()
        assert "if use_templates" in source

    def test_msa_injects_main_msa_file_paths_per_chain(self):
        """MSA pipeline injects main_msa_file_paths into each chain dict."""
        source = self._read_msa_source()
        assert 'chain["main_msa_file_paths"]' in source

    def test_msa_injects_template_path_per_chain(self):
        """MSA pipeline injects template_alignment_file_path into each chain dict."""
        source = self._read_msa_source()
        assert 'chain["template_alignment_file_path"]' in source

    def test_msa_handles_rna_chains(self):
        """MSA pipeline handles RNA chains with nhmmer."""
        source = self._read_msa_source()
        assert "nhmmer" in source
        assert "rfam" in source
        assert "rnacentral" in source

    def test_msa_pdb_seqres_existence_check(self):
        """Template search skips gracefully if pdb_seqres not present on NFS."""
        source = self._read_msa_source()
        assert "os.path.exists(pdb_seqres_path)" in source


class TestOF3PredictTemplateArgs:
    """Verify predict component template handling."""

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

    def test_predict_has_use_templates_parameter(self):
        """Predict component has use_templates parameter."""
        source = self._read_predict_source()
        assert "use_templates: bool = True" in source

    def test_predict_has_nfs_mmcif_dir_parameter(self):
        """Predict component has nfs_mmcif_dir parameter for template structures."""
        source = self._read_predict_source()
        assert "nfs_mmcif_dir: str" in source

    def test_predict_writes_runner_yaml_for_templates(self):
        """Predict writes runner YAML with template_preprocessor_settings."""
        source = self._read_predict_source()
        assert "runner.yaml" in source
        assert "template_preprocessor_settings" in source

    def test_predict_sets_structure_directory_in_runner_yaml(self):
        """Runner YAML sets structure_directory to nfs_mmcif_dir."""
        source = self._read_predict_source()
        assert "structure_directory" in source
        assert "nfs_mmcif_dir" in source

    def test_predict_disables_fetch_missing_in_runner_yaml(self):
        """Runner YAML disables fetch_missing_structures to stay VPC-isolated."""
        source = self._read_predict_source()
        assert "fetch_missing_structures" in source
        assert "False" in source

    def test_predict_passes_runner_yaml_to_run_openfold(self):
        """Predict passes --runner_yaml flag to run_openfold when templates enabled."""
        source = self._read_predict_source()
        assert "--runner_yaml=" in source
