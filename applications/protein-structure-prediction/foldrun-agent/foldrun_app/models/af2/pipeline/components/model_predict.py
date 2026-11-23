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
"""A component encapsulating AlphaFold model predict."""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.ALPHAFOLD_COMPONENTS_IMAGE, install_kfp_package=False)
def predict(
    model_features: Input[Artifact],
    model_params: Input[Artifact],
    model_name: str,
    prediction_index: int,
    num_ensemble: int,
    run_multimer_system: bool,
    random_seed: int,
    tf_force_unified_memory: str,
    xla_python_client_mem_fraction: str,
    raw_prediction: Output[Artifact],
    unrelaxed_protein: Output[Artifact],
    relaxed_protein: Output[Artifact],
    is_run_relax: str = "",
    relax_max_iterations: int = 0,
    relax_tolerance: float = 2.39,
    relax_stiffness: float = 10.0,
    relax_max_outer_iterations: int = 3,
    relax_use_gpu: bool = True,
):
    """Runs AlphaFold model inference and optional inline AMBER relaxation on the same GPU.

    When ``is_run_relax == "relax"``, inference executes inside a ``spawn`` child process so
    JAX/XLA releases 100% of GPU VRAM on exit before OpenMM initializes CUDA for relaxation.
    """

    import logging
    import os
    import shutil
    import time

    from alphafold_utils import (
        predict as alphafold_predict,
    )
    from alphafold_utils import (
        predict_in_subprocess,
        relax_protein,
    )

    logging.basicConfig(level=logging.INFO)
    os.environ["TF_FORCE_UNIFIED_MEMORY"] = tf_force_unified_memory
    os.environ["XLA_PYTHON_CLIENT_MEM_FRACTION"] = xla_python_client_mem_fraction

    should_relax = is_run_relax == "relax"
    logging.info(
        f"Starting model prediction {prediction_index} using model {model_name} "
        f"(inline_relax={should_relax})..."
    )
    t0 = time.time()

    raw_prediction.uri = f"{raw_prediction.uri}.pkl"
    unrelaxed_protein.uri = f"{unrelaxed_protein.uri}.pdb"
    relaxed_protein.uri = f"{relaxed_protein.uri}.pdb"

    predict_fn = predict_in_subprocess if should_relax else alphafold_predict
    prediction_result = predict_fn(
        model_features_path=model_features.path,
        model_params_path=model_params.path,
        model_name=model_name,
        num_ensemble=num_ensemble,
        run_multimer_system=run_multimer_system,
        random_seed=random_seed,
        raw_prediction_path=raw_prediction.path,
        unrelaxed_protein_path=unrelaxed_protein.path,
    )

    raw_prediction.metadata["category"] = "raw_prediction"
    raw_prediction.metadata["prediction_index"] = prediction_index
    raw_prediction.metadata["ranking_confidence"] = prediction_result["ranking_confidence"]
    unrelaxed_protein.metadata["category"] = "unrelaxed_protein"

    t_pred = time.time()
    logging.info(f"Model prediction completed. Elapsed time: {t_pred - t0:.1f}s")

    relaxed_protein.metadata["category"] = "relaxed_protein"
    if not should_relax:
        relaxed_protein.metadata["relax_skipped"] = True
        return

    relax_errors: tuple[type[Exception], ...] = (ValueError, RuntimeError)
    try:
        from openmm import OpenMMException

        relax_errors = (*relax_errors, OpenMMException)
    except ImportError:
        pass

    relaxed_protein.metadata["relax_failed"] = False
    t_relax0 = time.time()
    logging.info("Starting inline AMBER relaxation ...")
    try:
        relax_protein(
            unrelaxed_protein_path=unrelaxed_protein.path,
            relaxed_protein_path=relaxed_protein.path,
            max_iterations=relax_max_iterations,
            tolerance=relax_tolerance,
            stiffness=relax_stiffness,
            exclude_residues=[],
            max_outer_iterations=relax_max_outer_iterations,
            use_gpu=relax_use_gpu,
        )
        t_relax1 = time.time()
        logging.info(f"Inline AMBER relaxation completed. Elapsed time: {t_relax1 - t_relax0:.1f}s")
    except relax_errors as e:
        # AMBER minimization can fail on highly disordered structures (ValueError:
        # Minimization failed after 100 attempts) or with OpenMM platform errors
        # (RuntimeError / openmm.OpenMMException). Fall back to the unrelaxed
        # structure so the pipeline completes — unrelaxed PDB is still scientifically
        # valid, pLDDT/PAE unaffected. Exiting 0 here prevents KFP from retrying a
        # deterministic structural failure (and re-running the entire GPU prediction).
        t_relax1 = time.time()
        warning = (
            f"AMBER relaxation failed after {t_relax1 - t_relax0:.1f}s "
            f"({type(e).__name__}: {e}). "
            "Falling back to unrelaxed structure. "
            "pLDDT and PAE scores are unaffected."
        )
        logging.warning(f"RELAX_FALLBACK: {warning}")
        shutil.copy(unrelaxed_protein.path, relaxed_protein.path)
        relaxed_protein.metadata["relax_failed"] = True
        relaxed_protein.metadata["relax_warning"] = warning

    logging.info(f"Fused predict + relax completed. Total elapsed time: {time.time() - t0:.1f}s")
