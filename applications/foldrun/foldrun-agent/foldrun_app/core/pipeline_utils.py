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

"""Model-agnostic KFP pipeline compilation utilities."""

import logging
import re
import tempfile

logger = logging.getLogger(__name__)


def compile_pipeline(
    pipeline_function, output_path: str | None = None, pipeline_name: str = "alphafold-pipeline"
) -> str:
    """
    Compile KFP pipeline.

    Args:
        pipeline_function: Pipeline function to compile
        output_path: Output path for compiled pipeline (optional)
        pipeline_name: Pipeline name

    Returns:
        Path to compiled pipeline JSON
    """
    from kfp import compiler

    if output_path is None:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", pipeline_name).strip("_") or "pipeline"
        with tempfile.NamedTemporaryFile(
            prefix=f"{safe_name}_", suffix=".json", delete=False
        ) as tmp_file:
            output_path = tmp_file.name

    compiler.Compiler().compile(pipeline_func=pipeline_function, package_path=output_path)

    logger.info(f"Compiled pipeline to {output_path}")
    return output_path
