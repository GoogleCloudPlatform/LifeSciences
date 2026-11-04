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

import importlib
import logging
import os
import re
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Process-wide lock for thread-safe pipeline compilation to prevent
# concurrent compilations from racing on os.environ or sys.modules["config"]
PIPELINE_COMPILE_LOCK = threading.RLock()


@contextmanager
def model_pipeline_load_context(
    pkg_prefix: str,
    vertex_pipeline_dir: str,
    pipeline_module_name: str,
) -> Iterator[object]:
    """Thread-safely reload a model's pipeline config, components, and pipeline module in-place.

    Keeps ``sys.modules[f"{pkg_prefix}.config"]`` and package attributes consistent
    (so ``importlib.reload`` and attribute lookups never see stale module objects)
    while binding bare ``config`` for vendored component decorators during load.
    """
    abs_pipeline_dir = os.path.abspath(vertex_pipeline_dir)
    with PIPELINE_COMPILE_LOCK:
        had_pipeline_dir = abs_pipeline_dir in sys.path
        while abs_pipeline_dir in sys.path:
            sys.path.remove(abs_pipeline_dir)
        sys.path.insert(0, abs_pipeline_dir)

        try:
            parent_pkg = importlib.import_module(pkg_prefix)
            cfg_mod_name = f"{pkg_prefix}.config"
            if cfg_mod_name in sys.modules and sys.modules[cfg_mod_name] is not None:
                cfg_mod = importlib.reload(sys.modules[cfg_mod_name])
            else:
                cfg_mod = importlib.import_module(cfg_mod_name)
            parent_pkg.config = cfg_mod
            sys.modules["config"] = cfg_mod

            comp_prefix = f"{pkg_prefix}.components."
            for mod_name in sorted(sys.modules):
                if (
                    mod_name.startswith(comp_prefix)
                    and not mod_name.endswith(".alphafold_utils")
                    and sys.modules.get(mod_name) is not None
                ):
                    importlib.reload(sys.modules[mod_name])

            comp_init_name = f"{pkg_prefix}.components"
            if comp_init_name in sys.modules and sys.modules[comp_init_name] is not None:
                comp_pkg = importlib.reload(sys.modules[comp_init_name])
                parent_pkg.components = comp_pkg

            pipe_full_name = f"{pkg_prefix}.pipelines.{pipeline_module_name}"
            if pipe_full_name in sys.modules and sys.modules[pipe_full_name] is not None:
                pipe_mod = importlib.reload(sys.modules[pipe_full_name])
            else:
                pipe_mod = importlib.import_module(pipe_full_name)

            yield pipe_mod
        finally:
            if not had_pipeline_dir:
                while abs_pipeline_dir in sys.path:
                    sys.path.remove(abs_pipeline_dir)
            sys.modules.pop("config", None)


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

    with PIPELINE_COMPILE_LOCK:
        compiler.Compiler().compile(pipeline_func=pipeline_function, package_path=output_path)

    logger.info(f"Compiled pipeline to {output_path}")
    return output_path


def load_kfp_inference_pipeline(
    *,
    pkg_prefix: str,
    vertex_pipeline_dir: str,
    pipeline_module_name: str,
    factory_attr: str,
    enable_flex_start: bool = False,
    model_label: str = "KFP",
):
    """Reload a vendored KFP pipeline package under lock and invoke its pipeline factory."""
    with model_pipeline_load_context(
        pkg_prefix=pkg_prefix,
        vertex_pipeline_dir=vertex_pipeline_dir,
        pipeline_module_name=pipeline_module_name,
    ) as pipe_mod:
        strategy = "FLEX_START" if enable_flex_start else "STANDARD"
        logger.info(f"Loading {model_label} inference pipeline with {strategy} scheduling")
        factory = getattr(pipe_mod, factory_attr)
        return factory(strategy=strategy)


_CONFIGURE_SEEDS_COMPONENT = None


def get_configure_seeds_component():
    """Return the shared KFP ``configure_seeds`` component used by OF3 and Boltz-2."""
    global _CONFIGURE_SEEDS_COMPONENT
    if _CONFIGURE_SEEDS_COMPONENT is not None:
        return _CONFIGURE_SEEDS_COMPONENT

    from kfp import dsl

    @dsl.component(base_image="python:3.13-slim")
    def configure_seeds(
        num_model_seeds: int,
        base_seed: int,
    ) -> NamedTuple(  # ty: ignore[invalid-type-form]
        "ConfigureSeedsOutputs",
        [
            ("seed_configs", list),
        ],
    ):
        """Generate seed configs for ParallelFor from ``base_seed``."""
        import random
        from collections import namedtuple

        random.seed(base_seed)
        seeds = [random.randint(0, 2**32 - 1) for _ in range(num_model_seeds)]
        seed_configs = [{"seed_value": s} for s in seeds]

        output = namedtuple("ConfigureSeedsOutputs", ["seed_configs"])
        return output(seed_configs)

    _CONFIGURE_SEEDS_COMPONENT = configure_seeds
    return _CONFIGURE_SEEDS_COMPONENT
