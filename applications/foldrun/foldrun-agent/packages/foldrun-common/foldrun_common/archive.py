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

"""Shared compact ZIP bundle builder across Agent, Viewer, and Analysis Job."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from typing import Any

from google.cloud import storage

from .gcs import maybe_decompress_zst, sanitize_filename, sanitize_json_floats, validate_gcs_uri
from .report import build_archive_readme, build_expert_analysis_markdown

logger = logging.getLogger(__name__)


def build_and_upload_run_archive(
    analysis_path: str,
    bucket_name: str,
    summary_data: dict[str, Any],
    *,
    bundle_uri: str | None = None,
    project_id: str | None = None,
    storage_client: Any = None,
    upload_fn: Callable[[str, str], None] | None = None,
    allowed_prefixes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a compact, ranked ``artifacts_bundle.zip`` of run artifacts and upload to GCS.

    Includes:
      - ``report/expert_analysis.md``
      - ``input/`` (``sequence.fasta``, ``query.json``, or ``query.yaml``)
      - ``metrics/summary.json`` (and ``metrics/affinity.json`` if present)
      - ``structures/rank_XX_<name>.cif`` or ``.pdb`` (transparently decompressing ``.zst``)
      - ``plots/rank_XX_<name>_<plot_type>.png``
      - ``README.md``

    Excludes raw ``.pkl``, ``.npz``, full token-pair ``_confidences.json``, and MSA files.
    """
    if not analysis_path.endswith("/"):
        analysis_path += "/"

    effective_bundle_uri = bundle_uri or f"{analysis_path}artifacts_bundle.zip"
    target_bucket, bundle_blob_name = validate_gcs_uri(
        effective_bundle_uri,
        expected_bucket=bucket_name,
        allowed_prefixes=allowed_prefixes,
        allowed_extensions=(".zip",),
    )

    client = storage_client or storage.Client(project=project_id)
    bucket = client.bucket(target_bucket)

    def _uri_to_blob_name(gcs_uri: str) -> str | None:
        try:
            _, blob_name = validate_gcs_uri(
                gcs_uri,
                expected_bucket=target_bucket,
                allowed_prefixes=allowed_prefixes,
            )
            return blob_name
        except ValueError:
            return None

    download_tasks: list[tuple[str, str]] = []
    all_preds = list(
        summary_data.get("all_predictions_summary") or summary_data.get("top_predictions") or []
    )
    all_preds.sort(key=lambda p: p.get("plddt_rank") or p.get("rank") or 999)

    for idx, pred in enumerate(all_preds):
        rank = int(pred.get("plddt_rank") or pred.get("rank") or (idx + 1))
        label = sanitize_filename(
            pred.get("sample_name") or pred.get("model_name") or f"model_{rank}",
            fallback=f"model_{rank}",
        )
        rank_prefix = f"rank_{rank:02d}_{label}"

        if pred.get("cif_uri"):
            cif_uri = str(pred["cif_uri"])
            cif_ext = ".cif.zst" if cif_uri.lower().endswith(".cif.zst") else ".cif"
            download_tasks.append((f"structures/{rank_prefix}{cif_ext}", cif_uri))
        elif pred.get("pdb_uri") or pred.get("uri"):
            raw_uri = str(pred.get("pdb_uri") or pred.get("uri"))
            if raw_uri.endswith("/raw_prediction.pkl"):
                download_tasks.append(
                    (
                        f"structures/{rank_prefix}_unrelaxed.pdb",
                        raw_uri.replace("/raw_prediction.pkl", "/unrelaxed_protein.pdb"),
                    )
                )
                download_tasks.append(
                    (
                        f"structures/{rank_prefix}_relaxed.pdb",
                        raw_uri.replace("/raw_prediction.pkl", "/relaxed_protein.pdb"),
                    )
                )
            elif raw_uri.lower().endswith((".pdb", ".pdb.zst")):
                pdb_ext = ".pdb.zst" if raw_uri.lower().endswith(".pdb.zst") else ".pdb"
                download_tasks.append((f"structures/{rank_prefix}{pdb_ext}", raw_uri))

        plots = pred.get("plots") or {}
        for plot_key, plot_uri in plots.items():
            if not plot_uri:
                continue
            clean_key = sanitize_filename(plot_key.replace("_plot", ""), fallback="plot")
            download_tasks.append((f"plots/{rank_prefix}_{clean_key}.png", str(plot_uri)))

    def _fetch_blob_bytes(item: tuple[str, str]) -> tuple[str, bytes | None]:
        arc_path, uri = item
        blob_name = _uri_to_blob_name(uri)
        if not blob_name:
            return arc_path, None
        try:
            blob = bucket.blob(blob_name)
            if not blob.exists():
                return arc_path, None
            raw_bytes = blob.download_as_bytes()
            if arc_path.lower().endswith(".zst") or uri.lower().endswith(".zst"):
                target_arc = arc_path if arc_path.lower().endswith(".zst") else f"{arc_path}.zst"
                arc_path, raw_bytes = maybe_decompress_zst(target_arc, raw_bytes)
            return arc_path, raw_bytes
        except Exception as exc:
            logger.debug(f"Skipping optional archive member {uri}: {exc}")
            return arc_path, None

    fetched_items: list[tuple[str, bytes]] = []
    if download_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for arc_path, data in pool.map(_fetch_blob_bytes, download_tasks):
                if data is not None:
                    fetched_items.append((arc_path, data))

    summary_inner = summary_data.get("summary") or {}
    protein_info = summary_inner.get("protein_info") or {}
    included_files: list[str] = []
    seen_arcnames: set[str] = set()

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
        tmp_zip_path = tmp_zip.name

    try:
        with zipfile.ZipFile(tmp_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:

            def _write_entry(arc_name: str, payload: bytes) -> None:
                if arc_name in seen_arcnames:
                    return
                zf.writestr(arc_name, payload)
                seen_arcnames.add(arc_name)
                included_files.append(arc_name)

            # 1. Standalone Markdown report
            report_md = build_expert_analysis_markdown(summary_data)
            _write_entry("report/expert_analysis.md", report_md.encode("utf-8"))

            # 2. Input files (FASTA / JSON / YAML)
            fasta_seq = protein_info.get("fasta_sequence")
            if fasta_seq:
                header = protein_info.get("fasta_header") or summary_data.get("job_id", "sequence")
                fasta_content = f">{header}\n{fasta_seq}\n"
                _write_entry("input/sequence.fasta", fasta_content.encode("utf-8"))

            input_json_val = protein_info.get("input_query_json") or protein_info.get("input_query")
            if input_json_val:
                json_str = (
                    json.dumps(sanitize_json_floats(input_json_val), indent=2, allow_nan=False)
                    if isinstance(input_json_val, (dict, list))
                    else str(input_json_val)
                )
                _write_entry("input/query.json", json_str.encode("utf-8"))

            if protein_info.get("input_query_yaml"):
                yaml_val = protein_info["input_query_yaml"]
                yaml_str = (
                    yaml_val
                    if isinstance(yaml_val, str)
                    else json.dumps(sanitize_json_floats(yaml_val), indent=2, allow_nan=False)
                )
                _write_entry("input/query.yaml", yaml_str.encode("utf-8"))

            # 3. Affinity metrics if present (Boltz-2)
            affinity = summary_data.get("affinity") or summary_inner.get("affinity")
            if affinity:
                _write_entry(
                    "metrics/affinity.json",
                    json.dumps(sanitize_json_floats(affinity), indent=2, allow_nan=False).encode(
                        "utf-8"
                    ),
                )

            # 4. Structures and plots downloaded from GCS
            for arc_path, content_bytes in fetched_items:
                _write_entry(arc_path, content_bytes)

            # 5. Consolidated summary.json
            summary_copy = sanitize_json_floats(dict(summary_data))
            summary_copy["artifacts_bundle_uri"] = effective_bundle_uri
            _write_entry(
                "metrics/summary.json",
                json.dumps(summary_copy, indent=2, allow_nan=False).encode("utf-8"),
            )

            # 6. Root README.md
            readme_md = build_archive_readme(summary_data, included_files)
            _write_entry("README.md", readme_md.encode("utf-8"))

        bundle_size_bytes = os.path.getsize(tmp_zip_path)
        if upload_fn is not None:
            upload_fn(tmp_zip_path, effective_bundle_uri)
        else:
            bucket.blob(bundle_blob_name).upload_from_filename(
                tmp_zip_path, content_type="application/zip"
            )

        logger.info(
            f"Built and uploaded artifact bundle {effective_bundle_uri} "
            f"({bundle_size_bytes / 1024 / 1024:.2f} MB, {len(included_files)} files)"
        )
        return {
            "artifacts_bundle_uri": effective_bundle_uri,
            "artifacts_bundle_size_bytes": bundle_size_bytes,
            "artifacts_bundle_file_count": len(included_files),
        }
    finally:
        if os.path.exists(tmp_zip_path):
            os.remove(tmp_zip_path)


def ensure_run_archive_in_gcs(
    analysis_path: str,
    summary_data: dict[str, Any],
    *,
    project_id: str | None = None,
    storage_client: Any = None,
    expected_bucket: str | None = None,
    allowed_prefixes: Sequence[str] | None = None,
    raise_on_error: bool = False,
) -> str | None:
    """Return the GCS URI of ``artifacts_bundle.zip``, creating it on-demand if missing."""
    if not analysis_path.endswith("/"):
        analysis_path += "/"

    bundle_uri = str(
        summary_data.get("artifacts_bundle_uri") or f"{analysis_path}artifacts_bundle.zip"
    )
    try:
        bucket_name, bundle_blob_name = validate_gcs_uri(
            bundle_uri,
            expected_bucket=expected_bucket,
            allowed_prefixes=allowed_prefixes,
            allowed_extensions=(".zip",),
        )
        client = storage_client or storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        if bucket.blob(bundle_blob_name).exists():
            return bundle_uri

        logger.info(f"Archive bundle not yet present at {bundle_uri}; building on-demand...")
        meta = build_and_upload_run_archive(
            analysis_path=analysis_path,
            bucket_name=bucket_name,
            summary_data=summary_data,
            bundle_uri=bundle_uri,
            project_id=project_id,
            storage_client=client,
            allowed_prefixes=allowed_prefixes,
        )
        return str(meta["artifacts_bundle_uri"])
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning(f"Failed to ensure archive bundle at {bundle_uri}: {exc}")
        return None
