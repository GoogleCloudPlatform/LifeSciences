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

"""Tests for RFD4-Proteina input converter and specification validation."""

import json

from foldrun_app.models.rfd4_proteina.utils.input_converter import (
    build_rfd4_proteina_spec,
    count_tokens,
    is_rfd4_proteina_spec,
    validate_rfd4_proteina_spec,
)


class TestRFD4ProteinaInputConverter:
    """Tests for RFD4-Proteina specification handling."""

    def test_is_rfd4_proteina_spec_valid_json(self):
        spec_json = json.dumps({"design_mode": "binder_design", "target_structure": "target.cif"})
        assert is_rfd4_proteina_spec(spec_json) is True

    def test_is_rfd4_proteina_spec_valid_yaml(self):
        spec_yaml = """
        design_mode: binder_design
        target_structure: target.cif
        binder_length_range: 80..140
        """
        assert is_rfd4_proteina_spec(spec_yaml) is True

    def test_is_rfd4_proteina_spec_invalid(self):
        assert is_rfd4_proteina_spec("random plain string") is False
        assert is_rfd4_proteina_spec(None) is False

    def test_validate_spec_valid_binder_design(self):
        spec = {
            "design_mode": "binder_design",
            "target_structure": "target.cif",
            "target_chains": ["A"],
            "binding_residues": ["A:23", "A:24"],
        }
        is_valid, errors = validate_rfd4_proteina_spec(spec)
        assert is_valid is True
        assert errors == []

    def test_validate_spec_missing_target(self):
        spec = {
            "design_mode": "binder_design",
        }
        is_valid, errors = validate_rfd4_proteina_spec(spec)
        assert is_valid is False
        assert any("target_structure" in e for e in errors)

    def test_validate_spec_unsupported_mode(self):
        spec = {
            "design_mode": "invalid_mode",
            "target_structure": "target.cif",
        }
        is_valid, errors = validate_rfd4_proteina_spec(spec)
        assert is_valid is False
        assert any("Unsupported design_mode" in e for e in errors)

    def test_validate_spec_unconditional_monomer(self):
        spec = {
            "design_mode": "unconditional_monomer",
            "binder_length_range": "100..150",
        }
        is_valid, _errors = validate_rfd4_proteina_spec(spec)
        assert is_valid is True

    def test_count_tokens(self):
        spec = {
            "design_mode": "binder_design",
            "binder_length_range": "80..140",
            "target_residue_count": 350,
        }
        tokens = count_tokens(spec)
        assert tokens == 350 + 140

    def test_count_tokens_unconditional(self):
        spec = {
            "design_mode": "unconditional_monomer",
            "binder_length_range": "100..150",
        }
        tokens = count_tokens(spec)
        assert tokens == 150

    def test_build_rfd4_proteina_spec(self):
        spec = build_rfd4_proteina_spec(
            design_mode="binder_design",
            target_structure_filename="target.cif",
            target_chains=["A"],
            binding_residues=["A:23", "A:24"],
            binder_length_range="80..140",
        )
        assert spec["design_mode"] == "binder_design"
        assert spec["target_structure"] == "target.cif"
        assert spec["target_chains"] == ["A"]
        assert spec["binding_residues"] == ["A:23", "A:24"]
        assert spec["binder_length_range"] == "80..140"
