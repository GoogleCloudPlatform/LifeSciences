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

"""Generic database downloader driven by databases.yaml.

Reads the YAML manifest, builds download scripts (generic or custom),
and submits Cloud Batch jobs via core.batch.

Supports two data sources:
1. Internet download (default) — fetches from upstream URLs, backs up to GCS
2. GCS restore (--source-bucket) — restores from an existing GCS backup (any
   project), then optionally backs up to this project's GCS bucket. ~15 min
   vs 2-4 hours from internet. Enables cross-project database sharing.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from google.cloud import storage

from foldrun_app.core import batch as batch_utils

logger = logging.getLogger(__name__)

DATABASES_YAML = Path(__file__).parent.parent.parent / "databases.yaml"


def _get_models(db_config: Dict[str, Any]) -> List[str]:
    """Get the list of models for a database entry.

    Supports both 'models: [af2, of3]' (list) and legacy 'model: af2' (string).
    """
    models = db_config.get("models")
    if models:
        return models if isinstance(models, list) else [models]
    model = db_config.get("model")
    if model:
        return [model] if isinstance(model, str) else model
    return []


def load_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and return the databases.yaml manifest.

    Returns empty dict if the file doesn't exist (e.g., in Agent Engine
    where databases.yaml isn't bundled — download tools aren't used there).
    """
    path = path or DATABASES_YAML
    if not path.exists():
        logger.warning(f"databases.yaml not found at {path}, download tools will be unavailable")
        return {"databases": {}, "modes": {}}
    with open(path) as f:
        return yaml.safe_load(f)


def get_databases_for_models(
    manifest: Dict[str, Any],
    models: List[str],
    mode: Optional[str] = None,
) -> List[str]:
    """Return database names to download for the given models and mode.

    If mode is specified, uses the modes section of the manifest.
    Otherwise returns all databases tagged with the given models.
    """
    modes_config = manifest.get("modes", {})
    databases_config = manifest.get("databases", {})
    result = []

    for model in models:
        if mode and model in modes_config and mode in modes_config[model]:
            result.extend(modes_config[model][mode])
        elif mode and model in modes_config:
            # Mode doesn't exist for this model — fall back to all databases
            available = list(modes_config[model].keys())
            logger.warning(
                f"Mode '{mode}' not available for model '{model}' "
                f"(available: {', '.join(available)}). Downloading all databases."
            )
            result.extend(name for name, db in databases_config.items() if model in _get_models(db))
        else:
            # No mode specified — include all databases for this model
            result.extend(name for name, db in databases_config.items() if model in _get_models(db))

    # Deduplicate while preserving order (shared DBs may appear for multiple models)
    seen = set()
    deduped = []
    for name in result:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def check_gcs_exists(gcs_bucket: str, nfs_path: str) -> bool:
    """Check if a database already exists in GCS backup.

    Returns True if the GCS prefix has at least one object (indicating
    a previous successful download + sync).
    """
    try:
        client = storage.Client()
        bucket = client.bucket(gcs_bucket)
        prefix = nfs_path.rstrip("/") + "/"
        blobs = list(bucket.list_blobs(prefix=prefix, max_results=1))
        return len(blobs) > 0
    except Exception as e:
        logger.warning(f"Could not check GCS for {nfs_path}: {e}")
        return False


def check_existing(
    db_names: List[str],
    gcs_bucket: str,
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """Check which databases already exist in GCS.

    Returns dict of {db_name: exists_bool}.
    """
    if manifest is None:
        manifest = load_manifest()

    databases_config = manifest.get("databases", {})
    result = {}
    for db_name in db_names:
        if db_name not in databases_config:
            result[db_name] = False
            continue
        nfs_path = databases_config[db_name]["nfs_path"]
        result[db_name] = check_gcs_exists(gcs_bucket, nfs_path)
    return result


def build_script(db_name: str, db_config: Dict[str, Any], dest_path: str) -> str:
    """Build the bash script for a database download.

    If the entry has a 'script' field, use it directly (with {dest} substitution).
    Otherwise, generate a generic download + extract script from source/extract.
    """
    if "script" in db_config:
        return db_config["script"].replace("{dest}", dest_path)

    source = db_config.get("source")
    if not source:
        raise ValueError(f"Database '{db_name}' has neither 'script' nor 'source'")

    extract = db_config.get("extract", "none")

    lines = [f"aria2c --continue=true '{source}' --dir={dest_path}"]

    if extract == "gunzip":
        lines.append(f"gunzip -f {dest_path}/*.gz")
    elif extract == "tar":
        lines.append(f"tar xf {dest_path}/*.tar* -C {dest_path}")
        lines.append(f"rm -f {dest_path}/*.tar.gz {dest_path}/*.tar")
    elif extract == "tar_strip":
        lines.append(f"tar xf {dest_path}/*.tar* --strip-components=1 -C {dest_path}")
        lines.append(f"rm -f {dest_path}/*.tar.gz {dest_path}/*.tar")
    elif extract != "none":
        raise ValueError(f"Unknown extract method '{extract}' for '{db_name}'")

    return "\n".join(lines)


def _clean_label(value: str) -> str:
    """Clean a string for use as a GCP label value."""
    import re

    cleaned = re.sub(r"[^a-z0-9_-]", "-", value.lower())
    return cleaned[:63]


def submit_download(
    db_name: str,
    db_config: Dict[str, Any],
    project_id: str,
    region: str,
    zone: str,
    filestore_ip: str,
    nfs_share: str,
    nfs_mount: str,
    gcs_bucket: Optional[str] = None,
    source_bucket: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a Cloud Batch job to download a single database.

    Data source priority:
    1. source_bucket (if set) — restore from GCS (any project, ~15 min)
    2. Internet download (default) — fetch from upstream URLs (2-4 hours)

    After download/restore, backs up to gcs_bucket (if set).

    Returns dict with status, job_id, console_url, etc.
    """
    models = _get_models(db_config)
    model = models[0] if models else "unknown"
    display_name = db_config.get("display_name", db_name)
    nfs_path = db_config["nfs_path"]
    machine_type = db_config.get("machine_type", "n1-standard-4")
    local_ssd_count = db_config.get("local_ssd_count", 0)
    dest_path = f"{nfs_mount}/{nfs_path}"

    # Determine data source: GCS restore or internet download
    if source_bucket:
        source_gcs_path = f"gs://{source_bucket}/{nfs_path}/"
        download_script = (
            f"echo '=== Restoring from GCS: {source_gcs_path} ==='\n"
            f"gcloud storage rsync --recursive {source_gcs_path} {dest_path}/ 2>&1\n"
        )
        data_source = f"gcs:{source_bucket}"
    else:
        download_script = build_script(db_name, db_config, dest_path)
        data_source = "internet"

    # Backup to this project's GCS bucket (skip if source_bucket == gcs_bucket)
    gcs_sync = ""
    if gcs_bucket and gcs_bucket != source_bucket:
        gcs_path = f"gs://{gcs_bucket}/{nfs_path}/"
        # Use GCS_SYNC_SOURCE env var if set (e.g. for fast SSD -> GCS sync)
        # Fall back to dest_path (NFS)
        gcs_sync = (
            f"echo '=== Backing up to GCS: {gcs_path} ==='\n"
            f"SYNC_SOURCE=\"${{GCS_SYNC_SOURCE:-{dest_path}/}}\"\n"
            f"gcloud storage rsync --recursive \"$SYNC_SOURCE\" {gcs_path} 2>&1\n"
        )

    full_script = (
        f"set -e\n"
        f"apt-get update -qq && apt-get install -y -qq aria2 python3-crcmod 2>/dev/null || true\n"
        f"echo '=== {display_name} → NFS: {dest_path} (source: {data_source}) ==='\n"
        f"mkdir -p {dest_path}\n"
        f"{download_script}\n"
        f"echo '=== Download complete ==='\n"
        f"ls -lh {dest_path}/ | head -20\n"
        f"{gcs_sync}"
        f"echo '=== Done ==='\n"
    )

    # Job ID: <model>-dl-<db_name>-<timestamp>
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    clean_name = _clean_label(db_name).replace("_", "-")
    job_id = f"{model}-dl-{clean_name}-{timestamp}"

    labels = {
        "task": "database-download",
        "model": _clean_label(model),
        "database": _clean_label(db_name),
        "submitted-by": "foldrun",
    }

    try:
        result = batch_utils.submit_batch_job(
            project_id=project_id,
            region=region,
            zone=zone,
            job_id=job_id,
            script=full_script,
            machine_type=machine_type,
            filestore_ip=filestore_ip,
            nfs_share=nfs_share,
            nfs_mount=nfs_mount,
            labels=labels,
            local_ssd_count=local_ssd_count,
        )
        return {
            "status": "submitted",
            "database_name": db_name,
            "display_name": display_name,
            "model": model,
            "nfs_destination": dest_path,
            "machine_type": machine_type,
            **result,
        }
    except Exception as e:
        logger.error(f"Failed to submit batch job for {db_name}: {e}", exc_info=True)
        return {
            "status": "error",
            "database_name": db_name,
            "message": str(e),
        }


def submit_downloads(
    db_names: List[str],
    project_id: str,
    region: str,
    zone: str,
    filestore_ip: str,
    nfs_share: str,
    nfs_mount: str,
    gcs_bucket: Optional[str] = None,
    source_bucket: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Submit Cloud Batch jobs for multiple databases.

    Args:
        source_bucket: GCS bucket to restore from (any project). If set and the
            database exists there, restores from GCS instead of downloading from
            internet. The Compute Engine SA needs roles/storage.objectViewer on
            this bucket.
        gcs_bucket: This project's GCS bucket for backup after download.

    Checks GCS for existing data and skips databases already present
    unless force=True.

    Returns summary with submitted/skipped/failed counts and per-job results.
    """
    if manifest is None:
        manifest = load_manifest()

    databases_config = manifest.get("databases", {})

    # Check what already exists in this project's GCS bucket (skip if present)
    existing = {}
    if not force and gcs_bucket:
        existing = check_existing(db_names, gcs_bucket, manifest)

    # Check what's available in the source bucket for restore
    source_available = {}
    if source_bucket:
        source_available = check_existing(db_names, source_bucket, manifest)

    results = []

    for db_name in db_names:
        if db_name not in databases_config:
            results.append(
                {
                    "status": "error",
                    "database_name": db_name,
                    "message": f"Unknown database '{db_name}'",
                }
            )
            continue

        if not source_bucket and existing.get(db_name):
            nfs_path = databases_config[db_name]["nfs_path"]
            results.append(
                {
                    "status": "skipped",
                    "database_name": db_name,
                    "display_name": databases_config[db_name].get("display_name", db_name),
                    "message": f"Already exists in GCS (gs://{gcs_bucket}/{nfs_path}/). Use --force to re-download.",
                }
            )
            continue

        # Use source_bucket if the database is available there
        db_source_bucket = source_bucket if source_available.get(db_name) else None

        result = submit_download(
            db_name=db_name,
            db_config=databases_config[db_name],
            project_id=project_id,
            region=region,
            zone=zone,
            filestore_ip=filestore_ip,
            nfs_share=nfs_share,
            nfs_mount=nfs_mount,
            gcs_bucket=gcs_bucket,
            source_bucket=db_source_bucket,
        )
        results.append(result)

    submitted = [r for r in results if r["status"] == "submitted"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]

    return {
        "status": "submitted" if submitted else ("skipped" if skipped else "error"),
        "total": len(db_names),
        "submitted_count": len(submitted),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "jobs": results,
    }
