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
from typing import Any, Dict

from foldrun_app.core.pipeline_utils import compile_pipeline  # noqa: F401

logger = logging.getLogger(__name__)


def load_vertex_pipeline(enable_flex_start: bool = False, msa_method: str = "jackhmmer"):
    """
    Load AlphaFold pipeline from vendored copy.

    Args:
        enable_flex_start: Enable DWS FLEX_START scheduling (default: False)
        msa_method: MSA search method - 'jackhmmer' (CPU) or 'mmseqs2' (GPU).

    Returns:
        Pipeline function
    """
    import sys

    # Add vertex_pipeline to sys.path so 'config' module can be imported
    # This is needed for component compilation
    vertex_pipeline_dir = os.path.join(os.path.dirname(__file__), "..", "pipeline")
    vertex_pipeline_dir = os.path.abspath(vertex_pipeline_dir)

    if vertex_pipeline_dir not in sys.path:
        sys.path.insert(0, vertex_pipeline_dir)

    # Evict cached 'config' module so AF2's pipeline/config.py is used.
    # Both AF2 and OF3 components use `import config as config` (bare import).
    # Python caches the first one — evicting ensures the correct config loads.
    sys.modules.pop("config", None)

    from foldrun_app.models.af2.pipeline.pipelines.alphafold_inference_pipeline import (
        create_alphafold_inference_pipeline,
    )

    # Create pipeline with specified scheduling strategy
    strategy = "FLEX_START" if enable_flex_start else "STANDARD"
    logger.info(
        f"Loading AlphaFold inference pipeline with {strategy} scheduling, msa_method={msa_method}"
    )

    pipeline = create_alphafold_inference_pipeline(strategy=strategy, msa_method=msa_method)

    return pipeline


def get_pipeline_parameters(
    sequence_path: str,
    max_template_date: str,
    model_preset: str,
    project_id: str,
    region: str,
    use_small_bfd: bool = True,
    num_multimer_predictions_per_model: int = 5,
    is_run_relax: bool = True,
) -> Dict[str, Any]:
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
