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

"""Tests for FASTA → OF3 JSON conversion.

OF3 JSON schema: {"queries": {"name": {"chains": [{"molecule_type": ..., "chain_ids": [...], "sequence": ...}]}}}
"""

import json

import pytest

from foldrun_app.models.of3.utils.input_converter import (
    count_tokens,
    fasta_to_of3_json,
    is_of3_json,
)


class TestFastaToOF3Json:
    """Test FASTA to OF3 JSON conversion."""

    def test_single_protein(self):
        fasta = ">GCN4\nMKQHEDKLEEELLSKNYHLENEVARLKKLVGER"
        result = fasta_to_of3_json(fasta, "test_job")
        assert "queries" in result
        assert "test_job" in result["queries"]
        chains = result["queries"]["test_job"]["chains"]
        assert len(chains) == 1
        assert chains[0]["molecule_type"] == "protein"
        assert chains[0]["chain_ids"] == ["A"]
        assert chains[0]["sequence"].startswith("MKQHED")

    def test_multi_chain_protein(self):
        fasta = ">chain_A\nACDEFGHIKLMNPQRSTVWY\n>chain_B\nACDEFGHIKLMNPQRSTVWY"
        result = fasta_to_of3_json(fasta)
        chains = result["queries"]["chain_A"]["chains"]
        assert len(chains) == 2
        assert chains[0]["molecule_type"] == "protein"
        assert chains[0]["chain_ids"] == ["A"]
        assert chains[1]["chain_ids"] == ["B"]

    def test_rna_sequence(self):
        fasta = ">rna_chain\nACGUACGUACGUACGUACGUACGUACGUACGUACGU"
        result = fasta_to_of3_json(fasta)
        chains = result["queries"]["rna_chain"]["chains"]
        assert len(chains) == 1
        assert chains[0]["molecule_type"] == "rna"

    def test_default_query_name(self):
        fasta = ">my_protein\nACDEFGHIKLMNPQRSTVWY"
        result = fasta_to_of3_json(fasta)
        assert "my_protein" in result["queries"]

    def test_empty_fasta_raises(self):
        with pytest.raises(ValueError, match="No valid sequences"):
            fasta_to_of3_json("")

    def test_multiline_sequence(self):
        fasta = ">protein\nACDEFGHIKL\nMNPQRSTVWY"
        result = fasta_to_of3_json(fasta)
        chains = result["queries"]["protein"]["chains"]
        assert chains[0]["sequence"] == "ACDEFGHIKLMNPQRSTVWY"


class TestIsOF3Json:
    """Test OF3 JSON detection."""

    def test_valid_of3_json(self):
        data = json.dumps(
            {
                "queries": {
                    "test": {
                        "chains": [
                            {"molecule_type": "protein", "chain_ids": ["A"], "sequence": "ACDE"}
                        ]
                    }
                }
            }
        )
        assert is_of3_json(data) is True

    def test_fasta_not_json(self):
        assert is_of3_json(">protein\nACDE") is False

    def test_json_without_queries(self):
        assert is_of3_json('{"name": "test"}') is False

    def test_old_schema_not_detected(self):
        """Old 'sequences' schema should not be detected as OF3 JSON."""
        assert is_of3_json('{"sequences": [{"id": "a"}]}') is False

    def test_invalid_json(self):
        assert is_of3_json("{invalid}") is False

    def test_empty_string(self):
        assert is_of3_json("") is False


class TestCountTokens:
    """Test token counting with OF3 schema."""

    def test_single_chain(self):
        query = {
            "queries": {
                "test": {"chains": [{"molecule_type": "protein", "sequence": "ACDEFGHIKL"}]}
            }
        }
        assert count_tokens(query) == 10

    def test_multiple_chains(self):
        query = {
            "queries": {
                "test": {
                    "chains": [
                        {"molecule_type": "protein", "sequence": "ACDEFGHIKL"},
                        {"molecule_type": "rna", "sequence": "ACGU"},
                    ]
                }
            }
        }
        assert count_tokens(query) == 14

    def test_ligand_not_counted(self):
        """Ligands use SMILES, not sequences — should contribute 0 tokens."""
        query = {
            "queries": {
                "test": {
                    "chains": [
                        {"molecule_type": "protein", "sequence": "ACDE"},
                        {"molecule_type": "ligand", "smiles": "CC(=O)O"},
                    ]
                }
            }
        }
        assert count_tokens(query) == 4

    def test_empty_queries(self):
        query = {"queries": {}}
        assert count_tokens(query) == 0
