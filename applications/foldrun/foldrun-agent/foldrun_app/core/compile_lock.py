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

"""Process-wide re-entrant compilation lock and environment context manager."""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from foldrun_app.core.pipeline_utils import PIPELINE_COMPILE_LOCK

GLOBAL_COMPILE_LOCK = PIPELINE_COMPILE_LOCK

__all__ = [
    "GLOBAL_COMPILE_LOCK",
    "PIPELINE_COMPILE_LOCK",
    "CompileEnvContext",
    "compile_env_context",
    "setup_compile_env_vars",
]


class CompileEnvContext:
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
) -> CompileEnvContext:
    """Apply env_vars under lock immediately and return a CompileEnvContext for `with` restoration."""
    with lock:
        tracked_keys = set(env_vars.keys()) | set(keys_to_clear)
        previous_env = {key: os.environ.get(key) for key in tracked_keys}
        for key in keys_to_clear:
            os.environ.pop(key, None)
        os.environ.update(env_vars)
    return CompileEnvContext(
        lock=lock,
        env_vars=env_vars,
        keys_to_clear=keys_to_clear,
        previous_env=previous_env,
    )


@contextmanager
def compile_env_context(
    env_overrides: dict[str, str | None] | None = None,
    keys_to_clear: tuple[str, ...] = (),
) -> Iterator[dict[str, str]]:
    """Acquire GLOBAL_COMPILE_LOCK, apply env_overrides, and restore os.environ on exit."""
    overrides = env_overrides or {}
    with GLOBAL_COMPILE_LOCK:
        tracked_keys = set(overrides.keys()) | set(keys_to_clear)
        previous_env = {key: os.environ.get(key) for key in tracked_keys}
        try:
            for key in keys_to_clear:
                os.environ.pop(key, None)
            applied: dict[str, str] = {}
            for key, val in overrides.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(val)
                    applied[key] = str(val)
            yield applied
        finally:
            for key, prev_val in previous_env.items():
                if prev_val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev_val
