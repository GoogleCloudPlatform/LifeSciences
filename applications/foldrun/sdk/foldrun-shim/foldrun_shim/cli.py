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

"""Native CLI entrypoints for `boltz`, `run_openfold`, `run_alphafold.py`, `alphafold3`, `boltzgen`, `rfd4-generate`, and `bindcraft`."""

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from .client import (
    _RFD4_FAST_MODE_NOTICE,
    GatewayClient,
    _extract_bindcraft_design_path,
)


class StrippedArgs(tuple):
    """Tuple subclass preserving legacy positional unpacking while attaching hardware/scheduling attributes."""

    gpu_type: str | None
    enable_flex_start: bool | None
    fallback_to_ondemand: bool | None

    def __new__(
        cls,
        values: tuple[Any, ...],
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
    ) -> "StrippedArgs":
        instance = super().__new__(cls, values)
        instance.gpu_type = gpu_type
        instance.enable_flex_start = enable_flex_start
        instance.fallback_to_ondemand = fallback_to_ondemand
        return instance


def _strip_local_boltz_args(
    raw_args: list[str],
) -> StrippedArgs:
    """Extract positional DATA, --out_dir, --override, --gpu_type, --[no-]flex-start, and return sanitized scientific CLI args."""
    parser = argparse.ArgumentParser(prog="boltz predict", allow_abbrev=False)
    parser.add_argument("subcommand_or_data", help="Subcommand ('predict') or input path")
    parser.add_argument("data_if_subcommand", nargs="?", default=None)
    parser.add_argument("--out_dir", default="./")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--devices", default=None)
    parser.add_argument("--accelerator", default=None)
    parser.add_argument("--num_workers", default=None)
    parser.add_argument("--use_msa_server", action="store_true", default=False)
    parser.add_argument("--msa_server_url", default=None)
    parser.add_argument("--override", action="store_true", default=False)
    parser.add_argument("--gpu_type", default=None)
    parser.add_argument("--flex-start", dest="enable_flex_start", action="store_true", default=None)
    parser.add_argument(
        "--no-flex-start", dest="enable_flex_start", action="store_false", default=None
    )
    parser.add_argument(
        "--fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_false",
        default=None,
    )

    known, remaining = parser.parse_known_args(raw_args)
    if known.subcommand_or_data == "predict":
        if not known.data_if_subcommand:
            parser.error("the following arguments are required: DATA")
        data_path = known.data_if_subcommand
    else:
        data_path = known.subcommand_or_data

    if known.use_msa_server:
        print(
            "[foldrun-shim] Note: --use_msa_server ignored; FoldRun executes MSA searches "
            "against private Cloud Filestore NFS.",
            file=sys.stderr,
        )

    return StrippedArgs(
        (
            data_path,
            known.out_dir,
            bool(known.override),
            remaining,
        ),
        gpu_type=known.gpu_type,
        enable_flex_start=known.enable_flex_start,
        fallback_to_ondemand=known.fallback_to_ondemand,
    )


def _strip_local_openfold3_args(
    raw_args: list[str],
) -> StrippedArgs:
    """Extract --query_json, --output_dir, --runner_yaml, --override, --gpu_type, --[no-]flex-start, and scientific CLI args."""
    parser = argparse.ArgumentParser(prog="run_openfold", allow_abbrev=False)
    parser.add_argument("subcommand", nargs="?", default="predict")
    parser.add_argument("--query_json", required=True)
    parser.add_argument("--output_dir", "--out_dir", dest="output_dir", default="./")
    parser.add_argument("--runner_yaml", default=None)
    parser.add_argument("--inference_ckpt_path", default=None)
    parser.add_argument("--data_dir", default=None)
    parser.add_argument("--override", action="store_true", default=False)
    parser.add_argument("--gpu_type", default=None)
    parser.add_argument("--flex-start", dest="enable_flex_start", action="store_true", default=None)
    parser.add_argument(
        "--no-flex-start", dest="enable_flex_start", action="store_false", default=None
    )
    parser.add_argument(
        "--fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_false",
        default=None,
    )

    known, remaining = parser.parse_known_args(raw_args)
    return StrippedArgs(
        (
            known.query_json,
            known.output_dir,
            known.runner_yaml,
            bool(known.override),
            remaining,
        ),
        gpu_type=known.gpu_type,
        enable_flex_start=known.enable_flex_start,
        fallback_to_ondemand=known.fallback_to_ondemand,
    )


_AF2_LOCAL_PATH_FLAG_RE = re.compile(
    r"^--(?:data_dir|.*_database_path|.*_binary_path|pdb70_database_path|"
    r"template_mmcif_dir|obsolete_pdbs_path|use_gpu_relax|nouse_gpu_relax|"
    r"use_precomputed_msas|nouse_precomputed_msas)(?:=|$)"
)

_BOOL_LITERALS = frozenset({"true", "false", "1", "0", "yes", "no"})
_TRUE_LITERALS = frozenset({"true", "1", "yes"})

_AF2_BOOL_FLAGS = frozenset(
    {
        "benchmark",
        "save_embeddings",
        "save_distogram",
        "compress_large_output_files",
    }
)

_AF3_BOOL_FLAGS = frozenset(
    {
        "run_data_pipeline",
        "run_inference",
        "save_embeddings",
        "save_distogram",
        "compress_large_output_files",
        "force_output_dir",
    }
)


def _normalize_absl_boolean_args(
    tokens: list[str],
    *,
    bool_flags: frozenset[str],
    strip_true_flags: frozenset[str] = frozenset(),
    map_run_relax: bool = False,
) -> list[str]:
    """Normalize absl-style boolean flags (`--flag`, `--flag true`, `--noflag`) to `--flag=true|false`."""
    has_models_to_relax = any(
        t == "--models_to_relax" or t.startswith("--models_to_relax=") for t in tokens
    )
    normalized: list[str] = []
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if map_run_relax:
            if tok == "--norun_relax":
                if not has_models_to_relax:
                    normalized.append("--models_to_relax=none")
                    has_models_to_relax = True
                idx += 1
                continue
            if tok == "--run_relax" or tok.startswith("--run_relax="):
                val_str = "true"
                step = 1
                if "=" in tok:
                    val_str = tok.split("=", 1)[1].strip().lower()
                elif idx + 1 < len(tokens) and tokens[idx + 1].strip().lower() in _BOOL_LITERALS:
                    val_str = tokens[idx + 1].strip().lower()
                    step = 2
                if not has_models_to_relax:
                    relax_mode = "best" if val_str in _TRUE_LITERALS else "none"
                    normalized.append(f"--models_to_relax={relax_mode}")
                    has_models_to_relax = True
                idx += step
                continue

        if (
            tok.startswith("--no")
            and not tok.startswith("--no-")
            and "=" not in tok
            and len(tok) > 4
        ):
            base_name = tok[4:]
            normalized.append(f"--{base_name}=false")
            idx += 1
            continue

        if tok.startswith("--"):
            if "=" in tok:
                flag_part, val_part = tok.split("=", 1)
                bare_name = flag_part[2:]
                if bare_name in bool_flags and val_part.strip().lower() in _BOOL_LITERALS:
                    is_true = val_part.strip().lower() in _TRUE_LITERALS
                    if is_true and bare_name in strip_true_flags:
                        idx += 1
                        continue
                    normalized.append(f"--{bare_name}={'true' if is_true else 'false'}")
                    idx += 1
                    continue
            else:
                bare_name = tok[2:]
                if bare_name in bool_flags:
                    is_true = True
                    step = 1
                    if idx + 1 < len(tokens) and tokens[idx + 1].strip().lower() in _BOOL_LITERALS:
                        is_true = tokens[idx + 1].strip().lower() in _TRUE_LITERALS
                        step = 2
                    if is_true and bare_name in strip_true_flags:
                        idx += step
                        continue
                    normalized.append(f"--{bare_name}={'true' if is_true else 'false'}")
                    idx += step
                    continue

        normalized.append(tok)
        idx += 1
    return normalized


def _strip_local_alphafold2_args(
    raw_args: list[str],
) -> StrippedArgs:
    """Extract --fasta_paths, --output_dir, --override, --gpu_type, --[no-]flex-start, strip local DB paths, keep scientific flags."""
    parser = argparse.ArgumentParser(prog="run_alphafold.py", allow_abbrev=False)
    parser.add_argument("--fasta_paths", required=True)
    parser.add_argument("--output_dir", "--out_dir", dest="output_dir", default="./")
    parser.add_argument("--override", action="store_true", default=False)
    parser.add_argument("--gpu_type", default=None)
    parser.add_argument("--flex-start", dest="enable_flex_start", action="store_true", default=None)
    parser.add_argument(
        "--no-flex-start", dest="enable_flex_start", action="store_false", default=None
    )
    parser.add_argument(
        "--fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_false",
        default=None,
    )

    known, remaining = parser.parse_known_args(raw_args)
    fasta_list = [p.strip() for p in known.fasta_paths.split(",") if p.strip()]
    if not fasta_list:
        parser.error("--fasta_paths must contain at least one FASTA file path")

    filtered: list[str] = []
    skip_next = False
    for idx, token in enumerate(remaining):
        if skip_next:
            skip_next = False
            continue
        if _AF2_LOCAL_PATH_FLAG_RE.match(token):
            if (
                "=" not in token
                and (idx + 1) < len(remaining)
                and not remaining[idx + 1].startswith("--")
            ):
                skip_next = True
            continue
        filtered.append(token)

    normalized = _normalize_absl_boolean_args(
        filtered,
        bool_flags=_AF2_BOOL_FLAGS,
        map_run_relax=True,
    )

    return StrippedArgs(
        (
            fasta_list,
            known.output_dir,
            bool(known.override),
            normalized,
        ),
        gpu_type=known.gpu_type,
        enable_flex_start=known.enable_flex_start,
        fallback_to_ondemand=known.fallback_to_ondemand,
    )


def boltz_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
    poll_interval_sec: float = 15.0,
) -> int:
    """Entrypoint for `boltz predict <DATA> [OPTIONS]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    stripped = _strip_local_boltz_args(args)
    data_str, out_dir, override, scientific_args = stripped
    gpu_type = stripped.gpu_type
    enable_flex_start = stripped.enable_flex_start
    fallback_to_ondemand = stripped.fallback_to_ondemand
    client = GatewayClient(
        gateway_url=gateway_url,
        http_client=http_client,
        poll_interval_sec=poll_interval_sec,
    )

    target = Path(data_str).expanduser().resolve()
    if target.is_dir():
        inputs = sorted(
            p
            for p in target.iterdir()
            if p.is_file() and p.suffix.lower() in (".yaml", ".yml", ".fasta", ".fa")
        )
    else:
        inputs = [target]

    for idx, input_file in enumerate(inputs):
        state_file = (
            ".foldrun_job.json"
            if len(inputs) == 1
            else f".foldrun_job_{input_file.stem}_{idx}.json"
        )
        res = client.run_prediction(
            model="boltz",
            input_path=input_file,
            out_dir=out_dir,
            cli_args=scientific_args,
            override=override,
            gpu_type=gpu_type,
            enable_flex_start=enable_flex_start,
            fallback_to_ondemand=fallback_to_ondemand,
            state_filename=state_file,
        )
        print(f"[foldrun-shim] Boltz-2 job {res.job_id} completed -> {res.out_dir}")
    return 0


def openfold3_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
    poll_interval_sec: float = 15.0,
) -> int:
    """Entrypoint for `run_openfold predict --query_json=... [OPTIONS]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    stripped = _strip_local_openfold3_args(args)
    query_json, output_dir, runner_yaml, override, scientific_args = stripped
    gpu_type = stripped.gpu_type
    enable_flex_start = stripped.enable_flex_start
    fallback_to_ondemand = stripped.fallback_to_ondemand
    client = GatewayClient(
        gateway_url=gateway_url,
        http_client=http_client,
        poll_interval_sec=poll_interval_sec,
    )
    res = client.run_prediction(
        model="openfold3",
        input_path=query_json,
        out_dir=output_dir,
        cli_args=scientific_args,
        runner_yaml_path=runner_yaml,
        override=override,
        gpu_type=gpu_type,
        enable_flex_start=enable_flex_start,
        fallback_to_ondemand=fallback_to_ondemand,
    )
    print(f"[foldrun-shim] OpenFold 3 job {res.job_id} completed -> {res.out_dir}")
    return 0


def alphafold2_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
    poll_interval_sec: float = 15.0,
) -> int:
    """Entrypoint for `run_alphafold.py` (`--fasta_paths=...` -> AF2, `--json_path=...` -> AF3)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a == "--json_path" or a.startswith("--json_path=") for a in args):
        return alphafold3_main(
            args,
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
    stripped = _strip_local_alphafold2_args(args)
    fasta_list, output_dir, override, scientific_args = stripped
    gpu_type = stripped.gpu_type
    enable_flex_start = stripped.enable_flex_start
    fallback_to_ondemand = stripped.fallback_to_ondemand
    client = GatewayClient(
        gateway_url=gateway_url,
        http_client=http_client,
        poll_interval_sec=poll_interval_sec,
    )
    for idx, fasta_path in enumerate(fasta_list):
        stem = Path(fasta_path).stem
        state_file = (
            ".foldrun_job.json" if len(fasta_list) == 1 else f".foldrun_job_{stem}_{idx}.json"
        )
        res = client.run_prediction(
            model="alphafold2",
            input_path=fasta_path,
            out_dir=output_dir,
            cli_args=scientific_args,
            override=override,
            gpu_type=gpu_type,
            enable_flex_start=enable_flex_start,
            fallback_to_ondemand=fallback_to_ondemand,
            state_filename=state_file,
        )
        print(f"[foldrun-shim] AlphaFold 2 job {res.job_id} completed -> {res.out_dir}")
    return 0


def _strip_local_boltzgen_args(
    raw_args: list[str],
) -> StrippedArgs:
    """Extract subcommand ('run' or 'check'), spec path, --output/--out_dir, --override, --gpu_type, --[no-]flex-start, and scientific flags."""
    parser = argparse.ArgumentParser(prog="boltzgen", allow_abbrev=False)
    parser.add_argument("subcommand_or_spec", help="Subcommand ('run', 'check') or spec path")
    parser.add_argument("spec_if_subcommand", nargs="?", default=None)
    parser.add_argument("--output", "--out_dir", "--output_dir", dest="out_dir", default="./")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--devices", default=None)
    parser.add_argument("--accelerator", default=None)
    parser.add_argument("--num_workers", default=None)
    parser.add_argument("--override", action="store_true", default=False)
    parser.add_argument("--gpu_type", default=None)
    parser.add_argument("--flex-start", dest="enable_flex_start", action="store_true", default=None)
    parser.add_argument(
        "--no-flex-start", dest="enable_flex_start", action="store_false", default=None
    )
    parser.add_argument(
        "--fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_false",
        default=None,
    )

    known, remaining = parser.parse_known_args(raw_args)
    if known.subcommand_or_spec in ("run", "check"):
        if not known.spec_if_subcommand:
            parser.error("the following arguments are required: SPEC_YAML")
        subcommand = known.subcommand_or_spec
        spec_path = known.spec_if_subcommand
    else:
        subcommand = "run"
        spec_path = known.subcommand_or_spec

    return StrippedArgs(
        (
            subcommand,
            spec_path,
            known.out_dir,
            bool(known.override),
            remaining,
        ),
        gpu_type=known.gpu_type,
        enable_flex_start=known.enable_flex_start,
        fallback_to_ondemand=known.fallback_to_ondemand,
    )


def _strip_local_rfd4_proteina_args(
    raw_args: list[str],
) -> StrippedArgs:
    """Extract inputs=<spec>/--inputs=<spec>/<spec>, out_dir, override, and scientific/Hydra args."""
    input_path: str | None = None
    out_dir = "./"
    override = False
    gpu_type: str | None = None
    enable_flex_start: bool | None = None
    fallback_to_ondemand: bool | None = None
    compute_metrics_val: bool | None = None
    scientific_args: list[str] = []

    idx = 0
    while idx < len(raw_args):
        token = raw_args[idx].strip()
        bare_token = token.lstrip("+")
        if token in ("--inputs", "--input"):
            if idx + 1 >= len(raw_args):
                raise ValueError(f"{token} requires a file path argument")
            input_path = raw_args[idx + 1]
            idx += 2
            continue
        if bare_token.startswith(("--inputs=", "--input=", "inputs=", "input=")):
            input_path = bare_token.split("=", 1)[1]
            idx += 1
            continue
        if token in ("--out_dir", "--output_dir", "--output", "-o"):
            if idx + 1 >= len(raw_args):
                raise ValueError(f"{token} requires a directory argument")
            out_dir = raw_args[idx + 1]
            idx += 2
            continue
        if bare_token.startswith(
            (
                "--out_dir=",
                "--output_dir=",
                "--output=",
                "out_dir=",
                "output_dir=",
                "output=",
            )
        ):
            out_dir = bare_token.split("=", 1)[1]
            idx += 1
            continue
        if token in ("--metrics", "--compute_metrics"):
            if idx + 1 < len(raw_args) and raw_args[idx + 1].strip().lower() in _BOOL_LITERALS:
                compute_metrics_val = raw_args[idx + 1].strip().lower() in _TRUE_LITERALS
                idx += 2
            else:
                compute_metrics_val = True
                idx += 1
            continue
        if bare_token.startswith(
            ("--compute_metrics=", "--metrics=", "compute_metrics=", "metrics=")
        ):
            val_str = bare_token.split("=", 1)[1].strip().lower()
            compute_metrics_val = val_str in _TRUE_LITERALS
            idx += 1
            continue
        if token in (
            "--fast-no-metrics",
            "--no-metrics",
            "--nometrics",
            "--no_metrics",
            "~metrics",
            "'~metrics'",
            '"~metrics"',
        ):
            compute_metrics_val = False
            idx += 1
            continue
        if token in (
            "--hf_ckpt_path",
            "--hf_ckpt_revision",
            "--ckpt_path",
            "--cache",
            "--data_dir",
            "--config-path",
            "--config-name",
            "--config_path",
            "--config_name",
            "-cp",
            "-cn",
        ):
            idx += 2
            continue
        if bare_token.startswith(
            (
                "--hf_ckpt_path=",
                "hf_ckpt_path=",
                "--hf_ckpt_revision=",
                "hf_ckpt_revision=",
                "--ckpt_path=",
                "ckpt_path=",
                "--cache=",
                "cache=",
                "--data_dir=",
                "data_dir=",
                "--config-path=",
                "config_path=",
                "--config-name=",
                "config_name=",
            )
        ):
            idx += 1
            continue
        if token == "--override":
            override = True
            idx += 1
            continue
        if token.startswith(("override=", "--override=")):
            override = token.split("=", 1)[1].strip().lower() in ("true", "1", "yes")
            idx += 1
            continue
        if token == "--gpu_type":
            if idx + 1 < len(raw_args):
                gpu_type = raw_args[idx + 1]
            idx += 2
            continue
        if token.startswith(("--gpu_type=", "gpu_type=")):
            gpu_type = token.split("=", 1)[1]
            idx += 1
            continue
        if token in ("--flex-start", "--flex_start"):
            enable_flex_start = True
            idx += 1
            continue
        if token in ("--no-flex-start", "--no_flex_start"):
            enable_flex_start = False
            idx += 1
            continue
        if token.startswith(("--enable_flex_start=", "enable_flex_start=")):
            enable_flex_start = token.split("=", 1)[1].strip().lower() in (
                "true",
                "1",
                "yes",
            )
            idx += 1
            continue
        if token in ("--fallback-to-ondemand", "--fallback_to_ondemand"):
            fallback_to_ondemand = True
            idx += 1
            continue
        if token in ("--no-fallback-to-ondemand", "--no_fallback_to_ondemand"):
            fallback_to_ondemand = False
            idx += 1
            continue
        if token.startswith(("--fallback_to_ondemand=", "fallback_to_ondemand=")):
            fallback_to_ondemand = token.split("=", 1)[1].strip().lower() in (
                "true",
                "1",
                "yes",
            )
            idx += 1
            continue
        if (
            input_path is None
            and not token.startswith("-")
            and not token.startswith("~")
            and "=" not in token
        ):
            input_path = token
            idx += 1
            continue
        scientific_args.append(raw_args[idx])
        idx += 1

    if not input_path:
        raise ValueError(
            "rfd4-generate requires an input specification via inputs=<spec.yaml> or --inputs=<spec.yaml>"
        )

    if compute_metrics_val is True:
        scientific_args.append("compute_metrics=true")
    else:
        scientific_args.append("compute_metrics=false")

    return StrippedArgs(
        (input_path, out_dir, bool(override), scientific_args),
        gpu_type=gpu_type,
        enable_flex_start=enable_flex_start,
        fallback_to_ondemand=fallback_to_ondemand,
    )


def _strip_local_bindcraft_args(
    raw_args: list[str],
) -> StrippedArgs:
    """Extract --settings, preserve --filters/--advanced for bundling, resolve out_dir/design_path, keep scientific args."""
    parser = argparse.ArgumentParser(prog="bindcraft", allow_abbrev=False)
    parser.add_argument("positional_settings", nargs="?", default=None)
    parser.add_argument("--settings", "-s", default=None)
    parser.add_argument("--filters", "-f", default=None)
    parser.add_argument("--advanced", "-a", default=None)
    parser.add_argument("--out_dir", "--output_dir", "--output", "-o", dest="out_dir", default=None)
    parser.add_argument("--override", action="store_true", default=False)
    parser.add_argument("--gpu_type", default=None)
    parser.add_argument("--flex-start", dest="enable_flex_start", action="store_true", default=None)
    parser.add_argument(
        "--no-flex-start", dest="enable_flex_start", action="store_false", default=None
    )
    parser.add_argument(
        "--fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_false",
        default=None,
    )

    known, remaining = parser.parse_known_args(raw_args)
    settings_path = known.settings or known.positional_settings
    if not settings_path:
        parser.error("the following arguments are required: --settings")

    resolved_out_dir = known.out_dir
    if resolved_out_dir is None:
        resolved_out_dir = _extract_bindcraft_design_path(settings_path) or "./"

    forwarded = list(remaining)
    if known.filters:
        forwarded.extend(["--filters", known.filters])
    if known.advanced:
        forwarded.extend(["--advanced", known.advanced])

    return StrippedArgs(
        (
            settings_path,
            resolved_out_dir,
            bool(known.override),
            forwarded,
        ),
        gpu_type=known.gpu_type,
        enable_flex_start=known.enable_flex_start,
        fallback_to_ondemand=known.fallback_to_ondemand,
    )


def boltzgen_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
    poll_interval_sec: float = 15.0,
) -> int:
    """Entrypoint for `boltzgen run <spec.yaml> [OPTIONS]` and `boltzgen check <spec.yaml>`."""
    args = list(sys.argv[1:] if argv is None else argv)
    stripped = _strip_local_boltzgen_args(args)
    subcommand, spec_path, out_dir, override, scientific_args = stripped
    gpu_type = stripped.gpu_type
    enable_flex_start = stripped.enable_flex_start
    fallback_to_ondemand = stripped.fallback_to_ondemand

    resolved_spec = Path(spec_path).expanduser().resolve()
    if not resolved_spec.exists() or not resolved_spec.is_file():
        raise FileNotFoundError(f"BoltzGen spec file does not exist: {spec_path}")

    if subcommand == "check":
        loaded = yaml.safe_load(resolved_spec.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or (
            "entities" not in loaded and "sequences" not in loaded and "protocol" not in loaded
        ):
            raise ValueError(
                f"Invalid BoltzGen YAML specification in {spec_path}: must contain 'entities' or 'sequences'."
            )
        print(f"[foldrun-shim] BoltzGen specification '{resolved_spec.name}' passed validation.")
        return 0

    client = GatewayClient(
        gateway_url=gateway_url,
        http_client=http_client,
        poll_interval_sec=poll_interval_sec,
    )
    res = client.run_prediction(
        model="boltzgen",
        input_path=resolved_spec,
        out_dir=out_dir,
        cli_args=scientific_args,
        override=override,
        gpu_type=gpu_type,
        enable_flex_start=enable_flex_start,
        fallback_to_ondemand=fallback_to_ondemand,
    )
    print(f"[foldrun-shim] BoltzGen job {res.job_id} completed -> {res.out_dir}")
    return 0


def rfd4_proteina_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
    poll_interval_sec: float = 15.0,
) -> int:
    """Entrypoint for `rfd4-generate inputs=<spec.yaml> [OPTIONS]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    stripped = _strip_local_rfd4_proteina_args(args)
    spec_path, out_dir, override, scientific_args = stripped
    gpu_type = stripped.gpu_type
    enable_flex_start = stripped.enable_flex_start
    fallback_to_ondemand = stripped.fallback_to_ondemand
    if "compute_metrics=true" not in scientific_args:
        print(_RFD4_FAST_MODE_NOTICE)
    client = GatewayClient(
        gateway_url=gateway_url,
        http_client=http_client,
        poll_interval_sec=poll_interval_sec,
    )
    res = client.run_prediction(
        model="rfd4_proteina",
        input_path=spec_path,
        out_dir=out_dir,
        cli_args=scientific_args,
        override=override,
        gpu_type=gpu_type,
        enable_flex_start=enable_flex_start,
        fallback_to_ondemand=fallback_to_ondemand,
    )
    print(f"[foldrun-shim] RFD4-Proteina job {res.job_id} completed -> {res.out_dir}")
    return 0


def bindcraft_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
    poll_interval_sec: float = 15.0,
) -> int:
    """Entrypoint for `bindcraft --settings=<settings.json> [OPTIONS]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    stripped = _strip_local_bindcraft_args(args)
    settings_path, out_dir, override, scientific_args = stripped
    gpu_type = stripped.gpu_type
    enable_flex_start = stripped.enable_flex_start
    fallback_to_ondemand = stripped.fallback_to_ondemand
    client = GatewayClient(
        gateway_url=gateway_url,
        http_client=http_client,
        poll_interval_sec=poll_interval_sec,
    )
    res = client.run_prediction(
        model="bindcraft",
        input_path=settings_path,
        out_dir=out_dir,
        cli_args=scientific_args,
        override=override,
        gpu_type=gpu_type,
        enable_flex_start=enable_flex_start,
        fallback_to_ondemand=fallback_to_ondemand,
    )
    print(f"[foldrun-shim] BindCraft job {res.job_id} completed -> {res.out_dir}")
    return 0


def _strip_local_alphafold3_args(
    raw_args: list[str],
) -> StrippedArgs:
    """Extract --json_path / --fasta_paths / positional input, --output_dir, --override, --gpu_type, --[no-]flex-start, and scientific CLI args."""
    parser = argparse.ArgumentParser(prog="alphafold3", allow_abbrev=False)
    parser.add_argument("positional_input", nargs="?", default=None)
    parser.add_argument("--json_path", default=None)
    parser.add_argument("--fasta_paths", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--output_dir", "--out_dir", dest="output_dir", default="./af3_results")
    parser.add_argument("--override", action="store_true", default=False)
    parser.add_argument("--gpu_type", default=None)
    parser.add_argument("--flex-start", dest="enable_flex_start", action="store_true", default=None)
    parser.add_argument(
        "--no-flex-start", dest="enable_flex_start", action="store_false", default=None
    )
    parser.add_argument(
        "--fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-fallback-to-ondemand",
        dest="fallback_to_ondemand",
        action="store_false",
        default=None,
    )
    # Strip upstream local AF3 database/model/cache flags
    parser.add_argument("--model_dir", default=None)
    parser.add_argument("--db_dir", default=None)
    parser.add_argument("--input_dir", default=None)
    parser.add_argument("--jax_compilation_cache_dir", default=None)
    parser.add_argument("--buckets", default=None)
    parser.add_argument("--flash_attention_implementation", default=None)

    known, remaining = parser.parse_known_args(raw_args)
    filtered_remaining: list[str] = []
    idx = 0
    while idx < len(remaining):
        tok = remaining[idx]
        if _AF2_LOCAL_PATH_FLAG_RE.match(tok):
            if (
                "=" not in tok
                and (idx + 1) < len(remaining)
                and not remaining[idx + 1].startswith("--")
            ):
                idx += 2
            else:
                idx += 1
            continue
        filtered_remaining.append(tok)
        idx += 1

    normalized_remaining = _normalize_absl_boolean_args(
        filtered_remaining,
        bool_flags=_AF3_BOOL_FLAGS,
        strip_true_flags=frozenset({"run_inference"}),
    )

    input_path = known.json_path or known.fasta_paths or known.input or known.positional_input
    if not input_path:
        parser.error(
            "An input file must be specified via --json_path, --fasta_paths, or positional argument"
        )

    return StrippedArgs(
        (
            input_path,
            known.output_dir,
            bool(known.override),
            normalized_remaining,
        ),
        gpu_type=known.gpu_type,
        enable_flex_start=known.enable_flex_start,
        fallback_to_ondemand=known.fallback_to_ondemand,
    )


def alphafold3_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
    poll_interval_sec: float = 15.0,
) -> int:
    """Entrypoint for `alphafold3 [--json_path=<input.json>] [OPTIONS]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    stripped = _strip_local_alphafold3_args(args)
    input_path, out_dir, override, scientific_args = stripped
    gpu_type = stripped.gpu_type
    enable_flex_start = stripped.enable_flex_start
    fallback_to_ondemand = stripped.fallback_to_ondemand
    client = GatewayClient(
        gateway_url=gateway_url,
        http_client=http_client,
        poll_interval_sec=poll_interval_sec,
    )
    res = client.run_prediction(
        model="alphafold3",
        input_path=input_path,
        out_dir=out_dir,
        cli_args=scientific_args,
        override=override,
        gpu_type=gpu_type,
        enable_flex_start=enable_flex_start,
        fallback_to_ondemand=fallback_to_ondemand,
    )
    print(f"[foldrun-shim] AlphaFold 3 job {res.job_id} completed -> {res.out_dir}")
    return 0


main_alphafold2 = alphafold2_main
main_alphafold3 = alphafold3_main


def foldrun_main(
    argv: list[str] | None = None,
    *,
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> int:
    """CLI utility for managing FoldRun jobs (e.g. `foldrun cancel <job_id>`, `foldrun status <job_id>`)."""
    parser = argparse.ArgumentParser(prog="foldrun", description="FoldRun Gateway Management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cancel_parser = subparsers.add_parser("cancel", help="Cancel an active or queued FoldRun job")
    cancel_parser.add_argument("job_id", help="FoldRun job ID or Vertex AI PipelineJob name")

    status_parser = subparsers.add_parser("status", help="Get status of a FoldRun job")
    status_parser.add_argument("job_id", help="FoldRun job ID or Vertex AI PipelineJob name")

    subparsers.add_parser(
        "gpus",
        help="Inspect live GPU availability, active/queued CustomJob counts, and regional GPU quotas",
    )

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    client = GatewayClient(gateway_url=gateway_url, http_client=http_client)

    if args.command == "cancel":
        print(f"[foldrun] Cancelling job '{args.job_id}'...")
        res = client.cancel_job(args.job_id)
        print(f"[foldrun] Status: {res.get('status')}")
        print(f"[foldrun] Message: {res.get('message')}")
        if res.get("cancelled_resources"):
            print("[foldrun] Cancelled resources:")
            for r in res["cancelled_resources"]:
                print(f"  - {r}")
        return 0
    elif args.command == "status":
        resp = client._request("GET", f"/v1/jobs/{args.job_id}")
        if resp.status_code != 200:
            print(
                f"[foldrun] Error getting status (HTTP {resp.status_code}): {resp.text}",
                file=sys.stderr,
            )
            return 1
        data = resp.json()
        print(f"[foldrun] Job ID:          {data.get('job_id')}")
        print(f"[foldrun] Model:           {data.get('model')}")
        print(f"[foldrun] State:           {data.get('state')}")
        print(f"[foldrun] Execution Phase: {data.get('execution_phase', 'UNKNOWN')}")
        print(f"[foldrun] Progress:        {data.get('progress_percent', 0.0):.1f}%")
        print(f"[foldrun] Step:            {data.get('current_step')}")
        tasks = data.get("tasks") or []
        if tasks:
            print(f"\n[foldrun] Pipeline Tasks ({len(tasks)} total):")
            print(
                f"  {'TASK':<26} | {'STATE':<14} | {'STARTED (UTC)':<20} | {'ENDED (UTC)':<20} | {'CUSTOM JOB ID'}"
            )
            print(f"  {'-' * 26}-+-{'-' * 14}-+-{'-' * 20}-+-{'-' * 20}-+-{'-' * 22}")
            for t in tasks:
                t_name = str(t.get("task_name") or t.get("name") or "")[:26]
                t_state = str(t.get("state") or "")[:14]
                t_start = str(t.get("start_time") or "-")[:19].replace("T", " ")
                t_end = str(t.get("end_time") or "-")[:19].replace("T", " ")
                cjob = str(t.get("custom_job_id") or t.get("custom_job_resource") or "-")
                if "/" in cjob:
                    cjob = cjob.rsplit("/", 1)[-1]
                print(f"  {t_name:<26} | {t_state:<14} | {t_start:<20} | {t_end:<20} | {cjob}")
        if data.get("queue_notice"):
            print(f"\n[foldrun] Notice:\n{data.get('queue_notice')}")
        return 0
    elif args.command == "gpus":
        try:
            status = client.check_gpus()
            print(str(status))
            return 0
        except Exception as exc:
            print(f"[foldrun] Error checking GPU status: {exc}", file=sys.stderr)
            return 1
    return 0
