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

"""Pipeline compilation and execution utilities."""

import logging
import os
import threading
from typing import Any

from foldrun_app.core.compile_lock import GLOBAL_COMPILE_LOCK
from foldrun_app.core.pipeline_utils import (  # noqa: F401
    compile_pipeline,
    model_pipeline_load_context,
)

logger = logging.getLogger(__name__)

_PIPELINE_LOAD_LOCK = threading.Lock()


def load_vertex_pipeline(enable_flex_start: bool = False, msa_method: str = "jackhmmer"):
    """
    Load AlphaFold pipeline from vendored copy.

    Args:
        enable_flex_start: Enable DWS FLEX_START scheduling (default: False)
        msa_method: MSA search method - 'jackhmmer' (CPU) or 'mmseqs2' (GPU).

    Returns:
        Pipeline function
    """
    vertex_pipeline_dir = os.path.join(os.path.dirname(__file__), "..", "pipeline")
    with GLOBAL_COMPILE_LOCK, _PIPELINE_LOAD_LOCK:
        with model_pipeline_load_context(
            pkg_prefix="foldrun_app.models.af2.pipeline",
            vertex_pipeline_dir=vertex_pipeline_dir,
            pipeline_module_name="alphafold_inference_pipeline",
        ) as pipe_mod:
            strategy = "FLEX_START" if enable_flex_start else "STANDARD"
            logger.info(
                f"Loading AlphaFold inference pipeline with {strategy} scheduling, msa_method={msa_method}"
            )
            return pipe_mod.create_alphafold_inference_pipeline(
                strategy=strategy, msa_method=msa_method
            )


def get_pipeline_parameters(
    sequence_path: str,
    max_template_date: str,
    model_preset: str,
    project_id: str,
    region: str,
    use_small_bfd: bool = True,
    num_multimer_predictions_per_model: int = 5,
    is_run_relax: bool = True,
) -> dict[str, Any]:
    """
    Build pipeline parameters dictionary.

    Args:
        sequence_path: GCS path to FASTA file
        max_template_date: Maximum template date (YYYY-MM-DD)
        model_preset: 'monomer' or 'multimer'
        project_id: GCP project ID
        region: GCP region
        use_small_bfd: Use small BFD database
        num_multimer_predictions_per_model: Number of predictions per model
        is_run_relax: Run AMBER relaxation

    Returns:
        Parameters dictionary
    """
    return {
        "sequence_path": sequence_path,
        "max_template_date": max_template_date,
        "model_preset": model_preset,
        "project": project_id,
        "region": region,
        "use_small_bfd": use_small_bfd,
        "num_multimer_predictions_per_model": num_multimer_predictions_per_model,
        "is_run_relax": "relax" if is_run_relax else "",
    }
