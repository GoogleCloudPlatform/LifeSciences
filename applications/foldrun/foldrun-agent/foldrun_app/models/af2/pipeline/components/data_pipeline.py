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
"""A component encapsulating AlphaFold data pipelines."""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.ALPHAFOLD_COMPONENTS_IMAGE)
def data_pipeline(
    sequence: Input[Artifact],
    ref_databases: Input[Artifact],
    run_multimer_system: bool,
    use_small_bfd: bool,
    max_template_date: str,
    msa_method: str,
    msas: Output[Artifact],
    features: Output[Artifact],
):
    """Configures and runs AlphaFold data pipelines.

    Args:
        msa_method: MSA search method - 'jackhmmer' (default CPU) or 'mmseqs2'
                    (GPU-accelerated, requires use_small_bfd=True).
    """

    import hashlib
    import json
    import logging
    import os
    import shutil
    import time
    import uuid

    from alphafold_utils import run_data_pipeline, run_mmseqs2_data_pipeline

    preset = "multimer" if run_multimer_system else "monomer"
    logging.info(
        f"Starting {preset} AlphaFold data pipeline (msa_method={msa_method})"
    )
    t0 = time.time()

    mount_path = ref_databases.uri
    uniref90_database_path = os.path.join(mount_path, ref_databases.metadata["uniref90"])
    mgnify_database_path = os.path.join(mount_path, ref_databases.metadata["mgnify"])
    uniref30_database_path = os.path.join(mount_path, ref_databases.metadata["uniref30"])
    bfd_database_path = os.path.join(mount_path, ref_databases.metadata["bfd"])
    small_bfd_database_path = os.path.join(mount_path, ref_databases.metadata["small_bfd"])
    uniprot_database_path = os.path.join(mount_path, ref_databases.metadata["uniprot"])
    pdb70_database_path = os.path.join(mount_path, ref_databases.metadata["pdb70"])
    obsolete_pdbs_path = os.path.join(mount_path, ref_databases.metadata["pdb_obsolete"])
    seqres_database_path = os.path.join(mount_path, ref_databases.metadata["pdb_seqres"])
    mmcif_path = os.path.join(mount_path, ref_databases.metadata["pdb_mmcif"])
    os.makedirs(msas.path, exist_ok=True)

    # --- NFS feature cache ---
    # Cache key covers all inputs that affect the features.pkl content.
    with open(sequence.path) as _f:
        fasta_content = _f.read().strip()
    cache_key_data = json.dumps({
        "sequence": fasta_content,
        "max_template_date": max_template_date,
        "preset": preset,
        "use_small_bfd": use_small_bfd,
        "msa_method": msa_method,
    }, sort_keys=True)
    seq_hash = hashlib.sha256(cache_key_data.encode()).hexdigest()
    cache_key = f"{preset}_{seq_hash}"

    nfs_cache_base = os.path.join(mount_path, "af2_features_cache")
    nfs_tmp_base = os.path.join(mount_path, "af2_features_tmp")
    os.makedirs(nfs_cache_base, exist_ok=True)
    os.makedirs(nfs_tmp_base, exist_ok=True)

    seq_cache_dir = os.path.join(nfs_cache_base, cache_key)
    cached_features_path = os.path.join(seq_cache_dir, "features.pkl")
    cached_metadata_path = os.path.join(seq_cache_dir, "metadata.json")

    if os.path.exists(cached_features_path) and os.path.exists(cached_metadata_path):
        logging.info(f"AF2 feature cache hit for {cache_key}. Skipping MSA search.")
        shutil.copy(cached_features_path, features.path)
        with open(cached_metadata_path) as _f:
            cached_meta = json.load(_f)
        for k, v in cached_meta.get("features_metadata", {}).items():
            features.metadata[k] = v
        msas.metadata = cached_meta.get("msas_metadata", {})
        t1 = time.time()
        logging.info(f"Data pipeline (cache hit) completed. Elapsed time: {t1 - t0:.1f}s")
        return

    logging.info(f"AF2 feature cache miss for {cache_key}. Running full data pipeline.")

    if msa_method == "mmseqs2":
        # GPU-accelerated MMseqs2 pipeline (requires use_small_bfd=True)
        uniref90_mmseqs_path = os.path.join(mount_path, ref_databases.metadata["uniref90_mmseqs"])
        mgnify_mmseqs_path = os.path.join(mount_path, ref_databases.metadata["mgnify_mmseqs"])
        small_bfd_mmseqs_path = os.path.join(mount_path, ref_databases.metadata["small_bfd_mmseqs"])

        features_dict, msas_metadata = run_mmseqs2_data_pipeline(
            fasta_path=sequence.path,
            run_multimer_system=run_multimer_system,
            uniref90_mmseqs_path=uniref90_mmseqs_path,
            mgnify_mmseqs_path=mgnify_mmseqs_path,
            small_bfd_mmseqs_path=small_bfd_mmseqs_path,
            uniref90_database_path=uniref90_database_path,
            mgnify_database_path=mgnify_database_path,
            uniprot_database_path=uniprot_database_path,
            pdb70_database_path=pdb70_database_path,
            obsolete_pdbs_path=obsolete_pdbs_path,
            seqres_database_path=seqres_database_path,
            mmcif_path=mmcif_path,
            max_template_date=max_template_date,
            msa_output_path=msas.path,
            features_output_path=features.path,
        )
    else:
        # Standard JackHMMER/HHblits pipeline (CPU)
        features_dict, msas_metadata = run_data_pipeline(
            fasta_path=sequence.path,
            run_multimer_system=run_multimer_system,
            use_small_bfd=use_small_bfd,
            uniref90_database_path=uniref90_database_path,
            mgnify_database_path=mgnify_database_path,
            bfd_database_path=bfd_database_path,
            small_bfd_database_path=small_bfd_database_path,
            uniref30_database_path=uniref30_database_path,
            uniprot_database_path=uniprot_database_path,
            pdb70_database_path=pdb70_database_path,
            obsolete_pdbs_path=obsolete_pdbs_path,
            seqres_database_path=seqres_database_path,
            mmcif_path=mmcif_path,
            max_template_date=max_template_date,
            msa_output_path=msas.path,
            features_output_path=features.path,
        )

    features.metadata["category"] = "features"
    features.metadata["msa_method"] = msa_method
    if run_multimer_system:
        features.metadata["final_dedup_msa_size"] = int(features_dict["num_alignments"])
    else:
        features.metadata["final_dedup_msa_size"] = int(features_dict["num_alignments"][0])
        features.metadata["total_number_templates"] = int(
            features_dict["template_domain_names"].shape[0]
        )
    msas.metadata = msas_metadata

    # --- Cache promotion (atomic rename) ---
    run_id = str(uuid.uuid4())[:12]
    tmp_dir = os.path.join(nfs_tmp_base, f"{cache_key}_{run_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    shutil.copy(features.path, os.path.join(tmp_dir, "features.pkl"))
    with open(os.path.join(tmp_dir, "metadata.json"), "w") as _f:
        json.dump({
            "features_metadata": dict(features.metadata),
            "msas_metadata": msas.metadata,
        }, _f)
    try:
        os.rename(tmp_dir, seq_cache_dir)
        logging.info(f"Cached AF2 features for {cache_key}")
    except (FileExistsError, OSError):
        logging.info(f"Cache already populated for {cache_key} by concurrent run.")
        shutil.rmtree(tmp_dir, ignore_errors=True)

    t1 = time.time()
    logging.info(f"Data pipeline completed. Elapsed time: {t1 - t0:.1f}s")
