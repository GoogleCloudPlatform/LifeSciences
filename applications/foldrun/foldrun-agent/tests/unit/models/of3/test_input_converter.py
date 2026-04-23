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

"""Tests for FASTA → OF3 JSON conversion and validation.

OF3 JSON schema:
  {"queries": {"name": {"chains": [{"molecule_type": ..., "chain_ids": [...], "sequence": ...}]}}}
"""

import json

import pytest

from foldrun_app.models.of3.utils.input_converter import (
    _validate_sequence_chars,
    count_tokens,
    fasta_to_of3_json,
    is_of3_json,
    validate_of3_json,
)

# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

PROTEIN_SEQ = "MKQHEDKLEEELLSKNYHLENEVAR"
RNA_SEQ = "ACGUACGUACGUACGUACGUACGUACGUACGUACGU"
DNA_SEQ = "ACGTACGTACGTACGTACGTACGTACGTACGTACGT"

VALID_JSON = json.dumps({
    "queries": {
        "test": {
            "chains": [
                {"molecule_type": "protein", "chain_ids": ["A"], "sequence": PROTEIN_SEQ}
            ]
        }
    }
})

VALID_MULTICHAIN_JSON = json.dumps({
    "queries": {
        "test": {
            "chains": [
                {"molecule_type": "protein", "chain_ids": ["A"], "sequence": PROTEIN_SEQ},
                {"molecule_type": "rna", "chain_ids": ["B"], "sequence": RNA_SEQ},
            ]
        }
    }
})

VALID_LIGAND_JSON = json.dumps({
    "queries": {
        "test": {
            "chains": [
                {"molecule_type": "protein", "chain_ids": ["A"], "sequence": PROTEIN_SEQ},
                {"molecule_type": "ligand", "chain_ids": ["B"], "smiles": "CC(=O)O"},
            ]
        }
    }
})


# ---------------------------------------------------------------------------
# fasta_to_of3_json
# ---------------------------------------------------------------------------

class TestFastaToOF3Json:
    """FASTA → OF3 JSON conversion."""

    def test_single_protein(self):
        fasta = f">GCN4\n{PROTEIN_SEQ}"
        result = fasta_to_of3_json(fasta, "test_job")
        assert "queries" in result
        assert "test_job" in result["queries"]
        chains = result["queries"]["test_job"]["chains"]
        assert len(chains) == 1
        assert chains[0]["molecule_type"] == "protein"
        assert chains[0]["chain_ids"] == ["A"]
        assert chains[0]["sequence"] == PROTEIN_SEQ

    def test_multi_chain_protein(self):
        fasta = ">chain_A\nACDEFGHIKLMNPQRSTVWY\n>chain_B\nACDEFGHIKLMNPQRSTVWY"
        result = fasta_to_of3_json(fasta)
        chains = result["queries"]["chain_A"]["chains"]
        assert len(chains) == 2
        assert chains[0]["chain_ids"] == ["A"]
        assert chains[1]["chain_ids"] == ["B"]

    def test_rna_sequence(self):
        fasta = f">rna_chain\n{RNA_SEQ}"
        result = fasta_to_of3_json(fasta)
        chains = result["queries"]["rna_chain"]["chains"]
        assert chains[0]["molecule_type"] == "rna"

    def test_dna_sequence(self):
        fasta = f">dna_chain\n{DNA_SEQ}"
        result = fasta_to_of3_json(fasta)
        chains = result["queries"]["dna_chain"]["chains"]
        assert chains[0]["molecule_type"] == "dna"

    def test_default_query_name_from_header(self):
        fasta = f">my_protein\n{PROTEIN_SEQ}"
        result = fasta_to_of3_json(fasta)
        assert "my_protein" in result["queries"]

    def test_job_name_overrides_header(self):
        fasta = f">my_protein\n{PROTEIN_SEQ}"
        result = fasta_to_of3_json(fasta, job_name="custom_name")
        assert "custom_name" in result["queries"]

    def test_multiline_sequence_joined(self):
        fasta = ">protein\nACDEFGHIKL\nMNPQRSTVWY"
        result = fasta_to_of3_json(fasta)
        chains = result["queries"]["protein"]["chains"]
        assert chains[0]["sequence"] == "ACDEFGHIKLMNPQRSTVWY"

    def test_raw_sequence_no_header(self):
        """Bare sequence without '>' should be accepted."""
        result = fasta_to_of3_json(PROTEIN_SEQ)
        chains = list(result["queries"].values())[0]["chains"]
        assert chains[0]["molecule_type"] == "protein"
        assert chains[0]["sequence"] == PROTEIN_SEQ

    def test_lowercase_converted_to_uppercase(self):
        fasta = f">protein\n{PROTEIN_SEQ.lower()}"
        result = fasta_to_of3_json(fasta)
        chains = list(result["queries"].values())[0]["chains"]
        assert chains[0]["sequence"] == PROTEIN_SEQ.upper()

    def test_empty_fasta_raises(self):
        with pytest.raises(ValueError, match="No valid sequences"):
            fasta_to_of3_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="No valid sequences"):
            fasta_to_of3_json("   \n\n  ")

    def test_digits_stripped_silently(self):
        """FASTA converter strips non-alpha chars (copy-paste numbers etc).
        Digit-only invalid chars are removed before validation, not raised."""
        fasta = ">protein\n1 MKQHEDKLEE 2 ELLSKNYHL"
        result = fasta_to_of3_json(fasta)
        chains = list(result["queries"].values())[0]["chains"]
        assert chains[0]["sequence"] == "MKQHEDKLEEELLSKNYHL"

    def test_too_short_sequence_raises(self):
        fasta = ">short\nMKQ"  # 3 residues < 5 minimum
        with pytest.raises(ValueError, match="too short"):
            fasta_to_of3_json(fasta)


# ---------------------------------------------------------------------------
# is_of3_json
# ---------------------------------------------------------------------------

class TestIsOF3Json:
    """OF3 JSON format detection."""

    def test_valid_protein_json(self):
        assert is_of3_json(VALID_JSON) is True

    def test_valid_multichain_json(self):
        assert is_of3_json(VALID_MULTICHAIN_JSON) is True

    def test_valid_ligand_json(self):
        assert is_of3_json(VALID_LIGAND_JSON) is True

    def test_fasta_rejected(self):
        assert is_of3_json(f">protein\n{PROTEIN_SEQ}") is False

    def test_json_without_queries_rejected(self):
        assert is_of3_json('{"name": "test"}') is False

    def test_boltz2_yaml_rejected(self):
        assert is_of3_json("version: 1\nsequences:\n  - protein:\n      id: A") is False

    def test_queries_without_chains_rejected(self):
        """queries present but no chains list → should be rejected."""
        data = json.dumps({"queries": {"test": {"other_key": "value"}}})
        assert is_of3_json(data) is False

    def test_empty_queries_rejected(self):
        assert is_of3_json('{"queries": {}}') is False

    def test_invalid_json_rejected(self):
        assert is_of3_json("{invalid}") is False

    def test_empty_string_rejected(self):
        assert is_of3_json("") is False

    def test_boltz2_sequences_schema_rejected(self):
        """Old 'sequences' schema must not be detected as OF3 JSON."""
        assert is_of3_json('{"sequences": [{"id": "a"}]}') is False


# ---------------------------------------------------------------------------
# validate_of3_json
# ---------------------------------------------------------------------------

class TestValidateOF3Json:
    """validate_of3_json() structural and sequence validation."""

    def test_valid_protein(self):
        ok, errors, warnings = validate_of3_json(VALID_JSON)
        assert ok, errors
        assert errors == []

    def test_valid_multichain(self):
        ok, errors, warnings = validate_of3_json(VALID_MULTICHAIN_JSON)
        assert ok, errors

    def test_valid_ligand_smiles(self):
        ok, errors, warnings = validate_of3_json(VALID_LIGAND_JSON)
        assert ok, errors

    def test_valid_ligand_ccd(self):
        data = json.dumps({
            "queries": {
                "test": {
                    "chains": [
                        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": PROTEIN_SEQ},
                        {"molecule_type": "ligand", "chain_ids": ["B"], "ccd_codes": ["ATP"]},
                    ]
                }
            }
        })
        ok, errors, _ = validate_of3_json(data)
        assert ok, errors

    def test_valid_ligand_ccd_string(self):
        data = json.dumps({
            "queries": {
                "test": {
                    "chains": [
                        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": PROTEIN_SEQ},
                        {"molecule_type": "ligand", "chain_ids": ["B"], "ccd_codes": "ATP"},
                    ]
                }
            }
        })
        ok, errors, _ = validate_of3_json(data)
        assert ok, errors

    def test_ligand_ccd_codes_invalid_type(self):
        data = json.dumps({
            "queries": {
                "test": {
                    "chains": [
                        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": PROTEIN_SEQ},
                        {"molecule_type": "ligand", "chain_ids": ["B"], "ccd_codes": 123},
                    ]
                }
            }
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("must be a string or a list of strings" in e for e in errors)

    def test_ligand_ccd_codes_not_strings(self):
        data = json.dumps({
            "queries": {
                "test": {
                    "chains": [
                        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": PROTEIN_SEQ},
                        {"molecule_type": "ligand", "chain_ids": ["B"], "ccd_codes": [123]},
                    ]
                }
            }
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("must be strings" in e for e in errors)

    def test_bad_json_parse_error(self):
        ok, errors, _ = validate_of3_json("{invalid json")
        assert not ok
        assert any("parse error" in e.lower() for e in errors)

    def test_missing_queries(self):
        ok, errors, _ = validate_of3_json('{"name": "test"}')
        assert not ok
        assert any("queries" in e.lower() for e in errors)

    def test_empty_queries(self):
        ok, errors, _ = validate_of3_json('{"queries": {}}')
        assert not ok

    def test_chains_not_a_list(self):
        data = json.dumps({"queries": {"test": {"chains": "not_a_list"}}})
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("chains" in e for e in errors)

    def test_missing_molecule_type(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"chain_ids": ["A"], "sequence": PROTEIN_SEQ}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("molecule_type" in e for e in errors)

    def test_unknown_molecule_type(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "peptide", "chain_ids": ["A"], "sequence": PROTEIN_SEQ}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("molecule_type" in e.lower() or "peptide" in e for e in errors)

    def test_missing_chain_ids(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "protein", "sequence": PROTEIN_SEQ}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("chain_ids" in e for e in errors)

    def test_invalid_protein_chars(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "protein", "chain_ids": ["A"], "sequence": "MKQ123HEDKL"}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("invalid characters" in e.lower() for e in errors)

    def test_invalid_rna_chars(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "rna", "chain_ids": ["A"], "sequence": "ACGUTACGU"}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("invalid characters" in e.lower() for e in errors)

    def test_invalid_dna_chars(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "dna", "chain_ids": ["A"], "sequence": "ACGUACGT"}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("invalid characters" in e.lower() for e in errors)

    def test_empty_sequence(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "protein", "chain_ids": ["A"], "sequence": ""}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("empty" in e.lower() for e in errors)

    def test_ligand_missing_smiles_and_ccd(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "ligand", "chain_ids": ["B"]}
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert any("smiles" in e.lower() or "ccd_codes" in e.lower() for e in errors)

    def test_short_protein_produces_warning(self):
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "protein", "chain_ids": ["A"], "sequence": "MKQ"}
            ]}}
        })
        _, _, warnings = validate_of3_json(data)
        assert any("short" in w.lower() for w in warnings)

    def test_multiple_errors_reported_at_once(self):
        """All errors should be collected, not stopped at the first."""
        data = json.dumps({
            "queries": {"test": {"chains": [
                {"molecule_type": "protein", "sequence": "MKQ123"},  # missing chain_ids + bad chars
                {"molecule_type": "rna", "chain_ids": ["B"], "sequence": "ACGUT"},  # bad RNA chars
            ]}}
        })
        ok, errors, _ = validate_of3_json(data)
        assert not ok
        assert len(errors) >= 2


# ---------------------------------------------------------------------------
# _validate_sequence_chars
# ---------------------------------------------------------------------------

class TestValidateSequenceChars:
    """Direct unit tests for _validate_sequence_chars."""

    def test_valid_protein(self):
        assert _validate_sequence_chars(PROTEIN_SEQ, "protein") == []

    def test_valid_protein_ambiguity_codes(self):
        assert _validate_sequence_chars("MKQBXZ", "protein") == []

    def test_valid_rna(self):
        assert _validate_sequence_chars("ACGUACGU", "rna") == []

    def test_valid_dna(self):
        assert _validate_sequence_chars("ACGTACGT", "dna") == []

    def test_protein_with_numbers(self):
        errors = _validate_sequence_chars("MKQ123", "protein")
        assert errors
        assert any(c in errors[0] for c in ["1", "2", "3"])

    def test_rna_with_t(self):
        errors = _validate_sequence_chars("ACGUTACGU", "rna")
        assert errors
        assert "T" in errors[0]

    def test_dna_with_u(self):
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
    """Token counting with OF3 schema."""

    def test_single_chain(self):
        query = {"queries": {"test": {"chains": [
            {"molecule_type": "protein", "sequence": "ACDEFGHIKL"}
        ]}}}
        assert count_tokens(query) == 10

    def test_multiple_chains(self):
        query = {"queries": {"test": {"chains": [
            {"molecule_type": "protein", "sequence": "ACDEFGHIKL"},
            {"molecule_type": "rna", "sequence": "ACGU"},
        ]}}}
        assert count_tokens(query) == 14

    def test_ligand_not_counted(self):
        """Ligands (SMILES) contribute 0 tokens."""
        query = {"queries": {"test": {"chains": [
            {"molecule_type": "protein", "sequence": "ACDE"},
            {"molecule_type": "ligand", "smiles": "CC(=O)O"},
        ]}}}
        assert count_tokens(query) == 4

    def test_empty_queries(self):
        assert count_tokens({"queries": {}}) == 0

    def test_multiple_queries_summed(self):
        query = {"queries": {
            "q1": {"chains": [{"molecule_type": "protein", "sequence": "ACDE"}]},
            "q2": {"chains": [{"molecule_type": "rna", "sequence": "ACGU"}]},
        }}
        assert count_tokens(query) == 8
