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

"""Convert user inputs and design specifications for RFD4-Proteina."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SUPPORTED_DESIGN_MODES = {
    "binder_design",
    "motif_scaffolding",
    "unconditional_monomer",
}


def is_rfd4_proteina_spec(content: str) -> bool:
    """Check if content is valid RFD4-Proteina specification (JSON or YAML)."""
    if not isinstance(content, str):
        return False
    try:
        # Try JSON first
        data = json.loads(content)
        if isinstance(data, dict):
            return "design_mode" in data or "binder_length" in data or "target_structure" in data
    except Exception:
        pass

    try:
        # Try YAML
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return "design_mode" in data or "binder_length" in data or "target_structure" in data
    except Exception:
        pass

    return False


def validate_rfd4_proteina_spec(content: str | dict) -> tuple[bool, list[str]]:
    """Validate RFD4-Proteina design specification.

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    if isinstance(content, str):
        try:
            try:
                data = json.loads(content)
            except Exception:
                data = yaml.safe_load(content)
        except Exception as e:
            return False, [f"Specification parsing error: {e}"]
    elif isinstance(content, dict):
        data = content
    else:
        return False, ["Input must be a JSON/YAML string or dict."]

    if not isinstance(data, dict):
        return False, ["RFD4-Proteina specification must be a dictionary."]

    mode = data.get("design_mode", "binder_design")
    if mode not in SUPPORTED_DESIGN_MODES:
        errors.append(
            f"Unsupported design_mode '{mode}'. Supported: {', '.join(sorted(SUPPORTED_DESIGN_MODES))}"
        )

    if mode in ("binder_design", "motif_scaffolding"):
        if not data.get("target_structure") and not data.get("target_pdb"):
            errors.append(f"Design mode '{mode}' requires 'target_structure' to be specified.")

    return (len(errors) == 0, errors)


def count_tokens(spec_content: str | dict) -> int:
    """Estimate total tokens (residues) for hardware recommendation.

    RFD4-Proteina tokens include:
    - Target structure residues (estimated at ~500 if not parsed)
    - Designed length (max length in range)
    """
    data = None
    if isinstance(spec_content, str):
        try:
            data = json.loads(spec_content)
        except Exception:
            try:
                data = yaml.safe_load(spec_content)
            except Exception:
                pass
    elif isinstance(spec_content, dict):
        data = spec_content

    if not isinstance(data, dict):
        return 600

    tokens = 0
    mode = data.get("design_mode", "binder_design")

    # Binder / designed length
    length_str = str(data.get("binder_length_range", data.get("length", "80..140")))
    nums = [int(n) for n in re.findall(r"\d+", length_str)]
    tokens += max(nums) if nums else 100

    # Target residues
    if mode in ("binder_design", "motif_scaffolding"):
        target_res_count = data.get("target_residue_count")
        if target_res_count and isinstance(target_res_count, int):
            tokens += target_res_count
        else:
            tokens += 500  # Conservative estimate for target protein

    return max(tokens, 100)


def build_rfd4_proteina_spec(
    design_mode: str = "binder_design",
    target_structure_filename: str | None = None,
    target_chains: list[str] | None = None,
    binding_residues: list[str | int] | None = None,
    scaffold_motif: str | None = None,
    binder_length_range: str = "80..140",
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct an RFD4-Proteina specification dictionary.

    Args:
        design_mode: 'binder_design', 'motif_scaffolding', or 'unconditional_monomer'
        target_structure_filename: Name of target structure file staged in run dir
        target_chains: Target chains to bind against
        binding_residues: Interface/hotspot residues (e.g. ['A:23', 'A:24'])
        scaffold_motif: Fixed motif specification
        binder_length_range: Length range, e.g. '80..140'
        extra_params: Additional user-provided hyper-parameters

    Returns:
        Structured specification dict.
    """
    spec: dict[str, Any] = {
        "design_mode": design_mode,
        "binder_length_range": binder_length_range,
    }

    if design_mode in ("binder_design", "motif_scaffolding"):
        if target_structure_filename:
            spec["target_structure"] = target_structure_filename
        if target_chains:
            spec["target_chains"] = target_chains
        if binding_residues:
            spec["binding_residues"] = [str(r) for r in binding_residues]

    if design_mode == "motif_scaffolding" and scaffold_motif:
        spec["scaffold_motif"] = scaffold_motif

    if extra_params:
        spec["parameters"] = extra_params

    return spec
