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
        fasta_content: FASTA format string (one or more sequences).
        job_name: Optional query name.

    Returns:
        OF3 query JSON dictionary matching the run_openfold schema.
    """
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

    Args:
        content: Input string to check.

    Returns:
        True if content is valid OF3 JSON with 'queries' key.
    """
    content = content.strip()
    if not content.startswith("{"):
        return False
    try:
        data = json.loads(content)
        return isinstance(data, dict) and "queries" in data
    except (json.JSONDecodeError, ValueError):
        return False


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
