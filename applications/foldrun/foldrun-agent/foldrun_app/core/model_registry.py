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

"""Model plugin registry."""

import logging

logger = logging.getLogger(__name__)
_MODELS = {}


def register_model(model_id: str, model_module):
    """Register a model plugin."""
    _MODELS[model_id] = model_module
    logger.info(f"Registered model: {model_id}")


def get_model(model_id: str):
    """Get a registered model plugin."""
    if model_id not in _MODELS:
        available = ", ".join(_MODELS.keys()) or "(none)"
        raise ValueError(f"Unknown model '{model_id}'. Available: {available}")
    return _MODELS[model_id]


def list_models() -> list:
    """List registered model IDs."""
    return list(_MODELS.keys())
