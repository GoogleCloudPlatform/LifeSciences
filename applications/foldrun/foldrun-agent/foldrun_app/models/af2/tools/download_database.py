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

"""Tool for downloading individual genetic databases via Cloud Batch.

Downloads from source directly to NFS (Filestore), then backs up to GCS.
No large local disk needed — all data written to the NFS mount.

Database definitions live in databases.yaml. This tool provides the agent-facing
interface; actual Batch submission is handled by core.batch and core.download.

MMseqs2 conversion (for GPU-accelerated search) is handled separately by
ConvertMMseqs2Tool and is optional.  The standard AlphaFold2 pipeline
uses Jackhmmer/HHblits directly on the raw FASTA/HH-suite databases.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from foldrun_app.core import batch as batch_utils
from foldrun_app.core.download import _get_models, build_script, load_manifest

from ..base import AF2Tool

logger = logging.getLogger(__name__)

# Load database definitions from databases.yaml
_manifest = load_manifest()
_databases = _manifest.get("databases", {})

# AF2 databases — includes shared databases tagged with af2
DATABASE_REGISTRY = {name: db for name, db in _databases.items() if "af2" in _get_models(db)}

# Map database_name -> NFS subdirectory name
DATABASE_SUBDIRS = {
    name: db["nfs_path"] for name, db in _databases.items() if "af2" in _get_models(db)
}

VALID_DATABASE_NAMES = list(DATABASE_REGISTRY.keys())

# Databases that support MMseqs2 GPU indexing (FASTA format only).
MMSEQS2_INDEXABLE_DATABASES = {
    "uniref90": {
        "fasta_file": "uniref90.fasta",
        "mmseqs_name": "uniref90_mmseqs",
        "index_splits": 1,
    },
    "mgnify": {
        "fasta_file": "mgy_clusters_2022_05.fa",
        "mmseqs_name": "mgnify_mmseqs",
        "index_splits": 1,
    },
    "small_bfd": {
        "fasta_file": "bfd-first_non_consensus_sequences.fasta",
        "mmseqs_name": "small_bfd_mmseqs",
        "index_splits": 1,
    },
}

MMSEQS2_MACHINE_TYPE = "n1-highmem-32"
MMSEQS2_LOCAL_SSD_COUNT = 2


class DownloadDatabaseTool(AF2Tool):
    """Download a single genetic database via Cloud Batch.

    Downloads directly to NFS (Filestore), then backs up to GCS.
    Uses n1-standard-4 by default — cheap and sufficient for downloads.

    For MMseqs2-indexable databases (uniref90, mgnify, small_bfd), the
    response includes a flag indicating that optional GPU index conversion
    is available via ConvertMMseqs2Tool.
    """

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        database_name = arguments.get("database_name")
        if not database_name or database_name not in DATABASE_REGISTRY:
            return {
                "status": "error",
                "message": (
                    f"Invalid database_name '{database_name}'. "
                    f"Must be one of: {', '.join(VALID_DATABASE_NAMES)}"
                ),
            }

        db_config = DATABASE_REGISTRY[database_name]
        machine_type = arguments.get("machine_type", db_config.get("machine_type", "n1-standard-4"))
        subdir = arguments.get("nfs_target_dir", DATABASE_SUBDIRS[database_name])

        # GCS destination
        default_gcs_path = f"gs://{self.config.databases_bucket_name}/{subdir}/"
        gcs_output_path = arguments.get("gcs_output_path", default_gcs_path)
        if not gcs_output_path.endswith("/"):
            gcs_output_path += "/"

        try:
            filestore_ip, filestore_network = self._get_filestore_info()
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to get Filestore info: {str(e)}. Ensure FILESTORE_ID is configured.",
            }

        nfs_mount = self.config.nfs_mount_point
        nfs_share = self.config.nfs_share
        dest_path = f"{nfs_mount}/{subdir}"
        display_name = db_config.get("display_name", database_name)

        # Build download script from YAML definition
        download_script = build_script(database_name, db_config, dest_path)
        full_script = (
            f"set -e\n"
            f"apt-get update -qq && apt-get install -y -qq aria2 python3-crcmod 2>/dev/null || true\n"
            f"echo '=== Downloading {display_name} to NFS: {dest_path} ==='\n"
            f"mkdir -p {dest_path}\n"
            f"{download_script}\n"
            f"echo '=== Download complete ==='\n"
            f"ls -lh {dest_path}/ | head -20\n"
            f"echo '=== Backing up to GCS: {gcs_output_path} ==='\n"
            f"gcloud storage rsync --recursive {dest_path}/ {gcs_output_path} 2>&1\n"
            f"echo '=== Done ==='\n"
        )

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean_name = self._clean_label(database_name).replace("_", "-")
        job_id = f"af2-dl-{clean_name}-{timestamp}"

        logger.info(
            f"Submitting Cloud Batch job for {database_name} -> NFS:{dest_path} + GCS:{gcs_output_path}"
        )

        try:
            result = batch_utils.submit_batch_job(
                project_id=self.config.project_id,
                region=self.config.region,
                zone=self.config.zone,
                job_id=job_id,
                script=full_script,
                machine_type=machine_type,
                filestore_ip=filestore_ip,
                filestore_network=filestore_network,
                nfs_share=nfs_share,
                nfs_mount=nfs_mount,
                labels={
                    "task": "database-download",
                    "database": self._clean_label(database_name),
                    "submitted-by": "foldrun-agent",
                },
            )
            result.update(
                {
                    "status": "submitted",
                    "database_name": database_name,
                    "display_name": display_name,
                    "nfs_destination": dest_path,
                    "gcs_destination": gcs_output_path,
                    "machine_type": machine_type,
                    "message": (
                        f"Cloud Batch job for {display_name} submitted. "
                        f"Data will download directly to NFS ({dest_path}), "
                        f"then back up to GCS ({gcs_output_path})."
                    ),
                }
            )

            if database_name in MMSEQS2_INDEXABLE_DATABASES:
                result["mmseqs2_conversion_available"] = True
                result["mmseqs2_note"] = (
                    f"Optional: run ConvertMMseqs2Tool on '{database_name}' "
                    "to enable GPU-accelerated search (msa_method='mmseqs2'). "
                    "Not required for standard Jackhmmer/HHblits pipelines."
                )
            return result
        except Exception as e:
            logger.error(f"Failed to submit batch job for {database_name}: {e}", exc_info=True)
            return {
                "status": "error",
                "database_name": database_name,
                "message": f"Failed to submit Cloud Batch job: {str(e)}",
            }


class ConvertMMseqs2Tool(AF2Tool):
    """One-time conversion of existing FASTA databases on NFS to MMseqs2 format.

    Creates MMseqs2 database indexes alongside existing FASTA files so that
    GPU-accelerated search can be used with msa_method='mmseqs2'.
    """

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        databases = arguments.get("databases")
        if not databases:
            databases = list(MMSEQS2_INDEXABLE_DATABASES.keys())

        invalid = [db for db in databases if db not in MMSEQS2_INDEXABLE_DATABASES]
        if invalid:
            return {
                "status": "error",
                "message": (
                    f"Invalid database(s): {', '.join(invalid)}. "
                    f"Supported: {', '.join(MMSEQS2_INDEXABLE_DATABASES.keys())}"
                ),
            }

        machine_type_override = arguments.get("machine_type")

        try:
            filestore_ip, filestore_network = self._get_filestore_info()
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to get Filestore info: {str(e)}",
            }

        nfs_mount = self.config.nfs_mount_point
        nfs_share = self.config.nfs_share
        results = []

        for db_name in databases:
            db_info = MMSEQS2_INDEXABLE_DATABASES[db_name]
            subdir = DATABASE_SUBDIRS[db_name]
            fasta_path = f"{nfs_mount}/{subdir}/{db_info['fasta_file']}"
            mmseqs_path = f"{nfs_mount}/{subdir}/{db_info['mmseqs_name']}"
            machine_type = machine_type_override or MMSEQS2_MACHINE_TYPE
            index_splits = db_info.get("index_splits", 1)

            local_mmseqs = f"/mnt/localssd/{db_info['mmseqs_name']}"

            script = "\n".join(
                [
                    "set -e",
                    "echo '=== Setting up local SSD RAID-0 (750 GB) ==='",
                    "apt-get update -qq",
                    "apt-get install -y -qq mdadm python3-crcmod 2>/dev/null || true",
                    "mdadm --create /dev/md0 --level=0 --raid-devices=2 /dev/nvme0n1 /dev/nvme0n2 --force --run",
                    "mkfs.ext4 -F /dev/md0",
                    "mkdir -p /mnt/localssd",
                    "mount /dev/md0 /mnt/localssd",
                    "export TMPDIR=/mnt/localssd/tmp && mkdir -p $TMPDIR",
                    f"echo '=== Cleaning up previous MMseqs2 files for {db_name} ==='",
                    f"rm -f '{mmseqs_path}' '{mmseqs_path}'.*  '{mmseqs_path}_h' '{mmseqs_path}_h'.*",
                    f"rm -f '{mmseqs_path}_tmp' '{mmseqs_path}_tmp'.* '{mmseqs_path}_tmp_h' '{mmseqs_path}_tmp_h'.*",
                    "echo '=== Installing MMseqs2 ==='",
                    "wget -q -O- https://mmseqs.com/latest/mmseqs-linux-avx2.tar.gz | tar xz -C /mnt/localssd",
                    "MMSEQS=/mnt/localssd/mmseqs/bin/mmseqs",
                    f"echo '=== Converting {db_name} on local SSD ==='",
                    f"if [ -f '{fasta_path}' ]; then",
                    f"  $MMSEQS createdb '{fasta_path}' '{local_mmseqs}_tmp'",
                    f"  echo '{db_name}: makepaddedseqdb'",
                    f"  $MMSEQS makepaddedseqdb '{local_mmseqs}_tmp' '{local_mmseqs}'",
                    f"  rm -f '{local_mmseqs}_tmp' '{local_mmseqs}_tmp'.* '{local_mmseqs}_tmp_h' '{local_mmseqs}_tmp_h'.*",
                    f"  echo '{db_name}: createindex (--split {index_splits}, fully on local SSD)'",
                    f"  $MMSEQS createindex '{local_mmseqs}' /mnt/localssd/tmp --split {index_splits} --index-subset 2 --remove-tmp-files 1",
                    f"  echo '{db_name}: copying results to NFS'",
                    f"  cp {local_mmseqs}* {nfs_mount}/{subdir}/",
                    f"  echo '{db_name}: done'",
                    "else",
                    f"  echo '{db_name}: FASTA not found at {fasta_path}, skipping'",
                    "  exit 1",
                    "fi",
                    "echo '=== Syncing to GCS ==='",
                    f"gcloud storage rsync --recursive {nfs_mount}/{subdir}/ gs://{self.config.databases_bucket_name}/{subdir}/ 2>&1",
                    f"echo '=== {db_name} conversion complete ==='",
                ]
            )

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            clean_name = self._clean_label(db_name).replace("_", "-")
            job_id = f"af2-mmseqs2-{clean_name}-{timestamp}"

            try:
                result = batch_utils.submit_batch_job(
                    project_id=self.config.project_id,
                    region=self.config.region,
                    zone=self.config.zone,
                    job_id=job_id,
                    script=script,
                    machine_type=machine_type,
                    filestore_ip=filestore_ip,
                    filestore_network=filestore_network,
                    nfs_share=nfs_share,
                    nfs_mount=nfs_mount,
                    labels={
                        "task": "mmseqs2-conversion",
                        "database": self._clean_label(db_name),
                        "submitted-by": "foldrun-agent",
                    },
                    local_ssd_count=MMSEQS2_LOCAL_SSD_COUNT,
                )
                result["status"] = "submitted"
                result["database_name"] = db_name
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Failed to submit MMseqs2 conversion for {db_name}: {e}", exc_info=True
                )
                results.append(
                    {
                        "status": "error",
                        "database_name": db_name,
                        "message": f"Failed to submit: {str(e)}",
                    }
                )

        submitted = [r for r in results if r.get("status") == "submitted"]
        failed = [r for r in results if r.get("status") == "error"]

        return {
            "status": "submitted" if submitted else "error",
            "jobs": results,
            "summary": (
                f"Submitted {len(submitted)} parallel conversion job(s)"
                + (f", {len(failed)} failed to submit" if failed else "")
            ),
        }
