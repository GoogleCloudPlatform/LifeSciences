# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""AlphaFold 3 confidence and quality metrics utilities."""

from typing import Any


def compute_ranking_score(
    summary_confidences: dict[str, Any],
    is_multimer: bool | None = None,
) -> float | None:
    """Compute or extract the AlphaFold 3 ranking score from summary confidences.

    DeepMind's AlphaFold 3 ranking formula:
    - If 'ranking_score' is explicitly provided in the summary dictionary, return it (including 0.0).
    - If is_multimer is True or 'iptm' is present (not None), ranking_score = 0.8 * ipTM + 0.2 * pTM (multimer complex).
      Even if ipTM is 0.0 (e.g. failed interface), this formula is preserved (0.2 * pTM) rather than falling
      back to monomer pTM, preventing artificial score inflation on failed complexes.
    - If 'iptm' is absent or None (and is_multimer is not True), ranking_score = pTM (monomer).
    - Otherwise, returns None.

    Args:
        summary_confidences: Confidence summary dictionary from AF3 model output.
        is_multimer: Optional flag indicating whether input is a multimer complex.

    Returns:
        Float ranking score in range [0.0, 1.0], or None if confidence metrics are missing.
    """
    raw_score = summary_confidences.get("ranking_score")
    if raw_score is not None:
        try:
            return float(raw_score)
        except (ValueError, TypeError):
            pass

    ptm = summary_confidences.get("ptm")
    iptm = summary_confidences.get("iptm")

    try:
        ptm_val = float(ptm) if ptm is not None else None
    except (ValueError, TypeError):
        ptm_val = None

    try:
        iptm_val = float(iptm) if iptm is not None else None
    except (ValueError, TypeError):
        iptm_val = None

    multimer = is_multimer if is_multimer is not None else (iptm_val is not None)

    if multimer:
        i_val = iptm_val if iptm_val is not None else 0.0
        p_val = ptm_val if ptm_val is not None else 0.0
        return 0.8 * i_val + 0.2 * p_val
    elif ptm_val is not None:
        return ptm_val

    return None
