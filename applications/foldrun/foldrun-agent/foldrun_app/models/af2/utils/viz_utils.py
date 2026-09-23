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
import os
import pickle
import tempfile
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_ALLOWED_BUILTINS = {
    "dict",
    "list",
    "tuple",
    "set",
    "frozenset",
    "int",
    "float",
    "str",
    "bytes",
    "bool",
    "complex",
    "slice",
    "range",
}

_ALLOWED_NUMPY_MODULES = {
    "numpy",
    "numpy.core.multiarray",
    "numpy.core.numeric",
    "numpy._core.multiarray",
    "numpy._core.numeric",
}

_ALLOWED_NUMPY_NAMES = {
    "_reconstruct",
    "scalar",
    "ndarray",
    "dtype",
    "_frombuffer",
}

_ALLOWED_NUMPY_RECONSTRUCTORS = {
    getattr(getattr(np, "_core", np.core).multiarray, "_reconstruct", None),
} - {None}


def _safe_reconstruct_jax_array(
    fun: Any, args: Any, arr_state: Any, aval_state: Any = None
) -> np.ndarray:
    """Safely reconstruct a pickled JAX ArrayImpl into a NumPy ndarray."""
    if fun not in _ALLOWED_NUMPY_RECONSTRUCTORS:
        raise pickle.UnpicklingError(f"Forbidden constructor in JAX array reconstruction: {fun!r}")
    np_value = fun(*args)
    if not isinstance(np_value, np.ndarray):
        raise pickle.UnpicklingError(
            f"Expected ndarray from JAX array reconstruction, got {type(np_value).__name__}"
        )
    np_value.__setstate__(arr_state)
    if getattr(np_value.dtype, "hasobject", False):
        raise pickle.UnpicklingError("Object-dtype NumPy arrays are forbidden")
    return np_value


def _reject_object_dtype_arrays(obj: Any) -> None:
    """Recursively ensure no object-dtype NumPy arrays exist in deserialized payload."""
    if isinstance(obj, np.ndarray):
        if getattr(obj.dtype, "hasobject", False):
            raise pickle.UnpicklingError("Object-dtype NumPy arrays are forbidden")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _reject_object_dtype_arrays(k)
            _reject_object_dtype_arrays(v)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            _reject_object_dtype_arrays(item)


class _RestrictedUnpickler(pickle.Unpickler):
    """Restricted unpickler allowing only safe builtins and NumPy/JAX array primitives."""

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) == ("jax._src.array", "_reconstruct_array"):
            return _safe_reconstruct_jax_array
        if (module, name) == ("ml_dtypes", "bfloat16"):
            return super().find_class(module, name)
        if module == "builtins" and name in _ALLOWED_BUILTINS:
            return super().find_class(module, name)
        if module in _ALLOWED_NUMPY_MODULES and name in _ALLOWED_NUMPY_NAMES:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"Global '{module}.{name}' is forbidden")


def validate_safe_local_path(path: str, extra_allowed_dirs: list[str] | None = None) -> str:
    """Validate that a local filesystem path resolves within allowed directories."""
    if not path or "\x00" in path:
        raise ValueError(f"Invalid path: {path!r}")

    resolved = os.path.realpath(path)
    candidates = [
        tempfile.gettempdir(),
        os.getcwd(),
    ]
    if extra_allowed_dirs:
        candidates.extend(d for d in extra_allowed_dirs if d)

    allowed_roots = [os.path.realpath(d) for d in candidates]
    for root in allowed_roots:
        try:
            if os.path.commonpath([resolved, root]) == root:
                return resolved
        except ValueError:
            continue

    raise ValueError(f"Path '{path}' resolves outside allowed directories")


# pLDDT confidence bands
PLDDT_BANDS = [
    (0, 50, "#FF7D45", "Very low"),
    (50, 70, "#FFDB13", "Low"),
    (70, 90, "#65CBF3", "Confident"),
    (90, 100, "#0053D6", "Very high"),
]


def load_raw_prediction(pickle_path: str) -> dict[str, Any]:
    """
    Load raw prediction pickle file using restricted deserialization.

    Args:
        pickle_path: Path to pickle file

    Returns:
        Raw prediction dictionary
    """
    with open(pickle_path, "rb") as f:
        raw_prediction = _RestrictedUnpickler(f).load()

    if not isinstance(raw_prediction, dict):
        raise pickle.UnpicklingError(
            f"Expected prediction payload to be a dict, got {type(raw_prediction).__name__}"
        )

    _reject_object_dtype_arrays(raw_prediction)

    logger.info(f"Loaded raw prediction from {pickle_path}")
    return raw_prediction


def calculate_plddt_stats(raw_prediction: dict[str, Any]) -> dict[str, Any]:
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


def calculate_pae_stats(raw_prediction: dict[str, Any]) -> dict[str, Any] | None:
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
    pdb_path: str, raw_prediction_path: str, output_path: str | None = None
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
    if output_path is not None:
        output_path = validate_safe_local_path(output_path)

    # Load raw prediction
    raw_prediction = load_raw_prediction(raw_prediction_path)

    # Read PDB
    with open(pdb_path) as f:
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
        output_path = validate_safe_local_path(pdb_path.replace(".pdb", "_colored.pdb"))

    with open(output_path, "w") as f:
        f.write(colored_pdb)

    logger.info(f"Generated pLDDT-colored PDB: {output_path}")
    return output_path
