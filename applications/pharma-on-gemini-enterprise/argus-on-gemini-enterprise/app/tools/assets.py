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

"""Per-invocation asset store for generated images/charts.

Chart and infographic tools write PNG bytes here and return a lightweight
`asset://<id>` token. The whitepaper markdown references assets with normal
markdown image syntax, e.g. `![Cash runway](asset://chart_runway)`, and the PDF
renderer resolves those tokens to the actual image files at render time.

Using a token + temp file (rather than base64 inline in the markdown) keeps the
markdown compact for the LLM and works reliably with xhtml2pdf's file-based
image loading, including inside the Agent Engine container (writes to /tmp).
"""

import os
import re
import tempfile

_ASSET_DIR = os.path.join(tempfile.gettempdir(), "argus_assets")
os.makedirs(_ASSET_DIR, exist_ok=True)

_TOKEN_RE = re.compile(r"asset://([A-Za-z0-9_\-]+)")


def save_asset(asset_id: str, png_bytes: bytes) -> str:
    """Persist PNG bytes under an id and return the `asset://<id>` token."""
    asset_id = re.sub(r"[^A-Za-z0-9_\-]", "_", asset_id)
    path = os.path.join(_ASSET_DIR, f"{asset_id}.png")
    with open(path, "wb") as f:
        f.write(png_bytes)
    return f"asset://{asset_id}"


def asset_path(asset_id: str) -> str | None:
    path = os.path.join(_ASSET_DIR, f"{asset_id}.png")
    return path if os.path.exists(path) else None


def resolve_tokens_to_paths(markdown_text: str) -> str:
    """Replace `asset://<id>` tokens with absolute file paths so the markdown
    image tags point at real files for the PDF renderer. Unknown tokens are
    left as-is (they will simply render as broken/absent images)."""

    def repl(m: re.Match) -> str:
        p = asset_path(m.group(1))
        return p if p else m.group(0)

    return _TOKEN_RE.sub(repl, markdown_text)
