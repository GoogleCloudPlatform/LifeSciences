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

"""Unit tests for AlphaFold 3 Gateway flag allowlist, async KFP submission, and packaging."""

import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import zstandard as zstd
from fastapi.testclient import TestClient

from foldrun_app.gateway.app import create_gateway_app
from foldrun_app.gateway.flag_allowlist import (
    FlagValidationError,
    validate_and_partition_flags,
)
from foldrun_app.gateway.output_packager import build_or_get_native_outputs_archive
from foldrun_app.gateway.staging import compute_canonical_input_sha256

from .test_staging_and_packaging import FakeStorageClient


def test_af3_valid_flags():
    flags = [
        "--num_diffusion_samples=2",
        "--num_recycles=5",
        "--num_seeds=2",
        "--run_data_pipeline=false",
        "--max_template_date=2022-01-01",
    ]
    vf = validate_and_partition_flags("alphafold3", flags)
    assert vf.pipeline_params["num_diffusion_samples"] == 2
    assert vf.pipeline_params["num_recycles"] == 5
    assert vf.pipeline_params["run_data_pipeline"] is False
    assert vf.pipeline_params["max_template_date"] == "2022-01-01"


def test_af3_alias_flags():
    flags = [
        "--num_diffusion_samples=1",
        "--conformer_max_iterations=50",
    ]
    vf = validate_and_partition_flags("af3", flags)
    assert vf.pipeline_params["num_diffusion_samples"] == 1
    assert vf.pipeline_params["conformer_max_iterations"] == 50


def test_af3_invalid_flags():
    with pytest.raises(FlagValidationError):
        validate_and_partition_flags("alphafold3", ["--num_diffusion_samples=50"])

    with pytest.raises(FlagValidationError):
        validate_and_partition_flags("alphafold3", ["--malicious_flag=true"])


def test_af3_staging_sha256():
    primary_bytes = b'{"name": "test", "dialect": "alphafold3"}'
    sha1 = compute_canonical_input_sha256("alphafold3", primary_bytes, {}, {})
    sha2 = compute_canonical_input_sha256("alphafold3", primary_bytes, {}, {})
    assert sha1 == sha2
    assert len(sha1) == 64


def test_af3_gateway_async_kfp_submit_and_raw_output_packaging(monkeypatch):
    """Verify upstream AF3SubmitPredictionTool(sync=False) contract and af3_raw/ packaging."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("AF3_ENDPOINT", "auto")

    gcs_store: dict[str, bytes] = {}
    storage_client = FakeStorageClient(gcs_store)
    captured_args: list[dict] = []

    def _fake_af3_init(self, tool_config, config=None):
        self.tool_config = tool_config
        self.config = config
        self.storage_client = storage_client

    def _fake_af3_run(self, arguments):
        captured_args.append(arguments)
        job_name = arguments["job_name"]
        run_id = "alphafold3-inference-pipeline-20261027183000"
        cctx = zstd.ZstdCompressor()
        gcs_store[f"af3_predictions/{job_name}/input.json"] = b'{"dialect": "alphafold3"}'
        gcs_store[f"af3_predictions/{job_name}/analysis/summary.json"] = b'{"status": "ok"}'
        gcs_store[f"af3_predictions/{job_name}/af3_raw/seed-1_sample-0/model.cif.zst"] = (
            cctx.compress(b"data_upstream_af3\n")
        )
        gcs_store[
            f"af3_predictions/{job_name}/af3_raw/seed-1_sample-0/summary_confidences.json.zst"
        ] = cctx.compress(b'{"ranking_score": 0.95}\n')
        return {
            "status": "submitted",
            "job_id": run_id,
            "pipeline_job_resource": (
                f"projects/test-proj/locations/us-central1/pipelineJobs/{run_id}"
            ),
            "gcs_output_dir": f"gs://test-bucket/af3_predictions/{job_name}",
            "pipeline_root": "gs://test-bucket/pipeline_runs/20261027_183000",
        }

    with (
        patch(
            "foldrun_app.models.af3.tools.submit_prediction.AF3SubmitPredictionTool.__init__",
            _fake_af3_init,
        ),
        patch(
            "foldrun_app.models.af3.tools.submit_prediction.AF3SubmitPredictionTool.run",
            _fake_af3_run,
        ),
        patch("foldrun_app.gateway.router.list_pipeline_jobs", return_value=[]),
    ):
        app = create_gateway_app()
        client = TestClient(app)
        resp = client.post(
            "/v1/alphafold3/jobs",
            files={
                "input_file": (
                    "pdl1_complex.json",
                    b'{"name": "pdl1", "modelSeeds": [1, 2], "sequences": [{"protein": {"id": "A", "sequence": "MVTPEGNVSL"}}], "dialect": "alphafold3", "version": 1}',
                    "application/json",
                )
            },
            data={
                "input_filename": "pdl1_complex.json",
                "params": json.dumps(
                    {
                        "cli_args": [
                            "--num_diffusion_samples=3",
                            "--run_data_pipeline=false",
                        ]
                    }
                ),
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["job_id"] == "alphafold3-inference-pipeline-20261027183000"
        assert len(captured_args) == 1
        assert captured_args[0]["sync"] is False
        assert captured_args[0]["msa_free"] is True
        assert captured_args[0]["num_diffusion_samples"] == 3
        assert "model_seeds" not in captured_args[0]

        run_id = body["job_id"]
        assert f"gateway_jobs/{run_id}/manifest.json" in gcs_store
        fake_job = SimpleNamespace(
            name=body["pipeline_job_resource_name"],
            labels={"model_type": "alphafold3", "query_name": captured_args[0]["job_name"]},
            runtime_config=SimpleNamespace(
                gcs_output_directory="gs://test-bucket/pipeline_runs/20261027_183000",
                parameter_values={},
            ),
            job_detail=SimpleNamespace(task_details=[]),
        )
        archive_uri = build_or_get_native_outputs_archive(
            storage_client=storage_client,
            bucket_name="test-bucket",
            job_id=run_id,
            model="alphafold3",
            job=fake_job,
        )
        archive_blob_path = archive_uri.replace("gs://test-bucket/", "")
        with tarfile.open(fileobj=io.BytesIO(gcs_store[archive_blob_path]), mode="r:gz") as tf:
            names = set(tf.getnames())
            assert "pdl1_complex/seed-1_sample-0/model.cif" in names
            assert "pdl1_complex/seed-1_sample-0/summary_confidences.json" in names
            assert "pdl1_complex/input.json" not in names
            cif_bytes = tf.extractfile("pdl1_complex/seed-1_sample-0/model.cif").read()
            assert cif_bytes == b"data_upstream_af3\n"
