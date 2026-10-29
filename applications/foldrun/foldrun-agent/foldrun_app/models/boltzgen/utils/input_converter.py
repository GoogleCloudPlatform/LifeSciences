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

"""Convert user inputs and structured design specs to BoltzGen query YAML format."""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOLS = {
    "protein-anything",
    "peptide-anything",
    "protein-small_molecule",
    "nanobody-anything",
    "antibody-anything",
    "protein-redesign",
}


def is_boltzgen_yaml(content: str) -> bool:
    """Check if content is valid BoltzGen YAML format."""
    if not isinstance(content, str):
        return False
    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            return False
        # BoltzGen YAML typically has entities, sequences, or protocol at top-level
        return "entities" in data or "protocol" in data or "sequences" in data
    except Exception:
        return False


def validate_boltzgen_yaml(content: str | dict) -> tuple[bool, list[str]]:
    """Validate BoltzGen YAML content.

    Returns:
        (is_valid, list_of_error_messages)
    """
    errors = []
    if isinstance(content, str):
        try:
            data = yaml.safe_load(content)
        except Exception as e:
            return False, [f"YAML parsing error: {e}"]
    elif isinstance(content, dict):
        data = content
    else:
        return False, ["Input must be a YAML string or dict."]

    if not isinstance(data, dict):
        return False, ["BoltzGen specification must be a dictionary."]

    protocol = data.get("protocol")
    if protocol and protocol not in SUPPORTED_PROTOCOLS:
        errors.append(
            f"Unsupported protocol '{protocol}'. Supported: {', '.join(sorted(SUPPORTED_PROTOCOLS))}"
        )

    if "entities" not in data and "sequences" not in data:
        errors.append("BoltzGen YAML must define 'entities' or 'sequences'.")

    return (len(errors) == 0, errors)


def count_tokens(yaml_content: str | dict) -> int:
    """Estimate total tokens (residues + ligand atoms) for hardware recommendation.

    BoltzGen tokens include:
    - Target structure residues
    - Binder max length
    - Small molecule atoms
    """
    data = yaml.safe_load(yaml_content) if isinstance(yaml_content, str) else yaml_content
    if not isinstance(data, dict):
        return 1000  # Default fallback

    tokens = 0
    entities = data.get("entities", [])
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            # Design entity
            if "design" in entity:
                design = entity["design"]
                length_str = str(design.get("length", "100"))
                nums = [int(n) for n in re.findall(r"\d+", length_str)]
                tokens += max(nums) if nums else 100
            # Target protein/file entity or designed protein entity
            elif "file" in entity or "protein" in entity:
                if "protein" in entity:
                    seq_val = str(entity["protein"].get("sequence", "100"))
                    match = re.search(r"(\d+)\s*(?:\.\.|\-)\s*(\d+)", seq_val)
                    if match:
                        tokens += int(match.group(2))
                    elif seq_val.isdigit():
                        tokens += int(seq_val)
                    else:
                        tokens += len(seq_val)
                else:
                    tokens += 500  # Reasonable average target residue count
            # Ligand / small molecule
            elif "smiles" in entity or "ligand" in entity or "ccd" in entity:
                tokens += 50  # Typical small molecule atom count

    # Also check sequences list if present
    sequences = data.get("sequences", [])
    if isinstance(sequences, list):
        for seq in sequences:
            if isinstance(seq, dict):
                for val in seq.values():
                    if isinstance(val, dict) and "sequence" in val:
                        tokens += len(val["sequence"])

    return max(tokens, 150)


def build_boltzgen_yaml(
    target_structure_filename: str,
    target_chains: list[str] | None = None,
    binding_residues: list[str | int] | None = None,
    binder_length_range: str = "80..140",
    protocol: str = "protein-anything",
    ligand_smiles: str | None = None,
    ligand_ccd: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> str:
    """Build a BoltzGen query YAML string from structured parameters.

    Args:
        target_structure_filename: Local filename or path of target structure (e.g. 'target.cif').
        target_chains: Target chains to bind against (e.g. ['A']).
        binding_residues: Hotspot residues (e.g. ['23', '24'] or [23, 24] or ['A:23']).
        binder_length_range: Designed binder length range (default: '80..140').
        protocol: One of the 6 BoltzGen protocols.
        ligand_smiles: Optional SMILES string.
        ligand_ccd: Optional CCD code.
        extra_params: Optional additional dictionary keys to merge into the YAML.

    Returns:
        Formatted YAML string ready for `boltzgen check` and `boltzgen run`.
    """
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"Unsupported protocol: {protocol}. Supported: {', '.join(sorted(SUPPORTED_PROTOCOLS))}"
        )

    chains = target_chains or ["A"]

    # Parse binding residues if provided
    parsed_binding_residues: list[int] = []
    if binding_residues:
        for res in binding_residues:
            if isinstance(res, int):
                parsed_binding_residues.append(res)
            elif isinstance(res, str):
                # Clean up format like 'A:23', 'A23', or '23'
                num_str = re.sub(r"^[A-Za-z]+:?", "", res.strip())
                try:
                    parsed_binding_residues.append(int(num_str))
                except ValueError:
                    logger.warning(f"Could not parse residue number from '{res}'")

    # Designed binder protein entity (BoltzGen yaml_spec.md format)
    binder_id = "B" if "B" not in chains else "X"
    norm_length = re.sub(r"(\d+)\s*-\s*(\d+)", r"\1..\2", str(binder_length_range).strip())
    entities: list[dict[str, Any]] = [
        {
            "protein": {
                "id": binder_id,
                "sequence": norm_length,
            }
        }
    ]

    # Target structure file entity
    file_spec: dict[str, Any] = {
        "path": target_structure_filename,
        "include": [{"chain": {"id": ch}} for ch in chains],
    }
    if parsed_binding_residues:
        binding_str = ",".join(str(r) for r in sorted(parsed_binding_residues))
        file_spec["binding_types"] = [
            {"chain": {"id": ch, "binding": binding_str}} for ch in chains
        ]
    entities.append({"file": file_spec})

    # Optional ligand entity
    if ligand_smiles:
        entities.append({"ligand": {"id": "L", "smiles": ligand_smiles}})
    elif ligand_ccd:
        entities.append({"ligand": {"id": "L", "ccd": ligand_ccd}})

    query_dict: dict[str, Any] = {
        "entities": entities,
    }

    if extra_params:
        query_dict.update(extra_params)

    return yaml.safe_dump(query_dict, sort_keys=False)
