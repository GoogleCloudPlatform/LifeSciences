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

    def test_pipeline_passes_use_cueq_triangle_kernels(self):
        """Pipeline passes use_cueq_triangle_kernels to predict step."""
        source = self._read_pipeline_source()
        assert "use_cueq_triangle_kernels: bool = True" in source
        assert "use_cueq_triangle_kernels=use_cueq_triangle_kernels" in source


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
        assert "nfs_msa_cache_base" in source
        assert "of3_msas_cache" in source
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
        """Predict passes --runner_yaml flag to run_openfold."""
        source = self._read_predict_source()
        assert "--runner_yaml=" in source

    def test_predict_disables_msa_output_copies_in_runner_yaml(self):
        """Runner YAML disables per-seed MSA output writes to GCS artifact dir."""
        source = self._read_predict_source()
        assert "msa_computation_settings" in source
        assert "save_openfold_outputs" in source
        assert "save_colabfold_outputs" in source
        assert "save_mappings" in source

    def test_predict_has_use_cueq_triangle_kernels_parameter(self):
        """Predict component has use_cueq_triangle_kernels parameter defaulting to True."""
        source = self._read_predict_source()
        assert "use_cueq_triangle_kernels: bool = True" in source

    def test_predict_configures_cueq_triangle_kernels_in_runner_yaml(self):
        """Predict writes model_update with cuEquivariance triangle kernels when enabled."""
        source = self._read_predict_source()
        assert "if use_cueq_triangle_kernels:" in source
        assert "model_update" in source
        assert "use_cueq_triangle_kernels" in source
        assert "use_deepspeed_evo_attention" in source

    def test_predict_rejects_legacy_checkpoints(self, tmp_path, monkeypatch):
        """predict_of3 raises ValueError when given a deprecated pre-OpenBind checkpoint."""
        import sys
        from types import SimpleNamespace

        import pytest

        from foldrun_app.models.of3.pipeline import config as of3_config

        monkeypatch.setitem(sys.modules, "config", of3_config)
        from foldrun_app.models.of3.pipeline.components.predict import predict_of3

        with pytest.raises(ValueError, match="deprecated checkpoint"):
            predict_of3.python_func(
                updated_query_json=SimpleNamespace(path=str(tmp_path / "q.json")),
                seed_value=42,
                num_diffusion_samples=1,
                nfs_params_path="/mnt/nfs/foldrun/of3/params/of3-p2-155k.pt",
                predicted_structure=SimpleNamespace(
                    path=str(tmp_path / "p"), uri="gs://b/p", metadata={}
                ),
                confidence_json=SimpleNamespace(
                    path=str(tmp_path / "c"), uri="gs://b/c", metadata={}
                ),
            )

    def test_predict_selects_matching_cif_for_winning_confidence_sample(
        self, tmp_path, monkeypatch
    ):
        """Verify predict_of3 selects the CIF matching the winning confidence sample, not the first CIF in directory."""
        import json
        import subprocess
        import sys
        from types import SimpleNamespace

        import yaml

        from foldrun_app.models.of3.pipeline import config as of3_config

        monkeypatch.setitem(sys.modules, "config", of3_config)
        from foldrun_app.models.of3.pipeline.components.predict import predict_of3

        query_json_path = tmp_path / "query.json"
        query_json_path.write_text(
            json.dumps({"queries": {"test_query": {"chains": [{"sequence": "ACDE"}]}}})
        )

        output_dir = tmp_path / "outputs"
        output_dir.mkdir()
        predicted_structure_path = output_dir / "predicted_structure"
        confidence_json_path = output_dir / "confidence_json"

        seed_dir = output_dir / "test_query" / "seed_42"
        seed_dir.mkdir(parents=True)

        # Sample 0 has lower score
        (seed_dir / "test_query_seed_42_sample_0_confidences_aggregated.json").write_text(
            json.dumps({"ptm": 0.45, "sample_ranking_score": 0.45})
        )
        (seed_dir / "test_query_seed_42_sample_0_model.cif").write_text("SAMPLE_0_CIF_CONTENT")

        # Sample 1 has higher score
        (seed_dir / "test_query_seed_42_sample_1_confidences_aggregated.json").write_text(
            json.dumps({"ptm": 0.92, "sample_ranking_score": 0.92})
        )
        (seed_dir / "test_query_seed_42_sample_1_model.cif").write_text("SAMPLE_1_CIF_CONTENT")

        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)

        orig_listdir = os.listdir

        def sorted_listdir(path):
            return sorted(orig_listdir(path))

        monkeypatch.setattr(os, "listdir", sorted_listdir)

        updated_query_json = SimpleNamespace(path=str(query_json_path))
        predicted_structure = SimpleNamespace(
            path=str(predicted_structure_path), uri="gs://bucket/pred", metadata={}
        )
        confidence_json = SimpleNamespace(
            path=str(confidence_json_path), uri="gs://bucket/conf", metadata={}
        )

        predict_of3.python_func(
            updated_query_json=updated_query_json,
            seed_value=42,
            num_diffusion_samples=2,
            nfs_params_path="/nfs/params/of3-ob-2025-06-30-174k.pt",
            predicted_structure=predicted_structure,
            confidence_json=confidence_json,
            use_templates=False,
        )

        selected_conf = json.loads(confidence_json_path.read_text())
        selected_cif = predicted_structure_path.read_text()
        runner_cfg = yaml.safe_load((output_dir / "runner.yaml").read_text())

        assert selected_conf["ptm"] == 0.92
        assert selected_cif == "SAMPLE_1_CIF_CONTENT"
        assert runner_cfg["msa_computation_settings"]["save_openfold_outputs"] is False
        assert predicted_structure.metadata["checkpoint"] == "of3-ob-2025-06-30-174k.pt"
        assert confidence_json.metadata["checkpoint"] == "of3-ob-2025-06-30-174k.pt"


class TestOF3MSAPipelineCacheBehavior:
    """Validate MSA cache lookup, file path list injection, and cache promotion merging."""

    @staticmethod
    def _get_msa_func():
        import sys

        from foldrun_app.models.of3.pipeline import config as of3_config

        sys.modules["config"] = of3_config
        from foldrun_app.models.of3.pipeline.components.msa_pipeline import msa_pipeline_of3

        return msa_pipeline_of3.python_func

    @staticmethod
    def _make_artifact(path: str, uri: str = "", metadata: dict | None = None):
        class _DummyArtifact:
            def __init__(self, p, u, m):
                self.path = p
                self.uri = u
                self.metadata = m if m is not None else {}

        return _DummyArtifact(path, uri, metadata)

    def test_main_msa_file_paths_is_list_on_cache_miss_and_hit(self, tmp_path, monkeypatch):
        """Finding 4.33: chain['main_msa_file_paths'] must be a list of file paths, not a directory string."""
        import json
        import subprocess

        msa_func = self._get_msa_func()

        nfs_mount = tmp_path / "nfs"
        nfs_mount.mkdir()
        for db_name in ("uniref90.fasta", "mgnify.fasta", "pdb_seqres.fasta"):
            (nfs_mount / db_name).write_text(">dummy\nACDEFGHIK\n")

        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90.fasta",
                "mgnify": "mgnify.fasta",
                "pdb_seqres": "pdb_seqres.fasta",
            },
        )

        query_input = tmp_path / "query.json"
        query_input.write_text(
            json.dumps(
                {
                    "queries": {
                        "test_q": {
                            "chains": [
                                {
                                    "molecule_type": "protein",
                                    "chain_ids": ["A"],
                                    "sequence": "ACDEFGHIK",
                                }
                            ]
                        }
                    }
                }
            )
        )
        query_artifact = self._make_artifact(path=str(query_input), uri=str(query_input))
        out_miss_path = tmp_path / "out_miss.json"
        out_miss_artifact = self._make_artifact(path=str(out_miss_path), uri=str(out_miss_path))

        def _fake_run(cmd, check=True):
            out_idx = cmd.index("-A") + 1
            with open(cmd[out_idx], "w") as f:
                f.write("# STOCKHOLM 1.0\n//\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # First run: cache miss
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_miss_artifact,
            use_templates=True,
        )
        miss_result = json.loads(out_miss_path.read_text())
        miss_chain = miss_result["queries"]["test_q"]["chains"][0]
        assert isinstance(miss_chain["main_msa_file_paths"], list), (
            f"Expected list of file paths on cache miss, got {type(miss_chain['main_msa_file_paths'])}: "
            f"{miss_chain['main_msa_file_paths']}"
        )
        assert all(os.path.isfile(p) for p in miss_chain["main_msa_file_paths"])

        # Second run: cache hit
        out_hit_path = tmp_path / "out_hit.json"
        out_hit_artifact = self._make_artifact(path=str(out_hit_path), uri=str(out_hit_path))
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_hit_artifact,
            use_templates=True,
        )
        hit_result = json.loads(out_hit_path.read_text())
        hit_chain = hit_result["queries"]["test_q"]["chains"][0]
        assert isinstance(hit_chain["main_msa_file_paths"], list), (
            f"Expected list of file paths on cache hit, got {type(hit_chain['main_msa_file_paths'])}: "
            f"{hit_chain['main_msa_file_paths']}"
        )
        assert all(os.path.isfile(p) for p in hit_chain["main_msa_file_paths"])

    def test_cache_promotion_merges_template_alignment_when_cache_dir_exists(
        self, tmp_path, monkeypatch
    ):
        """Finding 4.34: When seq_cache_dir already exists (e.g. from use_templates=False),
        a subsequent run with use_templates=True must merge pdb_seqres.sto into seq_cache_dir
        rather than deleting it when os.rename fails on non-empty directory.
        """
        import json
        import subprocess

        msa_func = self._get_msa_func()

        nfs_mount = tmp_path / "nfs"
        nfs_mount.mkdir()
        for db_name in ("uniref90.fasta", "mgnify.fasta", "pdb_seqres.fasta"):
            (nfs_mount / db_name).write_text(">dummy\nACDEFGHIK\n")

        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90.fasta",
                "mgnify": "mgnify.fasta",
                "pdb_seqres": "pdb_seqres.fasta",
            },
        )

        query_input = tmp_path / "query.json"
        query_input.write_text(
            json.dumps(
                {
                    "queries": {
                        "test_q": {
                            "chains": [
                                {
                                    "molecule_type": "protein",
                                    "chain_ids": ["A"],
                                    "sequence": "ACDEFGHIK",
                                }
                            ]
                        }
                    }
                }
            )
        )
        query_artifact = self._make_artifact(path=str(query_input), uri=str(query_input))

        def _fake_run(cmd, check=True):
            out_idx = cmd.index("-A") + 1
            with open(cmd[out_idx], "w") as f:
                f.write("# STOCKHOLM 1.0\n//\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # First run: use_templates=False populates seq_cache_dir without pdb_seqres.sto
        out_no_tpl_path = tmp_path / "out_no_tpl.json"
        out_no_tpl_artifact = self._make_artifact(
            path=str(out_no_tpl_path), uri=str(out_no_tpl_path)
        )
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_no_tpl_artifact,
            use_templates=False,
        )

        # Second run: use_templates=True triggers cache miss and generates pdb_seqres.sto
        out_with_tpl_path = tmp_path / "out_with_tpl.json"
        out_with_tpl_artifact = self._make_artifact(
            path=str(out_with_tpl_path), uri=str(out_with_tpl_path)
        )
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_with_tpl_artifact,
            use_templates=True,
        )

        tpl_result = json.loads(out_with_tpl_path.read_text())
        tpl_chain = tpl_result["queries"]["test_q"]["chains"][0]
        assert "template_alignment_file_path" in tpl_chain
        assert os.path.isfile(tpl_chain["template_alignment_file_path"]), (
            f"Template alignment file was destroyed during cache promotion: "
            f"{tpl_chain['template_alignment_file_path']}"
        )

    def test_rna_msa_executes_nhmmer_against_default_databases_yaml_paths(
        self, tmp_path, monkeypatch
    ):
        """Verify RNA chains run nhmmer against rfam/rfam.fasta and rnacentral/rnacentral.fasta,
        properly handle empty cache directories when DBs are absent, and hit cache on subsequent runs.
        """
        import hashlib
        import importlib
        import json
        import subprocess

        import foldrun_app.models.of3.pipeline.config as pipeline_config

        for var in ("OF3_RFAM_PATH", "RFAM_PATH", "OF3_RNACENTRAL_PATH", "RNACENTRAL_PATH"):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(pipeline_config)

        msa_func = self._get_msa_func()

        nfs_mount = tmp_path / "nfs"
        (nfs_mount / "rfam").mkdir(parents=True)
        (nfs_mount / "rnacentral").mkdir(parents=True)
        for db_name in ("uniref90.fasta", "mgnify.fasta", "pdb_seqres.fasta"):
            (nfs_mount / db_name).write_text(">dummy\nACDEFGHIK\n")

        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90.fasta",
                "mgnify": "mgnify.fasta",
                "pdb_seqres": "pdb_seqres.fasta",
                "rfam": pipeline_config.RFAM_PATH,
                "rnacentral": pipeline_config.RNACENTRAL_PATH,
            },
        )

        seq = "ACGUACGUACGU"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()
        cache_dir = nfs_mount / "of3_msas_cache" / f"rna_{seq_hash}"
        cache_dir.mkdir(parents=True)

        query_input = tmp_path / "rna_query.json"
        query_input.write_text(
            json.dumps(
                {
                    "queries": {
                        "rna_q": {
                            "chains": [
                                {
                                    "molecule_type": "rna",
                                    "chain_ids": ["R"],
                                    "sequence": seq,
                                }
                            ]
                        }
                    }
                }
            )
        )
        query_artifact = self._make_artifact(path=str(query_input), uri=str(query_input))

        called_cmds = []

        def _fake_run(cmd, check=True):
            called_cmds.append(cmd)
            out_idx = cmd.index("-A") + 1
            with open(cmd[out_idx], "w") as f:
                f.write("# STOCKHOLM 1.0\n//\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Step 1: Pre-created empty cache dir while RNA FASTA files are absent -> no nhmmer calls
        out_empty_path = tmp_path / "out_empty.json"
        out_empty_artifact = self._make_artifact(path=str(out_empty_path), uri=str(out_empty_path))
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_empty_artifact,
            use_templates=False,
        )

        assert len(called_cmds) == 0
        assert out_empty_artifact.metadata["msa_chains"] == 0
        assert list(cache_dir.iterdir()) == []
        empty_res = json.loads(out_empty_path.read_text())
        empty_rna_chain = empty_res["queries"]["rna_q"]["chains"][0]
        assert "main_msa_file_paths" not in empty_rna_chain

        # Step 2: Create rfam.fasta and rnacentral.fasta -> runs nhmmer and populates cache
        (nfs_mount / "rfam" / "rfam.fasta").write_text(">rfam1\nACGUACGU\n")
        (nfs_mount / "rnacentral" / "rnacentral.fasta").write_text(">rnac1\nACGUACGU\n")

        out_rna_path = tmp_path / "out_rna.json"
        out_rna_artifact = self._make_artifact(path=str(out_rna_path), uri=str(out_rna_path))

        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_rna_artifact,
            use_templates=False,
        )

        assert len(called_cmds) == 2
        assert called_cmds[0][0] == "nhmmer"
        assert str(nfs_mount / "rfam" / "rfam.fasta") in called_cmds[0]
        assert str(nfs_mount / "rnacentral" / "rnacentral.fasta") in called_cmds[1]
        assert out_rna_artifact.metadata["msa_chains"] == 1

        rna_result = json.loads(out_rna_path.read_text())
        rna_chain = rna_result["queries"]["rna_q"]["chains"][0]
        assert len(rna_chain["main_msa_file_paths"]) == 2
        assert all(os.path.isfile(p) for p in rna_chain["main_msa_file_paths"])
        assert any(p.endswith("rfam_hits.sto") for p in rna_chain["main_msa_file_paths"])
        assert any(p.endswith("rnacentral_hits.sto") for p in rna_chain["main_msa_file_paths"])

        # Step 3: Re-run to verify cache hit
        out_hit_path = tmp_path / "out_hit.json"
        out_hit_artifact = self._make_artifact(path=str(out_hit_path), uri=str(out_hit_path))
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_hit_artifact,
            use_templates=False,
        )

        assert len(called_cmds) == 2  # No new nhmmer calls
        assert out_hit_artifact.metadata["msa_chains"] == 1
        hit_result = json.loads(out_hit_path.read_text())
        hit_chain = hit_result["queries"]["rna_q"]["chains"][0]
        assert len(hit_chain["main_msa_file_paths"]) == 2

    def test_rna_msa_empty_metadata_paths_do_not_execute_against_mount_root(
        self, tmp_path, monkeypatch
    ):
        """Verify that passing empty string metadata for rfam/rnacentral does not target mount root."""
        import json
        import subprocess

        msa_func = self._get_msa_func()

        nfs_mount = tmp_path / "nfs"
        nfs_mount.mkdir(parents=True)
        for db_name in ("uniref90.fasta", "mgnify.fasta", "pdb_seqres.fasta"):
            (nfs_mount / db_name).write_text(">dummy\nACDEFGHIK\n")

        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90.fasta",
                "mgnify": "mgnify.fasta",
                "pdb_seqres": "pdb_seqres.fasta",
                "rfam": "",
                "rnacentral": "",
            },
        )

        query_input = tmp_path / "query_rna_empty.json"
        query_input.write_text(
            json.dumps(
                {
                    "queries": {
                        "rna_q": {
                            "chains": [
                                {
                                    "molecule_type": "rna",
                                    "chain_ids": ["R"],
                                    "sequence": "ACGUACGU",
                                }
                            ]
                        }
                    }
                }
            )
        )
        query_artifact = self._make_artifact(path=str(query_input), uri=str(query_input))
        out_path = tmp_path / "out_rna_empty.json"
        out_artifact = self._make_artifact(path=str(out_path), uri=str(out_path))

        invoked_cmds = []

        def _fake_run(cmd, check=True):
            invoked_cmds.append(cmd)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_artifact,
            use_templates=False,
        )

        assert len(invoked_cmds) == 0
        assert out_artifact.metadata["msa_chains"] == 0
        res = json.loads(out_path.read_text())
        chain = res["queries"]["rna_q"]["chains"][0]
        assert "main_msa_file_paths" not in chain

    def test_core_mode_missing_databases_writes_single_sequence_sto_and_upgrades_later(
        self, tmp_path, monkeypatch
    ):
        """When uniref90/mgnify are absent on NFS (core mode), OF3 writes a single-sequence Stockholm MSA
        and cleanly upgrades the cache entry if full databases are subsequently installed.
        """
        import hashlib
        import json
        import subprocess

        msa_func = self._get_msa_func()

        nfs_mount = tmp_path / "nfs"
        nfs_mount.mkdir()
        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90.fasta",
                "mgnify": "mgnify.fasta",
                "pdb_seqres": "pdb_seqres.fasta",
            },
        )

        seq = "ACDEFGHIKLMNPQ"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()
        query_input = tmp_path / "query_core.json"
        query_input.write_text(
            json.dumps(
                {
                    "queries": {
                        "core_q": {
                            "chains": [
                                {
                                    "molecule_type": "protein",
                                    "chain_ids": ["A"],
                                    "sequence": seq,
                                }
                            ]
                        }
                    }
                }
            )
        )
        query_artifact = self._make_artifact(path=str(query_input), uri=str(query_input))
        out_core_path = tmp_path / "out_core.json"
        out_core_artifact = self._make_artifact(path=str(out_core_path), uri=str(out_core_path))

        invoked_cmds = []

        def _fake_run(cmd, check=True):
            invoked_cmds.append(cmd)
            out_idx = cmd.index("-A") + 1
            with open(cmd[out_idx], "w") as f:
                f.write(f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\nhomolog_full {seq}\n//\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # 1. Core mode: no uniref90, mgnify, or pdb_seqres on NFS
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_core_artifact,
            use_templates=True,
        )

        assert invoked_cmds == [], "jackhmmer must not run in core mode when DBs are missing"
        core_result = json.loads(out_core_path.read_text())
        core_chain = core_result["queries"]["core_q"]["chains"][0]
        assert len(core_chain["main_msa_file_paths"]) == 1
        sto_path = core_chain["main_msa_file_paths"][0]
        assert sto_path.endswith("uniref90_hits.sto")
        assert os.path.isfile(sto_path)
        with open(sto_path) as f:
            sto_text = f.read()
        assert sto_text == f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\n//\n"
        assert "template_alignment_file_path" not in core_chain
        assert out_core_artifact.metadata["msa_chains"] == 1
        assert out_core_artifact.metadata["template_chains"] == 0

        # 1b. Repeat core-mode run for the same sequence hits cache cleanly without touching tmp
        out_core_hit_path = tmp_path / "out_core_hit.json"
        out_core_hit_artifact = self._make_artifact(
            path=str(out_core_hit_path), uri=str(out_core_hit_path)
        )
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_core_hit_artifact,
            use_templates=True,
        )
        assert invoked_cmds == []
        core_hit_result = json.loads(out_core_hit_path.read_text())
        core_hit_chain = core_hit_result["queries"]["core_q"]["chains"][0]
        assert core_hit_chain["main_msa_file_paths"] == [sto_path]
        assert list((nfs_mount / "of3_msas_tmp").iterdir()) == []

        # 2. Full databases later installed on NFS -> upgrades uniref90_hits.sto and adds mgnify_hits.sto
        for db_name in ("uniref90.fasta", "mgnify.fasta", "pdb_seqres.fasta"):
            (nfs_mount / db_name).write_text(f">dummy\n{seq}\n")

        out_full_path = tmp_path / "out_full.json"
        out_full_artifact = self._make_artifact(path=str(out_full_path), uri=str(out_full_path))
        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_full_artifact,
            use_templates=True,
        )

        assert len(invoked_cmds) == 3
        full_result = json.loads(out_full_path.read_text())
        full_chain = full_result["queries"]["core_q"]["chains"][0]
        assert len(full_chain["main_msa_file_paths"]) == 2
        with open(full_chain["main_msa_file_paths"][0]) as f:
            assert "homolog_full" in f.read()
        assert os.path.isfile(full_chain["template_alignment_file_path"])
        # Ensure no leftover .tmp files in seq_cache_dir after atomic os.replace
        cache_files = os.listdir(os.path.dirname(sto_path))
        assert not any(name.endswith(".tmp") for name in cache_files)

    def test_preserves_custom_msa_and_template_paths(self, tmp_path, monkeypatch):
        """Pre-populated main_msa_file_paths and template_alignment_file_path skip HMMER."""
        import json
        import subprocess

        msa_func = self._get_msa_func()

        nfs_mount = tmp_path / "nfs"
        nfs_mount.mkdir()
        for db_name in ("uniref90.fasta", "mgnify.fasta", "pdb_seqres.fasta"):
            (nfs_mount / db_name).write_text(">dummy\nACDEFGHIK\n")

        custom_msa = tmp_path / "custom_uniref90_hits.sto"
        custom_msa.write_text("# STOCKHOLM 1.0\ncustom ACDEFGHIK\n//\n")
        custom_tpl = tmp_path / "custom_templates.sto"
        custom_tpl.write_text("# STOCKHOLM 1.0\ntpl ACDEFGHIK\n//\n")

        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90.fasta",
                "mgnify": "mgnify.fasta",
                "pdb_seqres": "pdb_seqres.fasta",
            },
        )

        query_input = tmp_path / "query_custom.json"
        query_input.write_text(
            json.dumps(
                {
                    "queries": {
                        "custom_q": {
                            "chains": [
                                {
                                    "molecule_type": "protein",
                                    "chain_ids": ["A"],
                                    "sequence": "ACDEFGHIK",
                                    "main_msa_file_paths": [str(custom_msa)],
                                    "template_alignment_file_path": str(custom_tpl),
                                },
                                {
                                    "molecule_type": "protein",
                                    "chain_ids": ["B"],
                                    "sequence": "MNPQRSTVW",
                                    "main_msa_file_paths": [str(custom_msa)],
                                },
                            ]
                        }
                    }
                }
            )
        )
        query_artifact = self._make_artifact(path=str(query_input), uri=str(query_input))
        out_path = tmp_path / "out_custom.json"
        out_artifact = self._make_artifact(path=str(out_path), uri=str(out_path))

        invoked_cmds = []

        def _fake_run(cmd, check=True):
            invoked_cmds.append(cmd)
            out_idx = cmd.index("-A") + 1
            with open(cmd[out_idx], "w") as f:
                f.write("# STOCKHOLM 1.0\n//\n")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        msa_func(
            query_json=query_artifact,
            ref_databases=ref_db,
            updated_query_json=out_artifact,
            use_templates=True,
        )

        # Chain A had both custom MSA and custom template -> 0 jackhmmer calls.
        # Chain B had custom MSA only with use_templates=True -> only 1 jackhmmer call (for pdb_seqres).
        assert len(invoked_cmds) == 1
        assert invoked_cmds[0][-1] == str(nfs_mount / "pdb_seqres.fasta")

        result = json.loads(out_path.read_text())
        chain_a, chain_b = result["queries"]["custom_q"]["chains"]
        assert chain_a["main_msa_file_paths"] == [str(custom_msa)]
        assert chain_a["template_alignment_file_path"] == str(custom_tpl)
        assert chain_b["main_msa_file_paths"] == [str(custom_msa)]
        assert chain_b["template_alignment_file_path"].endswith("pdb_seqres.sto")
        assert out_artifact.metadata["msa_chains"] == 2
        assert out_artifact.metadata["template_chains"] == 2


class TestOF3SubmitPredictionToolCuEq:
    """Verify OF3SubmitPredictionTool handling of cuEquivariance triangle kernels."""

    @staticmethod
    def _run_tool_with_mock_job(args: dict, env_overrides: dict | None = None):
        import json
        from unittest.mock import MagicMock, patch

        from foldrun_app.models.of3.tools.submit_prediction import OF3SubmitPredictionTool

        mock_job = MagicMock()
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            tool = OF3SubmitPredictionTool(tool_config={"name": "of3_submit_prediction"})

        query_content = json.dumps(
            {
                "queries": {
                    "test_q": {
                        "chains": [
                            {
                                "molecule_type": "protein",
                                "chain_ids": ["A"],
                                "sequence": "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG",
                            }
                        ]
                    }
                }
            }
        )
        base_args = {"input": query_content, "job_name": "test_cueq"}
        base_args.update(args)

        env = env_overrides or {}
        with (
            patch.dict(os.environ, env),
            patch.object(tool, "_upload_to_gcs"),
            patch.object(tool, "_get_filestore_info", return_value=("10.0.0.1", "test-net")),
            patch.object(tool.storage_client, "bucket") as mock_bucket,
            patch(
                "foldrun_app.models.of3.tools.submit_prediction.vertex_ai.PipelineJob",
                return_value=mock_job,
            ) as mock_pipeline_job_cls,
        ):
            mock_blob = MagicMock()
            mock_blob.exists.return_value = True
            mock_bucket.return_value.blob.return_value = mock_blob

            result = tool.run(base_args)
            call_kwargs = mock_pipeline_job_cls.call_args[1]
            return result, call_kwargs

    def test_default_enables_cueq(self):
        """When use_cueq_triangle_kernels is omitted, it defaults to True."""
        result, call_kwargs = self._run_tool_with_mock_job({})
        assert result["status"] == "submitted"
        assert call_kwargs["parameter_values"]["use_cueq_triangle_kernels"] is True
        assert call_kwargs["labels"]["use_cueq"] == "true"

    def test_explicit_false_disables_cueq(self):
        """When use_cueq_triangle_kernels=False is passed, it is respected."""
        result, call_kwargs = self._run_tool_with_mock_job({"use_cueq_triangle_kernels": False})
        assert result["status"] == "submitted"
        assert call_kwargs["parameter_values"]["use_cueq_triangle_kernels"] is False
        assert call_kwargs["labels"]["use_cueq"] == "false"

    def test_env_var_override_disables_cueq(self):
        """When OF3_USE_CUEQ_TRIANGLE_KERNELS=false, default becomes False."""
        result, call_kwargs = self._run_tool_with_mock_job(
            {}, env_overrides={"OF3_USE_CUEQ_TRIANGLE_KERNELS": "false"}
        )
        assert result["status"] == "submitted"
        assert call_kwargs["parameter_values"]["use_cueq_triangle_kernels"] is False
        assert call_kwargs["labels"]["use_cueq"] == "false"
