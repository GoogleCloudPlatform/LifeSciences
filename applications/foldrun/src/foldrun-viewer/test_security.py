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

"""Security unit tests for FoldRun Viewer (Findings 4.44 and 4.45)."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROJECT_ID", "test-project")
os.environ.setdefault("BUCKET_NAME", "allowed-foldrun-bucket")
os.environ.setdefault("REGION", "us-central1")

with (
    patch("google.auth.default", return_value=(MagicMock(), "test-project")),
    patch("google.cloud.storage.Client", return_value=MagicMock()),
):
    sys.path.insert(0, os.path.dirname(__file__))
    import app as viewer_app


class TestFinding444GcsUriValidation(unittest.TestCase):
    """Tests for Finding 4.44: Unrestricted Cloud Storage Object Read via Arbitrary GCS URIs."""

    def setUp(self):
        self.client = viewer_app.app.test_client()

    def test_parse_gcs_uri_rejects_foreign_bucket(self):
        """parse_gcs_uri must reject buckets that do not match BUCKET_NAME."""
        with self.assertRaises(ValueError):
            viewer_app.parse_gcs_uri(
                "gs://attacker-bucket/pipeline_runs/20260215_153755/ranked_0.pdb"
            )

    def test_parse_gcs_uri_rejects_path_traversal(self):
        """parse_gcs_uri must reject path traversal sequences ('..')."""
        with self.assertRaises(ValueError):
            viewer_app.parse_gcs_uri(
                "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/../../secrets.pdb"
            )

    def test_parse_gcs_uri_rejects_disallowed_prefix(self):
        """parse_gcs_uri must reject paths outside allowed prefixes."""
        with self.assertRaises(ValueError):
            viewer_app.parse_gcs_uri("gs://allowed-foldrun-bucket/etc/passwd.pdb")

    def test_parse_gcs_uri_rejects_disallowed_extension(self):
        """parse_gcs_uri must reject disallowed file extensions when specified."""
        with self.assertRaises(ValueError):
            viewer_app.parse_gcs_uri(
                "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/secret.env",
                allowed_extensions=(".pdb",),
            )

    def test_parse_gcs_uri_accepts_valid_uri(self):
        """parse_gcs_uri must accept valid bucket, prefix, and extension."""
        bucket, path = viewer_app.parse_gcs_uri(
            "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/ranked_0.pdb",
            allowed_extensions=(".pdb",),
        )
        self.assertEqual(bucket, "allowed-foldrun-bucket")
        self.assertEqual(path, "pipeline_runs/20260215_153755/ranked_0.pdb")

    def test_viewer_endpoints_reject_foreign_bucket_and_traversal(self):
        """Viewer endpoints (/api/pdb, /api/cif, /api/analysis, /api/image) must reject malicious URIs."""
        malicious_uris = [
            "gs://other-bucket/pipeline_runs/run1/model.pdb",
            "gs://allowed-foldrun-bucket/pipeline_runs/../../secret.pdb",
            "gs://allowed-foldrun-bucket/arbitrary_dir/model.pdb",
        ]
        for uri in malicious_uris:
            for endpoint, param in [
                ("/api/pdb", "uri"),
                ("/api/cif", "uri"),
                ("/api/analysis", "summary_uri"),
                ("/api/image", "uri"),
            ]:
                resp = self.client.get(f"{endpoint}?{param}={uri}")
                self.assertEqual(
                    resp.status_code,
                    400,
                    f"Expected 400 for {endpoint} with URI {uri}, got {resp.status_code}",
                )


class TestFinding445JobIdValidation(unittest.TestCase):
    """Tests for Finding 4.45: Vertex AI API Path Traversal via Unvalidated job_id in /api/analyze."""

    def setUp(self):
        self.client = viewer_app.app.test_client()

    def test_trigger_analysis_rejects_path_traversal_job_id(self):
        """POST /api/analyze must reject job_ids containing traversal or invalid characters."""
        malicious_job_ids = [
            "../otherEndpoint",
            "job123/../../customJobs/456",
            "job123?alt=media",
            "job123#fragment",
            "job 123",
        ]
        for bad_id in malicious_job_ids:
            resp = self.client.post("/api/analyze", json={"job_id": bad_id})
            self.assertEqual(
                resp.status_code,
                400,
                f"Expected 400 for malicious job_id '{bad_id}', got {resp.status_code}",
            )


if __name__ == "__main__":
    unittest.main()
