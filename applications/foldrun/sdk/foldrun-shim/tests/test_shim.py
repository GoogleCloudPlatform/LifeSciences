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

"""Unit tests for foldrun_shim.check_gpus() and `foldrun gpus` CLI."""

import io

import httpx
import pytest

import foldrun_shim
from foldrun_shim.cli import foldrun_main
from foldrun_shim.client import GatewayClient, GatewayGPUStatus


@pytest.fixture
def sample_gpu_data():
    return {
        "project_id": "test-project",
        "region": "us-central1",
        "tiers": [
            {
                "gpu_type": "L4",
                "accelerator_type": "NVIDIA_L4",
                "machine_type": "g2-standard-12",
                "vram_gb": 24,
                "on_demand_quota": 8,
                "flex_start_quota": 8,
                "running_on_demand": 0,
                "queued_on_demand": 0,
                "running_flex_start": 0,
                "queued_flex_start": 0,
                "status": "AVAILABLE",
                "typical_use": "Small-medium proteins (<500 residues)",
            },
            {
                "gpu_type": "A100",
                "accelerator_type": "NVIDIA_TESLA_A100",
                "machine_type": "a2-highgpu-1g",
                "vram_gb": 40,
                "on_demand_quota": 1,
                "flex_start_quota": 16,
                "running_on_demand": 0,
                "queued_on_demand": 0,
                "running_flex_start": 0,
                "queued_flex_start": 0,
                "status": "AVAILABLE",
                "typical_use": "Medium-large proteins",
            },
            {
                "gpu_type": "A100_80GB",
                "accelerator_type": "NVIDIA_A100_80GB",
                "machine_type": "a2-ultragpu-1g",
                "vram_gb": 80,
                "on_demand_quota": 0,
                "flex_start_quota": 0,
                "running_on_demand": 0,
                "queued_on_demand": 0,
                "running_flex_start": 0,
                "queued_flex_start": 0,
                "status": "NO_QUOTA",
                "typical_use": "Large complexes",
            },
        ],
        "active_pipeline_jobs": 0,
        "recommended_gpu_type": "L4",
        "recommended_enable_flex_start": True,
        "recommendation_reason": "Zero queued tasks detected across all GPU tiers.",
    }


def test_gateway_gpu_status_rendering(sample_gpu_data):
    status = GatewayGPUStatus(
        project_id=sample_gpu_data["project_id"],
        region=sample_gpu_data["region"],
        tiers=sample_gpu_data["tiers"],
        active_pipeline_jobs=sample_gpu_data["active_pipeline_jobs"],
        recommended_gpu_type=sample_gpu_data["recommended_gpu_type"],
        recommended_enable_flex_start=sample_gpu_data["recommended_enable_flex_start"],
        recommendation_reason=sample_gpu_data["recommendation_reason"],
    )

    # Verify plain-text string representation
    text_out = str(status)
    assert "FoldRun GPU Availability & Regional Quotas" in text_out
    assert "test-project" in text_out
    assert "us-central1" in text_out
    assert "L4" in text_out
    assert "A100" in text_out
    assert "A100_80GB" in text_out
    assert 'os.environ["FOLDRUN_GPU_TYPE"] = "L4"' in text_out
    assert 'os.environ["FOLDRUN_ENABLE_FLEX_START"] = "true"' in text_out

    # Verify HTML representation
    html_out = status._repr_html_()
    assert "<table" in html_out
    assert "g2-standard-12" in html_out
    assert "AVAILABLE" in html_out
    assert "NO_QUOTA" in html_out
    assert "Zero queued tasks detected" in html_out


def test_check_gpus_client(sample_gpu_data):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/gpus"
        return httpx.Response(200, json=sample_gpu_data)

    mock_transport = httpx.MockTransport(handler)
    mock_client = httpx.Client(transport=mock_transport, base_url="http://testserver")

    status = foldrun_shim.check_gpus(gateway_url="http://testserver", http_client=mock_client)
    assert status.project_id == "test-project"
    assert status.region == "us-central1"
    assert len(status.tiers) == 3
    assert status.recommended_gpu_type == "L4"
    assert status.recommended_enable_flex_start is True


def test_foldrun_gpus_cli(sample_gpu_data, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/gpus"
        return httpx.Response(200, json=sample_gpu_data)

    mock_transport = httpx.MockTransport(handler)
    mock_client = httpx.Client(transport=mock_transport, base_url="http://testserver")

    exit_code = foldrun_main(
        ["gpus"],
        gateway_url="http://testserver",
        http_client=mock_client,
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "FoldRun GPU Availability & Regional Quotas" in captured.out
    assert "test-project" in captured.out
    assert "L4" in captured.out
    assert "A100" in captured.out


def test_proactive_preflight_fallback(sample_gpu_data, tmp_path):
    queued_gpu_data = dict(sample_gpu_data)
    queued_gpu_data["recommended_enable_flex_start"] = False
    queued_gpu_data["recommended_gpu_type"] = "L4"
    queued_gpu_data["recommendation_reason"] = "DWS Flex Start has 5 queued task(s)."
    queued_gpu_data["tiers"] = [dict(t) for t in sample_gpu_data["tiers"]]
    queued_gpu_data["tiers"][0]["queued_flex_start"] = 5
    queued_gpu_data["tiers"][0]["status"] = "QUEUED"

    submitted_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/gpus":
            return httpx.Response(200, json=queued_gpu_data)
        elif request.url.path == "/v1/alphafold2/jobs":
            body = request.read().decode("utf-8", errors="ignore")
            import json as _json

            for part in body.split('name="params"'):
                if len(part) > 1 and "\r\n\r\n" in part:
                    raw_val = part.split("\r\n\r\n", 1)[1].split("\r\n", 1)[0]
                    try:
                        submitted_params.append(_json.loads(raw_val))
                    except Exception:
                        pass
            return httpx.Response(
                202,
                json={
                    "job_id": "af2-job-123",
                    "input_sha256": "abc",
                    "state": "PIPELINE_STATE_RUNNING",
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-123":
            return httpx.Response(
                200,
                json={
                    "job_id": "af2-job-123",
                    "state": "PIPELINE_STATE_SUCCEEDED",
                    "current_step": "relax",
                    "progress_percent": 100.0,
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-123/outputs":
            if request.url.params.get("stream") == "true":
                buf = io.BytesIO()
                import tarfile as _tarfile

                with _tarfile.open(fileobj=buf, mode="w:gz") as tf:
                    data = b"MODEL_PDB"
                    ti = _tarfile.TarInfo(name="ranked_0.pdb")
                    ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
                return httpx.Response(200, content=buf.getvalue())
            return httpx.Response(200, json={"download_url": None})
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(
        gateway_url="http://testserver",
        http_client=mock_client,
        poll_interval_sec=0.01,
        stream_outputs=True,
    )

    fasta_file = tmp_path / "test.fasta"
    fasta_file.write_text(">seq\nACDEF\n")

    res = client.run_prediction(
        model="alphafold2",
        input_path=fasta_file,
        out_dir=tmp_path / "out",
        gpu_type="L4",
        enable_flex_start=None,
        fallback_to_ondemand=True,
    )
    assert res.fallback_triggered is True
    assert res.state == "PIPELINE_STATE_SUCCEEDED"
    assert len(submitted_params) == 1
    assert submitted_params[0]["enable_flex_start"] is False


def test_inflight_queue_timeout_fallback(sample_gpu_data, tmp_path):
    submitted_jobs = []
    cancelled_jobs = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        if request.url.path == "/v1/gpus":
            return httpx.Response(200, json=sample_gpu_data)
        elif request.url.path == "/v1/alphafold2/jobs":
            body = request.read().decode("utf-8", errors="ignore")
            param_dict = {}
            for part in body.split('name="params"'):
                if len(part) > 1 and "\r\n\r\n" in part:
                    raw_val = part.split("\r\n\r\n", 1)[1].split("\r\n", 1)[0]
                    try:
                        param_dict = _json.loads(raw_val)
                    except Exception:
                        pass
            job_idx = len(submitted_jobs) + 1
            job_id = f"af2-job-{job_idx}"
            submitted_jobs.append((job_id, param_dict))
            return httpx.Response(
                202,
                json={
                    "job_id": job_id,
                    "input_sha256": "abc",
                    "state": "PIPELINE_STATE_RUNNING",
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-1":
            return httpx.Response(
                200,
                json={
                    "job_id": "af2-job-1",
                    "state": "PIPELINE_STATE_RUNNING",
                    "execution_phase": "QUEUED_GPU",
                    "queued_gpu_tasks": 1,
                    "max_queue_wait_sec": 200.0,
                    "current_step": "predict (queued)",
                    "progress_percent": 30.0,
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-1/cancel":
            cancelled_jobs.append("af2-job-1")
            return httpx.Response(
                200,
                json={
                    "job_id": "af2-job-1",
                    "state": "CANCELLED",
                    "cancelled_custom_jobs": [],
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-2":
            return httpx.Response(
                200,
                json={
                    "job_id": "af2-job-2",
                    "state": "PIPELINE_STATE_SUCCEEDED",
                    "current_step": "relax",
                    "progress_percent": 100.0,
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-2/outputs":
            if request.url.params.get("stream") == "true":
                buf = io.BytesIO()
                import tarfile as _tarfile

                with _tarfile.open(fileobj=buf, mode="w:gz") as tf:
                    data = b"MODEL_PDB"
                    ti = _tarfile.TarInfo(name="ranked_0.pdb")
                    ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
                return httpx.Response(200, content=buf.getvalue())
            return httpx.Response(200, json={"download_url": None})
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(
        gateway_url="http://testserver",
        http_client=mock_client,
        poll_interval_sec=0.01,
        stream_outputs=True,
    )

    fasta_file = tmp_path / "test.fasta"
    fasta_file.write_text(">seq\nACDEF\n")

    res = client.run_prediction(
        model="alphafold2",
        input_path=fasta_file,
        out_dir=tmp_path / "out",
        gpu_type="L4",
        enable_flex_start=True,
        queue_timeout_sec=150.0,
        fallback_to_ondemand=True,
    )
    assert res.fallback_triggered is True
    assert res.state == "PIPELINE_STATE_SUCCEEDED"
    assert "af2-job-1" in cancelled_jobs
    assert len(submitted_jobs) == 2
    assert submitted_jobs[0][1]["enable_flex_start"] is True
    assert submitted_jobs[1][1]["enable_flex_start"] is False


def test_disabled_fallback_raises_timeout_error(sample_gpu_data, tmp_path):
    cancelled_jobs = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/gpus":
            return httpx.Response(200, json=sample_gpu_data)
        elif request.url.path == "/v1/alphafold2/jobs":
            return httpx.Response(
                202,
                json={
                    "job_id": "af2-job-timeout",
                    "input_sha256": "abc",
                    "state": "PIPELINE_STATE_RUNNING",
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-timeout":
            return httpx.Response(
                200,
                json={
                    "job_id": "af2-job-timeout",
                    "state": "PIPELINE_STATE_RUNNING",
                    "execution_phase": "QUEUED_GPU",
                    "queued_gpu_tasks": 1,
                    "max_queue_wait_sec": 200.0,
                    "current_step": "predict (queued)",
                },
            )
        elif request.url.path == "/v1/jobs/af2-job-timeout/cancel":
            cancelled_jobs.append("af2-job-timeout")
            return httpx.Response(
                200,
                json={
                    "job_id": "af2-job-timeout",
                    "state": "CANCELLED",
                    "cancelled_custom_jobs": [],
                },
            )
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(
        gateway_url="http://testserver", http_client=mock_client, poll_interval_sec=0.01
    )

    fasta_file = tmp_path / "test.fasta"
    fasta_file.write_text(">seq\nACDEF\n")

    with pytest.raises(RuntimeError) as exc_info:
        client.run_prediction(
            model="alphafold2",
            input_path=fasta_file,
            out_dir=tmp_path / "out",
            gpu_type="L4",
            enable_flex_start=True,
            queue_timeout_sec=150.0,
            fallback_to_ondemand=False,
        )
    assert "queue wait time exceeded timeout" in str(exc_info.value)
    assert "af2-job-timeout" in cancelled_jobs


def test_cli_fallback_flags():
    from foldrun_shim.cli import (
        _strip_local_alphafold2_args,
        _strip_local_alphafold3_args,
        _strip_local_boltz_args,
        _strip_local_openfold3_args,
    )

    b_args = _strip_local_boltz_args(["predict", "input.yaml", "--fallback-to-ondemand"])
    assert b_args.fallback_to_ondemand is True

    b_no_args = _strip_local_boltz_args(["predict", "input.yaml", "--no-fallback-to-ondemand"])
    assert b_no_args.fallback_to_ondemand is False

    of3_args = _strip_local_openfold3_args(["--query_json=q.json", "--fallback-to-ondemand"])
    assert of3_args.fallback_to_ondemand is True

    af2_args = _strip_local_alphafold2_args(["--fasta_paths=f.fasta", "--no-fallback-to-ondemand"])
    assert af2_args.fallback_to_ondemand is False

    af3_args = _strip_local_alphafold3_args(["--json_path=q.json", "--fallback-to-ondemand"])
    assert af3_args.fallback_to_ondemand is True
