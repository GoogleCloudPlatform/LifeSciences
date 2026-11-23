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


class TestDataPipelineNumAlignments:
    """Verify safe integer conversion of features_dict["num_alignments"] in multimer mode."""

    def _run_data_pipeline_with_num_alignments(self, tmp_path, num_alignments_val):
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch

        pipeline_dir = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "foldrun_app",
                "models",
                "af2",
                "pipeline",
            )
        )
        fake_alphafold_utils = ModuleType("alphafold_utils")

        def _fake_run_data_pipeline(**kwargs):
            with open(kwargs["features_output_path"], "wb") as f:
                f.write(b"fake_features")
            return {"num_alignments": num_alignments_val}, {"msa_count": 1}

        fake_alphafold_utils.run_data_pipeline = _fake_run_data_pipeline
        fake_alphafold_utils.run_mmseqs2_data_pipeline = _fake_run_data_pipeline

        seq_file = tmp_path / "seq.fasta"
        seq_file.write_text(">chainA\nACDEFG\n>chainB\nACDEFG\n")
        nfs_dir = tmp_path / "nfs"
        nfs_dir.mkdir()
        msas_dir = tmp_path / "msas"
        features_file = tmp_path / "features.pkl"

        sequence = SimpleNamespace(path=str(seq_file))
        ref_databases = SimpleNamespace(
            uri=str(nfs_dir),
            metadata={
                "uniref90": "uniref90",
                "mgnify": "mgnify",
                "uniref30": "uniref30",
                "bfd": "bfd",
                "small_bfd": "small_bfd",
                "uniprot": "uniprot",
                "pdb70": "pdb70",
                "pdb_obsolete": "pdb_obsolete",
                "pdb_seqres": "pdb_seqres",
                "pdb_mmcif": "pdb_mmcif",
            },
        )
        msas = SimpleNamespace(path=str(msas_dir), metadata={})
        features = SimpleNamespace(path=str(features_file), metadata={})

        env = {
            "GCP_PROJECT_ID": "test-project",
            "GCP_REGION": "us-central1",
            "GCS_BUCKET_NAME": "test-bucket",
            "FILESTORE_ID": "test-nfs",
            "ALPHAFOLD_COMPONENTS_IMAGE": "af2-image:latest",
            "BOLTZ2_COMPONENTS_IMAGE": "boltz2-image:stable",
            "OPENFOLD3_COMPONENTS_IMAGE": "of3-image:latest",
            "NFS_SERVER": "10.1.0.2",
            "NFS_PATH": "/datasets",
            "NFS_MOUNT_POINT": "/mnt/nfs/foldrun",
            "NETWORK": "projects/123/global/networks/test-net",
            "DATA_PIPELINE_MACHINE_TYPE": "n4-standard-8",
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
        with (
            patch.dict(os.environ, env, clear=False),
            patch.dict(sys.modules, {"alphafold_utils": fake_alphafold_utils}),
        ):
            if pipeline_dir not in sys.path:
                sys.path.insert(0, pipeline_dir)
            from foldrun_app.models.af2.pipeline.components.data_pipeline import data_pipeline

            data_pipeline.python_func(
                sequence=sequence,
                ref_databases=ref_databases,
                run_multimer_system=True,
                use_small_bfd=True,
                max_template_date="2022-01-01",
                msa_method="jackhmmer",
                msas=msas,
                features=features,
            )
        return features.metadata["final_dedup_msa_size"]

    def test_multimer_num_alignments_1d_numpy_array(self, tmp_path):
        import numpy as np

        arr = np.array([128, 128, 128], dtype=np.int32)
        result = self._run_data_pipeline_with_num_alignments(tmp_path, arr)
        assert result == 128
        assert isinstance(result, int)

    def test_multimer_num_alignments_scalar(self, tmp_path):
        result = self._run_data_pipeline_with_num_alignments(tmp_path, 256)
        assert result == 256
        assert isinstance(result, int)

    def test_single_sequence_fallback_skips_af2_features_cache_promotion(self, tmp_path):
        """Fallback runs set single_sequence_fallback=True and skip af2_features_cache promotion."""
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch

        pipeline_dir = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "foldrun_app",
                "models",
                "af2",
                "pipeline",
            )
        )
        fake_alphafold_utils = ModuleType("alphafold_utils")

        def _fake_fallback_pipeline(**kwargs):
            with open(kwargs["features_output_path"], "wb") as f:
                f.write(b"fallback_features")
            return (
                {"num_alignments": 1},
                {"msas/uniref90_hits.sto": 1, "single_sequence_fallback": 1},
            )

        fake_alphafold_utils.run_data_pipeline = _fake_fallback_pipeline
        fake_alphafold_utils.run_mmseqs2_data_pipeline = _fake_fallback_pipeline

        seq_file = tmp_path / "seq_fb.fasta"
        seq_file.write_text(">chainA\nACDEFG\n>chainB\nACDEFG\n")
        nfs_dir = tmp_path / "nfs_fb"
        nfs_dir.mkdir()
        msas_dir = tmp_path / "msas_fb"
        features_file = tmp_path / "features_fb.pkl"

        sequence = SimpleNamespace(path=str(seq_file))
        ref_databases = SimpleNamespace(
            uri=str(nfs_dir),
            metadata={
                "uniref90": "uniref90",
                "mgnify": "mgnify",
                "uniref30": "uniref30",
                "bfd": "bfd",
                "small_bfd": "small_bfd",
                "uniprot": "uniprot",
                "pdb70": "pdb70",
                "pdb_obsolete": "pdb_obsolete",
                "pdb_seqres": "pdb_seqres",
                "pdb_mmcif": "pdb_mmcif",
            },
        )
        msas = SimpleNamespace(path=str(msas_dir), metadata={})
        features = SimpleNamespace(path=str(features_file), metadata={})

        with patch.dict(sys.modules, {"alphafold_utils": fake_alphafold_utils}):
            if pipeline_dir not in sys.path:
                sys.path.insert(0, pipeline_dir)
            from foldrun_app.models.af2.pipeline.components.data_pipeline import data_pipeline

            data_pipeline.python_func(
                sequence=sequence,
                ref_databases=ref_databases,
                run_multimer_system=True,
                use_small_bfd=True,
                max_template_date="2022-01-01",
                msa_method="jackhmmer",
                msas=msas,
                features=features,
            )

        assert features.metadata["single_sequence_fallback"] is True
        assert "single_sequence_fallback" not in msas.metadata
        assert msas.metadata == {"msas/uniref90_hits.sto": 1}
        assert list((nfs_dir / "af2_features_cache").iterdir()) == []


class TestAF2PerformanceOptimizations:
    """Tests for decoupled configure_run, normalized feature cache, and inline predict+relax."""

    def test_configure_run_uses_slim_python_image_and_no_pip_packages(self):
        src = _read_component("configure_run")
        assert 'base_image="python:3.13-slim"' in src
        assert "packages_to_install" not in src
        assert "google.cloud" not in src
        assert "from alphafold" not in src

    def test_af2_image_components_skip_runtime_kfp_install(self):
        for comp in (
            "data_pipeline",
            "model_predict",
            "relax_protein",
            "predict_relax",
            "aggregate_features",
            "hhblits",
            "hhsearch",
            "hmmsearch",
            "jackhmmer",
        ):
            src = _read_component(comp)
            assert "install_kfp_package=False" in src, (
                f"{comp}.py missing install_kfp_package=False"
            )

    def test_configure_run_python_func_monomer_and_multimer(self, tmp_path):
        from types import SimpleNamespace

        import pytest

        from foldrun_app.models.af2.pipeline.components.configure_run import configure_run

        mono_fasta = tmp_path / "mono.fasta"
        mono_fasta.write_text(">crambin\nTTCCPSIVARSNFNVCRLPGTPEAICATYTGCIIIPGATCPGDYAN\n")
        out_mono = configure_run.python_func(
            sequence=SimpleNamespace(path=str(mono_fasta)),
            model_preset="monomer",
            random_seed=100,
        )
        assert out_mono.run_multimer_system is False
        assert out_mono.num_ensemble == 1
        assert [r["model_name"] for r in out_mono.model_runners] == [
            "model_1",
            "model_2",
            "model_3",
            "model_4",
            "model_5",
        ]
        assert [r["random_seed"] for r in out_mono.model_runners] == [100, 101, 102, 103, 104]

        multi_fasta = tmp_path / "multi.fasta"
        multi_fasta.write_text(">A\nACDEFGHIKL\n>B\nMNPQRSTVWY\n")
        out_multi = configure_run.python_func(
            sequence=SimpleNamespace(path=str(multi_fasta)),
            model_preset="multimer",
            num_multimer_predictions_per_model=2,
        )
        assert out_multi.run_multimer_system is True
        assert len(out_multi.model_runners) == 10
        assert len({r["random_seed"] for r in out_multi.model_runners}) == 10
        assert all(r["model_name"].endswith("_multimer_v3") for r in out_multi.model_runners)

        with pytest.raises(ValueError, match="More than one sequence"):
            configure_run.python_func(
                sequence=SimpleNamespace(path=str(multi_fasta)),
                model_preset="monomer",
            )

    def test_data_pipeline_normalizes_feature_cache_key_across_headers_and_passes_shared_cache(
        self, tmp_path
    ):
        """Identical sequences with different FASTA headers/casing hit af2_features_cache."""
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch

        pipeline_dir = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "foldrun_app",
                "models",
                "af2",
                "pipeline",
            )
        )
        calls: list[dict] = []
        fake_alphafold_utils = ModuleType("alphafold_utils")

        def _fake_run_data_pipeline(**kwargs):
            calls.append(kwargs)
            with open(kwargs["features_output_path"], "wb") as f:
                f.write(b"cached_features_payload")
            import numpy as np

            return (
                {
                    "num_alignments": np.array([42], dtype=np.int32),
                    "template_domain_names": np.array([b"1crn_A"], dtype=object),
                },
                {"msas/uniref90_hits.sto": 42},
            )

        fake_alphafold_utils.run_data_pipeline = _fake_run_data_pipeline
        fake_alphafold_utils.run_mmseqs2_data_pipeline = _fake_run_data_pipeline

        nfs_dir = tmp_path / "nfs"
        nfs_dir.mkdir()
        ref_databases = SimpleNamespace(
            uri=str(nfs_dir),
            metadata={
                "uniref90": "uniref90",
                "mgnify": "mgnify",
                "uniref30": "uniref30",
                "bfd": "bfd",
                "small_bfd": "small_bfd",
                "uniprot": "uniprot",
                "pdb70": "pdb70",
                "pdb_obsolete": "pdb_obsolete",
                "pdb_seqres": "pdb_seqres",
                "pdb_mmcif": "pdb_mmcif",
            },
        )

        seq1 = tmp_path / "seq1.fasta"
        seq1.write_text(">sequence\nTTCCPSIVARSNFNVCRLPGTPEAICATYTGCIIIPGATCPGDYAN\n")
        seq2 = tmp_path / "seq2.fasta"
        seq2.write_text(">crambin\nttccpsivarsnfnvcrlpgtpeaicatytgciiipgatcpgdyan\n")

        with patch.dict(sys.modules, {"alphafold_utils": fake_alphafold_utils}):
            if pipeline_dir not in sys.path:
                sys.path.insert(0, pipeline_dir)
            from foldrun_app.models.af2.pipeline.components.data_pipeline import data_pipeline

            feat1 = SimpleNamespace(path=str(tmp_path / "feat1.pkl"), metadata={})
            msas1 = SimpleNamespace(path=str(tmp_path / "msas1"), metadata={})
            data_pipeline.python_func(
                sequence=SimpleNamespace(path=str(seq1)),
                ref_databases=ref_databases,
                use_small_bfd=True,
                max_template_date="2025-04-01",
                msa_method="jackhmmer",
                msas=msas1,
                features=feat1,
                model_preset="monomer",
            )
            assert len(calls) == 1
            assert calls[0]["shared_msa_cache_dir"] == os.path.join(
                str(nfs_dir), "shared_msas_cache"
            )
            assert calls[0]["shared_msa_tmp_dir"] == os.path.join(str(nfs_dir), "shared_msas_tmp")
            assert calls[0]["run_multimer_system"] is False

            # Second call with different header and lowercase sequence -> cache HIT (0 calls)
            feat2 = SimpleNamespace(path=str(tmp_path / "feat2.pkl"), metadata={})
            msas2 = SimpleNamespace(path=str(tmp_path / "msas2"), metadata={})
            data_pipeline.python_func(
                sequence=SimpleNamespace(path=str(seq2)),
                ref_databases=ref_databases,
                use_small_bfd=True,
                max_template_date="2025-04-01",
                msa_method="jackhmmer",
                msas=msas2,
                features=feat2,
                model_preset="monomer",
            )
            assert len(calls) == 1
            assert (tmp_path / "feat2.pkl").read_bytes() == b"cached_features_payload"
            assert feat2.metadata["final_dedup_msa_size"] == 42

    def test_predict_inline_relax_skip_and_fallback(self, tmp_path):
        """predict runs subprocess-isolated inference + inline relax, handles skip & RELAX_FALLBACK."""
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch

        pipeline_dir = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "foldrun_app",
                "models",
                "af2",
                "pipeline",
            )
        )
        events: list[str] = []
        fake_alphafold_utils = ModuleType("alphafold_utils")

        def _fake_predict(**kwargs):
            events.append("direct_predict")
            with open(kwargs["raw_prediction_path"], "wb") as f:
                f.write(b"raw")
            with open(kwargs["unrelaxed_protein_path"], "w") as f:
                f.write("UNRELAXED_PDB\n")
            return {"ranking_confidence": 92.3}

        def _fake_predict_subproc(**kwargs):
            events.append("subproc_predict")
            with open(kwargs["raw_prediction_path"], "wb") as f:
                f.write(b"raw")
            with open(kwargs["unrelaxed_protein_path"], "w") as f:
                f.write("UNRELAXED_PDB\n")
            return {"ranking_confidence": 92.3}

        def _fake_relax(**kwargs):
            events.append("relax")
            with open(kwargs["relaxed_protein_path"], "w") as f:
                f.write("RELAXED_PDB\n")

        fake_alphafold_utils.predict = _fake_predict
        fake_alphafold_utils.predict_in_subprocess = _fake_predict_subproc
        fake_alphafold_utils.relax_protein = _fake_relax

        with patch.dict(sys.modules, {"alphafold_utils": fake_alphafold_utils}):
            if pipeline_dir not in sys.path:
                sys.path.insert(0, pipeline_dir)
            from foldrun_app.models.af2.pipeline.components.model_predict import predict

            def _make_arts(prefix: str):
                raw_p = tmp_path / f"{prefix}_raw"
                unrel_p = tmp_path / f"{prefix}_unrel"
                rel_p = tmp_path / f"{prefix}_rel"
                return (
                    SimpleNamespace(path=str(raw_p), uri=str(raw_p), metadata={}),
                    SimpleNamespace(path=str(unrel_p), uri=str(unrel_p), metadata={}),
                    SimpleNamespace(path=str(rel_p), uri=str(rel_p), metadata={}),
                )

            # 1. Inline relax enabled
            raw1, unrel1, rel1 = _make_arts("ok")
            predict.python_func(
                model_features=SimpleNamespace(path="f.pkl"),
                model_params=SimpleNamespace(path="p"),
                model_name="model_1",
                prediction_index=0,
                num_ensemble=1,
                run_multimer_system=False,
                random_seed=42,
                tf_force_unified_memory="1",
                xla_python_client_mem_fraction="4.0",
                raw_prediction=raw1,
                unrelaxed_protein=unrel1,
                relaxed_protein=rel1,
                is_run_relax="relax",
            )
            assert events == ["subproc_predict", "relax"]
            assert rel1.metadata["relax_failed"] is False
            assert open(rel1.path).read() == "RELAXED_PDB\n"

            # 2. Inline relax skipped (is_run_relax="")
            events.clear()
            raw2, unrel2, rel2 = _make_arts("skip")
            predict.python_func(
                model_features=SimpleNamespace(path="f.pkl"),
                model_params=SimpleNamespace(path="p"),
                model_name="model_1",
                prediction_index=0,
                num_ensemble=1,
                run_multimer_system=False,
                random_seed=42,
                tf_force_unified_memory="1",
                xla_python_client_mem_fraction="4.0",
                raw_prediction=raw2,
                unrelaxed_protein=unrel2,
                relaxed_protein=rel2,
                is_run_relax="",
            )
            assert events == ["direct_predict"]
            assert rel2.metadata["relax_skipped"] is True
            assert not os.path.exists(rel2.path)

            # 3. RELAX_FALLBACK on ValueError copies unrelaxed -> relaxed and exits 0
            events.clear()
            fake_alphafold_utils.relax_protein = lambda **kwargs: (_ for _ in ()).throw(
                ValueError("Minimization failed after 100 attempts")
            )
            raw3, unrel3, rel3 = _make_arts("fallback")
            predict.python_func(
                model_features=SimpleNamespace(path="f.pkl"),
                model_params=SimpleNamespace(path="p"),
                model_name="model_1",
                prediction_index=0,
                num_ensemble=1,
                run_multimer_system=False,
                random_seed=42,
                tf_force_unified_memory="1",
                xla_python_client_mem_fraction="4.0",
                raw_prediction=raw3,
                unrelaxed_protein=unrel3,
                relaxed_protein=rel3,
                is_run_relax="relax",
            )
            assert events == ["subproc_predict"]
            assert rel3.metadata["relax_failed"] is True
            assert "Minimization failed" in rel3.metadata["relax_warning"]
            assert open(rel3.path).read() == "UNRELAXED_PDB\n"

            # 4. RELAX_FALLBACK catches openmm.OpenMMException (direct Exception subclass)
            class _FakeOpenMMException(Exception):
                pass

            fake_openmm = ModuleType("openmm")
            fake_openmm.OpenMMException = _FakeOpenMMException
            events.clear()
            fake_alphafold_utils.relax_protein = lambda **kwargs: (_ for _ in ()).throw(
                _FakeOpenMMException("CUDA platform init failed")
            )
            with patch.dict(sys.modules, {"openmm": fake_openmm}):
                raw4, unrel4, rel4 = _make_arts("openmm_fb")
                predict.python_func(
                    model_features=SimpleNamespace(path="f.pkl"),
                    model_params=SimpleNamespace(path="p"),
                    model_name="model_1",
                    prediction_index=0,
                    num_ensemble=1,
                    run_multimer_system=False,
                    random_seed=42,
                    tf_force_unified_memory="1",
                    xla_python_client_mem_fraction="4.0",
                    raw_prediction=raw4,
                    unrelaxed_protein=unrel4,
                    relaxed_protein=rel4,
                    is_run_relax="relax",
                )
                assert rel4.metadata["relax_failed"] is True
                assert "CUDA platform init failed" in rel4.metadata["relax_warning"]
                assert open(rel4.path).read() == "UNRELAXED_PDB\n"

                # Legacy relax_protein.py mirrors the same OpenMMException handling
                from foldrun_app.models.af2.pipeline.components.relax_protein import relax

                legacy_rel = SimpleNamespace(
                    path=str(tmp_path / "legacy_rel.pdb"),
                    uri=str(tmp_path / "legacy_rel"),
                    metadata={},
                )
                relax.python_func(
                    unrelaxed_protein=unrel4,
                    relaxed_protein=legacy_rel,
                )
                assert legacy_rel.metadata["relax_failed"] is True
                assert "CUDA platform init failed" in legacy_rel.metadata["relax_warning"]
                assert open(legacy_rel.path).read() == "UNRELAXED_PDB\n"

    def test_configure_run_and_data_pipeline_reject_invalid_inputs(self, tmp_path):
        """Reject non-positive multimer prediction counts, empty FASTA records, and headerless input."""
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch

        import pytest

        from foldrun_app.models.af2.pipeline.components.configure_run import configure_run

        valid_multi = tmp_path / "valid_multi.fasta"
        valid_multi.write_text(">A\nACDEFG\n>B\nMNPQRS\n")
        for bad_count in (0, -1):
            with pytest.raises(ValueError, match="num_multimer_predictions_per_model must be >= 1"):
                configure_run.python_func(
                    sequence=SimpleNamespace(path=str(valid_multi)),
                    model_preset="multimer",
                    num_multimer_predictions_per_model=bad_count,
                )

        empty_rec = tmp_path / "empty_rec.fasta"
        empty_rec.write_text(">empty\n\n>real\nACDEFGHIKL\n")
        trailing_empty = tmp_path / "trailing_empty.fasta"
        trailing_empty.write_text(">real\nACDEFGHIKL\n>empty\n")
        headerless = tmp_path / "headerless.fasta"
        headerless.write_text("ACDEFGHIKL\n")

        for path, pattern in (
            (empty_rec, "Empty FASTA record"),
            (trailing_empty, "Empty FASTA record"),
            (headerless, "must start with '>' header"),
        ):
            with pytest.raises(ValueError, match=pattern):
                configure_run.python_func(
                    sequence=SimpleNamespace(path=str(path)),
                    model_preset="monomer",
                )

        pipeline_dir = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "foldrun_app",
                "models",
                "af2",
                "pipeline",
            )
        )
        fake_alphafold_utils = ModuleType("alphafold_utils")
        fake_alphafold_utils.run_data_pipeline = lambda **kw: None
        fake_alphafold_utils.run_mmseqs2_data_pipeline = lambda **kw: None
        nfs_dir = tmp_path / "nfs_inv"
        nfs_dir.mkdir()
        ref_databases = SimpleNamespace(
            uri=str(nfs_dir),
            metadata={
                "uniref90": "u",
                "mgnify": "m",
                "uniref30": "u30",
                "bfd": "b",
                "small_bfd": "sb",
                "uniprot": "up",
                "pdb70": "p70",
                "pdb_obsolete": "po",
                "pdb_seqres": "ps",
                "pdb_mmcif": "pm",
            },
        )
        with patch.dict(sys.modules, {"alphafold_utils": fake_alphafold_utils}):
            if pipeline_dir not in sys.path:
                sys.path.insert(0, pipeline_dir)
            from foldrun_app.models.af2.pipeline.components.data_pipeline import data_pipeline

            for path, pattern in (
                (empty_rec, "Empty FASTA record"),
                (trailing_empty, "Empty FASTA record"),
                (headerless, "must start with '>' header"),
            ):
                with pytest.raises(ValueError, match=pattern):
                    data_pipeline.python_func(
                        sequence=SimpleNamespace(path=str(path)),
                        ref_databases=ref_databases,
                        use_small_bfd=True,
                        max_template_date="2025-04-01",
                        msa_method="jackhmmer",
                        msas=SimpleNamespace(path=str(tmp_path / "m_err"), metadata={}),
                        features=SimpleNamespace(path=str(tmp_path / "f_err.pkl"), metadata={}),
                        model_preset="monomer",
                    )
