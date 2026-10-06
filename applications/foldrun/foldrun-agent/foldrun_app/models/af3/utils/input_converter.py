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

"""Convert FASTA sequences and biological entities to AlphaFold 3 query JSON format.

AlphaFold 3 input JSON schema (dialect='alphafold3', version=1):
{
  "name": "target_complex",
  "modelSeeds": [1],
  "sequences": [
    {"protein": {"id": "A", "sequence": "MKTI..."}},
    {"rna": {"id": "B", "sequence": "AGCU..."}},
    {"dna": {"id": "C", "sequence": "AGCT..."}},
    {"ligand": {"id": "D", "ccdCodes": ["ATP"]}},
    {"ligand": {"id": "E", "ccdCodes": ["MG"]}},
    {"ligand": {"id": "F", "smiles": "CC(=O)O"}}
  ],
  "dialect": "alphafold3",
  "version": 1
}
"""

import json
import re

# Standard nucleotide alphabet (RNA)
_RNA_NT = set("ACGU")
# Standard nucleotide alphabet (DNA)
_DNA_NT = set("ACGT")
# Standard + extended amino acids
_PROTEIN_AA = set("ACDEFGHIKLMNPQRSTVWYBJOUXZ")
# Supported molecule keys in AF3 entity dicts
_VALID_ENTITY_KEYS = {"protein", "rna", "dna", "ligand", "ion"}


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


def _detect_molecule_type(sequence: str, header: str = "") -> tuple[str, str | None]:
    """Detect whether a sequence is protein, rna, or dna.

    Checks explicit FASTA header hints (e.g., '>seq|dna', 'type=dna') first.
    If no hint is present, inspects nucleotide and amino acid character sets.

    Args:
        sequence: The sequence string (uppercase).
        header: Optional FASTA header line.

    Returns:
        tuple of (molecule_type, optional_warning)
    """
    warning = None
    header_lower = header.lower()

    # Explicit header hints
    if any(h in header_lower for h in ("type=dna", "|dna", " dna", "_dna", "-dna", "dna_")):
        return "dna", None
    if any(h in header_lower for h in ("type=rna", "|rna", " rna", "_rna", "-rna", "rna_")):
        return "rna", None
    if any(
        h in header_lower for h in ("type=protein", "|protein", " protein", "_protein", "peptide")
    ):
        return "protein", None

    chars = set(sequence.upper())
    if "U" in chars and chars <= _RNA_NT:
        return "rna", None
    if chars <= _DNA_NT:
        if len(sequence) > 30:
            return "dna", None
        # Short sequence consisting strictly of A, C, G, T
        warning = (
            f"Short sequence of length {len(sequence)} ({sequence}) contains only A, C, G, T. "
            "Classified as DNA; specify '>header|protein' or '>header|dna' to disambiguate."
        )
        return "dna", warning

    return "protein", None


def _next_chain_id(index: int) -> str:
    """Generate chain identifiers A..Z, AA..ZZ."""
    if index < 26:
        return chr(ord("A") + index)
    first = chr(ord("A") + (index // 26) - 1)
    second = chr(ord("A") + (index % 26))
    return f"{first}{second}"


def is_af3_json(content: str | dict) -> bool:
    """Check if the given string or dict represents an AlphaFold 3 query JSON."""
    if isinstance(content, str):
        content = content.strip()
        if not (content.startswith("{") and content.endswith("}")):
            return False
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return False
    elif isinstance(content, dict):
        data = content
    else:
        return False

    if not isinstance(data, dict):
        return False

    if data.get("dialect") == "alphafold3":
        return True

    if "sequences" in data and isinstance(data["sequences"], list):
        for item in data["sequences"]:
            if isinstance(item, dict) and any(k in _VALID_ENTITY_KEYS for k in item.keys()):
                return True

    return False


def validate_af3_json(json_content: str | dict) -> tuple[bool, list[str], list[str]]:
    """Validate an AF3 query JSON structure.

    Returns:
        tuple of (is_valid, errors, warnings)
    """
    errors = []
    warnings = []

    if isinstance(json_content, str):
        try:
            data = json.loads(json_content)
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON: {e}"], []
    elif isinstance(json_content, dict):
        data = json_content
    else:
        return False, ["Input must be a JSON string or dict"], []

    name = data.get("name")
    if not name or not isinstance(name, str):
        errors.append("Missing or invalid 'name' field in AF3 query.")
    elif not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
        warnings.append(
            f"Job name '{name}' contains special characters; alphanumeric and underscore recommended."
        )

    sequences = data.get("sequences")
    if not sequences or not isinstance(sequences, list):
        errors.append("AF3 query must include a non-empty 'sequences' list.")
        return False, errors, warnings

    seen_ids = set()
    total_tokens = 0

    for idx, entity in enumerate(sequences):
        if not isinstance(entity, dict):
            errors.append(f"Entity at index {idx} must be a dictionary.")
            continue

        keys = [k for k in entity.keys() if k in _VALID_ENTITY_KEYS]
        if len(keys) != 1:
            errors.append(
                f"Entity at index {idx} must define exactly one molecule type among: {', '.join(_VALID_ENTITY_KEYS)}."
            )
            continue

        mol_type = keys[0]
        mol_data = entity[mol_type]

        if not isinstance(mol_data, dict):
            errors.append(f"Entity data for '{mol_type}' at index {idx} must be a dictionary.")
            continue

        chain_id = mol_data.get("id")
        if not chain_id:
            errors.append(f"Entity at index {idx} missing 'id' field.")
        else:
            chain_ids = chain_id if isinstance(chain_id, list) else [chain_id]
            if not chain_ids or not all(isinstance(cid, str) and cid.strip() for cid in chain_ids):
                errors.append(
                    f"Entity at index {idx} has invalid 'id' field (expected string or list of strings)."
                )
            else:
                for cid in chain_ids:
                    if cid in seen_ids:
                        errors.append(f"Duplicate chain ID '{cid}' found in AF3 entities.")
                    else:
                        seen_ids.add(cid)

        if mol_type in ("protein", "rna", "dna"):
            seq = mol_data.get("sequence", "")
            if not seq:
                errors.append(f"{mol_type.upper()} entity '{chain_id}' has an empty sequence.")
            else:
                seq_errors = _validate_sequence_chars(seq, mol_type)
                errors.extend(seq_errors)
        elif mol_type == "ligand":
            smiles = mol_data.get("smiles")
            ccd_codes = mol_data.get("ccdCodes") or mol_data.get("ccd_codes")
            if not smiles and not ccd_codes:
                errors.append(
                    f"Ligand entity '{chain_id}' must provide either 'smiles' or 'ccdCodes'."
                )
        elif mol_type == "ion":
            ion_symbol = (
                mol_data.get("ion") or mol_data.get("ccdCodes") or mol_data.get("ccd_codes")
            )
            if not ion_symbol:
                errors.append(f"Ion entity '{chain_id}' missing 'ion' element symbol.")

    if not errors:
        total_tokens = count_af3_tokens(data)
        if total_tokens > 5000:
            warnings.append(
                f"Total complex token count ({total_tokens}) is very large; may exceed endpoint memory."
            )

    return len(errors) == 0, errors, warnings


def fasta_to_af3_json(
    fasta_content: str,
    job_name: str | None = None,
    model_seeds: list[int] | None = None,
    msa_free: bool = True,
    warnings_out: list[str] | None = None,
) -> dict:
    """Convert FASTA content or raw sequence into AF3 query JSON format.

    Args:
        fasta_content: FASTA format string (monomer or multimer) or raw sequence.
        job_name: Optional job name (defaults to 'af3_prediction').
        model_seeds: Optional model seeds list (defaults to [1]).
        msa_free: Whether to configure prediction in zero-MSA mode (default: True).
        warnings_out: Optional list to collect conversion warnings.

    Returns:
        Structured AF3 query dictionary.
    """
    text = fasta_content.strip()
    name = job_name or "af3_prediction"
    entities = []

    # Check if text contains FASTA headers
    if ">" in text:
        records = []
        current_header = ""
        current_seq = []

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if current_seq:
                    records.append((current_header, "".join(current_seq)))
                    current_seq = []
                current_header = line[1:].strip()
            else:
                current_seq.append(line)
        if current_seq:
            records.append((current_header, "".join(current_seq)))

        for i, (header, seq) in enumerate(records):
            clean_seq = re.sub(r"\s+", "", seq).upper()
            mol_type, warn = _detect_molecule_type(clean_seq, header=header)
            if warn and warnings_out is not None:
                warnings_out.append(warn)
            chain_id = _next_chain_id(i)
            entities.append({mol_type: {"id": chain_id, "sequence": clean_seq}})
    else:
        clean_seq = re.sub(r"\s+", "", text).upper()
        mol_type, warn = _detect_molecule_type(clean_seq)
        if warn and warnings_out is not None:
            warnings_out.append(warn)
        entities.append({mol_type: {"id": "A", "sequence": clean_seq}})

    return {
        "name": name,
        "modelSeeds": model_seeds or [1],
        "sequences": entities,
        "msaFree": msa_free,
        "dialect": "alphafold3",
        "version": 1,
    }


def count_af3_tokens(data: dict) -> int:
    """Count the total number of tokens in an AF3 query dictionary."""
    total = 0
    for entity in data.get("sequences", []):
        for mol_type in ("protein", "rna", "dna"):
            if mol_type in entity and "sequence" in entity[mol_type]:
                mol_data = entity[mol_type]
                cid = mol_data.get("id", "A")
                mult = len(cid) if isinstance(cid, list) else 1
                total += len(mol_data["sequence"]) * max(1, mult)
        if "ligand" in entity:
            lig = entity["ligand"]
            cid = lig.get("id", "A")
            mult = len(cid) if isinstance(cid, list) else 1
            ccd = lig.get("ccdCodes") or lig.get("ccd_codes")
            if ccd:
                ccd_len = len(ccd) if isinstance(ccd, list) else 1
                total += ccd_len * max(1, mult)
            elif "smiles" in lig:
                total += max(len(lig["smiles"]) // 2, 5) * max(1, mult)
            else:
                total += 10 * max(1, mult)
        if "ion" in entity:
            ion_data = entity["ion"]
            cid = ion_data.get("id", "A") if isinstance(ion_data, dict) else "A"
            mult = len(cid) if isinstance(cid, list) else 1
            total += 1 * max(1, mult)
    return total
