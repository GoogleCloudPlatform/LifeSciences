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

"""Tests for Boltz-2 analysis utilities: parse_affinity, parse_cif_chains."""

import sys
import pytest
from unittest.mock import MagicMock

# Stub heavy imports BEFORE loading the module
_stubs = {
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
}
for name, stub in _stubs.items():
    sys.modules.setdefault(name, stub)

from foldrun_analysis import boltz2_analyzer  # noqa: E402
from foldrun_analysis import shared_utils  # noqa: E402


class TestParseAffinity:
    """Tests for parse_affinity() — converts raw Boltz-2 affinity JSON to metrics."""

    def test_very_strong_binder(self):
        """Very strong binder: IC50 < 10 nM, high probability."""
        raw = {
            "affinity_pred_value": -3.0,  # log10(0.001 μM) = log10(1 nM)
            "affinity_probability_binary": 0.95,
        }
        result = boltz2_analyzer.parse_affinity(raw)

        assert result["affinity_pred_value"] == -3.0
        assert abs(result["ic50_um"] - 0.001) < 1e-6
        assert abs(result["ic50_nm"] - 1.0) < 1e-3
        assert abs(result["pic50"] - 9.0) < 1e-6  # 6 - (-3) = 9
        assert result["binding_classification"] == "very_strong"
        assert result["binding_likelihood"] == "high"

    def test_strong_binder(self):
        """Strong binder: 10 ≤ IC50 < 100 nM."""
        raw = {
            "affinity_pred_value": -1.5,  # log10(0.0316 μM) ≈ 31.6 nM
            "affinity_probability_binary": 0.88,
        }
        result = boltz2_analyzer.parse_affinity(raw)

        assert result["binding_classification"] == "strong"
        assert result["binding_likelihood"] == "high"

    def test_moderate_binder(self):
        """Moderate binder: IC50 ~500 nM."""
        raw = {
            "affinity_pred_value": -0.3,  # log10(0.5 μM) ≈ -0.301
            "affinity_probability_binary": 0.65,
        }
        result = boltz2_analyzer.parse_affinity(raw)

        assert result["binding_classification"] == "moderate"
        assert result["binding_likelihood"] == "moderate"

    def test_weak_binder(self):
        """Weak binder: IC50 ~ 5 μM."""
        raw = {
            "affinity_pred_value": 0.699,  # log10(5 μM) ≈ 0.699
            "affinity_probability_binary": 0.35,
        }
        result = boltz2_analyzer.parse_affinity(raw)

        assert result["binding_classification"] == "weak"
        assert result["binding_likelihood"] == "low"

    def test_delta_g_formula(self):
        """ΔG = pIC50 × 1.364 kcal/mol (from official Boltz-2 docs)."""
        raw = {"affinity_pred_value": 0.0, "affinity_probability_binary": 0.5}
        result = boltz2_analyzer.parse_affinity(raw)
        assert abs(result["delta_g_kcal_mol"] - 8.184) < 0.01

    def test_missing_affinity_value(self):
        """Gracefully handles missing affinity_pred_value."""
        raw = {"affinity_probability_binary": 0.7}
        result = boltz2_analyzer.parse_affinity(raw)

        assert result["affinity_pred_value"] is None
        assert "ic50_nm" not in result
        assert "binding_classification" not in result

    def test_passthrough_of_ensemble_fields(self):
        """Ensemble sub-model predictions are passed through."""
        raw = {
            "affinity_pred_value": -1.0,
            "affinity_probability_binary": 0.8,
            "affinity_pred_value1": -0.9,
            "affinity_probability_binary1": 0.78,
            "affinity_pred_value2": -1.1,
            "affinity_probability_binary2": 0.82,
        }
        result = boltz2_analyzer.parse_affinity(raw)

        assert result["affinity_pred_value1"] == -0.9
        assert result["affinity_probability_binary2"] == 0.82


class TestParseCifChains:
    """Tests for updated parse_cif_chains() that returns (chain_info, plddt_scores)."""

    # Minimal synthetic CIF with B_iso_or_equiv column
    SIMPLE_CIF = """\
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
ATOM 1 N N MET A 1 1.0 2.0 3.0 1.0 82.5 1 MET A N
ATOM 2 CA C MET A 1 2.0 3.0 4.0 1.0 85.0 1 MET A CA
ATOM 3 N N GLY A 2 3.0 4.0 5.0 1.0 90.0 2 GLY A N
"""

    def test_column_detection(self):
        """_detect_atom_site_columns correctly maps field names to indices."""
        cols = shared_utils._detect_atom_site_columns(self.SIMPLE_CIF)
        assert cols["B_iso_or_equiv"] == 11
        assert cols["auth_asym_id"] == 14
        assert cols["auth_comp_id"] == 13
        assert cols["auth_seq_id"] == 12

    def test_returns_tuple(self):
        """parse_cif_chains returns (chain_info_list, plddt_scores_list)."""
        result = boltz2_analyzer.parse_cif_chains(self.SIMPLE_CIF)
        assert isinstance(result, tuple)
        assert len(result) == 2
        chain_info, plddt_scores = result
        assert isinstance(chain_info, list)
        assert isinstance(plddt_scores, list)

    def test_extracts_plddt_from_bfactors(self):
        """pLDDT scores are read from B_iso_or_equiv column."""
        _, plddt_scores = boltz2_analyzer.parse_cif_chains(self.SIMPLE_CIF, extract_plddt=True)
        assert len(plddt_scores) == 3
        assert plddt_scores[0] == pytest.approx(82.5)
        assert plddt_scores[1] == pytest.approx(85.0)
        assert plddt_scores[2] == pytest.approx(90.0)

    def test_chain_info_correct(self):
        """Chain info is correctly extracted."""
        chain_info, _ = boltz2_analyzer.parse_cif_chains(self.SIMPLE_CIF)
        assert len(chain_info) == 1
        assert chain_info[0]["chain_id"] == "A"
        assert chain_info[0]["atom_count"] == 3
        assert chain_info[0]["residue_count"] == 2
        assert chain_info[0]["molecule_type"] == "protein"

    def test_empty_cif_returns_empty(self):
        """CIF with no ATOM records returns empty lists."""
        chain_info, plddt_scores = boltz2_analyzer.parse_cif_chains(
            "loop_\n_atom_site.group_PDB\n"
        )
        assert chain_info == []
        assert plddt_scores == []

    def test_multi_chain(self):
        """Multi-chain CIF produces separate entries per chain."""
        two_chain_cif = self.SIMPLE_CIF + (
            "ATOM 4 N N ALA B 1 4.0 5.0 6.0 1.0 75.0 1 ALA B N\n"
        )
        chain_info, plddt_scores = boltz2_analyzer.parse_cif_chains(two_chain_cif, extract_plddt=True)
        assert len(chain_info) == 2
        assert chain_info[0]["chain_id"] == "A"
        assert chain_info[1]["chain_id"] == "B"
        assert len(plddt_scores) == 4
        assert plddt_scores[3] == pytest.approx(75.0)
