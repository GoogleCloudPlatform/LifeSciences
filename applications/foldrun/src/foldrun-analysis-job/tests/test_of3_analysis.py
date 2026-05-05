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

"""Tests for OpenFold3 analysis utilities: parse_cif_chains, calculate_per_chain_plddt."""

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

from foldrun_analysis import of3_analyzer  # noqa: E402


class TestOF3Analysis:
    """Tests for OpenFold3 post-processing functions."""

    # Minimal synthetic CIF matching OpenFold3's hardcoded indices:
    # parts[9] = seq_id, parts[10] = comp_id, parts[11] = chain_id
    SIMPLE_CIF = """\
ATOM 1 N N MET A 1 1.0 2.0 1 MET A
ATOM 2 CA C MET A 1 2.0 3.0 1 MET A
ATOM 3 N N GLY A 2 3.0 4.0 2 GLY A
"""

    def test_parse_cif_chains_protein(self):
        """Correctly parses protein structures and atom/residue counts from CIF."""
        chain_info, _ = of3_analyzer.parse_cif_chains(self.SIMPLE_CIF)
        assert len(chain_info) == 1
        assert chain_info[0]["chain_id"] == "A"
        assert chain_info[0]["atom_count"] == 3
        assert chain_info[0]["residue_count"] == 2
        assert chain_info[0]["molecule_type"] == "protein"

    def test_parse_cif_chains_multiple(self):
        """Correctly parses multi-chain structures and distinct molecule types."""
        cif_with_ligand = self.SIMPLE_CIF + ("HETATM 4 C C HOH B 1 4.0 5.0 1 HOH B\n")
        chain_info, _ = of3_analyzer.parse_cif_chains(cif_with_ligand)
        assert len(chain_info) == 2
        assert chain_info[0]["chain_id"] == "A"
        assert chain_info[0]["molecule_type"] == "protein"
        assert chain_info[1]["chain_id"] == "B"
        assert chain_info[1]["molecule_type"] == "ligand"

    def test_calculate_per_chain_plddt(self):
        """Correctly partitions flat pLDDT scores based on per-chain atom boundaries."""
        plddt_scores = [80.0, 85.0, 90.0, 70.0]
        chain_info = [
            {
                "chain_id": "A",
                "atom_count": 3,
                "residue_count": 2,
                "molecule_type": "protein",
                "comp_ids": ["MET", "GLY"],
            },
            {
                "chain_id": "B",
                "atom_count": 1,
                "residue_count": 1,
                "molecule_type": "ligand",
                "comp_ids": ["HOH"],
            },
        ]
        per_chain = of3_analyzer.calculate_per_chain_plddt(plddt_scores, chain_info)

        assert "A" in per_chain
        assert per_chain["A"]["mean"] == pytest.approx(85.0)  # (80+85+90)/3
        assert per_chain["A"]["min"] == 80.0
        assert per_chain["A"]["max"] == 90.0

        assert "B" in per_chain
        assert per_chain["B"]["mean"] == 70.0
        assert per_chain["B"]["min"] == 70.0
        assert per_chain["B"]["max"] == 70.0
