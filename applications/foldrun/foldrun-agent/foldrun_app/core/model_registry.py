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

"""Typed model plugin registry for FoldRun structure prediction backends."""

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)

_BUILTIN_MODEL_PACKAGES: tuple[str, ...] = (
    "foldrun_app.models.af2",
    "foldrun_app.models.of3",
    "foldrun_app.models.boltz2",
    "foldrun_app.models.af3",
    "foldrun_app.models.esmfold2",
    "foldrun_app.models.boltzgen",
    "foldrun_app.models.rfd4_proteina",
    "foldrun_app.models.bindcraft",
)


@dataclass(frozen=True)
class ModelDescriptor:
    """Typed descriptor for a FoldRun structure prediction model backend."""

    name: str
    display_name: str
    aliases: tuple[str, ...]
    job_id_prefixes: tuple[str, ...]
    capabilities: tuple[str, ...]
    input_format: str
    output_format: str
    default_gpus: tuple[str, ...]
    is_enabled: Callable[[], bool]
    get_config: Callable[[], CoreConfig]
    get_tool_classes: Callable[[], dict[str, type[BaseTool]]]
    analyze_tool_key: str
    results_tool_key: str
    viewer_tool_key: str


_MODELS: dict[str, ModelDescriptor] = {}
_ALIASES: dict[str, str] = {}
_bootstrapped = False


def register_model(descriptor: ModelDescriptor) -> ModelDescriptor:
    """Register a typed ``ModelDescriptor`` in the global model registry."""
    _MODELS[descriptor.name] = descriptor
    _ALIASES[descriptor.name.lower()] = descriptor.name
    for alias in descriptor.aliases:
        _ALIASES[alias.lower()] = descriptor.name
    logger.info(f"Registered model plugin: {descriptor.name} ({descriptor.display_name})")
    return descriptor


def ensure_models_registered() -> None:
    """Import built-in model packages once so all descriptors are registered."""
    global _bootstrapped
    if _bootstrapped and len(_MODELS) >= len(_BUILTIN_MODEL_PACKAGES):
        return
    _bootstrapped = True
    for pkg in _BUILTIN_MODEL_PACKAGES:
        importlib.import_module(pkg)


def find_model(name_or_alias: str | None) -> ModelDescriptor | None:
    """Look up a registered ``ModelDescriptor`` by canonical name or alias (returns None if unknown)."""
    if not isinstance(name_or_alias, str):
        return None
    cleaned = name_or_alias.strip().lower()
    if not cleaned:
        return None
    ensure_models_registered()
    canonical = _ALIASES.get(cleaned)
    if canonical is None:
        return None
    return _MODELS.get(canonical)


def get_model(model_id: str) -> ModelDescriptor:
    """Get a registered ``ModelDescriptor`` by canonical ID or alias."""
    descriptor = find_model(model_id)
    if descriptor is None:
        available = ", ".join(_MODELS.keys()) or "(none)"
        raise ValueError(f"Unknown model '{model_id}'. Available: {available}")
    return descriptor


def normalize_model_type(raw: str | None) -> str | None:
    """Normalize a raw ``model_type`` label or alias to a canonical backend name."""
    descriptor = find_model(raw)
    return descriptor.name if descriptor is not None else None


def infer_model_from_job_id(job_id: str | None) -> ModelDescriptor | None:
    """Infer a ``ModelDescriptor`` from a pipeline job ID or display name prefix."""
    if not isinstance(job_id, str) or not job_id.strip():
        return None
    ensure_models_registered()
    short_id = job_id.strip().rstrip("/").rsplit("/", 1)[-1].lower()
    for descriptor in _MODELS.values():
        if short_id.startswith(descriptor.job_id_prefixes):
            return descriptor
    return None


def infer_model_type_from_job_id(job_id: str | None) -> str | None:
    """Infer canonical model type string from a pipeline job ID or display name prefix."""
    descriptor = infer_model_from_job_id(job_id)
    return descriptor.name if descriptor is not None else None


def get_registered_models(*, enabled_only: bool = False) -> dict[str, ModelDescriptor]:
    """Return mapping of canonical model ID -> ``ModelDescriptor``."""
    ensure_models_registered()
    if not enabled_only:
        return dict(_MODELS)
    return {name: desc for name, desc in _MODELS.items() if desc.is_enabled()}


def list_models(*, enabled_only: bool = False) -> list[str]:
    """List registered canonical model IDs."""
    return list(get_registered_models(enabled_only=enabled_only).keys())
