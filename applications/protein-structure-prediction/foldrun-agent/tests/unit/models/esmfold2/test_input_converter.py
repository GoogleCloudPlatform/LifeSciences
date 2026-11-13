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

"""Tests for FASTA -> ESMFold2 JSON conversion and validation."""

import json

import pytest

from foldrun_app.models.esmfold2.utils.input_converter import (
    count_tokens,
    fasta_to_esmfold2_json,
    is_esmfold2_json,
    validate_esmfold2_json,
)

PROTEIN_SEQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
RNA_SEQ = "ACGUACGUACGUACGUACGUACGU"


class TestFastaToEsmfold2Json:
    """FASTA -> ESMFold2 JSON conversion."""

    def test_single_protein(self):
        fasta = f">ubiquitin\n{PROTEIN_SEQ}"
        data = json.loads(fasta_to_esmfold2_json(fasta, "ubq_job"))
        assert data["name"] == "ubq_job"
        assert len(data["sequences"]) == 1
        assert data["sequences"][0]["protein"]["id"] == "A"
        assert data["sequences"][0]["protein"]["sequence"] == PROTEIN_SEQ

    def test_multi_chain_dimer(self):
        fasta = f">chain_A\n{PROTEIN_SEQ}\n>chain_B\n{PROTEIN_SEQ}"
        data = json.loads(fasta_to_esmfold2_json(fasta, "ubq_dimer"))
        assert data["name"] == "ubq_dimer"
        assert len(data["sequences"]) == 2
        assert data["sequences"][0]["protein"]["id"] == "A"
        assert data["sequences"][1]["protein"]["id"] == "B"

    def test_raw_sequence_without_header(self):
        data = json.loads(fasta_to_esmfold2_json(PROTEIN_SEQ))
        assert len(data["sequences"]) == 1
        assert data["sequences"][0]["protein"]["sequence"] == PROTEIN_SEQ

    def test_mixed_protein_rna_and_ligand(self):
        fasta = f">prot\n{PROTEIN_SEQ}\n>rna_chain\n{RNA_SEQ}\n>L|ligand|ccd=ATP\nATP"
        data = json.loads(fasta_to_esmfold2_json(fasta, "complex_job"))
        assert len(data["sequences"]) == 3
        assert data["sequences"][0]["protein"]["id"] == "A"
        assert data["sequences"][1]["rna"]["id"] == "B"
        assert data["sequences"][1]["rna"]["sequence"] == RNA_SEQ
        assert data["sequences"][2]["ligand"] == {"id": "C", "ccd": ["ATP"]}

    def test_too_short_sequence_rejected(self):
        with pytest.raises(ValueError, match="too short"):
            fasta_to_esmfold2_json(">short\nMKQ")

    def test_empty_input_rejected(self):
        with pytest.raises(ValueError, match="No valid sequences"):
            fasta_to_esmfold2_json("   \n  ")


class TestIsAndValidateEsmfold2Json:
    """ESMFold2 JSON detection, validation, and token counting."""

    def test_valid_json_detected_and_validated(self):
        raw = fasta_to_esmfold2_json(f">ubq\n{PROTEIN_SEQ}")
        assert is_esmfold2_json(raw) is True
        ok, errors, warnings = validate_esmfold2_json(raw)
        assert ok is True
        assert errors == []
        assert warnings == []
        assert count_tokens(raw) == len(PROTEIN_SEQ)

    def test_fasta_and_of3_json_not_detected_as_esmfold2(self):
        assert is_esmfold2_json(f">ubq\n{PROTEIN_SEQ}") is False
        assert is_esmfold2_json('{"queries": {"q": {"chains": []}}}') is False

    def test_invalid_characters_and_missing_fields_caught(self):
        bad_json = json.dumps(
            {
                "name": "bad",
                "sequences": [
                    {"protein": {"id": "A", "sequence": "MQIFV123*"}},
                    {"protein": {"sequence": ""}},
                    {"unknown_mol": {"id": "C"}},
                ],
            }
        )
        ok, errors, _ = validate_esmfold2_json(bad_json)
        assert ok is False
        assert any("invalid characters" in e.lower() for e in errors)
        assert any("missing required field 'id'" in e for e in errors)
        assert any("unknown type" in e.lower() for e in errors)
