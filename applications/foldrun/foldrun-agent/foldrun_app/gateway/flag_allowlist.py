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

"""Per-model scientific CLI flag parser, validator, and allowlist."""

import argparse
import re
from dataclasses import dataclass, field
from typing import Any


class FlagValidationError(ValueError):
    """Raised when a client supplies a disallowed, unknown, or out-of-range CLI flag."""


class _StrictArgumentParser(argparse.ArgumentParser):
    """ArgumentParser with allow_abbrev=False that raises FlagValidationError instead of exiting."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        raise FlagValidationError(message)


@dataclass
class ValidatedModelFlags:
    """Partitioned KFP pipeline parameters and container extra_cli_args."""

    pipeline_params: dict[str, Any] = field(default_factory=dict)
    extra_cli_args: list[str] = field(default_factory=list)
    runner_yaml_overrides: dict[str, Any] = field(default_factory=dict)
    normalized_flags: dict[str, Any] = field(default_factory=dict)


_BLOCKED_FLAG_PREFIXES = (
    "--cache",
    "--out_dir",
    "--output_dir",
    "--output",
    "--data_dir",
    "--inference_ckpt_path",
    "--hf_ckpt_path",
    "--runner_yaml",
    "--query_json",
    "--fasta_paths",
    "--inputs",
    "--settings",
    "--devices",
    "--accelerator",
    "--num_workers",
    "--msa_server_url",
    "--use_gpu_relax",
    "--use_precomputed_msas",
    "--endpoint_name_or_id",
)

_MODEL_SCOPED_BLOCKED_FLAGS: dict[str, tuple[str, ...]] = {
    "boltz": ("--use_msa_server", "--filters", "--advanced"),
    "alphafold2": ("--use_msa_server", "--filters", "--advanced"),
    "boltzgen": ("--use_msa_server", "--filters", "--advanced"),
    "rfd4_proteina": ("--use_msa_server", "--filters", "--advanced"),
    "alphafold3": ("--use_msa_server", "--filters", "--advanced", "--run_inference"),
    "af3": ("--use_msa_server", "--filters", "--advanced", "--run_inference"),
    "openfold3": ("--filters", "--advanced"),
    "bindcraft": ("--use_msa_server",),
}

_BLOCKED_AF2_PATTERNS = (
    re.compile(r"^--.*_database_path(?:=|$)"),
    re.compile(r"^--.*_binary_path(?:=|$)"),
    re.compile(r"^--pdb70_database_path(?:=|$)"),
    re.compile(r"^--template_mmcif_dir(?:=|$)"),
    re.compile(r"^--obsolete_pdbs_path(?:=|$)"),
)

_ALLOWED_OF3_RUNNER_YAML_KEYS = {
    "model_update",
    "low_mem",
    "plddt",
    "pae",
    "experiment_settings",
    "num_diffusion_samples",
    "num_model_seeds",
}


def _parse_bool(val: str | bool) -> bool:
    if isinstance(val, bool):
        return val
    lowered = str(val).strip().lower()
    if lowered in ("true", "1", "yes", "y"):
        return True
    if lowered in ("false", "0", "no", "n"):
        return False
    raise FlagValidationError(f"Invalid boolean value: {val!r}")


def _check_blocked_flags(cli_args: list[str], model: str = "") -> None:
    scoped_blocked = _MODEL_SCOPED_BLOCKED_FLAGS.get(model, ())
    all_blocked = _BLOCKED_FLAG_PREFIXES + scoped_blocked
    for token in cli_args:
        flag_name = token.split("=", 1)[0].strip()
        for blocked in all_blocked:
            if flag_name == blocked or (
                not flag_name.startswith("-") and "=" in token and f"--{flag_name}" == blocked
            ):
                raise FlagValidationError(
                    f"Flag '{flag_name}' is an infrastructure/path flag managed by FoldRun and cannot be overridden."
                )
        for pat in _BLOCKED_AF2_PATTERNS:
            if pat.match(token):
                raise FlagValidationError(
                    f"Flag '{flag_name}' is a host database/binary path flag and cannot be overridden."
                )


def _int_in_range(name: str, low: int, high: int):
    def _validator(raw: str) -> int:
        try:
            val = int(raw)
        except ValueError as exc:
            raise FlagValidationError(f"{name} must be an integer, got {raw!r}") from exc
        if not (low <= val <= high):
            raise FlagValidationError(f"{name} must be between {low} and {high}, got {val}")
        return val

    return _validator


def _float_in_range(name: str, low: float, high: float):
    def _validator(raw: str) -> float:
        try:
            val = float(raw)
        except ValueError as exc:
            raise FlagValidationError(f"{name} must be a float, got {raw!r}") from exc
        if not (low <= val <= high):
            raise FlagValidationError(f"{name} must be between {low} and {high}, got {val}")
        return val

    return _validator


def validate_runner_yaml_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Validate OpenFold 3 runner.yaml scientific overrides and block path injection."""
    if not overrides:
        return {}
    if not isinstance(overrides, dict):
        raise FlagValidationError("runner_yaml_overrides must be a JSON object/mapping")

    unknown = set(overrides.keys()) - _ALLOWED_OF3_RUNNER_YAML_KEYS
    if unknown:
        raise FlagValidationError(
            f"Disallowed runner.yaml override key(s): {sorted(unknown)}. "
            f"Allowed keys: {sorted(_ALLOWED_OF3_RUNNER_YAML_KEYS)}"
        )

    # Ensure no nested path keys like structure_directory are smuggled inside overrides
    def _scan_forbidden_keys(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in (
                    "structure_directory",
                    "inference_ckpt_path",
                    "ccd_file_path",
                    "output_dir",
                ):
                    raise FlagValidationError(
                        f"Disallowed path configuration key '{k}' in runner.yaml overrides"
                    )
                _scan_forbidden_keys(v)
        elif isinstance(obj, list):
            for item in obj:
                _scan_forbidden_keys(item)

    _scan_forbidden_keys(overrides)
    return dict(overrides)


sanitize_runner_yaml_overrides = validate_runner_yaml_overrides


def validate_and_partition_flags(
    model: str,
    cli_args: list[str] | None = None,
    runner_yaml_overrides: dict[str, Any] | None = None,
) -> ValidatedModelFlags:
    """Validate CLI flags for a model and partition into pipeline_params and extra_cli_args."""
    args_list = list(cli_args or [])
    _check_blocked_flags(args_list, model=model)

    if model == "boltz":
        return _validate_boltz_flags(args_list)
    if model == "openfold3":
        return _validate_openfold3_flags(args_list, runner_yaml_overrides)
    if model == "alphafold2":
        return _validate_alphafold2_flags(args_list)
    if model == "boltzgen":
        return _validate_boltzgen_flags(args_list)
    if model == "rfd4_proteina":
        return _validate_rfd4_proteina_flags(args_list)
    if model == "bindcraft":
        return _validate_bindcraft_flags(args_list)
    if model in ("alphafold3", "af3"):
        return _validate_alphafold3_flags(args_list)
    raise FlagValidationError(f"Unsupported model: {model!r}")


def _validate_boltz_flags(cli_args: list[str]) -> ValidatedModelFlags:
    parser = _StrictArgumentParser(prog="boltz predict")
    # KFP pipeline parameters
    parser.add_argument(
        "--diffusion_samples",
        type=_int_in_range("--diffusion_samples", 1, 25),
        default=5,
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--num_model_seeds",
        "--num_seeds",
        dest="num_model_seeds",
        type=_int_in_range("--num_model_seeds", 1, 10),
        default=1,
    )
    # Container extra_cli_args
    parser.add_argument(
        "--recycling_steps",
        type=_int_in_range("--recycling_steps", 1, 20),
        default=None,
    )
    parser.add_argument(
        "--sampling_steps",
        type=_int_in_range("--sampling_steps", 1, 1000),
        default=None,
    )
    parser.add_argument(
        "--step_scale",
        type=_float_in_range("--step_scale", 0.1, 10.0),
        default=None,
    )
    parser.add_argument("--use_potentials", action="store_true", default=False)
    parser.add_argument("--write_full_pae", action="store_true", default=False)
    parser.add_argument("--write_full_pde", action="store_true", default=False)
    parser.add_argument(
        "--max_msa_seqs",
        type=_int_in_range("--max_msa_seqs", 32, 16384),
        default=None,
    )
    parser.add_argument("--subsample_msa", action="store_true", default=False)
    parser.add_argument(
        "--num_subsampled_msa",
        type=_int_in_range("--num_subsampled_msa", 1, 4096),
        default=None,
    )
    parser.add_argument(
        "--sampling_steps_affinity",
        type=_int_in_range("--sampling_steps_affinity", 1, 1000),
        default=None,
    )
    parser.add_argument(
        "--diffusion_samples_affinity",
        type=_int_in_range("--diffusion_samples_affinity", 1, 25),
        default=None,
    )
    parser.add_argument("--affinity_mw_correction", action="store_true", default=False)
    parser.add_argument("--output_format", choices=["mmcif"], default="mmcif")

    ns, unknown = parser.parse_known_args(cli_args)
    if unknown:
        raise FlagValidationError(f"Unsupported or disallowed boltz flag(s): {' '.join(unknown)}")

    pipeline_params: dict[str, Any] = {
        "num_diffusion_samples": ns.diffusion_samples,
        "num_model_seeds": ns.num_model_seeds,
    }
    if ns.seed is not None:
        pipeline_params["base_seed"] = ns.seed

    extra_cli_args: list[str] = []
    for name in (
        "recycling_steps",
        "sampling_steps",
        "step_scale",
        "max_msa_seqs",
        "num_subsampled_msa",
        "sampling_steps_affinity",
        "diffusion_samples_affinity",
    ):
        val = getattr(ns, name)
        if val is not None:
            extra_cli_args.append(f"--{name}={val}")

    for flag_bool in (
        "use_potentials",
        "write_full_pae",
        "write_full_pde",
        "subsample_msa",
        "affinity_mw_correction",
    ):
        if getattr(ns, flag_bool):
            extra_cli_args.append(f"--{flag_bool}")

    normalized = {
        "diffusion_samples": ns.diffusion_samples,
        "num_model_seeds": ns.num_model_seeds,
        "seed": ns.seed,
        "extra_cli_args": sorted(extra_cli_args),
    }
    return ValidatedModelFlags(
        pipeline_params=pipeline_params,
        extra_cli_args=extra_cli_args,
        normalized_flags=normalized,
    )


def _validate_openfold3_flags(
    cli_args: list[str],
    runner_yaml_overrides: dict[str, Any] | None = None,
) -> ValidatedModelFlags:
    parser = _StrictArgumentParser(prog="run_openfold predict")
    parser.add_argument(
        "--num_diffusion_samples",
        type=_int_in_range("--num_diffusion_samples", 1, 25),
        default=5,
    )
    parser.add_argument(
        "--num_model_seeds",
        type=_int_in_range("--num_model_seeds", 1, 10),
        default=1,
    )
    parser.add_argument("--use_templates", nargs="?", const=True, type=_parse_bool, default=True)
    parser.add_argument("--use_msa_server", nargs="?", const=True, type=_parse_bool, default=True)
    parser.add_argument("--seed", type=int, default=None)

    ns, unknown = parser.parse_known_args(cli_args)
    if unknown:
        raise FlagValidationError(
            f"Unsupported or disallowed run_openfold flag(s): {' '.join(unknown)}"
        )

    validated_overrides = validate_runner_yaml_overrides(runner_yaml_overrides)
    num_diff_samples = ns.num_diffusion_samples
    num_seeds = ns.num_model_seeds
    if "num_diffusion_samples" in validated_overrides:
        yaml_diff = int(validated_overrides.pop("num_diffusion_samples"))
        if not any(a.startswith("--num_diffusion_samples") for a in cli_args):
            num_diff_samples = _int_in_range("num_diffusion_samples", 1, 25)(str(yaml_diff))
    if "num_model_seeds" in validated_overrides:
        yaml_seeds = int(validated_overrides.pop("num_model_seeds"))
        if not any(a.startswith("--num_model_seeds") for a in cli_args):
            num_seeds = _int_in_range("num_model_seeds", 1, 10)(str(yaml_seeds))

    pipeline_params: dict[str, Any] = {
        "num_diffusion_samples": num_diff_samples,
        "num_model_seeds": num_seeds,
        "use_templates": ns.use_templates,
        "use_msa_server": ns.use_msa_server,
    }
    if ns.seed is not None:
        pipeline_params["base_seed"] = ns.seed

    normalized = {
        "num_diffusion_samples": num_diff_samples,
        "num_model_seeds": num_seeds,
        "use_templates": ns.use_templates,
        "seed": ns.seed,
        "runner_yaml_overrides": validated_overrides,
    }
    return ValidatedModelFlags(
        pipeline_params=pipeline_params,
        extra_cli_args=[],
        runner_yaml_overrides=validated_overrides,
        normalized_flags=normalized,
    )


def _validate_alphafold2_flags(cli_args: list[str]) -> ValidatedModelFlags:
    parser = _StrictArgumentParser(prog="run_alphafold.py")
    parser.add_argument(
        "--model_preset",
        choices=["monomer", "monomer_casp14", "monomer_ptm", "multimer"],
        default="monomer",
    )
    parser.add_argument(
        "--db_preset",
        choices=["full_dbs", "reduced_dbs"],
        default="reduced_dbs",
    )
    parser.add_argument("--max_template_date", type=str, default="2025-04-01")
    parser.add_argument(
        "--models_to_relax",
        choices=["all", "best", "none"],
        default="best",
    )
    parser.add_argument(
        "--num_multimer_predictions_per_model",
        type=_int_in_range("--num_multimer_predictions_per_model", 1, 25),
        default=5,
    )
    parser.add_argument(
        "--msa_method",
        choices=["auto", "jackhmmer", "mmseqs2"],
        default="auto",
    )
    parser.add_argument("--benchmark", nargs="?", const=True, type=_parse_bool, default=False)
    parser.add_argument(
        "--nobenchmark",
        dest="benchmark",
        action="store_false",
    )

    ns, unknown = parser.parse_known_args(cli_args)
    if unknown:
        raise FlagValidationError(
            f"Unsupported or disallowed AlphaFold2 flag(s): {' '.join(unknown)}"
        )

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", ns.max_template_date):
        raise FlagValidationError(
            f"--max_template_date must be YYYY-MM-DD, got {ns.max_template_date!r}"
        )

    use_small_bfd = ns.db_preset == "reduced_dbs"
    run_relaxation = ns.models_to_relax in ("all", "best")

    pipeline_params: dict[str, Any] = {
        "model_preset": ns.model_preset,
        "db_preset": ns.db_preset,
        "use_small_bfd": use_small_bfd,
        "max_template_date": ns.max_template_date,
        "models_to_relax": ns.models_to_relax,
        "run_relaxation": run_relaxation,
        "num_predictions_per_model": ns.num_multimer_predictions_per_model,
        "num_multimer_predictions_per_model": ns.num_multimer_predictions_per_model,
        "msa_method": ns.msa_method,
        "benchmark": ns.benchmark,
    }
    normalized = dict(pipeline_params)
    return ValidatedModelFlags(
        pipeline_params=pipeline_params,
        extra_cli_args=[],
        normalized_flags=normalized,
    )


def _validate_boltzgen_flags(cli_args: list[str]) -> ValidatedModelFlags:
    parser = _StrictArgumentParser(prog="boltzgen run")
    parser.add_argument(
        "--protocol",
        choices=[
            "protein-anything",
            "peptide-anything",
            "protein-small_molecule",
            "nanobody-anything",
            "antibody-anything",
            "protein-redesign",
        ],
        default="protein-anything",
    )
    parser.add_argument(
        "--num_designs_per_seed",
        "--num_designs",
        dest="num_designs_per_seed",
        type=_int_in_range("--num_designs", 1, 20),
        default=2,
    )
    parser.add_argument(
        "--budget_per_seed",
        "--budget",
        dest="budget_per_seed",
        type=_int_in_range("--budget", 1, 20),
        default=2,
    )
    parser.add_argument(
        "--num_model_seeds",
        "--num_seeds",
        dest="num_model_seeds",
        type=_int_in_range("--num_model_seeds", 1, 50),
        default=5,
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--binder_length_range", type=str, default="80..140")
    parser.add_argument("--target_chains", type=str, default=None)
    parser.add_argument("--binding_residues", type=str, default=None)

    ns, unknown = parser.parse_known_args(cli_args)
    if unknown:
        raise FlagValidationError(
            f"Unsupported or disallowed BoltzGen flag(s): {' '.join(unknown)}"
        )

    pipeline_params: dict[str, Any] = {
        "protocol": ns.protocol,
        "num_designs_per_seed": ns.num_designs_per_seed,
        "budget_per_seed": ns.budget_per_seed,
        "num_model_seeds": ns.num_model_seeds,
        "binder_length_range": ns.binder_length_range,
    }
    if ns.seed is not None:
        pipeline_params["base_seed"] = ns.seed
    if ns.target_chains is not None:
        pipeline_params["target_chains"] = [
            c.strip() for c in ns.target_chains.split(",") if c.strip()
        ]
    if ns.binding_residues is not None:
        pipeline_params["binding_residues"] = [
            r.strip() for r in ns.binding_residues.split(",") if r.strip()
        ]

    normalized = {
        **pipeline_params,
        "seed": ns.seed,
    }
    return ValidatedModelFlags(
        pipeline_params=pipeline_params,
        extra_cli_args=[],
        normalized_flags=normalized,
    )


_RFD4_HYDRA_TO_FLAG_MAP = {
    "num_replicates": "--num_designs_per_seed",
    "num_designs_per_seed": "--num_designs_per_seed",
    "generation.dataset.num_replicates": "--num_designs_per_seed",
    "num_model_seeds": "--num_model_seeds",
    "num_seeds": "--num_model_seeds",
    "seed": "--seed",
    "design_mode": "--design_mode",
    "binder_length_range": "--binder_length_range",
    "target_chains": "--target_chains",
    "binding_residues": "--binding_residues",
    "scaffold_motif": "--scaffold_motif",
    "compute_metrics": "--compute_metrics",
    "metrics": "--compute_metrics",
}

_RFD4_ALLOWED_HYDRA_OVERRIDES = {
    "~metrics",
    "compile_lengths=null",
}

_RFD4_ALLOWED_HYDRA_KEYS = {
    "num_steps",
    "nsteps",
    "guidance_scale",
    "guidance_w",
    "noise_scale",
    "temperature",
    "dt",
    "compile_lengths",
}

_RFD4_ALLOWED_HYDRA_SCALAR_PATHS = {
    "generation.args.num_steps",
    "generation.args.nsteps",
    "generation.args.guidance_scale",
    "generation.args.guidance_w",
    "generation.args.noise_scale",
    "generation.args.temperature",
    "generation.args.dt",
    "generation.model.guidance_scale",
    "generation.model.guidance_w",
    "generation.model.noise_scale",
    "generation.model.temperature",
    "generation.dataset.batch_size",
    "proteina.num_steps",
    "proteina.nsteps",
    "proteina.guidance_scale",
    "proteina.noise_scale",
    "proteina.temperature",
    "rfd4.num_steps",
    "rfd4.nsteps",
    "rfd4.guidance_scale",
    "rfd4.noise_scale",
    "rfd4.temperature",
}

_FORBIDDEN_HYDRA_FRAGMENTS = (
    "_target_",
    "_partial_",
    "_args_",
    "_convert_",
    "_recursive_",
    "hydra.",
    "${",
)
_SAFE_HYDRA_SCALAR_VALUE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _validate_safe_rel_config_path(raw: str, flag_name: str) -> str:
    cleaned = str(raw).strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if (
        not cleaned
        or cleaned.startswith("/")
        or cleaned.startswith("~")
        or ".." in cleaned.split("/")
    ):
        raise FlagValidationError(
            f"{flag_name} must be a relative filename or preset without '/' prefix or '..', got {raw!r}"
        )
    return cleaned


def _validate_rfd4_proteina_flags(cli_args: list[str]) -> ValidatedModelFlags:
    converted_args: list[str] = []
    extra_hydra_args: list[str] = []
    explicit_fast_override = False

    for token in cli_args:
        stripped = token.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        for forbidden in _FORBIDDEN_HYDRA_FRAGMENTS:
            if forbidden in lowered:
                raise FlagValidationError(
                    f"Disallowed Hydra directive or interpolation '{forbidden}' in {stripped!r}"
                )
        if stripped in _RFD4_ALLOWED_HYDRA_OVERRIDES:
            extra_hydra_args.append(stripped)
            if stripped == "~metrics":
                explicit_fast_override = True
            continue
        if "=" in stripped and not stripped.startswith("-"):
            h_key, h_val = stripped.split("=", 1)
            h_key_clean = h_key.lstrip("+").strip()
            h_val_clean = h_val.strip()
            if h_key_clean in _RFD4_HYDRA_TO_FLAG_MAP:
                converted_args.append(f"{_RFD4_HYDRA_TO_FLAG_MAP[h_key_clean]}={h_val_clean}")
                continue
            if (
                h_key_clean in _RFD4_ALLOWED_HYDRA_KEYS
                or h_key_clean in _RFD4_ALLOWED_HYDRA_SCALAR_PATHS
            ):
                if not _SAFE_HYDRA_SCALAR_VALUE_RE.match(h_val_clean):
                    raise FlagValidationError(
                        f"Invalid scalar value for Hydra override {h_key_clean!r}: {h_val_clean!r}"
                    )
                extra_hydra_args.append(f"{h_key_clean}={h_val_clean}")
                continue
            raise FlagValidationError(
                f"Unsupported or disallowed rfd4-generate Hydra override: {stripped!r}"
            )
        converted_args.append(stripped)

    parser = _StrictArgumentParser(prog="rfd4-generate")
    parser.add_argument(
        "--design_mode",
        choices=[
            "binder_design",
            "motif_scaffolding",
            "unconditional_monomer",
            "unconditional_generation",
        ],
        default="binder_design",
    )
    parser.add_argument(
        "--num_designs_per_seed",
        "--num_replicates",
        dest="num_designs_per_seed",
        type=_int_in_range("--num_designs_per_seed", 1, 20),
        default=2,
    )
    parser.add_argument(
        "--num_model_seeds",
        "--num_seeds",
        dest="num_model_seeds",
        type=_int_in_range("--num_model_seeds", 1, 50),
        default=5,
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--binder_length_range", type=str, default="80..140")
    parser.add_argument("--target_chains", type=str, default=None)
    parser.add_argument("--binding_residues", type=str, default=None)
    parser.add_argument("--scaffold_motif", type=str, default=None)
    parser.add_argument(
        "--compute_metrics",
        "--metrics",
        dest="compute_metrics",
        nargs="?",
        const=True,
        type=_parse_bool,
        default=None,
    )
    parser.add_argument(
        "--fast-no-metrics",
        "--no-metrics",
        dest="fast_no_metrics",
        action="store_true",
        default=False,
    )

    ns, unknown = parser.parse_known_args(converted_args)
    if unknown:
        raise FlagValidationError(
            f"Unsupported or disallowed RFD4-Proteina flag(s): {' '.join(unknown)}"
        )

    if ns.fast_no_metrics or explicit_fast_override:
        compute_metrics = False
    elif ns.compute_metrics is not None:
        compute_metrics = bool(ns.compute_metrics)
    else:
        compute_metrics = False

    if compute_metrics:
        extra_hydra_args = [a for a in extra_hydra_args if a != "~metrics"]
        if "--metrics" not in extra_hydra_args:
            extra_hydra_args.append("--metrics")

    normalized_design_mode = (
        "unconditional_monomer" if ns.design_mode == "unconditional_generation" else ns.design_mode
    )
    pipeline_params: dict[str, Any] = {
        "design_mode": normalized_design_mode,
        "num_designs_per_seed": ns.num_designs_per_seed,
        "num_model_seeds": ns.num_model_seeds,
        "binder_length_range": ns.binder_length_range,
        "compute_metrics": compute_metrics,
    }
    if ns.seed is not None:
        pipeline_params["base_seed"] = ns.seed
    if ns.target_chains is not None:
        pipeline_params["target_chains"] = [
            c.strip() for c in ns.target_chains.split(",") if c.strip()
        ]
    if ns.binding_residues is not None:
        pipeline_params["binding_residues"] = [
            r.strip() for r in ns.binding_residues.split(",") if r.strip()
        ]
    if ns.scaffold_motif is not None:
        pipeline_params["scaffold_motif"] = ns.scaffold_motif

    normalized = {
        **pipeline_params,
        "seed": ns.seed,
        "extra_cli_args": sorted(extra_hydra_args),
    }
    return ValidatedModelFlags(
        pipeline_params=pipeline_params,
        extra_cli_args=extra_hydra_args,
        normalized_flags=normalized,
    )


def _validate_bindcraft_flags(cli_args: list[str]) -> ValidatedModelFlags:
    parser = _StrictArgumentParser(prog="bindcraft")
    parser.add_argument(
        "--num_designs_per_seed",
        "--number_of_final_designs",
        dest="num_designs_per_seed",
        type=_int_in_range("--num_designs_per_seed", 1, 20),
        default=2,
    )
    parser.add_argument(
        "--num_model_seeds",
        "--num_seeds",
        dest="num_model_seeds",
        type=_int_in_range("--num_model_seeds", 1, 50),
        default=5,
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--binder_length_range", type=str, default="80..140")
    parser.add_argument("--target_chains", type=str, default=None)
    parser.add_argument("--binding_residues", type=str, default=None)
    parser.add_argument(
        "--filters",
        type=lambda v: _validate_safe_rel_config_path(v, "--filters"),
        default=None,
    )
    parser.add_argument(
        "--advanced",
        type=lambda v: _validate_safe_rel_config_path(v, "--advanced"),
        default=None,
    )
    parser.add_argument(
        "--no-pyrosetta",
        "--no_pyrosetta",
        dest="no_pyrosetta",
        nargs="?",
        const=True,
        type=_parse_bool,
        default=True,
    )

    ns, unknown = parser.parse_known_args(cli_args)
    if unknown:
        raise FlagValidationError(
            f"Unsupported or disallowed BindCraft flag(s): {' '.join(unknown)}"
        )

    pipeline_params: dict[str, Any] = {
        "num_designs_per_seed": ns.num_designs_per_seed,
        "num_model_seeds": ns.num_model_seeds,
        "binder_length_range": ns.binder_length_range,
    }
    if ns.seed is not None:
        pipeline_params["base_seed"] = ns.seed
    if ns.target_chains is not None:
        pipeline_params["target_chains"] = [
            c.strip() for c in ns.target_chains.split(",") if c.strip()
        ]
    if ns.binding_residues is not None:
        pipeline_params["binding_residues"] = [
            r.strip() for r in ns.binding_residues.split(",") if r.strip()
        ]

    extra_cli_args: list[str] = []
    if ns.no_pyrosetta:
        extra_cli_args.append("--no-pyrosetta")
    if ns.filters is not None:
        extra_cli_args.append(f"--filters={ns.filters}")
    if ns.advanced is not None:
        extra_cli_args.append(f"--advanced={ns.advanced}")

    normalized: dict[str, Any] = {
        **pipeline_params,
        "seed": ns.seed,
        "no_pyrosetta": ns.no_pyrosetta,
    }
    if ns.filters is not None:
        normalized["filters"] = ns.filters
    if ns.advanced is not None:
        normalized["advanced"] = ns.advanced

    return ValidatedModelFlags(
        pipeline_params=pipeline_params,
        extra_cli_args=extra_cli_args,
        normalized_flags=normalized,
    )


def _validate_alphafold3_flags(cli_args: list[str]) -> ValidatedModelFlags:
    parser = _StrictArgumentParser(prog="alphafold3")
    parser.add_argument(
        "--num_diffusion_samples",
        type=_int_in_range("--num_diffusion_samples", 1, 25),
        default=5,
    )
    parser.add_argument(
        "--num_recycles",
        type=_int_in_range("--num_recycles", 1, 20),
        default=10,
    )
    parser.add_argument(
        "--num_seeds",
        "--num_model_seeds",
        dest="num_model_seeds",
        type=_int_in_range("--num_seeds", 1, 10),
        default=1,
    )
    parser.add_argument("--model_seeds", type=str, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--base_seed", type=int, default=None)
    parser.add_argument("--max_template_date", type=str, default="2021-09-30")
    parser.add_argument(
        "--run_data_pipeline",
        nargs="?",
        const=True,
        type=_parse_bool,
        default=True,
    )
    parser.add_argument(
        "--norun_data_pipeline",
        dest="run_data_pipeline",
        action="store_false",
    )
    parser.add_argument(
        "--conformer_max_iterations",
        type=_int_in_range("--conformer_max_iterations", 1, 1000),
        default=100,
    )
    parser.add_argument(
        "--save_embeddings",
        nargs="?",
        const=True,
        type=_parse_bool,
        default=False,
    )
    parser.add_argument(
        "--nosave_embeddings",
        dest="save_embeddings",
        action="store_false",
    )
    parser.add_argument(
        "--save_distogram",
        nargs="?",
        const=True,
        type=_parse_bool,
        default=False,
    )
    parser.add_argument(
        "--nosave_distogram",
        dest="save_distogram",
        action="store_false",
    )
    parser.add_argument(
        "--compress_large_output_files",
        nargs="?",
        const=True,
        type=_parse_bool,
        default=False,
    )
    parser.add_argument(
        "--nocompress_large_output_files",
        dest="compress_large_output_files",
        action="store_false",
    )
    parser.add_argument(
        "--force_output_dir",
        nargs="?",
        const=True,
        type=_parse_bool,
        default=True,
    )
    parser.add_argument(
        "--noforce_output_dir",
        dest="force_output_dir",
        action="store_false",
    )

    ns, unknown = parser.parse_known_args(cli_args)
    if unknown:
        raise FlagValidationError(
            f"Unsupported or disallowed alphafold3 flag(s): {' '.join(unknown)}"
        )

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", ns.max_template_date):
        raise FlagValidationError(
            f"--max_template_date must be YYYY-MM-DD, got {ns.max_template_date!r}"
        )

    parsed_model_seeds: list[int] | None = None
    if ns.model_seeds is not None:
        raw_tokens = [
            tok.strip() for tok in ns.model_seeds.strip("[]() ").split(",") if tok.strip()
        ]
        if not raw_tokens or len(raw_tokens) > 10:
            raise FlagValidationError(
                f"--model_seeds must specify between 1 and 10 integer seeds, got {ns.model_seeds!r}"
            )
        try:
            parsed_model_seeds = [int(tok) for tok in raw_tokens]
        except ValueError as exc:
            raise FlagValidationError(
                f"--model_seeds must be comma-separated integers, got {ns.model_seeds!r}"
            ) from exc

    effective_num_seeds = len(parsed_model_seeds) if parsed_model_seeds else ns.num_model_seeds

    pipeline_params: dict[str, Any] = {
        "num_diffusion_samples": ns.num_diffusion_samples,
        "num_recycles": ns.num_recycles,
        "num_model_seeds": effective_num_seeds,
        "max_template_date": ns.max_template_date,
        "run_data_pipeline": ns.run_data_pipeline,
        "conformer_max_iterations": ns.conformer_max_iterations,
        "save_embeddings": ns.save_embeddings,
        "save_distogram": ns.save_distogram,
        "compress_large_output_files": ns.compress_large_output_files,
    }
    if parsed_model_seeds is not None:
        pipeline_params["model_seeds"] = parsed_model_seeds
    if ns.seed is not None:
        pipeline_params["base_seed"] = ns.seed
    elif ns.base_seed is not None:
        pipeline_params["base_seed"] = ns.base_seed

    normalized: dict[str, Any] = {
        "num_diffusion_samples": ns.num_diffusion_samples,
        "num_recycles": ns.num_recycles,
        "num_model_seeds": effective_num_seeds,
        "seed": ns.seed if ns.seed is not None else ns.base_seed,
        "max_template_date": ns.max_template_date,
        "run_data_pipeline": ns.run_data_pipeline,
        "conformer_max_iterations": ns.conformer_max_iterations,
        "save_embeddings": ns.save_embeddings,
        "save_distogram": ns.save_distogram,
        "compress_large_output_files": ns.compress_large_output_files,
    }
    if parsed_model_seeds is not None:
        normalized["model_seeds"] = parsed_model_seeds

    return ValidatedModelFlags(
        pipeline_params=pipeline_params,
        extra_cli_args=[],
        normalized_flags=normalized,
    )
