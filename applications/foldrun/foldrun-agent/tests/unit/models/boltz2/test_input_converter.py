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

"""Tests for FASTA → Boltz-2 YAML conversion and validation."""

import pytest
import yaml

from foldrun_app.models.boltz2.utils.input_converter import (
    _validate_sequence_chars,
    count_tokens,
    fasta_to_boltz2_yaml,
    is_boltz2_yaml,
    validate_boltz2_yaml,
)

# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

PROTEIN_SEQ = "MKQHEDKLEEELLSKNYHLENEVAR"  # 25 aa, unambiguous protein
RNA_SEQ = "ACGUACGUACGUACGUACGUACGUACGUACGUACGU"  # 36 nt, pure RNA
DNA_SEQ = "ACGTACGTACGTACGTACGTACGTACGTACGTACGT"  # 37 nt, pure DNA

VALID_PROTEIN_YAML = f"""\
version: 1
sequences:
  - protein:
      id: A
      sequence: {PROTEIN_SEQ}
"""

VALID_MULTICHAIN_YAML = f"""\
version: 1
sequences:
  - protein:
      id: A
      sequence: {PROTEIN_SEQ}
  - rna:
      id: B
      sequence: {RNA_SEQ}
"""

VALID_LIGAND_YAML = """\
version: 1
sequences:
  - protein:
      id: A
      sequence: MKQHEDKLEEELLSKNYHLENEVAR
  - smiles:
      id: B
      smiles: "CC(=O)O"
"""


# ---------------------------------------------------------------------------
# fasta_to_boltz2_yaml
# ---------------------------------------------------------------------------

class TestFastaToBoltz2Yaml:
    """FASTA → Boltz-2 YAML conversion."""

    def test_single_protein(self):
        fasta = f">GCN4\n{PROTEIN_SEQ}"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta, "test_job"))
        assert result["version"] == 1
        seqs = result["sequences"]
        assert len(seqs) == 1
        assert "protein" in seqs[0]
        assert seqs[0]["protein"]["id"] == "A"
        assert seqs[0]["protein"]["sequence"] == PROTEIN_SEQ

    def test_multi_chain_protein(self):
        fasta = ">chain_A\nACDEFGHIKLMNPQRSTVWY\n>chain_B\nACDEFGHIKLMNPQRSTVWY"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta))
        seqs = result["sequences"]
        assert len(seqs) == 2
        assert seqs[0]["protein"]["id"] == "A"
        assert seqs[1]["protein"]["id"] == "B"

    def test_rna_sequence(self):
        fasta = f">rna_chain\n{RNA_SEQ}"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta))
        assert "rna" in result["sequences"][0]

    def test_dna_sequence(self):
        fasta = f">dna_chain\n{DNA_SEQ}"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta))
        assert "dna" in result["sequences"][0]

    def test_multiline_sequence_joined(self):
        fasta = ">protein\nACDEFGHIKL\nMNPQRSTVWY"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta))
        assert result["sequences"][0]["protein"]["sequence"] == "ACDEFGHIKLMNPQRSTVWY"

    def test_raw_sequence_no_header(self):
        """Bare sequence without '>' header should be accepted."""
        result = yaml.safe_load(fasta_to_boltz2_yaml(PROTEIN_SEQ))
        assert "protein" in result["sequences"][0]
        assert result["sequences"][0]["protein"]["sequence"] == PROTEIN_SEQ

    def test_lowercase_converted_to_uppercase(self):
        fasta = f">protein\n{PROTEIN_SEQ.lower()}"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta))
        assert result["sequences"][0]["protein"]["sequence"] == PROTEIN_SEQ.upper()

    def test_empty_fasta_raises(self):
        with pytest.raises(ValueError, match="No valid sequences"):
            fasta_to_boltz2_yaml("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="No valid sequences"):
            fasta_to_boltz2_yaml("   \n\n  ")

    def test_digits_stripped_silently(self):
        """FASTA converter strips non-alpha chars (copy-paste numbers etc).
        Digit-only invalid chars are removed before validation, not raised."""
        fasta = ">protein\n1 MKQHEDKLEE 2 ELLSKNYHL"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta))
        # Numbers removed, remaining sequence is valid protein
        assert result["sequences"][0]["protein"]["sequence"] == "MKQHEDKLEEELLSKNYHL"

    def test_mixed_rna_classified_as_protein(self):
        """A sequence with both U and T can't be RNA or DNA — defaults to protein.
        The FASTA converter doesn't raise; validate_boltz2_yaml() catches this in YAML."""
        fasta = ">mixed\nACGUACGUTACGUACGUACGU"
        result = yaml.safe_load(fasta_to_boltz2_yaml(fasta))
        # Mixed ACGUT → detected as protein (fallback)
        assert "protein" in result["sequences"][0]

    def test_too_short_sequence_raises(self):
        fasta = ">short\nMKQ"  # 3 residues < 5 minimum
        with pytest.raises(ValueError, match="too short"):
            fasta_to_boltz2_yaml(fasta)


# ---------------------------------------------------------------------------
# is_boltz2_yaml
# ---------------------------------------------------------------------------

class TestIsBoltz2Yaml:
    """Boltz-2 YAML format detection."""

    def test_valid_yaml_version_1(self):
        assert is_boltz2_yaml(VALID_PROTEIN_YAML) is True

    def test_valid_multichain_yaml(self):
        assert is_boltz2_yaml(VALID_MULTICHAIN_YAML) is True

    def test_valid_ligand_yaml(self):
        assert is_boltz2_yaml(VALID_LIGAND_YAML) is True

    def test_wrong_version_rejected(self):
        """Version other than 1 must be rejected."""
        bad = VALID_PROTEIN_YAML.replace("version: 1", "version: 2")
        assert is_boltz2_yaml(bad) is False

    def test_missing_version_rejected(self):
        content = "sequences:\n  - protein:\n      id: A\n      sequence: ACDE"
        assert is_boltz2_yaml(content) is False

    def test_empty_sequences_rejected(self):
        content = "version: 1\nsequences: []"
        assert is_boltz2_yaml(content) is False

    def test_missing_sequences_rejected(self):
        content = "version: 1\nother: value"
        assert is_boltz2_yaml(content) is False

    def test_fasta_rejected(self):
        assert is_boltz2_yaml(f">protein\n{PROTEIN_SEQ}") is False

    def test_json_rejected(self):
        assert is_boltz2_yaml('{"queries": {}}') is False

    def test_malformed_yaml_rejected(self):
        assert is_boltz2_yaml("version: 1\nsequences:\n  - :\n    bad: [unclosed") is False

    def test_empty_string_rejected(self):
        assert is_boltz2_yaml("") is False


# ---------------------------------------------------------------------------
# validate_boltz2_yaml
# ---------------------------------------------------------------------------

class TestValidateBoltz2Yaml:
    """validate_boltz2_yaml() structural and sequence validation."""

    def test_valid_protein(self):
        ok, errors, warnings = validate_boltz2_yaml(VALID_PROTEIN_YAML)
        assert ok, errors
        assert errors == []

    def test_valid_multichain(self):
        ok, errors, warnings = validate_boltz2_yaml(VALID_MULTICHAIN_YAML)
        assert ok, errors

    def test_valid_ligand_smiles(self):
        ok, errors, warnings = validate_boltz2_yaml(VALID_LIGAND_YAML)
        assert ok, errors

    def test_valid_ligand_ccd(self):
        content = """\
version: 1
sequences:
  - protein:
      id: A
      sequence: MKQHEDKLEEELLSKNYHLENEVAR
  - ccd:
      id: B
      ccd: ATP
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert ok, errors

    def test_bad_yaml_parse_error(self):
        ok, errors, _ = validate_boltz2_yaml("version: 1\nsequences:\n  - :\n  bad: [unclosed")
        assert not ok
        assert any("parse error" in e.lower() for e in errors)

    def test_missing_version(self):
        content = "sequences:\n  - protein:\n      id: A\n      sequence: ACDEFGHIKLMN"
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("version" in e for e in errors)

    def test_wrong_version(self):
        content = VALID_PROTEIN_YAML.replace("version: 1", "version: 2")
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("version" in e.lower() for e in errors)

    def test_empty_sequences_list(self):
        content = "version: 1\nsequences: []"
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("empty" in e for e in errors)

    def test_missing_sequences_field(self):
        content = "version: 1\nother: value"
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("sequences" in e for e in errors)

    def test_unknown_sequence_type(self):
        content = """\
version: 1
sequences:
  - unknown_type:
      id: A
      sequence: MKQHEDKLEEELLSK
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("unknown" in e.lower() for e in errors)

    def test_missing_id_field(self):
        content = """\
version: 1
sequences:
  - protein:
      sequence: MKQHEDKLEEELLSK
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("id" in e for e in errors)

    def test_invalid_protein_chars(self):
        content = """\
version: 1
sequences:
  - protein:
      id: A
      sequence: MKQHED123KLEEELLSK
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("invalid characters" in e.lower() for e in errors)

    def test_invalid_rna_chars(self):
        content = """\
version: 1
sequences:
  - rna:
      id: A
      sequence: ACGUTACGU
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("invalid characters" in e.lower() for e in errors)

    def test_invalid_dna_chars(self):
        content = """\
version: 1
sequences:
  - dna:
      id: A
      sequence: ACGTACGUACGT
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("invalid characters" in e.lower() for e in errors)

    def test_empty_protein_sequence(self):
        content = """\
version: 1
sequences:
  - protein:
      id: A
      sequence: ""
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("empty" in e.lower() for e in errors)

    def test_ligand_missing_smiles_and_ccd(self):
        content = """\
version: 1
sequences:
  - smiles:
      id: B
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert any("smiles" in e.lower() or "ccd" in e.lower() for e in errors)

    def test_short_protein_produces_warning(self):
        content = """\
version: 1
sequences:
  - protein:
      id: A
      sequence: MKQ
"""
        # Short sequence — warning expected but not necessarily an error
        _, _, warnings = validate_boltz2_yaml(content)
        assert any("short" in w.lower() for w in warnings)

    def test_multiple_errors_reported_at_once(self):
        """All errors should be collected, not stopped at the first."""
        content = """\
version: 1
sequences:
  - protein:
      sequence: MKQ123
  - rna:
      id: B
      sequence: ACGUTACGU
"""
        ok, errors, _ = validate_boltz2_yaml(content)
        assert not ok
        assert len(errors) >= 2  # missing id + invalid chars in both chains


# ---------------------------------------------------------------------------
# _validate_sequence_chars
# ---------------------------------------------------------------------------

class TestValidateSequenceChars:
    """Direct unit tests for _validate_sequence_chars."""

    def test_valid_protein(self):
        assert _validate_sequence_chars(PROTEIN_SEQ, "protein") == []

    def test_valid_protein_with_ambiguity_codes(self):
        assert _validate_sequence_chars("MKQBXZ", "protein") == []

    def test_valid_rna(self):
        assert _validate_sequence_chars("ACGUACGU", "rna") == []

    def test_valid_dna(self):
        assert _validate_sequence_chars("ACGTACGT", "dna") == []

    def test_protein_with_numbers(self):
        errors = _validate_sequence_chars("MKQ123", "protein")
        assert errors
        assert "1" in errors[0] or "2" in errors[0] or "3" in errors[0]

    def test_rna_with_t(self):
        """T is not valid in RNA (use U instead)."""
        errors = _validate_sequence_chars("ACGUTACGU", "rna")
        assert errors
        assert "T" in errors[0]

    def test_dna_with_u(self):
        """U is not valid in DNA."""
        errors = _validate_sequence_chars("ACGUACGT", "dna")
        assert errors
        assert "U" in errors[0]

    def test_protein_with_special_chars(self):
        errors = _validate_sequence_chars("MKQ*HEDKL", "protein")
        assert errors


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------

class TestCountTokens:
    """Token counting using YAML parsing."""

    def test_single_chain(self):
        content = "version: 1\nsequences:\n  - protein:\n      id: A\n      sequence: ACDEFGHIKL\n"
        assert count_tokens(content) == 10

    def test_multiple_chains(self):
        content = (
            "version: 1\nsequences:\n"
            "  - protein:\n      id: A\n      sequence: ACDEFGHIKL\n"
            "  - rna:\n      id: B\n      sequence: ACGU\n"
        )
        assert count_tokens(content) == 14

    def test_ligand_not_counted(self):
        content = (
            "version: 1\nsequences:\n"
            "  - protein:\n      id: A\n      sequence: ACDE\n"
            "  - smiles:\n      id: B\n      smiles: CC(=O)O\n"
        )
        assert count_tokens(content) == 4

    def test_empty_sequences(self):
        content = "version: 1\nsequences:\n"
        assert count_tokens(content) == 0

    def test_fallback_on_bad_yaml(self):
        """Line-scan fallback should still count correctly for simple cases."""
        bad_yaml = "version: 1\nsequences:\n  - protein:\n      sequence: ACDEFGHIKL\n"
        # Even if parse succeeds, result should be 10
        assert count_tokens(bad_yaml) == 10
