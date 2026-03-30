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
):
    """Runs MSA search for OF3 and injects MSA file paths into query JSON.

    Protein chains: Jackhmmer against uniref90, mgnify, pdb_seqres
    RNA chains (if present): nhmmer against rfam, rnacentral
    """
    import json
    import logging
    import os
    import subprocess
    import time

    logging.info("Starting OF3 MSA pipeline")
    t0 = time.time()

    mount_path = ref_databases.uri

    # Read query JSON
    with open(query_json.path) as f:
        query_data = json.load(f)

    # Database paths
    uniref90_path = os.path.join(mount_path, ref_databases.metadata["uniref90"])
    mgnify_path = os.path.join(mount_path, ref_databases.metadata["mgnify"])
    pdb_seqres_path = os.path.join(mount_path, ref_databases.metadata["pdb_seqres"])
    rfam_path = os.path.join(mount_path, ref_databases.metadata["rfam"])
    rnacentral_path = os.path.join(mount_path, ref_databases.metadata["rnacentral"])

    # Output directory for MSA files
    msa_output_dir = os.path.join(os.path.dirname(updated_query_json.path), "msas")
    os.makedirs(msa_output_dir, exist_ok=True)

    # Process each sequence in the query
    msa_file_paths = {}
    for i, seq_entry in enumerate(query_data.get("sequences", [])):
        seq_type = seq_entry.get("type", "protein")
        seq_id = seq_entry.get("id", f"seq_{i}")

        if seq_type == "protein":
            # Run jackhmmer for protein sequences
            seq_msa_dir = os.path.join(msa_output_dir, seq_id)
            os.makedirs(seq_msa_dir, exist_ok=True)

            # Write sequence to tmp FASTA
            tmp_fasta = os.path.join(seq_msa_dir, f"{seq_id}.fasta")
            with open(tmp_fasta, "w") as f:
                f.write(f">{seq_id}\n{seq_entry['sequence']}\n")

            # Jackhmmer against uniref90
            uniref90_sto = os.path.join(seq_msa_dir, "uniref90.sto")
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

            # Jackhmmer against mgnify
            mgnify_sto = os.path.join(seq_msa_dir, "mgnify.sto")
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

            msa_file_paths[seq_id] = {
                "uniref90": uniref90_sto,
                "mgnify": mgnify_sto,
            }
            logging.info(f"Protein MSA complete for {seq_id}")

        elif seq_type == "rna":
            # Run nhmmer for RNA sequences
            seq_msa_dir = os.path.join(msa_output_dir, seq_id)
            os.makedirs(seq_msa_dir, exist_ok=True)

            tmp_fasta = os.path.join(seq_msa_dir, f"{seq_id}.fasta")
            with open(tmp_fasta, "w") as f:
                f.write(f">{seq_id}\n{seq_entry['sequence']}\n")

            # nhmmer against rfam
            rfam_sto = os.path.join(seq_msa_dir, "rfam.sto")
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

            # nhmmer against rnacentral
            rnacentral_sto = os.path.join(seq_msa_dir, "rnacentral.sto")
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

            msa_file_paths[seq_id] = {
                "rfam": rfam_sto,
                "rnacentral": rnacentral_sto,
            }
            logging.info(f"RNA MSA complete for {seq_id}")

    # Inject MSA paths into query JSON
    query_data["main_msa_file_paths"] = msa_file_paths

    # Write updated query JSON
    updated_query_json.uri = f"{updated_query_json.uri}.json"
    with open(updated_query_json.path, "w") as f:
        json.dump(query_data, f, indent=2)

    updated_query_json.metadata["category"] = "updated_query_json"
    updated_query_json.metadata["msa_sequences"] = len(msa_file_paths)

    t1 = time.time()
    logging.info(f"OF3 MSA pipeline completed. Elapsed time: {t1 - t0:.1f}s")
