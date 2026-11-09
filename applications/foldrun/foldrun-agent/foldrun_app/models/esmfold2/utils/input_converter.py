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

"""Convert FASTA sequences to ESMFold2 query JSON format and validate inputs.

ESMFold2 query JSON schema:
{
  "name": "<job_name>",
  "sequences": [
    {"protein": {"id": "A", "sequence": "MKTAY...", "modifications": [{"position": 0, "ccd": "SEP"}]}},
    {"rna": {"id": "B", "sequence": "AUGCU..."}},
    {"dna": {"id": "C", "sequence": "ATGC..."}},
    {"ligand": {"id": "L", "ccd": ["SAH"]}},
    {"ligand": {"id": "M", "smiles": "CC(=O)O"}}
  ]
}
"""

from __future__ import annotations

import json
import re
from typing import Any

from foldrun_app.core.fasta import (
    detect_molecule_type as _detect_molecule_type,  # noqa: F401
)
from foldrun_app.core.fasta import (
    next_chain_id,
    parse_multimolecule_fasta_records,
)
from foldrun_app.core.fasta import (
    validate_sequence_chars as _validate_sequence_chars,
)

_VALID_SEQ_TYPES = {"protein", "rna", "dna", "ligand"}


def fasta_to_esmfold2_json(fasta_content: str, job_name: str | None = None) -> str:
    """Convert FASTA content (or raw sequence) to ESMFold2 query JSON string.

    Supports protein, RNA, DNA, and ligand FASTA records (including headers like
    ``>L|ligand|ccd=ATP`` or ``>L|smiles`` where the body is a SMILES string).
    """
    content = (fasta_content or "").strip()
    if not content:
        raise ValueError("No valid sequences found in FASTA input")

    # Check if any record explicitly declares a ligand/ccd/smiles in its header
    has_ligand_header = bool(
        re.search(r"^>.*(?:\bligand\b|\bsmiles\b|\bccd[=:])", content, re.IGNORECASE | re.MULTILINE)
    )
    if not has_ligand_header:
        parsed_chains = parse_multimolecule_fasta_records(content, min_length=5)
        sequences: list[dict[str, Any]] = [
            {
                rec.molecule_type: {
                    "id": rec.chain_id,
                    "sequence": rec.sequence,
                }
            }
            for rec in parsed_chains
        ]
        name = job_name or parsed_chains[0].seq_id or "esmfold2_query"
        return json.dumps({"name": name, "sequences": sequences}, indent=2)

    # Mixed polymer + ligand FASTA parsing
    raw_blocks: list[tuple[str, str]] = []
    cur_header = ""
    cur_lines: list[str] = []
    for line in content.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(">"):
            if cur_header or cur_lines:
                raw_blocks.append((cur_header, "".join(cur_lines)))
            cur_header = s[1:].strip()
            cur_lines = []
        else:
            cur_lines.append(s)
    if cur_header or cur_lines:
        raw_blocks.append((cur_header, "".join(cur_lines)))

    sequences = []
    polymer_lines: list[str] = []
    chain_idx = 0
    for header, body in raw_blocks:
        h_lower = header.lower()
        cid = next_chain_id(chain_idx)
        chain_idx += 1
        ccd_match = re.search(r"\bccd[=:]([A-Za-z0-9_]+)", header, re.IGNORECASE)
        if ccd_match:
            sequences.append({"ligand": {"id": cid, "ccd": [ccd_match.group(1).upper()]}})
        elif "smiles" in h_lower:
            sequences.append({"ligand": {"id": cid, "smiles": body.strip()}})
        elif "ligand" in h_lower:
            token = body.strip()
            if re.fullmatch(r"[A-Za-z0-9]{1,5}", token):
                sequences.append({"ligand": {"id": cid, "ccd": [token.upper()]}})
            else:
                sequences.append({"ligand": {"id": cid, "smiles": token}})
        else:
            polymer_lines.append(f">{header}\n{body}")
            parsed_single = parse_multimolecule_fasta_records(f">{header}\n{body}", min_length=5)[0]
            sequences.append(
                {
                    parsed_single.molecule_type: {
                        "id": cid,
                        "sequence": parsed_single.sequence,
                    }
                }
            )

    name = job_name or "esmfold2_query"
    return json.dumps({"name": name, "sequences": sequences}, indent=2)


def is_esmfold2_json(content: str) -> bool:
    """Return True if ``content`` is a JSON string matching the ESMFold2 query schema."""
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped.startswith("{"):
        return False
    try:
        data = json.loads(stripped)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    sequences = data.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        return False
    for item in sequences:
        if not isinstance(item, dict) or not (set(item.keys()) & _VALID_SEQ_TYPES):
            return False
    return True


def validate_esmfold2_json(content: str) -> tuple[bool, list[str], list[str]]:
    """Validate an ESMFold2 query JSON string.

    Returns:
        ``(is_valid, errors, warnings)``
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        data = json.loads(content)
    except Exception as e:
        return False, [f"JSON parse error: {e}"], []

    if not isinstance(data, dict):
        return False, ["Top-level structure must be a JSON object"], []

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
                errors.append(f"sequences[{i}]: must be a JSON object, got {type(entry).__name__}")
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
                errors.append(f"sequences[{i}].{mol_type}: must be a JSON object")
                continue

            cid = chain_data.get("id")
            if cid is None or (isinstance(cid, (str, list)) and not cid):
                errors.append(f"sequences[{i}].{mol_type}: missing required field 'id'")

            if mol_type in ("protein", "rna", "dna"):
                seq = chain_data.get("sequence", "")
                if not seq or not isinstance(seq, str):
                    errors.append(f"sequences[{i}].{mol_type}: 'sequence' is empty or missing")
                else:
                    seq_errors = _validate_sequence_chars(seq.strip(), mol_type)
                    errors.extend(f"sequences[{i}].{mol_type}: {e}" for e in seq_errors)
                    if mol_type == "protein" and len(seq.strip()) < 5:
                        warnings.append(
                            f"sequences[{i}].protein: very short sequence ({len(seq.strip())} residues)"
                        )
                mods = chain_data.get("modifications")
                if mods is not None:
                    if not isinstance(mods, list):
                        errors.append(f"sequences[{i}].{mol_type}.modifications: must be a list")
                    else:
                        for m_idx, mod in enumerate(mods):
                            if (
                                not isinstance(mod, dict)
                                or "position" not in mod
                                or "ccd" not in mod
                            ):
                                errors.append(
                                    f"sequences[{i}].{mol_type}.modifications[{m_idx}]: "
                                    "must contain 'position' (int) and 'ccd' (str)"
                                )
            elif mol_type == "ligand":
                has_ccd = bool(chain_data.get("ccd"))
                has_smiles = bool(chain_data.get("smiles"))
                if not has_ccd and not has_smiles:
                    errors.append(f"sequences[{i}].ligand: must provide either 'ccd' or 'smiles'")
                if has_ccd and has_smiles:
                    errors.append(f"sequences[{i}].ligand: cannot specify both 'ccd' and 'smiles'")

    return len(errors) == 0, errors, warnings


def count_tokens(json_content: str | dict[str, Any]) -> int:
    """Count total sequence tokens in an ESMFold2 query JSON."""
    try:
        data = json.loads(json_content) if isinstance(json_content, str) else json_content
        if not isinstance(data, dict):
            return 0
        total = 0
        for entry in data.get("sequences", []):
            if not isinstance(entry, dict):
                continue
            for mol_type in ("protein", "rna", "dna"):
                if mol_type in entry and isinstance(entry[mol_type], dict):
                    c_data = entry[mol_type]
                    seq = str(c_data.get("sequence", ""))
                    cid = c_data.get("id")
                    mult = len(cid) if isinstance(cid, list) and cid else 1
                    total += len(seq) * mult
            if "ligand" in entry and isinstance(entry["ligand"], dict):
                c_data = entry["ligand"]
                cid = c_data.get("id")
                mult = len(cid) if isinstance(cid, list) and cid else 1
                total += 10 * mult
        return total
    except Exception:
        return 0
