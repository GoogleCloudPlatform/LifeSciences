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

"""Tests for trials text-match confidence safeguard (#91).

Covers:
  1. Short query (<=4 chars) with high hit count triggers low confidence
  2. Long query with normal hit count does NOT trigger
  3. `confidence` field appears in assessment output when triggered
  4. Relay `trials.text_match_not_mechanism` fires when triggered
  5. Verdict value is unchanged (backward compat)
  6. Phase 3+ ratio trigger fires independently
  7. High hit count trigger fires independently
  8. Relay code is registered in RELAY_CODES
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.trials import _analyze_trials
from dde.core.provenance import RELAY_CODES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_study(
    nct_id: str = "NCT00000001",
    status: str = "RECRUITING",
    phases: list[str] | None = None,
    sponsor: str = "TestSponsor",
) -> dict[str, Any]:
    """Build a minimal study dict matching the shape _extract_study returns."""
    if phases is None:
        phases = ["PHASE2"]
    from dde.commands.trials import _phase_label

    return {
        "nct_id": nct_id,
        "title": f"Study {nct_id}",
        "status": status,
        "phases": phases,
        "phase_label": _phase_label(phases),
        "conditions": ["TestCondition"],
        "interventions": [{"type": "DRUG", "name": "TestDrug"}],
        "sponsor": sponsor,
        "enrollment": 100,
        "start_date": "2024-01-01",
        "completion_date": "2025-12-31",
    }


def _make_studies(
    n: int,
    *,
    phases: list[str] | None = None,
    status: str = "RECRUITING",
) -> list[dict[str, Any]]:
    """Generate n study dicts."""
    return [
        _make_study(
            nct_id=f"NCT{i:08d}",
            status=status,
            phases=phases,
        )
        for i in range(n)
    ]


def _run_analyze(
    query_term: str,
    studies: list[dict[str, Any]],
    active_min_phase: float = 2.0,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Run _analyze_trials and collect relays."""
    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        from dde.core.provenance import relay

        if not any(r["code"] == code for r in relays):
            relays.append(relay(code, message))

    active, metrics, assessment = _analyze_trials(
        query_term,
        studies,
        active_min_phase,
        add_relay,
    )
    return active, metrics, assessment, relays


# ---------------------------------------------------------------------------
# Tests: relay code registration
# ---------------------------------------------------------------------------


def test_text_match_relay_registered():
    """trials.text_match_not_mechanism must be in RELAY_CODES."""
    assert "trials.text_match_not_mechanism" in RELAY_CODES


# ---------------------------------------------------------------------------
# Tests: confidence triggers
# ---------------------------------------------------------------------------


def test_short_query_high_hits_triggers_low_confidence():
    """Short query (<=4 chars) with >200 studies triggers low confidence."""
    studies = _make_studies(250, phases=["PHASE2"])
    _, _, assessment, _relays = _run_analyze("ARTN", studies)

    assert assessment.get("confidence") == "low_text_match_only"
    assert assessment.get("confidence_reason") is not None
    assert "ARTN" in assessment["confidence_reason"]


def test_short_query_high_hits_fires_relay():
    """Low-confidence detection must fire the text_match relay."""
    studies = _make_studies(250, phases=["PHASE2"])
    _, _, _, relays = _run_analyze("ARTN", studies)

    relay_codes = [r["code"] for r in relays]
    assert "trials.text_match_not_mechanism" in relay_codes


def test_short_query_high_hits_verdict_unchanged():
    """Verdict must remain active_pipeline — downstream may switch on it."""
    studies = _make_studies(250, phases=["PHASE2"], status="RECRUITING")
    _, _, assessment, _ = _run_analyze("ARTN", studies)

    assert assessment["verdict"] == "active_pipeline"


def test_long_query_normal_hits_no_confidence_downgrade():
    """Long query with normal hit count should NOT trigger."""
    studies = _make_studies(50, phases=["PHASE2"])
    _, _, assessment, relays = _run_analyze("BRCA1_HUMAN", studies)

    assert "confidence" not in assessment
    relay_codes = [r["code"] for r in relays]
    assert "trials.text_match_not_mechanism" not in relay_codes


def test_short_query_low_hits_triggers_on_short_alone():
    """Short query (<= 4 chars) triggers even with few studies."""
    studies = _make_studies(10, phases=["PHASE2"])
    _, _, assessment, _ = _run_analyze("XYZ", studies)

    # Short query alone is a trigger
    assert assessment.get("confidence") == "low_text_match_only"


def test_high_hit_count_alone_triggers():
    """Hit count >200 triggers even with a longer query."""
    studies = _make_studies(210, phases=["PHASE1"])
    _, _, assessment, relays = _run_analyze("LONGERQUERY", studies)

    assert assessment.get("confidence") == "low_text_match_only"
    relay_codes = [r["code"] for r in relays]
    assert "trials.text_match_not_mechanism" in relay_codes


def test_high_phase3_ratio_triggers():
    """Phase 3+ ratio >5% triggers confidence downgrade."""
    # 100 studies, 6 Phase 3 => 6% > 5% threshold
    phase2_studies = _make_studies(94, phases=["PHASE2"], status="COMPLETED")
    phase3_studies = _make_studies(6, phases=["PHASE3"], status="COMPLETED")
    # Give phase3 studies unique NCT IDs
    for i, s in enumerate(phase3_studies):
        s["nct_id"] = f"NCT_P3_{i:04d}"

    all_studies = phase2_studies + phase3_studies
    # Use a long query so only the ratio trigger fires
    _, _, assessment, _relays = _run_analyze("LONGQUERY", all_studies)

    assert assessment.get("confidence") == "low_text_match_only"
    assert "Phase 3+ ratio" in assessment.get("confidence_reason", "")


def test_no_trigger_when_all_below_thresholds():
    """No trigger when query is long, hits are low, and Phase 3+ ratio is low."""
    # 50 studies, 1 Phase 3 => 2% < 5%, long query, low hit count
    studies = _make_studies(49, phases=["PHASE1"], status="COMPLETED")
    studies.append(
        _make_study(nct_id="NCT_P3_ONLY", phases=["PHASE3"], status="COMPLETED")
    )

    _, _, assessment, relays = _run_analyze("VERYLONGQUERY", studies)

    assert "confidence" not in assessment
    relay_codes = [r["code"] for r in relays]
    assert "trials.text_match_not_mechanism" not in relay_codes


def test_confidence_fields_structure():
    """When triggered, both confidence and confidence_reason must be present."""
    studies = _make_studies(300, phases=["PHASE3"], status="RECRUITING")
    _, _, assessment, _ = _run_analyze("ABC", studies)

    assert "confidence" in assessment
    assert "confidence_reason" in assessment
    assert isinstance(assessment["confidence"], str)
    assert isinstance(assessment["confidence_reason"], str)
    assert len(assessment["confidence_reason"]) > 0


def test_verdict_values_preserved_across_all_cases():
    """Verdict logic must be unchanged regardless of confidence."""
    # active_pipeline: Phase 2+ recruiting
    studies_active = _make_studies(5, phases=["PHASE2"], status="RECRUITING")
    _, _, asmt_active, _ = _run_analyze("VERYLONGQUERY", studies_active)
    assert asmt_active["verdict"] == "active_pipeline"

    # early_pipeline: studies exist but none recruiting at Phase 2+
    studies_early = _make_studies(5, phases=["PHASE1"], status="COMPLETED")
    _, _, asmt_early, _ = _run_analyze("VERYLONGQUERY", studies_early)
    assert asmt_early["verdict"] == "early_pipeline"

    # no_pipeline: no studies
    _, _, asmt_none, _ = _run_analyze("VERYLONGQUERY", [])
    assert asmt_none["verdict"] == "no_pipeline"
