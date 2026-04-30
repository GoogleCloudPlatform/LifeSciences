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
"""KFP component that runs MSA search for OF3."""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.OPENFOLD3_COMPONENTS_IMAGE)
def msa_pipeline_of3(
    query_json: Input[Artifact],
    ref_databases: Input[Artifact],
    updated_query_json: Output[Artifact],
    use_templates: bool = True,
):
    """Runs MSA search for OF3 and injects MSA and template file paths into query JSON.

    Protein chains: Jackhmmer against uniref90, mgnify.
    Protein chains (use_templates=True): Jackhmmer against pdb_seqres for template hits.
    RNA chains (if present): nhmmer against rfam, rnacentral.

    MSA files are written to a job-specific subdirectory on NFS so that the predict
    task (which mounts the same NFS) can read the file paths embedded in the query JSON.

    Template alignment files are in Stockholm (.sto) format from jackhmmer, which OF3's
    StoParser can consume directly (see https://openfold-3.readthedocs.io/en/latest/).
    """
    import json
    import logging
    import os
    import subprocess
    import time
    import uuid
    import hashlib
    import shutil

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting OF3 MSA pipeline")
    t0 = time.time()

    mount_path = ref_databases.uri

    # Read query JSON
    with open(query_json.path) as f:
        query_data = json.load(f)

    # Database paths (relative to NFS mount point, resolved via artifact metadata)
    uniref90_path = os.path.join(mount_path, ref_databases.metadata["uniref90"])
    mgnify_path = os.path.join(mount_path, ref_databases.metadata["mgnify"])
    pdb_seqres_path = os.path.join(mount_path, ref_databases.metadata["pdb_seqres"])
    rfam_path = os.path.join(mount_path, ref_databases.metadata.get("rfam", ""))
    rnacentral_path = os.path.join(mount_path, ref_databases.metadata.get("rnacentral", ""))

    # Cache & Temp directories on NFS
    nfs_msa_cache_base = os.path.join(mount_path, "of3_msas_cache")
    nfs_msa_tmp_base = os.path.join(mount_path, "of3_msas_tmp")
    os.makedirs(nfs_msa_cache_base, exist_ok=True)
    os.makedirs(nfs_msa_tmp_base, exist_ok=True)

    run_id = str(uuid.uuid4())[:12]
    num_msa_chains = 0
    num_template_chains = 0

    # Iterate over queries and their chains — matches the OF3 InferenceQuerySet format:
    # { "queries": { "name": { "chains": [...] } } }
    for query_name, query_entry in query_data.get("queries", {}).items():
        for chain_idx, chain in enumerate(query_entry.get("chains", [])):
            seq_type = chain.get("molecule_type", "protein")

            # chain_ids can be a string "A" or a list ["A", "B"]
            chain_ids = chain.get("chain_ids", [])
            if isinstance(chain_ids, str):
                chain_ids = [chain_ids]
            chain_id = chain_ids[0] if chain_ids else f"chain_{chain_idx}"

            # Unique key for this chain's MSA directory
            seq_key = f"{query_name}_{chain_id}"

            if seq_type not in ("protein", "rna"):
                logging.info(f"Skipping MSA for {seq_type} chain {seq_key}")
                continue

            sequence = chain.get("sequence", "")
            if not sequence:
                logging.warning(f"Empty sequence for chain {seq_key}, skipping MSA")
                continue

            seq_hash = hashlib.sha256(sequence.encode()).hexdigest()
            seq_cache_dir = os.path.join(nfs_msa_cache_base, f"{seq_type}_{seq_hash}")

            # Cache lookup
            cache_hit = False
            if os.path.exists(seq_cache_dir):
                if seq_type == "protein":
                    required_files = ["uniref90_hits.sto", "mgnify_hits.sto"]
                    if use_templates and os.path.exists(pdb_seqres_path):
                        required_files.append("pdb_seqres.sto")
                elif seq_type == "rna":
                    required_files = []
                    if rfam_path and os.path.exists(rfam_path):
                        required_files.append("rfam_hits.sto")
                    if rnacentral_path and os.path.exists(rnacentral_path):
                        required_files.append("rnacentral_hits.sto")
                else:
                    required_files = []

                cache_hit = True
                for f in required_files:
                    if not os.path.exists(os.path.join(seq_cache_dir, f)):
                        cache_hit = False
                        break

            if cache_hit:
                logging.info(f"Cache hit for chain {seq_key} ({seq_type}_{seq_hash}). Reusing MSAs.")
                chain["main_msa_file_paths"] = seq_cache_dir
                num_msa_chains += 1
                if seq_type == "protein" and use_templates and os.path.exists(pdb_seqres_path):
                    chain["template_alignment_file_path"] = os.path.join(seq_cache_dir, "pdb_seqres.sto")
                    num_template_chains += 1
                continue

            logging.info(
                f"Cache miss for chain {seq_key} ({seq_type}_{seq_hash}). Generating MSAs."
            )
            seq_dir = os.path.join(nfs_msa_tmp_base, f"{seq_type}_{seq_hash}_{run_id}")
            os.makedirs(seq_dir, exist_ok=True)

            if seq_type == "protein":
                sequence = chain.get("sequence", "")
                if not sequence:
                    logging.warning(f"Empty sequence for chain {seq_key}, skipping MSA")
                    continue

                # Write query FASTA for HMMER tools
                tmp_fasta = os.path.join(seq_dir, "query.fasta")
                with open(tmp_fasta, "w") as f:
                    f.write(f">{seq_type}_{seq_hash}\n{sequence}\n")

                msa_files = []

                # Jackhmmer against UniRef90.
                # File must be named "uniref90_hits.sto" — OF3's MSASettings.max_seq_counts
                # filters by basename and expects the "_hits" suffix for all MSA files.
                uniref90_sto = os.path.join(seq_dir, "uniref90_hits.sto")
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
                msa_files.append(uniref90_sto)

                # Jackhmmer against MGnify ("mgnify_hits.sto" for same reason)
                mgnify_sto = os.path.join(seq_dir, "mgnify_hits.sto")
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
                msa_files.append(mgnify_sto)

                # Inject MSA paths into the chain dict (OF3 main_msa_file_paths field)
                chain["main_msa_file_paths"] = msa_files
                num_msa_chains += 1

                # Template search: jackhmmer against pdb_seqres → Stockholm output.
                # The .sto file is directly consumable by OF3's StoParser as
                # template_alignment_file_path (see OF3 template how-to guide).
                # We skip if pdb_seqres is not present (e.g. core-only install).
                if use_templates:
                    if os.path.exists(pdb_seqres_path):
                        pdb_seqres_sto = os.path.join(seq_dir, "pdb_seqres.sto")
                        subprocess.run(
                            [
                                "jackhmmer",
                                "-N",
                                "1",
                                "--cpu",
                                "8",
                                "-A",
                                pdb_seqres_sto,
                                tmp_fasta,
                                pdb_seqres_path,
                            ],
                            check=True,
                        )
                        chain["template_alignment_file_path"] = pdb_seqres_sto
                        num_template_chains += 1
                        logging.info(f"Template alignment written: {pdb_seqres_sto}")
                    else:
                        logging.warning(
                            f"pdb_seqres not found at {pdb_seqres_path}; "
                            f"skipping template search for chain {seq_key}. "
                            f"Run database setup with --models of3 to download it."
                        )

                logging.info(f"Protein MSA complete for {seq_key}")

            elif seq_type == "rna":
                sequence = chain.get("sequence", "")
                if not sequence:
                    logging.warning(f"Empty RNA sequence for chain {seq_key}, skipping MSA")
                    continue

                tmp_fasta = os.path.join(seq_dir, "query.fasta")
                with open(tmp_fasta, "w") as f:
                    f.write(f">{seq_type}_{seq_hash}\n{sequence}\n")

                rna_files = []

                # nhmmer against Rfam ("rfam_hits.sto" per OF3 max_seq_counts convention)
                if rfam_path and os.path.exists(rfam_path):
                    rfam_sto = os.path.join(seq_dir, "rfam_hits.sto")
                    subprocess.run(
                        [
                            "nhmmer",
                            "--noali",
                            "--cpu",
                            "8",
                            "-A",
                            rfam_sto,
                            tmp_fasta,
                            rfam_path,
                        ],
                        check=True,
                    )
                    rna_files.append(rfam_sto)

                # nhmmer against RNAcentral ("rnacentral_hits.sto" per same convention)
                if rnacentral_path and os.path.exists(rnacentral_path):
                    rnacentral_sto = os.path.join(seq_dir, "rnacentral_hits.sto")
                    subprocess.run(
                        [
                            "nhmmer",
                            "--noali",
                            "--cpu",
                            "8",
                            "-A",
                            rnacentral_sto,
                            tmp_fasta,
                            rnacentral_path,
                        ],
                        check=True,
                    )
                    rna_files.append(rnacentral_sto)

                if rna_files:
                    chain["main_msa_file_paths"] = rna_files

                logging.info(f"RNA MSA complete for {seq_key}")

            # Cache promotion (atomic rename to cache dir)
            try:
                os.rename(seq_dir, seq_cache_dir)
                logging.info(f"Cached MSAs for {seq_type}_{seq_hash}")
            except (FileExistsError, OSError):
                logging.info(
                    f"Cache already populated for {seq_type}_{seq_hash} by concurrent run or existing cache."
                )
                shutil.rmtree(seq_dir)

            # Point to the cache directory
            if os.path.exists(seq_cache_dir):
                if seq_type in ("protein", "rna"):
                    chain["main_msa_file_paths"] = seq_cache_dir
                    if seq_type == "protein" and "template_alignment_file_path" in chain:
                        chain["template_alignment_file_path"] = os.path.join(seq_cache_dir, "pdb_seqres.sto")

    # Write updated query JSON (with injected MSA and template paths)
    updated_query_json.uri = f"{updated_query_json.uri}.json"
    with open(updated_query_json.path, "w") as f:
        json.dump(query_data, f, indent=2)

    updated_query_json.metadata["category"] = "updated_query_json"
    updated_query_json.metadata["msa_chains"] = num_msa_chains
    updated_query_json.metadata["template_chains"] = num_template_chains
    updated_query_json.metadata["nfs_msa_cache_base"] = nfs_msa_cache_base
    updated_query_json.metadata["use_templates"] = use_templates

    t1 = time.time()
    logging.info(
        f"OF3 MSA pipeline completed. "
        f"MSA chains: {num_msa_chains}, Template chains: {num_template_chains}. "
        f"Elapsed time: {t1 - t0:.1f}s"
    )
