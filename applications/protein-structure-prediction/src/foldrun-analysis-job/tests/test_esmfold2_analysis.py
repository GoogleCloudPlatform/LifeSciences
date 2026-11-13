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

"""Unit tests for ESMFold2StructureAnalyzer in foldrun-analysis-job."""

from __future__ import annotations

import io
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_stubs = {
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
}
for name, stub in _stubs.items():
    sys.modules.setdefault(name, stub)

from foldrun_analysis import shared_utils  # noqa: E402
from foldrun_analysis.esmfold2_analyzer import ESMFold2StructureAnalyzer  # noqa: E402

SAMPLE_CIF = """\
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
ATOM 1 N N MET A 1 1.0 2.0 3.0 1.0 88.0 1 MET A N
ATOM 2 CA C MET A 1 2.0 3.0 4.0 1.0 90.0 1 MET A CA
ATOM 3 N N GLY A 2 3.0 4.0 5.0 1.0 92.0 2 GLY A N
"""


def _touch_plot_file(*args: Any, **kwargs: Any) -> None:
    for arg in args:
        if isinstance(arg, str) and arg.endswith(".png"):
            with open(arg, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")


class TestESMFold2StructureAnalyzer:
    """Tests for ESMFold2StructureAnalyzer per-sample analysis and PAE extraction."""

    def test_analyze_single_sample_extracts_confidences_and_pae(self):
        analyzer = ESMFold2StructureAnalyzer()
        pred = {
            "index": 0,
            "sample_name": "seed_1_sample_0",
            "seed": 1,
            "sample": 0,
            "cif_uri": "gs://test-bucket/pipeline_runs/r1/seed_1/ubq_seed_1_sample_0_model.cif",
            "confidences_uri": "gs://test-bucket/pipeline_runs/r1/seed_1/ubq_seed_1_sample_0_confidences.json",
            "pae_uri": "gs://test-bucket/pipeline_runs/r1/seed_1/ubq_seed_1_sample_0_pae.npz",
            "output_uri": "gs://test-bucket/pipeline_runs/r1/analysis/prediction_0_analysis.json",
        }

        conf_data = {
            "ptm": 0.89,
            "iptm": 0.0,
            "ranking_score": 0.89,
            "plddt_mean": 90.0,
            "chain_ids": ["A"],
        }
        pae_buf = io.BytesIO()
        np.savez_compressed(
            pae_buf, pae=np.array([[0.5, 2.5], [2.5, 0.5]], dtype=np.float32)
        )
        pae_bytes = pae_buf.getvalue()

        with (
            patch.object(
                shared_utils, "download_json_from_gcs", return_value=conf_data
            ),
            patch.object(
                shared_utils, "download_text_from_gcs", return_value=SAMPLE_CIF
            ),
            patch.object(
                shared_utils, "download_image_from_gcs", return_value=pae_bytes
            ),
            patch.object(
                shared_utils, "plot_plddt_distribution", side_effect=_touch_plot_file
            ),
            patch.object(
                shared_utils, "plot_error_matrix", side_effect=_touch_plot_file
            ),
            patch.object(shared_utils, "upload_to_gcs"),
        ):
            result = analyzer.analyze_sample(0, pred)

        assert result["ptm"] == pytest.approx(0.89)
        assert result["ranking_score"] == pytest.approx(0.89)
        assert result["plddt_mean"] == pytest.approx(90.0)
        assert result["pae_mean"] == pytest.approx(1.5)
        assert result["pae_max"] == pytest.approx(2.5)
        assert result["has_pae"] is True
        assert "plddt_plot" in result["plots"]
        assert "pae_plot" in result["plots"]
