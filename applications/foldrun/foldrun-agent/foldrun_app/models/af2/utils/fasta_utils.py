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

"""FASTA file validation and parsing utilities."""

import logging
import os
import re
from typing import Dict, List, Tuple

from Bio import SeqIO

logger = logging.getLogger(__name__)


class FastaValidationError(Exception):
    """Exception raised for FASTA validation errors."""

    pass


def validate_fasta_file(fasta_path: str) -> Tuple[bool, List[Dict]]:
    """
    Validate FASTA file and extract sequence information.

    Args:
        fasta_path: Path to FASTA file

    Returns:
        Tuple of (is_monomer, sequences)
        where sequences is a list of dicts with 'description' and 'sequence'
    """
    if not os.path.exists(fasta_path):
        raise FileNotFoundError(f"FASTA file not found: {fasta_path}")

    sequences = []
    with open(fasta_path, "r") as f:
        for record in SeqIO.parse(f, "fasta"):
            sequences.append({"description": record.description, "sequence": str(record.seq)})

    if not sequences:
        raise ValueError("No sequences found in FASTA file")

    is_monomer = len(sequences) == 1

    logger.info(f"Validated FASTA: {len(sequences)} sequence(s), monomer={is_monomer}")
    return is_monomer, sequences


def write_fasta(sequences: List[Dict], output_path: str):
    """
    Write sequences to FASTA file.

    Args:
        sequences: List of dicts with 'description' and 'sequence'
        output_path: Output FASTA file path
    """
    with open(output_path, "w") as f:
        for seq in sequences:
            f.write(f">{seq['description']}\n")
            f.write(f"{seq['sequence']}\n")

    logger.info(f"Wrote {len(sequences)} sequence(s) to {output_path}")


def parse_fasta_content(fasta_content: str) -> List[Dict]:
    """
    Parse FASTA content string.

    Args:
        fasta_content: FASTA format string

    Returns:
        List of sequence dictionaries

    Raises:
        FastaValidationError: If content is invalid
    """
    if not fasta_content or not fasta_content.strip():
        raise FastaValidationError("FASTA content is empty")

    # Fix common copy-paste formatting issues (missing newlines, etc.)
    from foldrun_app.core.fasta import fix_fasta

    fasta_content = fix_fasta(fasta_content)

    sequences = []
    current_desc = None
    current_seq = []

    for line in fasta_content.strip().split("\n"):
        line = line.strip()
        if not line:  # Skip empty lines
            continue

        if line.startswith(">"):
            if current_desc:
                seq_str = "".join(current_seq)
                sequences.append({"description": current_desc, "sequence": seq_str})
            current_desc = line[1:]
            current_seq = []
        else:
            current_seq.append(line)

    if current_desc:
        seq_str = "".join(current_seq)
        sequences.append({"description": current_desc, "sequence": seq_str})

    if not sequences:
        # Handle raw sequence without FASTA header
        raw = fasta_content.strip().replace("\n", "").replace(" ", "").upper()
        if raw and all(c in "ACDEFGHIKLMNPQRSTVWY" for c in raw):
            sequences = [{"description": "sequence", "sequence": raw}]
        else:
            raise FastaValidationError("No sequences found in FASTA content")

    # Validate each sequence
    _validate_sequences(sequences)

    return sequences


def get_sequence_length(sequences: List[Dict]) -> int:
    """
    Get total sequence length.

    Args:
        sequences: List of sequence dictionaries

    Returns:
        Total amino acid length
    """
    return sum(len(seq["sequence"]) for seq in sequences)


def _validate_sequences(sequences: List[Dict]) -> None:
    """Validate sequences for common issues.

    Args:
        sequences: List of sequence dictionaries

    Raises:
        FastaValidationError: If validation fails
    """
    # Standard amino acids
    valid_aa = set("ACDEFGHIKLMNPQRSTVWYX")  # Including X for unknown

    for i, seq_dict in enumerate(sequences):
        seq = seq_dict["sequence"]
        desc = seq_dict.get("description", f"Sequence {i + 1}")

        # Check for empty sequence
        if not seq or not seq.strip():
            raise FastaValidationError(f"{desc}: Sequence is empty")

        # Remove whitespace for validation
        seq_clean = re.sub(r"\s+", "", seq).upper()

        # Check minimum length
        if len(seq_clean) < 10:
            raise FastaValidationError(
                f"{desc}: Sequence too short ({len(seq_clean)} residues). "
                "Minimum 10 residues required for AlphaFold."
            )

        # Check for invalid characters
        invalid_chars = set()
        for char in seq_clean:
            if char not in valid_aa:
                invalid_chars.add(char)

        if invalid_chars:
            raise FastaValidationError(
                f"{desc}: Invalid amino acid characters found: {', '.join(sorted(invalid_chars))}. "
                "Only standard amino acids (ACDEFGHIKLMNPQRSTVWY) are allowed."
            )

        # Update sequence to cleaned version
        seq_dict["sequence"] = seq_clean

        logger.info(f"Validated sequence '{desc}': {len(seq_clean)} residues")


def validate_sequence_for_job_type(sequences: List[Dict], is_multimer: bool) -> None:
    """Validate that sequence count matches job type.

    Args:
        sequences: List of sequence dictionaries
        is_multimer: Whether this is a multimer job

    Raises:
        FastaValidationError: If sequence count doesn't match job type
    """
    num_chains = len(sequences)

    if is_multimer and num_chains < 2:
        logger.warning(
            f"Multimer job has only {num_chains} chain. Consider using monomer submission instead."
        )

    if not is_multimer and num_chains > 1:
        raise FastaValidationError(
            f"Monomer job has {num_chains} chains. "
            "Use multimer submission for multi-chain complexes."
        )
