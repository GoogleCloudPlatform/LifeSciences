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

"""Tests for AlphaFold 3 input converter and validator."""

import json

from foldrun_app.models.af3.utils.input_converter import (
    count_af3_tokens,
    fasta_to_af3_json,
    is_af3_json,
    validate_af3_json,
)


class TestAF3InputConverter:
    """Tests for converting FASTA and validating AF3 JSON structures."""

    def test_fasta_to_af3_json_monomer(self):
        seq = "MKTIIALSYIFCLVFA"
        data = fasta_to_af3_json(seq, job_name="test_monomer", msa_free=True)

        assert data["name"] == "test_monomer"
        assert data["msaFree"] is True
        assert data["dialect"] == "alphafold3"
        assert len(data["sequences"]) == 1
        assert "protein" in data["sequences"][0]
        assert data["sequences"][0]["protein"]["id"] == "A"
        assert data["sequences"][0]["protein"]["sequence"] == seq

    def test_fasta_to_af3_json_multimer(self):
        fasta = ">chainA\nMKTI\n>chainB\nALSY\n"
        data = fasta_to_af3_json(fasta, job_name="test_dimer")

        assert len(data["sequences"]) == 2
        assert data["sequences"][0]["protein"]["id"] == "A"
        assert data["sequences"][0]["protein"]["sequence"] == "MKTI"
        assert data["sequences"][1]["protein"]["id"] == "B"
        assert data["sequences"][1]["protein"]["sequence"] == "ALSY"

    def test_fasta_detection_rna_dna(self):
        rna_fasta = ">rna\nAGCUAGCU\n"
        dna_fasta = ">dna\nATCGATCGATCGATCGATCGATCGATCGATCG\n"  # > 30 nt

        rna_data = fasta_to_af3_json(rna_fasta)
        assert "rna" in rna_data["sequences"][0]

        dna_data = fasta_to_af3_json(dna_fasta)
        assert "dna" in dna_data["sequences"][0]

    def test_fasta_header_hints(self):
        short_dna_hint = ">chainA|dna\nATCG\n"
        short_prot_hint = ">chainB|protein\nACGT\n"

        dna_data = fasta_to_af3_json(short_dna_hint)
        assert "dna" in dna_data["sequences"][0]

        prot_data = fasta_to_af3_json(short_prot_hint)
        assert "protein" in prot_data["sequences"][0]

    def test_fasta_ambiguous_short_sequence_warning(self):
        short_ambiguous = ">chainA\nACGTACGT\n"
        warnings = []
        data = fasta_to_af3_json(short_ambiguous, warnings_out=warnings)
        assert "dna" in data["sequences"][0]
        assert len(warnings) == 1
        assert "Classified as DNA" in warnings[0]

    def test_is_af3_json(self):
        valid_json = json.dumps(
            {
                "name": "complex",
                "sequences": [{"protein": {"id": "A", "sequence": "MKTI"}}],
                "dialect": "alphafold3",
            }
        )
        assert is_af3_json(valid_json) is True
        assert is_af3_json({"sequences": [{"ion": {"id": "B", "ion": "MG"}}]}) is True
        assert is_af3_json("MKTI") is False
        assert is_af3_json("{invalid_json") is False

    def test_validate_af3_json_all_atom_complex(self):
        complex_data = {
            "name": "kinase_drug_complex",
            "modelSeeds": [1, 2],
            "sequences": [
                {"protein": {"id": "A", "sequence": "MKTI"}},
                {"rna": {"id": "B", "sequence": "AGCU"}},
                {"dna": {"id": "C", "sequence": "ATCG"}},
                {"ligand": {"id": "D", "ccdCodes": ["ATP"]}},
                {"ligand": {"id": "E", "smiles": "CC(=O)O"}},
                {"ion": {"id": "F", "ion": "MG"}},
            ],
            "msaFree": True,
        }

        is_valid, errors, _warnings = validate_af3_json(complex_data)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_af3_json_duplicate_chain_id(self):
        invalid_data = {
            "name": "bad_complex",
            "sequences": [
                {"protein": {"id": "A", "sequence": "MKTI"}},
                {"protein": {"id": "A", "sequence": "ALSY"}},
            ],
        }
        is_valid, errors, _warnings = validate_af3_json(invalid_data)
        assert is_valid is False
        assert any("Duplicate chain ID" in e for e in errors)

    def test_validate_af3_json_invalid_amino_acid(self):
        invalid_data = {
            "name": "bad_protein",
            "sequences": [{"protein": {"id": "A", "sequence": "MKTI123"}}],
        }
        is_valid, errors, _warnings = validate_af3_json(invalid_data)
        assert is_valid is False
        assert any("invalid characters" in e for e in errors)

    def test_count_af3_tokens(self):
        complex_data = {
            "sequences": [
                {"protein": {"id": "A", "sequence": "MKTIIALSY"}},  # 9
                {"rna": {"id": "B", "sequence": "AGCU"}},  # 4
                {"ligand": {"id": "C", "ccdCodes": ["ATP", "MG"]}},  # 2
                {"ion": {"id": "D", "ion": "ZN"}},  # 1
            ]
        }
        assert count_af3_tokens(complex_data) == 16
