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

"""FoldRun thin-permission native CLI and Jupyter Notebook shim."""

try:
    from importlib.metadata import version as _get_version

    __version__ = _get_version("foldrun-shim")
except Exception:
    __version__ = "0.1.0"

from .auth import FoldRunAuthError, invalidate_token_cache
from .client import (
    AlphaFold2NotebookHelper,
    AlphaFold3NotebookHelper,
    BindCraftNotebookHelper,
    BoltzGenNotebookHelper,
    BoltzNotebookHelper,
    GatewayClient,
    GatewayGPUStatus,
    GatewayJobResult,
    OpenFold3NotebookHelper,
    RFD4ProteinaNotebookHelper,
    af3,
    alphafold2,
    alphafold3,
    bindcraft,
    boltz,
    boltzgen,
    check_gpus,
    openfold3,
    rfd4_proteina,
)

boltz2 = boltz

__all__ = [
    "AlphaFold2NotebookHelper",
    "AlphaFold3NotebookHelper",
    "BindCraftNotebookHelper",
    "BoltzGenNotebookHelper",
    "BoltzNotebookHelper",
    "FoldRunAuthError",
    "GatewayClient",
    "GatewayGPUStatus",
    "GatewayJobResult",
    "OpenFold3NotebookHelper",
    "RFD4ProteinaNotebookHelper",
    "af3",
    "alphafold2",
    "alphafold3",
    "bindcraft",
    "boltz",
    "boltz2",
    "boltzgen",
    "check_gpus",
    "invalidate_token_cache",
    "openfold3",
    "rfd4_proteina",
]
