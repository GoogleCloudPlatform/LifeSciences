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
                "model": "gemini-4-argon",
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


class TestScientificIntegrityAndBfactors(unittest.TestCase):
    """Tests for strict RFC 8259 NaN/Inf sanitization, .zst decompression, and CIF B-factors."""

    def setUp(self):
        self.client = viewer_app.app.test_client()

    def test_sanitize_data_converts_nonfinite_floats_to_none(self):
        payload = {
            "plddt_mean": float("nan"),
            "std": float("nan"),
            "iptm": float("inf"),
            "delta_g": float("-inf"),
            "valid": 91.5,
            "nested": [1.0, float("nan"), float("inf")],
        }
        cleaned = viewer_app._sanitize_json_floats(payload)
        self.assertIsNone(cleaned["plddt_mean"])
        self.assertIsNone(cleaned["std"])
        self.assertIsNone(cleaned["iptm"])
        self.assertIsNone(cleaned["delta_g"])
        self.assertEqual(cleaned["valid"], 91.5)
        self.assertEqual(cleaned["nested"], [1.0, None, None])

    def test_load_gcs_file_json_maps_nan_constants_to_none(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_as_bytes.return_value = (
            b'{"plddt_mean": NaN, "iptm": Infinity, "neg": -Infinity, "ptm": 0.88}'
        )
        mock_bucket.blob.return_value = mock_blob
        with patch.object(
            viewer_app.storage_client, "bucket", return_value=mock_bucket
        ):
            res = viewer_app.load_gcs_file(
                "gs://allowed-foldrun-bucket/pipeline_runs/run1/analysis/summary.json",
                as_json=True,
                allowed_extensions=(".json",),
            )
        self.assertIsNone(res["plddt_mean"])
        self.assertIsNone(res["iptm"])
        self.assertIsNone(res["neg"])
        self.assertEqual(res["ptm"], 0.88)

    def test_cif_endpoint_sanitizes_bfactors_to_zero_and_preserves_nan_ligand(self):
        cif_text = (
            "data_design\n"
            "loop_\n"
            "_atom_site.group_PDB\n"
            "_atom_site.id\n"
            "_atom_site.type_symbol\n"
            "_atom_site.label_atom_id\n"
            "_atom_site.label_alt_id\n"
            "_atom_site.label_comp_id\n"
            "_atom_site.label_asym_id\n"
            "_atom_site.label_entity_id\n"
            "_atom_site.label_seq_id\n"
            "_atom_site.pdbx_PDB_ins_code\n"
            "_atom_site.Cartn_x\n"
            "_atom_site.Cartn_y\n"
            "_atom_site.Cartn_z\n"
            "_atom_site.occupancy\n"
            "_atom_site.B_iso_or_equiv\n"
            "ATOM 1 C CA . ALA A 1 1 ? 1.000 2.000 3.000 1.00 NaN\n"
            "HETATM 2 C C1 . NAN B 2 . ? 4.000 5.000 6.000 1.00 Inf\n"
        )
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_as_bytes.return_value = cif_text.encode("utf-8")
        mock_bucket.blob.return_value = mock_blob
        with patch.object(
            viewer_app.storage_client, "bucket", return_value=mock_bucket
        ):
            resp = self.client.get(
                "/api/cif?uri=gs://allowed-foldrun-bucket/pipeline_runs/run1/design_0.cif"
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn("NaN", body)
        self.assertNotIn("Inf", body)
        self.assertIn("ATOM 1 C CA . ALA A 1 1 ? 1.000 2.000 3.000 1.00 0.00", body)
        self.assertIn("HETATM 2 C C1 . NAN B 2 . ? 4.000 5.000 6.000 1.00 0.00", body)

    def test_cif_endpoint_accepts_cif_zst(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_as_bytes.return_value = b"compressed-zst-bytes"
        mock_bucket.blob.return_value = mock_blob
        decompressed = (
            b"ATOM 1 N N . MET A 1 1 ? 1.0 2.0 3.0 1.00 94.50 ? 1 MET A N 1\n"
        )
        with (
            patch.object(viewer_app.storage_client, "bucket", return_value=mock_bucket),
            patch.object(
                viewer_app, "_decompress_zst_bytes", return_value=decompressed
            ),
        ):
            resp = self.client.get(
                "/api/cif?uri=gs://allowed-foldrun-bucket/pipeline_runs/af3_run/seed-1_sample-0/model.cif.zst"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("94.50", resp.get_data(as_text=True))


class TestCustomSlugResolutionAndAnalyzeRouting(unittest.TestCase):
    """Tests for custom pipeline_runs/<slug>/ resolution, model_type normalization, and taskCount cap."""

    def setUp(self):
        self.client = viewer_app.app.test_client()

    def test_normalize_model_type_aliases(self):
        self.assertEqual(viewer_app._normalize_model_type("af2"), "alphafold2")
        self.assertEqual(viewer_app._normalize_model_type("alphafold2"), "alphafold2")
        self.assertEqual(viewer_app._normalize_model_type("af3"), "alphafold3")
        self.assertEqual(viewer_app._normalize_model_type("alphafold3"), "alphafold3")
        self.assertEqual(viewer_app._normalize_model_type("of3"), "openfold3")
        self.assertEqual(viewer_app._normalize_model_type("boltz"), "boltz2")

    def test_resolve_summary_and_uri_via_runtime_config_custom_slug(self):
        mock_authed = MagicMock()
        pj_resp = MagicMock()
        pj_resp.ok = True
        pj_resp.json.return_value = {
            "runtimeConfig": {
                "gcsOutputDirectory": "gs://allowed-foldrun-bucket/pipeline_runs/custom-boltz2-slug"
            }
        }
        mock_authed.get.return_value = pj_resp

        mock_bucket = MagicMock()
        af3_blob = MagicMock()
        af3_blob.exists.return_value = False
        summary_blob = MagicMock()
        summary_blob.download_as_bytes.return_value = (
            b'{"job_id": "custom-job-42", "model_type": "boltz2"}'
        )

        def _blob_side_effect(path):
            if path.startswith("af3_predictions/"):
                return af3_blob
            return summary_blob

        mock_bucket.blob.side_effect = _blob_side_effect

        with (
            patch.object(viewer_app, "_get_authed_session", return_value=mock_authed),
            patch.object(viewer_app.storage_client, "bucket", return_value=mock_bucket),
        ):
            summary, uri = viewer_app._resolve_summary_and_uri(job_id="custom-job-42")

        self.assertEqual(
            uri,
            "gs://allowed-foldrun-bucket/pipeline_runs/custom-boltz2-slug/analysis/summary.json",
        )
        self.assertEqual(summary["job_id"], "custom-job-42")

    def test_trigger_analysis_rejects_unknown_model_type(self):
        resp = self.client.post(
            "/api/analyze",
            json={"job_id": "valid-job-123", "model_type": "nonexistent_model"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported model_type", resp.get_json()["error"])

    def test_trigger_analysis_af3_discovers_zst_samples_and_caps_task_count(self):
        mock_authed = MagicMock()
        pj_resp = MagicMock()
        pj_resp.status_code = 200
        pj_resp.raise_for_status.return_value = None
        pj_resp.json.return_value = {
            "name": "projects/test-project/locations/us-central1/pipelineJobs/af3-job-1",
            "labels": {"model_type": "af3", "managed_by": "foldrun"},
            "runtimeConfig": {
                "gcsOutputDirectory": "gs://allowed-foldrun-bucket/pipeline_runs/af3-run-01"
            },
        }
        cr_resp = MagicMock()
        cr_resp.raise_for_status.return_value = None
        mock_authed.get.return_value = pj_resp
        mock_authed.post.return_value = cr_resp

        blob_list = []
        for idx in range(30):
            sample_dir = f"pipeline_runs/af3-run-01/seed-1_sample-{idx}"
            for suffix in (
                "model.cif.zst",
                "summary_confidences.json.zst",
                "confidences.json.zst",
            ):
                b = MagicMock()
                b.name = f"{sample_dir}/{suffix}"
                blob_list.append(b)

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.side_effect = [
            [],  # af3_predictions/ check
            blob_list,  # pipeline_runs/af3-run-01 check
        ]
        mock_bucket.blob.return_value = MagicMock()

        with (
            patch.object(viewer_app, "_get_authed_session", return_value=mock_authed),
            patch.object(viewer_app.storage_client, "bucket", return_value=mock_bucket),
        ):
            resp = self.client.post(
                "/api/analyze", json={"job_id": "af3-job-1", "model_type": "af3"}
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["model_type"], "alphafold3")
        self.assertEqual(payload["total_predictions"], 30)
        self.assertEqual(payload["task_count"], 25)
        cr_call_json = mock_authed.post.call_args.kwargs["json"]
        self.assertEqual(cr_call_json["overrides"]["taskCount"], 25)


if __name__ == "__main__":
    unittest.main()
