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

"""Pipeline compilation and execution utilities for BindCraft."""

import importlib
import logging
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from foldrun_app.core import pipeline_utils as _core_pipeline_utils
from foldrun_app.core.pipeline_utils import compile_pipeline  # noqa: F401

try:
    from foldrun_app.core.compile_lock import (
        GLOBAL_COMPILE_LOCK,
        setup_compile_env_vars,
    )
except ImportError:
    GLOBAL_COMPILE_LOCK = getattr(_core_pipeline_utils, "PIPELINE_COMPILE_LOCK", None)
    if GLOBAL_COMPILE_LOCK is None:
        GLOBAL_COMPILE_LOCK = threading.RLock()
        _core_pipeline_utils.PIPELINE_COMPILE_LOCK = GLOBAL_COMPILE_LOCK

    class _CompileEnvContext:
        """Re-entrant context manager that serializes compile env access and restores os.environ on exit."""

        def __init__(
            self,
            lock: threading.RLock,
            env_vars: dict[str, str],
            keys_to_clear: tuple[str, ...] = (),
            previous_env: dict[str, str | None] | None = None,
        ):
            self._lock = lock
            self._env_vars = env_vars
            self._keys_to_clear = keys_to_clear
            self._previous_env = previous_env if previous_env is not None else {}

        def __enter__(self) -> dict[str, str]:
            self._lock.acquire()
            try:
                for key in self._keys_to_clear:
                    os.environ.pop(key, None)
                os.environ.update(self._env_vars)
                return self._env_vars
            except Exception:
                self._lock.release()
                raise

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            try:
                for key, prev_val in self._previous_env.items():
                    if prev_val is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = prev_val
            finally:
                self._lock.release()

    def setup_compile_env_vars(
        env_vars: dict[str, str],
        keys_to_clear: tuple[str, ...] = (),
        lock: threading.RLock = GLOBAL_COMPILE_LOCK,
    ) -> _CompileEnvContext:
        """Apply env_vars under lock immediately and return a context manager for restoration."""
        with lock:
            tracked_keys = set(env_vars.keys()) | set(keys_to_clear)
            previous_env = {key: os.environ.get(key) for key in tracked_keys}
            for key in keys_to_clear:
                os.environ.pop(key, None)
            os.environ.update(env_vars)
        return _CompileEnvContext(
            lock=lock,
            env_vars=env_vars,
            keys_to_clear=keys_to_clear,
            previous_env=previous_env,
        )


try:
    from foldrun_app.core.pipeline_utils import model_pipeline_load_context
except ImportError:

    @contextmanager
    def model_pipeline_load_context(
        pkg_prefix: str,
        vertex_pipeline_dir: str,
        pipeline_module_name: str,
    ) -> Iterator[object]:
        """Thread-safely reload a model's pipeline config, components, and pipeline module in-place."""
        from unittest.mock import NonCallableMock

        abs_pipeline_dir = os.path.abspath(vertex_pipeline_dir)
        with GLOBAL_COMPILE_LOCK:
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
                    existing_pipe_mod = sys.modules[pipe_full_name]
                    mocked_attrs = {
                        k: v
                        for k, v in vars(existing_pipe_mod).items()
                        if isinstance(v, NonCallableMock)
                    }
                    pipe_mod = importlib.reload(existing_pipe_mod)
                    for k, v in mocked_attrs.items():
                        setattr(pipe_mod, k, v)
                else:
                    pipe_mod = importlib.import_module(pipe_full_name)

                yield pipe_mod
            finally:
                if not had_pipeline_dir:
                    while abs_pipeline_dir in sys.path:
                        sys.path.remove(abs_pipeline_dir)
                sys.modules.pop("config", None)


logger = logging.getLogger(__name__)


def load_vertex_pipeline(enable_flex_start: bool = False):
    """Load BindCraft pipeline from vendored copy.

    Args:
        enable_flex_start: Enable DWS FLEX_START scheduling (default: False).

    Returns:
        Pipeline function.
    """
    vertex_pipeline_dir = os.path.join(os.path.dirname(__file__), "..", "pipeline")
    with model_pipeline_load_context(
        pkg_prefix="foldrun_app.models.bindcraft.pipeline",
        vertex_pipeline_dir=vertex_pipeline_dir,
        pipeline_module_name="bindcraft_inference_pipeline",
    ) as pipe_mod:
        strategy = "FLEX_START" if enable_flex_start else "STANDARD"
        logger.info(f"Loading BindCraft inference pipeline with {strategy} scheduling")
        return pipe_mod.create_bindcraft_inference_pipeline(strategy=strategy)
