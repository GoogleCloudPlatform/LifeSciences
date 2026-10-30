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

"""Streaming native output directory reconstruction and Cloud Run V4 Signed URL generation."""

import csv
import io
import json
import logging
import re
import tarfile
import threading
from datetime import timedelta
from typing import Any

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

logger = logging.getLogger(__name__)

STREAM_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MB streaming buffer
MAX_ZST_DECOMPRESSED_BYTES = 512 * 1024 * 1024  # 512 MiB cap per .zst artifact
_ARCHIVE_LOCKS_GUARD = threading.Lock()
_ARCHIVE_LOCKS: dict[str, threading.Lock] = {}


def _get_job_archive_lock(lock_key: str) -> threading.Lock:
    with _ARCHIVE_LOCKS_GUARD:
        if lock_key not in _ARCHIVE_LOCKS:
            _ARCHIVE_LOCKS[lock_key] = threading.Lock()
        return _ARCHIVE_LOCKS[lock_key]


def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {gcs_uri!r}")
    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_path = parts[1] if len(parts) > 1 else ""
    return bucket_name, blob_path


def _stream_blob_into_tar(tf: tarfile.TarFile, blob: Any, arcname: str) -> None:
    """Stream a GCS blob directly into a streaming tarfile writer using tarfile.addfile."""
    if blob.size is None:
        blob.reload()
    size = int(blob.size or 0)
    tarinfo = tarfile.TarInfo(name=arcname)
    tarinfo.size = size
    with blob.open("rb", chunk_size=STREAM_CHUNK_BYTES) as reader:
        tf.addfile(tarinfo, fileobj=reader)


def _add_bytes_into_tar(tf: tarfile.TarFile, data: bytes, arcname: str) -> None:
    tarinfo = tarfile.TarInfo(name=arcname)
    tarinfo.size = len(data)
    tf.addfile(tarinfo, fileobj=io.BytesIO(data))


def load_job_manifest(
    storage_client: Any,
    bucket_name: str,
    job_id: str,
) -> dict[str, Any]:
    """Load gs://{bucket}/gateway_jobs/{job_id}/manifest.json if present."""
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(f"gateway_jobs/{job_id}/manifest.json")
    if blob.exists():
        return json.loads(blob.download_as_text())
    return {}


def build_or_get_native_outputs_archive(
    storage_client: Any,
    bucket_name: str,
    job_id: str,
    model: str,
    job: Any,
    include_pkl: bool = False,
) -> str:
    """Stream per-seed GCS artifacts into gs://{bucket}/gateway_jobs/{job_id}/native_outputs.tar.gz."""
    archive_name = "native_outputs_with_pkl.tar.gz" if include_pkl else "native_outputs.tar.gz"
    archive_blob_path = f"gateway_jobs/{job_id}/{archive_name}"
    archive_uri = f"gs://{bucket_name}/{archive_blob_path}"

    lock = _get_job_archive_lock(f"{job_id}:{archive_name}")
    with lock:
        bucket = storage_client.bucket(bucket_name)
        dest_blob = bucket.blob(archive_blob_path)
        if dest_blob.exists():
            return archive_uri

        manifest = load_job_manifest(storage_client, bucket_name, job_id)
        labels = dict(job.labels) if hasattr(job, "labels") and job.labels else {}
        input_stem = (
            manifest.get("input_stem")
            or labels.get("query_name")
            or labels.get("seq_name")
            or job_id
        )
        pipeline_root = manifest.get("pipeline_root") or (
            job.runtime_config.gcs_output_directory
            if hasattr(job, "runtime_config") and job.runtime_config
            else ""
        )
        gcs_output_dir = manifest.get("gcs_output_dir") or ""
        if not gcs_output_dir and hasattr(job, "runtime_config") and job.runtime_config:
            param_vals = getattr(job.runtime_config, "parameter_values", None) or {}
            if isinstance(param_vals, dict) and param_vals.get("job_prefix"):
                gcs_output_dir = f"gs://{bucket_name}/{str(param_vals['job_prefix']).lstrip('/')}"
        normalized_flags = manifest.get("normalized_flags") or {}

        with dest_blob.open("wb", content_type="application/gzip") as gcs_writer:
            with tarfile.open(fileobj=gcs_writer, mode="w|gz") as tf:
                if model in ("boltz", "boltz2"):
                    _package_boltz2_outputs(tf, bucket, pipeline_root, input_stem)
                elif model == "openfold3":
                    _package_openfold3_outputs(tf, bucket, pipeline_root, input_stem)
                elif model == "alphafold2":
                    models_to_relax = str(normalized_flags.get("models_to_relax", "best"))
                    _package_alphafold2_outputs(
                        tf,
                        storage_client,
                        bucket,
                        job,
                        input_stem,
                        models_to_relax=models_to_relax,
                        include_pkl=include_pkl,
                    )
                elif model in ("alphafold3", "af3"):
                    _package_alphafold3_outputs(
                        tf,
                        bucket,
                        pipeline_root,
                        input_stem,
                        include_large_data=include_pkl,
                        gcs_output_dir=gcs_output_dir,
                    )
                elif model in ("boltzgen", "rfd4_proteina", "rfd4-proteina", "bindcraft"):
                    _package_generative_binder_outputs(
                        tf,
                        bucket,
                        pipeline_root,
                        input_stem,
                        model,
                    )
                else:
                    raise ValueError(f"Unsupported model for output packaging: {model!r}")

        return archive_uri


def _package_boltz2_outputs(
    tf: tarfile.TarFile,
    bucket: Any,
    pipeline_root: str,
    input_stem: str,
) -> None:
    """Reconstruct Boltz-2 native output layout (`boltz_results_<stem>/predictions/<stem>/...`)."""
    _, prefix = _parse_gcs_uri(pipeline_root)
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    blobs = list(bucket.list_blobs(prefix=prefix))
    marker = "/boltz_results_patched_query/predictions/patched_query/"

    # Group per-seed blobs by the directory preceding marker
    seed_groups: dict[str, dict[int, dict[str, Any]]] = {}
    affinity_blob: Any = None

    sample_re = re.compile(
        r"^(?P<kind>patched_query|confidence_patched_query|pae_patched_query|pde_patched_query)_model_(?P<idx>\d+)\.(?P<ext>cif|json|npz)$"
    )

    for blob in blobs:
        if marker not in blob.name:
            continue
        seed_prefix, filename = blob.name.split(marker, 1)
        if "/" in filename:
            continue
        if filename == "affinity_patched_query.json" and affinity_blob is None:
            affinity_blob = blob
            continue

        m = sample_re.match(filename)
        if not m:
            continue
        sample_idx = int(m.group("idx"))
        kind = m.group("kind")
        seed_groups.setdefault(seed_prefix, {}).setdefault(sample_idx, {})[kind] = blob

    base_arc_dir = f"boltz_results_{input_stem}/predictions/{input_stem}"
    global_idx = 0
    for seed_prefix in sorted(seed_groups.keys()):
        samples_by_idx = seed_groups[seed_prefix]
        for sample_idx in sorted(samples_by_idx.keys()):
            entry = samples_by_idx[sample_idx]
            if "patched_query" in entry:
                _stream_blob_into_tar(
                    tf,
                    entry["patched_query"],
                    f"{base_arc_dir}/{input_stem}_model_{global_idx}.cif",
                )
            if "confidence_patched_query" in entry:
                _stream_blob_into_tar(
                    tf,
                    entry["confidence_patched_query"],
                    f"{base_arc_dir}/confidence_{input_stem}_model_{global_idx}.json",
                )
            if "pae_patched_query" in entry:
                _stream_blob_into_tar(
                    tf,
                    entry["pae_patched_query"],
                    f"{base_arc_dir}/pae_{input_stem}_model_{global_idx}.npz",
                )
            if "pde_patched_query" in entry:
                _stream_blob_into_tar(
                    tf,
                    entry["pde_patched_query"],
                    f"{base_arc_dir}/pde_{input_stem}_model_{global_idx}.npz",
                )
            global_idx += 1

    if affinity_blob is not None:
        _stream_blob_into_tar(
            tf,
            affinity_blob,
            f"{base_arc_dir}/affinity_{input_stem}.json",
        )


def _package_openfold3_outputs(
    tf: tarfile.TarFile,
    bucket: Any,
    pipeline_root: str,
    input_stem: str,
) -> None:
    """Reconstruct OpenFold 3 native output layout (`<query_name>/seed_<seed>/...`)."""
    _, prefix = _parse_gcs_uri(pipeline_root)
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    blobs = list(bucket.list_blobs(prefix=prefix))
    seed_re = re.compile(r"/(?P<qname>[^/]+)/(?P<seed_dir>seed_\d+)/(?P<fname>[^/]+)$")
    seen_arcnames: set[str] = set()

    for blob in blobs:
        m = seed_re.search(blob.name)
        if not m:
            continue
        fname = m.group("fname")
        if not (
            fname.endswith("_model.cif")
            or fname.endswith("_confidences_aggregated.json")
            or fname.endswith("_confidences.json")
        ):
            continue
        arcname = f"{m.group('qname')}/{m.group('seed_dir')}/{fname}"
        if arcname in seen_arcnames:
            continue
        seen_arcnames.add(arcname)
        _stream_blob_into_tar(tf, blob, arcname)


def _package_alphafold2_outputs(
    tf: tarfile.TarFile,
    storage_client: Any,
    bucket: Any,
    job: Any,
    fasta_stem: str,
    models_to_relax: str = "best",
    include_pkl: bool = False,
) -> None:
    """Reconstruct AlphaFold 2 native output layout (`<fasta_stem>/ranked_{0..N-1}.pdb`, etc.)."""
    task_details = (
        list(job.job_detail.task_details)
        if hasattr(job, "job_detail") and job.job_detail and hasattr(job.job_detail, "task_details")
        else []
    )

    features_uri: str | None = None
    msas_uri: str | None = None
    predict_records: list[dict[str, Any]] = []
    relaxed_by_unrelaxed_uri: dict[str, str] = {}
    relaxed_fallback_list: list[str] = []

    for idx, task in enumerate(task_details):
        tname = getattr(task, "task_name", "") or ""
        outputs = getattr(task, "outputs", None) or {}

        if "data-pipeline" in tname or tname == "data_pipeline":
            if "features" in outputs and getattr(outputs["features"], "artifacts", None):
                features_uri = outputs["features"].artifacts[0].uri
            if "msas" in outputs and getattr(outputs["msas"], "artifacts", None):
                msas_uri = outputs["msas"].artifacts[0].uri

        elif "predict" in tname and "relax" not in tname:
            raw_uri = None
            unrelaxed_uri = None
            ranking_confidence = 0.0
            model_name = f"model_{len(predict_records) + 1}"

            if (
                hasattr(task, "execution")
                and task.execution
                and getattr(task.execution, "metadata", None)
            ):
                meta = task.execution.metadata
                if "input:model_name" in meta:
                    model_name = str(meta["input:model_name"])

            if "raw_prediction" in outputs and getattr(
                outputs["raw_prediction"], "artifacts", None
            ):
                art = outputs["raw_prediction"].artifacts[0]
                raw_uri = getattr(art, "uri", None)
                if getattr(art, "metadata", None):
                    ranking_confidence = float(art.metadata.get("ranking_confidence", 0.0))
                    if model_name.startswith("model_") and art.metadata.get("model_name"):
                        model_name = str(art.metadata["model_name"])

            if "unrelaxed_protein" in outputs and getattr(
                outputs["unrelaxed_protein"], "artifacts", None
            ):
                unrelaxed_uri = getattr(outputs["unrelaxed_protein"].artifacts[0], "uri", None)

            if unrelaxed_uri or raw_uri:
                predict_records.append(
                    {
                        "model_name": model_name,
                        "ranking_confidence": ranking_confidence,
                        "unrelaxed_uri": unrelaxed_uri,
                        "raw_uri": raw_uri,
                        "task_index": idx,
                        "predict_order": len(predict_records),
                    }
                )

        elif "relax" in tname:
            if "relaxed_protein" in outputs and getattr(
                outputs["relaxed_protein"], "artifacts", None
            ):
                rel_uri = getattr(outputs["relaxed_protein"].artifacts[0], "uri", None)
                if rel_uri:
                    relaxed_fallback_list.append(rel_uri)
                    if (
                        hasattr(task, "inputs")
                        and task.inputs
                        and "unrelaxed_protein" in task.inputs
                    ):
                        in_arts = getattr(task.inputs["unrelaxed_protein"], "artifacts", None)
                        if in_arts and getattr(in_arts[0], "uri", None):
                            relaxed_by_unrelaxed_uri[in_arts[0].uri] = rel_uri

    # Sort predictions by ranking_confidence descending
    predict_records.sort(key=lambda r: r["ranking_confidence"], reverse=True)

    # Synthesize ranking_debug.json
    plddts = {r["model_name"]: r["ranking_confidence"] for r in predict_records}
    order = [r["model_name"] for r in predict_records]
    ranking_debug_bytes = json.dumps({"plddts": plddts, "order": order}, indent=2).encode("utf-8")
    _add_bytes_into_tar(tf, ranking_debug_bytes, f"{fasta_stem}/ranking_debug.json")

    def _get_blob(uri: str) -> Any:
        b_name, b_path = _parse_gcs_uri(uri)
        b_obj = bucket if b_name == bucket.name else storage_client.bucket(b_name)
        return b_obj.blob(b_path)

    for rank_idx, rec in enumerate(predict_records):
        mname = rec["model_name"]
        unrelaxed_uri = rec["unrelaxed_uri"]
        raw_uri = rec["raw_uri"]
        relaxed_uri = (relaxed_by_unrelaxed_uri.get(unrelaxed_uri) if unrelaxed_uri else None) or (
            relaxed_fallback_list[rec["predict_order"]]
            if rec["predict_order"] < len(relaxed_fallback_list)
            else None
        )

        if unrelaxed_uri:
            _stream_blob_into_tar(
                tf,
                _get_blob(unrelaxed_uri),
                f"{fasta_stem}/unrelaxed_{mname}.pdb",
            )

        include_relaxed_for_this_rank = models_to_relax == "all" or (
            models_to_relax == "best" and rank_idx == 0
        )
        if relaxed_uri and include_relaxed_for_this_rank:
            _stream_blob_into_tar(
                tf,
                _get_blob(relaxed_uri),
                f"{fasta_stem}/relaxed_{mname}.pdb",
            )
            ranked_source_uri = relaxed_uri
        else:
            ranked_source_uri = unrelaxed_uri

        if ranked_source_uri:
            _stream_blob_into_tar(
                tf,
                _get_blob(ranked_source_uri),
                f"{fasta_stem}/ranked_{rank_idx}.pdb",
            )

        if include_pkl and raw_uri:
            _stream_blob_into_tar(
                tf,
                _get_blob(raw_uri),
                f"{fasta_stem}/result_{mname}.pkl",
            )

    if features_uri:
        _stream_blob_into_tar(tf, _get_blob(features_uri), f"{fasta_stem}/features.pkl")

    if msas_uri:
        m_bucket_name, m_prefix = _parse_gcs_uri(msas_uri)
        m_bucket = bucket if m_bucket_name == bucket.name else storage_client.bucket(m_bucket_name)
        if m_prefix and not m_prefix.endswith("/"):
            m_prefix += "/"
        for msa_blob in m_bucket.list_blobs(prefix=m_prefix):
            rel_msa = msa_blob.name[len(m_prefix) :].lstrip("/")
            if rel_msa:
                _stream_blob_into_tar(tf, msa_blob, f"{fasta_stem}/msas/{rel_msa}")


def _package_alphafold3_outputs(
    tf: tarfile.TarFile,
    bucket: Any,
    pipeline_root: str,
    input_stem: str,
    include_large_data: bool = False,
    gcs_output_dir: str = "",
) -> None:
    """Reconstruct AlphaFold 3 native output layout (`<input_stem>/...`)."""
    prefix_blob_pairs: list[tuple[str, Any]] = []
    seen_blob_names: set[str] = set()

    for root_uri in (gcs_output_dir, pipeline_root):
        if not root_uri or not root_uri.startswith("gs://"):
            continue
        _, prefix = _parse_gcs_uri(root_uri)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        for blob in bucket.list_blobs(prefix=prefix):
            if blob.name not in seen_blob_names:
                seen_blob_names.add(blob.name)
                prefix_blob_pairs.append((prefix, blob))

    seen_arcnames: set[str] = set()

    for prefix, blob in prefix_blob_pairs:
        fname = blob.name.split("/")[-1]
        if not fname:
            continue

        # Skip KFP pipeline specs, runtime manifests, input copies, analysis artifacts, and logs
        if (
            fname.endswith((".yaml", ".yml"))
            or "executor" in fname.lower()
            or fname in ("manifest.json", "input.json", "execution.log")
            or "/analysis/" in blob.name
            or "/.locks/" in blob.name
        ):
            continue

        # Skip multi-GB *_data.json(.zst) (raw MSAs + templates) unless explicitly requested
        if not include_large_data and fname.endswith(("_data.json", "_data.json.zst")):
            continue

        is_cif = fname.endswith((".cif", ".cif.zst"))
        is_json = fname.endswith((".json", ".json.zst")) and not fname.endswith("_spec.json")
        is_csv = fname.endswith(".csv")
        is_npz = fname.endswith(".npz")
        is_terms = fname == "TERMS_OF_USE.md"

        if not (is_cif or is_json or is_csv or is_npz or is_terms):
            continue

        rel = blob.name[len(prefix) :].lstrip("/")
        if rel.startswith("outputs/"):
            rel = rel[len("outputs/") :]
        if rel.startswith("af3_raw/"):
            rel = rel[len("af3_raw/") :]
        if "/" in rel and not rel.startswith("seed-"):
            # Preserve seed-*/sample-*/ subpaths under AF3 outputs; flatten deep KFP executor dirs
            seed_idx = rel.find("/seed-")
            if seed_idx != -1:
                rel = rel[seed_idx + 1 :]
            else:
                rel = fname

        arcname = f"{input_stem}/{rel}"
        if arcname.endswith(".zst"):
            decompressed_arcname = arcname[: -len(".zst")]
            if decompressed_arcname in seen_arcnames:
                continue
            try:
                import zstandard as zstd

                dctx = zstd.ZstdDecompressor()
                out_buf = io.BytesIO()
                total_decompressed = 0
                with blob.open("rb", chunk_size=STREAM_CHUNK_BYTES) as raw_reader:
                    with dctx.stream_reader(raw_reader) as zst_reader:
                        while True:
                            chunk = zst_reader.read(STREAM_CHUNK_BYTES)
                            if not chunk:
                                break
                            total_decompressed += len(chunk)
                            if total_decompressed > MAX_ZST_DECOMPRESSED_BYTES:
                                raise ValueError(
                                    f"Decompressed size exceeds {MAX_ZST_DECOMPRESSED_BYTES} bytes"
                                )
                            out_buf.write(chunk)
                decompressed = out_buf.getvalue()
                _add_bytes_into_tar(tf, decompressed, decompressed_arcname)
                seen_arcnames.add(decompressed_arcname)
                continue
            except Exception as exc:
                logger.warning(f"Could not decompress {blob.name} ({exc}); storing compressed")

        if arcname in seen_arcnames:
            continue
        seen_arcnames.add(arcname)
        _stream_blob_into_tar(tf, blob, arcname)


def _package_generative_binder_outputs(
    tf: tarfile.TarFile,
    bucket: Any,
    pipeline_root: str,
    input_stem: str,
    model: str,
) -> None:
    """Reconstruct generative binder outputs (`<stem>/predictions/...`, `ranked_designs/`, model aliases)."""
    _, prefix = _parse_gcs_uri(pipeline_root)
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    blobs = list(bucket.list_blobs(prefix=prefix))
    cif_by_design_id: dict[str, Any] = {}
    conf_by_design_id: dict[str, dict[str, Any]] = {}
    seen_pred_files: set[str] = set()

    for blob in blobs:
        # Never package BindCraft Rejected/, Trajectory/, or MPNN/ intermediates as accepted designs
        if any(seg in blob.name for seg in ("/Rejected/", "/Trajectory/", "/MPNN/", "/analysis/")):
            continue
        fname = blob.name.split("/")[-1]
        if not (
            "/predictions/" in blob.name
            or fname.startswith(("design_seed", "confidence_design_seed"))
        ):
            continue
        if fname.startswith("design_seed") and fname.endswith(".cif"):
            design_id = fname[:-4]
            cif_by_design_id[design_id] = blob
            if fname not in seen_pred_files:
                seen_pred_files.add(fname)
                _stream_blob_into_tar(tf, blob, f"{input_stem}/predictions/{fname}")
        elif fname.startswith("confidence_design_seed") and fname.endswith(".json"):
            design_id = fname[len("confidence_") : -5]
            if fname not in seen_pred_files:
                seen_pred_files.add(fname)
                _stream_blob_into_tar(tf, blob, f"{input_stem}/predictions/{fname}")
            try:
                conf_by_design_id[design_id] = json.loads(
                    blob.download_as_text(),
                    parse_constant=lambda _: None,
                )
            except Exception:
                conf_by_design_id[design_id] = {"design_id": design_id, "ranking_score": None}

    all_design_ids = sorted(set(cif_by_design_id.keys()) | set(conf_by_design_id.keys()))
    records: list[tuple[str, dict[str, Any]]] = []
    for did in all_design_ids:
        cdata = dict(conf_by_design_id.get(did) or {})
        cdata.setdefault("design_id", did)
        records.append((did, cdata))

    def _safe_sort_score(item: tuple[str, dict[str, Any]]) -> tuple[int, float]:
        raw_score = item[1].get("ranking_score")
        if raw_score is None:
            return (0, 0.0)
        try:
            return (1, float(raw_score))
        except (TypeError, ValueError):
            return (0, 0.0)

    records.sort(key=_safe_sort_score, reverse=True)

    csv_columns = [
        "rank",
        "design_id",
        "seed_value",
        "ranking_score",
        "plddt",
        "iptm",
        "ptm",
        "sc_score",
        "clash_score",
        "interface_energy",
        "contact_surface_area",
        "designed_sequence",
        "metrics_mode",
        "metrics_computed",
        "cif_file",
    ]
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=csv_columns, extrasaction="ignore")
    writer.writeheader()

    def _csv_val(val: Any) -> Any:
        return "" if val is None else val

    for rank_idx, (did, cdata) in enumerate(records):
        ranked_cif_name = f"ranked_{rank_idx}.cif"
        cif_blob = cif_by_design_id.get(did)
        if cif_blob is not None:
            _stream_blob_into_tar(tf, cif_blob, f"{input_stem}/ranked_designs/{ranked_cif_name}")
            if model == "boltzgen":
                _stream_blob_into_tar(
                    tf, cif_blob, f"{input_stem}/final_ranked_designs/{ranked_cif_name}"
                )
            elif model == "bindcraft":
                _stream_blob_into_tar(tf, cif_blob, f"{input_stem}/Accepted/{ranked_cif_name}")

        plddt_val = cdata.get("plddt")
        if plddt_val is None:
            plddt_val = cdata.get("mean_plddt")

        row = {
            "rank": rank_idx,
            "design_id": did,
            "seed_value": _csv_val(cdata.get("seed_value")),
            "ranking_score": _csv_val(cdata.get("ranking_score")),
            "plddt": _csv_val(plddt_val),
            "iptm": _csv_val(cdata.get("iptm")),
            "ptm": _csv_val(cdata.get("ptm")),
            "sc_score": _csv_val(cdata.get("sc_score")),
            "clash_score": _csv_val(cdata.get("clash_score")),
            "interface_energy": _csv_val(cdata.get("interface_energy")),
            "contact_surface_area": _csv_val(cdata.get("contact_surface_area")),
            "designed_sequence": _csv_val(cdata.get("designed_sequence")),
            "metrics_mode": _csv_val(cdata.get("metrics_mode")),
            "metrics_computed": _csv_val(cdata.get("metrics_computed")),
            "cif_file": ranked_cif_name,
        }
        writer.writerow(row)

    csv_bytes = csv_buf.getvalue().encode("utf-8")
    _add_bytes_into_tar(tf, csv_bytes, f"{input_stem}/ranked_designs/metrics.csv")
    if model == "boltzgen":
        _add_bytes_into_tar(tf, csv_bytes, f"{input_stem}/final_ranked_designs/metrics.csv")
    elif model == "bindcraft":
        _add_bytes_into_tar(tf, csv_bytes, f"{input_stem}/Accepted/final_design_stats.csv")


def generate_v4_signed_download_url(
    storage_client: Any,
    archive_gcs_uri: str,
    expiration_minutes: int = 15,
) -> str:
    """Generate a 15-minute V4 Signed URL on Cloud Run using IAM signBlob credentials."""
    bucket_name, blob_path = _parse_gcs_uri(archive_gcs_uri)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)

    creds, _ = google.auth.default()
    if not getattr(creds, "valid", False) or not getattr(creds, "token", None):
        creds.refresh(GoogleAuthRequest())

    sa_email = getattr(creds, "service_account_email", None)
    token = getattr(creds, "token", None)

    kwargs: dict[str, Any] = {
        "version": "v4",
        "expiration": timedelta(minutes=expiration_minutes),
        "method": "GET",
    }
    if sa_email and token:
        kwargs["service_account_email"] = sa_email
        kwargs["access_token"] = token

    return blob.generate_signed_url(**kwargs)
