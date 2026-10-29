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

"""Unit tests for asset tar validation, path rewriting, output packaging, and compile lock."""

import io
import json
import os
import tarfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import yaml

from foldrun_app.gateway._compat import GLOBAL_COMPILE_LOCK, compile_env_context
from foldrun_app.gateway.output_packager import build_or_get_native_outputs_archive
from foldrun_app.gateway.staging import (
    BundleValidationError,
    extract_and_validate_assets_tar,
    rewrite_and_validate_input_references,
)
from foldrun_app.models.af2.base import AF2Tool
from foldrun_app.models.af2.tools.submit_monomer import (
    _COMPILE_ENV_LOCK as AF2_MONOMER_LOCK,
)
from foldrun_app.models.boltz2.utils.pipeline_utils import (
    load_vertex_pipeline as load_boltz2_pipeline,
)
from foldrun_app.models.of3.tools.submit_prediction import (
    _COMPILE_ENV_LOCK as OF3_ENV_LOCK,
)
from foldrun_app.models.of3.tools.submit_prediction import (
    _COMPILE_LOCK as OF3_LOCK,
)
from foldrun_app.models.of3.utils.pipeline_utils import (
    load_vertex_pipeline as load_of3_pipeline,
)


def _make_tar_bytes(entries: list[tuple[str, bytes, bytes]]) -> bytes:
    """Create a tar.gz archive from (name, content, tar_type) tuples."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data, ttype in entries:
            info = tarfile.TarInfo(name=name)
            info.type = ttype
            if ttype in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "/etc/passwd"
                info.size = 0
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestAssetsTarSecurityValidation:
    """Verify tar extraction rejects symlinks, hardlinks, devices, and path traversal."""

    def test_valid_assets_tar_extracted(self):
        raw = _make_tar_bytes(
            [
                ("./msas/custom.a3m", b">seq1\nACDEFG\n", tarfile.REGTYPE),
                ("templates/1abc.cif", b"data_1abc\n", tarfile.REGTYPE),
            ]
        )
        assets = extract_and_validate_assets_tar(raw)
        assert assets == {
            "msas/custom.a3m": b">seq1\nACDEFG\n",
            "templates/1abc.cif": b"data_1abc\n",
        }

    @pytest.mark.parametrize(
        "bad_entry",
        [
            ("symlink.a3m", b"", tarfile.SYMTYPE),
            ("hardlink.a3m", b"", tarfile.LNKTYPE),
            ("fifo.a3m", b"", tarfile.FIFOTYPE),
            ("../escape.a3m", b"evil", tarfile.REGTYPE),
            ("msas/../../escape.a3m", b"evil", tarfile.REGTYPE),
            ("/etc/passwd", b"evil", tarfile.REGTYPE),
        ],
    )
    def test_malicious_tar_entries_rejected(self, bad_entry):
        raw = _make_tar_bytes([bad_entry])
        with pytest.raises(BundleValidationError):
            extract_and_validate_assets_tar(raw)


class TestInputPathRewritingAndContainment:
    """Verify YAML/JSON asset reference validation and /gcs/... path rewriting."""

    def test_boltz_yaml_rewrites_bundled_paths_and_preserves_empty(self):
        yaml_in = """version: 1
sequences:
  - protein:
      id: A
      sequence: MVTPE
      msa: ./msas/custom.a3m
  - protein:
      id: B
      sequence: GGGGG
      msa: empty
templates:
  - cif: templates/1abc.cif
"""
        assets_map = {
            "msas/custom.a3m": b">A\nMVTPE\n",
            "templates/1abc.cif": b"data_1abc\n",
        }
        rewritten = rewrite_and_validate_input_references(
            model="boltz",
            input_text=yaml_in,
            assets_map=assets_map,
            bucket_name="my-bucket",
            job_id="boltz-gw-1234",
        )
        parsed = yaml.safe_load(rewritten)
        assert (
            parsed["sequences"][0]["protein"]["msa"]
            == "/gcs/my-bucket/gateway_jobs/boltz-gw-1234/inputs/assets/msas/custom.a3m"
        )
        assert parsed["sequences"][1]["protein"]["msa"] == "empty"
        assert (
            parsed["templates"][0]["cif"]
            == "/gcs/my-bucket/gateway_jobs/boltz-gw-1234/inputs/assets/templates/1abc.cif"
        )

    def test_boltz_yaml_rejects_unbundled_nfs_path(self):
        yaml_in = """version: 1
sequences:
  - protein:
      id: A
      sequence: MVTPE
      msa: /mnt/nfs/foldrun/secret.a3m
"""
        with pytest.raises(BundleValidationError):
            rewrite_and_validate_input_references(
                model="boltz",
                input_text=yaml_in,
                assets_map={},
                bucket_name="my-bucket",
                job_id="boltz-gw-1234",
            )

    def test_openfold3_json_rewrites_bundled_msa_and_templates(self):
        json_in = json.dumps(
            {
                "queries": {
                    "complex_1": {
                        "chains": [
                            {
                                "molecule_type": "protein",
                                "chain_ids": ["A"],
                                "sequence": "MVTPE",
                                "main_msa_file_paths": ["./msas/a.a3m"],
                                "template_alignment_file_path": "templates/align.sto",
                            }
                        ]
                    }
                }
            }
        )
        assets_map = {
            "msas/a.a3m": b">A\nMVTPE\n",
            "templates/align.sto": b"# STOCKHOLM 1.0\n",
        }
        rewritten = rewrite_and_validate_input_references(
            model="openfold3",
            input_text=json_in,
            assets_map=assets_map,
            bucket_name="my-bucket",
            job_id="of3-gw-5678",
        )
        parsed = json.loads(rewritten)
        chain = parsed["queries"]["complex_1"]["chains"][0]
        assert chain["main_msa_file_paths"] == [
            "/gcs/my-bucket/gateway_jobs/of3-gw-5678/inputs/assets/msas/a.a3m"
        ]
        assert (
            chain["template_alignment_file_path"]
            == "/gcs/my-bucket/gateway_jobs/of3-gw-5678/inputs/assets/templates/align.sto"
        )

    def test_generative_binder_inputs_rewrite_local_assets_and_preserve_pdb_ids(self):
        # BoltzGen with local file vs 4-char PDB ID
        bg_yaml = """entities:
  - protein:
      id: B
      sequence: 80..120
  - file:
      path: ./targets/pdl1.cif
"""
        rewritten_bg = rewrite_and_validate_input_references(
            model="boltzgen",
            input_text=bg_yaml,
            assets_map={"targets/pdl1.cif": b"data_pdl1\n"},
            bucket_name="my-bucket",
            job_id="bg-gw-1",
        )
        parsed_bg = yaml.safe_load(rewritten_bg)
        assert (
            parsed_bg["entities"][1]["file"]["path"]
            == "gs://my-bucket/gateway_jobs/bg-gw-1/inputs/assets/targets/pdl1.cif"
        )

        bg_pdb = """entities:
  - protein:
      id: B
      sequence: 80..120
  - file:
      path: 4Z18
"""
        rewritten_bg_pdb = rewrite_and_validate_input_references(
            model="boltzgen",
            input_text=bg_pdb,
            assets_map={},
            bucket_name="my-bucket",
            job_id="bg-gw-2",
        )
        assert yaml.safe_load(rewritten_bg_pdb)["entities"][1]["file"]["path"] == "4Z18"

        # RFD4-Proteina native YAML with local input file
        rfd4_yaml = """binder_design:
  input: ./target_pdl1.cif
  contig: 80-120, /0, A1-115
"""
        rewritten_rfd4 = rewrite_and_validate_input_references(
            model="rfd4_proteina",
            input_text=rfd4_yaml,
            assets_map={"target_pdl1.cif": b"data_pdl1\n"},
            bucket_name="my-bucket",
            job_id="rfd4-gw-1",
        )
        assert (
            yaml.safe_load(rewritten_rfd4)["binder_design"]["input"]
            == "gs://my-bucket/gateway_jobs/rfd4-gw-1/inputs/assets/target_pdl1.cif"
        )

        # BindCraft settings JSON with local starting_pdb
        bc_json = json.dumps(
            {
                "binder_name": "PDL1_Binder",
                "starting_pdb": "./target_pdl1.pdb",
                "chains": "A",
                "lengths": [80, 120],
            }
        )
        rewritten_bc = rewrite_and_validate_input_references(
            model="bindcraft",
            input_text=bc_json,
            assets_map={"target_pdl1.pdb": b"HEADER PDL1\nEND\n"},
            bucket_name="my-bucket",
            job_id="bc-gw-1",
        )
        assert (
            json.loads(rewritten_bc)["starting_pdb"]
            == "gs://my-bucket/gateway_jobs/bc-gw-1/inputs/assets/target_pdl1.pdb"
        )


class FakeBlob:
    def __init__(self, name: str, store: dict[str, bytes]):
        self.name = name
        self._store = store

    def exists(self) -> bool:
        return self.name in self._store

    @property
    def size(self) -> int:
        return len(self._store.get(self.name, b""))

    def upload_from_string(self, data: str | bytes, content_type: str | None = None) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._store[self.name] = data

    def download_as_text(self) -> str:
        return self._store[self.name].decode("utf-8")

    def download_as_bytes(self) -> bytes:
        return self._store[self.name]

    def open(self, mode: str = "rb", chunk_size: int | None = None, **kwargs):
        if "w" in mode:
            store = self._store
            name = self.name

            class _Writer(io.BytesIO):
                def close(self_inner):
                    store[name] = self_inner.getvalue()
                    super().close()

            return _Writer()
        return io.BytesIO(self._store[self.name])


class FakeBucket:
    def __init__(self, store: dict[str, bytes], name: str = "test-bucket"):
        self._store = store
        self.name = name

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self._store)

    def list_blobs(self, prefix: str = ""):
        return [
            FakeBlob(k, self._store) for k in sorted(self._store.keys()) if k.startswith(prefix)
        ]


class FakeStorageClient:
    def __init__(self, store: dict[str, bytes]):
        self._store = store

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self._store, name=name)


class TestStreamingOutputPackager:
    """Verify native directory layout reconstruction for Boltz-2, OF3, and AF2."""

    def test_boltz2_multiseed_output_reindexing_and_renaming(self):
        store: dict[str, bytes] = {}
        job_id = "boltz-gw-1234-20261023000000"
        pipeline_root = "gs://test-bucket/pipeline_runs/20261023_000000"
        store[f"gateway_jobs/{job_id}/manifest.json"] = json.dumps(
            {
                "job_id": job_id,
                "model": "boltz",
                "input_stem": "Affinity_Complex",
                "pipeline_root": pipeline_root,
            }
        ).encode("utf-8")

        # Seed 1 (2 samples) + Seed 2 (2 samples) + Affinity
        p_prefix = "pipeline_runs/20261023_000000/123/boltz2-inference-pipeline/"
        store[
            f"{p_prefix}predict-boltz2_1/output_dir/boltz_results_patched_query/predictions/patched_query/patched_query_model_0.cif"
        ] = b"cif_s1_0"
        store[
            f"{p_prefix}predict-boltz2_1/output_dir/boltz_results_patched_query/predictions/patched_query/patched_query_model_1.cif"
        ] = b"cif_s1_1"
        store[
            f"{p_prefix}predict-boltz2_1/output_dir/boltz_results_patched_query/predictions/patched_query/confidence_patched_query_model_0.json"
        ] = b'{"confidence_score": 0.9}'
        store[
            f"{p_prefix}predict-boltz2_2/output_dir/boltz_results_patched_query/predictions/patched_query/patched_query_model_0.cif"
        ] = b"cif_s2_0"
        store[
            f"{p_prefix}predict-boltz2_2/output_dir/boltz_results_patched_query/predictions/patched_query/patched_query_model_1.cif"
        ] = b"cif_s2_1"
        store[
            f"{p_prefix}predict-boltz2-affinity/affinity_dir/boltz_results_patched_query/predictions/patched_query/affinity_patched_query.json"
        ] = b'{"affinity_pred_value": -7.2}'

        client = FakeStorageClient(store)
        fake_job = SimpleNamespace(
            name=f"projects/p/locations/us-central1/pipelineJobs/{job_id}",
            labels={"model_type": "boltz2", "query_name": "affinity-complex"},
            runtime_config=SimpleNamespace(gcs_output_directory=pipeline_root),
            job_detail=SimpleNamespace(task_details=[]),
        )

        uri = build_or_get_native_outputs_archive(
            storage_client=client,
            bucket_name="test-bucket",
            job_id=job_id,
            model="boltz",
            job=fake_job,
        )
        assert uri == f"gs://test-bucket/gateway_jobs/{job_id}/native_outputs.tar.gz"

        archive_bytes = store[f"gateway_jobs/{job_id}/native_outputs.tar.gz"]
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            names = sorted(tf.getnames())
            assert (
                "boltz_results_Affinity_Complex/predictions/Affinity_Complex/Affinity_Complex_model_0.cif"
                in names
            )
            assert (
                "boltz_results_Affinity_Complex/predictions/Affinity_Complex/Affinity_Complex_model_1.cif"
                in names
            )
            assert (
                "boltz_results_Affinity_Complex/predictions/Affinity_Complex/Affinity_Complex_model_2.cif"
                in names
            )
            assert (
                "boltz_results_Affinity_Complex/predictions/Affinity_Complex/Affinity_Complex_model_3.cif"
                in names
            )
            assert (
                "boltz_results_Affinity_Complex/predictions/Affinity_Complex/affinity_Affinity_Complex.json"
                in names
            )

    def test_alphafold2_synthesizes_ranked_pdbs_and_ranking_debug_json(self):
        store: dict[str, bytes] = {}
        job_id = "alphafold2-gw-9999-20261023000000"
        pipeline_root = "gs://test-bucket/pipeline_runs/20261023_000000"
        store[f"gateway_jobs/{job_id}/manifest.json"] = json.dumps(
            {
                "job_id": job_id,
                "model": "alphafold2",
                "input_stem": "T1024",
                "pipeline_root": pipeline_root,
                "normalized_flags": {"models_to_relax": "all"},
            }
        ).encode("utf-8")

        store["pipeline_runs/20261023_000000/predict_1/unrelaxed_protein.pdb"] = b"UNRELAXED_1"
        store["pipeline_runs/20261023_000000/relax_1/relaxed_protein.pdb"] = b"RELAXED_1"
        store["pipeline_runs/20261023_000000/predict_2/unrelaxed_protein.pdb"] = b"UNRELAXED_2"
        store["pipeline_runs/20261023_000000/relax_2/relaxed_protein.pdb"] = b"RELAXED_2"

        # Task details where model_2 has higher confidence (92.5) than model_1 (81.0)
        def _make_predict_task(model_name: str, conf: float, uri: str):
            return SimpleNamespace(
                task_name="predict",
                execution=SimpleNamespace(metadata={"input:model_name": model_name}),
                outputs={
                    "raw_prediction": SimpleNamespace(
                        artifacts=[
                            SimpleNamespace(
                                uri=uri.replace("unrelaxed_protein.pdb", "result.pkl"),
                                metadata={"ranking_confidence": conf},
                            )
                        ]
                    ),
                    "unrelaxed_protein": SimpleNamespace(
                        artifacts=[SimpleNamespace(uri=uri, metadata={})]
                    ),
                },
            )

        def _make_relax_task(uri: str):
            return SimpleNamespace(
                task_name="relax",
                execution=SimpleNamespace(metadata={}),
                outputs={
                    "relaxed_protein": SimpleNamespace(
                        artifacts=[SimpleNamespace(uri=uri, metadata={})]
                    )
                },
            )

        fake_job = SimpleNamespace(
            name=f"projects/p/locations/us-central1/pipelineJobs/{job_id}",
            labels={"model_type": "alphafold2"},
            runtime_config=SimpleNamespace(gcs_output_directory=pipeline_root),
            job_detail=SimpleNamespace(
                task_details=[
                    _make_predict_task(
                        "model_1",
                        81.0,
                        f"{pipeline_root}/predict_1/unrelaxed_protein.pdb",
                    ),
                    _make_relax_task(f"{pipeline_root}/relax_1/relaxed_protein.pdb"),
                    _make_predict_task(
                        "model_2",
                        92.5,
                        f"{pipeline_root}/predict_2/unrelaxed_protein.pdb",
                    ),
                    _make_relax_task(f"{pipeline_root}/relax_2/relaxed_protein.pdb"),
                ]
            ),
        )

        client = FakeStorageClient(store)
        build_or_get_native_outputs_archive(
            storage_client=client,
            bucket_name="test-bucket",
            job_id=job_id,
            model="alphafold2",
            job=fake_job,
        )

        archive_bytes = store[f"gateway_jobs/{job_id}/native_outputs.tar.gz"]
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            ranking = json.loads(tf.extractfile("T1024/ranking_debug.json").read().decode("utf-8"))
            assert ranking["order"] == ["model_2", "model_1"]
            assert tf.extractfile("T1024/ranked_0.pdb").read() == b"RELAXED_2"
            assert tf.extractfile("T1024/ranked_1.pdb").read() == b"RELAXED_1"

    def test_generative_binder_output_packager_ranks_designs_and_writes_aliases(self):
        for model, alias_dir, alias_csv in [
            ("boltzgen", "final_ranked_designs", "metrics.csv"),
            ("rfd4_proteina", "ranked_designs", "metrics.csv"),
            ("bindcraft", "Accepted", "final_design_stats.csv"),
        ]:
            store: dict[str, bytes] = {}
            job_id = f"{model}-gw-1111-20261024000000"
            pipeline_root = "gs://test-bucket/pipeline_runs/20261024_000000"
            store[f"gateway_jobs/{job_id}/manifest.json"] = json.dumps(
                {
                    "job_id": job_id,
                    "model": model,
                    "input_stem": "pdl1_binder",
                    "pipeline_root": pipeline_root,
                }
            ).encode("utf-8")

            p_prefix = "pipeline_runs/20261024_000000/1/predict_seed1/predictions/"
            store[f"{p_prefix}design_seed42_0.cif"] = b"CIF_LOW"
            store[f"{p_prefix}confidence_design_seed42_0.json"] = json.dumps(
                {
                    "design_id": "design_seed42_0",
                    "seed_value": 42,
                    "ranking_score": 0.78,
                    "plddt": 81.2,
                    "iptm": 0.76,
                    "sc_score": 0.65,
                    "interface_energy": -28.4,
                    "contact_surface_area": 1150.0,
                }
            ).encode("utf-8")
            store[f"{p_prefix}design_seed42_1.cif"] = b"CIF_HIGH"
            store[f"{p_prefix}confidence_design_seed42_1.json"] = json.dumps(
                {
                    "design_id": "design_seed42_1",
                    "seed_value": 42,
                    "ranking_score": 0.93,
                    "plddt": 90.5,
                    "iptm": 0.91,
                    "sc_score": 0.89,
                    "interface_energy": -42.1,
                    "contact_surface_area": 1480.0,
                }
            ).encode("utf-8")

            fake_job = SimpleNamespace(
                name=f"projects/p/locations/us-central1/pipelineJobs/{job_id}",
                labels={"model_type": model, "query_name": "pdl1_binder"},
                runtime_config=SimpleNamespace(gcs_output_directory=pipeline_root),
                job_detail=SimpleNamespace(task_details=[]),
            )
            client = FakeStorageClient(store)
            build_or_get_native_outputs_archive(
                storage_client=client,
                bucket_name="test-bucket",
                job_id=job_id,
                model=model,
                job=fake_job,
            )

            archive_bytes = store[f"gateway_jobs/{job_id}/native_outputs.tar.gz"]
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
                names = set(tf.getnames())
                assert "pdl1_binder/predictions/design_seed42_0.cif" in names
                assert "pdl1_binder/predictions/design_seed42_1.cif" in names
                assert "pdl1_binder/ranked_designs/ranked_0.cif" in names
                assert "pdl1_binder/ranked_designs/metrics.csv" in names
                assert f"pdl1_binder/{alias_dir}/ranked_0.cif" in names
                assert f"pdl1_binder/{alias_dir}/{alias_csv}" in names
                assert tf.extractfile(f"pdl1_binder/{alias_dir}/ranked_0.cif").read() == b"CIF_HIGH"

    @pytest.mark.parametrize("model", ["alphafold3", "af3"])
    def test_alphafold3_packages_cif_and_json_outputs(self, model: str):
        store: dict[str, bytes] = {}
        job_id = f"{model.replace('_', '-')}-gw-4z18-20261026000000"
        pipeline_root = "gs://test-bucket/pipeline_runs/20261026_000000"
        store[f"gateway_jobs/{job_id}/manifest.json"] = json.dumps(
            {
                "job_id": job_id,
                "model": model,
                "input_stem": "4Z18",
                "pipeline_root": pipeline_root,
            }
        ).encode("utf-8")

        p_prefix = "pipeline_runs/20261026_000000/outputs/"
        store[f"{p_prefix}4Z18_model_0.cif"] = b"data_af3_cif_0\n"
        store[f"{p_prefix}4Z18_model_1.cif"] = b"data_af3_cif_1\n"
        store[f"{p_prefix}summary_confidences_0.json"] = b'{"ranking_score": 0.95}\n'
        store[f"{p_prefix}ranking_scores.csv"] = b"rank,score\n0,0.95\n"

        fake_job = SimpleNamespace(
            name=f"projects/p/locations/us-central1/pipelineJobs/{job_id}",
            labels={"model_type": model, "query_name": "4Z18"},
            runtime_config=SimpleNamespace(gcs_output_directory=pipeline_root),
            job_detail=SimpleNamespace(task_details=[]),
        )
        client = FakeStorageClient(store)
        uri = build_or_get_native_outputs_archive(
            storage_client=client,
            bucket_name="test-bucket",
            job_id=job_id,
            model=model,
            job=fake_job,
        )
        assert uri == f"gs://test-bucket/gateway_jobs/{job_id}/native_outputs.tar.gz"

        archive_bytes = store[f"gateway_jobs/{job_id}/native_outputs.tar.gz"]
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            names = set(tf.getnames())
            assert "4Z18/4Z18_model_0.cif" in names
            assert "4Z18/4Z18_model_1.cif" in names
            assert "4Z18/summary_confidences_0.json" in names
            assert "4Z18/ranking_scores.csv" in names
            assert tf.extractfile("4Z18/4Z18_model_0.cif").read() == b"data_af3_cif_0\n"


class TestGlobalCompileLockConcurrency:
    """Verify AF2, OF3, and Boltz-2 share GLOBAL_COMPILE_LOCK and reload config."""

    def test_all_model_locks_alias_global_compile_lock(self):
        assert AF2Tool._compile_env_lock is GLOBAL_COMPILE_LOCK
        assert AF2_MONOMER_LOCK is GLOBAL_COMPILE_LOCK
        assert OF3_LOCK is GLOBAL_COMPILE_LOCK
        assert OF3_ENV_LOCK is GLOBAL_COMPILE_LOCK

    def test_concurrent_of3_and_boltz2_pipeline_loads_with_env_overrides(self):
        def _worker(idx: int) -> str:
            if idx % 2 == 0:
                img = f"us-central1-docker.pkg.dev/p/r/of3:{idx}"
                with compile_env_context({"OPENFOLD3_COMPONENTS_IMAGE": img}):
                    assert os.environ["OPENFOLD3_COMPONENTS_IMAGE"] == img
                    load_of3_pipeline()
                    from foldrun_app.models.of3.pipeline import config as of3_cfg

                    assert of3_cfg.OPENFOLD3_COMPONENTS_IMAGE == img
                return img
            else:
                img = f"us-central1-docker.pkg.dev/p/r/boltz2:{idx}"
                with compile_env_context({"BOLTZ2_COMPONENTS_IMAGE": img}):
                    assert os.environ["BOLTZ2_COMPONENTS_IMAGE"] == img
                    load_boltz2_pipeline()
                    from foldrun_app.models.boltz2.pipeline import config as b2_cfg

                    assert b2_cfg.BOLTZ2_COMPONENTS_IMAGE == img
                return img

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(_worker, range(12)))
        assert len(results) == 12
