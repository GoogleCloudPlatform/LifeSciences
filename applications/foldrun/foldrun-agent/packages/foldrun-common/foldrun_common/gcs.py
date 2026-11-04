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

"""Shared GCS, zstandard decompression, filename sanitization, and NaN scrubbing helpers."""

from __future__ import annotations

import io
import logging
import math
import re
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

MAX_DECOMPRESSED_ZST_BYTES = 512 * 1024 * 1024  # 512 MB cap against compression bombs
JOB_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def sanitize_json_floats(obj: Any) -> Any:
    """Recursively replace NaN and Inf floats (including NumPy scalars/arrays) with None.

    Produces strict RFC 8259 JSON values without fabricating confidence scores.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    # Duck-typed support for numpy scalar / ndarray types without requiring numpy installed
    obj_type = type(obj)
    type_mod = getattr(obj_type, "__module__", "")
    if type_mod.startswith("numpy"):
        tolist = getattr(obj, "tolist", None)
        if callable(tolist):
            return sanitize_json_floats(tolist())
        item = getattr(obj, "item", None)
        if callable(item):
            return sanitize_json_floats(item())

    if isinstance(obj, dict):
        return {k: sanitize_json_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json_floats(x) for x in obj]
    return obj


def sanitize_filename(name: str | None, fallback: str = "artifact") -> str:
    """Sanitize a string for safe use in Content-Disposition headers and ZIP archive paths."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", str(name or fallback)).strip("._-")
    return cleaned or fallback


def validate_gcs_uri(
    uri: str,
    *,
    expected_bucket: str | None = None,
    allowed_prefixes: Sequence[str] | None = None,
    allowed_extensions: Sequence[str] | None = None,
    allow_bucket_root: bool = False,
) -> tuple[str, str]:
    """Parse and validate a ``gs://bucket/object`` URI with optional confinement checks.

    Args:
        uri: Target GCS URI string.
        expected_bucket: Optional required GCS bucket name.
        allowed_prefixes: Optional tuple/list of allowed object path prefixes.
        allowed_extensions: Optional tuple/list of allowed file extensions (case-insensitive).
        allow_bucket_root: Whether ``gs://bucket`` / ``gs://bucket/`` is permitted.

    Returns:
        ``(bucket_name, blob_path)`` tuple.
    """
    if not uri or not isinstance(uri, str) or "\x00" in uri:
        raise ValueError(f"Invalid GCS URI: {uri!r}")
    if not uri.startswith("gs://"):
        raise ValueError(f"URI must start with 'gs://', got: {uri!r}")

    remainder = uri[5:]
    bucket, _, blob_path = remainder.partition("/")
    if not bucket or (not blob_path and not allow_bucket_root):
        raise ValueError(f"Malformed GCS URI (missing bucket or object): {uri!r}")

    if blob_path.startswith("/") or ".." in blob_path.split("/"):
        raise ValueError(f"Path traversal detected in GCS URI: {uri!r}")

    if expected_bucket and bucket != expected_bucket:
        raise ValueError(
            f"Unauthorized GCS bucket '{bucket}' in URI {uri!r}; expected '{expected_bucket}'"
        )

    if allowed_prefixes and (
        not blob_path or not any(blob_path.startswith(prefix) for prefix in allowed_prefixes)
    ):
        raise ValueError(
            f"Invalid GCS path: '{blob_path}' must start with an allowed prefix {tuple(allowed_prefixes)}"
        )

    if allowed_extensions and not blob_path.lower().endswith(tuple(allowed_extensions)):
        raise ValueError(
            f"Invalid GCS path: '{blob_path}' must end with one of {tuple(allowed_extensions)}"
        )

    return bucket, blob_path


def decompress_zst_bytes(raw_bytes: bytes, max_bytes: int = MAX_DECOMPRESSED_ZST_BYTES) -> bytes:
    """Decompress zstandard-compressed bytes with a strict size limit."""
    import zstandard as zstd

    dctx = zstd.ZstdDecompressor()
    chunks: list[bytes] = []
    total = 0
    with dctx.stream_reader(io.BytesIO(raw_bytes)) as reader:
        while True:
            chunk = reader.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"Decompressed .zst payload exceeds safety limit of {max_bytes} bytes"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def maybe_decompress_zst(
    arc_path: str,
    raw_bytes: bytes,
    max_bytes: int = MAX_DECOMPRESSED_ZST_BYTES,
) -> tuple[str, bytes]:
    """Decompress ``.zst`` bytes and strip ``.zst`` suffix when zstandard is available."""
    if not arc_path.lower().endswith(".zst"):
        return arc_path, raw_bytes
    try:
        return arc_path[:-4], decompress_zst_bytes(raw_bytes, max_bytes=max_bytes)
    except ImportError:  # pragma: no cover
        return arc_path, raw_bytes


def is_stale_analysis_blob(filename: str) -> bool:
    """Return True if ``filename`` is a per-sample or consolidated artifact from a prior run."""
    if filename in ("summary.json", "artifacts_bundle.zip", "error.json"):
        return True
    if filename.endswith("_analysis.json"):
        return True
    if (
        filename.startswith("analysis_")
        and filename.endswith(".json")
        and filename != "analysis_metadata.json"
    ):
        return True
    return False


def cleanup_stale_analysis_blobs(bucket: Any, analysis_prefix: str) -> int:
    """Delete stale per-prediction and consolidated blobs under ``analysis_prefix``."""
    prefix = analysis_prefix if analysis_prefix.endswith("/") else f"{analysis_prefix}/"
    deleted = 0
    try:
        for blob in list(bucket.list_blobs(prefix=prefix)):
            filename = blob.name.rsplit("/", 1)[-1]
            if is_stale_analysis_blob(filename):
                try:
                    blob.delete()
                    deleted += 1
                except Exception as del_err:
                    logger.warning(f"Failed to delete stale blob {blob.name}: {del_err}")
    except Exception as list_err:
        logger.warning(f"Could not list stale analysis blobs under {prefix}: {list_err}")
    return deleted
