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

"""Unit tests for FoldRun Gateway CLI flag allowlist and runner.yaml sanitizer."""

import pytest

from foldrun_app.gateway.flag_allowlist import (
    FlagValidationError,
    sanitize_runner_yaml_overrides,
    validate_and_partition_flags,
)


class TestBoltzFlagAllowlist:
    """Verify Boltz-2 scientific flag validation and KFP vs container partitioning."""

    def test_valid_boltz_flags_partitioned_correctly(self):
        res = validate_and_partition_flags(
            model="boltz",
            cli_args=[
                "--use_potentials",
                "--recycling_steps",
                "5",
                "--sampling_steps",
                "250",
                "--diffusion_samples",
                "5",
                "--num_seeds",
                "3",
                "--seed",
                "100",
                "--step_scale",
                "1.5",
                "--output_format",
                "mmcif",
                "--write_full_pae",
                "--write_full_pde",
            ],
        )
        assert res.pipeline_params == {
            "num_diffusion_samples": 5,
            "num_model_seeds": 3,
            "base_seed": 100,
        }
        assert "--use_potentials" in res.extra_cli_args
        assert "--recycling_steps=5" in res.extra_cli_args
        assert "--write_full_pae" in res.extra_cli_args
        assert "--write_full_pde" in res.extra_cli_args
        assert res.normalized_flags["seed"] == 100

    @pytest.mark.parametrize(
        "bad_args",
        [
            ["--cache", "/etc/passwd"],
            ["--out_dir", "/tmp/evil"],
            ["--devices", "4"],
            ["--accelerator", "cpu"],
            ["--use_msa_server"],
            ["--unknown_flag", "123"],
            ["--use_pot"],  # prefix abbreviation must be rejected (allow_abbrev=False)
            ["--diffusion_samples", "999"],  # exceeds upper bound 25
            ["--recycling_steps", "0"],  # below lower bound 1
            ["--step_scale", "50.0"],  # exceeds upper bound 10.0
        ],
    )
    def test_rejected_boltz_flags_raise_error(self, bad_args):
        with pytest.raises(FlagValidationError):
            validate_and_partition_flags(model="boltz", cli_args=bad_args)


class TestOpenFold3FlagAllowlist:
    """Verify OpenFold 3 scientific flag and runner.yaml validation."""

    def test_valid_openfold3_flags_and_runner_yaml(self):
        res = validate_and_partition_flags(
            model="openfold3",
            cli_args=[
                "--num_diffusion_samples=4",
                "--num_model_seeds=2",
                "--use_templates=true",
            ],
            runner_yaml_overrides={
                "low_mem": True,
                "plddt": True,
                "model_update": {"presets": ["predict"]},
            },
        )
        assert res.pipeline_params == {
            "num_diffusion_samples": 4,
            "num_model_seeds": 2,
            "use_msa_server": True,
            "use_templates": True,
        }
        assert res.runner_yaml_overrides["low_mem"] is True

    def test_openfold3_boolean_flag_without_value_and_explicit_false(self):
        res_flag = validate_and_partition_flags(
            model="openfold3",
            cli_args=["--use_templates"],
        )
        assert res_flag.pipeline_params["use_templates"] is True

        res_false = validate_and_partition_flags(
            model="openfold3",
            cli_args=["--use_templates=false"],
        )
        assert res_false.pipeline_params["use_templates"] is False

    def test_blocked_openfold3_runner_yaml_keys_rejected(self):
        with pytest.raises(FlagValidationError, match="Disallowed"):
            sanitize_runner_yaml_overrides(
                {"template_preprocessor_settings": {"structure_directory": "/etc"}}
            )

    @pytest.mark.parametrize(
        "bad_args",
        [
            ["--inference_ckpt_path=/tmp/ckpt"],
            ["--output_dir=/tmp/out"],
            ["--query_json=foo.json"],
            ["--num_diffusion_samples=100"],
        ],
    )
    def test_rejected_openfold3_flags(self, bad_args):
        with pytest.raises(FlagValidationError):
            validate_and_partition_flags(model="openfold3", cli_args=bad_args)


class TestAlphaFold2FlagAllowlist:
    """Verify AlphaFold 2 flag allowlist and path-flag blocking."""

    def test_valid_alphafold2_multimer_flags(self):
        res = validate_and_partition_flags(
            model="alphafold2",
            cli_args=[
                "--model_preset=multimer",
                "--db_preset=full_dbs",
                "--max_template_date=2022-01-01",
                "--num_multimer_predictions_per_model=3",
                "--models_to_relax=all",
                "--benchmark=true",
            ],
        )
        assert res.pipeline_params["model_preset"] == "multimer"
        assert res.pipeline_params["use_small_bfd"] is False
        assert res.pipeline_params["max_template_date"] == "2022-01-01"
        assert res.pipeline_params["num_multimer_predictions_per_model"] == 3
        assert res.pipeline_params["models_to_relax"] == "all"
        assert res.pipeline_params["benchmark"] is True

    @pytest.mark.parametrize(
        "bad_args",
        [
            ["--data_dir=/mnt/nfs"],
            ["--uniref90_database_path=/etc/shadow"],
            ["--jackhmmer_binary_path=/bin/sh"],
            ["--template_mmcif_dir=/tmp"],
            ["--max_template_date=not-a-date"],
            ["--num_multimer_predictions_per_model=99"],
        ],
    )
    def test_rejected_alphafold2_flags(self, bad_args):
        with pytest.raises(FlagValidationError):
            validate_and_partition_flags(model="alphafold2", cli_args=bad_args)


class TestGenerativeBinderFlagAllowlists:
    """Verify BoltzGen, RFD4-Proteina, and BindCraft flag validation and Hydra normalization."""

    def test_valid_boltzgen_flags(self):
        res = validate_and_partition_flags(
            model="boltzgen",
            cli_args=[
                "--protocol=protein-anything",
                "--num_designs=4",
                "--budget=2",
                "--num_model_seeds=5",
                "--seed=42",
                "--binder_length_range=80..120",
                "--target_chains=A,B",
                "--binding_residues=45,46,49",
            ],
        )
        assert res.pipeline_params["protocol"] == "protein-anything"
        assert res.pipeline_params["num_designs_per_seed"] == 4
        assert res.pipeline_params["budget_per_seed"] == 2
        assert res.pipeline_params["num_model_seeds"] == 5
        assert res.pipeline_params["base_seed"] == 42
        assert res.pipeline_params["binder_length_range"] == "80..120"
        assert res.pipeline_params["target_chains"] == ["A", "B"]
        assert res.pipeline_params["binding_residues"] == ["45", "46", "49"]

    def test_valid_rfd4_proteina_hydra_and_flag_tokens(self):
        res = validate_and_partition_flags(
            model="rfd4_proteina",
            cli_args=[
                "num_replicates=4",
                "seed=42",
                "--num_model_seeds=5",
                "--design_mode=binder_design",
                "~metrics",
                "compile_lengths=null",
                "num_steps=100",
            ],
        )
        assert res.pipeline_params["design_mode"] == "binder_design"
        assert res.pipeline_params["num_designs_per_seed"] == 4
        assert res.pipeline_params["num_model_seeds"] == 5
        assert res.pipeline_params["base_seed"] == 42
        assert "~metrics" in res.extra_cli_args
        assert "compile_lengths=null" in res.extra_cli_args
        assert "num_steps=100" in res.extra_cli_args

    def test_valid_bindcraft_flags(self):
        res = validate_and_partition_flags(
            model="bindcraft",
            cli_args=[
                "--num_designs_per_seed=2",
                "--num_model_seeds=5",
                "--seed=42",
                "--no-pyrosetta",
            ],
        )
        assert res.pipeline_params["num_designs_per_seed"] == 2
        assert res.pipeline_params["num_model_seeds"] == 5
        assert res.pipeline_params["base_seed"] == 42
        assert "--no-pyrosetta" in res.extra_cli_args

    @pytest.mark.parametrize(
        "model,bad_args",
        [
            ("boltzgen", ["--output", "/tmp/out"]),
            ("boltzgen", ["--protocol", "invalid-protocol"]),
            ("boltzgen", ["--num_designs", "99"]),
            ("rfd4_proteina", ["hf_ckpt_path=/etc/passwd"]),
            ("rfd4_proteina", ["inputs=/etc/passwd"]),
            ("rfd4_proteina", ["num_replicates=99"]),
            ("rfd4_proteina", ["evil_hydra_key=123"]),
            ("bindcraft", ["--settings=/etc/passwd"]),
            ("bindcraft", ["--filters=/etc/passwd"]),
            ("bindcraft", ["--advanced=/etc/passwd"]),
            ("bindcraft", ["--num_designs_per_seed=50"]),
        ],
    )
    def test_rejected_generative_binder_flags(self, model, bad_args):
        with pytest.raises(FlagValidationError):
            validate_and_partition_flags(model=model, cli_args=bad_args)
