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

"""Tests for BoltzGen analysis utilities: sample analysis and result consolidation."""

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

from foldrun_analysis import boltzgen_analyzer  # noqa: E402


class TestBoltzGenAnalyzer:
    """Tests for BoltzGen analysis functions."""

    def test_generate_gemini_expert_analysis_skipped_on_error(self):
        summary_data = {
            "protein_info": {"protocol": "protein-anything"},
            "summary": {"total_predictions": 1},
            "best_prediction": {
                "ranking_score": 0.85,
                "sc_score": 0.35,
                "clash_score": 0.2,
                "iptm": 0.82,
                "ptm": 0.88,
                "plddt_mean": 87.5,
                "designed_sequence": "ACDEFGHIKLMNPQRSTVWY",
                "chain_info": [
                    {
                        "chain_id": "A",
                        "molecule_type": "protein",
                        "residue_count": 100,
                        "atom_count": 800,
                    },
                    {
                        "chain_id": "B",
                        "molecule_type": "protein",
                        "residue_count": 20,
                        "atom_count": 160,
                    },
                ],
            },
            "top_predictions": [
                {
                    "sample_name": "design_seed1_0",
                    "ranking_score": 0.85,
                    "sc_score": 0.35,
                    "iptm": 0.82,
                    "plddt_mean": 87.5,
                    "designed_sequence": "ACDEFGHIKLMNPQRSTVWY",
                }
            ],
        }
        with patch("google.genai.Client", side_effect=RuntimeError("No API key")):
            res = boltzgen_analyzer.generate_gemini_expert_analysis(summary_data)
            assert res["status"] == "skipped"
            assert "No API key" in res["reason"]

    def test_plot_ranking_comparison(self):
        top_predictions = [
            {"sample_name": "design_1", "ranking_score": 0.88, "iptm": 0.85},
            {"sample_name": "design_2", "ranking_score": 0.75, "iptm": 0.70},
        ]
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        boltzgen_analyzer.plt.subplots.return_value = (MagicMock(), MagicMock())

        with patch("google.cloud.storage.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            mock_client_cls.return_value = mock_client

            boltzgen_analyzer.plot_ranking_comparison(
                top_predictions, "gs://test-bucket/analysis/ranking.png"
            )
            assert mock_blob.upload_from_file.called

    def test_missing_metrics_never_fabricated_and_storage_bucket_safe(self):
        """Missing confidence metrics stay None (never fabricated 80.0/0.8/0.85)."""
        import json

        uploaded: dict[str, dict] = {}

        def fake_upload(local_path, gcs_uri, content_type=None, expected_bucket=None):
            with open(local_path, encoding="utf-8") as f:
                uploaded[gcs_uri] = json.load(f)

        with (
            patch(
                "foldrun_analysis.boltzgen_analyzer.download_json_from_gcs",
                return_value={"protocol": "nanobody-anything"},
            ),
            patch(
                "foldrun_analysis.boltzgen_analyzer.download_text_from_gcs",
                return_value="",
            ),
            patch(
                "foldrun_analysis.boltzgen_analyzer.upload_to_gcs",
                side_effect=fake_upload,
            ),
        ):
            boltzgen_analyzer.analyze_sample(
                0,
                {
                    "sample_name": "design_0",
                    "cif_uri": "gs://storage-bucket/runs/bg/design_0.cif",
                    "aggregated_uri": "gs://storage-bucket/runs/bg/design_0.json",
                },
                "storage-bucket",
                "gs://storage-bucket/runs/bg/analysis/",
            )

        res = uploaded[
            "gs://storage-bucket/runs/bg/analysis/prediction_0_analysis.json"
        ]
        assert res["plddt_mean"] is None
        assert res["iptm"] is None
        assert res["sc_score"] is None
        assert res["ranking_score"] is None
        assert res["quality_assessment"] == "uncomputed"
