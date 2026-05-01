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
"""KFP component that runs MSA search for BOLTZ2."""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.BOLTZ2_COMPONENTS_IMAGE)
def msa_pipeline_boltz2(
    query_json: Input[Artifact],
    ref_databases: Input[Artifact],
    updated_query_json: Output[Artifact],
):
    """Runs MSA search for BOLTZ2 protein chains and injects .a3m paths into query YAML.

    Protein chains: Jackhmmer against uniref90, mgnify → combined .a3m injected as msa: field.
    RNA, DNA, ligand chains: no MSA — Boltz-2 schema only supports msa: for protein.

    MSA files are written to a sequence-hash-keyed directory on NFS so that:
    - The predict task (which mounts the same NFS) can read the embedded paths.
    - Identical sequences reuse cached MSAs across pipeline runs (cache hit = no jackhmmer).

    Cache layout on NFS:
      boltz2_msas_cache/protein_<sha256>/combined.a3m   ← canonical cache entry
      boltz2_msas_tmp/protein_<sha256>_<run_id>/        ← in-progress temp dir (atomic rename)
    """
    import yaml
    import logging
    import os
    import subprocess
    import time
    import uuid
    import hashlib
    import shutil

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting BOLTZ2 MSA pipeline")
    t0 = time.time()

    def sto_to_a3m(sto_path: str, a3m_path: str) -> None:
        """Convert Stockholm alignment to A3M format in pure Python.

        esl-reformat (Easel/HMMER) is not reliably available in all container
        environments, so we implement the conversion directly.

        A3M rules:
        - Match columns (where query is non-gap): uppercase residues, gaps kept
        - Insertion columns (where query is gap): lowercase residues, gaps deleted
        """
        seqs: dict = {}
        order: list = []
        with open(sto_path) as f:
            for line in f:
                line = line.rstrip()
                if not line or line.startswith("#") or line == "//":
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2:
                    name, seq = parts
                    if name not in seqs:
                        seqs[name] = []
                        order.append(name)
                    seqs[name].append(seq)

        if not order:
            open(a3m_path, "w").close()
            return

        # Identify match columns from the query (first sequence)
        query_seq = "".join(seqs[order[0]])
        match_cols = {i for i, c in enumerate(query_seq) if c != "-"}

        with open(a3m_path, "w") as out:
            for name in order:
                full_seq = "".join(seqs[name])
                a3m_residues = []
                for i, c in enumerate(full_seq):
                    if i in match_cols:
                        # Match column: keep residue (uppercase) or gap
                        a3m_residues.append(c.upper() if c != "-" else c)
                    else:
                        # Insertion column: lowercase residues, skip gaps
                        if c not in ("-", "."):
                            a3m_residues.append(c.lower())
                out.write(f">{name}\n{''.join(a3m_residues)}\n")

    mount_path = ref_databases.uri

    # Read query YAML (KFP artifacts still use query_json parameter name)
    with open(query_json.path) as f:
        query_data = yaml.safe_load(f)

    # Database paths (protein MSA only — Boltz-2 schema has no msa: field for RNA/DNA)
    uniref90_path = os.path.join(mount_path, ref_databases.metadata["uniref90"])
    mgnify_path = os.path.join(mount_path, ref_databases.metadata["mgnify"])

    # Cache & Temp directories on NFS
    nfs_msa_cache_base = os.path.join(mount_path, "boltz2_msas_cache")
    nfs_msa_tmp_base = os.path.join(mount_path, "boltz2_msas_tmp")
    os.makedirs(nfs_msa_cache_base, exist_ok=True)
    os.makedirs(nfs_msa_tmp_base, exist_ok=True)

    run_id = str(uuid.uuid4())[:12]
    num_msa_sequences = 0

    for i, seq_entry in enumerate(query_data.get("sequences", [])):
        if "protein" not in seq_entry:
            # RNA, DNA, and ligand chains: no msa: injection (not supported by Boltz-2 schema)
            continue

        prot_data = seq_entry["protein"]
        seq_id = prot_data.get("id", f"seq_{i}")
        if isinstance(seq_id, list):
            seq_id = seq_id[0]  # handle [A, B] identical chains

        sequence = prot_data.get("sequence", "")
        if not sequence:
            logging.warning(f"Empty sequence for chain {seq_id}, skipping MSA")
            continue

        seq_hash = hashlib.sha256(sequence.encode()).hexdigest()
        seq_cache_dir = os.path.join(nfs_msa_cache_base, f"protein_{seq_hash}")

        # Cache lookup: combined.a3m is the canonical output for a Boltz2 protein MSA
        if os.path.exists(os.path.join(seq_cache_dir, "combined.a3m")):
            logging.info(f"Cache hit for chain {seq_id} (protein_{seq_hash}). Reusing MSA.")
            prot_data["msa"] = os.path.join(seq_cache_dir, "combined.a3m")
            num_msa_sequences += 1
            continue

        logging.info(f"Cache miss for chain {seq_id} (protein_{seq_hash}). Generating MSA.")
        seq_dir = os.path.join(nfs_msa_tmp_base, f"protein_{seq_hash}_{run_id}")
        os.makedirs(seq_dir, exist_ok=True)

        # Write sequence to tmp FASTA
        tmp_fasta = os.path.join(seq_dir, "query.fasta")
        with open(tmp_fasta, "w") as f:
            f.write(f">protein_{seq_hash}\n{sequence}\n")

        # Jackhmmer against uniref90
        uniref90_sto = os.path.join(seq_dir, "uniref90.sto")
        uniref90_a3m = os.path.join(seq_dir, "uniref90.a3m")
        subprocess.run(
            [
                "jackhmmer",
                "--noali",
                "-N",
                "1",
                "--cpu",
                "8",
                "-A",
                uniref90_sto,
                tmp_fasta,
                uniref90_path,
            ],
            check=True,
        )
        sto_to_a3m(uniref90_sto, uniref90_a3m)

        # Jackhmmer against mgnify
        mgnify_sto = os.path.join(seq_dir, "mgnify.sto")
        mgnify_a3m = os.path.join(seq_dir, "mgnify.a3m")
        subprocess.run(
            [
                "jackhmmer",
                "--noali",
                "-N",
                "1",
                "--cpu",
                "8",
                "-A",
                mgnify_sto,
                tmp_fasta,
                mgnify_path,
            ],
            check=True,
        )
        sto_to_a3m(mgnify_sto, mgnify_a3m)

        # Combine A3M files
        combined_a3m = os.path.join(seq_dir, "combined.a3m")
        with open(combined_a3m, "w") as fout:
            with open(uniref90_a3m) as fin1, open(mgnify_a3m) as fin2:
                fout.write(fin1.read())
                fout.write(fin2.read())

        logging.info(f"Protein MSA complete for {seq_id}")

        # Strip intermediates — only combined.a3m is needed for future cache hits.
        # Removes query.fasta, uniref90.sto, uniref90.a3m, mgnify.sto, mgnify.a3m
        # before the rename so the cache entry stays lean (~50–100 MB vs ~300–400 MB).
        for intermediate in (tmp_fasta, uniref90_sto, uniref90_a3m, mgnify_sto, mgnify_a3m):
            try:
                os.remove(intermediate)
            except OSError:
                pass

        # Cache promotion: atomic rename so concurrent runs don't corrupt the cache
        try:
            os.rename(seq_dir, seq_cache_dir)
            logging.info(f"Cached MSA for protein_{seq_hash}")
        except (FileExistsError, OSError):
            logging.info(
                f"Cache already populated for protein_{seq_hash} by concurrent run or existing cache."
            )
            shutil.rmtree(seq_dir)

        if os.path.exists(os.path.join(seq_cache_dir, "combined.a3m")):
            prot_data["msa"] = os.path.join(seq_cache_dir, "combined.a3m")
            num_msa_sequences += 1

    # Write updated query YAML
    updated_query_json.uri = f"{updated_query_json.uri}.yaml"
    with open(updated_query_json.path, "w") as f:
        yaml.dump(query_data, f)

    updated_query_json.metadata["category"] = "updated_query_json"
    updated_query_json.metadata["msa_sequences"] = num_msa_sequences
    updated_query_json.metadata["nfs_msa_cache_base"] = nfs_msa_cache_base

    t1 = time.time()
    logging.info(
        f"BOLTZ2 MSA pipeline completed. "
        f"MSA sequences: {num_msa_sequences}. "
        f"Elapsed time: {t1 - t0:.1f}s"
    )
