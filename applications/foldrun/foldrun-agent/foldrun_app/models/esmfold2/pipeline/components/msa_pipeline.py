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
"""KFP component that runs MSA search for full ESMFold2 protein chains."""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.ESMFOLD2_COMPONENTS_IMAGE, install_kfp_package=False)
def msa_pipeline_esmfold2(
    query_json: Input[Artifact],
    ref_databases: Input[Artifact],
    updated_query_json: Output[Artifact],
):
    """Runs Jackhmmer MSA search for ESMFold2 protein chains and injects .a3m paths into query JSON.

    Caches sequence-hashed A3M alignments under `esmfold2_msas_cache/protein_<sha256>/combined.a3m`
    with guaranteed query-length match-column alignment for `esm.models.esmfold2.MSA.from_a3m`.
    """
    import hashlib
    import json
    import logging
    import os
    import shutil
    import subprocess
    import time
    import uuid

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting ESMFold2 MSA pipeline")
    t0 = time.time()

    def sto_to_a3m_rows(sto_path: str, expected_len: int) -> list[tuple[str, str]]:
        """Convert Jackhmmer Stockholm alignment to validated A3M (header, seq) rows."""
        seqs: dict[str, list[str]] = {}
        order: list[str] = []
        rf_chunks: list[str] = []
        with open(sto_path) as f:
            for line in f:
                line = line.rstrip()
                if not line or line == "//":
                    continue
                if line.startswith("#=GC RF"):
                    parts = line.split()
                    if len(parts) >= 3:
                        rf_chunks.append(parts[2])
                    continue
                if line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2:
                    name, seq = parts
                    if name not in seqs:
                        seqs[name] = []
                        order.append(name)
                    seqs[name].append(seq)

        if not order:
            return []

        rf_str = "".join(rf_chunks)
        if rf_str:
            match_cols = {i for i, c in enumerate(rf_str) if c not in (".", "-")}
        else:
            query_aligned = "".join(seqs[order[0]])
            match_cols = {i for i, c in enumerate(query_aligned) if c not in ("-", ".")}

        rows: list[tuple[str, str]] = []
        for idx, name in enumerate(order):
            # Skip query row (idx == 0); caller prepends canonical query row once
            if idx == 0:
                continue
            full_seq = "".join(seqs[name])
            a3m_chars: list[str] = []
            match_count = 0
            for i, c in enumerate(full_seq):
                if i in match_cols:
                    a3m_chars.append("-" if c in ("-", ".") else c.upper())
                    match_count += 1
                else:
                    if c not in ("-", "."):
                        a3m_chars.append(c.lower())
            if match_count == expected_len:
                rows.append((name, "".join(a3m_chars)))
        return rows

    mount_path = ref_databases.uri

    with open(query_json.path) as f:
        query_data = json.load(f)

    uniref90_path = os.path.join(mount_path, ref_databases.metadata["uniref90"])
    mgnify_path = os.path.join(mount_path, ref_databases.metadata["mgnify"])

    nfs_msa_cache_base = os.path.join(mount_path, "esmfold2_msas_cache")
    nfs_msa_tmp_base = os.path.join(mount_path, "esmfold2_msas_tmp")
    os.makedirs(nfs_msa_cache_base, exist_ok=True)
    os.makedirs(nfs_msa_tmp_base, exist_ok=True)

    run_id = str(uuid.uuid4())[:12]
    num_msa_sequences = 0

    for i, seq_entry in enumerate(query_data.get("sequences", [])):
        if "protein" not in seq_entry:
            continue

        prot_data = seq_entry["protein"]
        seq_id = prot_data.get("id", f"seq_{i}")
        if isinstance(seq_id, list):
            seq_id = seq_id[0]

        existing_msa = prot_data.get("msa")
        if existing_msa is not None and str(existing_msa).strip() != "":
            normalized_msa = str(existing_msa).strip()
            if normalized_msa.lower() == "empty":
                prot_data["msa"] = "empty"
                logging.info(f"Preserving single-sequence MSA (empty) for chain {seq_id}")
                num_msa_sequences += 1
                continue
            real_mount = os.path.realpath(mount_path)
            real_msa = os.path.realpath(normalized_msa)
            if os.path.commonpath([real_mount, real_msa]) == real_mount and os.path.isfile(
                real_msa
            ):
                logging.info(
                    f"Preserving pre-populated MSA for chain {seq_id}: {real_msa} (skipping jackhmmer)"
                )
                num_msa_sequences += 1
                continue
            logging.warning(
                f"Ignoring invalid or missing pre-populated MSA path for chain {seq_id}: {existing_msa!r}"
            )
            prot_data.pop("msa", None)

        sequence = str(prot_data.get("sequence", "")).strip()
        if not sequence:
            logging.warning(f"Empty sequence for chain {seq_id}, skipping MSA")
            continue

        seq_hash = hashlib.sha256(sequence.encode()).hexdigest()
        seq_cache_dir = os.path.join(nfs_msa_cache_base, f"protein_{seq_hash}")

        if os.path.exists(os.path.join(seq_cache_dir, "combined.a3m")):
            logging.info(f"Cache hit for chain {seq_id} (protein_{seq_hash}). Reusing MSA.")
            prot_data["msa"] = os.path.join(seq_cache_dir, "combined.a3m")
            num_msa_sequences += 1
            continue

        if not (os.path.exists(uniref90_path) and os.path.exists(mgnify_path)):
            logging.warning(
                f"Reference sequence databases not found on NFS ({uniref90_path}, {mgnify_path}); "
                f"falling back to single-sequence mode for chain {seq_id}."
            )
            prot_data["msa"] = "empty"
            continue

        logging.info(f"Cache miss for chain {seq_id} (protein_{seq_hash}). Generating MSA.")
        seq_dir = os.path.join(nfs_msa_tmp_base, f"protein_{seq_hash}_{run_id}")
        os.makedirs(seq_dir, exist_ok=True)

        tmp_fasta = os.path.join(seq_dir, "query.fasta")
        with open(tmp_fasta, "w") as f:
            f.write(f">query_{seq_id}\n{sequence}\n")

        combined_a3m = os.path.join(seq_dir, "combined.a3m")
        n_cpus = str(os.cpu_count() or 8)
        uniref90_sto = os.path.join(seq_dir, "uniref90.sto")
        subprocess.run(
            [
                "jackhmmer",
                "--noali",
                "-N",
                "1",
                "--cpu",
                n_cpus,
                "-E",
                "0.0001",
                "--incE",
                "0.0001",
                "-A",
                uniref90_sto,
                tmp_fasta,
                uniref90_path,
            ],
            check=True,
        )
        uniref_rows = sto_to_a3m_rows(uniref90_sto, len(sequence))

        mgnify_sto = os.path.join(seq_dir, "mgnify.sto")
        subprocess.run(
            [
                "jackhmmer",
                "--noali",
                "-N",
                "1",
                "--cpu",
                n_cpus,
                "-E",
                "0.0001",
                "--incE",
                "0.0001",
                "-A",
                mgnify_sto,
                tmp_fasta,
                mgnify_path,
            ],
            check=True,
        )
        mgnify_rows = sto_to_a3m_rows(mgnify_sto, len(sequence))

        seen_seqs = {sequence}
        with open(combined_a3m, "w") as fout:
            fout.write(f">query_{seq_id}\n{sequence}\n")
            for hdr, a3m_seq in uniref_rows + mgnify_rows:
                if a3m_seq in seen_seqs:
                    continue
                seen_seqs.add(a3m_seq)
                fout.write(f">{hdr}\n{a3m_seq}\n")
                if len(seen_seqs) >= 2048:
                    break

        logging.info(
            f"Built combined.a3m for chain {seq_id} with {len(seen_seqs)} unique sequences "
            f"(uniref90={len(uniref_rows)}, mgnify={len(mgnify_rows)})"
        )

        for intermediate in (tmp_fasta, uniref90_sto, mgnify_sto):
            try:
                os.remove(intermediate)
            except OSError:
                pass

        try:
            os.rename(seq_dir, seq_cache_dir)
            logging.info(f"Cached MSA for protein_{seq_hash}")
        except (FileExistsError, OSError):
            logging.info(f"Cache already populated for protein_{seq_hash} by concurrent run.")
            shutil.rmtree(seq_dir)

        if os.path.exists(os.path.join(seq_cache_dir, "combined.a3m")):
            prot_data["msa"] = os.path.join(seq_cache_dir, "combined.a3m")
            num_msa_sequences += 1

    updated_query_json.uri = f"{updated_query_json.uri}.json"
    with open(updated_query_json.path, "w") as f:
        json.dump(query_data, f, indent=2)

    updated_query_json.metadata["category"] = "updated_query_json"
    updated_query_json.metadata["msa_sequences"] = num_msa_sequences
    updated_query_json.metadata["nfs_msa_cache_base"] = nfs_msa_cache_base

    t1 = time.time()
    logging.info(
        f"ESMFold2 MSA pipeline completed. "
        f"MSA sequences: {num_msa_sequences}. "
        f"Elapsed time: {t1 - t0:.1f}s"
    )
