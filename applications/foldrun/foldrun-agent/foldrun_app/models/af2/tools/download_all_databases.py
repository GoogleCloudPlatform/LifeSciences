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

"""Tool for downloading all genetic databases in parallel via Cloud Batch.

Submits one Cloud Batch job per database. Each job downloads directly to NFS
(Filestore) and then backs up to GCS. Database definitions come from
databases.yaml.
"""

import logging
from typing import Any, Dict

from foldrun_app.core.download import get_databases_for_models, load_manifest

from ..base import AF2Tool
from .download_database import DATABASE_SUBDIRS, DownloadDatabaseTool

logger = logging.getLogger(__name__)


class DownloadAllDatabasesTool(AF2Tool):
    """Download all genetic databases required for AlphaFold via Cloud Batch.

    Submits one Cloud Batch job per database for parallel downloading.
    Each job downloads directly to NFS and backs up to GCS.
    """

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        download_mode = arguments.get("download_mode", "full")
        if download_mode == "full_dbs":
            download_mode = "full"
        elif download_mode == "reduced_dbs":
            download_mode = "reduced"

        manifest = load_manifest()
        try:
            databases = get_databases_for_models(manifest, ["af2"], download_mode)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        gcs_output_prefix = arguments.get(
            "gcs_output_prefix",
            f"gs://{self.config.databases_bucket_name}/",
        )
        if not gcs_output_prefix.endswith("/"):
            gcs_output_prefix += "/"

        logger.info(
            f"Submitting Cloud Batch download jobs for {len(databases)} databases "
            f"(mode: {download_mode})"
        )

        download_tool = DownloadDatabaseTool(tool_config=self.tool_config, config=self.config)

        submitted_jobs = []
        errors = []

        for db_name in databases:
            subdir = DATABASE_SUBDIRS.get(db_name, db_name)
            gcs_path = f"{gcs_output_prefix}{subdir}/"
            result = download_tool.run(
                {
                    "database_name": db_name,
                    "gcs_output_path": gcs_path,
                }
            )

            if result.get("status") == "error":
                errors.append({"database_name": db_name, "error": result.get("message")})
            else:
                submitted_jobs.append(result)

        return {
            "status": "submitted" if submitted_jobs else "error",
            "download_mode": download_mode,
            "gcs_output_prefix": gcs_output_prefix,
            "total_databases": len(databases),
            "submitted_count": len(submitted_jobs),
            "error_count": len(errors),
            "submitted_jobs": submitted_jobs,
            "errors": errors if errors else None,
            "message": (
                f"Submitted {len(submitted_jobs)}/{len(databases)} Cloud Batch jobs "
                f"({download_mode} mode). Each job downloads directly to NFS, "
                f"then backs up to GCS."
            ),
        }
