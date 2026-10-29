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

"""Tests for RFD4-Proteina analysis utilities: sample analysis and result consolidation."""

import sys
from unittest.mock import MagicMock, patch

# Stub heavy imports BEFORE loading the module
_stubs = {
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
    "matplotlib": MagicMock(),
    "matplotlib.pyplot": MagicMock(),
    "seaborn": MagicMock(),
}
for name, stub in _stubs.items():
    sys.modules.setdefault(name, stub)

from foldrun_analysis import rfd4_proteina_analyzer  # noqa: E402


class TestRFD4ProteinaAnalyzer:
    """Tests for RFD4-Proteina analysis functions."""

    def test_generate_gemini_expert_analysis_skipped_on_error(self):
        summary_data = {
            "protein_info": {"design_mode": "binder_design"},
            "summary": {"total_predictions": 1},
            "best_prediction": {
                "ranking_score": 0.86,
                "sc_score": 0.38,
                "clash_score": 0.15,
                "iptm": 0.84,
                "ptm": 0.87,
                "plddt_mean": 88.0,
                "designed_sequence": "ACDEFGHIKLMNPQRSTVWY",
                "chain_info": [
                    {
                        "chain_id": "A",
                        "molecule_type": "protein",
                        "residue_count": 120,
                        "atom_count": 960,
                    },
                    {
                        "chain_id": "B",
                        "molecule_type": "protein",
                        "residue_count": 30,
                        "atom_count": 240,
                    },
                ],
            },
            "top_predictions": [
                {
                    "sample_name": "design_seed1_0",
                    "ranking_score": 0.86,
                    "sc_score": 0.38,
                    "iptm": 0.84,
                    "plddt_mean": 88.0,
                    "designed_sequence": "ACDEFGHIKLMNPQRSTVWY",
                }
            ],
        }
        with patch("google.genai.Client", side_effect=RuntimeError("No API key")):
            res = rfd4_proteina_analyzer.generate_gemini_expert_analysis(summary_data)
            assert res["status"] == "skipped"
            assert "No API key" in res["reason"]

    def test_plot_ranking_comparison(self):
        top_predictions = [
            {"sample_name": "design_1", "ranking_score": 0.89, "iptm": 0.86},
            {"sample_name": "design_2", "ranking_score": 0.78, "iptm": 0.72},
        ]
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        rfd4_proteina_analyzer.plt.subplots.return_value = (MagicMock(), MagicMock())

        with patch("google.cloud.storage.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            mock_client_cls.return_value = mock_client

            rfd4_proteina_analyzer.plot_ranking_comparison(
                top_predictions, "gs://test-bucket/analysis/ranking.png"
            )
            assert mock_blob.upload_from_file.called

    def test_fast_mode_none_metrics_and_storage_bucket_prefix(self):
        """RFD4 fast mode (~metrics) keeps None scores, sets fast_no_self_consistency, and never corrupts gs://storage-bucket."""
        import json

        uploaded: dict[str, dict] = {}

        def fake_upload(local_path, gcs_uri, content_type=None, expected_bucket=None):
            with open(local_path, encoding="utf-8") as f:
                uploaded[gcs_uri] = json.load(f)

        nan_cif = (
            "ATOM 1 N N . MET A 1 1 ? 1.0 2.0 3.0 1.0 nan ? 1 MET A N 1\n"
            "ATOM 2 CA C . MET A 1 1 ? 2.0 3.0 4.0 1.0 nan ? 1 MET A CA 1\n"
        )
        fast_conf = {
            "metrics_computed": False,
            "metrics_mode": "fast_no_self_consistency",
            "designed_sequence": "MKV",
            "design_mode": "binder_design",
        }

        with (
            patch(
                "foldrun_analysis.rfd4_proteina_analyzer.download_json_from_gcs",
                return_value=fast_conf,
            ),
            patch(
                "foldrun_analysis.rfd4_proteina_analyzer.download_text_from_gcs",
                return_value=nan_cif,
            ),
            patch(
                "foldrun_analysis.rfd4_proteina_analyzer.upload_to_gcs",
                side_effect=fake_upload,
            ),
        ):
            rfd4_proteina_analyzer.analyze_sample(
                0,
                {
                    "sample_name": "design_0",
                    "cif_uri": "gs://storage-bucket/runs/rfd4/design_0.cif",
                    "aggregated_uri": "gs://storage-bucket/runs/rfd4/design_0_conf.json",
                },
                "storage-bucket",
                "gs://storage-bucket/runs/rfd4/analysis/",
            )

        out_uri = "gs://storage-bucket/runs/rfd4/analysis/prediction_0_analysis.json"
        assert out_uri in uploaded
        sample_doc = uploaded[out_uri]
        assert sample_doc["metrics_computed"] is False
        assert sample_doc["metrics_mode"] == "fast_no_self_consistency"
        assert sample_doc["plddt_mean"] is None
        assert sample_doc["iptm"] is None
        assert sample_doc["sc_score"] is None
        assert sample_doc["ranking_score"] is None
        assert sample_doc["quality_assessment"] == "uncomputed"

        # Consolidate and verify Gemini prompt handles None without TypeError
        mock_blob = MagicMock()
        mock_blob.name = "runs/rfd4/analysis/prediction_0_analysis.json"
        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [mock_blob]
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        captured_prompts: list[str] = []

        def fake_generate_content(*, model, contents, config):
            captured_prompts.append(contents)
            resp = MagicMock()
            resp.text = "Fast mode orthogonal validation recommended."
            return resp

        fake_genai_client = MagicMock()
        fake_genai_client.models.generate_content.side_effect = fake_generate_content

        with (
            patch("google.cloud.storage.Client", return_value=mock_client),
            patch(
                "foldrun_analysis.rfd4_proteina_analyzer.download_json_from_gcs",
                return_value=dict(sample_doc),
            ),
            patch(
                "foldrun_analysis.rfd4_proteina_analyzer.download_text_from_gcs",
                return_value="contig: 50-50\n",
            ),
            patch(
                "foldrun_analysis.rfd4_proteina_analyzer.get_job_metadata",
                return_value={"labels": {}, "parameters": {}},
            ),
            patch(
                "foldrun_analysis.rfd4_proteina_analyzer.upload_to_gcs",
                side_effect=fake_upload,
            ),
            patch("google.genai.Client", return_value=fake_genai_client),
        ):
            rfd4_proteina_analyzer.consolidate_analyses(
                {
                    "job_id": "rfd4-fast-1",
                    "query_yaml_uri": "gs://storage-bucket/runs/rfd4/query.yaml",
                    "predictions": [{"sample_name": "design_0"}],
                },
                "storage-bucket",
                "gs://storage-bucket/runs/rfd4/analysis/",
            )

        summary_uri = "gs://storage-bucket/runs/rfd4/analysis/summary.json"
        assert summary_uri in uploaded
        summary_doc = uploaded[summary_uri]
        assert summary_doc["summary"]["metrics_mode"] == "fast_no_self_consistency"
        assert (
            summary_doc["summary"]["protein_info"]["input_query_yaml"]
            == "contig: 50-50\n"
        )
        assert summary_doc["summary"]["quality_metrics"]["best_ranking_score"] is None
        assert summary_doc["summary"]["statistics"]["total_predictions"] == 1
        assert (
            captured_prompts
            and "Not computed (fast mode ~metrics)" in captured_prompts[0]
        )
