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

"""Pipeline compilation and execution utilities for OF3."""

import os

from foldrun_app.core.compile_lock import GLOBAL_COMPILE_LOCK  # noqa: F401
from foldrun_app.core.pipeline_utils import (  # noqa: F401
    compile_pipeline,
    load_kfp_inference_pipeline,
    model_pipeline_load_context,
)


def load_vertex_pipeline(enable_flex_start: bool = False):
    """Load OF3 pipeline from vendored copy."""
    return load_kfp_inference_pipeline(
        pkg_prefix="foldrun_app.models.of3.pipeline",
        vertex_pipeline_dir=os.path.join(os.path.dirname(__file__), "..", "pipeline"),
        pipeline_module_name="of3_inference_pipeline",
        factory_attr="create_of3_inference_pipeline",
        enable_flex_start=enable_flex_start,
        model_label="OF3",
    )
