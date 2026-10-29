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

"""Shared target structure resolver for generative binder models (BoltzGen, RFD4-Proteina, BindCraft).

Supports three input modalities:
1. 4-letter RCSB PDB ID (e.g. '4Z18', '6M1U'): Fetches directly from RCSB PDB.
2. Google Cloud Storage URI ('gs://...'): Validates existence and stages for pipeline execution.
3. Authorized local file path: Validated against FOLDRUN_ALLOWED_INPUT_DIR.

Staged target structures are uploaded to gs://{bucket}/targets/{job_name}/{filename}
so Vertex AI Pipeline components can reliably access them.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request

from google.cloud import storage

logger = logging.getLogger(__name__)

PDB_ID_REGEX = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


def is_pdb_id(value: str) -> bool:
    """Check if string is a valid 4-letter RCSB PDB ID."""
    if not isinstance(value, str):
        return False
    return bool(PDB_ID_REGEX.match(value.strip()))


def validate_local_file_path(file_path: str) -> str:
    """Validate that local file path is within FOLDRUN_ALLOWED_INPUT_DIR.

    Raises:
        ValueError: If FOLDRUN_ALLOWED_INPUT_DIR is unset, or file is outside allowed path.
    """
    allowed_dir = os.environ.get("FOLDRUN_ALLOWED_INPUT_DIR", "").strip()
    if not allowed_dir:
        raise ValueError(
            "Local file input is not allowed unless FOLDRUN_ALLOWED_INPUT_DIR is explicitly configured. "
            "Provide an RCSB PDB ID (e.g. '4Z18') or a gs:// URI instead."
        )

    real_allowed = os.path.realpath(allowed_dir)
    real_path = os.path.realpath(file_path)

    if (
        os.path.commonpath([real_allowed, real_path]) != real_allowed
        or real_path == real_allowed
        or not os.path.isfile(real_path)
    ):
        raise ValueError(
            f"Local file path '{file_path}' does not exist or is outside authorized directory '{real_allowed}'."
        )

    return real_path


def download_rcsb_structure(pdb_id: str, dest_dir: str, prefer_format: str = "cif") -> str:
    """Download structure from RCSB PDB to destination directory.

    Args:
        pdb_id: 4-character PDB ID (case-insensitive)
        dest_dir: Local destination directory
        prefer_format: 'cif' (default) or 'pdb'

    Returns:
        Local path to downloaded structure file.
    """
    clean_id = pdb_id.strip().lower()
    os.makedirs(dest_dir, exist_ok=True)

    ext = prefer_format.lower()
    if ext not in ("cif", "pdb"):
        ext = "cif"

    url = f"https://files.rcsb.org/download/{clean_id}.{ext}"
    dest_path = os.path.join(dest_dir, f"{clean_id}.{ext}")

    logger.info(f"Downloading RCSB structure {pdb_id.upper()} from {url}...")
    headers = {
        "User-Agent": "FoldRun/1.0 (Google Cloud Life Sciences; mailto:foldrun-support@google.com)"
    }
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            if not content:
                raise ValueError(f"Empty response received from RCSB for {pdb_id.upper()}")
            with open(dest_path, "wb") as f:
                f.write(content)
        return dest_path
    except urllib.error.HTTPError as e:
        # If PDB format was preferred but failed (e.g. large structures only available as mmCIF), fallback to CIF
        if ext == "pdb":
            logger.warning(
                f"Failed to fetch .pdb for {clean_id} (HTTP {e.code}), falling back to .cif"
            )
            return download_rcsb_structure(clean_id, dest_dir, prefer_format="cif")
        raise ValueError(f"Failed to download structure for PDB ID '{pdb_id.upper()}': {e}") from e
    except Exception as e:
        raise ValueError(f"Error downloading structure for PDB ID '{pdb_id.upper()}': {e}") from e


def _sanitize_job_segment(job_name: str) -> str:
    """Sanitize job_name for safe temp directory and GCS blob prefix construction."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (job_name or "").strip()).strip("._-")
    return cleaned[:96] or "job"


def cleanup_staged_target(local_path: str | None) -> None:
    """Remove temporary directory created by resolve_target_structure if applicable."""
    if not local_path:
        return
    try:
        parent = os.path.dirname(os.path.abspath(local_path))
        temp_root = os.path.realpath(tempfile.gettempdir())
        real_parent = os.path.realpath(parent)
        if os.path.commonpath([temp_root, real_parent]) == temp_root and os.path.basename(
            real_parent
        ).startswith("foldrun_target_"):
            shutil.rmtree(real_parent, ignore_errors=True)
    except Exception:
        pass


def resolve_target_structure(
    target_input: str,
    gcs_bucket: str,
    job_name: str,
    prefer_format: str = "cif",
    storage_client: storage.Client | None = None,
    *,
    allow_local_files: bool = True,
    restrict_to_bucket: bool = False,
) -> tuple[str, str]:
    """Resolve target structure from RCSB PDB ID, GCS URI, or local file path, and stage to GCS.

    Args:
        target_input: 4-letter PDB ID, 'gs://...' URI, or local file path
        gcs_bucket: GCS bucket name to stage targets into
        job_name: Unique job name for staging directory isolation
        prefer_format: Preferred format ('cif' or 'pdb')
        storage_client: Optional existing google.cloud.storage.Client
        allow_local_files: Whether local server filesystem paths are permitted
        restrict_to_bucket: Whether gs:// URIs must belong to ``gcs_bucket``

    Returns:
        tuple[str, str]: (target_gcs_uri, local_path)
            target_gcs_uri: gs://{bucket}/targets/{safe_job_name}/{filename}
            local_path: Local filesystem path of the resolved structure file

    Raises:
        ValueError: If input format is invalid, file missing, or download/upload fails.
    """
    if not target_input or not isinstance(target_input, str):
        raise ValueError("Target structure input cannot be empty.")

    target_input = target_input.strip()
    safe_job_name = _sanitize_job_segment(job_name)
    client = storage_client or storage.Client()
    staging_dir = tempfile.mkdtemp(prefix=f"foldrun_target_{safe_job_name}_")

    try:
        # Case 1: 4-letter RCSB PDB ID
        if is_pdb_id(target_input):
            clean_id = target_input.lower()
            local_path = download_rcsb_structure(clean_id, staging_dir, prefer_format=prefer_format)
            filename = os.path.basename(local_path)
            dest_blob_path = f"targets/{safe_job_name}/{filename}"
            bucket = client.bucket(gcs_bucket)
            blob = bucket.blob(dest_blob_path)
            logger.info(
                f"Uploading RCSB target {filename} to gs://{gcs_bucket}/{dest_blob_path}..."
            )
            blob.upload_from_filename(local_path)
            return f"gs://{gcs_bucket}/{dest_blob_path}", local_path

        # Case 2: Google Cloud Storage URI
        if target_input.startswith("gs://"):
            gcs_parts = target_input[5:].split("/", 1)
            src_bucket_name = gcs_parts[0]
            src_blob_path = gcs_parts[1] if len(gcs_parts) > 1 else ""

            if not src_blob_path or ".." in src_blob_path.split("/"):
                raise ValueError(
                    f"Invalid GCS URI '{target_input}': missing or invalid object path."
                )

            if restrict_to_bucket and src_bucket_name != gcs_bucket:
                raise ValueError(
                    f"Cross-bucket GCS URI '{target_input}' is not permitted; "
                    f"target structures must reside in 'gs://{gcs_bucket}/'."
                )

            src_bucket = client.bucket(src_bucket_name)
            src_blob = src_bucket.blob(src_blob_path)
            if not src_blob.exists():
                raise ValueError(f"GCS target structure '{target_input}' does not exist.")

            filename = os.path.basename(src_blob_path)
            if (
                not filename
                or filename in {".", ".."}
                or not filename.lower().endswith((".cif", ".pdb", ".bcif"))
            ):
                raise ValueError(
                    f"Target structure '{filename}' has unsupported extension. Must be .cif, .pdb, or .bcif"
                )

            local_path = os.path.join(staging_dir, filename)
            logger.info(f"Downloading target from {target_input} to {local_path}...")
            src_blob.download_to_filename(local_path)

            # Stage to job target directory if in a different bucket/prefix
            dest_blob_path = f"targets/{safe_job_name}/{filename}"
            if src_bucket_name == gcs_bucket and src_blob_path.startswith(
                f"targets/{safe_job_name}/"
            ):
                return target_input, local_path

            dest_bucket = client.bucket(gcs_bucket)
            dest_blob = dest_bucket.blob(dest_blob_path)
            logger.info(f"Staging target {filename} to gs://{gcs_bucket}/{dest_blob_path}...")
            dest_blob.upload_from_filename(local_path)
            return f"gs://{gcs_bucket}/{dest_blob_path}", local_path

        # Case 3: Local file path (only checked when allow_local_files=True)
        if not allow_local_files:
            raise ValueError(
                f"Local file path '{target_input}' is not allowed in remote submissions. "
                "Provide a 4-letter RCSB PDB ID (e.g. '4Z18'), a gs:// URI in the FoldRun bucket, "
                "or upload the file via the SDK bundle."
            )

        if os.path.isfile(target_input):
            validated_path = validate_local_file_path(target_input)
            filename = os.path.basename(validated_path)
            if not filename.lower().endswith((".cif", ".pdb", ".bcif")):
                raise ValueError(
                    f"Local target structure '{filename}' has unsupported extension. Must be .cif, .pdb, or .bcif"
                )

            dest_blob_path = f"targets/{safe_job_name}/{filename}"
            bucket = client.bucket(gcs_bucket)
            blob = bucket.blob(dest_blob_path)
            logger.info(
                f"Uploading local target {filename} to gs://{gcs_bucket}/{dest_blob_path}..."
            )
            blob.upload_from_filename(validated_path)
            shutil.rmtree(staging_dir, ignore_errors=True)
            return f"gs://{gcs_bucket}/{dest_blob_path}", validated_path

        raise ValueError(
            f"Target structure '{target_input}' is neither a valid 4-letter RCSB PDB ID (e.g. '4Z18'), "
            "a valid gs:// URI, nor an authorized local file path."
        )
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def map_auth_to_label_seq_id(
    structure_path: str,
    chain_id: str,
    auth_seq_ids: list[int | str],
) -> list[int]:
    """Map author residue sequence numbers (auth_seq_id) to canonical 1-based label_seq_id.

    BoltzGen requires 1-indexed canonical label_seq_id for binding residues in MMCIF files.
    """
    clean_auth_ids = set()
    for item in auth_seq_ids:
        if isinstance(item, int):
            clean_auth_ids.add(str(item))
        elif isinstance(item, str):
            parts = item.split(":")
            clean_auth_ids.add(parts[-1].strip())

    try:
        import gemmi

        doc = gemmi.cif.read_file(structure_path)
        block = doc.sole_block()
        atom_site = block.find(
            ["_atom_site.auth_asym_id", "_atom_site.auth_seq_id", "_atom_site.label_seq_id"]
        )
        mapped = set()
        for row in atom_site:
            row_chain = row[0]
            row_auth = row[1]
            row_label = row[2]
            if (row_chain == chain_id or not chain_id) and row_auth in clean_auth_ids:
                if row_label and row_label != "?" and row_label != ".":
                    try:
                        mapped.add(int(row_label))
                    except ValueError:
                        pass
        if mapped:
            return sorted(mapped)
    except Exception as e:
        logger.warning(f"Could not map auth_seq_id to label_seq_id via gemmi: {e}")

    fallback_ints = []
    for item in clean_auth_ids:
        try:
            fallback_ints.append(int(item))
        except ValueError:
            pass
    return sorted(fallback_ints)
