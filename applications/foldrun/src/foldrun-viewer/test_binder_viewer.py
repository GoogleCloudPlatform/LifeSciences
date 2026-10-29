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

"""Generative binder model routing and discovery unit tests for FoldRun Viewer."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROJECT_ID", "test-project")
os.environ.setdefault("BUCKET_NAME", "allowed-foldrun-bucket")
os.environ.setdefault("REGION", "us-central1")
os.environ.setdefault("GCS_BUCKET", "allowed-foldrun-bucket")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("GOOGLE_CLOUD_REGION", "us-central1")

with (
    patch("google.auth.default", return_value=(MagicMock(), "test-project")),
    patch("google.cloud.storage.Client", return_value=MagicMock()),
):
    sys.path.insert(0, os.path.dirname(__file__))
    import app as viewer_app


class TestGenerativeBinderViewerRouting(unittest.TestCase):
    """Tests for generative binder model_type normalization and /api/analyze routing."""

    def setUp(self):
        self.client = viewer_app.app.test_client()

    @staticmethod
    def _mock_blob(name: str):
        b = MagicMock()
        b.name = name
        return b

    def test_normalize_model_type_aliases(self):
        self.assertEqual(viewer_app._normalize_model_type("af2"), "alphafold2")
        self.assertEqual(viewer_app._normalize_model_type("af3"), "alphafold3")
        self.assertEqual(viewer_app._normalize_model_type("of3"), "openfold3")
        self.assertEqual(viewer_app._normalize_model_type("boltz"), "boltz2")
        self.assertEqual(viewer_app._normalize_model_type("boltz-2"), "boltz2")
        self.assertEqual(viewer_app._normalize_model_type("boltzgen"), "boltzgen")
        self.assertEqual(
            viewer_app._normalize_model_type("rfd4-proteina"), "rfd4_proteina"
        )
        self.assertEqual(
            viewer_app._normalize_model_type("rfd4_proteina"), "rfd4_proteina"
        )
        self.assertEqual(viewer_app._normalize_model_type("rfd4"), "rfd4_proteina")
        self.assertEqual(viewer_app._normalize_model_type("proteina"), "rfd4_proteina")
        self.assertEqual(viewer_app._normalize_model_type("bindcraft"), "bindcraft")

    def test_infer_model_type_from_display_name(self):
        self.assertEqual(
            viewer_app._infer_model_type_from_display_name("boltzgen-pdl1-run"),
            "boltzgen",
        )
        self.assertEqual(
            viewer_app._infer_model_type_from_display_name("rfd4-proteina-design"),
            "rfd4_proteina",
        )
        self.assertEqual(
            viewer_app._infer_model_type_from_display_name("bindcraft-binder-run"),
            "bindcraft",
        )

    def test_trigger_analysis_boltzgen_discovery(self):
        mock_authed = MagicMock()
        pj_resp = MagicMock()
        pj_resp.status_code = 200
        pj_resp.raise_for_status.return_value = None
        pj_resp.json.return_value = {
            "name": "projects/test-project/locations/us-central1/pipelineJobs/boltzgen-job-1",
            "labels": {"model_type": "boltzgen"},
            "runtimeConfig": {
                "gcsOutputDirectory": "gs://allowed-foldrun-bucket/pipeline_runs/boltzgen-run-01"
            },
        }
        cr_resp = MagicMock()
        cr_resp.raise_for_status.return_value = None
        mock_authed.get.return_value = pj_resp
        mock_authed.post.return_value = cr_resp

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [
            self._mock_blob(
                "pipeline_runs/boltzgen-run-01/predictions/confidence_design_0.json"
            ),
            self._mock_blob("pipeline_runs/boltzgen-run-01/predictions/design_0.cif"),
        ]
        mock_bucket.blob.return_value = MagicMock()

        with (
            patch.object(viewer_app, "_get_authed_session", return_value=mock_authed),
            patch.object(viewer_app.storage_client, "bucket", return_value=mock_bucket),
        ):
            resp = self.client.post(
                "/api/analyze",
                json={"job_id": "boltzgen-job-1", "model_type": "boltzgen"},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["model_type"], "boltzgen")
        self.assertEqual(payload["total_predictions"], 1)

    def test_trigger_analysis_rfd4_proteina_hyphen_alias(self):
        mock_authed = MagicMock()
        pj_resp = MagicMock()
        pj_resp.status_code = 200
        pj_resp.raise_for_status.return_value = None
        pj_resp.json.return_value = {
            "name": "projects/test-project/locations/us-central1/pipelineJobs/rfd4-job-1",
            "labels": {"model_type": "rfd4-proteina"},
            "runtimeConfig": {
                "gcsOutputDirectory": "gs://allowed-foldrun-bucket/pipeline_runs/rfd4-run-01"
            },
        }
        cr_resp = MagicMock()
        cr_resp.raise_for_status.return_value = None
        mock_authed.get.return_value = pj_resp
        mock_authed.post.return_value = cr_resp

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [
            self._mock_blob(
                "pipeline_runs/rfd4-run-01/predictions/confidence_design_0.json"
            ),
            self._mock_blob("pipeline_runs/rfd4-run-01/predictions/design_0.cif"),
        ]
        mock_bucket.blob.return_value = MagicMock()

        with (
            patch.object(viewer_app, "_get_authed_session", return_value=mock_authed),
            patch.object(viewer_app.storage_client, "bucket", return_value=mock_bucket),
        ):
            resp = self.client.post(
                "/api/analyze",
                json={"job_id": "rfd4-job-1", "model_type": "rfd4-proteina"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["model_type"], "rfd4_proteina")

    def test_trigger_analysis_bindcraft_accepted_pdb_discovery(self):
        mock_authed = MagicMock()
        pj_resp = MagicMock()
        pj_resp.status_code = 200
        pj_resp.raise_for_status.return_value = None
        pj_resp.json.return_value = {
            "name": "projects/test-project/locations/us-central1/pipelineJobs/bindcraft-job-1",
            "labels": {"model_type": "bindcraft"},
            "runtimeConfig": {
                "gcsOutputDirectory": "gs://allowed-foldrun-bucket/pipeline_runs/bindcraft-run-01"
            },
        }
        cr_resp = MagicMock()
        cr_resp.raise_for_status.return_value = None
        mock_authed.get.return_value = pj_resp
        mock_authed.post.return_value = cr_resp

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [
            self._mock_blob("pipeline_runs/bindcraft-run-01/final_design_stats.csv"),
            self._mock_blob(
                "pipeline_runs/bindcraft-run-01/Accepted/binder_l80_s1.pdb"
            ),
        ]
        mock_bucket.blob.return_value = MagicMock()

        with (
            patch.object(viewer_app, "_get_authed_session", return_value=mock_authed),
            patch.object(viewer_app.storage_client, "bucket", return_value=mock_bucket),
        ):
            resp = self.client.post(
                "/api/analyze",
                json={"job_id": "bindcraft-job-1", "model_type": "bindcraft"},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["model_type"], "bindcraft")
        self.assertEqual(payload["total_predictions"], 1)


if __name__ == "__main__":
    unittest.main()
