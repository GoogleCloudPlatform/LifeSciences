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
"""A component encapsulating AlphaFold model relaxation."""

from typing import List

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.ALPHAFOLD_COMPONENTS_IMAGE)
def relax(
    unrelaxed_protein: Input[Artifact],
    relaxed_protein: Output[Artifact],
    max_iterations: int = 0,
    tolerance: float = 2.39,
    stiffness: float = 10.0,
    exclude_residues: List[str] = [],
    max_outer_iterations: int = 3,
    use_gpu: bool = True,
    tf_force_unified_memory: str = "",
    xla_python_client_mem_fraction: str = "",
):
    """Configures and runs Amber relaxation."""

    import logging
    import os
    import time

    from alphafold_utils import relax_protein

    os.environ["TF_FORCE_UNIFIED_MEMORY"] = tf_force_unified_memory
    os.environ["XLA_PYTHON_CLIENT_MEM_FRACTION"] = xla_python_client_mem_fraction

    import shutil

    t0 = time.time()

    relaxed_protein.uri = f"{relaxed_protein.uri}.pdb"
    relaxed_protein.metadata["category"] = "relaxed_protein"
    relaxed_protein.metadata["relax_failed"] = False

    # Read mean pLDDT from the b-factor column of the unrelaxed PDB.
    # AlphaFold writes per-residue pLDDT into b-factors at prediction time.
    def _mean_plddt_from_pdb(path: str) -> float:
        scores = []
        with open(path) as f:
            for line in f:
                if line.startswith(("ATOM  ", "HETATM")):
                    try:
                        scores.append(float(line[60:66]))
                    except ValueError:
                        pass
        return sum(scores) / len(scores) if scores else 100.0

    mean_plddt = _mean_plddt_from_pdb(unrelaxed_protein.path)
    logging.info(f"Mean pLDDT: {mean_plddt:.1f}")

    # Skip relaxation for very low confidence structures (pLDDT < 50).
    # These are too disordered for AMBER to converge meaningfully, and attempting
    # it wastes GPU time. The fallback catch below handles unexpected failures
    # on structures that pass this threshold but still fail to minimize.
    if mean_plddt < 50.0:
        warning = (
            f"Skipping AMBER relaxation — mean pLDDT {mean_plddt:.1f} < 50 "
            "(very low confidence; structure too disordered to relax meaningfully). "
            "Unrelaxed structure used. pLDDT and PAE scores are unaffected."
        )
        logging.warning(f"RELAX_FALLBACK: {warning}")
        shutil.copy(unrelaxed_protein.path, relaxed_protein.path)
        relaxed_protein.metadata["relax_failed"] = True
        relaxed_protein.metadata["relax_warning"] = warning
        return

    logging.info("Starting model relaxation ...")
    try:
        relax_protein(
            unrelaxed_protein_path=unrelaxed_protein.path,
            relaxed_protein_path=relaxed_protein.path,
            max_iterations=max_iterations,
            tolerance=tolerance,
            stiffness=stiffness,
            exclude_residues=exclude_residues,
            max_outer_iterations=max_outer_iterations,
            use_gpu=use_gpu,
        )
        t1 = time.time()
        logging.info(f"Model relaxation completed. Elapsed time: {t1 - t0}")

    except ValueError as e:
        # AMBER minimization can fail on higher-confidence structures with
        # locally strained geometry. Fall back to unrelaxed — still valid.
        t1 = time.time()
        warning = (
            f"AMBER relaxation failed after {t1 - t0:.1f}s: {e}. "
            "Falling back to unrelaxed structure. "
            "pLDDT and PAE scores are unaffected."
        )
        logging.warning(f"RELAX_FALLBACK: {warning}")
        shutil.copy(unrelaxed_protein.path, relaxed_protein.path)
        relaxed_protein.metadata["relax_failed"] = True
        relaxed_protein.metadata["relax_warning"] = warning
