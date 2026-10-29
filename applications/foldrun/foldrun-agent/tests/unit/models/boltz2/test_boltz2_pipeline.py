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
        assert "ConfigureSeedsBOLTZ2" in source
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


class TestBOLTZ2MSAPipelineSource:
    """Inspect BOLTZ2 MSA pipeline source for correctness."""

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
            "boltz2",
            "pipeline",
            "components",
            "msa_pipeline.py",
        )
        with open(path) as f:
            return f.read()

    def test_msa_writes_to_nfs_cache(self):
        """MSA pipeline writes to NFS-shared cache directory, not local temp."""
        source = self._read_msa_source()
        assert "nfs_msa_cache_base" in source
        assert "boltz2_msas_cache" in source
        assert "boltz2_msas_tmp" in source

    def test_msa_uses_sequence_hash_for_cache_key(self):
        """MSA pipeline uses SHA256 of sequence as cache key."""
        source = self._read_msa_source()
        assert "hashlib" in source
        assert "sha256" in source
        assert "seq_hash" in source

    def test_msa_cache_hit_skips_jackhmmer(self):
        """Cache hit path reuses combined.a3m without running jackhmmer."""
        source = self._read_msa_source()
        assert "Cache hit" in source
        assert 'combined.a3m"' in source

    def test_msa_atomic_cache_promotion(self):
        """MSA is written to tmp dir and atomically renamed to cache dir."""
        source = self._read_msa_source()
        assert "os.rename(seq_dir, seq_cache_dir)" in source
        assert "uuid" in source

    def test_msa_runs_jackhmmer_uniref90_and_mgnify(self):
        """MSA pipeline runs jackhmmer against uniref90 and mgnify."""
        source = self._read_msa_source()
        assert "uniref90_path" in source
        assert "mgnify_path" in source
        assert "jackhmmer" in source

    def test_msa_injects_combined_a3m_as_msa_field(self):
        """MSA pipeline injects combined.a3m path as msa: field in protein chain."""
        source = self._read_msa_source()
        assert 'prot_data["msa"]' in source
        assert "combined.a3m" in source

    def test_msa_skips_non_protein_chains(self):
        """MSA pipeline skips RNA, DNA, and ligand chains (Boltz-2 schema)."""
        source = self._read_msa_source()
        assert '"protein" not in seq_entry' in source

    def test_msa_concurrent_cache_race_handled(self):
        """Concurrent runs that race on cache promotion are handled gracefully."""
        source = self._read_msa_source()
        assert "FileExistsError" in source
        assert "shutil.rmtree" in source

    def test_msa_strips_intermediates_before_cache_promotion(self):
        """Intermediates are deleted before rename so cache entry only contains combined.a3m."""
        source = self._read_msa_source()
        assert "os.remove(intermediate)" in source
        # All five intermediates must be listed for removal
        for name in ("tmp_fasta", "uniref90_sto", "uniref90_a3m", "mgnify_sto", "mgnify_a3m"):
            assert name in source


class TestBOLTZ2MSAPipelineCoreAndCustomMSA:
    """Validate Boltz-2 core-mode single-sequence fallback and custom MSA preservation."""

    @staticmethod
    def _get_msa_func(monkeypatch):
        import sys

        from foldrun_app.models.boltz2.pipeline import config as boltz2_config

        monkeypatch.setitem(sys.modules, "config", boltz2_config)
        from foldrun_app.models.boltz2.pipeline.components.msa_pipeline import (
            msa_pipeline_boltz2,
        )

        return msa_pipeline_boltz2.python_func

    @staticmethod
    def _make_artifact(path: str, uri: str = "", metadata: dict | None = None):
        from types import SimpleNamespace

        return SimpleNamespace(
            path=path, uri=uri, metadata=metadata if metadata is not None else {}
        )

    def test_core_mode_missing_databases_emits_msa_empty_without_polluting_cache(
        self, tmp_path, monkeypatch
    ):
        """When uniref90/mgnify are absent on NFS, msa_pipeline_boltz2 sets msa: empty and skips jackhmmer."""
        import subprocess

        import yaml

        msa_func = self._get_msa_func(monkeypatch)

        nfs_mount = tmp_path / "nfs"
        nfs_mount.mkdir()
        # Do NOT create uniref90.fasta or mgnify.fasta on NFS (simulates DOWNLOAD_MODE=core)
        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90/uniref90.fasta",
                "mgnify": "mgnify/mgy_clusters_2022_05.fa",
            },
        )

        query_yaml = tmp_path / "query.yaml"
        query_yaml.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "sequences": [
                        {"protein": {"id": "A", "sequence": "ACDEFGHIKLMNPQRSTVWY"}},
                        {"ligand": {"id": "L", "smiles": "CC(=O)O"}},
                    ],
                }
            )
        )
        out_yaml = tmp_path / "updated_query.yaml"
        out_artifact = self._make_artifact(path=str(out_yaml), uri=str(out_yaml))

        invoked = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: invoked.append((a, kw)))

        msa_func(
            query_json=self._make_artifact(path=str(query_yaml), uri=str(query_yaml)),
            ref_databases=ref_db,
            updated_query_json=out_artifact,
        )

        assert invoked == [], "jackhmmer must not be called when reference DBs are absent"
        updated = yaml.safe_load(out_yaml.read_text())
        assert updated["sequences"][0]["protein"]["msa"] == "empty"
        assert "msa" not in updated["sequences"][1]["ligand"]
        assert out_artifact.metadata["msa_sequences"] == 0
        # Temp dir should be cleaned up and cache should remain empty
        assert list((nfs_mount / "boltz2_msas_tmp").iterdir()) == []
        assert list((nfs_mount / "boltz2_msas_cache").iterdir()) == []

    def test_preserves_custom_a3m_and_explicit_empty_msa_without_running_jackhmmer(
        self, tmp_path, monkeypatch
    ):
        """User-supplied existing .a3m paths (inside mount_path) and msa: empty are preserved without running jackhmmer."""
        import subprocess

        import yaml

        msa_func = self._get_msa_func(monkeypatch)

        nfs_mount = tmp_path / "nfs"
        nfs_mount.mkdir()
        custom_a3m = nfs_mount / "custom_alignment.a3m"
        custom_a3m.write_text(">A\nACDEFGHIKL\n>homolog_1\nACDEFGHIKM\n")
        outside_a3m = tmp_path / "outside.a3m"
        outside_a3m.write_text(">C\nACDEFGHIKL\n")

        ref_db = self._make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={
                "uniref90": "uniref90/uniref90.fasta",
                "mgnify": "mgnify/mgy_clusters_2022_05.fa",
            },
        )

        query_yaml = tmp_path / "query_custom.yaml"
        query_yaml.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "sequences": [
                        {
                            "protein": {
                                "id": "A",
                                "sequence": "ACDEFGHIKL",
                                "msa": str(custom_a3m),
                            }
                        },
                        {
                            "protein": {
                                "id": "B",
                                "sequence": "MNPQRSTVWY",
                                "msa": " EMPTY ",
                            }
                        },
                        {
                            "protein": {
                                "id": "C",
                                "sequence": "ACDEFGHIKL",
                                "msa": str(outside_a3m),
                            }
                        },
                    ],
                }
            )
        )
        out_yaml = tmp_path / "updated_custom.yaml"
        out_artifact = self._make_artifact(path=str(out_yaml), uri=str(out_yaml))

        invoked = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: invoked.append((a, kw)))

        msa_func(
            query_json=self._make_artifact(path=str(query_yaml), uri=str(query_yaml)),
            ref_databases=ref_db,
            updated_query_json=out_artifact,
        )

        assert invoked == [], (
            "jackhmmer must not run when chains specify valid msa or fall back in core mode"
        )
        updated = yaml.safe_load(out_yaml.read_text())
        assert updated["sequences"][0]["protein"]["msa"] == str(custom_a3m)
        assert updated["sequences"][1]["protein"]["msa"] == "empty"
        # Chain C had an MSA path outside mount_path -> ignored and fell back to core mode ("empty")
        assert updated["sequences"][2]["protein"]["msa"] == "empty"
        assert out_artifact.metadata["msa_sequences"] == 2
