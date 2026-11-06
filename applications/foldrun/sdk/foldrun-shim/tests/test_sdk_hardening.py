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

"""Comprehensive unit tests for foldrun-shim SDK, CLI, bundler, auth, and notebooks."""

import ast
import base64
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

import foldrun_shim
from foldrun_shim import (
    FoldRunAuthError,
    alphafold2,
    bindcraft,
    invalidate_token_cache,
    rfd4_proteina,
)
from foldrun_shim.auth import (
    _is_token_expired,
    _load_dotenv_fallback,
    fetch_gateway_headers,
)
from foldrun_shim.bundler import bundle_local_inputs, compute_client_input_sha256
from foldrun_shim.cli import (
    _strip_local_alphafold2_args,
    _strip_local_alphafold3_args,
    _strip_local_bindcraft_args,
    _strip_local_rfd4_proteina_args,
    alphafold2_main,
    rfd4_proteina_main,
)
from foldrun_shim.client import (
    _RFD4_FAST_MODE_NOTICE,
    GatewayClient,
    GatewayJobResult,
)

SDK_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS_DIR = SDK_ROOT / "notebooks"


def _make_jwt(payload: dict[str, Any]) -> str:
    header_b64 = (
        base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode("ascii").rstrip("=")
    )
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    )
    return f"{header_b64}.{payload_b64}.signature"


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _extract_multipart_params(request: httpx.Request) -> dict[str, Any]:
    body = request.read().decode("utf-8", errors="ignore")
    for part in body.split('name="params"'):
        if len(part) > 1 and "\r\n\r\n" in part:
            raw_val = part.split("\r\n\r\n", 1)[1].split("\r\n", 1)[0]
            try:
                return json.loads(raw_val)
            except Exception:
                pass
    return {}


# =============================================================================
# C7: Re-attach to .foldrun_job.json BEFORE check_gpus()
# =============================================================================


def test_reattach_running_job_skips_check_gpus_and_preserves_job(tmp_path):
    fasta = tmp_path / "monomer.fasta"
    fasta.write_text(">seq\nACDEFGHIKLMNPQRSTVWY\n", encoding="utf-8")
    out_dir = tmp_path / "af2_out"
    out_dir.mkdir()

    bundled = bundle_local_inputs("alphafold2", fasta)
    state_file = out_dir / ".foldrun_job.json"
    state_file.write_text(
        json.dumps(
            {
                "job_id": "running-job-777",
                "model": "alphafold2",
                "input_sha256": bundled.client_input_sha256,
                "viewer_url": "https://viewer.example/jobs/running-job-777",
                "gpu_type": "L4",
                "enable_flex_start": True,
                "fallback_triggered": False,
            }
        ),
        encoding="utf-8",
    )

    calls = {"gpus": 0, "submit": 0, "cancel": 0, "status": 0}
    archive_bytes = _make_tar_gz({"monomer/ranked_0.pdb": b"ATOM      1  N   ALA A   1\nEND\n"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/gpus":
            calls["gpus"] += 1
            # Simulate DWS queues appearing while our job is ALREADY running on a GPU
            return httpx.Response(
                200,
                json={
                    "project_id": "proj",
                    "region": "us-central1",
                    "tiers": [
                        {
                            "gpu_type": "L4",
                            "queued_flex_start": 6,
                            "status": "QUEUED",
                            "flex_start_quota": 8,
                        }
                    ],
                    "recommended_gpu_type": "L4",
                    "recommended_enable_flex_start": False,
                    "recommendation_reason": "DWS queue detected",
                },
            )
        if request.url.path == "/v1/alphafold2/jobs":
            calls["submit"] += 1
            return httpx.Response(500, text="Should not submit a duplicate job on re-attach!")
        if request.url.path.endswith("/cancel"):
            calls["cancel"] += 1
            return httpx.Response(500, text="Should never cancel an active job on re-attach!")
        if request.url.path == "/v1/jobs/running-job-777":
            calls["status"] += 1
            if calls["status"] <= 2:
                return httpx.Response(
                    200,
                    json={
                        "job_id": "running-job-777",
                        "state": "PIPELINE_STATE_RUNNING",
                        "current_step": "predict",
                        "progress_percent": 65.0,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "job_id": "running-job-777",
                    "state": "PIPELINE_STATE_SUCCEEDED",
                    "current_step": "relax",
                    "progress_percent": 100.0,
                },
            )
        if request.url.path == "/v1/jobs/running-job-777/outputs":
            if request.url.params.get("stream") == "true":
                return httpx.Response(200, content=archive_bytes)
            return httpx.Response(200, json={"download_url": None})
        return httpx.Response(404)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(
        gateway_url="http://testserver",
        http_client=mock_http,
        poll_interval_sec=0.001,
    )

    res = client.run_prediction(
        model="alphafold2",
        input_path=fasta,
        out_dir=out_dir,
        fallback_to_ondemand=True,
    )

    assert calls["gpus"] == 0
    assert calls["submit"] == 0
    assert calls["cancel"] == 0
    assert res.job_id == "running-job-777"
    assert res.deduplicated is True
    assert (out_dir / "monomer" / "ranked_0.pdb").is_file()


def test_explicit_hardware_change_invalidates_state_file(tmp_path):
    fasta = tmp_path / "monomer.fasta"
    fasta.write_text(">seq\nACDEFG\n", encoding="utf-8")
    out_dir = tmp_path / "af2_out"
    out_dir.mkdir()

    bundled = bundle_local_inputs("alphafold2", fasta)
    (out_dir / ".foldrun_job.json").write_text(
        json.dumps(
            {
                "job_id": "old-l4-job",
                "model": "alphafold2",
                "input_sha256": bundled.client_input_sha256,
                "gpu_type": "L4",
                "enable_flex_start": True,
            }
        ),
        encoding="utf-8",
    )

    submitted_params: list[dict[str, Any]] = []
    archive_bytes = _make_tar_gz({"ranked_0.pdb": b"PDB"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/alphafold2/jobs":
            submitted_params.append(_extract_multipart_params(request))
            return httpx.Response(
                202,
                json={
                    "job_id": "new-a100-job",
                    "input_sha256": bundled.client_input_sha256,
                },
            )
        if request.url.path == "/v1/jobs/new-a100-job":
            return httpx.Response(
                200,
                json={"job_id": "new-a100-job", "state": "PIPELINE_STATE_SUCCEEDED"},
            )
        if request.url.path == "/v1/jobs/new-a100-job/outputs":
            return httpx.Response(200, content=archive_bytes)
        return httpx.Response(404)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(
        gateway_url="http://testserver",
        http_client=mock_http,
        poll_interval_sec=0.001,
        stream_outputs=True,
    )

    res = client.run_prediction(
        model="alphafold2",
        input_path=fasta,
        out_dir=out_dir,
        gpu_type="A100",
        enable_flex_start=False,
    )
    assert res.job_id == "new-a100-job"
    assert len(submitted_params) == 1
    assert submitted_params[0]["gpu_type"] == "A100"
    assert submitted_params[0]["enable_flex_start"] is False


# =============================================================================
# H10: override=True sends both "override": True and "deduplicate": False
# =============================================================================


def test_override_flag_sends_override_true_and_deduplicate_false(tmp_path):
    fasta = tmp_path / "monomer.fasta"
    fasta.write_text(">seq\nACDEFG\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / ".foldrun_job.json").write_text(
        json.dumps({"job_id": "cached-job", "model": "alphafold2", "input_sha256": "any"}),
        encoding="utf-8",
    )

    submitted_params: list[dict[str, Any]] = []
    archive_bytes = _make_tar_gz({"ranked_0.pdb": b"PDB"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/alphafold2/jobs":
            submitted_params.append(_extract_multipart_params(request))
            return httpx.Response(202, json={"job_id": "override-job-99", "input_sha256": "abc"})
        if request.url.path == "/v1/jobs/override-job-99":
            return httpx.Response(
                200,
                json={"job_id": "override-job-99", "state": "PIPELINE_STATE_SUCCEEDED"},
            )
        if request.url.path == "/v1/jobs/override-job-99/outputs":
            return httpx.Response(200, content=archive_bytes)
        return httpx.Response(404)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(
        gateway_url="http://testserver",
        http_client=mock_http,
        poll_interval_sec=0.001,
        stream_outputs=True,
    )
    res = client.run_prediction(
        model="alphafold2",
        input_path=fasta,
        out_dir=out_dir,
        override=True,
        enable_flex_start=False,
    )
    assert res.job_id == "override-job-99"
    assert len(submitted_params) == 1
    assert submitted_params[0]["override"] is True
    assert submitted_params[0]["deduplicate"] is False


# =============================================================================
# C9: Auth Caching, JWT Expiry, 401 Refresh, Impersonation & Polling Backoff
# =============================================================================


def test_jwt_expiry_and_expired_env_token_fallthrough(monkeypatch, caplog):
    invalidate_token_cache()
    expired_jwt = _make_jwt({"exp": int(time.time()) - 120, "email": "user@corp.example"})
    near_expiry_jwt = _make_jwt({"exp": int(time.time()) + 100, "email": "user@corp.example"})
    fresh_jwt = _make_jwt(
        {"exp": int(time.time()) + 3600, "email": "sa@proj.iam.gserviceaccount.com"}
    )

    assert _is_token_expired(expired_jwt) is True
    assert _is_token_expired(near_expiry_jwt) is True
    assert _is_token_expired(fresh_jwt) is False

    monkeypatch.setenv("FOLDRUN_ID_TOKEN", expired_jwt)
    monkeypatch.delenv("FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FOLDRUN_IMPERSONATE_SA", raising=False)

    fetch_calls = []

    def fake_fetch_id_token(_req, audience):
        fetch_calls.append(audience)
        return fresh_jwt

    with patch("google.oauth2.id_token.fetch_id_token", side_effect=fake_fetch_id_token):
        headers = fetch_gateway_headers("https://foldrun-gateway-real.a.run.app")

    assert headers == {"Authorization": f"Bearer {fresh_jwt}"}
    assert fetch_calls == ["https://foldrun-gateway-real.a.run.app"]
    assert any("FOLDRUN_ID_TOKEN is expired" in r.message for r in caplog.records)
    invalidate_token_cache()


def test_oidc_token_caching_and_invalidation(monkeypatch):
    invalidate_token_cache()
    monkeypatch.delenv("FOLDRUN_ID_TOKEN", raising=False)
    monkeypatch.delenv("FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FOLDRUN_IMPERSONATE_SA", raising=False)

    counter = {"n": 0}

    def fake_fetch_id_token(_req, _audience):
        counter["n"] += 1
        return _make_jwt({"exp": int(time.time()) + 3600, "seq": counter["n"]})

    with patch("google.oauth2.id_token.fetch_id_token", side_effect=fake_fetch_id_token):
        h1 = fetch_gateway_headers("https://gw-cache-test.a.run.app")
        h2 = fetch_gateway_headers("https://gw-cache-test.a.run.app")
        h3 = fetch_gateway_headers("https://gw-cache-test.a.run.app/")
        assert h1 == h2 == h3
        assert counter["n"] == 1

        invalidate_token_cache("https://gw-cache-test.a.run.app")
        h4 = fetch_gateway_headers("https://gw-cache-test.a.run.app")
        assert counter["n"] == 2
        assert h4 != h1

    invalidate_token_cache()


def test_gateway_client_401_invalidates_cache_and_retries(monkeypatch):
    invalidate_token_cache()
    monkeypatch.delenv("FOLDRUN_ID_TOKEN", raising=False)
    monkeypatch.delenv("FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT", raising=False)

    tok_stale = _make_jwt({"exp": int(time.time()) + 3600, "ver": "stale"})
    tok_fresh = _make_jwt({"exp": int(time.time()) + 3600, "ver": "fresh"})
    tokens = iter([tok_stale, tok_fresh])

    def fake_fetch_id_token(_req, _aud):
        return next(tokens)

    seen_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_val = request.headers.get("Authorization", "")
        seen_auth_headers.append(auth_val)
        if auth_val == f"Bearer {tok_stale}":
            return httpx.Response(401, json={"detail": "Token revoked"})
        return httpx.Response(
            200,
            json={
                "project_id": "p",
                "region": "us-central1",
                "tiers": [],
                "active_pipeline_jobs": 0,
            },
        )

    mock_http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://gw-401.a.run.app",
    )
    with patch("google.oauth2.id_token.fetch_id_token", side_effect=fake_fetch_id_token):
        client = GatewayClient(gateway_url="https://gw-401.a.run.app", http_client=mock_http)
        status = client.check_gpus()

    assert status.project_id == "p"
    assert seen_auth_headers == [f"Bearer {tok_stale}", f"Bearer {tok_fresh}"]
    invalidate_token_cache()


def test_impersonation_failure_raises_foldrun_auth_error(monkeypatch):
    invalidate_token_cache()
    monkeypatch.delenv("FOLDRUN_ID_TOKEN", raising=False)
    monkeypatch.setenv(
        "FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT",
        "gateway-caller@test-proj.iam.gserviceaccount.com",
    )

    def fail_subprocess(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["gcloud"],
            stderr="ERROR: (gcloud.auth.print-identity-token) PERMISSION_DENIED",
        )

    with (
        patch("foldrun_shim.auth.subprocess.run", side_effect=fail_subprocess),
        patch("google.auth.default", side_effect=RuntimeError("No ADC")),
    ):
        with pytest.raises(FoldRunAuthError, match="PERMISSION_DENIED"):
            fetch_gateway_headers("https://gw-imp.a.run.app")

    invalidate_token_cache()


def test_gcloud_audiences_then_bare_fallback_for_human_user(monkeypatch):
    invalidate_token_cache()
    monkeypatch.delenv("FOLDRUN_ID_TOKEN", raising=False)
    monkeypatch.delenv("FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT", raising=False)

    user_jwt = _make_jwt({"exp": int(time.time()) + 3600, "email": "scientist@university.edu"})
    recorded_cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        recorded_cmds.append(list(cmd))
        if any(a.startswith("--audiences=") for a in cmd):
            return subprocess.CompletedProcess(
                cmd,
                returncode=1,
                stdout="",
                stderr="Invalid account type for --audiences",
            )
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=user_jwt + "\n", stderr="")

    with (
        patch(
            "google.oauth2.id_token.fetch_id_token",
            side_effect=RuntimeError("Not on GCE"),
        ),
        patch("foldrun_shim.auth.subprocess.run", side_effect=fake_run),
    ):
        headers = fetch_gateway_headers("https://gw-human.a.run.app")

    assert headers == {"Authorization": f"Bearer {user_jwt}"}
    assert len(recorded_cmds) == 2
    assert any(a.startswith("--audiences=") for a in recorded_cmds[0])
    assert not any(a.startswith("--audiences=") for a in recorded_cmds[1])
    invalidate_token_cache()


def test_dotenv_fallback_export_prefix_and_placeholder_handling(tmp_path, monkeypatch):
    sub_dir = tmp_path / "notebooks"
    sub_dir.mkdir()
    monkeypatch.chdir(sub_dir)
    monkeypatch.delenv("FOLDRUN_ENV_FILE", raising=False)
    monkeypatch.delenv("FOLDRUN_GATEWAY_URL", raising=False)
    monkeypatch.delenv("export_foo", raising=False)
    monkeypatch.delenv("_foo", raising=False)
    monkeypatch.setenv("EXISTING_REAL_VAR", "keep_me")
    monkeypatch.setenv("PLACEHOLDER_VAR", "<YOUR_PROJECT>")

    # Child .env has export_foo (should NOT strip 'export') and a placeholder gateway URL
    (sub_dir / ".env").write_text(
        "export_foo=preserved_key\n"
        "EXISTING_REAL_VAR=clobber_attempt\n"
        "PLACEHOLDER_VAR=resolved_from_dotenv\n"
        "FOLDRUN_GATEWAY_URL=https://foldrun-gateway-example.a.run.app\n",
        encoding="utf-8",
    )
    # Parent ../.env has the real gateway URL
    (tmp_path / ".env").write_text(
        'export FOLDRUN_GATEWAY_URL="https://foldrun-gateway-prod123-uc.a.run.app"\n',
        encoding="utf-8",
    )

    _load_dotenv_fallback()

    assert foldrun_shim.auth.os.environ.get("export_foo") == "preserved_key"
    assert "_foo" not in foldrun_shim.auth.os.environ
    assert foldrun_shim.auth.os.environ.get("EXISTING_REAL_VAR") == "keep_me"
    assert foldrun_shim.auth.os.environ.get("PLACEHOLDER_VAR") == "resolved_from_dotenv"
    assert (
        foldrun_shim.auth.os.environ.get("FOLDRUN_GATEWAY_URL")
        == "https://foldrun-gateway-prod123-uc.a.run.app"
    )


def test_polling_exponential_backoff_survives_transient_errors(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("foldrun_shim.client.time.sleep", lambda s: sleeps.append(s))

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs/resilient-job":
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ReadTimeout("Transient Cloud Run cold-start timeout", request=request)
            if attempts["n"] == 2:
                return httpx.Response(503, text="Service Unavailable")
            if attempts["n"] == 3:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(
                200,
                json={"job_id": "resilient-job", "state": "PIPELINE_STATE_SUCCEEDED"},
            )
        return httpx.Response(404)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(
        gateway_url="http://testserver",
        http_client=mock_http,
        poll_interval_sec=0.01,
    )
    status = client._poll_until_complete("resilient-job", tmp_path / ".foldrun_job.json")
    assert status["state"] == "PIPELINE_STATE_SUCCEEDED"
    assert attempts["n"] == 4
    assert len(sleeps) == 3


# =============================================================================
# H9: Automatic Output Download Fallback to ?stream=true
# =============================================================================


@pytest.mark.parametrize("failure_mode", ["500", "501", "403", "signed_url_blocked"])
def test_download_outputs_falls_back_to_stream(tmp_path, failure_mode):
    archive_bytes = _make_tar_gz({"predictions/ranked_0.cif": b"data_model\n#\n"})
    stream_called = {"ok": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs/job-stream/outputs":
            if request.url.params.get("stream") == "true":
                stream_called["ok"] = True
                return httpx.Response(200, content=archive_bytes)
            if failure_mode in ("500", "501", "403"):
                return httpx.Response(int(failure_mode), json={"detail": "signBlob unavailable"})
            return httpx.Response(
                200,
                json={"download_url": "https://storage.googleapis.com/blocked/outputs.tar.gz"},
            )
        return httpx.Response(404)

    mock_http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://testserver")
    client = GatewayClient(gateway_url="http://testserver", http_client=mock_http)

    if failure_mode == "signed_url_blocked":
        with patch.object(
            client,
            "_download_signed_url",
            side_effect=httpx.ConnectError("VPC-SC blocked direct GCS access"),
        ):
            extracted = client._download_and_extract_outputs("job-stream", tmp_path / "out")
    else:
        extracted = client._download_and_extract_outputs("job-stream", tmp_path / "out")

    assert stream_called["ok"] is True
    assert len(extracted) == 1
    assert (tmp_path / "out" / "predictions" / "ranked_0.cif").read_bytes() == b"data_model\n#\n"


# =============================================================================
# H14 & C2: Bundler Flag-Pair Digest & AlphaFold 3 External Asset Bundling
# =============================================================================


def test_compute_client_input_sha256_prevents_flag_value_swap_collision():
    h_a = compute_client_input_sha256(
        "boltz",
        b"version: 1\n",
        {},
        ["--diffusion_samples", "5", "--sampling_steps", "200"],
        {},
    )
    h_swapped = compute_client_input_sha256(
        "boltz",
        b"version: 1\n",
        {},
        ["--diffusion_samples", "200", "--sampling_steps", "5"],
        {},
    )
    h_reordered_equals = compute_client_input_sha256(
        "boltz",
        b"version: 1\n",
        {},
        ["--sampling_steps=200", "--diffusion_samples=5"],
        {},
    )
    assert h_a != h_swapped
    assert h_a == h_reordered_equals


def test_alphafold3_external_msa_and_template_bundling(tmp_path):
    msa_dir = tmp_path / "msas"
    tpl_dir = tmp_path / "templates"
    msa_dir.mkdir()
    tpl_dir.mkdir()
    (msa_dir / "prot_unpaired.a3m").write_text(">q\nACDEFG\n", encoding="utf-8")
    (msa_dir / "prot_paired.a3m").write_text(">q\nACDEFG\n", encoding="utf-8")
    (msa_dir / "rna_unpaired.a3m").write_text(">r\nAUGCUU\n", encoding="utf-8")
    (tpl_dir / "1abc.cif").write_text("data_1ABC\n#\n", encoding="utf-8")

    af3_list_payload = [
        {
            "name": "complex_with_assets",
            "modelSeeds": [1],
            "dialect": "alphafold3",
            "version": 2,
            "sequences": [
                {
                    "protein": {
                        "id": "A",
                        "sequence": "ACDEFG",
                        "unpairedMsaPath": "msas/prot_unpaired.a3m",
                        "pairedMsaPath": "./msas/prot_paired.a3m",
                        "templates": [
                            {
                                "mmcifPath": "templates/1abc.cif",
                                "queryIndices": [0],
                                "templateIndices": [0],
                            }
                        ],
                    }
                },
                {
                    "rna": {
                        "id": "R",
                        "sequence": "AUGCUU",
                        "unpairedMsaPath": "msas/rna_unpaired.a3m",
                    }
                },
                {
                    "dna": {
                        "id": "D",
                        "sequence": "ATCG",
                        "unpairedMsaPath": "gs://remote-bucket/dna.a3m",
                    }
                },
            ],
        }
    ]
    json_path = tmp_path / "af3_query.json"
    json_path.write_text(json.dumps(af3_list_payload, indent=2), encoding="utf-8")

    bundled = bundle_local_inputs("alphafold3", json_path)
    assert set(bundled.assets_map.keys()) == {
        "msas/prot_unpaired.a3m",
        "msas/prot_paired.a3m",
        "msas/rna_unpaired.a3m",
        "templates/1abc.cif",
    }
    assert bundled.assets_tar_bytes is not None


# =============================================================================
# H15 & Decision #2: CLI & Helper Parity + Live Server Allowlist Cross-Check
# =============================================================================


def test_bindcraft_cli_and_helper_design_path_and_json_bundling(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    filters_dir = tmp_path / "settings_filters"
    adv_dir = tmp_path / "settings_advanced"
    filters_dir.mkdir()
    adv_dir.mkdir()
    (filters_dir / "custom_filters.json").write_text(
        '{"Average_i_pTM": {"threshold": 0.6}}', encoding="utf-8"
    )
    (adv_dir / "custom_adv.json").write_text('{"omit_AAs": "C"}', encoding="utf-8")

    settings_file = tmp_path / "pdl1_settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "design_path": "./my_pdl1_campaign",
                "binder_name": "PDL1",
                "starting_pdb": "4Z18",
                "chains": "A",
                "lengths": [65, 110],
            }
        ),
        encoding="utf-8",
    )

    stripped = _strip_local_bindcraft_args(
        [
            f"--settings={settings_file}",
            "-f",
            "./settings_filters/custom_filters.json",
            "-a",
            "./settings_advanced/custom_adv.json",
            "--no-pyrosetta",
            "--num_designs_per_seed=2",
        ]
    )
    s_path, out_dir, override, forwarded = stripped
    assert out_dir == "./my_pdl1_campaign"
    assert override is False

    bundled = bundle_local_inputs("bindcraft", s_path, cli_args=forwarded)
    assert "settings_filters/custom_filters.json" in bundled.assets_map
    assert "settings_advanced/custom_adv.json" in bundled.assets_map
    assert bundled.cli_args == [
        "--no-pyrosetta",
        "--num_designs_per_seed=2",
        "--filters",
        "settings_filters/custom_filters.json",
        "--advanced",
        "settings_advanced/custom_adv.json",
    ]

    # Explicit -o overrides design_path
    stripped_o = _strip_local_bindcraft_args(
        [f"--settings={settings_file}", "-o", "./explicit_dir"]
    )
    assert stripped_o[1] == "./explicit_dir"

    # Helper predict(out_dir=None) extracts design_path
    recorded = {}

    def fake_run_prediction(self, model, input_path, out_dir, cli_args, **kwargs):
        recorded["out_dir"] = out_dir
        recorded["cli_args"] = cli_args
        return GatewayJobResult(
            job_id="bc-1", model=model, state="SUCCEEDED", out_dir=Path(out_dir)
        )

    monkeypatch.setattr(GatewayClient, "run_prediction", fake_run_prediction)
    bindcraft.predict(
        settings_file,
        filters="./settings_filters/custom_filters.json",
        no_pyrosetta=True,
        gateway_url="http://localhost:8080",
    )
    assert recorded["out_dir"] == "./my_pdl1_campaign"
    assert "--no-pyrosetta=true" in recorded["cli_args"]


def test_rfd4_proteina_fast_mode_and_4tuple_unpacking(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "design.yaml"
    spec.write_text("binder_task:\n  contig: A1-100/0 60-80\n", encoding="utf-8")

    # Default fast mode + -o flag -> 4-tuple with compute_metrics=false
    stripped_fast = _strip_local_rfd4_proteina_args(
        [
            f"inputs={spec}",
            "-o",
            str(tmp_path / "rfd4_out"),
            "num_replicates=4",
            "seed=42",
        ]
    )
    assert len(stripped_fast) == 4
    in_p, out_d, ovr, sci_fast = stripped_fast
    assert in_p == str(spec)
    assert out_d == str(tmp_path / "rfd4_out")
    assert ovr is False
    assert "compute_metrics=false" in sci_fast

    # Explicit --metrics -> compute_metrics=true
    stripped_metrics = _strip_local_rfd4_proteina_args(
        [f"inputs={spec}", "output=./scored", "--metrics", "num_replicates=2"]
    )
    assert stripped_metrics[1] == "./scored"
    assert "compute_metrics=true" in stripped_metrics[3]

    # Explicit ~metrics -> compute_metrics=false
    stripped_tilde = _strip_local_rfd4_proteina_args([f"inputs={spec}", "~metrics"])
    assert "compute_metrics=false" in stripped_tilde[3]

    # Verify rfd4_proteina_main unpacks 4-tuple cleanly and prints fast-mode notice
    recorded_cli: list[list[str]] = []

    def fake_run_prediction(self, model, input_path, out_dir, cli_args, **kwargs):
        recorded_cli.append(list(cli_args))
        return GatewayJobResult(
            job_id="rfd4-1", model=model, state="SUCCEEDED", out_dir=Path(out_dir)
        )

    monkeypatch.setattr(GatewayClient, "run_prediction", fake_run_prediction)

    rc = rfd4_proteina_main(
        [f"inputs={spec}", "-o", str(tmp_path / "rfd4_cli")],
        gateway_url="http://localhost:8080",
    )
    assert rc == 0
    out_text = capsys.readouterr().out
    assert _RFD4_FAST_MODE_NOTICE in out_text
    assert recorded_cli[-1] == ["compute_metrics=false"]

    # Helper default vs compute_metrics=True
    rfd4_proteina.predict(spec, out_dir=tmp_path / "h_fast", gateway_url="http://localhost:8080")
    assert "compute_metrics=false" in recorded_cli[-1]

    rfd4_proteina.predict(
        spec,
        out_dir=tmp_path / "h_full",
        compute_metrics=True,
        gateway_url="http://localhost:8080",
    )
    assert "compute_metrics=true" in recorded_cli[-1]


def test_af2_af3_absl_boolean_flags_run_alphafold_dispatch_and_server_allowlist(
    tmp_path, monkeypatch
):
    # 1. AF2 absl boolean normalization & local flag stripping
    af2_stripped = _strip_local_alphafold2_args(
        [
            "--fasta_paths=monomer.fasta",
            "--output_dir=./af2_out",
            "--model_preset=monomer",
            "--db_preset=reduced_dbs",
            "--nouse_gpu_relax",
            "--nouse_precomputed_msas",
            "--norun_relax",
            "--nobenchmark",
            "--data_dir=/mnt/local/db",
        ]
    )
    assert af2_stripped[3] == [
        "--model_preset=monomer",
        "--db_preset=reduced_dbs",
        "--models_to_relax=none",
        "--benchmark=false",
    ]

    # 2. AF3 absl boolean normalization & redundant --run_inference=true stripping
    af3_stripped = _strip_local_alphafold3_args(
        [
            "--json_path=query.json",
            "--output_dir=./af3_out",
            "--norun_data_pipeline",
            "--run_inference=true",
            "--save_embeddings",
            "--nosave_distogram",
            "--compress_large_output_files",
            "--num_diffusion_samples=1",
        ]
    )
    assert af3_stripped[3] == [
        "--run_data_pipeline=false",
        "--save_embeddings=true",
        "--save_distogram=false",
        "--compress_large_output_files=true",
        "--num_diffusion_samples=1",
    ]

    # --norun_inference is preserved as --run_inference=false so server rejects data-pipeline-only
    af3_no_inf = _strip_local_alphafold3_args(["--json_path=query.json", "--norun_inference"])
    assert "--run_inference=false" in af3_no_inf[3]

    # 3. Verify alphafold2_main and standalone run_alphafold.py dispatch --json_path to AF3
    dispatched: list[tuple[str, list[str]]] = []

    def fake_run_prediction(self, model, input_path, out_dir, cli_args, **kwargs):
        dispatched.append((model, list(cli_args)))
        return GatewayJobResult(
            job_id="job-x", model=model, state="SUCCEEDED", out_dir=Path(out_dir)
        )

    monkeypatch.setattr(GatewayClient, "run_prediction", fake_run_prediction)

    q_json = tmp_path / "query.json"
    q_json.write_text(
        '{"dialect": "alphafold3", "version": 2, "name": "q", "modelSeeds": [1], "sequences": []}',
        encoding="utf-8",
    )

    alphafold2_main(
        [f"--json_path={q_json}", "--norun_data_pipeline"],
        gateway_url="http://localhost:8080",
    )
    assert dispatched[-1][0] == "alphafold3"
    assert "--run_data_pipeline=false" in dispatched[-1][1]

    spec = importlib.util.spec_from_file_location(
        "run_alphafold_script", SDK_ROOT / "run_alphafold.py"
    )
    assert spec is not None and spec.loader is not None
    run_af_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_af_mod)

    monkeypatch.setenv("FOLDRUN_GATEWAY_URL", "http://localhost:8080")
    run_af_mod.main([f"--json_path={q_json}", "--num_diffusion_samples=2"])
    assert dispatched[-1][0] == "alphafold3"
    assert "--num_diffusion_samples=2" in dispatched[-1][1]

    # Python helper run_relax=False + use_gpu_relax=True normalization
    fasta = tmp_path / "m.fasta"
    fasta.write_text(">s\nACDEFG\n", encoding="utf-8")
    alphafold2.predict(
        fasta,
        output_dir=tmp_path / "af2_h",
        run_relax=False,
        use_gpu_relax=True,
        gateway_url="http://localhost:8080",
    )
    assert dispatched[-1][0] == "alphafold2"
    assert "--models_to_relax=none" in dispatched[-1][1]
    assert not any("use_gpu_relax" in a for a in dispatched[-1][1])

    # 4. Cross-check against live server flag_allowlist if foldrun-agent is present in repo
    allowlist_path = (
        SDK_ROOT.parent.parent / "foldrun-agent" / "foldrun_app" / "gateway" / "flag_allowlist.py"
    )
    if allowlist_path.is_file():
        al_spec = importlib.util.spec_from_file_location("_server_flag_allowlist", allowlist_path)
        assert al_spec is not None and al_spec.loader is not None
        al_mod = importlib.util.module_from_spec(al_spec)
        sys.modules["_server_flag_allowlist"] = al_mod
        try:
            al_spec.loader.exec_module(al_mod)
            validate_and_partition_flags = al_mod.validate_and_partition_flags

            v_af2 = validate_and_partition_flags("alphafold2", af2_stripped[3])
            assert v_af2.pipeline_params["run_relaxation"] is False

            v_af3 = validate_and_partition_flags("alphafold3", af3_stripped[3])
            assert v_af3.pipeline_params["run_data_pipeline"] is False
            assert v_af3.pipeline_params["save_embeddings"] is True

            v_rfd4 = validate_and_partition_flags(
                "rfd4_proteina", ["num_replicates=4", "compute_metrics=false"]
            )
            assert v_rfd4.pipeline_params["compute_metrics"] is False
            assert v_rfd4.pipeline_params["num_designs_per_seed"] == 4

            v_rfd4_metrics = validate_and_partition_flags(
                "rfd4_proteina", ["num_replicates=2", "compute_metrics=true"]
            )
            assert v_rfd4_metrics.pipeline_params["compute_metrics"] is True

            v_bc = validate_and_partition_flags(
                "bindcraft",
                [
                    "--no-pyrosetta",
                    "--filters",
                    "settings_filters/custom_filters.json",
                    "--advanced",
                    "settings_advanced/custom_adv.json",
                ],
            )
            assert "--filters=settings_filters/custom_filters.json" in v_bc.extra_cli_args
            assert "--advanced=settings_advanced/custom_adv.json" in v_bc.extra_cli_args
        finally:
            sys.modules.pop("_server_flag_allowlist", None)


# =============================================================================
# C1 & C8: Automated Structural & AST Validation Across All 7 Notebooks
# =============================================================================


EXPECTED_NOTEBOOKS = [
    "alphafold2/AlphaFold2_FoldRun_Shim.ipynb",
    "alphafold3/AlphaFold3_FoldRun_Shim.ipynb",
    "boltz2/Boltz2_FoldRun_Shim.ipynb",
    "openfold3/OpenFold3_FoldRun_Shim.ipynb",
    "boltzgen/BoltzGen_FoldRun_Shim.ipynb",
    "rfd4_proteina/RFD4_Proteina_FoldRun_Shim.ipynb",
    "bindcraft/BindCraft_FoldRun_Shim.ipynb",
]


def _sanitize_ipython_cell(src: str) -> str:
    lines = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("!", "%")):
            indent = line[: len(line) - len(stripped)]
            lines.append(f"{indent}pass")
        else:
            lines.append(line)
    return "\n".join(lines)


def test_all_seven_notebooks_valid_ast_setup_cell_and_honest_fallbacks():
    for rel_nb in EXPECTED_NOTEBOOKS:
        nb_path = NOTEBOOKS_DIR / rel_nb
        assert nb_path.is_file(), f"Missing notebook: {nb_path}"
        nb_data = json.loads(nb_path.read_text(encoding="utf-8"))
        cells = nb_data.get("cells", [])
        assert len(cells) >= 10, f"Unexpectedly few cells in {rel_nb}"

        # AST-parse every code cell
        for cell_idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            raw_src = "".join(cell.get("source", []))
            sanitized = _sanitize_ipython_cell(raw_src)
            try:
                ast.parse(sanitized, filename=f"{rel_nb}#cell_{cell_idx}")
            except SyntaxError as exc:
                pytest.fail(f"SyntaxError in {rel_nb} cell {cell_idx}: {exc}")

        # Verify Cell 2 setup cell has sysconfig PATH fix, placeholder detection, and export fix
        setup_src = "".join(cells[2].get("source", []))
        assert 'sysconfig.get_path("scripts")' in setup_src, (
            f"Missing sysconfig PATH setup in {rel_nb}"
        )
        assert "_is_placeholder_bucket" in setup_src, f"Missing _is_placeholder_bucket in {rel_nb}"
        assert 'r"^export(?=\\s)\\s*"' in setup_src, f"Missing safe export regex in {rel_nb}"

        # Extract and execute _is_placeholder_value + _is_placeholder_bucket helpers to verify behavior
        tree = ast.parse(_sanitize_ipython_cell(setup_src))
        helper_ns: dict[str, Any] = {}
        fn_nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in ("_is_placeholder_value", "_is_placeholder_bucket")
        ]
        exec(
            compile(ast.Module(body=fn_nodes, type_ignores=[]), filename=rel_nb, mode="exec"),
            helper_ns,
        )
        fn = helper_ns["_is_placeholder_bucket"]
        assert fn("your-project-foldrun-data") is True
        assert fn("<PROJECT_ID>-foldrun-data") is True
        assert fn("") is True
        assert fn("my-real-bio-foldrun-data") is False

    # C1: AlphaFold 3 notebook must not download unrelated 1TOP.pdb and must supply explicit empty MSA/templates
    af3_nb = json.loads(
        (NOTEBOOKS_DIR / "alphafold3/AlphaFold3_FoldRun_Shim.ipynb").read_text(encoding="utf-8")
    )
    af3_full_text = json.dumps(af3_nb)
    assert "1TOP.pdb" not in af3_full_text
    af3_cell5 = "".join(af3_nb["cells"][5]["source"])
    assert '"unpairedMsa": ""' in af3_cell5
    assert '"pairedMsa": ""' in af3_cell5
    assert '"templates": []' in af3_cell5
    af3_cell11 = "".join(af3_nb["cells"][11]["source"])
    assert "FOLDRUN_SYNTHETIC_DEMO" in af3_cell11
    assert "SYNTHETIC DEMO DATA" in af3_cell11
    assert "raise FileNotFoundError" in af3_cell11

    # C1: OpenFold 3 and BindCraft notebooks also gate synthetic fallback behind FOLDRUN_SYNTHETIC_DEMO
    of3_nb = json.loads(
        (NOTEBOOKS_DIR / "openfold3/OpenFold3_FoldRun_Shim.ipynb").read_text(encoding="utf-8")
    )
    of3_cell19 = "".join(of3_nb["cells"][19]["source"])
    assert "FOLDRUN_SYNTHETIC_DEMO" in of3_cell19
    assert "SYNTHETIC DEMO DATA" in of3_cell19
    assert "raise FileNotFoundError" in of3_cell19

    bc_nb = json.loads(
        (NOTEBOOKS_DIR / "bindcraft/BindCraft_FoldRun_Shim.ipynb").read_text(encoding="utf-8")
    )
    bc_cell18 = "".join(bc_nb["cells"][18]["source"])
    assert "FOLDRUN_SYNTHETIC_DEMO" in bc_cell18
    assert "SYNTHETIC DEMO DATA" in bc_cell18
    assert "raise FileNotFoundError" in bc_cell18

    # C8: Boltz-2 notebook uses distinct output subdirectories across examples
    boltz_text = (NOTEBOOKS_DIR / "boltz2/Boltz2_FoldRun_Shim.ipynb").read_text(encoding="utf-8")
    for subdir in (
        "./boltz_out/prot",
        "./boltz_out/affinity_cli",
        "./boltz_out/affinity",
        "./boltz_out/pocket",
        "./boltz_out/custom_msa",
        "./boltz_out/de_novo",
    ):
        assert subdir in boltz_text, f"Missing isolated Boltz-2 output directory {subdir}"
