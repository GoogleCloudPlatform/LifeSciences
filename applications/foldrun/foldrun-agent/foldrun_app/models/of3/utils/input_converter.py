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

"""Convert FASTA sequences to OF3 query JSON format.

OF3 query JSON schema:
{
  "queries": {
    "<query_name>": {
      "chains": [
        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": "MKTI..."},
        {"molecule_type": "rna",     "chain_ids": ["B"], "sequence": "AGCU..."},
        {"molecule_type": "ligand",  "chain_ids": ["C"], "smiles": "CC(=O)O"}
      ]
    }
  }
}
"""

import json
import re
from typing import Optional

# Standard nucleotide alphabet (RNA)
_RNA_NT = set("ACGU")
# Standard nucleotide alphabet (DNA)
_DNA_NT = set("ACGT")
# Standard + extended amino acids
_PROTEIN_AA = set("ACDEFGHIKLMNPQRSTVWYBJOUXZ")
# Valid molecule types in OF3 JSON
_VALID_MOL_TYPES = {"protein", "rna", "dna", "ligand"}


def _validate_sequence_chars(sequence: str, mol_type: str) -> list[str]:
    """Return a list of error strings for invalid characters in a sequence."""
    errors = []
    if mol_type == "protein":
        invalid = sorted(set(sequence.upper()) - _PROTEIN_AA)
        if invalid:
            errors.append(
                f"Protein sequence contains invalid characters: {', '.join(invalid)}. "
                "Expected standard amino acids (ACDEFGHIKLMNPQRSTVWY) plus ambiguity codes."
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


def _detect_molecule_type(sequence: str) -> str:
    """Detect whether a sequence is protein, rna, or dna.

    Args:
        sequence: The sequence string (uppercase).

    Returns:
        'protein', 'rna', or 'dna'
    """
    chars = set(sequence.upper())
    # If it contains U, it's RNA
    if "U" in chars and chars <= _RNA_NT:
        return "rna"
    # If it's only ACGT and long enough, it's DNA
    if chars <= _DNA_NT and len(sequence) > 30:
        return "dna"
    # Default to protein
    return "protein"


def fasta_to_of3_json(fasta_content: str, job_name: Optional[str] = None) -> dict:
    """Convert FASTA content to OF3 query JSON format.

    Args:
        fasta_content: FASTA format string (one or more sequences) or raw sequence.
        job_name: Optional query name.

    Returns:
        OF3 query JSON dictionary matching the run_openfold schema.
    """
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

    query_name = job_name or parsed[0][0]

    # Build chains with auto-assigned chain IDs (A, B, C, ...)
    chains = []
    for i, (seq_id, sequence) in enumerate(parsed):
        chain_id = chr(65 + i) if i < 26 else f"chain_{i}"
        mol_type = _detect_molecule_type(sequence)
        chains.append(
            {
                "molecule_type": mol_type,
                "chain_ids": [chain_id],
                "sequence": sequence,
            }
        )

    return {
        "queries": {
            query_name: {
                "chains": chains,
            }
        }
    }


def is_of3_json(content: str) -> bool:
    """Detect if content is already OF3 query JSON format.

    Parses and validates structure rather than relying on string heuristics.
    Returns False for any parse error or missing required fields.
    """
    content = content.strip()
    if not content.startswith("{"):
        return False
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict) or "queries" not in data:
        return False
    queries = data["queries"]
    if not isinstance(queries, dict) or not queries:
        return False
    # At least one query must have a 'chains' list
    for query_data in queries.values():
        if isinstance(query_data, dict) and isinstance(query_data.get("chains"), list):
            return True
    return False


def validate_of3_json(content: str) -> tuple[bool, list[str], list[str]]:
    """Validate an OF3 query JSON string.

    Args:
        content: Raw JSON string.

    Returns:
        (is_valid, errors, warnings) where errors and warnings are lists of strings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        return False, [f"JSON parse error: {e}"], []

    if not isinstance(data, dict):
        return False, ["Top-level structure must be a JSON object"], []

    queries = data.get("queries")
    if not isinstance(queries, dict) or not queries:
        return False, ["Missing or empty 'queries' object"], []

    for query_name, query_data in queries.items():
        if not isinstance(query_data, dict):
            errors.append(f"queries.{query_name}: must be a JSON object")
            continue

        chains = query_data.get("chains")
        if not isinstance(chains, list) or not chains:
            errors.append(f"queries.{query_name}: 'chains' must be a non-empty list")
            continue

        for i, chain in enumerate(chains):
            if not isinstance(chain, dict):
                errors.append(f"queries.{query_name}.chains[{i}]: must be a JSON object")
                continue

            mol_type = chain.get("molecule_type")
            if not mol_type:
                errors.append(f"queries.{query_name}.chains[{i}]: missing 'molecule_type'")
                continue
            if mol_type not in _VALID_MOL_TYPES:
                errors.append(
                    f"queries.{query_name}.chains[{i}]: unknown molecule_type '{mol_type}'. "
                    f"Valid types: {sorted(_VALID_MOL_TYPES)}"
                )

            chain_ids = chain.get("chain_ids")
            if not chain_ids:
                errors.append(f"queries.{query_name}.chains[{i}]: missing 'chain_ids'")

            if mol_type in ("protein", "rna", "dna"):
                seq = chain.get("sequence", "")
                if not seq:
                    errors.append(
                        f"queries.{query_name}.chains[{i}].{mol_type}: 'sequence' is empty or missing"
                    )
                else:
                    seq_errors = _validate_sequence_chars(str(seq), mol_type)
                    errors.extend(
                        f"queries.{query_name}.chains[{i}]: {e}" for e in seq_errors
                    )
                    if mol_type == "protein" and len(str(seq)) < 5:
                        warnings.append(
                            f"queries.{query_name}.chains[{i}].protein: "
                            f"very short sequence ({len(str(seq))} residues)"
                        )
            elif mol_type == "ligand":
                if not chain.get("smiles") and not chain.get("ccd_codes"):
                    errors.append(
                        f"queries.{query_name}.chains[{i}].ligand: "
                        "must provide either 'smiles' or 'ccd_codes'"
                    )
                ccd_codes = chain.get("ccd_codes")
                if ccd_codes:
                    if isinstance(ccd_codes, str):
                        pass
                    elif isinstance(ccd_codes, list):
                        if not all(isinstance(c, str) for c in ccd_codes):
                            errors.append(
                                f"queries.{query_name}.chains[{i}].ligand: all items in 'ccd_codes' must be strings"
                            )
                    else:
                        errors.append(
                            f"queries.{query_name}.chains[{i}].ligand: 'ccd_codes' must be a string or a list of strings"
                        )

    return len(errors) == 0, errors, warnings


def count_tokens(query_json: dict) -> int:
    """Count total tokens in an OF3 query JSON.

    Tokens = residues (protein) + nucleotides (RNA/DNA).
    Ligands (SMILES) are not counted as sequence tokens.

    Args:
        query_json: OF3 query JSON dictionary.

    Returns:
        Total token count.
    """
    total = 0
    for query_name, query_data in query_json.get("queries", {}).items():
        for chain in query_data.get("chains", []):
            total += len(chain.get("sequence", ""))
    return total
