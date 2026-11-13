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

"""Convert FASTA sequences to BOLTZ2 query YAML format."""

from foldrun_app.core.fasta import (
    detect_molecule_type as _detect_molecule_type,  # noqa: F401
)
from foldrun_app.core.fasta import (
    parse_multimolecule_fasta_records,
)
from foldrun_app.core.fasta import (
    validate_sequence_chars as _validate_sequence_chars,
)

# Valid top-level sequence type keys in a Boltz-2 YAML
_VALID_SEQ_TYPES = {"protein", "rna", "dna", "smiles", "ccd", "ligand"}


def fasta_to_boltz2_yaml(fasta_content: str, job_name: str | None = None) -> str:
    """Convert FASTA content (or raw sequence) to BOLTZ2 query YAML format."""
    parsed_chains = parse_multimolecule_fasta_records(fasta_content, min_length=5)

    yaml_lines = ["version: 1", "sequences:"]
    for rec in parsed_chains:
        yaml_lines.append(f"  - {rec.molecule_type}:")
        yaml_lines.append(f"      id: {rec.chain_id}")
        yaml_lines.append(f"      sequence: {rec.sequence}")

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
                    errors.extend(f"sequences[{i}].{mol_type}: {e}" for e in seq_errors)
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
