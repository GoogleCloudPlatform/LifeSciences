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

"""NCBI E-utilities shared configuration.

The API key and QPS constant are derived from a single ``os.environ.get``
call.  Both ``commands/pubmed.py`` and ``commands/geo.py`` import from
here so the two cannot diverge: when the key is present, QPS is 10 and
every request carries the key; when it is absent, QPS stays at 3 and no
``api_key`` parameter appears.
"""

from __future__ import annotations

import os
from urllib.parse import quote

_NCBI_KEY: str = os.environ.get("NCBI_API_KEY", "")

#: NCBI allows 3 req/s without an API key, 10 req/s with one.
EUTILS_QPS: float = 10.0 if _NCBI_KEY else 3.0


def api_key_suffix() -> str:
    """Return ``&api_key=...`` for URL string concatenation, or ``""``."""
    return f"&api_key={quote(_NCBI_KEY, safe='')}" if _NCBI_KEY else ""


def api_key_params() -> dict[str, str]:
    """Return ``{"api_key": ...}`` for the ``params=`` kwarg, or ``{}``."""
    return {"api_key": _NCBI_KEY} if _NCBI_KEY else {}
