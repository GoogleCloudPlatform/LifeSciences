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

    def test_index_and_job_viewer_reject_invalid_job_id_redirect(self):
        """GET /?job_id=... and /job/<job_id> must reject invalid job_ids and allow valid local redirects."""
        valid_id = "alphafold-inference-pipeline-20260215153755"
        for url in (f"/?job_id={valid_id}", f"/job/{valid_id}"):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers["Location"], f"/combined?job_id={valid_id}")

        malicious_ids = [
            "https://evil.example.com",
            "//evil.example.com",
            "../otherEndpoint",
            "job 123",
            "job123?redirect=https://evil.example.com",
        ]
        for bad_id in malicious_ids:
            resp = self.client.get(f"/?job_id={bad_id}")
            self.assertEqual(
                resp.status_code,
                400,
                f"Expected 400 for GET /?job_id={bad_id}, got {resp.status_code}",
            )
            resp_combined = self.client.get(f"/combined?job_id={bad_id}")
            self.assertEqual(
                resp_combined.status_code,
                400,
                f"Expected 400 for GET /combined?job_id={bad_id}, got {resp_combined.status_code}",
            )


class TestGenerateSvgsDefusedXml(unittest.TestCase):
    """Verify scripts/generate-svgs.py uses defusedxml.ElementTree instead of xml.etree.ElementTree."""

    def test_generate_svgs_uses_defusedxml(self):
        script_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "..", "scripts", "generate-svgs.py"
            )
        )
        with open(script_path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("import defusedxml.ElementTree as ET", source)
        self.assertNotIn("xml.etree.ElementTree", source)


class TestArtifactDownloadEndpoints(unittest.TestCase):
    """Tests for isolated artifact download endpoints (/api/download/bundle and /api/download/file)."""

    def setUp(self):
        self.client = viewer_app.app.test_client()
        self.sample_summary = {
            "job_id": "alphafold-inference-pipeline-20260215153755",
            "model_type": "alphafold2",
            "analyzed_at": "2026-02-15T15:40:00Z",
            "artifacts_bundle_uri": "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/analysis/artifacts_bundle.zip",
            "summary": {
                "quality_metrics": {
                    "quality_assessment": "very_high_confidence",
                    "best_model": "model_1_pred_0",
                    "best_model_plddt": 93.1,
                }
            },
            "all_predictions_summary": [
                {
                    "rank": 1,
                    "model_name": "model_1_pred_0",
                    "uri": "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/predict/model_1_pred_0/raw_prediction.pkl",
                    "plots": {
                        "plddt_plot": "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/analysis/plddt_plot_0.png",
                    },
                }
            ],
            "expert_analysis": {
                "status": "success",
                "model": "gemini-3.1-pro-preview",
                "analysis": "High confidence prediction.",
            },
        }

    def test_download_endpoints_reject_malicious_inputs(self):
        """/api/download/bundle and /api/download/file must reject foreign buckets, path traversal, and bad job_ids."""
        for endpoint in ("/api/download/bundle", "/api/download/file"):
            resp_missing = self.client.get(endpoint)
            self.assertEqual(resp_missing.status_code, 400)

            for bad_uri in (
                "gs://foreign-bucket/pipeline_runs/run1/analysis/summary.json",
                "gs://allowed-foldrun-bucket/pipeline_runs/../../secret.json",
                "gs://allowed-foldrun-bucket/etc/summary.json",
            ):
                resp = self.client.get(f"{endpoint}?summary_uri={bad_uri}")
                self.assertEqual(resp.status_code, 400)

            for bad_id in ("../traversal", "job 123", "job?foo=bar"):
                resp = self.client.get(f"{endpoint}?job_id={bad_id}")
                self.assertEqual(resp.status_code, 400)

    def test_download_bundle_redirects_to_signed_url_when_available(self):
        """/api/download/bundle redirects (302) to https://storage.googleapis.com/... when V4 signing succeeds."""
        summary_uri = "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/analysis/summary.json"
        signed = "https://storage.googleapis.com/allowed-foldrun-bucket/pipeline_runs/20260215_153755/analysis/artifacts_bundle.zip?X-Goog-Expires=3600"
        with (
            patch.object(
                viewer_app,
                "_resolve_summary_and_uri",
                return_value=(self.sample_summary, summary_uri),
            ),
            patch.object(
                viewer_app,
                "_ensure_bundle_in_gcs",
                return_value=self.sample_summary["artifacts_bundle_uri"],
            ),
            patch.object(
                viewer_app, "_generate_signed_url_or_none", return_value=signed
            ),
        ):
            resp = self.client.get(f"/api/download/bundle?summary_uri={summary_uri}")
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers["Location"], signed)

    def test_download_file_report_returns_markdown_attachment(self):
        """/api/download/file?type=report returns standalone Markdown attachment."""
        summary_uri = "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/analysis/summary.json"
        with patch.object(
            viewer_app,
            "_resolve_summary_and_uri",
            return_value=(self.sample_summary, summary_uri),
        ):
            resp = self.client.get(
                f"/api/download/file?summary_uri={summary_uri}&type=report"
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn("attachment;", resp.headers.get("Content-Disposition", ""))
            self.assertIn(
                "alphafold-inference-pipeline-20260215153755_expert_analysis.md",
                resp.headers.get("Content-Disposition", ""),
            )
            body = resp.get_data(as_text=True)
            self.assertIn("# FoldRun Structure & Analysis Report", body)
            self.assertIn("High confidence prediction.", body)

    def test_download_file_structure_streams_when_local_adc_cannot_sign(self):
        """/api/download/file?type=structure&rank=1 streams PDB with attachment header when V4 signing returns None."""
        summary_uri = "gs://allowed-foldrun-bucket/pipeline_runs/20260215_153755/analysis/summary.json"
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_bytes.return_value = b"HEADER MOCK PDB\nEND\n"
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        with (
            patch.object(
                viewer_app,
                "_resolve_summary_and_uri",
                return_value=(self.sample_summary, summary_uri),
            ),
            patch.object(viewer_app, "_generate_signed_url_or_none", return_value=None),
            patch.object(viewer_app.storage_client, "bucket", return_value=mock_bucket),
        ):
            resp = self.client.get(
                f"/api/download/file?summary_uri={summary_uri}&type=structure&rank=1"
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn("attachment;", resp.headers.get("Content-Disposition", ""))
            self.assertIn(
                "_rank_01_model_1_pred_0.pdb",
                resp.headers.get("Content-Disposition", ""),
            )
            self.assertEqual(resp.get_data(), b"HEADER MOCK PDB\nEND\n")


if __name__ == "__main__":
    unittest.main()
