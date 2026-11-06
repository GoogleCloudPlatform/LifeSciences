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

"""Tool for retrieving RFD4-Proteina analysis results from GCS."""

import json
import logging
import math
import time
from typing import Any
from urllib.parse import quote_plus

from google.cloud import storage

from ..base import RFD4ProteinaTool

logger = logging.getLogger(__name__)


def _sanitize_data(obj: Any) -> Any:
    """Recursively sanitize NaN/Inf floats to None and strip bulky per-residue score arrays."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {
            k: _sanitize_data(v) for k, v in obj.items() if k not in ("plddt_scores", "per_residue")
        }
    if isinstance(obj, list):
        return [_sanitize_data(x) for x in obj]
    return obj


class RFD4ProteinaGetAnalysisResultsTool(RFD4ProteinaTool):
    """Retrieves RFD4-Proteina analysis results from Cloud Run Jobs."""

    def _get_analysis_path(self, job: Any) -> str:
        """Get GCS analysis path for a job."""
        pipeline_root = None
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        if not pipeline_root:
            raise ValueError("Job does not have gcs_output_directory in runtime_config")

        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        return f"{pipeline_root}analysis/"

    def _read_from_gcs(self, gcs_uri: str) -> dict[str, Any]:
        """Read JSON data from GCS."""
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        parts = gcs_uri[5:].split("/", 1)
        bucket_name = parts[0]
        blob_name = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            raise FileNotFoundError(f"Blob does not exist: {gcs_uri}")

        content = blob.download_as_string()
        return _sanitize_data(json.loads(content, parse_constant=lambda _: None))

    def _generate_viewer_url(self, job_id: str, best_prediction: dict[str, Any]) -> str:
        """Generate viewer URL pointing to best prediction."""
        base_url = self.config.viewer_url
        if not base_url:
            return ""

        cif_uri = best_prediction.get("cif_uri", "")
        if not cif_uri:
            return f"{base_url}/job/{job_id}"

        cif_param = quote_plus(cif_uri)
        return f"{base_url}/job/{job_id}?structure={cif_param}"

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Retrieve analysis results for an RFD4-Proteina job.

        Args:
            arguments: {
                'job_name': Pipeline job name or ID,
                'wait_for_completion': Whether to wait if in progress (default: False),
                'timeout_seconds': Max wait time (default: 300),
            }
        """
        job_name = arguments.get("job_id") or arguments.get("job_name")
        if not job_name:
            raise ValueError("job_name is required")

        wait_for_completion = arguments.get("wait_for_completion") or arguments.get("wait", False)
        timeout_seconds = arguments.get("timeout_seconds") or arguments.get("timeout", 300)

        from foldrun_app.core.vertex_utils import get_pipeline_job

        try:
            job = get_pipeline_job(job_name, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_name}: {e!s}"}
        analysis_path = self._get_analysis_path(job)
        job_id = job.name.split("/")[-1]

        summary_uri = f"{analysis_path}summary.json"

        if wait_for_completion:
            start_time = time.time()
            while time.time() - start_time < timeout_seconds:
                try:
                    summary_data = self._read_from_gcs(summary_uri)
                    viewer_url = self._generate_viewer_url(
                        job_id, summary_data.get("best_prediction", {})
                    )
                    return {
                        "status": "COMPLETED",
                        "job_id": job_id,
                        "analysis_path": analysis_path,
                        "summary": summary_data,
                        "viewer_url": viewer_url,
                    }
                except FileNotFoundError:
                    time.sleep(5)

            return {
                "status": "TIMEOUT",
                "message": f"Analysis did not complete within {timeout_seconds} seconds",
                "analysis_path": analysis_path,
                "job_id": job_id,
            }

        try:
            summary_data = self._read_from_gcs(summary_uri)
            viewer_url = self._generate_viewer_url(job_id, summary_data.get("best_prediction", {}))
            return {
                "status": "COMPLETED",
                "job_id": job_id,
                "analysis_path": analysis_path,
                "summary": summary_data,
                "viewer_url": viewer_url,
            }
        except FileNotFoundError:
            return {
                "status": "NOT_FOUND",
                "message": "Analysis has not been run or is still processing. Use rfd4_proteina_analyze_job first.",
                "analysis_path": analysis_path,
                "job_id": job_id,
            }
