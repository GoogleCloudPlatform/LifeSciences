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

"""End-to-end integration tests for foldrun-shim CLI & Notebook helpers against FastAPI Gateway."""

import json
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_SDK_PATH = str(Path(__file__).resolve().parents[4] / "sdk" / "foldrun-shim")
if _SDK_PATH not in sys.path:
    sys.path.insert(0, _SDK_PATH)

from foldrun_shim import (  # noqa: E402
    alphafold3,
    bindcraft,
    boltz,
    boltzgen,
    rfd4_proteina,
)
from foldrun_shim.cli import (  # noqa: E402
    alphafold2_main,
    alphafold3_main,
    bindcraft_main,
    boltz_main,
    boltzgen_main,
    openfold3_main,
    rfd4_proteina_main,
)

from foldrun_app.gateway.app import create_gateway_app  # noqa: E402

from .test_staging_and_packaging import FakeStorageClient  # noqa: E402


class _MockPipelineEnvironment:
    """In-memory mock of GCS + Vertex AI PipelineJobs for end-to-end gateway testing."""

    def __init__(self) -> None:
        self.gcs_store: dict[str, bytes] = {}
        self.storage_client = FakeStorageClient(self.gcs_store)
        self.jobs: dict[str, SimpleNamespace] = {}
        self.submit_count = 0

    def register_submitted_job(
        self,
        job_id: str,
        model_label: str,
        query_name: str,
        pipeline_root: str,
        input_sha256: str,
        initial_state: str = "PIPELINE_STATE_SUCCEEDED",
        gcs_output_dir: str = "",
    ) -> dict[str, Any]:
        self.submit_count += 1
        root_prefix = pipeline_root.replace("gs://test-bucket/", "").rstrip("/")
        if model_label == "boltz2":
            self.gcs_store[
                f"{root_prefix}/1/boltz2-inference-pipeline/predict-boltz2/output_dir/"
                "boltz_results_patched_query/predictions/patched_query/patched_query_model_0.cif"
            ] = b"data_boltz_structure\n"
            self.gcs_store[
                f"{root_prefix}/1/boltz2-inference-pipeline/predict-boltz2/output_dir/"
                "boltz_results_patched_query/predictions/patched_query/confidence_patched_query_model_0.json"
            ] = b'{"confidence_score": 0.94}\n'
            self.gcs_store[
                f"{root_prefix}/1/boltz2-inference-pipeline/predict-boltz2-affinity/affinity_dir/"
                "boltz_results_patched_query/predictions/patched_query/affinity_patched_query.json"
            ] = b'{"affinity_pred_value": -8.1}\n'
        elif model_label == "openfold3":
            self.gcs_store[
                f"{root_prefix}/1/of3-inference-pipeline/predict-of3/output_dir/"
                f"{query_name}/seed_42/{query_name}_seed_42_model.cif"
            ] = b"data_of3_structure\n"
            self.gcs_store[
                f"{root_prefix}/1/of3-inference-pipeline/predict-of3/output_dir/"
                f"{query_name}/seed_42/{query_name}_seed_42_confidences_aggregated.json"
            ] = b'{"plddt": 89.2}\n'
        elif model_label == "alphafold2":
            self.gcs_store[f"{root_prefix}/predict_1/unrelaxed_protein.pdb"] = (
                b"HEADER AF2 UNRELAXED\nEND\n"
            )
            self.gcs_store[f"{root_prefix}/relax_1/relaxed_protein.pdb"] = (
                b"HEADER AF2 RELAXED\nEND\n"
            )
        elif model_label in ("boltzgen", "rfd4-proteina", "bindcraft"):
            self.gcs_store[f"{root_prefix}/1/predict_seed42/predictions/design_seed42_0.cif"] = (
                b"data_designed_binder\n"
            )
            self.gcs_store[
                f"{root_prefix}/1/predict_seed42/predictions/confidence_design_seed42_0.json"
            ] = json.dumps(
                {
                    "design_id": "design_seed42_0",
                    "seed_value": 42,
                    "ranking_score": 0.91,
                    "plddt": 89.5,
                    "iptm": 0.88,
                    "sc_score": 0.87,
                    "interface_energy": -38.5,
                    "contact_surface_area": 1350.0,
                    "designed_sequence": "MVTPEGNVSL",
                }
            ).encode("utf-8")
        elif model_label == "alphafold3":
            import zstandard as zstd

            cctx = zstd.ZstdCompressor()
            af3_prefix = (
                gcs_output_dir.replace("gs://test-bucket/", "").rstrip("/") + "/af3_raw"
                if gcs_output_dir
                else f"{root_prefix}/af3_outputs"
            )
            self.gcs_store[f"{af3_prefix}/seed-1_sample-0/model.cif.zst"] = cctx.compress(
                b"data_af3_structure\n"
            )
            self.gcs_store[f"{af3_prefix}/seed-1_sample-0/summary_confidences.json.zst"] = (
                cctx.compress(b'{"ranking_score": 0.93, "ptm": 0.90, "iptm": 0.89}\n')
            )

        task_details = []
        if model_label == "alphafold2":
            task_details = [
                SimpleNamespace(
                    task_id="1",
                    task_name="predict",
                    state=SimpleNamespace(name="SUCCEEDED"),
                    start_time=None,
                    end_time=None,
                    error=None,
                    execution=SimpleNamespace(metadata={"input:model_name": "model_1"}),
                    outputs={
                        "raw_prediction": SimpleNamespace(
                            artifacts=[
                                SimpleNamespace(
                                    uri=f"{pipeline_root}/predict_1/result.pkl",
                                    metadata={"ranking_confidence": 91.4},
                                )
                            ]
                        ),
                        "unrelaxed_protein": SimpleNamespace(
                            artifacts=[
                                SimpleNamespace(
                                    uri=f"{pipeline_root}/predict_1/unrelaxed_protein.pdb",
                                    metadata={},
                                )
                            ]
                        ),
                    },
                ),
                SimpleNamespace(
                    task_id="2",
                    task_name="relax",
                    state=SimpleNamespace(name="SUCCEEDED"),
                    start_time=None,
                    end_time=None,
                    error=None,
                    execution=SimpleNamespace(metadata={}),
                    outputs={
                        "relaxed_protein": SimpleNamespace(
                            artifacts=[
                                SimpleNamespace(
                                    uri=f"{pipeline_root}/relax_1/relaxed_protein.pdb",
                                    metadata={},
                                )
                            ]
                        )
                    },
                ),
            ]

        resource_name = f"projects/test-proj/locations/us-central1/pipelineJobs/{job_id}"
        job_obj = SimpleNamespace(
            name=resource_name,
            display_name=query_name,
            state=SimpleNamespace(name=initial_state),
            labels={
                "model_type": model_label,
                "query_name": query_name,
                "input_sha256": input_sha256[:63],
            },
            create_time=None,
            start_time=None,
            end_time=None,
            error=None,
            pipeline_spec=None,
            runtime_config=SimpleNamespace(
                gcs_output_directory=pipeline_root,
                parameter_values={},
            ),
            job_detail=SimpleNamespace(task_details=task_details),
        )
        self.jobs[job_id] = job_obj
        out: dict[str, Any] = {
            "job_id": job_id,
            "pipeline_job_resource": resource_name,
            "status": initial_state,
            "pipeline_root": pipeline_root,
        }
        if gcs_output_dir:
            out["gcs_output_dir"] = gcs_output_dir
        return out


@pytest.fixture
def mock_gateway_env(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("GCS_DATABASES_BUCKET", "test-gdbs-bucket")
    monkeypatch.setenv("FILESTORE_ID", "foldrun-nfs")
    monkeypatch.setenv("FOLDRUN_VIEWER_URL", "https://viewer.example.com")
    monkeypatch.setenv("AF3_ENDPOINT", "auto")

    env = _MockPipelineEnvironment()

    def _fake_boltz_init(self, tool_config, config=None):
        self.tool_config = tool_config
        self.config = SimpleNamespace(
            project_id="test-proj", region="us-central1", bucket_name="test-bucket"
        )
        self.storage_client = env.storage_client

    def _fake_boltz_run(self, arguments):
        job_id = arguments["custom_job_id"]
        sha = arguments.get("extra_labels", {}).get("input_sha256", "")
        return env.register_submitted_job(
            job_id=job_id,
            model_label="boltz2",
            query_name=arguments.get("query_name", "query"),
            pipeline_root=arguments["pipeline_root"],
            input_sha256=sha,
        )

    def _fake_of3_init(self, tool_config, config=None):
        self.tool_config = tool_config
        self.config = SimpleNamespace(
            project_id="test-proj", region="us-central1", bucket_name="test-bucket"
        )
        self.storage_client = env.storage_client

    def _fake_of3_run(self, arguments):
        job_id = arguments["custom_job_id"]
        sha = arguments.get("extra_labels", {}).get("input_sha256", "")
        return env.register_submitted_job(
            job_id=job_id,
            model_label="openfold3",
            query_name=arguments.get("query_name", "query"),
            pipeline_root=arguments["pipeline_root"],
            input_sha256=sha,
        )

    def _fake_af2_init(self, tool_config, config=None):
        self.tool_config = tool_config
        self.config = SimpleNamespace(
            project_id="test-proj", region="us-central1", bucket_name="test-bucket"
        )
        self.storage_client = env.storage_client

    def _fake_af2_run(self, arguments):
        job_id = arguments["custom_job_id"]
        sha = arguments.get("extra_labels", {}).get("input_sha256", "")
        return env.register_submitted_job(
            job_id=job_id,
            model_label="alphafold2",
            query_name=arguments.get("job_name", "query"),
            pipeline_root=arguments["pipeline_root"],
            input_sha256=sha,
        )

    def _fake_optional_binder_submit_tool(model: str, cfg: Any):
        model_label = "rfd4-proteina" if model == "rfd4_proteina" else model

        class _FakeBinderTool:
            def __init__(self) -> None:
                self.storage_client = env.storage_client
                self.config = cfg

            def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
                job_id = arguments["custom_job_id"]
                sha = arguments.get("extra_labels", {}).get("input_sha256", "")
                return env.register_submitted_job(
                    job_id=job_id,
                    model_label=model_label,
                    query_name=arguments.get("query_name", "query"),
                    pipeline_root=arguments["pipeline_root"],
                    input_sha256=sha,
                )

        return _FakeBinderTool()

    def _fake_af3_run(self, arguments):
        assert arguments.get("sync") is False
        job_name = arguments["job_name"]
        run_id = f"alphafold3-inference-pipeline-202610271830{env.submit_count:02d}"
        pipeline_root = f"gs://test-bucket/pipeline_runs/20261027_1830{env.submit_count:02d}"
        gcs_output_dir = f"gs://test-bucket/af3_predictions/{job_name}"
        return env.register_submitted_job(
            job_id=run_id,
            model_label="alphafold3",
            query_name=job_name,
            pipeline_root=pipeline_root,
            input_sha256="",
            gcs_output_dir=gcs_output_dir,
        )

    def _fake_get_pipeline_job(job_id, project_id=None, region=None):
        short_id = job_id.split("/")[-1]
        if short_id not in env.jobs:
            raise ValueError(f"Job {short_id} not found")
        return env.jobs[short_id]

    def _fake_list_pipeline_jobs(project_id, region, filter_str=None, page_size=20):
        return []

    def _fake_cfg(*args, **kwargs):
        return SimpleNamespace(
            project_id="test-proj", region="us-central1", bucket_name="test-bucket"
        )

    patches = [
        patch(
            "foldrun_app.models.boltz2.tools.submit_prediction.BOLTZ2SubmitPredictionTool.__init__",
            _fake_boltz_init,
        ),
        patch(
            "foldrun_app.models.boltz2.tools.submit_prediction.BOLTZ2SubmitPredictionTool.run",
            _fake_boltz_run,
        ),
        patch(
            "foldrun_app.models.of3.tools.submit_prediction.OF3SubmitPredictionTool.__init__",
            _fake_of3_init,
        ),
        patch(
            "foldrun_app.models.of3.tools.submit_prediction.OF3SubmitPredictionTool.run",
            _fake_of3_run,
        ),
        patch(
            "foldrun_app.models.af2.tools.submit_monomer.AF2SubmitMonomerTool.__init__",
            _fake_af2_init,
        ),
        patch(
            "foldrun_app.models.af2.tools.submit_monomer.AF2SubmitMonomerTool.run",
            _fake_af2_run,
        ),
        patch(
            "foldrun_app.models.af2.tools.submit_multimer.AF2SubmitMultimerTool.__init__",
            _fake_af2_init,
        ),
        patch(
            "foldrun_app.models.af2.tools.submit_multimer.AF2SubmitMultimerTool.run",
            _fake_af2_run,
        ),
        patch(
            "foldrun_app.models.af3.tools.submit_prediction.AF3SubmitPredictionTool.__init__",
            _fake_af2_init,
        ),
        patch(
            "foldrun_app.models.af3.tools.submit_prediction.AF3SubmitPredictionTool.run",
            _fake_af3_run,
        ),
        patch("foldrun_app.gateway.router._get_optional_binder_config", _fake_cfg),
        patch(
            "foldrun_app.gateway.router._get_optional_binder_submit_tool",
            _fake_optional_binder_submit_tool,
        ),
        patch("foldrun_app.gateway.router.get_pipeline_job", _fake_get_pipeline_job),
        patch("foldrun_app.gateway.router.list_pipeline_jobs", _fake_list_pipeline_jobs),
        patch("foldrun_app.gateway.router._trigger_analysis_if_needed_async", lambda *a, **k: None),
        patch(
            "foldrun_app.gateway.router.generate_v4_signed_download_url",
            lambda storage_client, archive_gcs_uri, **kw: (
                f"http://testserver/v1/jobs/{archive_gcs_uri.split('/')[4]}/outputs?stream=true"
            ),
        ),
        patch("foldrun_app.gateway.router.BOLTZ2Config", _fake_cfg),
        patch("foldrun_app.gateway.router.OF3Config", _fake_cfg),
        patch("foldrun_app.gateway.router.AF2Config", _fake_cfg),
        patch("foldrun_app.gateway.router.AF3Config", _fake_cfg),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        app = create_gateway_app()
        client = TestClient(app)
        yield env, client


def test_optional_binder_models_return_501_when_package_not_installed(monkeypatch):
    """Verify generative binder endpoints return HTTP 501 cleanly when optional package is absent."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    app = create_gateway_app()
    client = TestClient(app)

    with patch("importlib.util.find_spec", return_value=None):
        resp_bg = client.post(
            "/v1/boltzgen/jobs",
            files={"input_file": ("spec.yaml", b"entities: []\n", "text/yaml")},
        )
        assert resp_bg.status_code == 501
        assert "generative binder models package" in resp_bg.json()["detail"]

        resp_rfd4 = client.post(
            "/v1/rfd4_proteina/jobs",
            files={"input_file": ("spec.yaml", b"design_mode: binder_design\n", "text/yaml")},
        )
        assert resp_rfd4.status_code == 501

        resp_bc = client.post(
            "/v1/bindcraft/jobs",
            files={"input_file": ("settings.json", b'{"chains": "A"}', "application/json")},
        )
        assert resp_bc.status_code == 501


class TestGatewayEndToEndCUJs:
    """Verify CUJs 1-4: Native CLI & Jupyter Notebook execution across all 7 models."""

    def test_cuj1_boltz_cli_with_custom_msa_and_affinity(self, tmp_path: Path, mock_gateway_env):
        env, test_client = mock_gateway_env
        msa_dir = tmp_path / "msas"
        msa_dir.mkdir()
        (msa_dir / "custom.a3m").write_text(">proteinA\nMVTPEGNVSL\n", encoding="utf-8")

        yaml_file = tmp_path / "Affinity_Target.yaml"
        yaml_file.write_text(
            """version: 1
sequences:
  - protein:
      id: A
      sequence: MVTPEGNVSL
      msa: ./msas/custom.a3m
  - ligand:
      id: B
      smiles: 'CC(=O)OC1=CC=CC=C1C(=O)O'
properties:
  - affinity:
      binder: B
""",
            encoding="utf-8",
        )
        out_dir = tmp_path / "results"

        rc = boltz_main(
            [
                "predict",
                str(yaml_file),
                "--use_potentials",
                "--diffusion_samples",
                "2",
                "--out_dir",
                str(out_dir),
            ],
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert rc == 0
        assert env.submit_count == 1

        pred_dir = out_dir / "boltz_results_Affinity_Target" / "predictions" / "Affinity_Target"
        assert (pred_dir / "Affinity_Target_model_0.cif").exists()
        assert (pred_dir / "confidence_Affinity_Target_model_0.json").exists()
        assert (pred_dir / "affinity_Affinity_Target.json").exists()
        assert (out_dir / ".foldrun_job.json").exists()

    def test_cuj2_jupyter_notebook_interrupt_resume_and_failed_job_invalidation(
        self, tmp_path: Path, mock_gateway_env
    ):
        env, test_client = mock_gateway_env
        yaml_file = tmp_path / "notebook_query.yaml"
        yaml_file.write_text(
            """version: 1
sequences:
  - protein:
      id: A
      sequence: MVTPEGNVSL
      msa: empty
""",
            encoding="utf-8",
        )
        out_dir = tmp_path / "nb_out"

        res1 = boltz.predict(
            yaml_file,
            out_dir=out_dir,
            use_potentials=True,
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert env.submit_count == 1
        assert res1.deduplicated is False
        assert "FoldRun Cloud Prediction" in res1._repr_html_()

        res2 = boltz.predict(
            yaml_file,
            out_dir=out_dir,
            use_potentials=True,
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert env.submit_count == 1
        assert res2.job_id == res1.job_id
        assert res2.deduplicated is True

        env.jobs[res1.job_id].state = SimpleNamespace(name="PIPELINE_STATE_FAILED")
        res3 = boltz.predict(
            yaml_file,
            out_dir=out_dir,
            use_potentials=True,
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert env.submit_count == 2
        assert res3.job_id != res1.job_id

    def test_cuj3_openfold3_and_alphafold2_cli_and_notebook_helpers(
        self, tmp_path: Path, mock_gateway_env
    ):
        _env, test_client = mock_gateway_env

        (tmp_path / "chainA.a3m").write_text(">A\nMVTPEGNVSL\n", encoding="utf-8")
        query_json = tmp_path / "of3_query.json"
        query_json.write_text(
            json.dumps(
                {
                    "queries": {
                        "target_1": {
                            "chains": [
                                {
                                    "molecule_type": "protein",
                                    "chain_ids": ["A"],
                                    "sequence": "MVTPEGNVSL",
                                    "main_msa_file_paths": ["chainA.a3m"],
                                }
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        runner_yaml = tmp_path / "runner.yaml"
        runner_yaml.write_text("low_mem: true\nplddt: true\n", encoding="utf-8")
        of3_out = tmp_path / "of3_out"

        rc_of3 = openfold3_main(
            [
                "predict",
                f"--query_json={query_json}",
                f"--runner_yaml={runner_yaml}",
                f"--output_dir={of3_out}",
                "--num_diffusion_samples=3",
            ],
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert rc_of3 == 0
        assert (of3_out / "of3_query" / "seed_42" / "of3_query_seed_42_model.cif").exists()

        f1 = tmp_path / "seq1.fasta"
        f2 = tmp_path / "seq2.fasta"
        f1.write_text(">seq1\nMVTPEGNVSL\n", encoding="utf-8")
        f2.write_text(">seq2\nGGGGGGGGGG\n", encoding="utf-8")
        af2_out = tmp_path / "af2_out"

        rc_af2 = alphafold2_main(
            [
                f"--fasta_paths={f1},{f2}",
                f"--output_dir={af2_out}",
                "--data_dir=/local/ignored/databases",
                "--uniref90_database_path=/local/ignored/uniref90.fasta",
                "--model_preset=monomer",
                "--max_template_date=2022-01-01",
            ],
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert rc_af2 == 0
        assert (af2_out / "seq1" / "ranked_0.pdb").exists()
        assert (af2_out / "seq1" / "ranking_debug.json").exists()
        assert (af2_out / "seq2" / "ranked_0.pdb").exists()

    def test_cuj4_generative_binder_models_cli_and_notebook_helpers(
        self, tmp_path: Path, mock_gateway_env
    ):
        _env, test_client = mock_gateway_env

        (tmp_path / "custom_target.cif").write_text("data_target\n", encoding="utf-8")
        bg_yaml = tmp_path / "pdl1_binder.yaml"
        bg_yaml.write_text(
            """entities:
  - protein:
      id: B
      sequence: 80..120
  - file:
      path: ./custom_target.cif
      include:
        - chain:
            id: A
""",
            encoding="utf-8",
        )
        bg_out = tmp_path / "boltzgen_out"

        assert boltzgen_main(["check", str(bg_yaml)]) == 0
        rc_bg = boltzgen_main(
            [
                "run",
                str(bg_yaml),
                "--output",
                str(bg_out),
                "--protocol",
                "protein-anything",
                "--num_designs",
                "4",
                "--budget",
                "2",
                "--num_model_seeds",
                "5",
                "--cache",
                "/ignored/local/cache",
            ],
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert rc_bg == 0
        assert (bg_out / "pdl1_binder" / "final_ranked_designs" / "ranked_0.cif").exists()
        assert (bg_out / "pdl1_binder" / "final_ranked_designs" / "metrics.csv").exists()
        assert (bg_out / "pdl1_binder" / "predictions" / "design_seed42_0.cif").exists()

        res_bg_nb = boltzgen.predict(
            bg_yaml,
            out_dir=tmp_path / "boltzgen_nb_out",
            protocol="protein-anything",
            num_designs=4,
            budget=2,
            num_model_seeds=5,
            seed=42,
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert res_bg_nb.model == "boltzgen"

        rfd4_spec = tmp_path / "pdl1_binder_design.yaml"
        rfd4_spec.write_text(
            """design_mode: binder_design
target_structure: 4Z18
target_chains:
  - A
binding_residues:
  - A54
  - A56
binder_length_range: 80..120
""",
            encoding="utf-8",
        )
        rfd4_out = tmp_path / "rfd4_out"
        rc_rfd4 = rfd4_proteina_main(
            [
                f"inputs={rfd4_spec}",
                f"out_dir={rfd4_out}",
                "hf_ckpt_path=/ignored/local/ckpt",
                "num_replicates=4",
                "seed=42",
                "--num_model_seeds=5",
                "~metrics",
            ],
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert rc_rfd4 == 0
        assert (rfd4_out / "pdl1_binder_design" / "ranked_designs" / "ranked_0.cif").exists()
        assert (rfd4_out / "pdl1_binder_design" / "ranked_designs" / "metrics.csv").exists()

        res_rfd4_nb = rfd4_proteina.predict(
            rfd4_spec,
            out_dir=tmp_path / "rfd4_nb_out",
            design_mode="binder_design",
            num_designs_per_seed=4,
            num_model_seeds=5,
            seed=42,
            gpu_type="auto",
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert res_rfd4_nb.model == "rfd4_proteina"
        assert res_rfd4_nb.job_id.startswith("rfd4-proteina-gw-")
        assert "_" not in res_rfd4_nb.job_id
        assert re.match(r"^[a-z][-a-z0-9]{0,127}$", res_rfd4_nb.job_id) is not None

        (tmp_path / "target_pdl1.pdb").write_text("HEADER PDL1\nEND\n", encoding="utf-8")
        bc_settings = tmp_path / "pdl1_settings.json"
        bc_settings.write_text(
            json.dumps(
                {
                    "design_path": "./bindcraft_out",
                    "binder_name": "PDL1_Binder",
                    "starting_pdb": "./target_pdl1.pdb",
                    "chains": "A",
                    "target_hotspot_residues": "54,56,66",
                    "lengths": [80, 120],
                    "number_of_final_designs": 2,
                }
            ),
            encoding="utf-8",
        )
        bc_out = tmp_path / "bindcraft_out"
        rc_bc = bindcraft_main(
            [
                f"--settings={bc_settings}",
                "--filters=default_filters.json",
                "--advanced=default_4stage_multimer.json",
                "--no-pyrosetta",
                f"--out_dir={bc_out}",
                "--num_designs_per_seed=2",
                "--num_model_seeds=5",
            ],
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert rc_bc == 0
        assert (bc_out / "pdl1_settings" / "Accepted" / "ranked_0.cif").exists()
        assert (bc_out / "pdl1_settings" / "Accepted" / "final_design_stats.csv").exists()

        res_bc_nb = bindcraft.predict(
            bc_settings,
            out_dir=tmp_path / "bindcraft_nb_out",
            num_designs_per_seed=2,
            num_model_seeds=5,
            seed=42,
            gateway_url="http://testserver",
            http_client=test_client,
            poll_interval_sec=0.01,
        )
        assert res_bc_nb.model == "bindcraft"


def test_verbatim_huggingface_rfd4_proteina_pipeline(tmp_path: Path):
    """Verify that the verbatim upstream HuggingFace nvidia/RFD4-Proteina command passes end-to-end."""
    from foldrun_shim.bundler import bundle_local_inputs
    from foldrun_shim.cli import _strip_local_rfd4_proteina_args

    from foldrun_app.gateway.flag_allowlist import _validate_rfd4_proteina_flags
    from foldrun_app.gateway.staging import _rewrite_rfd4_proteina_spec

    hf_args = [
        "inputs=data/benchmarks/monomer/monomer-short.cif",
        "hf_ckpt_path=nvidia/RFD4-Proteina",
        "hf_ckpt_revision=a06e2509c7033b010fc43614e9fd840303588502",
        "num_replicates=1",
        "out_dir=./runs/first",
        "~metrics",
        "compile_lengths=null",
    ]

    stripped = _strip_local_rfd4_proteina_args(hf_args)
    input_path, out_dir, override, sci_args = stripped
    assert input_path == "data/benchmarks/monomer/monomer-short.cif"
    assert out_dir == "./runs/first"
    assert not override
    assert stripped.gpu_type is None
    assert "hf_ckpt_path=nvidia/RFD4-Proteina" not in sci_args
    assert "hf_ckpt_revision=a06e2509c7033b010fc43614e9fd840303588502" not in sci_args

    bundled = bundle_local_inputs(
        model="rfd4_proteina",
        input_path=input_path,
        cli_args=sci_args,
    )
    assert bundled.model == "rfd4_proteina"
    assert bundled.input_filename == "monomer-short.cif"
    spec_data = json.loads(bundled.primary_bytes.decode("utf-8"))
    assert spec_data == {"benchmark_input": "data/benchmarks/monomer/monomer-short.cif"}

    val_flags = _validate_rfd4_proteina_flags(sci_args)
    assert val_flags.pipeline_params["num_designs_per_seed"] == 1
    assert val_flags.pipeline_params["compute_metrics"] is False
    assert "~metrics" not in val_flags.extra_cli_args
    assert "compile_lengths=null" in val_flags.extra_cli_args

    rewritten_spec = _rewrite_rfd4_proteina_spec(
        bundled.primary_bytes.decode("utf-8"),
        bundled.assets_map,
        gcs_uri_assets_prefix="gs://test-bucket/assets",
    )
    assert json.loads(rewritten_spec) == {
        "benchmark_input": "data/benchmarks/monomer/monomer-short.cif"
    }


def test_rfd4_proteina_job_id_sanitization_for_vertex_pipelines(mock_gateway_env, tmp_path: Path):
    """Verify submit_model_job for rfd4_proteina generates a job_id with hyphens matching ^[a-z][-a-z0-9]{0,127}$."""
    _env, test_client = mock_gateway_env
    rfd4_spec = tmp_path / "pdl1_binder_design.yaml"
    rfd4_spec.write_text(
        """design_mode: binder_design
target_structure: 4Z18
target_chains:
  - A
binding_residues:
  - A54
  - A56
binder_length_range: 80..120
""",
        encoding="utf-8",
    )
    res = rfd4_proteina.predict(
        rfd4_spec,
        out_dir=tmp_path / "rfd4_out",
        design_mode="binder_design",
        gateway_url="http://testserver",
        http_client=test_client,
        poll_interval_sec=0.01,
    )
    assert res.job_id.startswith("rfd4-proteina-gw-")
    assert "_" not in res.job_id
    assert re.match(r"^[a-z][-a-z0-9]{0,127}$", res.job_id) is not None


def test_cuj4_alphafold3_cli_and_notebook_with_zst_outputs(mock_gateway_env, tmp_path: Path):
    """Verify AlphaFold 3 CLI and Notebook SDK against Gateway with transparent .zst output decompression."""
    _env, test_client = mock_gateway_env
    af3_json = tmp_path / "pdl1_af3.json"
    af3_json.write_text(
        json.dumps(
            {
                "name": "pdl1_af3",
                "modelSeeds": [1, 2],
                "sequences": [{"protein": {"id": "A", "sequence": "MVTPEGNVSL"}}],
                "dialect": "alphafold3",
                "version": 1,
            }
        ),
        encoding="utf-8",
    )

    cli_out = tmp_path / "af3_cli_out"
    rc = alphafold3_main(
        [
            f"--json_path={af3_json}",
            f"--output_dir={cli_out}",
            "--num_diffusion_samples=5",
        ],
        gateway_url="http://testserver",
        http_client=test_client,
        poll_interval_sec=0.01,
    )
    assert rc == 0
    cif_files = list(cli_out.rglob("*.cif"))
    assert len(cif_files) == 1
    assert cif_files[0].read_text(encoding="utf-8") == "data_af3_structure\n"
    summary_files = list(cli_out.rglob("summary_confidences.json"))
    assert len(summary_files) == 1
    assert json.loads(summary_files[0].read_text(encoding="utf-8"))["ranking_score"] == 0.93

    nb_out = tmp_path / "af3_nb_out"
    res = alphafold3.predict(
        af3_json,
        output_dir=nb_out,
        model_seeds=[1, 2],
        num_diffusion_samples=5,
        override=True,
        gateway_url="http://testserver",
        http_client=test_client,
        poll_interval_sec=0.01,
    )
    assert res.model == "alphafold3"
    assert res.job_id.startswith("alphafold3-inference-pipeline-")
    assert len(res.structures) == 1
