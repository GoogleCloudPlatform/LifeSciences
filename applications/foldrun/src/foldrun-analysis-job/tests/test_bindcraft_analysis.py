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

"""Tests for BindCraft analysis utilities: sample analysis and result consolidation."""

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

from foldrun_analysis import bindcraft_analyzer  # noqa: E402


class TestBindCraftAnalyzer:
    """Tests for BindCraft analysis functions."""

    def test_generate_gemini_expert_analysis_skipped_on_error(self):
        summary_data = {
            "protein_info": {},
            "summary": {"total_predictions": 1},
            "best_prediction": {
                "ranking_score": 0.88,
                "iptm": 0.86,
                "ptm": 0.89,
                "plddt_mean": 88.5,
                "interface_energy": -38.5,
                "contact_surface_area": 1420.0,
                "designed_sequence": "ACDEFGHIKLMNPQRSTVWY",
                "chain_info": [
                    {
                        "chain_id": "A",
                        "molecule_type": "protein",
                        "residue_count": 140,
                        "atom_count": 1100,
                    },
                    {
                        "chain_id": "B",
                        "molecule_type": "protein",
                        "residue_count": 40,
                        "atom_count": 320,
                    },
                ],
            },
            "top_predictions": [
                {
                    "sample_name": "design_seed1_0",
                    "ranking_score": 0.88,
                    "iptm": 0.86,
                    "plddt_mean": 88.5,
                    "interface_energy": -38.5,
                    "designed_sequence": "ACDEFGHIKLMNPQRSTVWY",
                }
            ],
        }
        with patch("google.genai.Client", side_effect=RuntimeError("No API key")):
            res = bindcraft_analyzer.generate_gemini_expert_analysis(summary_data)
            assert res["status"] == "skipped"
            assert "No API key" in res["reason"]

    def test_plot_ranking_comparison(self):
        top_predictions = [
            {"sample_name": "design_1", "ranking_score": 0.91, "iptm": 0.88},
            {"sample_name": "design_2", "ranking_score": 0.81, "iptm": 0.76},
        ]
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        bindcraft_analyzer.plt.subplots.return_value = (MagicMock(), MagicMock())

        with patch("google.cloud.storage.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            mock_client_cls.return_value = mock_client

            bindcraft_analyzer.plot_ranking_comparison(
                top_predictions, "gs://test-bucket/analysis/ranking.png"
            )
            assert mock_blob.upload_from_file.called

    def test_missing_metrics_never_fabricated_and_custom_output_uri(self):
        """BindCraft analyzer honors custom sample output_uri and never fabricates -35.0/1250.0/83.0."""
        import json

        uploaded: dict[str, dict] = {}

        def fake_upload(local_path, gcs_uri, content_type=None, expected_bucket=None):
            with open(local_path, encoding="utf-8") as f:
                uploaded[gcs_uri] = json.load(f)

        with (
            patch(
                "foldrun_analysis.bindcraft_analyzer.download_json_from_gcs",
                return_value={"Sequence": "ACDE"},
            ),
            patch(
                "foldrun_analysis.bindcraft_analyzer.download_text_from_gcs",
                return_value="",
            ),
            patch(
                "foldrun_analysis.bindcraft_analyzer.upload_to_gcs",
                side_effect=fake_upload,
            ),
        ):
            bindcraft_analyzer.analyze_sample(
                0,
                {
                    "sample_name": "Design_1",
                    "cif_uri": "gs://storage-bucket/runs/bc/Accepted/Design_1.cif",
                    "aggregated_uri": "gs://storage-bucket/runs/bc/Accepted/Design_1.json",
                    "output_uri": "gs://storage-bucket/runs/bc/analysis/Design_1_analysis.json",
                },
                "storage-bucket",
                "gs://storage-bucket/runs/bc/analysis/",
            )

        res = uploaded["gs://storage-bucket/runs/bc/analysis/Design_1_analysis.json"]
        assert res["plddt_mean"] is None
        assert res["iptm"] is None
        assert res["interface_energy"] is None
        assert res["contact_surface_area"] is None
        assert res["ranking_score"] is None
        assert res["designed_sequence"] == "ACDE"
