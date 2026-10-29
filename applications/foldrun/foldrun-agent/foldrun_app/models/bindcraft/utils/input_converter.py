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

"""Convert user inputs and structured specifications to BindCraft settings format."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def is_bindcraft_settings(content: str) -> bool:
    """Check if content is valid BindCraft settings format (JSON or YAML)."""
    if not isinstance(content, str):
        return False
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return (
                "starting_pdb" in data
                or "target_pdb" in data
                or "target_structure" in data
                or "binder_length" in data
                or "lengths" in data
            )
    except Exception:
        pass

    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return (
                "starting_pdb" in data
                or "target_pdb" in data
                or "target_structure" in data
                or "binder_length" in data
                or "lengths" in data
            )
    except Exception:
        pass

    return False


def validate_bindcraft_settings(content: str | dict) -> tuple[bool, list[str]]:
    """Validate BindCraft settings content.

    Returns:
        (is_valid, list_of_error_messages)
    """
    errors = []
    if isinstance(content, str):
        try:
            try:
                data = json.loads(content)
            except Exception:
                data = yaml.safe_load(content)
        except Exception as e:
            return False, [f"Settings parsing error: {e}"]
    elif isinstance(content, dict):
        data = content
    else:
        return False, ["Input must be a JSON/YAML string or dict."]

    if not isinstance(data, dict):
        return False, ["BindCraft settings must be a dictionary."]

    if (
        not data.get("starting_pdb")
        and not data.get("target_structure")
        and not data.get("target_pdb")
        and not data.get("target")
    ):
        errors.append(
            "BindCraft settings must specify 'starting_pdb', 'target_structure', or 'target_pdb'."
        )

    return (len(errors) == 0, errors)


def count_tokens(settings: str | dict) -> int:
    """Estimate total residues (target residues + max binder length) for hardware recommendation.

    ColabDesign backpropagation scales quadratically with residue count.
    """
    data = None
    if isinstance(settings, str):
        try:
            data = json.loads(settings)
        except Exception:
            try:
                data = yaml.safe_load(settings)
            except Exception:
                pass
    elif isinstance(settings, dict):
        data = settings

    if not isinstance(data, dict):
        return 350

    total_residues = 0

    # Binder length (supports list [65, 150] or range string "80..140" / "80-140")
    raw_length = data.get("binder_length_range", data.get("lengths", data.get("length", "80..140")))
    if isinstance(raw_length, (list, tuple)) and raw_length:
        try:
            total_residues += max(int(x) for x in raw_length)
        except (ValueError, TypeError):
            total_residues += 100
    else:
        length_str = str(raw_length)
        nums = [int(n) for n in re.findall(r"\d+", length_str)]
        total_residues += max(nums) if nums else 100

    # Target residues
    target_res = data.get("target_residue_count")
    if target_res and isinstance(target_res, int):
        total_residues += target_res
    else:
        total_residues += 250  # Average target size estimate

    return max(total_residues, 100)


def build_bindcraft_settings(
    target_structure_filename: str,
    target_chains: list[str] | None = None,
    binding_residues: list[str | int] | None = None,
    binder_length_range: str = "80..140",
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a BindCraft settings dictionary compatible with FreeBindCraft.

    Args:
        target_structure_filename: Local/staged filename of target structure (e.g. target.pdb or target.cif)
        target_chains: Target chains to bind against (e.g. ['A'])
        binding_residues: Hotspot residues (e.g. ['A:23', 'A:24'] or ['23', '24'])
        binder_length_range: Length range string (e.g. '80..140' or '60..100')
        extra_params: Additional user-provided hyper-parameters

    Returns:
        Structured settings dictionary with both FreeBindCraft native keys
        (design_path, binder_name, starting_pdb, chains, target_hotspot_residues, lengths,
        number_of_final_designs) and FoldRun compatibility keys.
    """
    chains = target_chains or ["A"]
    target_chain_str = ",".join(chains)

    match = re.search(r"(\d+)\s*(?:\.\.|\-)\s*(\d+)", str(binder_length_range))
    if match:
        lengths = [int(match.group(1)), int(match.group(2))]
    else:
        val = int(re.sub(r"[^\d]", "", str(binder_length_range)) or 100)
        lengths = [val, val]

    hotspot_str = ""
    if binding_residues:
        cleaned_hotspots = []
        for r in binding_residues:
            r_str = re.sub(r"^[A-Za-z]+:?", "", str(r).strip())
            if r_str:
                cleaned_hotspots.append(r_str)
        hotspot_str = ",".join(cleaned_hotspots)

    settings: dict[str, Any] = {
        "design_path": "./bindcraft_out",
        "binder_name": "FoldRunBinder",
        "starting_pdb": target_structure_filename,
        "chains": target_chain_str,
        "target_hotspot_residues": hotspot_str,
        "lengths": lengths,
        "number_of_final_designs": 2,
        # FoldRun compatibility aliases
        "target_pdb": target_structure_filename,
        "target_structure": target_structure_filename,
        "target_chains": target_chain_str,
        "binder_length_range": binder_length_range,
        "no_pyrosetta": True,
    }

    if binding_residues:
        settings["hotspot_residues"] = [str(r) for r in binding_residues]

    if extra_params:
        settings.update(extra_params)

    return settings
