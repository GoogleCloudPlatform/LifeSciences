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
"""KFP component that runs BOLTZ2 structure prediction for a single seed.

Each seed runs as a separate GPU task via ParallelFor.
Uses --seed CLI flag (not a YAML field) to control diffusion randomness.
Output lands under out_dir/boltz_results_<stem>/predictions/<stem>/.

CLI reference:
    boltz predict <input.yaml> --out_dir PATH --diffusion_samples N
                               --cache PATH --seed N --override
"""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.BOLTZ2_COMPONENTS_IMAGE)
def predict_boltz2(
    updated_query_json: Input[Artifact],
    seed_value: int,
    num_diffusion_samples: int,
    nfs_cache_path: str,
    predicted_structure: Output[Artifact],
    confidence_json: Output[Artifact],
):
    """Runs BOLTZ2 structure prediction for a single seed.

    Passes --seed to the boltz CLI so each ParallelFor task uses a distinct seed.
    Boltz-2 wraps output under boltz_results_<stem>/; we search that subdirectory.
    """
    import json
    import logging
    import os
    import subprocess
    import time

    logging.info(f"Starting BOLTZ2 prediction (seed={seed_value}, samples={num_diffusion_samples})")
    t0 = time.time()

    # Output paths
    output_dir = os.path.dirname(predicted_structure.path)
    os.makedirs(output_dir, exist_ok=True)

    predicted_structure.uri = f"{predicted_structure.uri}.cif"
    confidence_json.uri = f"{confidence_json.uri}.json"

    # Copy query YAML to local dir so boltz derives a deterministic stem
    import shutil
    query_local = os.path.join(output_dir, "patched_query.yaml")
    shutil.copy2(updated_query_json.path, query_local)

    # Run Boltz-2 prediction via boltz CLI.
    # --seed controls the diffusion seed per task (no seeds: field in YAML schema).
    # boltz wraps output under: out_dir/boltz_results_patched_query/
    cmd = [
        "boltz",
        "predict",
        query_local,
        f"--out_dir={output_dir}",
        f"--diffusion_samples={num_diffusion_samples}",
        f"--cache={nfs_cache_path}",
        f"--seed={seed_value}",
        "--override",
        "--no_kernels",  # disable cuequivariance CUDA kernels — requires newer
                         # driver than Vertex AI A100 nodes currently support
    ]

    logging.info(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # Find output files in the nested directory structure.
    # boltz adds boltz_results_<stem>/ wrapper: out_dir/boltz_results_patched_query/predictions/patched_query/
    best_cif = None
    best_conf = None
    best_score = -1.0

    predictions_dir = os.path.join(
        output_dir, "boltz_results_patched_query", "predictions", "patched_query"
    )
    if os.path.exists(predictions_dir):
        for fname in os.listdir(predictions_dir):
            src = os.path.join(predictions_dir, fname)
            if fname.startswith("confidence_") and fname.endswith(".json"):
                with open(src) as f:
                    conf_data = json.load(f)
                score = conf_data.get("confidence_score", 0.0)
                if score > best_score:
                    best_score = score
                    best_conf = src
                    # Look for CIF in same directory (remove confidence_ prefix)
                    cif_name = fname.replace("confidence_", "").replace(".json", ".cif")
                    cif_path = os.path.join(predictions_dir, cif_name)
                    if os.path.exists(cif_path):
                        best_cif = cif_path

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

    if best_conf and os.path.exists(confidence_json.path):
        with open(confidence_json.path) as f:
            conf_data = json.load(f)
        confidence_json.metadata["category"] = "confidence"
        if "confidence_score" in conf_data:
            confidence_json.metadata["ranking_score"] = conf_data["confidence_score"]

    t1 = time.time()
    logging.info(
        f"BOLTZ2 prediction completed (seed={seed_value}). Best confidence_score={best_score:.3f}. Elapsed time: {t1 - t0:.1f}s"
    )
