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

"""Shared FASTA preprocessing, sequence validation, and molecule-type detection across models."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Standard 20 amino acids (used for header-concatenation repair)
_VALID_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
_MIN_TRAILING_SEQ_LEN = 10

# Standard nucleotide alphabet (RNA)
RNA_NT: frozenset[str] = frozenset("ACGU")
# Standard nucleotide alphabet (DNA)
DNA_NT: frozenset[str] = frozenset("ACGT")
# Standard + extended amino acids (includes B/Z ambiguity codes, X unknown, U selenocysteine, O pyrrolysine)
PROTEIN_AA: frozenset[str] = frozenset("ACDEFGHIKLMNPQRSTVWYBJOUXZ")


@dataclass(frozen=True)
class ParsedFastaChain:
    """Normalized polymer chain record parsed from FASTA input."""

    seq_id: str
    header: str
    chain_id: str
    molecule_type: str
    sequence: str
    warning: str | None = None


def next_chain_id(index: int) -> str:
    """Generate chain identifiers A..Z, AA..ZZ."""
    if index < 26:
        return chr(ord("A") + index)
    first = chr(ord("A") + (index // 26) - 1)
    second = chr(ord("A") + (index % 26))
    return f"{first}{second}"


def validate_sequence_chars(sequence: str, mol_type: str) -> list[str]:
    """Return a list of error strings for invalid characters in a polymer sequence."""
    errors: list[str] = []
    upper = sequence.upper()
    if mol_type == "protein":
        invalid = sorted(set(upper) - PROTEIN_AA)
        if invalid:
            errors.append(
                f"Protein sequence contains invalid characters: {', '.join(invalid)}. "
                "Expected standard amino acids (ACDEFGHIKLMNPQRSTVWY) plus ambiguity codes."
            )
    elif mol_type == "rna":
        invalid = sorted(set(upper) - RNA_NT)
        if invalid:
            errors.append(
                f"RNA sequence contains invalid characters: {', '.join(invalid)}. "
                "Expected A, C, G, U only."
            )
    elif mol_type == "dna":
        invalid = sorted(set(upper) - DNA_NT)
        if invalid:
            errors.append(
                f"DNA sequence contains invalid characters: {', '.join(invalid)}. "
                "Expected A, C, G, T only."
            )
    return errors


def detect_molecule_type_with_warning(
    sequence: str,
    header: str = "",
    *,
    short_acgt_is_dna: bool = False,
) -> tuple[str, str | None]:
    """Detect whether a polymer sequence is 'protein', 'rna', or 'dna', with optional warning.

    Checks explicit FASTA header hints (e.g., '>seq|dna', 'type=rna') first, then
    inspects nucleotide and amino acid character sets.
    """
    header_lower = header.lower()
    if any(h in header_lower for h in ("type=dna", "|dna", " dna", "_dna", "-dna", "dna_")):
        return "dna", None
    if any(h in header_lower for h in ("type=rna", "|rna", " rna", "_rna", "-rna", "rna_")):
        return "rna", None
    if any(
        h in header_lower for h in ("type=protein", "|protein", " protein", "_protein", "peptide")
    ):
        return "protein", None

    chars = set(sequence.upper())
    if "U" in chars and chars <= RNA_NT:
        return "rna", None
    if chars <= DNA_NT:
        if len(sequence) > 30:
            return "dna", None
        if short_acgt_is_dna:
            warning = (
                f"Short sequence of length {len(sequence)} ({sequence}) contains only A, C, G, T. "
                "Classified as DNA; specify '>header|protein' or '>header|dna' to disambiguate."
            )
            return "dna", warning
    return "protein", None


def detect_molecule_type(sequence: str, header: str = "") -> str:
    """Detect whether a polymer sequence is 'protein', 'rna', or 'dna'."""
    mol_type, _ = detect_molecule_type_with_warning(
        sequence, header=header, short_acgt_is_dna=False
    )
    return mol_type


def parse_multimolecule_fasta_records(
    fasta_content: str,
    *,
    min_length: int = 5,
    strip_non_alpha: bool = True,
    short_acgt_is_dna: bool = False,
) -> list[ParsedFastaChain]:
    """Parse and validate multi-molecule FASTA content into normalized chain records."""
    content = fasta_content.strip()
    if not content:
        raise ValueError("No valid sequences found in FASTA input")
    if not content.startswith(">"):
        content = f">sequence\n{content}"

    raw_records: list[tuple[str, str, str]] = []
    current_id: str | None = None
    current_header = ""
    current_seq_lines: list[str] = []

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_id is not None and current_seq_lines:
                raw_records.append((current_id, current_header, "".join(current_seq_lines).upper()))
            current_header = line[1:].strip()
            parts = current_header.split(None, 1)
            raw_token = parts[0] if parts else f"seq_{len(raw_records)}"
            # Strip trailing '|dna' / '|rna' / '|protein' hint from seq_id if present
            current_id = raw_token.split("|", 1)[0] or raw_token
            current_seq_lines = []
        else:
            cleaned = (
                re.sub(r"[^A-Za-z]", "", line) if strip_non_alpha else re.sub(r"\s+", "", line)
            )
            current_seq_lines.append(cleaned)

    if current_id is not None and current_seq_lines:
        raw_records.append((current_id, current_header, "".join(current_seq_lines).upper()))

    if not raw_records:
        raise ValueError("No valid sequences found in FASTA input")

    errors: list[str] = []
    chains: list[ParsedFastaChain] = []
    for i, (seq_id, header, sequence) in enumerate(raw_records):
        if not sequence:
            errors.append(f"Sequence '{seq_id}' is empty after stripping non-alphabetic characters")
            continue
        if min_length > 0 and len(sequence) < min_length:
            errors.append(
                f"Sequence '{seq_id}' is too short ({len(sequence)} residues, minimum {min_length})"
            )
        mol_type, warning = detect_molecule_type_with_warning(
            sequence, header=header, short_acgt_is_dna=short_acgt_is_dna
        )
        errors.extend(validate_sequence_chars(sequence, mol_type))
        chains.append(
            ParsedFastaChain(
                seq_id=seq_id,
                header=header,
                chain_id=next_chain_id(i),
                molecule_type=mol_type,
                sequence=sequence,
                warning=warning,
            )
        )

    if errors:
        raise ValueError("FASTA validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    return chains


def _split_header_sequence(line: str) -> str:
    """Split a FASTA header line if a >=10-residue AA sequence is concatenated to it.

    Runs in linear O(len(line)) time without regex backtracking.
    """
    if not line.startswith(">"):
        return line

    stripped = line.rstrip()
    i = len(stripped)
    while i > 1 and stripped[i - 1] in _VALID_AA:
        i -= 1

    if len(stripped) - i >= _MIN_TRAILING_SEQ_LEN:
        return f"{stripped[:i]}\n{stripped[i:]}"
    return line


def fix_fasta(content: str) -> str:
    """Preprocess FASTA content to fix common formatting issues.

    Handles:
    - Missing newlines before '>' headers (chat copy-paste strips them)
    - Sequence concatenated onto header line (no newline after header name)
    - Missing header for leading sequence
    """
    if not content or not content.strip():
        return content

    content = re.sub(r"([^>\n])(>)", r"\1\n\2", content)
    content = "\n".join(_split_header_sequence(line) for line in content.split("\n"))

    stripped = content.strip()
    if ">" in stripped and not stripped.startswith(">"):
        content = ">chain_A\n" + content

    return content
