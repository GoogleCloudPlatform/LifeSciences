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

"""Tool for retrieving AlphaFold 3 prediction results and biophysical confidence metrics."""

import json
import logging
from typing import Any

from google.api_core.exceptions import GoogleAPICallError

from ..base import MODEL_CIF_FILENAME, AF3Tool
from ..utils.metrics import compute_ranking_score

logger = logging.getLogger(__name__)


class AF3GetResultsTool(AF3Tool):
    """Retrieves prediction metrics, confidence scores, and CIF coordinates for an AF3 run."""

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fetch AF3 results from GCS.

        Args:
            arguments: {'job_id': AlphaFold 3 prediction job ID or name}

        Returns:
            Dictionary containing metrics (ranking_score, pTM, ipTM, pLDDT), quality assessment, and CIF URI.
        """
        job_id = arguments.get("job_id")
        if not job_id:
            return {"status": "error", "message": "Missing required argument 'job_id'."}

        bucket_name = self.config.bucket_name
        try:
            bucket = self.storage_client.bucket(bucket_name)

            conf_blob = bucket.blob(self.get_conf_blob_path(job_id))
            cif_blob = bucket.blob(self.get_cif_blob_path(job_id))

            conf_exists = conf_blob.exists()
            cif_exists = cif_blob.exists()

            if not conf_exists and not cif_exists:
                return {
                    "status": "not_found",
                    "job_id": job_id,
                    "message": (
                        f"No AlphaFold 3 artifacts found at {self.get_job_gcs_prefix(job_id)}."
                    ),
                }

            summary_confidences = {}
            if conf_exists:
                try:
                    summary_confidences = json.loads(conf_blob.download_as_text())
                except Exception as e:
                    logger.warning(f"Could not parse summary_confidences.json for {job_id}: {e}")

            cif_filename = MODEL_CIF_FILENAME.format(job_id=job_id)
            cif_uri = f"{self.get_job_gcs_prefix(job_id)}/{cif_filename}" if cif_exists else None

            ranking_score = compute_ranking_score(summary_confidences)
            ptm = summary_confidences.get("ptm")
            iptm = summary_confidences.get("iptm")
            mean_plddt = summary_confidences.get("mean_plddt")

            # Biophysical confidence categorization following DeepMind AF3 benchmark standards
            if ranking_score is not None:
                score = float(ranking_score)
                if score >= 0.80:
                    assessment = "High confidence (reliable complex interface and fold)"
                elif score >= 0.60:
                    assessment = (
                        "Moderate confidence (plausible overall topology; verify interface)"
                    )
                else:
                    assessment = "Low confidence (uncertain relative domain/chain orientation)"
            else:
                assessment = "Metrics pending or incomplete"

            viewer_url = (
                f"{self.config.viewer_url}/job/{job_id}?model=af3"
                if self.config.viewer_url
                else self.gcs_console_url(cif_uri)
            )

            from foldrun_app.core.download_utils import (
                generate_signed_download_url,
                prepare_signing_context,
            )

            signing_client, signing_creds = prepare_signing_context(
                project_id=self.config.project_id
            )

            cif_signed_url = (
                generate_signed_download_url(
                    cif_uri,
                    download_filename=cif_filename,
                    project_id=self.config.project_id,
                    storage_client=signing_client,
                    credentials=signing_creds,
                )
                if cif_uri
                else None
            )
            conf_uri = (
                f"gs://{bucket_name}/{self.get_conf_blob_path(job_id)}" if conf_exists else None
            )
            conf_signed_url = (
                generate_signed_download_url(
                    conf_uri,
                    download_filename=f"{job_id}_summary_confidences.json",
                    project_id=self.config.project_id,
                    content_type="application/json",
                    storage_client=signing_client,
                    credentials=signing_creds,
                )
                if conf_uri
                else None
            )

            return {
                "status": "succeeded",
                "job_id": job_id,
                "model": "AlphaFold 3",
                "cif_uri": cif_uri,
                "cif_signed_url": cif_signed_url,
                "downloads": {
                    "best_structure_signed_url": cif_signed_url,
                    "summary_json_signed_url": conf_signed_url,
                    "expires_in_minutes": 60,
                },
                "metrics": {
                    "ranking_score": round(float(ranking_score), 4)
                    if ranking_score is not None
                    else None,
                    "ptm": round(float(ptm), 4) if ptm is not None else None,
                    "iptm": round(float(iptm), 4) if iptm is not None else None,
                    "mean_plddt": round(float(mean_plddt), 2) if mean_plddt is not None else None,
                    "has_clash": summary_confidences.get("has_clash", 0.0),
                    "fraction_disordered": summary_confidences.get("fraction_disordered"),
                },
                "quality_assessment": assessment,
                "viewer_url": viewer_url,
                "console_url": self.gcs_console_url(self.get_job_gcs_prefix(job_id)),
            }

        except GoogleAPICallError as e:
            logger.exception(f"GCS API error while retrieving AF3 results for {job_id}: {e}")
            return {
                "status": "error",
                "job_id": job_id,
                "message": f"Cloud Storage API error: {e.message or str(e)}",
            }
        except Exception as e:
            logger.exception(f"Unexpected error while retrieving AF3 results for {job_id}: {e}")
            return {
                "status": "error",
                "job_id": job_id,
                "message": f"Failed to retrieve AlphaFold 3 results: {e!s}",
            }
