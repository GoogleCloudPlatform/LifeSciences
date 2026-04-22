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

"""Submit database download jobs via Cloud Batch.

Reads databases.yaml and submits one Batch job per database entry.

Usage:
    # Download all AF2 databases (reduced mode)
    python scripts/setup_data.py --models af2 --mode reduced

    # Download all OF3 databases
    python scripts/setup_data.py --models of3

    # Download OF3 core only (weights + CCD, no RNA databases)
    python scripts/setup_data.py --models of3 --mode core

    # Download everything for both models
    python scripts/setup_data.py --models af2,of3

    # Download a single database ad-hoc
    python scripts/setup_data.py --db of3_params

    # List available databases
    python scripts/setup_data.py --list
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from foldrun_app.core.config import CoreConfig
from foldrun_app.core.download import (
    _get_models,
    check_existing,
    get_databases_for_models,
    load_manifest,
    submit_downloads,
)


def get_infra_params(config: CoreConfig) -> dict:
    """Resolve infrastructure parameters needed for Batch submission."""
    if not config.filestore_ip:
        raise ValueError("FILESTORE_IP environment variable must be set.")
    return {
        "project_id": config.project_id,
        "region": config.region,
        "zone": config.zone,
        "filestore_ip": config.filestore_ip,
        "nfs_share": config.nfs_share,
        "nfs_mount": config.nfs_mount_point,
        "gcs_bucket": config.databases_bucket_name,
    }


def show_status(manifest):
    """Check which databases exist in GCS and display status."""
    from foldrun_app.core.download import check_gcs_exists
    from foldrun_app.models.af2.tools.download_database import MMSEQS2_INDEXABLE_DATABASES

    config = CoreConfig()
    gcs_bucket = config.databases_bucket_name

    databases = manifest.get("databases", {})
    db_names = list(databases.keys())

    print(f"Checking GCS bucket: gs://{gcs_bucket}/")
    existing = check_existing(db_names, gcs_bucket, manifest)

    # Check MMseqs2 indexes
    mmseqs2_status = {}
    for db_name, info in MMSEQS2_INDEXABLE_DATABASES.items():
        nfs_path = databases.get(db_name, {}).get("nfs_path", db_name)
        mmseqs_prefix = f"{nfs_path}/{info['mmseqs_name']}"
        mmseqs2_status[db_name] = check_gcs_exists(gcs_bucket, mmseqs_prefix)

    by_model = {}
    for name, db in databases.items():
        for model in _get_models(db):
            by_model.setdefault(model, []).append((name, db, existing.get(name, False)))

    for model, dbs in sorted(by_model.items()):
        print(f"\n{model}:")
        for name, db, exists in dbs:
            status = "present" if exists else "MISSING"
            line = f"  {name:25s} [{status:7s}]  {db.get('display_name', '')}"
            if name in mmseqs2_status:
                mmseqs = "ready" if mmseqs2_status[name] else "not built"
                line += f"  (mmseqs2: {mmseqs})"
            print(line)


def dry_run(db_names, config, manifest, force):
    """Show what would be downloaded without submitting jobs."""
    gcs_bucket = config.databases_bucket_name
    databases_config = manifest.get("databases", {})

    existing = {}
    if not force and gcs_bucket:
        print(f"Checking GCS bucket: gs://{gcs_bucket}/\n")
        existing = check_existing(db_names, gcs_bucket, manifest)

    to_download = []
    to_skip = []
    for name in db_names:
        db = databases_config.get(name, {})
        display = db.get("display_name", name)
        nfs_path = db.get("nfs_path", name)
        if existing.get(name) and not force:
            to_skip.append((name, display, nfs_path))
        else:
            to_download.append((name, display, nfs_path))

    if to_skip:
        print(f"Would skip ({len(to_skip)} already in GCS):")
        for name, display, nfs_path in to_skip:
            print(f"  {name:25s} {display}")

    if to_download:
        print(f"\nWould download ({len(to_download)}):")
        for name, display, nfs_path in to_download:
            print(f"  {name:25s} -> /mnt/nfs/foldrun/{nfs_path}")

    if not to_download:
        print("\nNothing to download. Use --force to re-download.")
    else:
        print(f"\nRun without --dry-run to submit {len(to_download)} Batch job(s).")


def list_databases(manifest):
    """Print all available databases grouped by model."""
    databases = manifest.get("databases", {})
    modes = manifest.get("modes", {})

    by_model = {}
    for name, db in databases.items():
        model = db.get("model", "unknown")
        by_model.setdefault(model, []).append((name, db))

    for model, dbs in sorted(by_model.items()):
        print(f"\n{model}:")
        for name, db in dbs:
            print(f"  {name:25s} {db.get('display_name', '')}")

        if model in modes:
            print(f"  Modes: {', '.join(modes[model].keys())}")


def main():
    parser = argparse.ArgumentParser(description="Submit database download jobs to Cloud Batch.")
    parser.add_argument(
        "--models",
        help="Comma-separated list of models (e.g. af2,of3). Downloads all databases for those models.",
    )
    parser.add_argument(
        "--mode",
        help="Download mode (e.g. reduced, full, core). Model-specific — see databases.yaml.",
    )
    parser.add_argument(
        "--db",
        metavar="DATABASE",
        help="Download a single database by name (e.g. of3_params, uniref90).",
    )
    parser.add_argument(
        "--source-bucket",
        metavar="BUCKET",
        help="Restore databases from this GCS bucket instead of downloading from "
        "internet. Can be any bucket (cross-project). The Compute Engine SA "
        "needs roles/storage.objectViewer on this bucket. ~15 min vs 2-4 hours.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if data already exists in GCS.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without submitting jobs.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available databases and exit.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check which databases exist in GCS and exit.",
    )
    args = parser.parse_args()

    manifest = load_manifest()

    if args.list:
        list_databases(manifest)
        return

    if args.status:
        show_status(manifest)
        return

    if not args.models and not args.db:
        # Default: use DOWNLOAD_MODELS env var, or af2,of3,boltz
        models_str = os.environ.get("DOWNLOAD_MODELS", "af2,of3,boltz")
        args.models = models_str

    if not args.mode:
        args.mode = os.environ.get("DOWNLOAD_MODE")

    # Resolve which databases to download
    if args.db:
        db_names = [args.db]
    else:
        models = [m.strip() for m in args.models.split(",")]
        db_names = get_databases_for_models(manifest, models, args.mode)

    if not db_names:
        print("No databases to download.")
        return

    config = CoreConfig()

    if args.dry_run:
        dry_run(db_names, config, manifest, args.force)
        return

    print(f"Databases to download: {', '.join(db_names)}")

    infra = get_infra_params(config)

    # Support --source-bucket or GCS_SOURCE_BUCKET env var
    source_bucket = args.source_bucket or os.environ.get("GCS_SOURCE_BUCKET")
    if source_bucket:
        print(f"Source bucket: gs://{source_bucket}/ (will restore from GCS instead of internet)")

    result = submit_downloads(
        db_names=db_names,
        manifest=manifest,
        force=args.force,
        source_bucket=source_bucket,
        **infra,
    )

    submitted = result["submitted_count"]
    skipped = result.get("skipped_count", 0)
    errors = result["error_count"]
    total = result["total"]

    if skipped > 0:
        print(f"\nSkipped {skipped} (already in GCS):")
        for job in result["jobs"]:
            if job["status"] == "skipped":
                print(f"  {job['database_name']:25s} {job['message']}")

    if submitted == 0 and errors > 0:
        print(f"\nERROR: {errors} downloads failed.")
        for job in result["jobs"]:
            if job["status"] == "error":
                print(f"  - {job['database_name']}: {job['message']}")
        sys.exit(1)

    if submitted == 0 and skipped > 0:
        print(f"\nAll {skipped} databases already present. Use --force to re-download.")
        return

    print(f"\nSubmitted {submitted}/{total} download jobs.")
    for job in result["jobs"]:
        if job["status"] == "submitted":
            print(f"  {job['database_name']:25s} {job['job_id']}")
            print(f"    -> {job['nfs_destination']}")

    if errors > 0:
        print(f"\nWARNING: {errors} jobs failed:")
        for job in result["jobs"]:
            if job["status"] == "error":
                print(f"  - {job['database_name']}: {job['message']}")

    print("\nDownloads run asynchronously in Cloud Batch.")
    print(
        f"Monitor: gcloud batch jobs list --project={config.project_id} --location={config.region}"
    )


if __name__ == "__main__":
    main()
