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

"""FASTA validation utilities for AlphaFold submissions."""

import re
from typing import Dict, List


class FastaValidationError(Exception):
    """Exception raised for FASTA validation errors."""

    pass


def validate_fasta_sequence(sequence: str, is_multimer: bool = False) -> Dict[str, any]:
    """Validate a FASTA sequence string for AlphaFold submission.

    Args:
        sequence: The FASTA sequence string (can be multi-line with headers or just raw sequence)
        is_multimer: Whether this is a multimer submission (allows multiple chains)

    Returns:
        Dictionary with validation results:
        {
            'valid': bool,
            'error': str (if not valid),
            'chains': List[Dict] with chain info,
            'num_chains': int,
            'total_length': int,
            'warnings': List[str]
        }

    Raises:
        FastaValidationError: If validation fails
    """
    result = {
        "valid": True,
        "error": None,
        "chains": [],
        "num_chains": 0,
        "total_length": 0,
        "warnings": [],
    }

    if not sequence or not sequence.strip():
        raise FastaValidationError("Sequence is empty or contains only whitespace")

    # Parse FASTA format - handle both raw sequences and FASTA with headers
    chains = _parse_fasta_chains(sequence)

    if not chains:
        raise FastaValidationError("No valid sequences found in input")

    # Validate number of chains
    num_chains = len(chains)
    result["num_chains"] = num_chains

    if not is_multimer and num_chains > 1:
        raise FastaValidationError(
            f"Monomer submission has {num_chains} chains. "
            "Use submit_af2_multimer_prediction for multi-chain complexes."
        )

    if is_multimer and num_chains < 2:
        result["warnings"].append(
            f"Multimer submission has only {num_chains} chain. "
            "Consider using submit_af2_monomer_prediction instead."
        )

    # Validate each chain
    total_length = 0
    for i, chain_info in enumerate(chains):
        chain_seq = chain_info["sequence"]
        chain_name = chain_info["name"] or f"Chain_{i + 1}"

        # Check minimum length
        if len(chain_seq) < 10:
            raise FastaValidationError(
                f"{chain_name}: Sequence too short ({len(chain_seq)} residues). "
                "Minimum 10 residues required."
            )

        # Check for invalid characters
        invalid_chars = _find_invalid_amino_acids(chain_seq)
        if invalid_chars:
            raise FastaValidationError(
                f"{chain_name}: Invalid amino acid characters found: {', '.join(sorted(set(invalid_chars)))}. "
                "Only standard amino acids (ACDEFGHIKLMNPQRSTVWY) are allowed."
            )

        # Check for lowercase (common formatting issue)
        if chain_seq != chain_seq.upper():
            result["warnings"].append(
                f"{chain_name}: Contains lowercase letters - will be converted to uppercase"
            )

        # Check for spaces or other whitespace
        if any(c.isspace() for c in chain_seq):
            result["warnings"].append(
                f"{chain_name}: Contains whitespace characters - will be removed"
            )

        total_length += len(chain_seq)
        result["chains"].append(
            {"name": chain_name, "sequence": chain_seq, "length": len(chain_seq)}
        )

    result["total_length"] = total_length

    # Check total length limits
    if total_length > 5000:
        result["warnings"].append(
            f"Total sequence length ({total_length} residues) is very long. "
            "Consider using A100-80GB GPU for better memory capacity."
        )
    elif is_multimer and total_length > 2000:
        result["warnings"].append(
            f"Multimer sequence length ({total_length} residues) is long. "
            "Consider using A100 or A100-80GB GPU."
        )

    return result


def _parse_fasta_chains(sequence: str) -> List[Dict[str, str]]:
    """Parse FASTA format into individual chains.

    Handles multiple formats:
    - Standard FASTA with > headers
    - Multimer format with : separator (SEQA:SEQB)
    - Raw sequence (no headers)

    Args:
        sequence: Input sequence string

    Returns:
        List of dicts with 'name' and 'sequence' keys
    """
    chains = []

    # Fix common copy-paste formatting issues (missing newlines, etc.)
    from foldrun_app.core.fasta import fix_fasta

    sequence = fix_fasta(sequence)

    # Check if it's FASTA format with headers
    if ">" in sequence:
        # Standard FASTA format
        lines = sequence.strip().split("\n")
        current_name = None
        current_seq = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith(">"):
                # Save previous chain if exists
                if current_name is not None and current_seq:
                    seq_clean = _clean_sequence("".join(current_seq))
                    if seq_clean:
                        chains.append({"name": current_name, "sequence": seq_clean})

                # Start new chain
                current_name = line[1:].strip() or f"Chain_{len(chains) + 1}"
                current_seq = []
            else:
                current_seq.append(line)

        # Save last chain
        if current_name is not None and current_seq:
            seq_clean = _clean_sequence("".join(current_seq))
            if seq_clean:
                chains.append({"name": current_name, "sequence": seq_clean})

    # Check if it's multimer format with : separator
    elif ":" in sequence:
        parts = sequence.split(":")
        for i, part in enumerate(parts):
            seq_clean = _clean_sequence(part)
            if seq_clean:
                chains.append({"name": f"Chain_{i + 1}", "sequence": seq_clean})

    # Raw sequence (no headers)
    else:
        seq_clean = _clean_sequence(sequence)
        if seq_clean:
            chains.append({"name": "Chain_1", "sequence": seq_clean})

    return chains


def _clean_sequence(sequence: str) -> str:
    """Clean a sequence string by removing whitespace and converting to uppercase.

    Args:
        sequence: Raw sequence string

    Returns:
        Cleaned sequence string (uppercase, no whitespace)
    """
    # Remove all whitespace
    seq = re.sub(r"\s+", "", sequence)
    # Convert to uppercase
    seq = seq.upper()
    return seq


def _find_invalid_amino_acids(sequence: str) -> List[str]:
    """Find invalid amino acid characters in a sequence.

    Args:
        sequence: Amino acid sequence (should be uppercase)

    Returns:
        List of invalid characters found
    """
    # Standard amino acids (20 canonical)
    valid_aa = set("ACDEFGHIKLMNPQRSTVWY")

    # Also allow X (unknown) and U (selenocysteine, rare but valid)
    valid_aa.add("X")
    valid_aa.add("U")

    invalid = []
    for char in sequence.upper():
        if char not in valid_aa:
            invalid.append(char)

    return invalid


def format_fasta_for_submission(sequence: str, name: str = "sequence") -> str:
    """Format a sequence string as proper FASTA for submission.

    Args:
        sequence: The sequence string (can be raw or with headers)
        name: Name/description for the sequence header

    Returns:
        Properly formatted FASTA string
    """
    # Clean the sequence
    seq_clean = _clean_sequence(sequence)

    # Format as FASTA
    return f">{name}\n{seq_clean}\n"
