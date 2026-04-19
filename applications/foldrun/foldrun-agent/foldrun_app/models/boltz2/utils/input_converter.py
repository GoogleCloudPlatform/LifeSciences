"""Convert FASTA sequences to BOLTZ2 query YAML format."""

import re
from typing import Optional

# Standard nucleotide alphabet (RNA)
_RNA_NT = set("ACGU")
# Standard nucleotide alphabet (DNA)
_DNA_NT = set("ACGT")

def _detect_molecule_type(sequence: str) -> str:
    """Detect whether a sequence is protein, rna, or dna."""
    chars = set(sequence.upper())
    if "U" in chars and chars <= _RNA_NT:
        return "rna"
    if chars <= _DNA_NT and len(sequence) > 30:
        return "dna"
    return "protein"

def fasta_to_boltz2_yaml(fasta_content: str, job_name: Optional[str] = None) -> str:
    """Convert FASTA content to BOLTZ2 query YAML format."""
    parsed = []
    current_id = None
    current_seq_lines = []

    for line in fasta_content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_id is not None and current_seq_lines:
                seq = "".join(current_seq_lines).upper()
                parsed.append((current_id, seq))
            header = line[1:].strip()
            parts = header.split(None, 1)
            current_id = parts[0] if parts else f"seq_{len(parsed)}"
            current_seq_lines = []
        else:
            cleaned = re.sub(r"[^A-Za-z]", "", line)
            current_seq_lines.append(cleaned)

    if current_id is not None and current_seq_lines:
        seq = "".join(current_seq_lines).upper()
        parsed.append((current_id, seq))

    if not parsed:
        raise ValueError("No valid sequences found in FASTA input")

    yaml_lines = ["version: 1", "sequences:"]
    for i, (seq_id, sequence) in enumerate(parsed):
        chain_id = chr(65 + i) if i < 26 else f"chain_{i}"
        mol_type = _detect_molecule_type(sequence)
        
        yaml_lines.append(f"  - {mol_type}:")
        yaml_lines.append(f"      id: {chain_id}")
        yaml_lines.append(f"      sequence: {sequence}")
        
    return "\n".join(yaml_lines)

def is_boltz2_yaml(content: str) -> bool:
    """Detect if content is already BOLTZ2 query YAML format."""
    content = content.strip()
    return content.startswith("version:") and "sequences:" in content

def count_tokens(yaml_data: str) -> int:
    """Count total tokens in an BOLTZ2 query YAML."""
    total = 0
    for line in yaml_data.split("\n"):
        line = line.strip()
        if line.startswith("sequence:"):
            total += len(line.split("sequence:")[-1].strip())
    return total
