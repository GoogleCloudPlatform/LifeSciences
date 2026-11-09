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

"""Local asset bundler, path containment validator, and canonical digest computation."""

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MAX_ASSETS_FILES = 50
MAX_ASSETS_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MB
_PDB_ID_OR_FILE_RE = re.compile(r"^[0-9][A-Za-z0-9]{3}(?:\.(?:cif|pdb|bcif))?$", re.IGNORECASE)


@dataclass
class BundledInput:
    """Bundled local input file, optional assets.tar.gz bytes, and deterministic digest."""

    model: str
    input_path: Path
    input_filename: str
    primary_bytes: bytes
    assets_tar_bytes: bytes | None
    assets_map: dict[str, bytes] = field(default_factory=dict)
    cli_args: list[str] = field(default_factory=list)
    runner_yaml_overrides: dict[str, Any] = field(default_factory=dict)
    client_input_sha256: str = ""


def _resolve_and_collect_asset(
    base_dir: Path,
    raw_ref: str,
    assets_map: dict[str, bytes],
) -> str:
    """Resolve a referenced file relative to base_dir, enforce containment, and read bytes."""
    cleaned = raw_ref.strip().replace("\\", "/")
    if cleaned.startswith("/") or cleaned.startswith("~"):
        raise ValueError(
            f"Referenced asset path {raw_ref!r} must be a relative path inside {base_dir}"
        )

    candidate = (base_dir / cleaned).resolve()
    effective_base = base_dir
    if not candidate.exists():
        cwd_base = Path.cwd().resolve()
        cwd_candidate = (cwd_base / cleaned).resolve()
        if cwd_candidate.exists() and cwd_candidate.is_relative_to(cwd_base):
            candidate = cwd_candidate
            effective_base = cwd_base

    if not candidate.is_relative_to(effective_base):
        raise ValueError(
            f"Path traversal detected: {raw_ref!r} resolves outside input directory {effective_base}"
        )
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"Referenced asset file {raw_ref!r} does not exist at {candidate}")

    rel_posix = candidate.relative_to(effective_base).as_posix()
    if rel_posix not in assets_map:
        assets_map[rel_posix] = candidate.read_bytes()
    return rel_posix


def _collect_boltz_assets(base_dir: Path, primary_text: str, assets_map: dict[str, bytes]) -> None:
    stripped = primary_text.strip()
    if not stripped.startswith("version:"):
        return

    data = yaml.safe_load(primary_text)
    if not isinstance(data, dict):
        return

    for seq_entry in data.get("sequences", []) or []:
        if isinstance(seq_entry, dict) and isinstance(seq_entry.get("protein"), dict):
            prot = seq_entry["protein"]
            msa_val = prot.get("msa")
            if msa_val is not None and str(msa_val).strip().lower() != "empty":
                _resolve_and_collect_asset(base_dir, str(msa_val), assets_map)

    for tpl_entry in data.get("templates", []) or []:
        if isinstance(tpl_entry, dict):
            for key in ("cif", "pdb"):
                if tpl_entry.get(key):
                    _resolve_and_collect_asset(base_dir, str(tpl_entry[key]), assets_map)


def _collect_openfold3_assets(
    base_dir: Path, primary_text: str, assets_map: dict[str, bytes]
) -> None:
    stripped = primary_text.strip()
    if not stripped.startswith("{"):
        return

    data = json.loads(primary_text)
    if not isinstance(data, dict) or not isinstance(data.get("queries"), dict):
        return

    for _qname, qdata in data["queries"].items():
        if not isinstance(qdata, dict):
            continue
        for chain in qdata.get("chains", []) or []:
            if not isinstance(chain, dict):
                continue
            for p in chain.get("main_msa_file_paths", []) or []:
                _resolve_and_collect_asset(base_dir, str(p), assets_map)
            for single_key in ("paired_msa_file_path", "template_alignment_file_path"):
                if chain.get(single_key):
                    _resolve_and_collect_asset(base_dir, str(chain[single_key]), assets_map)


def _collect_alphafold3_assets(
    base_dir: Path, primary_text: str, assets_map: dict[str, bytes]
) -> None:
    """Collect external unpairedMsaPath, pairedMsaPath, and templates[*].mmcifPath from AF3 JSON."""
    stripped = primary_text.strip()
    if not stripped.startswith(("{", "[")):
        return

    try:
        data = json.loads(primary_text)
    except Exception:
        return

    queries = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    for qdata in queries:
        if not isinstance(qdata, dict):
            continue
        for seq_entry in qdata.get("sequences", []) or []:
            if not isinstance(seq_entry, dict):
                continue
            for entity_key in ("protein", "rna", "dna"):
                entity = seq_entry.get(entity_key)
                if not isinstance(entity, dict):
                    continue
                for msa_key in ("unpairedMsaPath", "pairedMsaPath"):
                    msa_ref = entity.get(msa_key)
                    if (
                        isinstance(msa_ref, str)
                        and msa_ref.strip()
                        and not msa_ref.strip().startswith("gs://")
                    ):
                        _resolve_and_collect_asset(base_dir, msa_ref, assets_map)
                for tpl in entity.get("templates") or []:
                    if isinstance(tpl, dict):
                        mmcif_ref = tpl.get("mmcifPath")
                        if (
                            isinstance(mmcif_ref, str)
                            and mmcif_ref.strip()
                            and not mmcif_ref.strip().startswith("gs://")
                        ):
                            _resolve_and_collect_asset(base_dir, mmcif_ref, assets_map)


def _should_skip_remote_or_pdb_id(base_dir: Path, raw_ref: str) -> bool:
    """Return True if raw_ref is a gs:// URI or a 4-char RCSB PDB ID not present as a local file."""
    cleaned = raw_ref.strip()
    if not cleaned or cleaned.startswith("gs://"):
        return True
    if "/" not in cleaned and "\\" not in cleaned and _PDB_ID_OR_FILE_RE.match(cleaned):
        if not (base_dir / cleaned).exists() and not (Path.cwd().resolve() / cleaned).exists():
            return True
    return False


def _parse_yaml_or_json(primary_text: str) -> dict[str, Any] | None:
    stripped = primary_text.strip()
    if not stripped:
        return None
    if stripped.startswith("{"):
        try:
            data = json.loads(primary_text)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
    try:
        data = yaml.safe_load(primary_text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _collect_boltzgen_assets(
    base_dir: Path, primary_text: str, assets_map: dict[str, bytes]
) -> None:
    data = _parse_yaml_or_json(primary_text)
    if not isinstance(data, dict):
        return

    if data.get("target_structure"):
        raw_ts = str(data["target_structure"])
        if not _should_skip_remote_or_pdb_id(base_dir, raw_ts):
            _resolve_and_collect_asset(base_dir, raw_ts, assets_map)

    for ent in data.get("entities", []) or []:
        if isinstance(ent, dict) and isinstance(ent.get("file"), dict):
            raw_path = ent["file"].get("path")
            if raw_path and not _should_skip_remote_or_pdb_id(base_dir, str(raw_path)):
                _resolve_and_collect_asset(base_dir, str(raw_path), assets_map)


def _collect_rfd4_proteina_assets(
    base_dir: Path, primary_text: str, assets_map: dict[str, bytes]
) -> None:
    if primary_text.lstrip().startswith("data_"):
        return
    data = _parse_yaml_or_json(primary_text)
    if not isinstance(data, dict):
        return

    for key in ("target_structure", "target_pdb"):
        if data.get(key):
            raw_ts = str(data[key])
            if not _should_skip_remote_or_pdb_id(base_dir, raw_ts):
                _resolve_and_collect_asset(base_dir, raw_ts, assets_map)

    for task_cfg in data.values():
        if isinstance(task_cfg, dict) and task_cfg.get("input"):
            raw_in = str(task_cfg["input"])
            if not _should_skip_remote_or_pdb_id(base_dir, raw_in):
                _resolve_and_collect_asset(base_dir, raw_in, assets_map)


def _collect_bindcraft_assets(
    base_dir: Path, primary_text: str, assets_map: dict[str, bytes]
) -> None:
    data = _parse_yaml_or_json(primary_text)
    if not isinstance(data, dict):
        return

    for key in ("starting_pdb", "target_pdb", "target_structure"):
        if data.get(key):
            raw_ts = str(data[key])
            if not _should_skip_remote_or_pdb_id(base_dir, raw_ts):
                _resolve_and_collect_asset(base_dir, raw_ts, assets_map)


def _bundle_bindcraft_cli_assets(
    base_dir: Path,
    cli_args: list[str],
    assets_map: dict[str, bytes],
) -> list[str]:
    """Bundle BindCraft --filters and --advanced JSON files and normalize their CLI relative paths."""
    rewritten: list[str] = []
    idx = 0
    n = len(cli_args)

    def _handle_json_flag(canonical_flag: str, raw_val: str) -> list[str]:
        cleaned = raw_val.strip().replace("\\", "/")
        if cleaned.startswith("gs://"):
            return [canonical_flag, cleaned]
        trimmed = cleaned
        while trimmed.startswith("./"):
            trimmed = trimmed[2:]
        local_exists = (base_dir / cleaned).exists() or (Path.cwd().resolve() / cleaned).exists()
        if (
            not local_exists
            and not cleaned.startswith(("/", "~"))
            and ".." not in trimmed.split("/")
            and (
                trimmed.startswith(("settings_filters/", "settings_advanced/"))
                or ("/" not in trimmed and trimmed.lower().endswith(".json"))
            )
        ):
            # Allow container-built-in BindCraft preset path when user did not create a local copy
            return [canonical_flag, trimmed]
        rel_posix = _resolve_and_collect_asset(base_dir, raw_val, assets_map)
        return [canonical_flag, rel_posix]

    while idx < n:
        tok = str(cli_args[idx]).strip()
        if tok.startswith(("--filters=", "-f=")):
            rewritten.extend(_handle_json_flag("--filters", tok.split("=", 1)[1]))
            idx += 1
        elif tok in ("--filters", "-f") and (idx + 1) < n:
            rewritten.extend(_handle_json_flag("--filters", str(cli_args[idx + 1])))
            idx += 2
        elif tok.startswith(("--advanced=", "-a=")):
            rewritten.extend(_handle_json_flag("--advanced", tok.split("=", 1)[1]))
            idx += 1
        elif tok in ("--advanced", "-a") and (idx + 1) < n:
            rewritten.extend(_handle_json_flag("--advanced", str(cli_args[idx + 1])))
            idx += 2
        else:
            rewritten.append(cli_args[idx])
            idx += 1
    return rewritten


def _build_assets_tar_gz(assets_map: dict[str, bytes]) -> bytes | None:
    if not assets_map:
        return None
    if len(assets_map) > MAX_ASSETS_FILES:
        raise ValueError(
            f"Referenced {len(assets_map)} asset files, exceeding max {MAX_ASSETS_FILES}"
        )
    total_bytes = sum(len(b) for b in assets_map.values())
    if total_bytes > MAX_ASSETS_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"Referenced assets total {total_bytes} bytes, exceeding max {MAX_ASSETS_UNCOMPRESSED_BYTES}"
        )

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel_posix in sorted(assets_map.keys()):
            content = assets_map[rel_posix]
            info = tarfile.TarInfo(name=rel_posix)
            info.size = len(content)
            info.type = tarfile.REGTYPE
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _is_flag_token(tok: str) -> bool:
    if not tok.startswith("-"):
        return False
    if len(tok) > 1 and (tok[1].isdigit() or (tok[1] == "." and len(tok) > 2 and tok[2].isdigit())):
        return False
    return True


def _normalize_cli_args_for_hash(cli_args: list[str]) -> list[list[str]]:
    """Canonicalize CLI tokens into sorted [flag, value] pairs to prevent value-swap hash collisions."""
    pairs: list[list[str]] = []
    idx = 0
    n = len(cli_args)
    while idx < n:
        tok = str(cli_args[idx]).strip()
        if not tok:
            idx += 1
            continue
        if _is_flag_token(tok):
            if "=" in tok:
                flag, val = tok.split("=", 1)
                norm_val = val.lower() if val.lower() in ("true", "false") else val
                pairs.append([flag, norm_val])
                idx += 1
            elif (
                (idx + 1) < n
                and not _is_flag_token(str(cli_args[idx + 1]).strip())
                and not str(cli_args[idx + 1]).strip().startswith("~")
                and "=" not in str(cli_args[idx + 1]).strip()
            ):
                val = str(cli_args[idx + 1]).strip()
                norm_val = val.lower() if val.lower() in ("true", "false") else val
                pairs.append([tok, norm_val])
                idx += 2
            else:
                pairs.append([tok, "true"])
                idx += 1
        elif "=" in tok and not tok.startswith("~"):
            key, val = tok.split("=", 1)
            norm_val = val.lower() if val.lower() in ("true", "false") else val
            pairs.append([key.strip(), norm_val.strip()])
            idx += 1
        else:
            pairs.append([tok, ""])
            idx += 1
    return sorted(pairs)


def compute_client_input_sha256(
    model: str,
    primary_bytes: bytes,
    assets_map: dict[str, bytes],
    cli_args: list[str],
    runner_yaml_overrides: dict[str, Any],
) -> str:
    """Compute a deterministic client-side SHA-256 over raw input bytes, assets, and CLI flags."""
    primary_hash = hashlib.sha256(primary_bytes).hexdigest()
    sorted_asset_hashes = [
        [rel_path, hashlib.sha256(content).hexdigest()]
        for rel_path, content in sorted(assets_map.items(), key=lambda kv: kv[0])
    ]
    assets_json = json.dumps(sorted_asset_hashes, separators=(",", ":"), sort_keys=True)
    flags_payload = {
        "cli_args": _normalize_cli_args_for_hash(cli_args),
        "runner_yaml_overrides": runner_yaml_overrides or {},
    }
    flags_json = json.dumps(flags_payload, separators=(",", ":"), sort_keys=True)
    payload = f"{model}\0{primary_hash}\0{assets_json}\0{flags_json}".encode()
    return hashlib.sha256(payload).hexdigest()


def bundle_local_inputs(
    model: str,
    input_path: str | Path,
    cli_args: list[str] | None = None,
    runner_yaml_path: str | Path | None = None,
) -> BundledInput:
    """Inspect local input file, bundle referenced assets into tar.gz, and compute digest."""
    resolved_input = Path(input_path).expanduser().resolve()
    is_benchmark = False
    primary_bytes: bytes
    input_filename: str

    if not resolved_input.exists() or not resolved_input.is_file():
        if model == "rfd4_proteina":
            norm_path = str(input_path).replace("\\", "/")
            if norm_path.startswith("data/benchmarks/") or "/data/benchmarks/" in norm_path:
                rel_benchmark_path = "data/benchmarks/" + norm_path.split("data/benchmarks/", 1)[1]
                primary_bytes = json.dumps(
                    {"benchmark_input": rel_benchmark_path}, indent=2
                ).encode("utf-8")
                input_filename = Path(rel_benchmark_path).name
                is_benchmark = True
        if not is_benchmark:
            raise FileNotFoundError(f"Input file does not exist: {input_path}")
    else:
        primary_bytes = resolved_input.read_bytes()
        input_filename = resolved_input.name

    primary_text = primary_bytes.decode("utf-8")
    base_dir = resolved_input.parent if not is_benchmark else Path.cwd()
    assets_map: dict[str, bytes] = {}
    clean_cli_args = list(cli_args or [])

    if model == "boltz":
        _collect_boltz_assets(base_dir, primary_text, assets_map)
    elif model == "openfold3":
        _collect_openfold3_assets(base_dir, primary_text, assets_map)
    elif model in ("alphafold3", "af3"):
        _collect_alphafold3_assets(base_dir, primary_text, assets_map)
    elif model == "boltzgen":
        _collect_boltzgen_assets(base_dir, primary_text, assets_map)
    elif model == "rfd4_proteina":
        _collect_rfd4_proteina_assets(base_dir, primary_text, assets_map)
    elif model == "bindcraft":
        _collect_bindcraft_assets(base_dir, primary_text, assets_map)
        clean_cli_args = _bundle_bindcraft_cli_assets(base_dir, clean_cli_args, assets_map)

    runner_yaml_overrides: dict[str, Any] = {}
    if runner_yaml_path is not None:
        resolved_runner = Path(runner_yaml_path).expanduser().resolve()
        if not resolved_runner.exists() or not resolved_runner.is_file():
            raise FileNotFoundError(f"Runner YAML file does not exist: {runner_yaml_path}")
        loaded_yaml = yaml.safe_load(resolved_runner.read_text(encoding="utf-8"))
        if isinstance(loaded_yaml, dict):
            runner_yaml_overrides = loaded_yaml

    assets_tar_bytes = _build_assets_tar_gz(assets_map)
    client_sha256 = compute_client_input_sha256(
        model=model,
        primary_bytes=primary_bytes,
        assets_map=assets_map,
        cli_args=clean_cli_args,
        runner_yaml_overrides=runner_yaml_overrides,
    )

    return BundledInput(
        model=model,
        input_path=resolved_input,
        input_filename=input_filename,
        primary_bytes=primary_bytes,
        assets_tar_bytes=assets_tar_bytes,
        assets_map=assets_map,
        cli_args=clean_cli_args,
        runner_yaml_overrides=runner_yaml_overrides,
        client_input_sha256=client_sha256,
    )
