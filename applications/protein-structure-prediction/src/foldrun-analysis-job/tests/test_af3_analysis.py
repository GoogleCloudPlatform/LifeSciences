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

"""Tests for AlphaFold 3 parallel Cloud Run Job analysis (af3_analyzer.py)."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub heavy imports BEFORE loading the module
_stubs = {
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
}
for name, stub in _stubs.items():
    sys.modules.setdefault(name, stub)

from foldrun_analysis import af3_analyzer  # noqa: E402


class TestAF3Analysis:
    """Tests for AlphaFold 3 parallel Cloud Run Job post-processing functions."""

    SAMPLE_AF3_CIF = """\
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.pdbx_PDB_model_num
ATOM 1 N N . MET A 1 1 ? 1.0 2.0 3.0 1.00 92.0 1 MET A 1
ATOM 2 C CA . MET A 1 1 ? 2.0 3.0 4.0 1.00 94.0 1 MET A 1
ATOM 3 N N . GLY A 1 2 ? 3.0 4.0 5.0 1.00 88.0 2 GLY A 1
"""

    def test_extract_residue_plddts_from_cif(self):
        """Extracts per-residue mean pLDDT values from AF3 mmCIF lines."""
        scores = af3_analyzer._extract_residue_plddts_from_cif(self.SAMPLE_AF3_CIF)
        assert len(scores) == 2
        assert scores[0] == pytest.approx(93.0)
        assert scores[1] == pytest.approx(88.0)

    def test_compute_af3_ranking_score_explicit_and_derived(self):
        """Computes AF3 ranking score from explicit value or pTM/ipTM formula."""
        assert (
            af3_analyzer._compute_af3_ranking_score({"ranking_score": 0.91234})
            == 0.9123
        )
        # Derived: 0.8 * 0.90 + 0.2 * 0.85 + 0.5 * 0.10 - 100 * 0 = 0.72 + 0.17 + 0.05 = 0.94
        derived = af3_analyzer._compute_af3_ranking_score(
            {"ptm": 0.85, "iptm": 0.90, "fraction_disordered": 0.10, "has_clash": 0.0}
        )
        assert derived == pytest.approx(0.94)

    def test_run_task_writes_prediction_analysis_and_triggers_consolidate_on_last_task(
        self,
    ):
        """Task N-1 writes prediction_N_analysis.json, waits for siblings, and consolidates."""
        task_config = {
            "job_id": "af3_complex_25",
            "pipeline_job_id": "alphafold3-inference-pipeline-20261010190031",
            "pipeline_root_prefix": "pipeline_runs/20261010_190031",
            "mode_label": "Full MSA + Templates",
            "predictions": [
                {
                    "index": 0,
                    "sample_name": "seed_1_sample_0 (Full MSA + Templates)",
                    "cif_uri": "gs://test-bucket/af3_predictions/af3_complex_25/af3_raw/seed-1_sample-0/model.cif",
                    "summary_uri": "gs://test-bucket/af3_predictions/af3_complex_25/af3_raw/seed-1_sample-0/summary_confidences.json",
                    "confidences_uri": "gs://test-bucket/af3_predictions/af3_complex_25/af3_raw/seed-1_sample-0/confidences.json",
                    "output_uri": "gs://test-bucket/af3_predictions/af3_complex_25/analysis/prediction_0_analysis.json",
                }
            ],
        }
        uploaded_files = {}

        def fake_upload(local_path, gcs_uri):
            import os

            if os.path.exists(local_path):
                with open(local_path) as f:
                    uploaded_files[gcs_uri] = f.read()
            else:
                uploaded_files[gcs_uri] = "<mock_plot_bytes>"

        with (
            patch.object(
                af3_analyzer,
                "download_json_from_gcs",
                side_effect=[
                    {
                        "ranking_score": 0.92,
                        "ptm": 0.89,
                        "iptm": 0.93,
                        "has_clash": 0.0,
                    },
                    {"pae": [[0.5, 2.5], [2.5, 0.5]]},
                ],
            ),
            patch.object(
                af3_analyzer, "download_text_from_gcs", return_value=self.SAMPLE_AF3_CIF
            ),
            patch.object(af3_analyzer, "plot_plddt_distribution"),
            patch.object(af3_analyzer, "plot_error_matrix"),
            patch.object(af3_analyzer, "upload_to_gcs", side_effect=fake_upload),
            patch.object(
                af3_analyzer, "wait_for_sibling_tasks", return_value=True
            ) as mock_wait,
            patch.object(af3_analyzer, "consolidate_results") as mock_consolidate,
        ):
            af3_analyzer.run_task(
                task_index=0,
                task_count=1,
                task_config=task_config,
                bucket_name="test-bucket",
                analysis_path="gs://test-bucket/af3_predictions/af3_complex_25/analysis/",
            )

        out_uri = "gs://test-bucket/af3_predictions/af3_complex_25/analysis/prediction_0_analysis.json"
        assert out_uri in uploaded_files
        mock_wait.assert_called_once()
        mock_consolidate.assert_called_once()
