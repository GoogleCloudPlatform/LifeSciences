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

"""Tests for BoltzGen input converter and YAML builders."""

import yaml

from foldrun_app.models.boltzgen.utils.input_converter import (
    build_boltzgen_yaml,
    count_tokens,
    is_boltzgen_yaml,
    validate_boltzgen_yaml,
)


class TestBoltzGenInputConverter:
    """Tests for BoltzGen input converter and validation functions."""

    def test_is_boltzgen_yaml_valid(self):
        sample_yaml = """
version: 1
protocol: protein-anything
entities:
  - file:
      path: target.cif
    chains:
      - id: A
        binding_residues: [23, 24]
  - design:
      protocol: protein-anything
      length: 80..140
      id: B
"""
        assert is_boltzgen_yaml(sample_yaml) is True

    def test_is_boltzgen_yaml_invalid(self):
        assert is_boltzgen_yaml(">seq1\nACDEFGHIKLMNPQRSTVWY") is False
        assert is_boltzgen_yaml("just random text") is False
        assert is_boltzgen_yaml(123) is False

    def test_validate_boltzgen_yaml_valid(self):
        sample_yaml = """
version: 1
protocol: protein-anything
entities:
  - file:
      path: target.cif
  - design:
      protocol: protein-anything
      length: 80..140
"""
        ok, errors = validate_boltzgen_yaml(sample_yaml)
        assert ok is True
        assert len(errors) == 0

    def test_validate_boltzgen_yaml_unsupported_protocol(self):
        sample_yaml = """
version: 1
protocol: invalid-protocol
entities:
  - file:
      path: target.cif
"""
        ok, errors = validate_boltzgen_yaml(sample_yaml)
        assert ok is False
        assert any("Unsupported protocol" in e for e in errors)

    def test_validate_boltzgen_yaml_missing_entities(self):
        sample_yaml = """
version: 1
protocol: protein-anything
"""
        ok, errors = validate_boltzgen_yaml(sample_yaml)
        assert ok is False
        assert any("entities" in e for e in errors)

    def test_count_tokens_with_range(self):
        sample_yaml = """
version: 1
protocol: protein-anything
entities:
  - file:
      path: target.cif
    chains:
      - id: A
  - design:
      length: 80..120
"""
        tokens = count_tokens(sample_yaml)
        # 500 (target fallback) + 120 (max binder length) = 620
        assert tokens >= 620

    def test_build_boltzgen_yaml_defaults(self):
        yaml_str = build_boltzgen_yaml(
            target_structure_filename="target.cif",
            target_chains=["A"],
            binding_residues=["23", "A:24"],
            binder_length_range="80..140",
            protocol="protein-anything",
        )
        data = yaml.safe_load(yaml_str)
        assert len(data["entities"]) == 2
        assert data["entities"][0]["protein"]["id"] == "B"
        assert data["entities"][0]["protein"]["sequence"] == "80..140"
        assert data["entities"][1]["file"]["path"] == "target.cif"
        assert data["entities"][1]["file"]["include"][0]["chain"]["id"] == "A"
        assert data["entities"][1]["file"]["binding_types"][0]["chain"]["binding"] == "23,24"

    def test_build_boltzgen_yaml_small_molecule(self):
        yaml_str = build_boltzgen_yaml(
            target_structure_filename="target.cif",
            target_chains=["A"],
            protocol="protein-small_molecule",
            ligand_smiles="CC(=O)NC1=CC=C(C=C1)O",
        )
        data = yaml.safe_load(yaml_str)
        assert len(data["entities"]) == 3
        assert data["entities"][2]["ligand"]["smiles"] == "CC(=O)NC1=CC=C(C=C1)O"
