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

"""Tests for FASTA → BOLTZ2 YAML conversion."""

import pytest
import yaml

from foldrun_app.models.boltz2.utils.input_converter import (
    count_tokens,
    fasta_to_boltz2_yaml,
    is_boltz2_yaml,
)


class TestFastaToBOLTZ2Yaml:
    """Test FASTA to BOLTZ2 YAML conversion."""

    def test_single_protein(self):
        fasta = ">GCN4\nMKQHEDKLEEELLSKNYHLENEVARLKKLVGER"
        result_yaml = fasta_to_boltz2_yaml(fasta, "test_job")
        result = yaml.safe_load(result_yaml)
        assert result["version"] == 1  # Boltz-2 uses version: 1 (optional, defaults to 1)
        sequences = result["sequences"]
        assert len(sequences) == 1
        assert "protein" in sequences[0]
        assert sequences[0]["protein"]["id"] == "A"
        assert sequences[0]["protein"]["sequence"].startswith("MKQHED")

    def test_multi_chain_protein(self):
        fasta = ">chain_A\nACDEFGHIKLMNPQRSTVWY\n>chain_B\nACDEFGHIKLMNPQRSTVWY"
        result_yaml = fasta_to_boltz2_yaml(fasta)
        result = yaml.safe_load(result_yaml)
        sequences = result["sequences"]
        assert len(sequences) == 2
        assert "protein" in sequences[0]
        assert sequences[0]["protein"]["id"] == "A"
        assert "protein" in sequences[1]
        assert sequences[1]["protein"]["id"] == "B"

    def test_rna_sequence(self):
        fasta = ">rna_chain\nACGUACGUACGUACGUACGUACGUACGUACGUACGU"
        result_yaml = fasta_to_boltz2_yaml(fasta)
        result = yaml.safe_load(result_yaml)
        sequences = result["sequences"]
        assert len(sequences) == 1
        assert "rna" in sequences[0]

    def test_empty_fasta_raises(self):
        with pytest.raises(ValueError, match="No valid sequences"):
            fasta_to_boltz2_yaml("")

    def test_multiline_sequence(self):
        fasta = ">protein\nACDEFGHIKL\nMNPQRSTVWY"
        result_yaml = fasta_to_boltz2_yaml(fasta)
        result = yaml.safe_load(result_yaml)
        sequences = result["sequences"]
        assert sequences[0]["protein"]["sequence"] == "ACDEFGHIKLMNPQRSTVWY"


class TestIsBOLTZ2Yaml:
    """Test BOLTZ2 YAML detection."""

    def test_valid_boltz2_yaml(self):
        data = "version: 2\nsequences:\n  - protein:\n      id: A\n      sequence: ACDE"
        assert is_boltz2_yaml(data) is True

    def test_fasta_not_yaml(self):
        assert is_boltz2_yaml(">protein\nACDE") is False

    def test_invalid_yaml(self):
        assert is_boltz2_yaml("invalid") is False

    def test_empty_string(self):
        assert is_boltz2_yaml("") is False


class TestCountTokens:
    """Test token counting with BOLTZ2 YAML schema."""

    def test_single_chain(self):
        query = "version: 2\nsequences:\n  - protein:\n      id: A\n      sequence: ACDEFGHIKL\n"
        assert count_tokens(query) == 10

    def test_multiple_chains(self):
        query = "version: 2\nsequences:\n  - protein:\n      id: A\n      sequence: ACDEFGHIKL\n  - rna:\n      id: B\n      sequence: ACGU\n"
        assert count_tokens(query) == 14

    def test_empty_queries(self):
        query = "version: 2\nsequences:\n"
        assert count_tokens(query) == 0
