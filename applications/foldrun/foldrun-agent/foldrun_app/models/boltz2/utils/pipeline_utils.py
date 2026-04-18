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

"""Pipeline compilation and execution utilities for BOLTZ2."""

import logging
import os
import sys

from foldrun_app.core.pipeline_utils import compile_pipeline  # noqa: F401

logger = logging.getLogger(__name__)


def load_vertex_pipeline(enable_flex_start: bool = False):
    """Load BOLTZ2 pipeline from vendored copy.

    Args:
        enable_flex_start: Enable DWS FLEX_START scheduling (default: False).

    Returns:
        Pipeline function.
    """
    # Add pipeline dir to sys.path so 'config' module can be imported by components
    vertex_pipeline_dir = os.path.join(os.path.dirname(__file__), "..", "pipeline")
    vertex_pipeline_dir = os.path.abspath(vertex_pipeline_dir)

    if vertex_pipeline_dir not in sys.path:
        sys.path.insert(0, vertex_pipeline_dir)

    # Evict cached 'config' module so BOLTZ2's pipeline/config.py is used.
    # Both AF2 and BOLTZ2 components use `import config as config` (bare import).
    # Python caches the first one — evicting ensures the correct config loads.
    sys.modules.pop("config", None)

    from foldrun_app.models.boltz2.pipeline.pipelines.boltz2_inference_pipeline import (
        create_boltz2_inference_pipeline,
    )

    strategy = "FLEX_START" if enable_flex_start else "STANDARD"
    logger.info(f"Loading BOLTZ2 inference pipeline with {strategy} scheduling")

    pipeline = create_boltz2_inference_pipeline(strategy=strategy)
    return pipeline
