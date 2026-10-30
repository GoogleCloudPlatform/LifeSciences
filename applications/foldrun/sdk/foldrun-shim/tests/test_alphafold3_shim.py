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

"""Unit tests for AlphaFold 3 foldrun-shim SDK helper and CLI."""

import tempfile
from pathlib import Path

from foldrun_shim import (
    AlphaFold3NotebookHelper,
    af3,
    alphafold3,
)
from foldrun_shim.cli import _strip_local_alphafold3_args
from foldrun_shim.client import GatewayJobResult


def test_import_helpers():
    assert isinstance(alphafold3, AlphaFold3NotebookHelper)
    assert af3 is alphafold3


def test_strip_local_alphafold3_args():
    input_path, out_dir, override, scientific_args = _strip_local_alphafold3_args(
        [
            "--json_path=query.json",
            "--output_dir=./results",
            "--override",
            "--num_diffusion_samples=2",
            "--num_recycles=5",
        ]
    )
    assert input_path == "query.json"
    assert out_dir == "./results"
    assert override is True
    assert "--num_diffusion_samples=2" in scientific_args
    assert "--num_recycles=5" in scientific_args


def test_strip_local_alphafold3_args_positional():
    input_path, out_dir, _override, scientific_args = _strip_local_alphafold3_args(
        [
            "my_query.json",
            "--out_dir=./my_out",
            "--model_dir=/models/af3",
            "--db_dir=/databases/af3",
            "--jax_compilation_cache_dir=/tmp/jax_cache",
            "--buckets=256,512,1024",
            "--flash_attention_implementation=triton",
            "--uniref90_database_path=/db/uniref90.fasta",
            "--jackhmmer_binary_path=/usr/bin/jackhmmer",
            "--num_seeds=3",
        ]
    )
    assert input_path == "my_query.json"
    assert out_dir == "./my_out"
    assert scientific_args == ["--num_seeds=3"]


def test_compute_client_input_sha256_cli_args_order_independent():
    from foldrun_shim.bundler import compute_client_input_sha256

    h1 = compute_client_input_sha256(
        "alphafold2",
        b">seq\nACDEFG\n",
        {},
        [
            "--model_preset=monomer",
            "--db_preset=full_dbs",
            "--max_template_date=2022-01-01",
        ],
        {},
    )
    h2 = compute_client_input_sha256(
        "alphafold2",
        b">seq\nACDEFG\n",
        {},
        [
            "--max_template_date=2022-01-01",
            "--model_preset=monomer",
            "--db_preset=full_dbs",
        ],
        {},
    )
    assert h1 == h2


def test_bindcraft_cwd_asset_fallback(tmp_path, monkeypatch):
    from foldrun_shim.bundler import bundle_local_inputs

    monkeypatch.chdir(tmp_path)
    target_pdb = tmp_path / "target_pdl1.pdb"
    target_pdb.write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 20.00           N\n"
    )
    settings_dir = tmp_path / "settings_target"
    settings_dir.mkdir()
    settings_json = settings_dir / "PDL1.json"
    settings_json.write_text(
        '{"starting_pdb": "./target_pdl1.pdb", "chains": "A", "lengths": [65, 120]}'
    )

    bundled = bundle_local_inputs("bindcraft", settings_json)
    assert "target_pdl1.pdb" in bundled.assets_map


def test_predict_helper_mock(monkeypatch):
    recorded_calls = []

    def mock_run_prediction(self, model, input_path, out_dir, cli_args, **kwargs):
        recorded_calls.append(
            {
                "model": model,
                "input_path": input_path,
                "out_dir": out_dir,
                "cli_args": cli_args,
            }
        )
        return GatewayJobResult(
            job_id="test-job-123",
            model="alphafold3",
            state="PIPELINE_STATE_SUCCEEDED",
            out_dir=Path(out_dir),
        )

    from foldrun_shim.client import GatewayClient

    monkeypatch.setattr(GatewayClient, "run_prediction", mock_run_prediction)

    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        f.write('{"dialect": "alphafold3"}')
        tmp_name = f.name

    try:
        res = alphafold3.predict(
            json_path=tmp_name,
            output_dir="./custom_out",
            num_diffusion_samples=1,
            run_data_pipeline=False,
            gateway_url="http://localhost:8080",
        )
        assert res.job_id == "test-job-123"
        assert len(recorded_calls) == 1
        assert recorded_calls[0]["model"] == "alphafold3"
        assert "--num_diffusion_samples=1" in recorded_calls[0]["cli_args"]
        assert "--run_data_pipeline=false" in recorded_calls[0]["cli_args"]
    finally:
        import os

        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
