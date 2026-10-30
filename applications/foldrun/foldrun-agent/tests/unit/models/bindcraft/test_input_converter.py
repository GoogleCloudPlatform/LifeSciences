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

"""Tests for BindCraft input converter and settings validation."""

import json

from foldrun_app.models.bindcraft.utils.input_converter import (
    build_bindcraft_settings,
    count_tokens,
    is_bindcraft_settings,
    validate_bindcraft_settings,
)


class TestBindCraftInputConverter:
    """Tests for BindCraft settings handling."""

    def test_is_bindcraft_settings_valid_json(self):
        settings_json = json.dumps({"target_pdb": "target.pdb", "binder_length_range": "80..140"})
        assert is_bindcraft_settings(settings_json) is True

    def test_is_bindcraft_settings_valid_yaml(self):
        settings_yaml = """
        target_pdb: target.pdb
        binder_length_range: 80..140
        """
        assert is_bindcraft_settings(settings_yaml) is True

    def test_is_bindcraft_settings_invalid(self):
        assert is_bindcraft_settings("random text") is False
        assert is_bindcraft_settings(None) is False

    def test_validate_settings_valid(self):
        settings = {
            "target_pdb": "target.pdb",
            "target_chains": "A",
            "hotspot_residues": ["A:23", "A:24"],
        }
        is_valid, errors = validate_bindcraft_settings(settings)
        assert is_valid is True
        assert errors == []

    def test_validate_settings_missing_target(self):
        settings = {
            "binder_length_range": "80..140",
        }
        is_valid, errors = validate_bindcraft_settings(settings)
        assert is_valid is False
        assert any("target_structure" in e for e in errors)

    def test_count_tokens(self):
        settings = {
            "target_pdb": "target.pdb",
            "binder_length_range": "80..140",
            "target_residue_count": 200,
        }
        residues = count_tokens(settings)
        assert residues == 200 + 140

    def test_build_bindcraft_settings(self):
        settings = build_bindcraft_settings(
            target_structure_filename="target.pdb",
            target_chains=["A"],
            binding_residues=["A:23", "A:24"],
            binder_length_range="80..140",
        )
        assert settings["target_pdb"] == "target.pdb"
        assert settings["target_chains"] == "A"
        assert settings["hotspot_residues"] == ["A:23", "A:24"]
        assert settings["binder_length_range"] == "80..140"
        assert settings["no_pyrosetta"] is True
