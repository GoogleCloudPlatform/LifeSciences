#!/usr/bin/env python3
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

"""Tests for bounded structure screening (#38).

Covers the four required validation scenarios verbatim from the
refinement comment:

1. Low score on one conformation (borderline/not-druggable verdict)
   -> correctly scoped assessment, relay preserved, next-conformation
   suggestion.

2. Favorable model score on a non-experimental structure
   -> fpocket.conformation_dependent relay preserved, assessment does
   not overclaim.

3. An irrelevant high-scoring site (a cavity elsewhere in the
   structure, not at the intended intervention site)
   -> correctly rejected as not relevant, not counted as a pass.

4. A modality for which pocket geometry is not the relevant test
   -> correctly produces a scoped/not-applicable assessment rather
   than a forced pocket score.

Also tests:
- Budget respect (screening stops within the declared budget)
- Structure retrieval vs. new-prediction distinction
- Relay preservation through the screening layer
- Evidence status mapping
- Assessment record schema compliance

Run with:
    PYTHONPATH=tools python3 tests/test_structure_screening.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.structure_screening import (
    PocketResult,
    ScreenBudget,
    StructureCandidate,
    build_assessment_record,
    check_modality_applicability,
    check_site_relevance,
    classify_structure_source,
    screen_structures,
)
from dde.core.evidence import validate_assessment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _check(name: str, fn: Any) -> None:
    global _PASS, _FAIL
    try:
        fn()
        _PASS += 1
        print(f"  PASS  {name}")
    except Exception:
        _FAIL += 1
        print(f"  FAIL  {name}")
        traceback.print_exc()
        print()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _low_score_pocket_result() -> PocketResult:
    """A pocket result with a sub-cutoff score on one conformation.

    Simulates the CDK2 2W1D case: a drugged site that scores 0.293.
    """
    return PocketResult(
        verdict="no-druggable-pocket-in-this-conformation",
        drug_score=0.293,
        best_pocket_rank=1,
        structure_name="CDK2-2W1D.cif",
        is_experimental=True,
        relays=[
            {
                "code": "fpocket.single_conformation",
                "message": (
                    "0.293 in CDK2-2W1D.cif; the same CDK2 ATP site "
                    "scores 0.17, 0.29 and 0.94 in three crystals."
                ),
            }
        ],
        analysis_path="raw/structures/CDK2-2W1D.pocket.analysis.json",
        threshold_set="pocket@1.0",
        thresholds_applied={"druggable_dscore": 0.5, "borderline_dscore": 0.2},
    )


def _favorable_model_pocket_result() -> PocketResult:
    """A favorable score on a non-experimental (predicted) structure.

    The score is high but the structure is an AlphaFold model, so
    fpocket.conformation_dependent fires.
    """
    return PocketResult(
        verdict="druggable-pocket-present",
        drug_score=0.872,
        best_pocket_rank=1,
        structure_name="AF-P04637-F1.cif",
        is_experimental=False,
        relays=[
            {
                "code": "fpocket.conformation_dependent",
                "message": (
                    "AF-P04637-F1.cif is not established as an "
                    "experimental structure (no EXPDTA/_exptl.method "
                    "record). Druggability scores are conformation-"
                    "dependent and were trained on crystal structures."
                ),
            },
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": (
                    "0.872 is cavity shape in AF-P04637-F1.cif; "
                    "not an affinity, not a potency, not evidence "
                    "a compound binds."
                ),
            },
        ],
        analysis_path="raw/structures/AF-P04637-F1.pocket.analysis.json",
        threshold_set="pocket@1.0",
        thresholds_applied={"druggable_dscore": 0.5, "borderline_dscore": 0.2},
    )


def _irrelevant_site_pocket_result() -> PocketResult:
    """A high-scoring pocket that is NOT at the intended intervention site.

    The structure has a druggable pocket, but it's at a different
    location from the target site. When --near was used, the verdict
    reports the site, not the global best.
    """
    return PocketResult(
        verdict="no-pocket-at-site-in-this-conformation",
        drug_score=0.0,  # At the site, no pocket
        best_pocket_rank=None,
        structure_name="MDA5-AF2.cif",
        is_experimental=False,
        relays=[
            {
                "code": "fpocket.single_conformation",
                "message": (
                    "no pocket at the requested site in MDA5-AF2.cif; "
                    "the same CDK2 ATP site scores 0.17, 0.29 and 0.94 "
                    "in three crystals."
                ),
            },
            {
                "code": "fpocket.conformation_dependent",
                "message": (
                    "MDA5-AF2.cif is not established as an experimental structure."
                ),
            },
        ],
        analysis_path="raw/structures/MDA5-AF2.pocket.analysis.json",
        threshold_set="pocket@1.0",
        site_query="A:145,A:146,B:12",
        site_relevant=False,
    )


def _druggable_pocket_result() -> PocketResult:
    """A druggable pocket on an experimental structure."""
    return PocketResult(
        verdict="druggable-pocket-present",
        drug_score=0.939,
        best_pocket_rank=1,
        structure_name="CDK2-1HCK.pdb",
        is_experimental=True,
        relays=[
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": (
                    "0.939 is cavity shape in CDK2-1HCK.pdb; "
                    "not an affinity, not a potency, not evidence "
                    "a compound binds."
                ),
            }
        ],
        analysis_path="raw/structures/CDK2-1HCK.pocket.analysis.json",
        threshold_set="pocket@1.0",
    )


# ===========================================================================
# Tests
# ===========================================================================

print("=" * 60)
print("test_structure_screening.py — issue #38 structure screening")
print("=" * 60)


# ---------------------------------------------------------------------------
# 1. Low score on one conformation
# ---------------------------------------------------------------------------
print("\n--- Scenario 1: Low score on one conformation ---")


def test_low_score_scoped_assessment():
    """A low score on one conformation produces an 'insufficient' assessment
    with the single_conformation relay preserved and a next-conformation
    suggestion."""
    result = _low_score_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )

    # Evidence status: insufficient, NOT contradicted
    assert assessment["evidence_status"] == "insufficient", (
        f"expected 'insufficient', got {assessment['evidence_status']!r}; "
        "a sub-cutoff score on one conformation does not contradict "
        "druggability (CDK2 calibration: 0.17-0.94)"
    )

    # Relay preserved
    relay_codes = [r["code"] for r in assessment.get("relay_codes", [])]
    assert "fpocket.single_conformation" in relay_codes, (
        f"fpocket.single_conformation relay not preserved; relay_codes: {relay_codes}"
    )

    # CDK2 calibration numbers in rationale
    assert "CDK2" in assessment["rationale"] or "0.94" in assessment["rationale"], (
        "CDK2 calibration numbers not in rationale"
    )

    # Next conformation suggestion
    assert (
        "another conformation" in assessment["rationale"].lower()
        or "additional conformation" in assessment["rationale"].lower()
        or "next discriminating characterization" in assessment["rationale"].lower()
    ), "next-conformation suggestion not in rationale"

    # Schema valid
    errors = validate_assessment(assessment)
    assert errors == [], f"assessment validation errors: {errors}"


_check(
    "low score -> insufficient, relay preserved, next-conformation suggested",
    test_low_score_scoped_assessment,
)


def test_low_score_does_not_claim_undruggable():
    """The rationale must NOT say 'the target is not druggable' or
    'undruggable' — only that no druggable pocket was found in this
    conformation."""
    result = _low_score_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    rationale_lower = assessment["rationale"].lower()

    # Must NOT contain absolute claims
    assert "target is not druggable" not in rationale_lower, (
        "rationale makes absolute undruggability claim"
    )
    assert "target is undruggable" not in rationale_lower, (
        "rationale makes absolute undruggability claim"
    )

    # Must scope to "this conformation"
    assert "conformation" in rationale_lower, "rationale does not scope to conformation"


_check(
    "low score -> rationale does not claim 'undruggable'",
    test_low_score_does_not_claim_undruggable,
)


def test_borderline_score_scoped():
    """A borderline score also produces 'insufficient' with the relay."""
    result = PocketResult(
        verdict="borderline",
        drug_score=0.35,
        best_pocket_rank=1,
        structure_name="TARGET-1ABC.pdb",
        is_experimental=True,
        relays=[
            {
                "code": "fpocket.single_conformation",
                "message": (
                    "0.35 in TARGET-1ABC.pdb; the same CDK2 ATP site "
                    "scores 0.17, 0.29 and 0.94 in three crystals."
                ),
            }
        ],
        analysis_path="raw/structures/TARGET-1ABC.pocket.analysis.json",
        threshold_set="pocket@1.0",
    )
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    assert assessment["evidence_status"] == "insufficient"
    relay_codes = [r["code"] for r in assessment.get("relay_codes", [])]
    assert "fpocket.single_conformation" in relay_codes


_check("borderline score -> insufficient with relay", test_borderline_score_scoped)


# ---------------------------------------------------------------------------
# 2. Favorable model score on a non-experimental structure
# ---------------------------------------------------------------------------
print("\n--- Scenario 2: Favorable model score (non-experimental) ---")


def test_favorable_model_score_does_not_overclaim():
    """A favorable score on a non-experimental structure produces an
    assessment that does not overclaim. The conformation_dependent relay
    is preserved and the assessment notes the model constraint."""
    result = _favorable_model_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )

    # The relay codes must be preserved
    relay_codes = [r["code"] for r in assessment.get("relay_codes", [])]
    assert "fpocket.conformation_dependent" in relay_codes, (
        f"fpocket.conformation_dependent relay not preserved; "
        f"relay_codes: {relay_codes}"
    )
    assert "fpocket.druggability_is_not_affinity" in relay_codes, (
        f"fpocket.druggability_is_not_affinity relay not preserved; "
        f"relay_codes: {relay_codes}"
    )

    # Confidence must be low for non-experimental structures
    assert assessment.get("confidence") == "low", (
        f"expected confidence 'low' for non-experimental, "
        f"got {assessment.get('confidence')!r}"
    )

    # Rationale must mention non-experimental / model constraint
    rationale_lower = assessment["rationale"].lower()
    assert (
        "not established as experimental" in rationale_lower
        or "non-experimental" in rationale_lower
        or "conformation_dependent" in rationale_lower
    ), "rationale does not note non-experimental structure"

    # Schema valid
    errors = validate_assessment(assessment)
    assert errors == [], f"assessment validation errors: {errors}"


_check(
    "favorable model score -> relays preserved, does not overclaim",
    test_favorable_model_score_does_not_overclaim,
)


def test_model_score_both_directions():
    """The rationale must note that the score constrains the model in
    BOTH directions: a low score is not evidence against druggability,
    and a high score is not evidence for it."""
    result = _favorable_model_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    rationale_lower = assessment["rationale"].lower()
    assert "both directions" in rationale_lower or (
        "not evidence" in rationale_lower
        and ("against" in rationale_lower or "for it" in rationale_lower)
    ), "rationale does not note both-directions constraint"


_check(
    "model score rationale notes both-directions constraint",
    test_model_score_both_directions,
)


# ---------------------------------------------------------------------------
# 3. Irrelevant high-scoring site
# ---------------------------------------------------------------------------
print("\n--- Scenario 3: Irrelevant high-scoring site ---")


def test_irrelevant_site_rejected():
    """A high-scoring cavity elsewhere in the structure (not at the
    intended intervention site) is correctly rejected as not relevant
    and not counted as a pass."""
    result = _irrelevant_site_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
        intended_site_residues=["A:145", "A:146", "B:12"],
    )

    # Evidence status: NOT supported
    assert assessment["evidence_status"] != "supported", (
        f"expected non-supported status for irrelevant site, "
        f"got {assessment['evidence_status']!r}"
    )
    assert assessment["evidence_status"] == "insufficient", (
        f"expected 'insufficient', got {assessment['evidence_status']!r}"
    )

    # Schema valid
    errors = validate_assessment(assessment)
    assert errors == [], f"assessment validation errors: {errors}"


_check("irrelevant site -> not counted as pass", test_irrelevant_site_rejected)


def test_irrelevant_site_rationale():
    """The rationale explains why a cavity elsewhere doesn't count."""
    result = _irrelevant_site_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
        intended_site_residues=["A:145", "A:146", "B:12"],
    )
    rationale_lower = assessment["rationale"].lower()
    assert (
        "not at the intended" in rationale_lower
        or "no pocket" in rationale_lower
        or "no-pocket-at-site" in rationale_lower
    ), "rationale does not explain site irrelevance"


_check(
    "irrelevant site -> rationale explains rejection", test_irrelevant_site_rationale
)


def test_global_druggable_with_site_specified_is_insufficient():
    """A globally druggable pocket, when the caller specified intended
    site residues but did NOT use --near, should be 'insufficient'
    because site relevance cannot be confirmed."""
    result = _druggable_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
        intended_site_residues=["A:145", "A:146"],
    )
    # The pocket is druggable globally, but we have site info and
    # can't confirm it's at the right place
    assert assessment["evidence_status"] == "insufficient", (
        f"expected 'insufficient' when site specified but not queried, "
        f"got {assessment['evidence_status']!r}"
    )


_check(
    "global druggable + site specified -> insufficient (unconfirmed relevance)",
    test_global_druggable_with_site_specified_is_insufficient,
)


# ---------------------------------------------------------------------------
# 4. Modality where pocket geometry is not the relevant test
# ---------------------------------------------------------------------------
print("\n--- Scenario 4: Inapplicable modality ---")


def test_antibody_modality_not_applicable():
    """An antibody modality produces 'not_yet_applicable' rather than
    forcing a pocket score."""
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=_druggable_pocket_result(),  # Would be druggable
        modality="antibody",
    )
    assert assessment["evidence_status"] == "not_yet_applicable", (
        f"expected 'not_yet_applicable' for antibody, "
        f"got {assessment['evidence_status']!r}"
    )
    # Must NOT have a pocket score in the record
    assert "evidence" not in assessment or assessment.get("evidence") is None, (
        "antibody assessment should not carry pocket evidence"
    )


_check(
    "antibody -> not_yet_applicable, no forced pocket score",
    test_antibody_modality_not_applicable,
)


def test_biologic_modality_not_applicable():
    """A biologic modality produces 'not_yet_applicable'."""
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=None,
        modality="biologic",
    )
    assert assessment["evidence_status"] == "not_yet_applicable"

    errors = validate_assessment(assessment)
    assert errors == [], f"assessment validation errors: {errors}"


_check("biologic -> not_yet_applicable", test_biologic_modality_not_applicable)


def test_small_molecule_is_applicable():
    """A small molecule modality IS applicable for pocket screening."""
    applicable, _ = check_modality_applicability("small_molecule")
    assert applicable is True


_check("small_molecule -> applicable", test_small_molecule_is_applicable)


def test_molecular_glue_is_applicable():
    """A molecular glue modality IS applicable for pocket screening."""
    applicable, _ = check_modality_applicability("molecular_glue")
    assert applicable is True


_check("molecular_glue -> applicable", test_molecular_glue_is_applicable)


def test_gene_therapy_not_applicable():
    """Gene therapy is NOT applicable for pocket screening."""
    applicable, _ = check_modality_applicability("gene_therapy")
    assert applicable is False


_check("gene_therapy -> not applicable", test_gene_therapy_not_applicable)


def test_modality_check_via_screen_structures():
    """screen_structures produces a not_yet_applicable record for
    inapplicable modalities without attempting pocket analysis."""
    candidates = [
        StructureCandidate(
            source="alphafold_db",
            identifier="P04637",
            is_experimental=False,
        )
    ]
    budget = ScreenBudget()

    # A pocket_runner that would fail if called
    def fail_runner(c):
        raise RuntimeError("should not be called for antibody")

    assessments = screen_structures(
        candidates=candidates,
        concept_ref="IC-001",
        modality="antibody",
        budget=budget,
        pocket_runner=fail_runner,
    )
    assert len(assessments) == 1
    assert assessments[0]["evidence_status"] == "not_yet_applicable"


_check(
    "screen_structures: antibody -> not_yet_applicable, no pocket run",
    test_modality_check_via_screen_structures,
)


# ---------------------------------------------------------------------------
# 5. Budget respect
# ---------------------------------------------------------------------------
print("\n--- Budget respect ---")


def test_budget_max_structures():
    """Screening stops when the structure count limit is reached."""
    budget = ScreenBudget(max_structures=2, max_wall_clock_seconds=9999)
    candidates = [
        StructureCandidate(
            source="alphafold_db",
            identifier=f"P0000{i}",
            is_experimental=False,
        )
        for i in range(5)
    ]

    call_count = 0

    def counting_runner(c):
        nonlocal call_count
        call_count += 1
        return _low_score_pocket_result()

    assessments = screen_structures(
        candidates=candidates,
        concept_ref="IC-001",
        modality="small_molecule",
        budget=budget,
        pocket_runner=counting_runner,
    )

    # Should have evaluated exactly 2 structures, then stopped
    assert call_count == 2, f"expected 2 calls, got {call_count}"
    # The 3rd record should note budget exhaustion
    budget_records = [
        a for a in assessments if "budget" in (a.get("rationale") or "").lower()
    ]
    assert len(budget_records) > 0, "no record noting budget exhaustion"


_check("budget: max_structures=2 -> stops after 2", test_budget_max_structures)


def test_budget_wall_clock():
    """Screening stops when wall-clock budget is exhausted."""
    # Use a very small budget that will expire during iteration
    budget = ScreenBudget(max_structures=100, max_wall_clock_seconds=0.001)
    candidates = [
        StructureCandidate(
            source="alphafold_db",
            identifier=f"P0000{i}",
            is_experimental=False,
        )
        for i in range(10)
    ]

    def slow_runner(c):
        time.sleep(0.01)  # 10ms per structure
        return _low_score_pocket_result()

    assessments = screen_structures(
        candidates=candidates,
        concept_ref="IC-001",
        modality="small_molecule",
        budget=budget,
        pocket_runner=slow_runner,
    )

    # Should not have evaluated all 10
    total_evaluated = sum(
        1 for a in assessments if a.get("execution_outcome") == "completed"
    )
    assert total_evaluated < 10, (
        f"expected fewer than 10 evaluated, got {total_evaluated}"
    )


_check("budget: wall-clock exhausted -> stops early", test_budget_wall_clock)


# ---------------------------------------------------------------------------
# 6. Structure retrieval vs. new prediction distinction
# ---------------------------------------------------------------------------
print("\n--- Retrieval vs. new prediction ---")


def test_retrieval_in_scope():
    """Retrieval sources are within scope."""
    budget = ScreenBudget()
    for source in ("alphafold_db", "pdb", "existing_model"):
        in_scope, _ = classify_structure_source(source, budget)
        assert in_scope is True, f"{source} should be in scope"


_check("retrieval sources -> in scope", test_retrieval_in_scope)


def test_new_prediction_out_of_scope():
    """New prediction sources are out of scope for bounded screen."""
    budget = ScreenBudget(allow_new_predictions=False)
    for source in ("af3_prediction", "af3_new", "new_prediction"):
        in_scope, reason = classify_structure_source(source, budget)
        assert in_scope is False, f"{source} should be out of scope"
        assert "separate" in reason.lower() or "justification" in reason.lower(), (
            f"{source}: reason should mention separate justification"
        )


_check("new prediction sources -> out of scope", test_new_prediction_out_of_scope)


def test_no_structure_produces_not_assessed():
    """When no suitable structure exists, the assessment is not_assessed
    with data_unavailable, not a fabricated retrieval."""
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=None,
        modality="small_molecule",
        execution_outcome="data_unavailable",
        data_unavailable_reason=(
            "no AlphaFold DB entry or PDB structure available; "
            "a new AF3 prediction would require separate justification"
        ),
    )
    assert assessment["evidence_status"] == "not_assessed"
    assert assessment["execution_outcome"] == "data_unavailable"

    errors = validate_assessment(assessment)
    assert errors == [], f"assessment validation errors: {errors}"


_check(
    "no structure -> not_assessed / data_unavailable",
    test_no_structure_produces_not_assessed,
)


def test_new_prediction_blocked_in_screen():
    """Attempting to screen with a new prediction source is blocked."""
    candidates = [
        StructureCandidate(
            source="af3_prediction",
            identifier="P04637",
            is_experimental=False,
        )
    ]
    budget = ScreenBudget(allow_new_predictions=False)

    assessments = screen_structures(
        candidates=candidates,
        concept_ref="IC-001",
        modality="small_molecule",
        budget=budget,
    )
    assert len(assessments) == 1
    assert assessments[0]["execution_outcome"] == "blocked"
    assert (
        "separate" in assessments[0].get("rationale", "").lower()
        or "justification" in assessments[0].get("rationale", "").lower()
        or "prediction" in assessments[0].get("rationale", "").lower()
    )


_check(
    "new prediction in screen -> blocked with justification note",
    test_new_prediction_blocked_in_screen,
)


# ---------------------------------------------------------------------------
# 7. Relay preservation
# ---------------------------------------------------------------------------
print("\n--- Relay preservation ---")


def test_single_conformation_relay_preserved():
    """fpocket.single_conformation relay is preserved through screening."""
    result = _low_score_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    relay_codes = [r["code"] for r in assessment.get("relay_codes", [])]
    assert "fpocket.single_conformation" in relay_codes

    # The message is also preserved
    relay_messages = {
        r["code"]: r["message"] for r in assessment.get("relay_codes", [])
    }
    assert "CDK2" in relay_messages.get("fpocket.single_conformation", "")


_check(
    "fpocket.single_conformation relay preserved with CDK2 message",
    test_single_conformation_relay_preserved,
)


def test_conformation_dependent_relay_preserved():
    """fpocket.conformation_dependent relay is preserved through screening."""
    result = _favorable_model_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    relay_codes = [r["code"] for r in assessment.get("relay_codes", [])]
    assert "fpocket.conformation_dependent" in relay_codes


_check(
    "fpocket.conformation_dependent relay preserved",
    test_conformation_dependent_relay_preserved,
)


def test_druggability_not_affinity_relay_preserved():
    """fpocket.druggability_is_not_affinity relay is preserved through
    screening on a druggable pocket."""
    result = _druggable_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    relay_codes = [r["code"] for r in assessment.get("relay_codes", [])]
    assert "fpocket.druggability_is_not_affinity" in relay_codes


_check(
    "fpocket.druggability_is_not_affinity relay preserved",
    test_druggability_not_affinity_relay_preserved,
)


def test_no_relays_dropped_in_screening():
    """All relay codes from the pocket result survive into the assessment."""
    result = PocketResult(
        verdict="borderline",
        drug_score=0.35,
        best_pocket_rank=1,
        structure_name="TARGET.cif",
        is_experimental=False,
        relays=[
            {"code": "fpocket.single_conformation", "message": "msg1"},
            {"code": "fpocket.conformation_dependent", "message": "msg2"},
        ],
        analysis_path="raw/structures/TARGET.pocket.analysis.json",
        threshold_set="pocket@1.0",
    )
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    input_codes = {r["code"] for r in result.relays}
    output_codes = {r["code"] for r in assessment.get("relay_codes", [])}
    assert input_codes == output_codes, (
        f"relay codes changed: input={input_codes}, output={output_codes}"
    )


_check("all relay codes preserved without change", test_no_relays_dropped_in_screening)


# ---------------------------------------------------------------------------
# 8. Evidence status mapping
# ---------------------------------------------------------------------------
print("\n--- Evidence status mapping ---")


def test_druggable_pocket_supported():
    """A druggable pocket on experimental structure -> supported."""
    result = _druggable_pocket_result()
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
        # No intended_site_residues -> global query is sufficient
    )
    assert assessment["evidence_status"] == "supported"


_check(
    "druggable pocket (experimental, global) -> supported",
    test_druggable_pocket_supported,
)


def test_site_druggable_supported():
    """A site-druggable verdict -> supported."""
    result = PocketResult(
        verdict="site-druggable",
        drug_score=0.85,
        best_pocket_rank=3,
        structure_name="TARGET-1ABC.pdb",
        is_experimental=True,
        relays=[
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": "0.85 is cavity shape.",
            },
        ],
        site_query="A:100,A:101",
        site_relevant=True,
    )
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
        intended_site_residues=["A:100", "A:101"],
    )
    assert assessment["evidence_status"] == "supported"


_check("site-druggable -> supported", test_site_druggable_supported)


def test_no_pockets_detected_insufficient():
    """No pockets detected -> insufficient."""
    result = PocketResult(
        verdict="no-pockets-detected",
        drug_score=None,
        best_pocket_rank=None,
        structure_name="TARGET.cif",
        is_experimental=True,
        relays=[],
    )
    assessment = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result,
        modality="small_molecule",
    )
    assert assessment["evidence_status"] == "insufficient"


_check("no-pockets-detected -> insufficient", test_no_pockets_detected_insufficient)


# ---------------------------------------------------------------------------
# 9. Assessment record schema compliance
# ---------------------------------------------------------------------------
print("\n--- Assessment record schema compliance ---")


def test_all_scenario_records_pass_validation():
    """Every assessment record produced by the four scenarios passes
    the evidence schema validator."""
    scenarios = [
        # Scenario 1: low score
        build_assessment_record(
            assessment_id="AR-001",
            concept_ref="IC-001-r1",
            pocket_result=_low_score_pocket_result(),
            modality="small_molecule",
        ),
        # Scenario 2: favorable model score
        build_assessment_record(
            assessment_id="AR-002",
            concept_ref="IC-001-r1",
            pocket_result=_favorable_model_pocket_result(),
            modality="small_molecule",
        ),
        # Scenario 3: irrelevant site
        build_assessment_record(
            assessment_id="AR-003",
            concept_ref="IC-001-r1",
            pocket_result=_irrelevant_site_pocket_result(),
            modality="small_molecule",
            intended_site_residues=["A:145", "A:146", "B:12"],
        ),
        # Scenario 4: inapplicable modality
        build_assessment_record(
            assessment_id="AR-004",
            concept_ref="IC-001-r1",
            pocket_result=None,
            modality="antibody",
        ),
        # No structure available
        build_assessment_record(
            assessment_id="AR-005",
            concept_ref="IC-001-r1",
            pocket_result=None,
            modality="small_molecule",
            execution_outcome="data_unavailable",
        ),
    ]
    for i, record in enumerate(scenarios, 1):
        errors = validate_assessment(record)
        assert errors == [], (
            f"scenario {i} ({record.get('id')}) validation errors: {errors}"
        )


_check(
    "all scenario records pass evidence schema validation",
    test_all_scenario_records_pass_validation,
)


# ---------------------------------------------------------------------------
# 10. No cross-target ranking
# ---------------------------------------------------------------------------
print("\n--- No cross-target ranking ---")


def test_no_cross_target_ranking_structure():
    """The screening output does not include any field that would
    enable ranking across different targets by raw pocket score."""
    # Screen two different targets' results
    result_a = PocketResult(
        verdict="druggable-pocket-present",
        drug_score=0.7,
        best_pocket_rank=1,
        structure_name="TARGET_A.cif",
        is_experimental=True,
        relays=[
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": "0.7 is cavity shape.",
            },
        ],
    )
    result_b = PocketResult(
        verdict="druggable-pocket-present",
        drug_score=0.9,
        best_pocket_rank=1,
        structure_name="TARGET_B.cif",
        is_experimental=True,
        relays=[
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": "0.9 is cavity shape.",
            },
        ],
    )

    assessment_a = build_assessment_record(
        assessment_id="AR-001",
        concept_ref="IC-001-r1",
        pocket_result=result_a,
        modality="small_molecule",
    )
    assessment_b = build_assessment_record(
        assessment_id="AR-002",
        concept_ref="IC-002-r1",
        pocket_result=result_b,
        modality="small_molecule",
    )

    # Both are "supported" — one is not ranked above the other
    assert (
        assessment_a["evidence_status"]
        == assessment_b["evidence_status"]
        == "supported"
    )

    # No "rank" or "priority" field in the assessment
    for assessment in (assessment_a, assessment_b):
        assert "rank" not in assessment
        assert "priority" not in assessment
        assert "comparative_score" not in assessment


_check(
    "no cross-target ranking field in assessment records",
    test_no_cross_target_ranking_structure,
)


# ---------------------------------------------------------------------------
# 11. ScreenBudget validation
# ---------------------------------------------------------------------------
print("\n--- ScreenBudget validation ---")


def test_budget_validation():
    """Budget validates its constraints."""
    try:
        ScreenBudget(max_structures=0)
        raise AssertionError("expected ValueError for max_structures=0")
    except ValueError:
        pass

    try:
        ScreenBudget(max_wall_clock_seconds=-1)
        raise AssertionError("expected ValueError for negative wall clock")
    except ValueError:
        pass

    # Valid budget should work
    budget = ScreenBudget(max_structures=3, max_wall_clock_seconds=60)
    assert budget.max_structures == 3
    assert budget.max_wall_clock_seconds == 60.0
    assert budget.allow_new_predictions is False


_check("ScreenBudget validates constraints", test_budget_validation)


# ---------------------------------------------------------------------------
# 12. StructureCandidate source classification
# ---------------------------------------------------------------------------
print("\n--- StructureCandidate source classification ---")


def test_candidate_is_retrieval():
    """Retrieval candidates are classified correctly."""
    for source in ("alphafold_db", "pdb", "existing_model"):
        c = StructureCandidate(source=source, identifier="X", is_experimental=False)
        assert c.is_retrieval is True, f"{source} should be retrieval"
        assert c.is_new_prediction is False


_check("retrieval candidates classified correctly", test_candidate_is_retrieval)


def test_candidate_is_prediction():
    """New prediction candidates are classified correctly."""
    for source in ("af3_prediction", "af3_new", "new_prediction"):
        c = StructureCandidate(source=source, identifier="X", is_experimental=False)
        assert c.is_retrieval is False
        assert c.is_new_prediction is True, f"{source} should be prediction"


_check("prediction candidates classified correctly", test_candidate_is_prediction)


# ---------------------------------------------------------------------------
# 13. check_site_relevance function
# ---------------------------------------------------------------------------
print("\n--- Site relevance checking ---")


def test_site_relevance_no_site_specified():
    """When no intended site is specified, relevance is unknown."""
    result = _druggable_pocket_result()
    relevant, reason = check_site_relevance(result, None)
    assert relevant is False
    assert (
        "global" in reason.lower()
        or "not specified" in reason.lower()
        or "no --near" in reason.lower()
    )


_check("no site specified -> relevance unknown", test_site_relevance_no_site_specified)


def test_site_relevance_druggable_at_site():
    """A site-druggable result is relevant."""
    result = PocketResult(
        verdict="site-druggable",
        drug_score=0.85,
        best_pocket_rank=3,
        structure_name="TARGET.pdb",
        is_experimental=True,
        relays=[],
        site_query="A:100",
        site_relevant=True,
    )
    relevant, _reason = check_site_relevance(result, ["A:100"])
    assert relevant is True


_check("site-druggable -> relevant", test_site_relevance_druggable_at_site)


def test_site_relevance_no_pocket_at_site():
    """No pocket at site -> not relevant."""
    result = PocketResult(
        verdict="no-pocket-at-site-in-this-conformation",
        drug_score=None,
        best_pocket_rank=None,
        structure_name="TARGET.pdb",
        is_experimental=True,
        relays=[],
        site_query="A:100",
        site_relevant=False,
    )
    relevant, _reason = check_site_relevance(result, ["A:100"])
    assert relevant is False


_check("no pocket at site -> not relevant", test_site_relevance_no_pocket_at_site)


# ---------------------------------------------------------------------------
# 14. Full screen_structures workflow
# ---------------------------------------------------------------------------
print("\n--- Full screen_structures workflow ---")


def test_screen_structures_mixed_candidates():
    """screen_structures handles a mix of valid and invalid candidates."""
    candidates = [
        StructureCandidate(
            source="alphafold_db",
            identifier="P04637",
            is_experimental=False,
        ),
        StructureCandidate(
            source="af3_prediction",
            identifier="P04637",
            is_experimental=False,
        ),
        StructureCandidate(
            source="pdb",
            identifier="1HCK",
            is_experimental=True,
        ),
    ]
    budget = ScreenBudget(max_structures=5)

    call_identifiers = []

    def mock_runner(c):
        call_identifiers.append(c.identifier)
        if c.identifier == "P04637":
            return _favorable_model_pocket_result()
        return _druggable_pocket_result()

    assessments = screen_structures(
        candidates=candidates,
        concept_ref="IC-001",
        modality="small_molecule",
        budget=budget,
        pocket_runner=mock_runner,
    )

    # Should have 3 assessments
    assert len(assessments) == 3, f"expected 3, got {len(assessments)}"

    # First: AF DB retrieval (completed)
    assert assessments[0]["execution_outcome"] == "completed"

    # Second: AF3 prediction (blocked)
    assert assessments[1]["execution_outcome"] == "blocked"

    # Third: PDB retrieval (completed)
    assert assessments[2]["execution_outcome"] == "completed"

    # The AF3 prediction should NOT have called the runner
    assert (
        "af3_prediction"
        not in [c.source for c in candidates if c.identifier in call_identifiers]
        or len(call_identifiers) == 2
    ), f"AF3 prediction should not have called the runner; called: {call_identifiers}"


_check(
    "screen_structures: mixed candidates handled correctly",
    test_screen_structures_mixed_candidates,
)


def test_screen_structures_empty_candidates():
    """screen_structures with no candidates produces a single
    data_unavailable record."""
    assessments = screen_structures(
        candidates=[],
        concept_ref="IC-001",
        modality="small_molecule",
        budget=ScreenBudget(),
    )
    assert len(assessments) == 1
    assert assessments[0]["evidence_status"] == "not_assessed"
    assert assessments[0]["execution_outcome"] == "data_unavailable"


_check(
    "screen_structures: empty candidates -> data_unavailable",
    test_screen_structures_empty_candidates,
)


def test_screen_structures_tool_failure():
    """screen_structures handles tool failures gracefully."""
    candidates = [
        StructureCandidate(
            source="pdb",
            identifier="1ABC",
            is_experimental=True,
        )
    ]
    budget = ScreenBudget()

    def failing_runner(c):
        raise RuntimeError("fpocket not on PATH")

    assessments = screen_structures(
        candidates=candidates,
        concept_ref="IC-001",
        modality="small_molecule",
        budget=budget,
        pocket_runner=failing_runner,
    )
    assert len(assessments) == 1
    assert assessments[0]["execution_outcome"] == "tool_failed"
    assert assessments[0]["evidence_status"] == "not_assessed"


_check(
    "screen_structures: tool failure -> tool_failed record",
    test_screen_structures_tool_failure,
)


# ---------------------------------------------------------------------------
# 15. _parse_pocket_analysis — real pocket output parsing
# ---------------------------------------------------------------------------
print("\n--- Pocket analysis output parsing ---")

from dde.commands.structure_screening import _parse_pocket_analysis


def test_parse_pocket_analysis_druggable():
    """_parse_pocket_analysis correctly parses a druggable analysis record."""
    import tempfile

    analysis_data = {
        "source": "raw/structures/CDK2-1HCK.pockets.json",
        "threshold_set": "pocket@1.0",
        "thresholds_applied": {"druggable_dscore": 0.5, "borderline_dscore": 0.2},
        "metrics": {
            "n_pockets": 5,
            "best_pocket": {
                "rank": 1,
                "druggability_score": 0.939,
                "score": 0.43,
                "volume": 1234.5,
                "n_alpha_spheres": 42,
            },
            "site": None,
        },
        "assessment": {
            "verdict": "druggable-pocket-present",
            "statement": "Best pocket scores 0.939 (druggable) across 5 detected pocket(s).",
            "advisories": [],
        },
        "mandatory_relays": [
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": "0.939 is cavity shape in CDK2-1HCK.pdb; not an affinity.",
            }
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(analysis_data, f)
        f.flush()
        analysis_path = Path(f.name)

    try:
        candidate = StructureCandidate(
            source="pdb",
            identifier="CDK2-1HCK.pdb",
            is_experimental=True,
        )
        result = _parse_pocket_analysis(analysis_path, candidate)

        assert result.verdict == "druggable-pocket-present"
        assert result.drug_score == 0.939
        assert result.best_pocket_rank == 1
        assert result.is_experimental is True
        assert result.threshold_set == "pocket@1.0"
        assert len(result.relays) == 1
        assert result.relays[0]["code"] == "fpocket.druggability_is_not_affinity"
    finally:
        analysis_path.unlink(missing_ok=True)


_check(
    "_parse_pocket_analysis: druggable analysis parsed correctly",
    test_parse_pocket_analysis_druggable,
)


def test_parse_pocket_analysis_site_query():
    """_parse_pocket_analysis handles --near site-specific results."""
    import tempfile

    analysis_data = {
        "source": "raw/structures/TARGET.pockets.json",
        "threshold_set": "pocket@1.0",
        "thresholds_applied": {"druggable_dscore": 0.5, "borderline_dscore": 0.2},
        "metrics": {
            "n_pockets": 3,
            "best_pocket": {
                "rank": 1,
                "druggability_score": 0.85,
            },
            "site": {
                "requested": ["A:100", "A:101"],
                "pockets_at_site": [
                    {
                        "rank": 2,
                        "druggability_score": 0.72,
                        "matched_residues": ["A:100"],
                    }
                ],
            },
        },
        "assessment": {
            "verdict": "site-druggable",
            "statement": "Pocket 2 lines the requested site and scores 0.720 (druggable)",
        },
        "mandatory_relays": [
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": "0.720 is cavity shape.",
            }
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(analysis_data, f)
        f.flush()
        analysis_path = Path(f.name)

    try:
        candidate = StructureCandidate(
            source="pdb",
            identifier="TARGET.pdb",
            is_experimental=True,
        )
        result = _parse_pocket_analysis(analysis_path, candidate)

        assert result.verdict == "site-druggable"
        # Should use the site pocket's score, not the global best
        assert result.drug_score == 0.72
        assert result.best_pocket_rank == 2
        assert result.site_relevant is True
        assert result.site_query == "A:100,A:101"
    finally:
        analysis_path.unlink(missing_ok=True)


_check(
    "_parse_pocket_analysis: site-specific results parsed correctly",
    test_parse_pocket_analysis_site_query,
)


def test_parse_pocket_analysis_no_site_hit():
    """_parse_pocket_analysis handles --near with no pocket at site."""
    import tempfile

    analysis_data = {
        "source": "raw/structures/TARGET.pockets.json",
        "threshold_set": "pocket@1.0",
        "thresholds_applied": {"druggable_dscore": 0.5, "borderline_dscore": 0.2},
        "metrics": {
            "n_pockets": 2,
            "best_pocket": {
                "rank": 1,
                "druggability_score": 0.6,
            },
            "site": {
                "requested": ["B:200", "B:201"],
                "pockets_at_site": [],
            },
        },
        "assessment": {
            "verdict": "no-pocket-at-site-in-this-conformation",
            "statement": "No pocket lines the requested residues.",
        },
        "mandatory_relays": [
            {
                "code": "fpocket.single_conformation",
                "message": "no pocket at the requested site.",
            }
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(analysis_data, f)
        f.flush()
        analysis_path = Path(f.name)

    try:
        candidate = StructureCandidate(
            source="pdb",
            identifier="TARGET.pdb",
            is_experimental=True,
        )
        result = _parse_pocket_analysis(analysis_path, candidate)

        assert result.verdict == "no-pocket-at-site-in-this-conformation"
        assert result.site_relevant is False
        assert result.site_query == "B:200,B:201"
    finally:
        analysis_path.unlink(missing_ok=True)


_check(
    "_parse_pocket_analysis: no-pocket-at-site parsed correctly",
    test_parse_pocket_analysis_no_site_hit,
)


# ---------------------------------------------------------------------------
# 16. Integration: _parse_pocket_analysis → build_assessment_record
# ---------------------------------------------------------------------------
print("\n--- Integration: parsed analysis → assessment record ---")


def test_parsed_analysis_to_assessment_record():
    """End-to-end: parse a realistic pocket analysis output, then feed it
    through build_assessment_record and verify the resulting evidence
    assessment is correct and schema-valid.

    This tests the real data path: pocket analysis JSON → PocketResult →
    assessment record. No hand-crafted PocketResult fixtures — the
    PocketResult is built by _parse_pocket_analysis from a realistic
    analysis file matching what ``dde pocket analyze`` actually writes."""
    import tempfile

    # Realistic analysis output matching dde pocket analyze's format
    analysis_data = {
        "source": "raw/structures/CDK2-2W1D.pockets.json",
        "threshold_set": "pocket@1.0",
        "thresholds_applied": {"druggable_dscore": 0.5, "borderline_dscore": 0.2},
        "threshold_sources": ["built-in"],
        "threshold_provenance": "loaded from built-in defaults",
        "metrics": {
            "n_pockets": 4,
            "best_pocket": {
                "rank": 1,
                "druggability_score": 0.293,
                "score": 0.22,
                "volume": 876.3,
                "n_alpha_spheres": 38,
                "centre_of_mass_max_sphere_distance": 12.5,
            },
            "site": None,
            "volume_estimate_tolerance": 0.03,
        },
        "assessment": {
            "verdict": "no-druggable-pocket-in-this-conformation",
            "statement": "Best pocket scores 0.293 (not-druggable) across 4 detected pocket(s).",
            "advisories": [
                "Volumes are Monte Carlo estimates; differences under 3% between runs are noise."
            ],
            "relayed_run_warnings": [],
        },
        "mandatory_relays": [
            {
                "code": "fpocket.single_conformation",
                "message": (
                    "0.293 in CDK2-2W1D.cif; the same CDK2 ATP site "
                    "scores 0.17, 0.29 and 0.94 in three crystals."
                ),
            }
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(analysis_data, f)
        f.flush()
        analysis_path = Path(f.name)

    try:
        candidate = StructureCandidate(
            source="pdb",
            identifier="CDK2-2W1D.cif",
            is_experimental=True,
        )

        # Step 1: Parse the analysis file (real parsing, not a mock)
        pocket_result = _parse_pocket_analysis(analysis_path, candidate)

        # Verify parsed result
        assert pocket_result.verdict == "no-druggable-pocket-in-this-conformation"
        assert pocket_result.drug_score == 0.293
        assert pocket_result.threshold_set == "pocket@1.0"
        assert len(pocket_result.relays) == 1
        assert pocket_result.relays[0]["code"] == "fpocket.single_conformation"

        # Step 2: Build assessment record (real assessment, not a mock)
        assessment = build_assessment_record(
            assessment_id="AR-001",
            concept_ref="IC-CDK2-r1",
            pocket_result=pocket_result,
            modality="small_molecule",
        )

        # Verify the assessment
        assert assessment["evidence_status"] == "insufficient", (
            "CDK2 sub-cutoff score should be insufficient, not contradicted"
        )
        assert assessment["execution_outcome"] == "completed"

        # Relay preserved
        relay_codes = [r["code"] for r in assessment.get("relay_codes", [])]
        assert "fpocket.single_conformation" in relay_codes

        # CDK2 calibration in rationale
        assert "CDK2" in assessment["rationale"] or "0.94" in assessment["rationale"]

        # Schema valid
        errors = validate_assessment(assessment)
        assert errors == [], f"assessment validation errors: {errors}"
    finally:
        analysis_path.unlink(missing_ok=True)


_check(
    "integration: parsed analysis → assessment record end-to-end",
    test_parsed_analysis_to_assessment_record,
)


# ---------------------------------------------------------------------------
# 17. Integration: make_pocket_runner + CliRunner
# ---------------------------------------------------------------------------
print("\n--- Integration: real pocket_runner via CliRunner ---")


def test_make_pocket_runner_requires_click():
    """make_pocket_runner requires click to be installed.
    Tests the import path and validates the function signature."""
    from dde.commands.structure_screening import make_pocket_runner

    # The function itself is importable regardless of click.
    # Calling it requires click.testing.CliRunner.
    try:
        from click.testing import CliRunner  # noqa: F401 — availability check

        # Click IS available — test that make_pocket_runner returns a callable
        runner = make_pocket_runner(project_dir="/tmp/nonexistent")
        assert callable(runner), "make_pocket_runner should return a callable"
        print("    (click is available, make_pocket_runner returns a callable)")
    except ImportError:
        # Click is not installed — the function exists but calling it
        # should raise ImportError
        try:
            make_pocket_runner()
            raise AssertionError(
                "make_pocket_runner should raise ImportError without click"
            )
        except ImportError:
            pass
        print(
            "    (click not installed — skipped CliRunner test, import path validated)"
        )


_check(
    "make_pocket_runner: import path and callable validation",
    test_make_pocket_runner_requires_click,
)


def test_cli_command_registration():
    """When click is installed, structure_screen is a Click group with a
    run subcommand. When click is not installed, the import still works
    for library use but the CLI command is not defined."""
    try:
        import click
        from dde.commands.structure_screening import structure_screen

        assert isinstance(structure_screen, click.Group), (
            f"structure_screen should be a click.Group, got {type(structure_screen)}"
        )
        assert "run" in structure_screen.commands, (
            "structure_screen should have a 'run' subcommand"
        )
        print("    (click available — CLI command verified)")
    except ImportError:
        # Without click, structure_screen is not defined — verify the
        # import of library functions still works
        from dde.commands.structure_screening import screen_structures

        assert callable(screen_structures)
        print("    (click not installed — library import verified)")


_check(
    "CLI command registration: structure-screen group with run subcommand",
    test_cli_command_registration,
)


def test_screen_structures_with_parsed_analysis():
    """Full integration: screen_structures with a pocket_runner that returns
    results from _parse_pocket_analysis (simulating the real data path without
    requiring fpocket on PATH).

    This bridges the gap between the mock-runner tests and a fully live run:
    the pocket_runner returns PocketResults built by the real parser, not by
    hand-crafted constructors."""
    import tempfile

    # Write a realistic analysis JSON to disk
    analysis_data = {
        "source": "raw/structures/TEST.pockets.json",
        "threshold_set": "pocket@1.0",
        "thresholds_applied": {"druggable_dscore": 0.5, "borderline_dscore": 0.2},
        "metrics": {
            "n_pockets": 2,
            "best_pocket": {
                "rank": 1,
                "druggability_score": 0.65,
                "score": 0.3,
                "volume": 950.0,
                "n_alpha_spheres": 35,
            },
            "site": None,
            "volume_estimate_tolerance": 0.03,
        },
        "assessment": {
            "verdict": "druggable-pocket-present",
            "statement": "Best pocket scores 0.650 (druggable) across 2 detected pocket(s).",
            "advisories": [],
        },
        "mandatory_relays": [
            {
                "code": "fpocket.druggability_is_not_affinity",
                "message": "0.650 is cavity shape in TEST.pdb; not an affinity.",
            }
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(analysis_data, f)
        f.flush()
        analysis_path = Path(f.name)

    try:
        # A pocket_runner that uses the real parser
        def parsed_runner(candidate):
            return _parse_pocket_analysis(analysis_path, candidate)

        candidates = [
            StructureCandidate(
                source="pdb",
                identifier="TEST.pdb",
                is_experimental=True,
            ),
        ]
        budget = ScreenBudget(max_structures=5)

        assessments = screen_structures(
            candidates=candidates,
            concept_ref="IC-TEST",
            modality="small_molecule",
            budget=budget,
            pocket_runner=parsed_runner,
        )

        assert len(assessments) == 1
        a = assessments[0]
        assert a["evidence_status"] == "supported"
        assert a["execution_outcome"] == "completed"
        relay_codes = [r["code"] for r in a.get("relay_codes", [])]
        assert "fpocket.druggability_is_not_affinity" in relay_codes

        errors = validate_assessment(a)
        assert errors == [], f"assessment validation errors: {errors}"
    finally:
        analysis_path.unlink(missing_ok=True)


_check(
    "screen_structures with _parse_pocket_analysis pocket_runner (integration)",
    test_screen_structures_with_parsed_analysis,
)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
