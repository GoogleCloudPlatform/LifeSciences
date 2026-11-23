# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utility functions that encapsulate AlphaFold inference components.

Two byte-identical copies of this module are maintained in the repository:
  1. src/alphafold-components/src/components/alphafold_utils.py
     Baked into the Docker container image (via Dockerfile:
     ADD src/components/alphafold_utils.py) and executed inside Agent Platform
     Pipeline tasks at runtime.
  2. foldrun-agent/foldrun_app/models/af2/pipeline/components/alphafold_utils.py
     Reference copy used for IDE support, type checking, and unit testing in
     the agent codebase.

Both copies must remain byte-for-byte identical (enforced by
test_alphafold_utils_copies_remain_identical).
"""

import glob
import hashlib
import json
import logging
import multiprocessing
import os
import pickle
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from alphafold.common import protein, residue_constants
from alphafold.data import parsers, pipeline_multimer, templates
from alphafold.data.pipeline import make_msa_features, make_sequence_features
from alphafold.data.tools import hhblits, hhsearch, hmmsearch, jackhmmer
from alphafold.model import config, data, model
from alphafold.relax import relax

JACKHMMER_BINARY_PATH = shutil.which("jackhmmer")
HHBLITS_BINARY_PATH = shutil.which("hhblits")
HHSEARCH_BINARY_PATH = shutil.which("hhsearch")
HMMSEARCH_BINARY_PATH = shutil.which("hmmsearch")
KALIGN_BINARY_PATH = shutil.which("kalign")
HMMBUILD_BINARY_PATH = shutil.which("hmmbuild")
MMSEQS2_BINARY_PATH: str = shutil.which("mmseqs") or "mmseqs"

MAX_TEMPLATE_HITS = 20
UNIREF_MAX_HITS = 10000
MGNIFY_MAX_HITS = 501
UNIPROT_MAX_HITS = 50000

logger = logging.getLogger(__name__)


def _load_features(features_path: str) -> dict[str, str]:
    """Loads pickeled features."""
    with open(features_path, "rb") as f:
        features = pickle.load(f)
    return features


def _read_msa(msa_path: str, msa_format: str) -> str:
    """Reads and parses an MSA file."""
    if os.path.exists(msa_path):
        with open(msa_path) as f:
            msa = f.read()
        if msa_format == "sto":
            msa = parsers.parse_stockholm(msa)
        elif msa_format == "a3m":
            msa = parsers.parse_a3m(msa)
        else:
            raise RuntimeError(f"Unsupported MSA format: {msa_format}")
    return msa


def _read_sequence(sequence_path: str) -> tuple[str, str, int]:
    """Reads and parses a FASTA sequence file."""
    with open(sequence_path) as f:
        sequence_str = f.read()
    sequences, sequence_descs = parsers.parse_fasta(sequence_str)
    if len(sequences) != 1:
        raise ValueError(f"More than one input sequence found in {sequence_path}.")
    return sequences[0], sequence_descs[0], len(sequences[0])


def _read_template_features(template_features_path) -> dict[str, str]:
    """Reads and unpickles a pdb structure."""
    with open(template_features_path, "rb") as f:
        template_features = pickle.load(f)
    return template_features


def _resolve_chain_msa_dir(msa_output_path: str, desc: str, chain_idx: int) -> str:
    """Resolve and validate a per-chain MSA output directory inside msa_output_path.

    Sanitizes the FASTA description into a safe single-component directory name
    and enforces canonical path containment within ``msa_output_path``.
    """
    safe_desc = re.sub(r"[^a-zA-Z0-9._-]", "_", desc).strip("._")
    if not safe_desc:
        safe_desc = f"chain_{chain_idx}"
    safe_desc = safe_desc[:128]

    real_msa_root = os.path.realpath(msa_output_path)
    chain_msa_dir = os.path.join(real_msa_root, safe_desc)
    real_chain_dir = os.path.realpath(chain_msa_dir)
    if (
        os.path.commonpath([real_msa_root, real_chain_dir]) != real_msa_root
        or real_chain_dir == real_msa_root
    ):
        raise ValueError(
            f"Resolved chain MSA directory {real_chain_dir!r} escapes msa_output_path {real_msa_root!r}"
        )
    return real_chain_dir


def _write_a3m_as_sto(a3m_str: str, sto_path: str):
    """Convert A3M alignment to minimal Stockholm format for template search."""
    lines = a3m_str.strip().split("\n")
    names = []
    seqs = []
    seen_names: dict[str, int] = {}
    current_name = None
    current_seq = []
    for line in lines:
        if line.startswith(">"):
            if current_name is not None:
                names.append(current_name)
                seqs.append("".join(current_seq))
            tokens = line[1:].split()
            base_name = tokens[0] if tokens else f"seq_{len(names)}"
            count = seen_names.get(base_name, 0)
            seen_names[base_name] = count + 1
            current_name = base_name if count == 0 else f"{base_name}_{count}"
            current_seq = []
        else:
            # Remove lowercase insertions for Stockholm format
            current_seq.append("".join(c for c in line if not c.islower()))
    if current_name is not None:
        names.append(current_name)
        seqs.append("".join(current_seq))

    with open(sto_path, "w") as f:
        f.write("# STOCKHOLM 1.0\n")
        f.writelines(f"{name}\t{seq}\n" for name, seq in zip(names, seqs))
        f.write("//\n")


def _make_empty_template_features(num_res: int) -> dict[str, np.ndarray]:
    """Build empty template feature arrays with valid ranks/shapes for num_res."""
    n_atom_types = getattr(residue_constants, "atom_type_num", 37)
    template_dtypes = getattr(templates, "TEMPLATE_FEATURES", {})
    return {
        "template_aatype": np.zeros(
            (0, num_res, 22), dtype=template_dtypes.get("template_aatype", np.float32)
        ),
        "template_all_atom_masks": np.zeros(
            (0, num_res, n_atom_types),
            dtype=template_dtypes.get("template_all_atom_masks", np.float32),
        ),
        "template_all_atom_positions": np.zeros(
            (0, num_res, n_atom_types, 3),
            dtype=template_dtypes.get("template_all_atom_positions", np.float32),
        ),
        "template_domain_names": np.empty(
            (0,), dtype=template_dtypes.get("template_domain_names", np.object_)
        ),
        "template_sequence": np.empty(
            (0,), dtype=template_dtypes.get("template_sequence", np.object_)
        ),
        "template_sum_probs": np.zeros(
            (0, 1), dtype=template_dtypes.get("template_sum_probs", np.float32)
        ),
    }


def _normalize_template_features(
    raw_template_features: Mapping[str, np.ndarray], num_res: int
) -> dict[str, np.ndarray]:
    """Ensure zero-hit template features have 3D/4D shapes expected by AF2."""
    aatype = raw_template_features.get("template_aatype")
    if aatype is None or aatype.size == 0 or aatype.ndim < 3:
        return _make_empty_template_features(num_res)
    return dict(raw_template_features)


def _make_all_seq_msa_features(msa) -> dict[str, np.ndarray]:
    """Construct *_all_seq MSA pairing features from a parsed MSA."""
    all_seq_features = make_msa_features([msa])
    msa_pairing_mod = getattr(pipeline_multimer, "msa_pairing", None)
    base_msa_feats = getattr(
        msa_pairing_mod,
        "MSA_FEATURES",
        ("msa", "msa_mask", "deletion_matrix", "deletion_matrix_int"),
    )
    valid_feats = (*base_msa_feats, "msa_species_identifiers")
    return {f"{k}_all_seq": v for k, v in all_seq_features.items() if k in valid_feats}


def _get_pdb_chain_ids() -> str | tuple | list:
    """Return canonical AF2 PDB chain ID sequence ('ABCDEFGHIJKLMNOPQRSTUVWXYZ...')."""
    for candidate in (
        getattr(pipeline_multimer, "PDB_CHAIN_IDS", None),
        getattr(protein, "PDB_CHAIN_IDS", None),
    ):
        if isinstance(candidate, (str, list, tuple)) and len(candidate) > 0:
            return candidate
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _assemble_multimer_features(
    per_chain_monomer_features: list[tuple[str, dict]],
) -> dict:
    """Convert per-chain monomer features with unique chain IDs, add assembly features, pair/merge, and pad."""
    all_chain_features = {}
    for chain_id, chain_feat in per_chain_monomer_features:
        converted = pipeline_multimer.convert_monomer_features(chain_feat, chain_id=chain_id)
        all_chain_features[chain_id] = converted

    all_chain_features = pipeline_multimer.add_assembly_features(all_chain_features)
    fp_mod = getattr(pipeline_multimer, "feature_processing", None)
    pair_and_merge_fn = getattr(fp_mod, "pair_and_merge", None) or getattr(
        pipeline_multimer, "_pair_and_merge", None
    )
    if pair_and_merge_fn is None:
        raise RuntimeError("pair_and_merge function not found on pipeline_multimer")
    np_example = pair_and_merge_fn(all_chain_features=all_chain_features)
    pad_msa_fn = getattr(pipeline_multimer, "pad_msa", None)
    if pad_msa_fn is not None:
        np_example = pad_msa_fn(np_example, 512)
    return np_example


def _run_single_sequence_fallback(
    fasta_path: str,
    run_multimer_system: bool,
    msa_output_path: str,
    features_output_path: str,
) -> tuple[dict, dict[str, int]]:
    """Generates single-sequence template-free features when reference databases are missing."""
    logger.warning(
        "Reference sequence databases not found on NFS; falling back to single-sequence "
        "template-free feature generation (core mode)."
    )
    os.makedirs(msa_output_path, exist_ok=True)
    with open(fasta_path, encoding="utf-8") as f:
        fasta_str = f.read()
    sequences, sequence_descs = parsers.parse_fasta(fasta_str)
    sequences = [s.strip().upper() for s in sequences]
    if not sequences:
        raise ValueError(f"No input sequences found in {fasta_path}.")
    if any(not s for s in sequences):
        raise ValueError(f"Empty FASTA record found in {fasta_path}.")
    if not run_multimer_system and len(sequences) != 1:
        raise ValueError(f"Expected 1 sequence for monomer in {fasta_path}, got {len(sequences)}.")

    if not run_multimer_system:
        seq, seq_desc, num_res = sequences[0], sequence_descs[0], len(sequences[0])
        a3m_str = f">{seq_desc}\n{seq}\n"
        sto_path = os.path.join(msa_output_path, "uniref90_hits.sto")
        _write_a3m_as_sto(a3m_str, sto_path)
        sequence_features = make_sequence_features(
            sequence=seq, description=seq_desc, num_res=num_res
        )
        msa_features = make_msa_features(msas=[parsers.parse_a3m(a3m_str)])
        template_features = _make_empty_template_features(num_res)
        feature_dict = {
            **sequence_features,
            **msa_features,
            **template_features,
        }
        with open(features_output_path, "wb") as f:
            pickle.dump(feature_dict, f, protocol=4)
        return feature_dict, {
            os.path.join(os.path.basename(msa_output_path), "uniref90_hits.sto"): 1,
            "single_sequence_fallback": 1,
        }

    # Multimer system fallback
    pdb_chain_ids = _get_pdb_chain_ids()
    if len(sequences) > len(pdb_chain_ids):
        raise ValueError(
            f"Cannot process more chains ({len(sequences)}) than PDB_CHAIN_IDS ({len(pdb_chain_ids)})."
        )

    is_homomer_or_monomer = len(set(sequences)) == 1
    per_chain_features: list[tuple[str, dict]] = []
    for chain_idx, (seq, desc) in enumerate(zip(sequences, sequence_descs)):
        chain_id = pdb_chain_ids[chain_idx]
        chain_fasta = os.path.join(msa_output_path, f"chain_{chain_id}.fasta")
        with open(chain_fasta, "w") as f:
            f.write(f">{desc}\n{seq}\n")

        dir_label = f"{chain_id}_{desc}" if sequence_descs.count(desc) > 1 else desc
        chain_msa_dir = _resolve_chain_msa_dir(msa_output_path, dir_label, chain_idx)
        os.makedirs(chain_msa_dir, exist_ok=True)

        a3m_str = f">{desc}\n{seq}\n"
        sto_path = os.path.join(chain_msa_dir, "uniref90_hits.sto")
        _write_a3m_as_sto(a3m_str, sto_path)
        with open(sto_path) as f:
            sto_str = f.read()
        sto_msa = parsers.parse_stockholm(sto_str)

        sequence_features = make_sequence_features(sequence=seq, description=desc, num_res=len(seq))
        msa_features = make_msa_features(msas=[parsers.parse_a3m(a3m_str)])
        template_features = _make_empty_template_features(len(seq))
        chain_feat = {
            **sequence_features,
            **msa_features,
            **template_features,
        }
        if not is_homomer_or_monomer:
            chain_feat.update(_make_all_seq_msa_features(sto_msa))
        per_chain_features.append((chain_id, chain_feat))

    feature_dict = _assemble_multimer_features(per_chain_features)

    with open(features_output_path, "wb") as f:
        pickle.dump(feature_dict, f, protocol=4)

    msas_metadata = {}
    folders = [
        os.path.join(msa_output_path, d)
        for d in os.listdir(msa_output_path)
        if os.path.isdir(os.path.join(msa_output_path, d))
    ]
    for folder in folders:
        for file in os.listdir(folder):
            filepath = os.path.join(folder, file)
            if os.path.isfile(filepath):
                key = os.path.join(folder.split(os.sep)[-1], file)
                msas_metadata[key] = 1
    msas_metadata["single_sequence_fallback"] = 1

    return feature_dict, msas_metadata


def _has_af2_raw_databases(
    *,
    run_multimer_system: bool,
    use_small_bfd: bool,
    uniref90_database_path: str,
    mgnify_database_path: str,
    bfd_database_path: str,
    small_bfd_database_path: str,
    uniref30_database_path: str,
    uniprot_database_path: str,
    pdb70_database_path: str,
    seqres_database_path: str,
    mmcif_path: str,
) -> bool:
    """Return True if all AF2 reference databases required by the preset exist."""
    if not (
        os.path.exists(uniref90_database_path)
        and os.path.exists(mgnify_database_path)
        and os.path.isdir(mmcif_path)
    ):
        return False
    if use_small_bfd:
        if not os.path.exists(small_bfd_database_path):
            return False
    elif not (
        glob.glob(f"{glob.escape(bfd_database_path)}_*")
        and glob.glob(f"{glob.escape(uniref30_database_path)}_*")
    ):
        return False
    if run_multimer_system:
        return os.path.exists(seqres_database_path) and os.path.exists(uniprot_database_path)
    return bool(glob.glob(f"{glob.escape(pdb70_database_path)}_*"))


def _db_size_bytes(path_or_paths: str | list[str]) -> int:
    """Return effective linear-scan size in bytes for database paths or HH-suite prefixes.

    For HH-suite database prefixes (BFD / UniRef30), ``hhblits`` linearly prefilters against
    ``*_cs219.ffdata`` (~20-60 GB) rather than scanning the full ``*_a3m.ffdata`` /
    ``*_hhm.ffdata`` archives (~1.7 TB). Measuring ``cs219.ffdata`` avoids starving concurrent
    ``jackhmmer`` searches to ``--cpu 1`` under ``use_small_bfd=False``.
    """
    paths = [path_or_paths] if isinstance(path_or_paths, str) else list(path_or_paths)
    total = 0
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isfile(path):
                total += os.path.getsize(path)
            else:
                cs219 = f"{path}_cs219.ffdata"
                if os.path.isfile(cs219):
                    total += os.path.getsize(cs219)
                else:
                    escaped = glob.escape(path)
                    for matched in glob.glob(f"{escaped}_*"):
                        if matched.endswith(("_a3m.ffdata", "_hhm.ffdata")):
                            continue
                        if os.path.isfile(matched):
                            total += os.path.getsize(matched)
        except OSError:
            continue
    return total


def _resolve_search_cpus(
    jobs: list[tuple[str, int, float]],
    total_cpus: int,
) -> list[int]:
    """Allocate vCPUs across concurrent MSA search jobs proportional to database size.

    Jobs are expected to be ordered largest-first and run with ``max_workers = min(2, len(jobs))``.
    A single job receives all ``total_cpus``; two or more jobs split ``total_cpus`` across the
    initial concurrent pair proportional to on-disk database bytes (falling back to nominal weights
    when files are smaller than 1 MiB, e.g. unit-test stubs), guaranteeing ``>= 1`` vCPU per job
    and ``cpu0 + cpu1 == total_cpus``. Any queued trailing job inherits ``min(cpu0, cpu1)`` so
    peak active threads never exceed ``total_cpus`` while ``jobs[0]`` is still running.
    """
    if not jobs:
        return []
    if len(jobs) == 1:
        return [max(1, total_cpus)]
    if total_cpus <= 1:
        return [1] * len(jobs)

    min_real_db_bytes = 1_048_576  # 1 MiB threshold vs tiny unit-test stubs
    size0, size1 = jobs[0][1], jobs[1][1]
    if size0 >= min_real_db_bytes and size1 >= min_real_db_bytes:
        w0, w1 = float(size0), float(size1)
    else:
        w0 = max(0.01, float(jobs[0][2]))
        w1 = max(0.01, float(jobs[1][2]))

    cpu0 = round(total_cpus * w0 / (w0 + w1))
    cpu0 = max(1, min(total_cpus - 1, cpu0))
    cpu1 = total_cpus - cpu0
    trailing_cpus = min(cpu0, cpu1)
    return [cpu0, cpu1] + [trailing_cpus] * (len(jobs) - 2)


def _truncate_stockholm_msa(sto_str: str, max_sequences: int) -> str:
    """Truncate a Stockholm alignment string to at most ``max_sequences`` unique sequences.

    Preserves header/footer lines (``# STOCKHOLM 1.0``, ``#=GF``, ``#=GC``, ``//``) and
    interleaved blocks for the first ``max_sequences`` sequence identifiers while filtering
    ``#=GS``/``#=GR`` annotations and alignment rows for dropped sequences. Returns ``sto_str``
    untouched when it already has ``<= max_sequences`` unique sequences.
    """
    if max_sequences <= 0 or not sto_str:
        return sto_str

    lines = sto_str.splitlines()
    kept_names: set[str] = set()
    exceeded = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped == "//":
            continue
        seq_name = stripped.split(None, 1)[0]
        if seq_name not in kept_names:
            if len(kept_names) >= max_sequences:
                exceeded = True
                break
            kept_names.add(seq_name)

    if not exceeded:
        return sto_str

    out_lines: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped == "//":
            out_lines.append(raw_line)
        elif stripped.startswith(("#=GS", "#=GR")):
            parts = stripped.split(None, 2)
            if len(parts) >= 2 and parts[1] in kept_names:
                out_lines.append(raw_line)
        elif stripped.startswith("#"):
            out_lines.append(raw_line)
        else:
            seq_name = stripped.split(None, 1)[0]
            if seq_name in kept_names:
                out_lines.append(raw_line)

    suffix = "\n" if sto_str.endswith("\n") else ""
    return "\n".join(out_lines) + suffix


def _promote_shared_msa_dir(seq_dir: str, seq_cache_dir: str, run_id: str) -> None:
    """Atomically promote a staged MSA directory into ``shared_msas_cache``.

    Attempts an atomic ``os.rename`` first; if ``seq_cache_dir`` already exists (e.g. populated by
    another model or concurrent run), atomically merges only missing files via per-file
    ``{dst}.{run_id}.tmp`` staging and ``os.replace``, then cleans up ``seq_dir``.
    """
    # Drop any 0-byte staged files prior to promotion so os.rename never exposes empty files.
    for fname in os.listdir(seq_dir):
        src_file = os.path.join(seq_dir, fname)
        if os.path.isfile(src_file) and os.path.getsize(src_file) == 0:
            os.remove(src_file)

    try:
        os.rename(seq_dir, seq_cache_dir)
        logger.info("Promoted shared protein MSAs to %s", seq_cache_dir)
    except (FileExistsError, OSError):
        logger.info(
            "Shared MSA cache directory already exists at %s; atomically merging missing files.",
            seq_cache_dir,
        )
        os.makedirs(seq_cache_dir, exist_ok=True)
        try:
            for fname in os.listdir(seq_dir):
                src_file = os.path.join(seq_dir, fname)
                dst_file = os.path.join(seq_cache_dir, fname)
                dst_missing_or_empty = not os.path.exists(dst_file) or (
                    os.path.isfile(dst_file) and os.path.getsize(dst_file) == 0
                )
                if (
                    os.path.isfile(src_file)
                    and os.path.getsize(src_file) > 0
                    and dst_missing_or_empty
                ):
                    tmp_dst = f"{dst_file}.{run_id}.tmp"
                    try:
                        shutil.copy2(src_file, tmp_dst)
                        os.replace(tmp_dst, dst_file)
                    except BaseException:
                        if os.path.exists(tmp_dst):
                            os.remove(tmp_dst)
                        raise
        finally:
            shutil.rmtree(seq_dir, ignore_errors=True)


def _run_template_search_for_chain(
    *,
    sequence: str,
    uniref90_sto_truncated: str,
    run_multimer_system: bool,
    template_searcher,
    template_featurizer,
    chain_msa_dir: str,
) -> tuple[str, int, dict[str, np.ndarray]]:
    """Run HHsearch (monomer) or Hmmsearch (multimer) template search and featurization."""
    t0 = time.time()
    msa_for_templates = parsers.deduplicate_stockholm_msa(uniref90_sto_truncated)
    msa_for_templates = parsers.remove_empty_columns_from_stockholm_msa(msa_for_templates)

    if run_multimer_system:
        hits_str = template_searcher.query(msa_for_templates)
        hits_filename = "pdb_hits.sto"
        hits_count = len(parsers.parse_stockholm(hits_str))
    else:
        msa_for_templates = parsers.convert_stockholm_to_a3m(msa_for_templates)
        hits_str = template_searcher.query(msa_for_templates)
        hits_filename = "pdb_hits.hhr"
        hits_count = len(parsers.parse_hhr(hits_str))

    hits_path = os.path.join(chain_msa_dir, hits_filename)
    with open(hits_path, "w") as f:
        f.write(hits_str)

    template_hits = template_searcher.get_template_hits(
        output_string=hits_str, input_sequence=sequence
    )
    templates_result = template_featurizer.get_templates(
        query_sequence=sequence, hits=template_hits
    )
    normalized_templates = _normalize_template_features(
        templates_result.features, num_res=len(sequence)
    )
    logger.info(
        "Completed template search (%s): %d hits in %.1fs",
        hits_filename,
        len(template_hits),
        time.time() - t0,
    )
    return hits_filename, hits_count, normalized_templates


def _run_parallel_single_chain(
    *,
    sequence: str,
    description: str,
    chain_msa_dir: str,
    run_multimer_system: bool,
    run_uniprot: bool,
    use_small_bfd: bool,
    uniref90_database_path: str,
    mgnify_database_path: str,
    bfd_database_path: str,
    small_bfd_database_path: str,
    uniref30_database_path: str,
    uniprot_database_path: str,
    template_searcher,
    template_featurizer,
    shared_msa_cache_dir: str | None = None,
    shared_msa_tmp_dir: str | None = None,
    total_cpus: int | None = None,
) -> tuple[dict, dict[str, int]]:
    """Run AF2 MSA + template searches concurrently for one chain with shared NFS cache reuse."""
    os.makedirs(chain_msa_dir, exist_ok=True)
    sequence = sequence.strip().upper()
    num_res = len(sequence)
    seq_hash = hashlib.sha256(sequence.encode()).hexdigest()
    cpus = total_cpus if total_cpus is not None else (os.cpu_count() or 8)
    run_id = str(uuid.uuid4())[:12]

    # Check unified cross-model shared protein MSA cache (uniref90 + mgnify Stockholm files)
    uniref90_sto: str | None = None
    mgnify_sto: str | None = None
    has_cached_uniref90 = False
    has_cached_mgnify = False

    if shared_msa_cache_dir:
        seq_cache_dir = os.path.join(shared_msa_cache_dir, f"protein_{seq_hash}")
        cached_uniref90 = os.path.join(seq_cache_dir, "uniref90_hits.sto")
        cached_mgnify = os.path.join(seq_cache_dir, "mgnify_hits.sto")
        if os.path.isfile(cached_uniref90) and os.path.getsize(cached_uniref90) > 0:
            with open(cached_uniref90) as f:
                uniref90_sto = f.read()
            has_cached_uniref90 = True
            logger.info("Shared MSA cache hit for uniref90 (protein_%s)", seq_hash)
        if os.path.isfile(cached_mgnify) and os.path.getsize(cached_mgnify) > 0:
            with open(cached_mgnify) as f:
                mgnify_sto = f.read()
            has_cached_mgnify = True
            logger.info("Shared MSA cache hit for mgnify (protein_%s)", seq_hash)

    # Build independent MSA search specs, ordered largest DB first so max_workers=2 keeps all
    # vCPUs busy and overlaps smaller searches + template search behind the largest DB.
    msa_specs: list[tuple[str, int, float]] = []
    if not has_cached_mgnify:
        msa_specs.append(("mgnify", _db_size_bytes(mgnify_database_path), 2.0))
    if not has_cached_uniref90:
        msa_specs.append(("uniref90", _db_size_bytes(uniref90_database_path), 1.0))
    if use_small_bfd:
        msa_specs.append(("small_bfd", _db_size_bytes(small_bfd_database_path), 0.3))
    else:
        msa_specs.append(("bfd", _db_size_bytes([bfd_database_path, uniref30_database_path]), 4.0))
    if run_uniprot:
        msa_specs.append(("uniprot", _db_size_bytes(uniprot_database_path), 1.8))

    min_real_db_bytes = 1_048_576
    msa_specs.sort(
        key=lambda spec: (spec[1] if spec[1] >= min_real_db_bytes else 0, spec[2]),
        reverse=True,
    )
    alloc_cpus = _resolve_search_cpus(msa_specs, cpus)

    results_lock = threading.Lock()
    search_outputs: dict[str, str] = {}
    template_result_holder: list[tuple[str, int, dict[str, np.ndarray]]] = []
    uniref90_trunc_holder: list[str] = []

    if has_cached_uniref90 and uniref90_sto is not None:
        uniref90_trunc_holder.append(_truncate_stockholm_msa(uniref90_sto, UNIREF_MAX_HITS))

    with tempfile.TemporaryDirectory(prefix="af2_query_") as query_tmp_dir:
        query_fasta = os.path.join(query_tmp_dir, "query.fasta")
        with open(query_fasta, "w") as f:
            f.write(f">protein_{seq_hash}\n{sequence}\n")

        failed = threading.Event()

        def _exec_msa_job(db_name: str, job_cpus: int) -> None:
            if failed.is_set():
                return
            try:
                logger.info("Running %s search (--cpu %d)", db_name, job_cpus)
                job_t0 = time.time()
                if db_name == "uniref90":
                    runner = jackhmmer.Jackhmmer(
                        binary_path=JACKHMMER_BINARY_PATH,
                        database_path=uniref90_database_path,
                        n_cpu=job_cpus,
                    )
                    sto_str = runner.query(query_fasta)[0]["sto"]
                    logger.info(
                        "Completed uniref90 (--cpu %d) in %.1fs", job_cpus, time.time() - job_t0
                    )
                    sto_trunc = _truncate_stockholm_msa(sto_str, UNIREF_MAX_HITS)
                    with results_lock:
                        search_outputs["uniref90"] = sto_str
                        uniref90_trunc_holder.append(sto_trunc)
                    if not failed.is_set():
                        tpl_res = _run_template_search_for_chain(
                            sequence=sequence,
                            uniref90_sto_truncated=sto_trunc,
                            run_multimer_system=run_multimer_system,
                            template_searcher=template_searcher,
                            template_featurizer=template_featurizer,
                            chain_msa_dir=chain_msa_dir,
                        )
                        with results_lock:
                            template_result_holder.append(tpl_res)
                elif db_name == "mgnify":
                    runner = jackhmmer.Jackhmmer(
                        binary_path=JACKHMMER_BINARY_PATH,
                        database_path=mgnify_database_path,
                        n_cpu=job_cpus,
                    )
                    sto_str = runner.query(query_fasta)[0]["sto"]
                    logger.info(
                        "Completed mgnify (--cpu %d) in %.1fs", job_cpus, time.time() - job_t0
                    )
                    with results_lock:
                        search_outputs["mgnify"] = sto_str
                elif db_name == "small_bfd":
                    runner = jackhmmer.Jackhmmer(
                        binary_path=JACKHMMER_BINARY_PATH,
                        database_path=small_bfd_database_path,
                        n_cpu=job_cpus,
                    )
                    sto_str = runner.query(query_fasta)[0]["sto"]
                    logger.info(
                        "Completed small_bfd (--cpu %d) in %.1fs", job_cpus, time.time() - job_t0
                    )
                    with results_lock:
                        search_outputs["small_bfd"] = sto_str
                elif db_name == "bfd":
                    runner = hhblits.HHBlits(
                        binary_path=HHBLITS_BINARY_PATH,
                        databases=[bfd_database_path, uniref30_database_path],
                        n_cpu=job_cpus,
                    )
                    a3m_str = runner.query(query_fasta)[0]["a3m"]
                    logger.info(
                        "Completed bfd+uniref30 (--cpu %d) in %.1fs",
                        job_cpus,
                        time.time() - job_t0,
                    )
                    with results_lock:
                        search_outputs["bfd"] = a3m_str
                elif db_name == "uniprot":
                    runner = jackhmmer.Jackhmmer(
                        binary_path=JACKHMMER_BINARY_PATH,
                        database_path=uniprot_database_path,
                        n_cpu=job_cpus,
                    )
                    sto_str = runner.query(query_fasta)[0]["sto"]
                    logger.info(
                        "Completed uniprot (--cpu %d) in %.1fs", job_cpus, time.time() - job_t0
                    )
                    with results_lock:
                        search_outputs["uniprot"] = sto_str
                else:
                    raise ValueError(f"Unknown MSA database job: {db_name}")
            except BaseException:
                failed.set()
                raise

        def _exec_cached_template_search() -> None:
            if failed.is_set():
                return
            try:
                tpl_res = _run_template_search_for_chain(
                    sequence=sequence,
                    uniref90_sto_truncated=uniref90_trunc_holder[0],
                    run_multimer_system=run_multimer_system,
                    template_searcher=template_searcher,
                    template_featurizer=template_featurizer,
                    chain_msa_dir=chain_msa_dir,
                )
                with results_lock:
                    template_result_holder.append(tpl_res)
            except BaseException:
                failed.set()
                raise

        total_tasks = len(msa_specs) + (1 if has_cached_uniref90 else 0)
        max_workers = min(2, total_tasks)
        if max_workers <= 1:
            for (db_name, _, _), job_cpus in zip(msa_specs, alloc_cpus, strict=True):
                _exec_msa_job(db_name, job_cpus)
            if has_cached_uniref90:
                _exec_cached_template_search()
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(_exec_msa_job, db_name, job_cpus)
                    for (db_name, _, _), job_cpus in zip(msa_specs, alloc_cpus, strict=True)
                ]
                if has_cached_uniref90:
                    futures.append(pool.submit(_exec_cached_template_search))
                try:
                    for fut in as_completed(futures):
                        fut.result()
                except BaseException:
                    failed.set()
                    for f in futures:
                        f.cancel()
                    raise

    if not has_cached_uniref90:
        uniref90_sto = search_outputs["uniref90"]
    if not has_cached_mgnify:
        mgnify_sto = search_outputs["mgnify"]

    assert uniref90_sto is not None and mgnify_sto is not None

    # Promote untruncated uniref90/mgnify Stockholm alignments to shared_msas_cache
    if shared_msa_cache_dir and (not has_cached_uniref90 or not has_cached_mgnify):
        normalized_cache_dir = os.path.normpath(shared_msa_cache_dir)
        tmp_base = shared_msa_tmp_dir or os.path.join(
            os.path.dirname(normalized_cache_dir), "shared_msas_tmp"
        )
        os.makedirs(shared_msa_cache_dir, exist_ok=True)
        os.makedirs(tmp_base, exist_ok=True)
        seq_dir = os.path.join(tmp_base, f"protein_{seq_hash}_{run_id}")
        seq_cache_dir = os.path.join(shared_msa_cache_dir, f"protein_{seq_hash}")
        try:
            os.makedirs(seq_dir, exist_ok=True)
            if uniref90_sto:
                with open(os.path.join(seq_dir, "uniref90_hits.sto"), "w") as f:
                    f.write(uniref90_sto)
            if mgnify_sto:
                with open(os.path.join(seq_dir, "mgnify_hits.sto"), "w") as f:
                    f.write(mgnify_sto)
            _promote_shared_msa_dir(seq_dir, seq_cache_dir, run_id)
        except BaseException:
            shutil.rmtree(seq_dir, ignore_errors=True)
            raise

    # Truncate in-memory to AF2 limits and persist chain MSA artifacts
    uniref90_sto_trunc = (
        uniref90_trunc_holder[0]
        if uniref90_trunc_holder
        else _truncate_stockholm_msa(uniref90_sto, UNIREF_MAX_HITS)
    )
    mgnify_sto_trunc = _truncate_stockholm_msa(mgnify_sto, MGNIFY_MAX_HITS)

    with open(os.path.join(chain_msa_dir, "uniref90_hits.sto"), "w") as f:
        f.write(uniref90_sto_trunc)
    with open(os.path.join(chain_msa_dir, "mgnify_hits.sto"), "w") as f:
        f.write(mgnify_sto_trunc)

    uniref90_msa = parsers.parse_stockholm(uniref90_sto_trunc)
    mgnify_msa = parsers.parse_stockholm(mgnify_sto_trunc)

    if use_small_bfd:
        bfd_filename = "small_bfd_hits.sto"
        bfd_sto = search_outputs["small_bfd"]
        with open(os.path.join(chain_msa_dir, bfd_filename), "w") as f:
            f.write(bfd_sto)
        bfd_msa = parsers.parse_stockholm(bfd_sto)
    else:
        bfd_filename = "bfd_uniref_hits.a3m"
        bfd_a3m = search_outputs["bfd"]
        with open(os.path.join(chain_msa_dir, bfd_filename), "w") as f:
            f.write(bfd_a3m)
        bfd_msa = parsers.parse_a3m(bfd_a3m)

    hits_filename, hits_count, template_features = template_result_holder[0]

    sequence_features = make_sequence_features(
        sequence=sequence, description=description, num_res=num_res
    )
    msa_features = make_msa_features((uniref90_msa, bfd_msa, mgnify_msa))
    chain_features = {
        **sequence_features,
        **msa_features,
        **template_features,
    }

    dir_name = os.path.basename(os.path.normpath(chain_msa_dir))
    chain_msas_metadata: dict[str, int] = {
        os.path.join(dir_name, "uniref90_hits.sto"): len(uniref90_msa),
        os.path.join(dir_name, "mgnify_hits.sto"): len(mgnify_msa),
        os.path.join(dir_name, bfd_filename): len(bfd_msa),
        os.path.join(dir_name, hits_filename): hits_count,
    }

    if run_uniprot:
        uniprot_sto_trunc = _truncate_stockholm_msa(search_outputs["uniprot"], UNIPROT_MAX_HITS)
        with open(os.path.join(chain_msa_dir, "uniprot_hits.sto"), "w") as f:
            f.write(uniprot_sto_trunc)
        uniprot_msa = parsers.parse_stockholm(uniprot_sto_trunc)
        chain_features.update(_make_all_seq_msa_features(uniprot_msa))
        chain_msas_metadata[os.path.join(dir_name, "uniprot_hits.sto")] = len(uniprot_msa)

    return chain_features, chain_msas_metadata


def run_data_pipeline(
    fasta_path: str,
    run_multimer_system: bool,
    uniref90_database_path: str,
    mgnify_database_path: str,
    bfd_database_path: str,
    small_bfd_database_path: str,
    uniref30_database_path: str,
    uniprot_database_path: str,
    pdb70_database_path: str,
    obsolete_pdbs_path: str,
    seqres_database_path: str,
    mmcif_path: str,
    max_template_date: str,
    msa_output_path: str,
    features_output_path: str,
    use_small_bfd: bool,
    shared_msa_cache_dir: str | None = None,
    shared_msa_tmp_dir: str | None = None,
) -> tuple[dict, dict[str, int]]:
    """Runs AlphaFold data pipeline with parallel MSA searches and shared NFS cache reuse."""
    if not _has_af2_raw_databases(
        run_multimer_system=run_multimer_system,
        use_small_bfd=use_small_bfd,
        uniref90_database_path=uniref90_database_path,
        mgnify_database_path=mgnify_database_path,
        bfd_database_path=bfd_database_path,
        small_bfd_database_path=small_bfd_database_path,
        uniref30_database_path=uniref30_database_path,
        uniprot_database_path=uniprot_database_path,
        pdb70_database_path=pdb70_database_path,
        seqres_database_path=seqres_database_path,
        mmcif_path=mmcif_path,
    ):
        return _run_single_sequence_fallback(
            fasta_path=fasta_path,
            run_multimer_system=run_multimer_system,
            msa_output_path=msa_output_path,
            features_output_path=features_output_path,
        )

    os.makedirs(msa_output_path, exist_ok=True)
    with open(fasta_path, encoding="utf-8") as f:
        fasta_str = f.read()
    raw_sequences, sequence_descs = parsers.parse_fasta(fasta_str)
    sequences = [s.strip().upper() for s in raw_sequences]
    if not sequences:
        raise ValueError(f"No input sequences found in {fasta_path}.")
    if any(not s for s in sequences):
        raise ValueError(f"Empty FASTA record found in {fasta_path}.")
    if not run_multimer_system and len(sequences) != 1:
        raise ValueError(f"Expected 1 sequence for monomer in {fasta_path}, got {len(sequences)}.")

    if run_multimer_system:
        template_searcher = hmmsearch.Hmmsearch(
            binary_path=HMMSEARCH_BINARY_PATH,
            hmmbuild_binary_path=HMMBUILD_BINARY_PATH,
            database_path=seqres_database_path,
        )
        template_featurizer = templates.HmmsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path,
        )
    else:
        template_searcher = hhsearch.HHSearch(
            binary_path=HHSEARCH_BINARY_PATH, databases=[pdb70_database_path]
        )
        template_featurizer = templates.HhsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path,
        )

    msas_metadata: dict[str, int] = {}
    if not run_multimer_system:
        feature_dict, msas_metadata = _run_parallel_single_chain(
            sequence=sequences[0],
            description=sequence_descs[0],
            chain_msa_dir=msa_output_path,
            run_multimer_system=False,
            run_uniprot=False,
            use_small_bfd=use_small_bfd,
            uniref90_database_path=uniref90_database_path,
            mgnify_database_path=mgnify_database_path,
            bfd_database_path=bfd_database_path,
            small_bfd_database_path=small_bfd_database_path,
            uniref30_database_path=uniref30_database_path,
            uniprot_database_path=uniprot_database_path,
            template_searcher=template_searcher,
            template_featurizer=template_featurizer,
            shared_msa_cache_dir=shared_msa_cache_dir,
            shared_msa_tmp_dir=shared_msa_tmp_dir,
        )
    else:
        pdb_chain_ids = _get_pdb_chain_ids()
        if len(sequences) > len(pdb_chain_ids):
            raise ValueError(
                f"Cannot process more chains ({len(sequences)}) than PDB_CHAIN_IDS ({len(pdb_chain_ids)})."
            )

        is_homomer_or_monomer = len(set(sequences)) == 1
        processed_chains: dict[str, dict] = {}
        per_chain_features: list[tuple[str, dict]] = []

        for chain_idx, (seq, desc) in enumerate(zip(sequences, sequence_descs, strict=True)):
            chain_id = pdb_chain_ids[chain_idx]
            if seq in processed_chains:
                logger.info(
                    "Reusing in-memory MSA and template features for duplicate chain %s",
                    chain_id,
                )
                chain_feat = {
                    k: v.copy() if isinstance(v, np.ndarray) else v
                    for k, v in processed_chains[seq].items()
                }
                chain_feat["domain_name"] = np.array([desc.encode("utf-8")], dtype=np.object_)
                per_chain_features.append((chain_id, chain_feat))
                continue

            chain_msa_dir = _resolve_chain_msa_dir(msa_output_path, chain_id, chain_idx)
            chain_feat, chain_meta = _run_parallel_single_chain(
                sequence=seq,
                description=desc,
                chain_msa_dir=chain_msa_dir,
                run_multimer_system=True,
                run_uniprot=not is_homomer_or_monomer,
                use_small_bfd=use_small_bfd,
                uniref90_database_path=uniref90_database_path,
                mgnify_database_path=mgnify_database_path,
                bfd_database_path=bfd_database_path,
                small_bfd_database_path=small_bfd_database_path,
                uniref30_database_path=uniref30_database_path,
                uniprot_database_path=uniprot_database_path,
                template_searcher=template_searcher,
                template_featurizer=template_featurizer,
                shared_msa_cache_dir=shared_msa_cache_dir,
                shared_msa_tmp_dir=shared_msa_tmp_dir,
            )
            processed_chains[seq] = chain_feat
            msas_metadata.update(chain_meta)
            per_chain_features.append((chain_id, chain_feat))

        feature_dict = _assemble_multimer_features(per_chain_features)

    with open(features_output_path, "wb") as f:
        pickle.dump(feature_dict, f, protocol=4)

    return feature_dict, msas_metadata


def predict(
    model_features_path: str,
    model_params_path: str,
    model_name: str,
    num_ensemble: int,
    run_multimer_system: bool,
    random_seed: int,
    raw_prediction_path: str,
    unrelaxed_protein_path: str,
) -> Mapping[str, str]:
    """Runs inference on an AlphaFold model."""

    # Ensure random_seed is a plain Python int, not a JAX traced value
    # This fixes: TypeError: PRNG key seed must be an integer; got Traced<ShapedArray(float32[], weak_type=True)>
    random_seed = int(random_seed)

    model_config = config.model_config(model_name)
    if run_multimer_system:
        model_config.model.num_ensemble_eval = num_ensemble
    elif "num_ensemble_eval" in model_config.data.eval:
        model_config.data.eval.num_ensemble_eval = num_ensemble
    elif "num_ensemble" in model_config.data.eval:
        model_config.data.eval.num_ensemble = num_ensemble

    model_params = data.get_model_haiku_params(model_name=model_name, data_dir=model_params_path)
    model_runner = model.RunModel(model_config, model_params)

    features = _load_features(model_features_path)
    processed_feature_dict = model_runner.process_features(
        raw_features=features, random_seed=random_seed
    )

    prediction_result = model_runner.predict(feat=processed_feature_dict, random_seed=random_seed)

    with open(raw_prediction_path, "wb") as f:
        pickle.dump(prediction_result, f, protocol=4)

    plddt = prediction_result["plddt"]
    plddt_b_factors = np.repeat(plddt[:, None], residue_constants.atom_type_num, axis=-1)
    unrelaxed_structure = protein.from_prediction(
        features=processed_feature_dict,
        result=prediction_result,
        b_factors=plddt_b_factors,
        remove_leading_feature_dimension=not model_runner.multimer_mode,
    )
    unrelaxed_pdbs = protein.to_pdb(unrelaxed_structure)
    with open(unrelaxed_protein_path, "w") as f:
        f.write(unrelaxed_pdbs)

    return prediction_result


def _write_json_atomic(path: str, payload: dict) -> None:
    """Write a JSON payload atomically via sibling temp file + ``os.replace``."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp_path, path)


def _predict_worker(
    model_features_path: str,
    model_params_path: str,
    model_name: str,
    num_ensemble: int,
    run_multimer_system: bool,
    random_seed: int,
    raw_prediction_path: str,
    unrelaxed_protein_path: str,
    result_meta_path: str,
) -> None:
    """Spawned subprocess entrypoint that runs predict() and persists scalar metadata."""
    logging.basicConfig(level=logging.INFO)
    try:
        prediction_result = predict(
            model_features_path=model_features_path,
            model_params_path=model_params_path,
            model_name=model_name,
            num_ensemble=num_ensemble,
            run_multimer_system=run_multimer_system,
            random_seed=random_seed,
            raw_prediction_path=raw_prediction_path,
            unrelaxed_protein_path=unrelaxed_protein_path,
        )
        _write_json_atomic(
            result_meta_path,
            {"ranking_confidence": float(prediction_result["ranking_confidence"])},
        )
    except BaseException as exc:
        try:
            _write_json_atomic(result_meta_path, {"error": f"{type(exc).__name__}: {exc}"})
        except OSError:
            pass
        raise


def predict_in_subprocess(
    model_features_path: str,
    model_params_path: str,
    model_name: str,
    num_ensemble: int,
    run_multimer_system: bool,
    random_seed: int,
    raw_prediction_path: str,
    unrelaxed_protein_path: str,
) -> dict[str, float]:
    """Run AlphaFold inference in a spawned child process so GPU VRAM is released on exit.

    Used by fused predict+relax tasks so JAX/XLA releases its entire CUDA context and
    memory pool before OpenMM initializes CUDA for AMBER relaxation on the same GPU.
    """
    ctx = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="af2_predict_subproc_") as tmp_dir:
        result_meta_path = os.path.join(tmp_dir, "result_meta.json")
        proc = ctx.Process(
            target=_predict_worker,
            kwargs={
                "model_features_path": model_features_path,
                "model_params_path": model_params_path,
                "model_name": model_name,
                "num_ensemble": num_ensemble,
                "run_multimer_system": run_multimer_system,
                "random_seed": int(random_seed),
                "raw_prediction_path": raw_prediction_path,
                "unrelaxed_protein_path": unrelaxed_protein_path,
                "result_meta_path": result_meta_path,
            },
        )
        proc.start()
        proc.join()

        meta: dict = {}
        if os.path.isfile(result_meta_path):
            try:
                with open(result_meta_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    meta = loaded
            except (OSError, json.JSONDecodeError, ValueError) as read_exc:
                logger.warning("Could not read predict subprocess metadata: %s", read_exc)

        if proc.exitcode != 0:
            detail = meta.get("error") or f"exit code {proc.exitcode}"
            raise RuntimeError(
                f"AlphaFold predict subprocess failed (exit code {proc.exitcode}): {detail}"
            )
        if "ranking_confidence" not in meta:
            raise RuntimeError(
                "AlphaFold predict subprocess completed without writing result metadata."
            )
        return {"ranking_confidence": float(meta["ranking_confidence"])}


def relax_protein(
    unrelaxed_protein_path: str,
    relaxed_protein_path: str,
    max_iterations: int = 0,
    tolerance: float = 2.39,
    stiffness: float = 10.0,
    exclude_residues: list[str] | None = None,
    max_outer_iterations: int = 3,
    use_gpu=False,
) -> Mapping[str, str]:
    """Runs AMBER relaxation."""
    if exclude_residues is None:
        exclude_residues = []

    with open(unrelaxed_protein_path) as f:
        unrelaxed_protein_pdb = f.read()

    unrelaxed_structure = protein.from_pdb_string(unrelaxed_protein_pdb)
    amber_relaxer = relax.AmberRelaxation(
        max_iterations=max_iterations,
        tolerance=tolerance,
        stiffness=stiffness,
        exclude_residues=exclude_residues,
        max_outer_iterations=max_outer_iterations,
        use_gpu=use_gpu,
    )
    relaxed_protein_pdb, _, _ = amber_relaxer.process(prot=unrelaxed_structure)

    logger.info(f"Saving relaxed protein to {relaxed_protein_path}")
    with open(relaxed_protein_path, "w") as f:
        f.write(relaxed_protein_pdb)

    return relaxed_protein_pdb


def predict_relax(
    model_features_path: str,
    model_params_path: str,
    prediction_runners: list[dict],
    num_ensemble: int,
    run_multimer_system: bool,
    raw_prediction_path: str,
    unrelaxed_protein_path: str,
    relaxed_protein_path: str,
    run_relax: bool,
    max_iterations: int = 0,
    tolerance: float = 2.39,
    stiffness: float = 10.0,
    exclude_residues: list[str] | None = None,
    max_outer_iterations: int = 3,
    use_gpu=True,
) -> Mapping[str, str]:
    """Runs predictions and relaxations sequentially on all specified models."""
    if exclude_residues is None:
        exclude_residues = []

    model_names = {runner["model_name"] for runner in prediction_runners}
    runners = {}
    for model_name in model_names:
        model_config = config.model_config(model_name)
        if run_multimer_system:
            model_config.model.num_ensemble_eval = num_ensemble
        elif "num_ensemble_eval" in model_config.data.eval:
            model_config.data.eval.num_ensemble_eval = num_ensemble
        elif "num_ensemble" in model_config.data.eval:
            model_config.data.eval.num_ensemble = num_ensemble
        model_params = data.get_model_haiku_params(
            model_name=model_name, data_dir=model_params_path
        )
        model_runner = model.RunModel(model_config, model_params)
        runners[model_name] = model_runner

    model_runners = {}
    for runner in prediction_runners:
        prediction_name = f"{runner['model_name']}_pred_{runner['prediction_index']}"
        model_runners[prediction_name] = (
            runners[runner["model_name"]],
            int(runner["random_seed"]),
        )

    logger.info("Have %d models: %s", len(model_runners), list(model_runners.keys()))

    if run_relax:
        amber_relaxer = relax.AmberRelaxation(
            max_iterations=max_iterations,
            tolerance=tolerance,
            stiffness=stiffness,
            exclude_residues=exclude_residues,
            max_outer_iterations=max_outer_iterations,
            use_gpu=use_gpu,
        )
    else:
        amber_relaxer = None

    # Run the predictions
    feature_dict = _load_features(model_features_path)
    timings = {}
    unrelaxed_pdbs = {}
    relaxed_pdbs = {}
    ranking_confidences = {}
    for model_name, prediction_runner in model_runners.items():
        logger.info("Running prediction %s", model_name)
        t_0 = time.time()
        model_random_seed = int(prediction_runner[1])
        model_runner = prediction_runner[0]
        processed_feature_dict = model_runner.process_features(
            feature_dict, random_seed=model_random_seed
        )
        timings[f"process_features_{model_name}"] = time.time() - t_0

        t_0 = time.time()
        prediction_result = model_runner.predict(
            processed_feature_dict, random_seed=model_random_seed
        )
        t_diff = time.time() - t_0
        timings[f"predict_and_compile_{model_name}"] = t_diff
        logger.info(
            "Total JAX model %s predict time (includes compilation time, see --benchmark): %.1fs",
            model_name,
            t_diff,
        )

        plddt = prediction_result["plddt"]
        ranking_confidences[model_name] = prediction_result["ranking_confidence"]

        # Save the model outputs.
        result_output_path = os.path.join(raw_prediction_path, f"result_{model_name}.pkl")
        with open(result_output_path, "wb") as f:
            pickle.dump(prediction_result, f, protocol=4)

        # Add the predicted LDDT in the b-factor column.
        # Note that higher predicted LDDT value means higher model confidence.
        plddt_b_factors = np.repeat(plddt[:, None], residue_constants.atom_type_num, axis=-1)
        unrelaxed_protein = protein.from_prediction(
            features=processed_feature_dict,
            result=prediction_result,
            b_factors=plddt_b_factors,
            remove_leading_feature_dimension=not model_runner.multimer_mode,
        )

        unrelaxed_pdbs[model_name] = protein.to_pdb(unrelaxed_protein)
        unrelaxed_pdb_path = os.path.join(unrelaxed_protein_path, f"unrelaxed_{model_name}.pdb")
        with open(unrelaxed_pdb_path, "w") as f:
            f.write(unrelaxed_pdbs[model_name])

        if amber_relaxer:
            # Relax the prediction.
            t_0 = time.time()
            relaxed_pdb_str, _, _ = amber_relaxer.process(prot=unrelaxed_protein)
            timings[f"relax_{model_name}"] = time.time() - t_0

            relaxed_pdbs[model_name] = relaxed_pdb_str

            # Save the relaxed PDB.
            relaxed_output_path = os.path.join(relaxed_protein_path, f"relaxed_{model_name}.pdb")
            with open(relaxed_output_path, "w") as f:
                f.write(relaxed_pdb_str)

    logger.info("Final timings  %s ", timings)

    return ranking_confidences


def aggregate(
    sequence_path: str,
    msa_paths: list[tuple[str, str]],
    template_features_path: str,
    output_features_path: str,
) -> dict[str, str]:
    """Aggregates MSAs and template features to create model features."""

    # Create sequence features
    seq, seq_desc, num_res = _read_sequence(sequence_path)
    sequence_features = make_sequence_features(sequence=seq, description=seq_desc, num_res=num_res)
    # Create MSA features
    msas = []
    for msa_path, msa_format in msa_paths:
        msas.append(_read_msa(msa_path, msa_format))
    if not msas:
        raise RuntimeError("No MSAs passed to the component")
    msa_features = make_msa_features(msas=msas)
    # Create template features
    template_features = _read_template_features(template_features_path)

    model_features = {**sequence_features, **msa_features, **template_features}
    with open(output_features_path, "wb") as f:
        pickle.dump(model_features, f, protocol=4)

    return model_features


def run_jackhmmer(input_path: str, msa_path: str, database_path: str, maxseq: int, n_cpu: int = 8):
    """Runs jackhmeer and saves results to files."""

    runner = jackhmmer.Jackhmmer(
        binary_path=JACKHMMER_BINARY_PATH,
        database_path=database_path,
        n_cpu=n_cpu,
    )

    results = runner.query(input_path, maxseq)[0]
    with open(msa_path, "w") as f:
        f.write(results["sto"])

    return parsers.parse_stockholm(results["sto"]), "sto"


def run_hhblits(input_path: str, msa_path: str, database_paths: list[str], n_cpu: int, maxseq: int):
    """Runs hhblits and saves results to a file."""

    runner = hhblits.HHBlits(
        binary_path=HHBLITS_BINARY_PATH,
        databases=database_paths,
        n_cpu=n_cpu,
        maxseq=maxseq,
    )

    results = runner.query(input_path)[0]
    with open(msa_path, "w") as f:
        f.write(results["a3m"])

    return parsers.parse_a3m(results["a3m"]), "a3m"


def run_hhsearch(
    sequence_path: str,
    msa_path: str,
    msa_data_format: str,
    template_hits_path: str,
    template_features_path: str,
    template_dbs_paths: list[str],
    mmcif_path: str,
    obsolete_path: str,
    max_template_date: str,
    max_template_hits: int,
    maxseq: int,
):
    """Runs hhsearch and saves results to a file."""

    if msa_data_format != "sto" and msa_data_format != "a3m":
        raise ValueError(f"Unsupported MSA format: {msa_data_format}")

    sequence, _, _ = _read_sequence(sequence_path)

    template_searcher = hhsearch.HHSearch(
        binary_path=HHSEARCH_BINARY_PATH, databases=template_dbs_paths, maxseq=maxseq
    )

    template_featurizer = templates.HhsearchHitFeaturizer(
        mmcif_dir=mmcif_path,
        max_template_date=max_template_date,
        max_hits=max_template_hits,
        kalign_binary_path=KALIGN_BINARY_PATH,
        obsolete_pdbs_path=obsolete_path,
        release_dates_path=None,
    )

    with open(msa_path) as f:
        msa_str = f.read()

    if msa_data_format == "sto":
        msa_for_templates = parsers.deduplicate_stockholm_msa(msa_str)
        msa_for_templates = parsers.remove_empty_columns_from_stockholm_msa(msa_for_templates)
        msa_for_templates = parsers.convert_stockholm_to_a3m(msa_for_templates)
    elif msa_data_format == "a3m":
        msa_for_templates = msa_str
    else:
        raise ValueError(f"Unsupported MSA format: {msa_data_format}")

    hhr_str = template_searcher.query(msa_for_templates)
    with open(template_hits_path, "w") as f:
        f.write(hhr_str)

    template_hits = template_searcher.get_template_hits(
        output_string=hhr_str, input_sequence=sequence
    )
    templates_result = template_featurizer.get_templates(
        query_sequence=sequence, hits=template_hits
    )
    with open(template_features_path, "wb") as f:
        pickle.dump(templates_result.features, f, protocol=4)

    return parsers.parse_hhr(hhr_str), templates_result.features


def run_hmmsearch(
    sequence_path: str,
    msa_path: str,
    msa_data_format: str,
    template_hits_path: str,
    template_features_path: str,
    template_db_path: str,
    mmcif_path: str,
    obsolete_path: str,
    max_template_date,
    max_template_hits,
):
    """Runs hhsearch and saves results to a file."""

    if msa_data_format != "sto":
        raise ValueError(f"Unsupported MSA format: {msa_data_format}")

    sequence, _, _ = _read_sequence(sequence_path)

    template_searcher = hmmsearch.Hmmsearch(
        binary_path=HMMSEARCH_BINARY_PATH,
        hmmbuild_binary_path=HMMBUILD_BINARY_PATH,
        database_path=template_db_path,
    )

    template_featurizer = templates.HmmsearchHitFeaturizer(
        mmcif_dir=mmcif_path,
        max_template_date=max_template_date,
        max_hits=max_template_hits,
        kalign_binary_path=KALIGN_BINARY_PATH,
        obsolete_pdbs_path=obsolete_path,
        release_dates_path=None,
    )

    with open(msa_path) as f:
        msa_str = f.read()

    msa_for_templates = parsers.deduplicate_stockholm_msa(msa_str)
    msa_for_templates = parsers.remove_empty_columns_from_stockholm_msa(msa_for_templates)

    sto_str = template_searcher.query(msa_for_templates)
    with open(template_hits_path, "w") as f:
        f.write(sto_str)

    template_hits = template_searcher.get_template_hits(
        output_string=sto_str, input_sequence=sequence
    )
    templates_result = template_featurizer.get_templates(
        query_sequence=sequence, hits=template_hits
    )

    with open(template_features_path, "wb") as f:
        pickle.dump(templates_result.features, f, protocol=4)

    return parsers.parse_stockholm(sto_str), templates_result.features


def run_mmseqs2_search(
    query_fasta: str,
    target_db: str,
    output_a3m: str,
    tmp_dir: str,
    gpu: bool = True,
    max_seqs: int = 10000,
    split: int = 0,
) -> int:
    """Run MMseqs2 search against a single database and produce A3M output.

    Args:
        query_fasta: Path to query FASTA file.
        target_db: Path to MMseqs2 target database (created by mmseqs createdb).
        output_a3m: Path to write the output A3M alignment.
        tmp_dir: Temporary directory for intermediate files.
        gpu: Whether to enable GPU acceleration.
        max_seqs: Maximum number of sequences to return.
        split: Number of splits for search-time memory management. Controls how
            many chunks the index is divided into so each chunk fits in GPU VRAM.
            0 means auto (MMseqs2 decides based on available memory).

    Returns:
        Number of sequences in the output alignment.
    """
    query_db = os.path.join(tmp_dir, "queryDB")
    result_db = os.path.join(tmp_dir, "resultDB")

    # Create query database from FASTA
    subprocess.run(
        [MMSEQS2_BINARY_PATH, "createdb", query_fasta, query_db],
        check=True,
    )

    # Search target database
    search_cmd = [
        MMSEQS2_BINARY_PATH,
        "search",
        query_db,
        target_db,
        result_db,
        tmp_dir,
        "--max-seqs",
        str(max_seqs),
        "-s",
        "7.5",
    ]
    if gpu:
        search_cmd.extend(["--gpu", "1"])
    if split > 0:
        search_cmd.extend(["--split", str(split)])
    subprocess.run(search_cmd, check=True)

    # Convert results to A3M
    subprocess.run(
        [
            MMSEQS2_BINARY_PATH,
            "result2msa",
            query_db,
            target_db,
            result_db,
            output_a3m,
            "--msa-format-mode",
            "6",
        ],
        check=True,
    )

    # Count sequences in output
    n_seqs = 0
    with open(output_a3m) as f:
        for line in f:
            if line.startswith(">"):
                n_seqs += 1
    return n_seqs


def run_mmseqs2_data_pipeline(
    fasta_path: str,
    run_multimer_system: bool,
    uniref90_mmseqs_path: str,
    mgnify_mmseqs_path: str,
    small_bfd_mmseqs_path: str,
    uniref90_database_path: str,
    mgnify_database_path: str,
    uniprot_database_path: str,
    pdb70_database_path: str,
    obsolete_pdbs_path: str,
    seqres_database_path: str,
    mmcif_path: str,
    max_template_date: str,
    msa_output_path: str,
    features_output_path: str,
    small_bfd_database_path: str = "",
) -> tuple[dict, dict[str, int]]:
    """MSA generation using MMseqs2-GPU, then standard AF2 feature generation.

    Replaces JackHMMER/HHblits MSA search with GPU-accelerated MMseqs2 for
    the three FASTA sequence databases (uniref90, mgnify, small_bfd).
    Template search still uses HHsearch/hmmsearch (CPU, fast).

    Only valid when use_small_bfd=True (all databases are FASTA format).
    """
    effective_small_bfd_path = small_bfd_database_path or (
        os.path.join(
            os.path.dirname(small_bfd_mmseqs_path),
            "bfd-first_non_consensus_sequences.fasta",
        )
        if small_bfd_mmseqs_path
        else ""
    )
    if not _has_af2_raw_databases(
        run_multimer_system=run_multimer_system,
        use_small_bfd=True,
        uniref90_database_path=uniref90_database_path,
        mgnify_database_path=mgnify_database_path,
        bfd_database_path="",
        small_bfd_database_path=effective_small_bfd_path,
        uniref30_database_path="",
        uniprot_database_path=uniprot_database_path,
        pdb70_database_path=pdb70_database_path,
        seqres_database_path=seqres_database_path,
        mmcif_path=mmcif_path,
    ):
        return _run_single_sequence_fallback(
            fasta_path=fasta_path,
            run_multimer_system=run_multimer_system,
            msa_output_path=msa_output_path,
            features_output_path=features_output_path,
        )

    logger.info("Starting MMseqs2-GPU data pipeline")
    os.makedirs(msa_output_path, exist_ok=True)

    # Detect GPU availability
    gpu_available = _check_gpu_available()
    logger.info(f"GPU available for MMseqs2: {gpu_available}")

    # Parse input sequences (handles both monomer and multimer FASTA)
    with open(fasta_path, encoding="utf-8") as f:
        fasta_str = f.read()
    raw_sequences, descriptions = parsers.parse_fasta(fasta_str)
    sequences = [s.strip().upper() for s in raw_sequences]
    if not sequences:
        raise ValueError(f"No input sequences found in {fasta_path}.")
    if any(not s for s in sequences):
        raise ValueError(f"Empty FASTA record found in {fasta_path}.")

    if run_multimer_system:
        pdb_chain_ids = _get_pdb_chain_ids()
        if len(sequences) > len(pdb_chain_ids):
            raise ValueError(
                f"Cannot process more chains ({len(sequences)}) than PDB_CHAIN_IDS ({len(pdb_chain_ids)})."
            )

        is_homomer_or_monomer = len(set(sequences)) == 1
        per_chain_features: list[tuple[str, dict]] = []
        for chain_idx, (seq, desc) in enumerate(zip(sequences, descriptions)):
            chain_id = pdb_chain_ids[chain_idx]
            chain_fasta = os.path.join(msa_output_path, f"chain_{chain_id}.fasta")
            with open(chain_fasta, "w", encoding="utf-8") as f:
                f.write(f">{desc}\n{seq}\n")

            dir_label = f"{chain_id}_{desc}" if descriptions.count(desc) > 1 else desc
            chain_msa_dir = _resolve_chain_msa_dir(msa_output_path, dir_label, chain_idx)
            os.makedirs(chain_msa_dir, exist_ok=True)

            chain_feat = _run_mmseqs2_single_chain(
                fasta_path=chain_fasta,
                sequence=seq,
                description=desc,
                msa_output_dir=chain_msa_dir,
                uniref90_mmseqs_path=uniref90_mmseqs_path,
                mgnify_mmseqs_path=mgnify_mmseqs_path,
                small_bfd_mmseqs_path=small_bfd_mmseqs_path,
                pdb70_database_path=pdb70_database_path,
                seqres_database_path=seqres_database_path,
                mmcif_path=mmcif_path,
                obsolete_pdbs_path=obsolete_pdbs_path,
                max_template_date=max_template_date,
                run_multimer_system=True,
                gpu=gpu_available,
            )

            # Construct UniProt *_all_seq pairing features when 2+ unique sequences exist
            if not is_homomer_or_monomer:
                if uniprot_database_path and os.path.exists(uniprot_database_path):
                    uniprot_runner = jackhmmer.Jackhmmer(
                        binary_path=JACKHMMER_BINARY_PATH,
                        database_path=uniprot_database_path,
                    )
                    uniprot_results = uniprot_runner.query(chain_fasta)[0]
                    uniprot_sto_path = os.path.join(chain_msa_dir, "uniprot_hits.sto")
                    with open(uniprot_sto_path, "w") as uf:
                        uf.write(uniprot_results["sto"])
                    uniprot_msa = parsers.parse_stockholm(uniprot_results["sto"])
                else:
                    fallback_sto_path = os.path.join(chain_msa_dir, "uniref90.sto")
                    if os.path.exists(fallback_sto_path):
                        with open(fallback_sto_path) as sf:
                            uniprot_msa = parsers.parse_stockholm(sf.read())
                    else:
                        uniprot_msa = parsers.parse_a3m(f">{desc}\n{seq}\n")
                chain_feat.update(_make_all_seq_msa_features(uniprot_msa))

            per_chain_features.append((chain_id, chain_feat))

        # Convert monomer features per chain ID, add assembly features, pair/merge, and pad
        feature_dict = _assemble_multimer_features(per_chain_features)
    else:
        # Monomer: single chain
        if len(sequences) != 1:
            raise ValueError(f"Expected 1 sequence for monomer, got {len(sequences)}")

        with tempfile.TemporaryDirectory(prefix="af2_mmseqs_query_") as query_tmp_dir:
            query_fasta = fasta_path
            if raw_sequences[0] != sequences[0]:
                query_fasta = os.path.join(query_tmp_dir, "query.fasta")
                with open(query_fasta, "w", encoding="utf-8") as qf:
                    qf.write(f">{descriptions[0]}\n{sequences[0]}\n")

            feature_dict = _run_mmseqs2_single_chain(
                fasta_path=query_fasta,
                sequence=sequences[0],
                description=descriptions[0],
                msa_output_dir=msa_output_path,
                uniref90_mmseqs_path=uniref90_mmseqs_path,
                mgnify_mmseqs_path=mgnify_mmseqs_path,
                small_bfd_mmseqs_path=small_bfd_mmseqs_path,
                pdb70_database_path=pdb70_database_path,
                seqres_database_path=seqres_database_path,
                mmcif_path=mmcif_path,
                obsolete_pdbs_path=obsolete_pdbs_path,
                max_template_date=max_template_date,
                run_multimer_system=False,
                gpu=gpu_available,
            )

    # Save features
    with open(features_output_path, "wb") as f:
        pickle.dump(feature_dict, f, protocol=4)

    # Collect MSA metadata
    msas_metadata = {}
    if run_multimer_system:
        folders = [
            os.path.join(msa_output_path, d)
            for d in os.listdir(msa_output_path)
            if os.path.isdir(os.path.join(msa_output_path, d))
        ]
        paths = []
        for folder in folders:
            paths += [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f))
            ]
    else:
        paths = [
            os.path.join(msa_output_path, f)
            for f in os.listdir(msa_output_path)
            if os.path.isfile(os.path.join(msa_output_path, f))
        ]

    for filepath in paths:
        ext = filepath.split(".")[-1]
        try:
            with open(filepath) as f:
                content = f.read()
            if ext == "sto":
                parsed = parsers.parse_stockholm(content)
            elif ext == "a3m":
                parsed = parsers.parse_a3m(content)
            elif ext == "hhr":
                parsed = parsers.parse_hhr(content)
            else:
                continue
            key = os.path.join(filepath.split(os.sep)[-2], filepath.split(os.sep)[-1])
            msas_metadata[key] = len(parsed)
        except Exception as exc:
            logger.debug("Skipping MSA metadata entry %s: %s", filepath, exc)
            continue

    return feature_dict, msas_metadata


def _check_gpu_available() -> bool:
    """Check if a CUDA GPU is available for MMseqs2."""
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5, check=False)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_mmseqs2_single_chain(
    fasta_path: str,
    sequence: str,
    description: str,
    msa_output_dir: str,
    uniref90_mmseqs_path: str,
    mgnify_mmseqs_path: str,
    small_bfd_mmseqs_path: str,
    pdb70_database_path: str,
    seqres_database_path: str,
    mmcif_path: str,
    obsolete_pdbs_path: str,
    max_template_date: str,
    run_multimer_system: bool,
    gpu: bool = True,
) -> dict:
    """Run MMseqs2 MSA search + template search for a single chain.

    Returns AlphaFold feature dict for this chain.
    """
    num_res = len(sequence)
    t0 = time.time()

    # 1. Run MMseqs2 search on each database
    # Split values sized so each chunk fits in L4 24 GB VRAM:
    #   uniref90: ~165 GB index / 8 splits = ~21 GB/chunk
    #   mgnify:   ~259 GB index / 12 splits = ~22 GB/chunk
    #   small_bfd: ~36 GB index / 2 splits = ~18 GB/chunk
    db_searches = [
        ("uniref90", uniref90_mmseqs_path, 10000, 8),
        ("mgnify", mgnify_mmseqs_path, 501, 12),
        ("small_bfd", small_bfd_mmseqs_path, 10000, 2),
    ]

    msas = []
    for db_name, db_path, max_seqs, split in db_searches:
        a3m_path = os.path.join(msa_output_dir, f"{db_name}.a3m")
        tmp_dir = tempfile.mkdtemp(prefix=f"mmseqs2_{db_name}_")
        try:
            logger.info(f"Running MMseqs2 search against {db_name} (gpu={gpu}, split={split})")
            t_search = time.time()
            n_seqs = run_mmseqs2_search(
                query_fasta=fasta_path,
                target_db=db_path,
                output_a3m=a3m_path,
                tmp_dir=tmp_dir,
                gpu=gpu,
                max_seqs=max_seqs,
                split=split,
            )
            logger.info(f"MMseqs2 {db_name}: {n_seqs} sequences in {time.time() - t_search:.1f}s")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Parse the A3M output, stripping null bytes that MMseqs2
        # result2msa can emit for padded sequences.
        with open(a3m_path) as f:
            a3m_str = f.read().replace("\x00", "")
        msa = parsers.parse_a3m(a3m_str)
        msas.append(msa)

    logger.info(f"All MMseqs2 searches done in {time.time() - t0:.1f}s")

    # 2. Build sequence features
    sequence_features = make_sequence_features(
        sequence=sequence,
        description=description,
        num_res=num_res,
    )

    # 3. Build MSA features from all MSAs
    msa_features = make_msa_features(msas=msas)

    # 4. Template search (still CPU — HHsearch for monomer, hmmsearch for multimer)
    # Use the uniref90 A3M as input for template search
    uniref90_a3m_path = os.path.join(msa_output_dir, "uniref90.a3m")

    if run_multimer_system:
        template_searcher = hmmsearch.Hmmsearch(
            binary_path=HMMSEARCH_BINARY_PATH,
            hmmbuild_binary_path=HMMBUILD_BINARY_PATH,
            database_path=seqres_database_path,
        )
        template_featurizer = templates.HmmsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path,
        )
        # hmmsearch needs Stockholm format — convert A3M to STO
        with open(uniref90_a3m_path) as f:
            a3m_str = f.read()
        # Write as STO for template search compatibility
        sto_path = os.path.join(msa_output_dir, "uniref90.sto")
        _write_a3m_as_sto(a3m_str, sto_path)
        with open(sto_path) as f:
            sto_str = f.read()
        msa_for_templates = parsers.deduplicate_stockholm_msa(sto_str)
        msa_for_templates = parsers.remove_empty_columns_from_stockholm_msa(msa_for_templates)
        hits_str = template_searcher.query(msa_for_templates)
    else:
        template_searcher = hhsearch.HHSearch(
            binary_path=HHSEARCH_BINARY_PATH,
            databases=[pdb70_database_path],
        )
        template_featurizer = templates.HhsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path,
        )
        # HHsearch uses A3M format directly
        with open(uniref90_a3m_path) as f:
            msa_for_templates = f.read()
        hits_str = template_searcher.query(msa_for_templates)

    # Save template hits
    hits_ext = "sto" if run_multimer_system else "hhr"
    hits_path = os.path.join(msa_output_dir, f"pdb_hits.{hits_ext}")
    with open(hits_path, "w") as f:
        f.write(hits_str)

    template_hits = template_searcher.get_template_hits(
        output_string=hits_str, input_sequence=sequence
    )
    templates_result = template_featurizer.get_templates(
        query_sequence=sequence, hits=template_hits
    )

    logger.info(f"Template search found {len(template_hits)} hits")

    # 5. Combine all features (normalizing empty template shapes if 0 hits were found)
    normalized_templates = _normalize_template_features(templates_result.features, num_res=num_res)
    feature_dict = {
        **sequence_features,
        **msa_features,
        **normalized_templates,
    }

    return feature_dict
