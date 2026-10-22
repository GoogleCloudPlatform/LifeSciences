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

"""Google Cloud Storage (GCS) tools for browsing, ingesting AlphaGenome outputs, and exporting variant dossiers."""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import os
import pathlib
import re
from datetime import UTC, datetime
from typing import Any

from google.cloud import storage

from .alphagenome_scoring import parse_and_score_alphagenome_output

_SUPPORTED_EXTENSIONS = (
    ".json",
    ".jsonl",
    ".tsv",
    ".csv",
    ".vcf",
    ".vcf.gz",
    ".txt",
)

_GCS_URI_PATTERN = re.compile(r"^gs://(?P<bucket>[^/]+)(?:/(?P<blob>.*))?$")

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB max compressed/raw object download
MAX_DECOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB max decompressed payload


def _parse_gcs_uri(uri_or_bucket: str) -> tuple[str, str]:
    """Extracts bucket name and prefix from a gs:// URI or bucket name string."""
    cleaned = uri_or_bucket.strip()
    match = _GCS_URI_PATTERN.match(cleaned)
    if match:
        bucket = match.group("bucket")
        blob = match.group("blob") or ""
        return bucket, blob
    return cleaned, ""


def _resolve_default_bucket(gcs_uri_or_bucket: str = "") -> str:
    """Resolves the target GCS bucket from explicit arg, ALPHAGENOME_GCS_BUCKET, or project default."""
    if gcs_uri_or_bucket and gcs_uri_or_bucket.strip():
        return gcs_uri_or_bucket.strip()
    env_bucket = os.getenv("ALPHAGENOME_GCS_BUCKET", "").strip()
    if env_bucket:
        return env_bucket
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if project_id:
        return f"{project_id}-alphagenome-outputs"
    return ""


def list_alphagenome_bucket_files(
    gcs_uri_or_bucket: str = "",
    prefix: str = "",
    max_results: int = 50,
) -> dict[str, Any]:
    """Lists supported AlphaGenome output files in a Google Cloud Storage bucket.

    Args:
        gcs_uri_or_bucket: GCS URI (gs://my-bucket/path) or bucket name. Defaults to
                           ALPHAGENOME_GCS_BUCKET or '<GOOGLE_CLOUD_PROJECT>-alphagenome-outputs'.
        prefix: Optional blob prefix/folder filter.
        max_results: Maximum number of files to return (default 50).

    Returns:
        Dictionary with status, bucket, matching file list, sizes, and timestamps.
    """
    target = _resolve_default_bucket(gcs_uri_or_bucket)
    if not target:
        return {
            "status": "error",
            "message": "No GCS bucket specified and ALPHAGENOME_GCS_BUCKET env var is unset.",
            "files": [],
        }

    bucket_name, uri_prefix = _parse_gcs_uri(target)
    effective_prefix = uri_prefix or prefix

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blobs = client.list_blobs(bucket, prefix=effective_prefix)

        matched_files = []
        for b in blobs:
            name_lower = b.name.lower()
            if any(name_lower.endswith(ext) for ext in _SUPPORTED_EXTENSIONS):
                matched_files.append(
                    {
                        "gcs_uri": f"gs://{bucket_name}/{b.name}",
                        "name": b.name,
                        "size_bytes": b.size,
                        "updated": b.updated.isoformat() if b.updated else None,
                        "content_type": b.content_type,
                    }
                )
                if len(matched_files) >= max_results:
                    break

        return {
            "status": "success",
            "bucket": bucket_name,
            "prefix": effective_prefix,
            "count": len(matched_files),
            "files": matched_files,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed listing GCS bucket '{bucket_name}': {e}",
            "files": [],
        }


def _resolve_gcs_blob_target(
    gcs_uri_or_blob: str,
) -> tuple[str, str, str] | dict[str, Any]:
    """Resolves either a `gs://<bucket>/<blob>` URI or a relative blob path against `_resolve_default_bucket()`."""
    cleaned = gcs_uri_or_blob.strip().strip("'\"`")
    if not cleaned:
        return {
            "status": "error",
            "message": "Empty GCS URI or blob path provided.",
            "variants": [],
        }

    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://")):
        return {
            "status": "error",
            "message": f"HTTP(S) URLs are not supported in '{cleaned}'. Expected 'gs://<bucket>/<path>' or a relative bucket blob path.",
            "variants": [],
        }
    if "://" in lowered and not lowered.startswith("gs://"):
        return {
            "status": "error",
            "message": f"Unsupported URI scheme in '{cleaned}'. Expected 'gs://<bucket>/<path>' or a relative bucket blob path.",
            "variants": [],
        }

    if lowered.startswith("gs://"):
        normalized_uri = "gs://" + cleaned[5:]
        match = _GCS_URI_PATTERN.match(normalized_uri)
        if not match or not match.group("blob"):
            return {
                "status": "error",
                "message": f"Invalid GCS URI '{cleaned}'. Expected 'gs://<bucket>/<path>' or a relative blob path.",
                "variants": [],
            }
        bucket_name = match.group("bucket")
        blob_name = match.group("blob").lstrip("/")
        if not blob_name or ".." in pathlib.PurePosixPath(blob_name).parts:
            return {
                "status": "error",
                "message": f"Invalid GCS blob path in '{cleaned}'.",
                "variants": [],
            }
        return bucket_name, blob_name, f"gs://{bucket_name}/{blob_name}"

    blob_rel = cleaned.lstrip("/")
    if not blob_rel or ".." in pathlib.PurePosixPath(blob_rel).parts:
        return {
            "status": "error",
            "message": f"Invalid GCS blob path '{cleaned}': path traversal is not allowed.",
            "variants": [],
        }

    default_target = _resolve_default_bucket()
    if not default_target:
        return {
            "status": "error",
            "message": (
                f"Relative GCS blob path '{cleaned}' requires ALPHAGENOME_GCS_BUCKET "
                "or GOOGLE_CLOUD_PROJECT to resolve the default bucket."
            ),
            "variants": [],
        }

    bucket_name, default_prefix = _parse_gcs_uri(default_target)
    norm_prefix = default_prefix.strip("/")
    if norm_prefix and not blob_rel.startswith(f"{norm_prefix}/"):
        blob_name = f"{norm_prefix}/{blob_rel}"
    else:
        blob_name = blob_rel

    if not bucket_name or not blob_name:
        return {
            "status": "error",
            "message": f"Unable to resolve bucket and blob for '{cleaned}'.",
            "variants": [],
        }

    return bucket_name, blob_name, f"gs://{bucket_name}/{blob_name}"


def import_alphagenome_from_gcs(
    gcs_uri: str,
    top_n: int = 25,
) -> dict[str, Any]:
    """Downloads an AlphaGenome file from GCS, parses variants, and computes AVI scores.

    Supports both full `gs://<bucket>/<blob_path>` URIs and relative blob paths
    (e.g., `samples/sample_alphagenome_variants.vcf`) resolved against the default
    `ALPHAGENOME_GCS_BUCKET` / `<GOOGLE_CLOUD_PROJECT>-alphagenome-outputs` bucket.
    Supports .json, .jsonl, .tsv, .csv, .vcf, and .vcf.gz formats with strict size bounds.

    Args:
        gcs_uri: Full Google Cloud Storage URI (`gs://<bucket>/<blob_path>`) or a relative
            blob path inside the default AlphaGenome bucket (e.g. `samples/sample_alphagenome_variants.vcf`).
        top_n: Maximum number of parsed variants to return (default 25).

    Returns:
        Parsed variant records with AVI Phred tiers, modality scores, and Atlas deep-links.
    """
    resolved = _resolve_gcs_blob_target(gcs_uri)
    if isinstance(resolved, dict):
        return resolved

    bucket_name, blob_name, cleaned_uri = resolved

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        with contextlib.suppress(Exception):
            blob.reload()

        if isinstance(blob.size, int) and blob.size > MAX_DOWNLOAD_BYTES:
            return {
                "status": "error",
                "message": (
                    f"GCS object '{cleaned_uri}' size ({blob.size} bytes) exceeds "
                    f"maximum allowed limit of {MAX_DOWNLOAD_BYTES} bytes (50 MB)."
                ),
                "variants": [],
            }

        raw_bytes = blob.download_as_bytes()
        if len(raw_bytes) > MAX_DOWNLOAD_BYTES:
            return {
                "status": "error",
                "message": (
                    f"Downloaded payload from '{cleaned_uri}' ({len(raw_bytes)} bytes) exceeds "
                    f"maximum allowed limit of {MAX_DOWNLOAD_BYTES} bytes (50 MB)."
                ),
                "variants": [],
            }

        # Handle bounded gzip decompression if extension is .gz or magic header matches
        if blob_name.endswith(".gz") or raw_bytes[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes)) as gz:
                decompressed = gz.read(MAX_DECOMPRESSED_BYTES + 1)
            if len(decompressed) > MAX_DECOMPRESSED_BYTES:
                return {
                    "status": "error",
                    "message": (
                        f"Decompressed payload from '{cleaned_uri}' exceeds "
                        f"maximum allowed limit of {MAX_DECOMPRESSED_BYTES} bytes (100 MB)."
                    ),
                    "variants": [],
                }
            raw_bytes = decompressed

        result = parse_and_score_alphagenome_output(raw_bytes, filename_hint=blob_name)
        if result.get("status") == "success":
            variants = result.get("variants", [])
            result["variants"] = variants[:top_n]
            result["total_in_file"] = len(variants)
            result["source_gcs_uri"] = cleaned_uri

        return result
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed importing AlphaGenome file from '{cleaned_uri}': {e}",
            "variants": [],
        }


def save_dossier_to_gcs(
    dossier_markdown: str,
    variant_label: str = "variant_report",
    metadata: dict[str, Any] | None = None,
    gcs_bucket: str = "",
    prefix: str = "dossiers",
) -> dict[str, Any]:
    """Saves the finalized 6-section Markdown dossier and JSON metadata to Google Cloud Storage.

    Writes two objects under gs://<bucket>/<prefix>/:
      - <timestamp>_<slug>_dossier.md
      - <timestamp>_<slug>_metadata.json

    Args:
        dossier_markdown: The complete validated 6-section Markdown dossier.
        variant_label: Identifier used in the output filename (e.g., 'rs4244285' or 'sample_variants').
        metadata: Optional structured metadata dictionary to save alongside the Markdown report.
        gcs_bucket: Target GCS bucket name or gs:// URI. Defaults to ALPHAGENOME_GCS_BUCKET env var.
        prefix: Folder prefix inside the bucket (default 'dossiers').

    Returns:
        Dictionary with status, bucket, dossier_gcs_uri, metadata_gcs_uri, and console URLs.
    """
    target = _resolve_default_bucket(gcs_bucket)
    if not target:
        return {
            "status": "skipped",
            "message": "ALPHAGENOME_GCS_BUCKET is not set; skipping GCS dossier upload.",
        }

    bucket_name, uri_prefix = _parse_gcs_uri(target)
    effective_prefix = (uri_prefix or prefix).strip("/")

    slug = (
        re.sub(r"[^a-zA-Z0-9._-]+", "_", variant_label.strip()).strip("_")
        or "variant_report"
    )
    slug = slug[:64]
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    base_path = (
        f"{effective_prefix}/{ts}_{slug}" if effective_prefix else f"{ts}_{slug}"
    )
    md_blob_name = f"{base_path}_dossier.md"
    json_blob_name = f"{base_path}_metadata.json"

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        md_blob = bucket.blob(md_blob_name)
        md_blob.upload_from_string(
            dossier_markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

        payload = {
            "timestamp_utc": ts,
            "variant_label": variant_label,
            "dossier_gcs_uri": f"gs://{bucket_name}/{md_blob_name}",
            "metadata_gcs_uri": f"gs://{bucket_name}/{json_blob_name}",
            **(metadata or {}),
        }
        json_blob = bucket.blob(json_blob_name)
        json_blob.upload_from_string(
            json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
            content_type="application/json; charset=utf-8",
        )

        return {
            "status": "success",
            "bucket": bucket_name,
            "dossier_gcs_uri": f"gs://{bucket_name}/{md_blob_name}",
            "metadata_gcs_uri": f"gs://{bucket_name}/{json_blob_name}",
            "console_folder_url": f"https://console.cloud.google.com/storage/browser/{bucket_name}/{effective_prefix}",
            "console_dossier_url": f"https://console.cloud.google.com/storage/browser/_details/{bucket_name}/{md_blob_name}",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed saving dossier to gs://{bucket_name}/{md_blob_name}: {e}",
        }
