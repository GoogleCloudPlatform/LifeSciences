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

"""Input bundle validation, path rewriting, canonical SHA-256 computation, and GCS staging."""

import hashlib
import io
import json
import os
import posixpath
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any

import yaml

MAX_ASSETS_FILES = 50
MAX_ASSETS_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MB
_PDB_ID_OR_FILE_RE = re.compile(r"^[0-9][A-Za-z0-9]{3}(?:\.(?:cif|pdb|bcif))?$", re.IGNORECASE)


class BundleValidationError(ValueError):
    """Raised when an uploaded input or assets bundle violates security or schema rules."""


@dataclass
class StagedJobInputs:
    """Metadata and rewritten input content ready for GCS upload and pipeline submission."""

    job_id: str
    model: str
    input_filename: str
    input_stem: str
    canonical_sha256: str
    rewritten_input_text: str
    extracted_assets: dict[str, bytes] = field(default_factory=dict)
    gcs_input_uri: str = ""
    gcs_manifest_uri: str = ""
    pipeline_root: str = ""


def normalize_rel_asset_path(raw_path: str) -> str:
    """Normalize a relative asset path and reject traversal or absolute paths."""
    cleaned = raw_path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned:
        raise BundleValidationError("Asset path cannot be empty")
    if cleaned.startswith("/") or cleaned.startswith("~"):
        raise BundleValidationError(
            f"Absolute or home-relative asset paths are forbidden: {raw_path!r}"
        )
    norm = posixpath.normpath(cleaned)
    if norm == "." or norm == ".." or norm.startswith("../") or "/../" in f"/{norm}/":
        raise BundleValidationError(
            f"Path traversal ('..') is forbidden in asset path: {raw_path!r}"
        )
    return norm


def extract_and_validate_assets_tar(assets_tar_bytes: bytes | None) -> dict[str, bytes]:
    """Safely inspect and extract an uploaded assets.tar.gz in memory.

    Enforces:
    - Regular files only (`member.isfile()`), rejecting symlinks, hardlinks, FIFOs, devices.
    - Relative paths only (no leading `/` or `..`).
    - Max 50 files and 256 MB cumulative uncompressed bytes.
    """
    if not assets_tar_bytes:
        return {}

    extracted: dict[str, bytes] = {}
    total_bytes = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(assets_tar_bytes), mode="r:gz") as tf:
            members = tf.getmembers()
            if len(members) > MAX_ASSETS_FILES:
                raise BundleValidationError(
                    f"Assets bundle contains {len(members)} entries, exceeding max {MAX_ASSETS_FILES}"
                )
            for member in members:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise BundleValidationError(
                        f"Non-regular file entry '{member.name}' (symlink/hardlink/device) is forbidden in assets bundle"
                    )
                norm_name = normalize_rel_asset_path(member.name)
                if member.size < 0 or (total_bytes + member.size) > MAX_ASSETS_UNCOMPRESSED_BYTES:
                    raise BundleValidationError(
                        f"Assets bundle exceeds max uncompressed size of {MAX_ASSETS_UNCOMPRESSED_BYTES} bytes"
                    )
                fobj = tf.extractfile(member)
                if fobj is None:
                    raise BundleValidationError(
                        f"Could not read member '{member.name}' from archive"
                    )
                data = fobj.read(MAX_ASSETS_UNCOMPRESSED_BYTES - total_bytes + 1)
                total_bytes += len(data)
                if total_bytes > MAX_ASSETS_UNCOMPRESSED_BYTES:
                    raise BundleValidationError(
                        f"Assets bundle exceeds max uncompressed size of {MAX_ASSETS_UNCOMPRESSED_BYTES} bytes"
                    )
                extracted[norm_name] = data
    except tarfile.TarError as exc:
        raise BundleValidationError(f"Invalid assets.tar.gz archive: {exc}") from exc

    return extracted


def compute_canonical_input_sha256(
    model: str,
    primary_file_bytes: bytes,
    assets_map: dict[str, bytes],
    normalized_flags: dict[str, Any],
) -> str:
    """Compute the canonical SHA-256 digest used for deduplication and .foldrun_job.json."""
    primary_hash = hashlib.sha256(primary_file_bytes).hexdigest()
    sorted_asset_hashes = [
        [rel_path, hashlib.sha256(content).hexdigest()]
        for rel_path, content in sorted(assets_map.items(), key=lambda kv: kv[0])
    ]
    assets_json = json.dumps(sorted_asset_hashes, separators=(",", ":"), sort_keys=True)
    flags_json = json.dumps(normalized_flags, separators=(",", ":"), sort_keys=True)

    payload = f"{model}\0{primary_hash}\0{assets_json}\0{flags_json}".encode()
    return hashlib.sha256(payload).hexdigest()


def validate_same_bucket_gcs_uri(raw_uri: str, bucket_name: str, field_label: str) -> str:
    """Ensure a gs:// URI targets the configured workspace bucket without traversal."""
    cleaned = raw_uri.strip()
    expected_prefix = f"gs://{bucket_name}/"
    if not cleaned.startswith(expected_prefix):
        raise BundleValidationError(
            f"Input field '{field_label}' references external or cross-bucket URI {raw_uri!r}. "
            f"Only URIs inside '{expected_prefix}' or uploaded assets_bundle files are permitted."
        )
    rel_blob = cleaned[len(expected_prefix) :]
    if not rel_blob or ".." in rel_blob.split("/"):
        raise BundleValidationError(f"Invalid GCS object path in '{field_label}': {raw_uri!r}")
    return cleaned


def rewrite_and_validate_input_references(
    model: str,
    input_text: str,
    assets_map: dict[str, bytes],
    bucket_name: str,
    job_id: str,
) -> str:
    """Verify every referenced file path in YAML/JSON is in assets_map and rewrite to /gcs/... or gs://..."""
    gcs_fuse_assets_prefix = f"/gcs/{bucket_name}/gateway_jobs/{job_id}/inputs/assets"
    gcs_uri_assets_prefix = f"gs://{bucket_name}/gateway_jobs/{job_id}/inputs/assets"

    if model == "boltz":
        return _rewrite_boltz_yaml(input_text, assets_map, gcs_fuse_assets_prefix)
    if model == "openfold3":
        return _rewrite_openfold3_json(input_text, assets_map, gcs_fuse_assets_prefix)
    if model == "boltzgen":
        return _rewrite_boltzgen_yaml(input_text, assets_map, gcs_uri_assets_prefix, bucket_name)
    if model == "rfd4_proteina":
        return _rewrite_rfd4_proteina_spec(
            input_text, assets_map, gcs_uri_assets_prefix, bucket_name
        )
    if model == "bindcraft":
        return _rewrite_bindcraft_settings(
            input_text, assets_map, gcs_uri_assets_prefix, bucket_name
        )
    if model in ("alphafold3", "af3"):
        return _rewrite_alphafold3_json(input_text, assets_map, gcs_uri_assets_prefix, bucket_name)
    return input_text


def _resolve_asset_to_gcs_uri_or_keep_pdb(
    raw_ref: str,
    assets_map: dict[str, bytes],
    gcs_uri_assets_prefix: str,
    field_label: str,
    bucket_name: str | None = None,
    allow_pdb_id: bool = True,
) -> str:
    cleaned = raw_ref.strip()
    if cleaned.startswith("gs://"):
        inferred_bucket = bucket_name or gcs_uri_assets_prefix[5:].split("/", 1)[0]
        return validate_same_bucket_gcs_uri(cleaned, inferred_bucket, field_label)
    norm_ref = normalize_rel_asset_path(cleaned)
    if norm_ref in assets_map:
        return f"{gcs_uri_assets_prefix}/{norm_ref}"
    if (
        allow_pdb_id
        and "/" not in cleaned
        and "\\" not in cleaned
        and _PDB_ID_OR_FILE_RE.match(cleaned)
    ):
        return cleaned
    raise BundleValidationError(
        f"Input field '{field_label}' references file {raw_ref!r} (normalized: {norm_ref!r}) "
        "which was not uploaded in assets_bundle. Host/NFS paths are forbidden."
    )


def _resolve_asset_to_gcs_fuse(
    raw_ref: str,
    assets_map: dict[str, bytes],
    gcs_fuse_assets_prefix: str,
    field_label: str,
) -> str:
    norm_ref = normalize_rel_asset_path(raw_ref)
    if norm_ref not in assets_map:
        raise BundleValidationError(
            f"Input field '{field_label}' references file {raw_ref!r} (normalized: {norm_ref!r}) "
            "which was not uploaded in assets_bundle. Host/NFS paths are forbidden."
        )
    return f"{gcs_fuse_assets_prefix}/{norm_ref}"


def _rewrite_boltz_yaml(
    input_text: str,
    assets_map: dict[str, bytes],
    gcs_fuse_assets_prefix: str,
) -> str:
    stripped = input_text.strip()
    if not stripped.startswith("version:"):
        # FASTA input; no file path references
        return input_text

    try:
        data = yaml.safe_load(input_text)
    except Exception as exc:
        raise BundleValidationError(f"Invalid Boltz-2 YAML: {exc}") from exc

    if not isinstance(data, dict):
        return input_text

    modified = False
    for idx, seq_entry in enumerate(data.get("sequences", []) or []):
        if isinstance(seq_entry, dict) and isinstance(seq_entry.get("protein"), dict):
            prot = seq_entry["protein"]
            msa_val = prot.get("msa")
            if msa_val is not None and str(msa_val).strip().lower() != "empty":
                prot["msa"] = _resolve_asset_to_gcs_fuse(
                    str(msa_val),
                    assets_map,
                    gcs_fuse_assets_prefix,
                    f"sequences[{idx}].protein.msa",
                )
                modified = True

    for idx, tpl_entry in enumerate(data.get("templates", []) or []):
        if isinstance(tpl_entry, dict):
            for key in ("cif", "pdb"):
                if tpl_entry.get(key):
                    tpl_entry[key] = _resolve_asset_to_gcs_fuse(
                        str(tpl_entry[key]),
                        assets_map,
                        gcs_fuse_assets_prefix,
                        f"templates[{idx}].{key}",
                    )
                    modified = True

    if not modified:
        return input_text
    return yaml.safe_dump(data, sort_keys=False)


def _rewrite_openfold3_json(
    input_text: str,
    assets_map: dict[str, bytes],
    gcs_fuse_assets_prefix: str,
) -> str:
    stripped = input_text.strip()
    if not stripped.startswith("{"):
        return input_text

    try:
        data = json.loads(input_text)
    except Exception as exc:
        raise BundleValidationError(f"Invalid OpenFold 3 JSON: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("queries"), dict):
        return input_text

    modified = False
    for qname, qdata in data["queries"].items():
        if not isinstance(qdata, dict):
            continue
        for cidx, chain in enumerate(qdata.get("chains", []) or []):
            if not isinstance(chain, dict):
                continue
            msa_list = chain.get("main_msa_file_paths")
            if msa_list is not None:
                if not isinstance(msa_list, list):
                    raise BundleValidationError(
                        f"queries.{qname}.chains[{cidx}].main_msa_file_paths must be a list"
                    )
                chain["main_msa_file_paths"] = [
                    _resolve_asset_to_gcs_fuse(
                        str(p),
                        assets_map,
                        gcs_fuse_assets_prefix,
                        f"queries.{qname}.chains[{cidx}].main_msa_file_paths",
                    )
                    for p in msa_list
                ]
                modified = True

            for single_key in ("paired_msa_file_path", "template_alignment_file_path"):
                if chain.get(single_key):
                    chain[single_key] = _resolve_asset_to_gcs_fuse(
                        str(chain[single_key]),
                        assets_map,
                        gcs_fuse_assets_prefix,
                        f"queries.{qname}.chains[{cidx}].{single_key}",
                    )
                    modified = True

    if not modified:
        return input_text
    return json.dumps(data, indent=2)


def _rewrite_boltzgen_yaml(
    input_text: str,
    assets_map: dict[str, bytes],
    gcs_uri_assets_prefix: str,
    bucket_name: str | None = None,
) -> str:
    try:
        data = yaml.safe_load(input_text)
    except Exception as exc:
        raise BundleValidationError(f"Invalid BoltzGen YAML: {exc}") from exc

    if not isinstance(data, dict):
        return input_text

    modified = False
    if data.get("target_structure"):
        new_ts = _resolve_asset_to_gcs_uri_or_keep_pdb(
            str(data["target_structure"]),
            assets_map,
            gcs_uri_assets_prefix,
            "target_structure",
            bucket_name=bucket_name,
        )
        if new_ts != str(data["target_structure"]):
            data["target_structure"] = new_ts
            modified = True

    for idx, ent in enumerate(data.get("entities", []) or []):
        if isinstance(ent, dict) and isinstance(ent.get("file"), dict):
            raw_path = ent["file"].get("path")
            if raw_path:
                new_path = _resolve_asset_to_gcs_uri_or_keep_pdb(
                    str(raw_path),
                    assets_map,
                    gcs_uri_assets_prefix,
                    f"entities[{idx}].file.path",
                    bucket_name=bucket_name,
                )
                if new_path != str(raw_path):
                    ent["file"]["path"] = new_path
                    modified = True

    if not modified:
        return input_text
    return yaml.safe_dump(data, sort_keys=False)


def _rewrite_rfd4_proteina_spec(
    input_text: str,
    assets_map: dict[str, bytes],
    gcs_uri_assets_prefix: str,
    bucket_name: str | None = None,
) -> str:
    if input_text.lstrip().startswith("data_"):
        return input_text

    is_json = input_text.strip().startswith("{")
    try:
        data = json.loads(input_text) if is_json else yaml.safe_load(input_text)
    except Exception as exc:
        raise BundleValidationError(f"Invalid RFD4-Proteina specification: {exc}") from exc

    if not isinstance(data, dict):
        return input_text

    if "benchmark_input" in data:
        bench_rel = normalize_rel_asset_path(str(data["benchmark_input"]))
        if not bench_rel.startswith("data/benchmarks/"):
            raise BundleValidationError(
                f"benchmark_input must reside under 'data/benchmarks/', got {data['benchmark_input']!r}"
            )
        return input_text

    modified = False
    for key in ("target_structure", "target_pdb"):
        if data.get(key):
            new_val = _resolve_asset_to_gcs_uri_or_keep_pdb(
                str(data[key]),
                assets_map,
                gcs_uri_assets_prefix,
                key,
                bucket_name=bucket_name,
            )
            if new_val != str(data[key]):
                data[key] = new_val
                modified = True

    for tname, task_cfg in data.items():
        if isinstance(task_cfg, dict) and task_cfg.get("input"):
            new_in = _resolve_asset_to_gcs_uri_or_keep_pdb(
                str(task_cfg["input"]),
                assets_map,
                gcs_uri_assets_prefix,
                f"{tname}.input",
                bucket_name=bucket_name,
            )
            if new_in != str(task_cfg["input"]):
                task_cfg["input"] = new_in
                modified = True

    if not modified:
        return input_text
    return json.dumps(data, indent=2) if is_json else yaml.safe_dump(data, sort_keys=False)


def _rewrite_bindcraft_settings(
    input_text: str,
    assets_map: dict[str, bytes],
    gcs_uri_assets_prefix: str,
    bucket_name: str | None = None,
) -> str:
    is_json = input_text.strip().startswith("{")
    try:
        data = json.loads(input_text) if is_json else yaml.safe_load(input_text)
    except Exception as exc:
        raise BundleValidationError(f"Invalid BindCraft settings: {exc}") from exc

    if not isinstance(data, dict):
        return input_text

    modified = False
    for key in ("starting_pdb", "target_pdb", "target_structure"):
        if data.get(key):
            new_val = _resolve_asset_to_gcs_uri_or_keep_pdb(
                str(data[key]),
                assets_map,
                gcs_uri_assets_prefix,
                key,
                bucket_name=bucket_name,
            )
            if new_val != str(data[key]):
                data[key] = new_val
                modified = True

    for aux_key in ("_foldrun_filters_path", "_foldrun_advanced_path"):
        if data.get(aux_key):
            new_aux = _resolve_asset_to_gcs_uri_or_keep_pdb(
                str(data[aux_key]),
                assets_map,
                gcs_uri_assets_prefix,
                aux_key,
                bucket_name=bucket_name,
                allow_pdb_id=False,
            )
            if new_aux != str(data[aux_key]):
                data[aux_key] = new_aux
                modified = True

    if not modified:
        return input_text
    return json.dumps(data, indent=2) if is_json else yaml.safe_dump(data, sort_keys=False)


def _rewrite_alphafold3_json(
    input_text: str,
    assets_map: dict[str, bytes],
    gcs_uri_assets_prefix: str,
    bucket_name: str | None = None,
) -> str:
    """Validate and rewrite AlphaFold 3 JSON external asset paths (or inline FASTA pass-through)."""
    stripped = input_text.strip()
    if not stripped.startswith("{"):
        return input_text

    try:
        data = json.loads(input_text)
    except Exception as exc:
        raise BundleValidationError(f"Invalid AlphaFold 3 JSON: {exc}") from exc

    if not isinstance(data, dict):
        return input_text

    modified = False
    if data.get("userCCDPath"):
        new_ccd = _resolve_asset_to_gcs_uri_or_keep_pdb(
            str(data["userCCDPath"]),
            assets_map,
            gcs_uri_assets_prefix,
            "userCCDPath",
            bucket_name=bucket_name,
            allow_pdb_id=False,
        )
        if new_ccd != str(data["userCCDPath"]):
            data["userCCDPath"] = new_ccd
            modified = True

    for sidx, seq_entry in enumerate(data.get("sequences", []) or []):
        if not isinstance(seq_entry, dict):
            continue
        if isinstance(seq_entry.get("protein"), dict):
            prot = seq_entry["protein"]
            for msa_key in ("unpairedMsaPath", "pairedMsaPath"):
                if prot.get(msa_key):
                    new_msa = _resolve_asset_to_gcs_uri_or_keep_pdb(
                        str(prot[msa_key]),
                        assets_map,
                        gcs_uri_assets_prefix,
                        f"sequences[{sidx}].protein.{msa_key}",
                        bucket_name=bucket_name,
                        allow_pdb_id=False,
                    )
                    if new_msa != str(prot[msa_key]):
                        prot[msa_key] = new_msa
                        modified = True
            for tidx, tpl in enumerate(prot.get("templates", []) or []):
                if isinstance(tpl, dict) and tpl.get("mmcifPath"):
                    new_mmcif = _resolve_asset_to_gcs_uri_or_keep_pdb(
                        str(tpl["mmcifPath"]),
                        assets_map,
                        gcs_uri_assets_prefix,
                        f"sequences[{sidx}].protein.templates[{tidx}].mmcifPath",
                        bucket_name=bucket_name,
                        allow_pdb_id=False,
                    )
                    if new_mmcif != str(tpl["mmcifPath"]):
                        tpl["mmcifPath"] = new_mmcif
                        modified = True
            if (
                any(
                    k in prot
                    for k in ("unpairedMsa", "unpairedMsaPath", "pairedMsa", "pairedMsaPath")
                )
                and "templates" not in prot
            ):
                prot["templates"] = []
                modified = True

        elif isinstance(seq_entry.get("rna"), dict):
            rna = seq_entry["rna"]
            if rna.get("unpairedMsaPath"):
                new_rna_msa = _resolve_asset_to_gcs_uri_or_keep_pdb(
                    str(rna["unpairedMsaPath"]),
                    assets_map,
                    gcs_uri_assets_prefix,
                    f"sequences[{sidx}].rna.unpairedMsaPath",
                    bucket_name=bucket_name,
                    allow_pdb_id=False,
                )
                if new_rna_msa != str(rna["unpairedMsaPath"]):
                    rna["unpairedMsaPath"] = new_rna_msa
                    modified = True

    if not modified:
        return input_text
    return json.dumps(data, indent=2)


def stage_job_inputs_to_gcs(
    storage_client: Any,
    bucket_name: str,
    job_id: str,
    model: str,
    input_filename: str,
    rewritten_input_text: str,
    assets_map: dict[str, bytes],
    canonical_sha256: str,
    cli_args: list[str],
    pipeline_root: str,
    normalized_flags: dict[str, Any],
) -> StagedJobInputs:
    """Upload manifest.json, rewritten input file, and extracted assets to GCS."""
    safe_basename = os.path.basename(input_filename.strip() or f"input.{model}")
    input_stem = os.path.splitext(safe_basename)[0] or "query"

    bucket = storage_client.bucket(bucket_name)
    base_prefix = f"gateway_jobs/{job_id}"

    # 1. Upload extracted assets under gateway_jobs/{job_id}/inputs/assets/
    for rel_path, content_bytes in assets_map.items():
        asset_blob = bucket.blob(f"{base_prefix}/inputs/assets/{rel_path}")
        asset_blob.upload_from_string(content_bytes)

    # 2. Upload primary rewritten input
    input_blob_path = f"{base_prefix}/inputs/{safe_basename}"
    input_blob = bucket.blob(input_blob_path)
    input_blob.upload_from_string(rewritten_input_text, content_type="text/plain; charset=utf-8")
    gcs_input_uri = f"gs://{bucket_name}/{input_blob_path}"

    # 3. Upload manifest.json preserving case-sensitive input_stem and pipeline_root
    manifest_data = {
        "job_id": job_id,
        "model": model,
        "input_filename": safe_basename,
        "input_stem": input_stem,
        "input_sha256": canonical_sha256,
        "cli_args": cli_args,
        "normalized_flags": normalized_flags,
        "pipeline_root": pipeline_root,
        "gcs_input_uri": gcs_input_uri,
        "assets": sorted(assets_map.keys()),
    }
    manifest_blob_path = f"{base_prefix}/manifest.json"
    bucket.blob(manifest_blob_path).upload_from_string(
        json.dumps(manifest_data, indent=2),
        content_type="application/json",
    )

    return StagedJobInputs(
        job_id=job_id,
        model=model,
        input_filename=safe_basename,
        input_stem=input_stem,
        canonical_sha256=canonical_sha256,
        rewritten_input_text=rewritten_input_text,
        extracted_assets=assets_map,
        gcs_input_uri=gcs_input_uri,
        gcs_manifest_uri=f"gs://{bucket_name}/{manifest_blob_path}",
        pipeline_root=pipeline_root,
    )
