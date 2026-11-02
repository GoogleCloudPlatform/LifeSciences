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

from foldrun_app.core.compile_lock import GLOBAL_COMPILE_LOCK  # noqa: F401
from foldrun_app.core.pipeline_utils import (  # noqa: F401
    compile_pipeline,
    model_pipeline_load_context,
)

logger = logging.getLogger(__name__)


def load_vertex_pipeline(enable_flex_start: bool = False):
    """Load BOLTZ2 pipeline from vendored copy.

    Args:
        enable_flex_start: Enable DWS FLEX_START scheduling (default: False).

    Returns:
        Pipeline function.
    """
    vertex_pipeline_dir = os.path.join(os.path.dirname(__file__), "..", "pipeline")
    with model_pipeline_load_context(
        pkg_prefix="foldrun_app.models.boltz2.pipeline",
        vertex_pipeline_dir=vertex_pipeline_dir,
        pipeline_module_name="boltz2_inference_pipeline",
    ) as pipe_mod:
        strategy = "FLEX_START" if enable_flex_start else "STANDARD"
        logger.info(f"Loading BOLTZ2 inference pipeline with {strategy} scheduling")
        return pipe_mod.create_boltz2_inference_pipeline(strategy=strategy)
