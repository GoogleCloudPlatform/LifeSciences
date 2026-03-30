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

"""Tests for core FASTA preprocessing and AF2 parser integration."""

from unittest.mock import patch


class TestFixFasta:
    """Tests for core.fasta.fix_fasta preprocessing."""

    def test_normal_fasta_unchanged(self):
        from foldrun_app.core.fasta import fix_fasta

        normal = (
            ">chain_A\nMKQLEDKVEELLSKNYHLENEVARLKKLVGER\n>chain_B\nMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
        )
        result = fix_fasta(normal)
        assert result == normal

    def test_missing_newline_before_header(self):
        from foldrun_app.core.fasta import fix_fasta

        corrupt = (
            ">chain_A\nMKQLEDKVEELLSKNYHLENEVARLKKLVGER>chain_B\nMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
        )
        result = fix_fasta(corrupt)
        assert "\n>chain_B\n" in result

    def test_no_headers_at_all(self):
        from foldrun_app.core.fasta import fix_fasta

        raw = "MKQLEDKVEELLSKNYHLENEVARLKKLVGER"
        result = fix_fasta(raw)
        # No '>' in input, so no header prepended — raw passthrough
        assert ">" not in result

    def test_sequence_concatenated_onto_header(self):
        from foldrun_app.core.fasta import fix_fasta

        # Header name runs into sequence with no newline
        corrupt = ">chain_BMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
        result = fix_fasta(corrupt)
        lines = result.strip().split("\n")
        assert lines[0].startswith(">chain_B")
        assert lines[1] == "MKQLEDKVEELLSKNYHLENEVARLKKLVGER"

    def test_fully_corrupt_gcn4_input(self):
        """The exact failure case: two chains pasted with no newlines."""
        from foldrun_app.core.fasta import fix_fasta

        corrupt = "RMKQLEDKVEELLSKNYHLENEVARLKKLVGER>chain_BRMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
        result = fix_fasta(corrupt)
        # Should have two headers and two sequences
        assert result.count(">") == 2
        lines = [l for l in result.strip().split("\n") if l.strip()]
        # 4 lines: header, seq, header, seq
        assert len(lines) == 4

    def test_header_ending_in_valid_aa_short_suffix_not_split(self):
        """Header names like >GCN4_PROTEIN_A should NOT be split."""
        from foldrun_app.core.fasta import fix_fasta

        normal = ">GCN4_PROTEIN_A\nMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
        result = fix_fasta(normal)
        lines = result.strip().split("\n")
        assert lines[0] == ">GCN4_PROTEIN_A"

    def test_empty_input(self):
        from foldrun_app.core.fasta import fix_fasta

        assert fix_fasta("") == ""
        assert fix_fasta("   ") == "   "
        assert fix_fasta(None) is None


class TestParserIntegration:
    """Test that fix_fasta integrates with the AF2 FASTA parsers."""

    def test_parse_fasta_content_handles_corrupt_multimer(self, mock_env_vars):
        """parse_fasta_content should auto-repair and parse corrupt multimer input."""
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.utils.fasta_utils import parse_fasta_content

            corrupt = ">chain_A\nRMKQLEDKVEELLSKNYHLENEVARLKKLVGER>chain_B\nRMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
            result = parse_fasta_content(corrupt)
            assert len(result) == 2
            assert result[0]["sequence"] == "RMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
            assert result[1]["sequence"] == "RMKQLEDKVEELLSKNYHLENEVARLKKLVGER"

    def test_parse_fasta_content_normal_input_still_works(self, mock_env_vars):
        """Normal FASTA input should parse correctly after fix_fasta."""
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.utils.fasta_utils import parse_fasta_content

            normal = ">ubiquitin\nMQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
            result = parse_fasta_content(normal)
            assert len(result) == 1
            assert len(result[0]["sequence"]) == 76

    def test_validate_fasta_sequence_handles_corrupt(self, mock_env_vars):
        """fasta_validator should auto-repair corrupt input."""
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.utils.fasta_validator import validate_fasta_sequence

            corrupt = ">chain_A\nRMKQLEDKVEELLSKNYHLENEVARLKKLVGER>chain_B\nRMKQLEDKVEELLSKNYHLENEVARLKKLVGER"
            result = validate_fasta_sequence(corrupt, is_multimer=True)
            assert result["valid"] is True
            assert result["num_chains"] == 2
            assert result["total_length"] == 66
