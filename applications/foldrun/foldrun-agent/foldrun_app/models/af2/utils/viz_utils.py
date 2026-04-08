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

"""Visualization utilities for AlphaFold structures."""

import logging
import pickle
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# pLDDT confidence bands
PLDDT_BANDS = [
    (0, 50, "#FF7D45", "Very low"),
    (50, 70, "#FFDB13", "Low"),
    (70, 90, "#65CBF3", "Confident"),
    (90, 100, "#0053D6", "Very high"),
]


def load_raw_prediction(pickle_path: str) -> Dict[str, Any]:
    """
    Load raw prediction pickle file.

    Args:
        pickle_path: Path to pickle file

    Returns:
        Raw prediction dictionary
    """
    with open(pickle_path, "rb") as f:
        raw_prediction = pickle.load(f)

    logger.info(f"Loaded raw prediction from {pickle_path}")
    return raw_prediction


def calculate_plddt_stats(raw_prediction: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate pLDDT statistics.

    Args:
        raw_prediction: Raw prediction dictionary

    Returns:
        pLDDT statistics
    """
    plddt_scores = raw_prediction["plddt"]

    stats = {
        "mean": float(np.mean(plddt_scores)),
        "median": float(np.median(plddt_scores)),
        "min": float(np.min(plddt_scores)),
        "max": float(np.max(plddt_scores)),
        "std": float(np.std(plddt_scores)),
        "per_residue": plddt_scores.tolist(),
    }

    # Distribution by confidence band
    distribution = {}
    for min_val, max_val, _, label in PLDDT_BANDS:
        count = np.sum((plddt_scores >= min_val) & (plddt_scores <= max_val))
        distribution[label.lower().replace(" ", "_")] = int(count)

    stats["distribution"] = distribution

    return stats


def calculate_pae_stats(raw_prediction: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Calculate PAE statistics (if available).

    Args:
        raw_prediction: Raw prediction dictionary

    Returns:
        PAE statistics or None if not available
    """
    if "predicted_aligned_error" not in raw_prediction:
        return None

    pae = raw_prediction["predicted_aligned_error"]
    max_pae = raw_prediction.get("max_predicted_aligned_error", np.max(pae))

    stats = {
        "mean": float(np.mean(pae)),
        "median": float(np.median(pae)),
        "min": float(np.min(pae)),
        "max": float(np.max(pae)),
        "max_predicted": float(max_pae),
        "matrix": pae.tolist(),
    }

    return stats


def get_quality_assessment(plddt_mean: float) -> str:
    """
    Get quality assessment based on mean pLDDT.

    Args:
        plddt_mean: Mean pLDDT score

    Returns:
        Quality assessment string
    """
    if plddt_mean >= 90:
        return "very_high_confidence"
    elif plddt_mean >= 70:
        return "high_confidence"
    elif plddt_mean >= 50:
        return "low_confidence"
    else:
        return "very_low_confidence"


def overwrite_b_factors(pdb_content: str, b_factors: np.ndarray) -> str:
    """
    Overwrite B-factors in PDB file with new values.

    Args:
        pdb_content: PDB file content as string
        b_factors: New B-factor values

    Returns:
        Modified PDB content
    """
    lines = pdb_content.split("\n")
    atom_idx = 0
    new_lines = []

    for line in lines:
        if line.startswith("ATOM"):
            if atom_idx < len(b_factors):
                # Replace B-factor (columns 61-66 in PDB format)
                new_line = line[:60] + f"{b_factors[atom_idx]:6.2f}" + line[66:]
                new_lines.append(new_line)
                atom_idx += 1
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    return "\n".join(new_lines)


def generate_plddt_colored_pdb(
    pdb_path: str, raw_prediction_path: str, output_path: Optional[str] = None
) -> str:
    """
    Generate PDB file with B-factors colored by pLDDT bands.

    Args:
        pdb_path: Path to input PDB file
        raw_prediction_path: Path to raw prediction pickle
        output_path: Output path (optional)

    Returns:
        Path to colored PDB file
    """
    # Load raw prediction
    raw_prediction = load_raw_prediction(raw_prediction_path)

    # Read PDB
    with open(pdb_path, "r") as f:
        pdb_content = f.read()

    # Calculate banded B-factors
    banded_b_factors = []
    final_atom_mask = raw_prediction["structure_module"]["final_atom_mask"]

    for plddt in raw_prediction["plddt"]:
        for idx, (min_val, max_val, _, _) in enumerate(PLDDT_BANDS):
            if plddt >= min_val and plddt <= max_val:
                banded_b_factors.append(idx)
                break

    banded_b_factors = np.array(banded_b_factors)[:, None] * final_atom_mask
    banded_b_factors = banded_b_factors.flatten()

    # Overwrite B-factors
    colored_pdb = overwrite_b_factors(pdb_content, banded_b_factors)

    # Write output
    if output_path is None:
        output_path = pdb_path.replace(".pdb", "_colored.pdb")

    with open(output_path, "w") as f:
        f.write(colored_pdb)

    logger.info(f"Generated pLDDT-colored PDB: {output_path}")
    return output_path
