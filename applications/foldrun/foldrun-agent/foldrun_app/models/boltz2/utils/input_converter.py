"""Convert FASTA sequences to BOLTZ2 query YAML format."""

import re
from typing import Optional

# Standard nucleotide alphabet (RNA)
_RNA_NT = set("ACGU")
# Standard nucleotide alphabet (DNA)
_DNA_NT = set("ACGT")
# Standard + extended amino acids (includes B/Z ambiguity codes, X unknown, U selenocysteine, O pyrrolysine)
_PROTEIN_AA = set("ACDEFGHIKLMNPQRSTVWYBJOUXZ")
# Valid top-level sequence type keys in a Boltz-2 YAML
_VALID_SEQ_TYPES = {"protein", "rna", "dna", "smiles", "ccd", "ligand"}


def _detect_molecule_type(sequence: str) -> str:
    """Detect whether a sequence is protein, rna, or dna."""
    chars = set(sequence.upper())
    if "U" in chars and chars <= _RNA_NT:
        return "rna"
    if chars <= _DNA_NT and len(sequence) > 30:
        return "dna"
    return "protein"


def _validate_sequence_chars(sequence: str, mol_type: str) -> list[str]:
    """Return a list of error strings for invalid characters in a sequence.

    Args:
        sequence: Uppercase sequence string.
        mol_type: 'protein', 'rna', or 'dna'.

    Returns:
        List of error strings (empty if valid).
    """
    errors = []
    if mol_type == "protein":
        invalid = sorted(set(sequence.upper()) - _PROTEIN_AA)
        if invalid:
            errors.append(
                f"Protein sequence contains invalid characters: {', '.join(invalid)}. "
                "Expected standard amino acids (ACDEFGHIKLMNPQRSTVWY) plus ambiguity codes (BXZ) and U/O."
            )
    elif mol_type == "rna":
        invalid = sorted(set(sequence.upper()) - _RNA_NT)
        if invalid:
            errors.append(
                f"RNA sequence contains invalid characters: {', '.join(invalid)}. "
                "Expected A, C, G, U only."
            )
    elif mol_type == "dna":
        invalid = sorted(set(sequence.upper()) - _DNA_NT)
        if invalid:
            errors.append(
                f"DNA sequence contains invalid characters: {', '.join(invalid)}. "
                "Expected A, C, G, T only."
            )
    return errors


def fasta_to_boltz2_yaml(fasta_content: str, job_name: Optional[str] = None) -> str:
    """Convert FASTA content (or raw sequence) to BOLTZ2 query YAML format."""
    content = fasta_content.strip()
    if not content.startswith(">"):
        content = f">sequence\n{content}"

    parsed = []
    current_id = None
    current_seq_lines = []

    for line in content.split("\n"):
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

    errors = []
    for seq_id, sequence in parsed:
        if not sequence:
            errors.append(f"Sequence '{seq_id}' is empty after stripping non-alphabetic characters")
            continue
        if len(sequence) < 5:
            errors.append(f"Sequence '{seq_id}' is too short ({len(sequence)} residues, minimum 5)")
        mol_type = _detect_molecule_type(sequence)
        errors.extend(_validate_sequence_chars(sequence, mol_type))

    if errors:
        raise ValueError("FASTA validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    yaml_lines = ["version: 1", "sequences:"]
    for i, (seq_id, sequence) in enumerate(parsed):
        chain_id = chr(65 + i) if i < 26 else f"chain_{i}"
        mol_type = _detect_molecule_type(sequence)
        yaml_lines.append(f"  - {mol_type}:")
        yaml_lines.append(f"      id: {chain_id}")
        yaml_lines.append(f"      sequence: {sequence}")

    return "\n".join(yaml_lines)


def is_boltz2_yaml(content: str) -> bool:
    """Detect if content is already in Boltz-2 query YAML format.

    Parses the YAML rather than relying on string heuristics.
    Returns False for any parse error or missing required fields.
    """
    content = content.strip()
    if not content.startswith("version:"):
        return False
    try:
        import yaml
        data = yaml.safe_load(content)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("version") != 1:
        return False
    if not isinstance(data.get("sequences"), list) or not data["sequences"]:
        return False
    return True


def validate_boltz2_yaml(content: str) -> tuple[bool, list[str], list[str]]:
    """Validate a Boltz-2 query YAML string.

    Args:
        content: Raw YAML string.

    Returns:
        (is_valid, errors, warnings) where errors and warnings are lists of strings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        import yaml
        data = yaml.safe_load(content)
    except Exception as e:
        return False, [f"YAML parse error: {e}"], []

    if not isinstance(data, dict):
        return False, ["Top-level structure must be a mapping"], []

    # version
    version = data.get("version")
    if version is None:
        errors.append("Missing required field: 'version'")
    elif version != 1:
        errors.append(f"Unsupported version: {version!r}. Expected 1")

    # sequences
    sequences = data.get("sequences")
    if sequences is None:
        errors.append("Missing required field: 'sequences'")
    elif not isinstance(sequences, list):
        errors.append("'sequences' must be a list")
    elif not sequences:
        errors.append("'sequences' list is empty — at least one chain is required")
    else:
        for i, entry in enumerate(sequences):
            if not isinstance(entry, dict):
                errors.append(f"sequences[{i}]: must be a mapping, got {type(entry).__name__}")
                continue
            keys = set(entry.keys())
            unknown = keys - _VALID_SEQ_TYPES
            if unknown:
                errors.append(
                    f"sequences[{i}]: unknown type(s) {sorted(unknown)}. "
                    f"Valid types: {sorted(_VALID_SEQ_TYPES)}"
                )
            known = keys & _VALID_SEQ_TYPES
            if not known:
                errors.append(
                    f"sequences[{i}]: missing molecule type key. "
                    f"Valid types: {sorted(_VALID_SEQ_TYPES)}"
                )
                continue

            mol_type = next(iter(known))
            chain_data = entry[mol_type]
            if not isinstance(chain_data, dict):
                errors.append(f"sequences[{i}].{mol_type}: must be a mapping")
                continue

            # id is required
            if "id" not in chain_data:
                errors.append(f"sequences[{i}].{mol_type}: missing required field 'id'")

            # sequence-bearing types must have a sequence or ccd/smiles
            if mol_type in ("protein", "rna", "dna"):
                seq = chain_data.get("sequence", "")
                if not seq:
                    errors.append(f"sequences[{i}].{mol_type}: 'sequence' is empty or missing")
                else:
                    seq_errors = _validate_sequence_chars(str(seq), mol_type)
                    errors.extend(
                        f"sequences[{i}].{mol_type}: {e}" for e in seq_errors
                    )
                    if mol_type == "protein" and len(str(seq)) < 5:
                        warnings.append(
                            f"sequences[{i}].protein: very short sequence ({len(str(seq))} residues)"
                        )
            elif mol_type in ("smiles", "ligand"):
                if not chain_data.get("smiles") and not chain_data.get("ccd"):
                    errors.append(
                        f"sequences[{i}].{mol_type}: must provide either 'smiles' or 'ccd'"
                    )

    return len(errors) == 0, errors, warnings


def count_tokens(yaml_data: str) -> int:
    """Count total sequence tokens in a Boltz-2 query YAML.

    Uses YAML parsing when possible; falls back to line scanning.
    """
    try:
        import yaml
        data = yaml.safe_load(yaml_data)
        total = 0
        for entry in data.get("sequences", []):
            for mol_type in ("protein", "rna", "dna"):
                if mol_type in entry:
                    seq = entry[mol_type].get("sequence", "")
                    total += len(str(seq))
        return total
    except Exception:
        # Fallback: scan lines for 'sequence:' fields
        total = 0
        for line in yaml_data.split("\n"):
            line = line.strip()
            if line.startswith("sequence:"):
                total += len(line.split("sequence:", 1)[-1].strip())
        return total
