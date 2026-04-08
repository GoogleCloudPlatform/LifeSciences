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

"""Shared FASTA preprocessing for all models.

Fixes common copy-paste issues from chat interfaces where newlines
get stripped, causing malformed FASTA that fails downstream pipelines.
"""

import re


def fix_fasta(content: str) -> str:
    """Preprocess FASTA content to fix common formatting issues.

    Handles:
    - Missing newlines before '>' headers (chat copy-paste strips them)
    - Sequence concatenated onto header line (no newline after header name)
    - Missing header for leading sequence

    Args:
        content: Raw FASTA content (may be malformed)

    Returns:
        Cleaned FASTA content with proper newlines
    """
    if not content or not content.strip():
        return content

    # Insert newline before any '>' that isn't at the start of a line
    content = re.sub(r"([^>\n])(>)", r"\1\n\2", content)

    # Fix headers where sequence is concatenated onto the header line.
    # Match ">header_text" followed by a run of valid amino acids at end of line.
    # Only split if the trailing AA run is >= 10 chars (likely a sequence, not
    # part of the header name like ">GCN4_PROTEIN_A").
    content = re.sub(
        r"^(>[^\n]*?)([ACDEFGHIKLMNPQRSTVWY]{10,})\s*$",
        r"\1\n\2",
        content,
        flags=re.MULTILINE,
    )

    # If content has '>' headers but doesn't start with one, the leading
    # text is a headerless sequence. Prepend a default header.
    stripped = content.strip()
    if ">" in stripped and not stripped.startswith(">"):
        content = ">chain_A\n" + content

    return content
