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
"""KFP component that runs OF3 structure prediction for a single seed.

Each seed runs as a separate GPU task via ParallelFor.
The component patches the query JSON's seeds field to the specific seed value,
so OF3 names output directories correctly (e.g., seed_1181241943/ not seed_42/).

CLI reference (from openfold3.run_openfold):
    run_openfold predict
        --query_json PATH           (required)
        --inference_ckpt_path PATH  (optional, defaults to ~/.openfold3/)
        --output_dir PATH           (optional)
        --use_msa_server BOOL       (default: True)
        --use_templates BOOL        (default: True)
        --runner_yaml PATH          (optional, overrides dataset/model config)
        --num_diffusion_samples INT (optional)
        --num_model_seeds INT       (optional)

Template handling:
    When use_templates=True and nfs_mmcif_dir is provided, a runner YAML is written
    with template_preprocessor_settings pointing to the NFS pdb_mmcif directory.
    OF3 uses this to resolve template structures (CIF files) for featurization.
    The template alignment files (.sto from jackhmmer against pdb_seqres) are embedded
    in the query JSON per chain by the MSA pipeline step.
"""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.OPENFOLD3_COMPONENTS_IMAGE)
def predict_of3(
    updated_query_json: Input[Artifact],
    seed_value: int,
    num_diffusion_samples: int,
    nfs_params_path: str,
    predicted_structure: Output[Artifact],
    confidence_json: Output[Artifact],
    use_templates: bool = True,
    nfs_mmcif_dir: str = "",
):
    """Runs OF3 structure prediction for a single seed.

    Generates a runner YAML with the specific seed value, then calls
    run_openfold predict with --runner_yaml and --num_model_seeds=1.
    Each seed runs as a separate GPU task via ParallelFor.

    When use_templates=True, writes a runner YAML configuring OF3's template
    preprocessor to resolve structures from the local NFS pdb_mmcif directory
    rather than downloading from RCSB. Template alignment files (.sto) must
    already be embedded in the query JSON by the MSA pipeline.
    """
    import json
    import logging
    import os
    import subprocess
    import time

    logging.info(f"Starting OF3 prediction (seed={seed_value}, samples={num_diffusion_samples})")
    t0 = time.time()

    params_path = nfs_params_path

    # Output paths
    output_dir = os.path.dirname(predicted_structure.path)
    os.makedirs(output_dir, exist_ok=True)

    predicted_structure.uri = f"{predicted_structure.uri}.cif"
    confidence_json.uri = f"{confidence_json.uri}.json"

    # Patch the query JSON's seeds field so OF3 names the output directory
    # with the actual seed value (e.g., seed_1181241943/ instead of seed_42/).
    # Without this, all seed tasks write to seed_<base_seed>/ because OF3
    # derives the directory name from the query JSON, not the runner_yaml.
    patched_query_path = os.path.join(output_dir, "patched_query.json")
    with open(updated_query_json.path) as f:
        query_data = json.load(f)
    query_data["seeds"] = [seed_value]
    with open(patched_query_path, "w") as f:
        json.dump(query_data, f, indent=2)
    logging.info(f"Patched query JSON seeds to [{seed_value}]")

    # Write runner YAML if using templates with local NFS CIF structures.
    # This configures OF3's TemplatePreprocessorSettings to look in pdb_mmcif
    # instead of downloading from RCSB, keeping prediction VPC-isolated.
    runner_yaml_path = None
    if use_templates and nfs_mmcif_dir:
        import yaml

        runner_config = {
            "template_preprocessor_settings": {
                "structure_directory": nfs_mmcif_dir,
                "fetch_missing_structures": False,
                "structure_file_format": "cif",
            }
        }
        runner_yaml_path = os.path.join(output_dir, "runner.yaml")
        with open(runner_yaml_path, "w") as f:
            yaml.dump(runner_config, f, default_flow_style=False)
        logging.info(
            f"Runner YAML written: {runner_yaml_path} "
            f"(structure_directory={nfs_mmcif_dir})"
        )
    elif use_templates:
        logging.warning(
            "use_templates=True but nfs_mmcif_dir not provided; "
            "OF3 will attempt to download template structures from RCSB"
        )

    # Run OpenFold3 prediction via run_openfold CLI entrypoint
    cmd = [
        "run_openfold",
        "predict",
        f"--query_json={patched_query_path}",
        f"--inference_ckpt_path={params_path}",
        f"--output_dir={output_dir}",
        "--num_model_seeds=1",
        f"--num_diffusion_samples={num_diffusion_samples}",
        "--use_msa_server=False",
        f"--use_templates={str(use_templates)}",
    ]
    if runner_yaml_path:
        cmd.append(f"--runner_yaml={runner_yaml_path}")

    logging.info(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # Find output files in the nested directory structure
    # OF3 outputs: output_dir/<query_name>/seed_<N>/<name>_seed_<N>_sample_<M>_model.cif
    #              output_dir/<query_name>/seed_<N>/<name>_seed_<N>_sample_<M>_confidences_aggregated.json
    # Determine if this is a monomer — OF3 uses AF3-style ranking:
    #   sample_ranking_score = 0.8 * iptm + 0.2 * ptm
    # For a single-chain prediction iptm ≈ 0 (no interface), so all samples
    # get near-zero scores and the "best" selection is essentially random.
    # For monomers we rank by ptm instead, which correctly reflects fold quality.
    with open(updated_query_json.path) as f:
        _qdata = json.load(f)
    _chains = []
    for _q in _qdata.get("queries", {}).values():
        _chains.extend(_q.get("chains", []))
    _is_monomer = len(_chains) <= 1

    if _is_monomer:
        logging.info("Monomer detected — ranking samples by ptm (not sample_ranking_score)")
    else:
        logging.info(f"Complex detected ({len(_chains)} chains) — ranking samples by sample_ranking_score")

    best_cif = None
    best_conf = None
    best_score = -1.0

    for root, dirs, files in os.walk(output_dir):
        for fname in files:
            src = os.path.join(root, fname)
            if fname.endswith("_confidences_aggregated.json"):
                with open(src) as f:
                    conf_data = json.load(f)
                if _is_monomer:
                    # Use ptm for monomers — iptm≈0 makes sample_ranking_score useless
                    score = conf_data.get("ptm", conf_data.get("sample_ranking_score", 0.0))
                else:
                    score = conf_data.get("sample_ranking_score", 0.0)
                if score > best_score:
                    best_score = score
                    best_conf = src
                    # Look for CIF in same directory
                    for cif_name in os.listdir(root):
                        if cif_name.endswith(".cif"):
                            best_cif = os.path.join(root, cif_name)
                            break

    # Copy best results to output artifacts
    if best_cif:
        import shutil

        shutil.copy2(best_cif, predicted_structure.path)
    if best_conf:
        import shutil

        shutil.copy2(best_conf, confidence_json.path)

    predicted_structure.metadata["category"] = "predicted_structure"
    predicted_structure.metadata["seed_value"] = seed_value
    predicted_structure.metadata["num_diffusion_samples"] = num_diffusion_samples
    predicted_structure.metadata["use_templates"] = use_templates

    if best_conf and os.path.exists(confidence_json.path):
        with open(confidence_json.path) as f:
            conf_data = json.load(f)
        confidence_json.metadata["category"] = "confidence"
        confidence_json.metadata["is_monomer"] = _is_monomer
        confidence_json.metadata["use_templates"] = use_templates
        # Store both scores so downstream tools can display the right one
        if "sample_ranking_score" in conf_data:
            confidence_json.metadata["sample_ranking_score"] = conf_data["sample_ranking_score"]
        if "ptm" in conf_data:
            confidence_json.metadata["ptm"] = conf_data["ptm"]
        # ranking_score: ptm for monomers, sample_ranking_score for complexes
        confidence_json.metadata["ranking_score"] = best_score

    t1 = time.time()
    logging.info(
        f"OF3 prediction completed (seed={seed_value}). Best ranking_score={best_score:.3f}. Elapsed time: {t1 - t0:.1f}s"
    )
