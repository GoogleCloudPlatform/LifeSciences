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
"""KFP component that runs ESMFold2 / ESMFold2-Fast structure prediction for a single seed.

Each seed runs as a separate GPU task via ParallelFor.
Loads pre-staged weights and CCD cache from NFS (`nfs_cache_path`) with zero
runtime Hugging Face downloads.
"""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.ESMFOLD2_COMPONENTS_IMAGE, install_kfp_package=False)
def predict_esmfold2(
    updated_query_json: Input[Artifact],
    seed_value: int,
    num_diffusion_samples: int,
    num_loops: int,
    num_sampling_steps: int,
    nfs_cache_path: str,
    predicted_structure: Output[Artifact],
    confidence_json: Output[Artifact],
):
    """Runs ESMFold2 / ESMFold2-Fast structure prediction for a single seed."""
    import json
    import logging
    import os
    import shutil
    import time
    from pathlib import Path

    import numpy as np
    import torch
    from esm.models.esmfold2 import (
        MSA,
        DNAInput,
        ESMFold2InputBuilder,
        EsmFold2Model,
        LigandInput,
        Modification,
        ProteinInput,
        RNAInput,
        StructurePredictionInput,
    )

    logging.basicConfig(level=logging.INFO)
    logging.info(
        f"Starting ESMFold2 prediction (seed={seed_value}, samples={num_diffusion_samples}, "
        f"loops={num_loops}, steps={num_sampling_steps}, cache={nfs_cache_path})"
    )
    t0 = time.time()

    output_dir = os.path.dirname(predicted_structure.path)
    predictions_dir = os.path.join(output_dir, "predictions")
    os.makedirs(predictions_dir, exist_ok=True)

    predicted_structure.uri = f"{predicted_structure.uri}.cif"
    confidence_json.uri = f"{confidence_json.uri}.json"

    with open(updated_query_json.path) as f:
        query_data = json.load(f)

    spi_sequences = []
    ordered_chain_ids: list[str] = []

    for i, entry in enumerate(query_data.get("sequences", [])):
        if "protein" in entry:
            pdata = entry["protein"]
            raw_ids = pdata.get("id", f"A{i}")
            cids = [str(c) for c in raw_ids] if isinstance(raw_ids, list) else [str(raw_ids)]
            mods = [
                Modification(position=int(m["position"]), ccd=str(m["ccd"]))
                for m in (pdata.get("modifications") or [])
            ]
            msa_val = pdata.get("msa")
            msa_obj = None
            if msa_val and str(msa_val).strip().lower() != "empty" and os.path.isfile(str(msa_val)):
                loaded_msa = MSA.from_a3m(
                    path=str(msa_val), remove_insertions=True, max_sequences=1024
                )
                if len(loaded_msa.entries) > 0:
                    msa_obj = loaded_msa
            for cid in cids:
                spi_sequences.append(
                    ProteinInput(
                        id=cid,
                        sequence=str(pdata["sequence"]).strip(),
                        modifications=mods or None,
                        msa=msa_obj,
                    )
                )
                ordered_chain_ids.append(cid)
        elif "rna" in entry:
            rdata = entry["rna"]
            raw_ids = rdata.get("id", f"R{i}")
            cids = [str(c) for c in raw_ids] if isinstance(raw_ids, list) else [str(raw_ids)]
            mods = [
                Modification(position=int(m["position"]), ccd=str(m["ccd"]))
                for m in (rdata.get("modifications") or [])
            ]
            for cid in cids:
                spi_sequences.append(
                    RNAInput(
                        id=cid,
                        sequence=str(rdata["sequence"]).strip(),
                        modifications=mods or None,
                    )
                )
                ordered_chain_ids.append(cid)
        elif "dna" in entry:
            ddata = entry["dna"]
            raw_ids = ddata.get("id", f"D{i}")
            cids = [str(c) for c in raw_ids] if isinstance(raw_ids, list) else [str(raw_ids)]
            mods = [
                Modification(position=int(m["position"]), ccd=str(m["ccd"]))
                for m in (ddata.get("modifications") or [])
            ]
            for cid in cids:
                spi_sequences.append(
                    DNAInput(
                        id=cid,
                        sequence=str(ddata["sequence"]).strip(),
                        modifications=mods or None,
                    )
                )
                ordered_chain_ids.append(cid)
        elif "ligand" in entry:
            ldata = entry["ligand"]
            raw_ids = ldata.get("id", f"L{i}")
            cids = [str(c) for c in raw_ids] if isinstance(raw_ids, list) else [str(raw_ids)]
            raw_ccd = ldata.get("ccd")
            ccd_list = None
            if raw_ccd:
                ccd_list = (
                    [str(c).upper() for c in raw_ccd]
                    if isinstance(raw_ccd, list)
                    else [str(raw_ccd).upper()]
                )
            smiles_str = str(ldata["smiles"]).strip() if ldata.get("smiles") else None
            for cid in cids:
                spi_sequences.append(LigandInput(id=cid, ccd=ccd_list, smiles=smiles_str))
                ordered_chain_ids.append(cid)

    spi = StructurePredictionInput(sequences=spi_sequences)

    cache_dir = Path(nfs_cache_path)
    if not (cache_dir / "config.json").exists():
        raise FileNotFoundError(
            f"ESMFold2 checkpoint config.json not found at {cache_dir}. "
            "Ensure esmfold2_fast_cache or esmfold2_cache has been downloaded to NFS."
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        # Hardware FP8 GEMMs require compute capability >= 8.9 (L4 8.9, H100 9.0, RTX PRO 6000 12.0);
        # Ampere A100 (8.0) must use bf16.
        esmc_precision = "fp8" if torch.cuda.get_device_capability() >= (8, 9) else "bf16"
    else:
        esmc_precision = "fp32"
    logging.info(
        f"Loading EsmFold2Model from {cache_dir} on device={device} esmc_precision={esmc_precision}"
    )

    model = EsmFold2Model.from_pretrained(
        str(cache_dir),
        esmc_precision=esmc_precision,
        device=device,
    )
    model._offload_esmc = device == "cuda"
    builder = ESMFold2InputBuilder(ccd_cache=cache_dir)

    raw_results = builder.fold(
        model,
        spi,
        num_loops=num_loops,
        num_sampling_steps=num_sampling_steps,
        num_diffusion_samples=num_diffusion_samples,
        seed=seed_value,
    )
    results_list = raw_results if isinstance(raw_results, list) else [raw_results]

    best_cif = None
    best_conf = None
    best_score = -1.0

    for sample_idx, res in enumerate(results_list):
        sample_name = f"seed_{seed_value}_sample_{sample_idx}"
        cif_path = os.path.join(predictions_dir, f"{sample_name}_model.cif")
        conf_path = os.path.join(predictions_dir, f"{sample_name}_confidences.json")
        pae_path = os.path.join(predictions_dir, f"{sample_name}_pae.npz")

        with open(cif_path, "w") as f:
            f.write(res.complex.to_mmcif())

        if res.plddt is not None:
            plddt_np = res.plddt.detach().float().cpu().numpy().reshape(-1)
            plddt_100 = plddt_np * 100.0 if float(np.max(plddt_np)) <= 1.0001 else plddt_np
            plddt_mean = float(np.mean(plddt_100)) if plddt_100.size > 0 else 0.0
        else:
            plddt_100 = np.array([], dtype=np.float32)
            plddt_mean = 0.0

        ptm = float(res.ptm) if res.ptm is not None else float(plddt_mean / 100.0)
        iptm = float(res.iptm) if res.iptm is not None else None

        if len(ordered_chain_ids) > 1 and iptm is not None:
            ranking_score = 0.8 * iptm + 0.2 * ptm
        else:
            ranking_score = 0.8 * ptm + 0.2 * (plddt_mean / 100.0)

        chain_ptm: dict[str, float] = {}
        chain_pair_iptm: dict[str, dict[str, float]] = {}
        if res.pair_chains_iptm is not None:
            pair_mat = res.pair_chains_iptm.detach().float().cpu().numpy()
            if pair_mat.ndim == 3:
                pair_mat = pair_mat[0]
            n_c = pair_mat.shape[0]
            labels = (
                ordered_chain_ids
                if len(ordered_chain_ids) == n_c
                else [
                    ordered_chain_ids[idx] if idx < len(ordered_chain_ids) else f"chain_{idx}"
                    for idx in range(n_c)
                ]
            )
            for idx_i, cid_i in enumerate(labels):
                chain_ptm[cid_i] = float(pair_mat[idx_i, idx_i])
                chain_pair_iptm[cid_i] = {
                    cid_j: float(pair_mat[idx_i, idx_j]) for idx_j, cid_j in enumerate(labels)
                }

        pae_mean = None
        pae_max = None
        if res.pae is not None:
            pae_np = res.pae.detach().float().cpu().numpy()
            if pae_np.ndim == 3:
                pae_np = pae_np[0]
            np.savez_compressed(pae_path, pae=pae_np)
            pae_mean = float(np.mean(pae_np))
            pae_max = float(np.max(pae_np))

        conf_payload = {
            "sample_name": sample_name,
            "seed": seed_value,
            "sample": sample_idx,
            "ranking_score": ranking_score,
            "ptm": ptm,
            "iptm": iptm,
            "plddt_mean": plddt_mean,
            "pae_mean": pae_mean,
            "pae_max": pae_max,
            "chain_ids": ordered_chain_ids,
            "chain_ptm": chain_ptm,
            "chain_pair_iptm": chain_pair_iptm,
            "plddt": [round(float(v), 2) for v in plddt_100.tolist()],
        }
        with open(conf_path, "w") as f:
            json.dump(conf_payload, f, indent=2)

        if ranking_score > best_score:
            best_score = ranking_score
            best_cif = cif_path
            best_conf = conf_path

    if best_cif:
        shutil.copy2(best_cif, predicted_structure.path)
    if best_conf:
        shutil.copy2(best_conf, confidence_json.path)

    predicted_structure.metadata["category"] = "predicted_structure"
    predicted_structure.metadata["seed_value"] = seed_value
    predicted_structure.metadata["num_diffusion_samples"] = num_diffusion_samples
    confidence_json.metadata["category"] = "confidence"
    confidence_json.metadata["ranking_score"] = best_score

    t1 = time.time()
    logging.info(
        f"ESMFold2 prediction completed (seed={seed_value}, samples={len(results_list)}). "
        f"Best ranking_score={best_score:.3f}. Elapsed time: {t1 - t0:.1f}s"
    )
