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

"""Tests for competitive differentiation assessment (issue #37).

Covers the acceptance criteria from the refinement comment:
  1. Three-dimension separation (competitor activity, patentability, FTO)
  2. A crowded-but-differentiated concept (density alone is not rejection)
  3. A patent-search miss with incomplete coverage (gap is stated)
  4. A documented program-specific constraint (charter-level exclusion)
  5. FTO disclaimer is mandatory and always present
  6. Dimensions are never blended into one score
  7. Assessment records follow dde.evidence-assessment.v1

Run with:
    PYTHONPATH=tools python3 tests/test_differentiation.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.differentiation import (
    EVIDENCE_TYPE_COMPETITOR,
    EVIDENCE_TYPE_PATENT,
    assess_competitive_differentiation,
    build_assessment_records,
)
from dde.core.evidence import validate_assessment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def _crowded_patents() -> list[dict[str, Any]]:
    """A crowded field with many recent patents from multiple assignees."""
    patents = []
    assignees = [
        "Pfizer Inc",
        "Novartis AG",
        "Eli Lilly",
        "AstraZeneca",
        "Merck KGaA",
        "Roche AG",
        "BMS",
        "Sanofi SA",
        "GSK plc",
        "AbbVie Inc",
        "Gilead Sciences",
        "Amgen Inc",
    ]
    for i in range(30):
        patents.append(
            {
                "publication_number": f"US20240{i:05d}A1",
                "title": f"CDK4 inhibitor compound {i}",
                "snippet": "Novel small molecule CDK4 inhibitor for solid tumors",
                "assignee": assignees[i % len(assignees)],
                "priority_date": "20230601",
                "filing_date": "20240101",
                "publication_date": "20240701",
                "language": "en",
            }
        )
    return patents


def _sparse_patents() -> list[dict[str, Any]]:
    """Sparse field — only a few old patents."""
    return [
        {
            "publication_number": "US20150012345A1",
            "title": "Novel FBXL19 modulator",
            "snippet": "Compositions for modulating FBXL19 activity",
            "assignee": "Academic University",
            "priority_date": "20140301",
            "filing_date": "20150101",
            "publication_date": "20150701",
            "language": "en",
        },
    ]


def _no_patents() -> list[dict[str, Any]]:
    """Empty search result — potential search miss or novel target."""
    return []


def _mixed_jurisdiction_patents() -> list[dict[str, Any]]:
    """Patents from multiple jurisdictions, some gaps."""
    return [
        {
            "publication_number": "US20230012345A1",
            "title": "Target X inhibitor",
            "snippet": "Small molecule inhibitor",
            "assignee": "US Pharma",
            "priority_date": "20230101",
            "filing_date": "20230401",
            "publication_date": "20230701",
            "language": "en",
        },
        {
            "publication_number": "EP3456789B1",
            "title": "Target X modulator",
            "snippet": "Biologic modulator",
            "assignee": "EU Biotech",
            "priority_date": "20220601",
            "filing_date": "20220901",
            "publication_date": "20230301",
            "language": "en",
        },
    ]


# ===========================================================================
# Tests
# ===========================================================================

print("=" * 60)
print("test_differentiation.py — issue #37 competitive differentiation")
print("=" * 60)


# ---------------------------------------------------------------------------
# 1. Three-dimension separation
# ---------------------------------------------------------------------------
print("\n--- Three-dimension separation ---")


def test_three_dimensions_always_present():
    """Assessment always produces exactly three dimensions."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
        modality="small_molecule",
        indication="solid_tumors",
    )
    dims = result["dimensions"]
    assert "competitor_activity" in dims, "missing competitor_activity"
    assert "patentability" in dims, "missing patentability"
    assert "freedom_to_operate" in dims, "missing freedom_to_operate"
    assert len(dims) == 3, f"expected exactly 3 dimensions, got {len(dims)}"


_check("three dimensions always present", test_three_dimensions_always_present)


def test_dimensions_are_independent():
    """Dimensions are explicitly marked as independent, never blended."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    assert result["dimensions_are_independent"] is True
    assert result["blended_score"] is None


_check("dimensions are independent (never blended)", test_dimensions_are_independent)


def test_each_dimension_carries_search_metadata():
    """Each dimension carries search_date, search_scope, coverage_limits."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    for dim_name, dim in result["dimensions"].items():
        assert "search_date" in dim, f"{dim_name}: missing search_date"
        assert "search_scope" in dim, f"{dim_name}: missing search_scope"
        assert "coverage_limits" in dim, f"{dim_name}: missing coverage_limits"


_check(
    "each dimension carries search metadata",
    test_each_dimension_carries_search_metadata,
)


def test_strong_differentiation_and_fto_concern_both_surface():
    """A fixture with strong differentiation AND a real FTO concern —
    confirm both surface, neither is netted out.

    This is the critical test: a concept that has a differentiated angle
    in a crowded field but also has FTO risk must show BOTH dimensions
    independently.
    """
    # Create patents: many recent filings (high FTO risk) but in a
    # different modality (so differentiation exists).
    patents = []
    for i in range(25):
        patents.append(
            {
                "publication_number": f"US20240{i:05d}A1",
                "title": f"CDK4 biologic therapy {i}",
                "snippet": "Biologic antibody targeting CDK4",
                "assignee": f"Company {i % 5}",
                "priority_date": "20230601",
                "filing_date": "20240101",
                "publication_date": "20240701",
                "language": "en",
            }
        )

    result = assess_competitive_differentiation(
        patents,
        "CDK4",
        modality="small_molecule",
        indication="solid_tumors",
    )

    dims = result["dimensions"]

    # High competitor activity
    comp = dims["competitor_activity"]
    assert comp["density"] in ("high_activity", "moderate_activity"), (
        f"expected high/moderate activity, got {comp['density']}"
    )

    # FTO should show risk because of dense filings
    fto = dims["freedom_to_operate"]
    assert fto["risk_level"] in ("moderate_risk", "high_risk"), (
        f"expected moderate/high risk, got {fto['risk_level']}"
    )

    # BUT the patentability dimension should show potential novelty
    # because the existing patents are biologics, not small molecules
    pat = dims["patentability"]
    # The patentability dimension is separate from the FTO dimension
    # and should reflect the modality gap.
    assert pat["novelty_assessment"] != "", "patentability should have an assessment"

    # The key assertion: ALL THREE dimensions are present and independent.
    # A system that blends them would lose the FTO concern behind the
    # strong differentiation, or reject the concept despite the
    # differentiation because of FTO risk.
    assert result["dimensions_are_independent"] is True
    assert result["blended_score"] is None

    # Both the high competitor activity AND the FTO risk are visible.
    assert comp["recent_patents"] > 0
    assert fto["recent_filings"] > 0


_check(
    "strong differentiation AND FTO concern both surface",
    test_strong_differentiation_and_fto_concern_both_surface,
)


# ---------------------------------------------------------------------------
# 2. Crowded but potentially differentiated concept
# ---------------------------------------------------------------------------
print("\n--- Crowded-but-differentiated concept ---")


def test_crowded_field_not_automatic_veto():
    """A crowded field alone does not produce a rejection.

    This tests the hard constraint: 'Existing work in a field is not by
    itself a scientific or commercial veto.'
    """
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
        modality="small_molecule",
        indication="solid_tumors",
    )

    comp = result["dimensions"]["competitor_activity"]
    assert comp["density"] == "high_activity", (
        f"expected high_activity, got {comp['density']}"
    )
    # Explicit check: crowding alone is not a veto.
    assert comp["crowding_is_veto"] is False, (
        "crowding_is_veto must be False — crowding alone is not a veto"
    )
    # The note should say so explicitly.
    note_lower = comp["note"].lower()
    assert "not" in note_lower and (
        "veto" in note_lower or "gate" in note_lower or "rejected" in note_lower
    ), "note must explicitly state crowding is not a veto/gate"


_check("crowded field is not automatic veto", test_crowded_field_not_automatic_veto)


def test_differentiation_despite_crowding():
    """A concept in a crowded field with a different modality has
    differentiation potential — patentability should reflect this."""
    # Crowded field with biologics; our concept is small_molecule.
    patents = []
    for i in range(20):
        patents.append(
            {
                "publication_number": f"US20240{i:05d}A1",
                "title": f"MDA5 antibody therapeutic {i}",
                "snippet": "Biologic antibody targeting MDA5 for autoimmune disease",
                "assignee": f"BioPharma {chr(65 + i % 10)}",
                "priority_date": "20230601",
                "filing_date": "20240101",
                "publication_date": "20240701",
                "language": "en",
            }
        )

    result = assess_competitive_differentiation(
        patents,
        "MDA5",
        modality="small_molecule",
        indication="psoriatic_arthritis",
    )

    comp = result["dimensions"]["competitor_activity"]
    pat = result["dimensions"]["patentability"]

    # Competitor activity is high (many patents exist).
    assert comp["density"] in ("moderate_activity", "high_activity")
    # But patentability may show potential novelty because existing
    # patents are for a different modality.
    assert pat["novelty_assessment"] is not None
    # The three dimensions are independent: high competitor activity
    # does not force patentability or FTO to a negative conclusion.
    assert result["dimensions_are_independent"] is True


_check(
    "differentiation despite crowding (modality gap)",
    test_differentiation_despite_crowding,
)


# ---------------------------------------------------------------------------
# 3. Patent-search miss with incomplete coverage
# ---------------------------------------------------------------------------
print("\n--- Patent-search miss with incomplete coverage ---")


def test_empty_search_records_coverage_gap():
    """An empty search result (possible search miss) must explicitly
    record the coverage gap, not silently treat it as 'no patents'.

    This tests the hard constraint: 'coverage gap is explicitly recorded,
    not silently absent.'
    """
    result = assess_competitive_differentiation(
        _no_patents(),
        "NOVEL_TARGET_XYZ",
        modality="small_molecule",
    )

    # Coverage limits are present at the top level.
    meta = result["search_metadata"]
    assert meta["coverage_limits"], "coverage_limits must not be empty"
    assert (
        "exhaustive" in meta["coverage_limits"].lower()
        or "not" in meta["coverage_limits"].lower()
    ), "coverage limits must state the search is not exhaustive"

    # Each dimension also carries its own coverage limits.
    for dim_name, dim in result["dimensions"].items():
        assert dim["coverage_limits"], f"{dim_name}: coverage_limits must not be empty"

    # FTO dimension should have unresolved questions even with no results.
    fto = result["dimensions"]["freedom_to_operate"]
    assert len(fto["unresolved_questions"]) > 0, (
        "FTO should have unresolved questions even with empty results"
    )
    # Specifically: unpublished applications should be mentioned.
    unresolved_text = " ".join(fto["unresolved_questions"]).lower()
    assert "unpublished" in unresolved_text, (
        "unresolved questions should mention unpublished applications"
    )


_check(
    "empty search result records coverage gap explicitly",
    test_empty_search_records_coverage_gap,
)


def test_incomplete_jurisdiction_coverage_stated():
    """When some major jurisdictions are missing from results, the
    coverage gap is explicitly stated."""
    result = assess_competitive_differentiation(
        _mixed_jurisdiction_patents(),
        "Target X",
    )
    fto = result["dimensions"]["freedom_to_operate"]
    unresolved_text = " ".join(fto["unresolved_questions"]).lower()

    # We have US and EP patents but missing CN, JP, KR, WO.
    # At least some of these should be listed as missing.
    assert any(j.lower() in unresolved_text for j in ("cn", "jp", "kr", "wo")), (
        "unresolved questions should mention missing jurisdictions"
    )


_check(
    "incomplete jurisdiction coverage is stated",
    test_incomplete_jurisdiction_coverage_stated,
)


def test_coverage_disclaimer_present():
    """Every assessment carries a coverage disclaimer."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    assert "coverage_disclaimer" in result
    assert "exhaustive" in result["coverage_disclaimer"].lower()


_check("coverage disclaimer present", test_coverage_disclaimer_present)


# ---------------------------------------------------------------------------
# 4. Program-specific constraint (charter-level exclusion)
# ---------------------------------------------------------------------------
print("\n--- Program-specific constraint (charter exclusion) ---")


def test_charter_constraint_recorded():
    """A charter-level exclusion is recorded as a program constraint,
    distinct from a scientific rejection."""
    constraint = "No oral formulations per charter DEC-003"
    result = assess_competitive_differentiation(
        _sparse_patents(),
        "FBXL19",
        modality="small_molecule",
        indication="psoriatic_arthritis",
        charter_constraints=[constraint],
    )
    assert constraint in result["charter_constraints"]

    # Build assessment records and check the constraint record.
    records = build_assessment_records(result, "IC-042")
    constraint_records = [
        r for r in records if "charter constraint" in r["claim"].lower()
    ]
    assert len(constraint_records) >= 1, (
        "expected at least one charter constraint assessment record"
    )
    cr = constraint_records[0]
    assert (
        "program" in cr["rationale"].lower() or "charter" in cr["rationale"].lower()
    ), "rationale must identify this as a program constraint"
    assert (
        "scientific rejection" in cr["rationale"].lower()
        or "not a scientific" in cr["rationale"].lower()
    ), "rationale must distinguish from scientific rejection"


_check(
    "charter constraint recorded as program constraint",
    test_charter_constraint_recorded,
)


def test_charter_constraint_distinct_from_science():
    """Charter constraint assessment records use the correct
    evidence_type and make the distinction explicit."""
    result = assess_competitive_differentiation(
        _sparse_patents(),
        "FBXL19",
        charter_constraints=["Excluded: biologic modality per DEC-005"],
    )
    records = build_assessment_records(result, "IC-042")
    constraint_records = [
        r for r in records if "charter constraint" in r["claim"].lower()
    ]
    for cr in constraint_records:
        # Must use competitive_precedent evidence type.
        assert cr["evidence"]["evidence_type"] == EVIDENCE_TYPE_COMPETITOR
        # Must say it's a program constraint, not scientific.
        assert "program" in cr["rationale"].lower()


_check(
    "charter constraint distinct from scientific rejection",
    test_charter_constraint_distinct_from_science,
)


# ---------------------------------------------------------------------------
# 5. FTO disclaimer is mandatory
# ---------------------------------------------------------------------------
print("\n--- FTO disclaimer mandatory ---")


def test_fto_disclaimer_on_assessment():
    """Top-level FTO disclaimer is always present."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    assert "fto_disclaimer" in result
    assert (
        "not formal legal clearance" in result["fto_disclaimer"].lower()
        or "not formal legal clearance" in result["fto_disclaimer"]
    ), "FTO disclaimer must state this is not formal legal clearance"


_check("FTO disclaimer on top-level assessment", test_fto_disclaimer_on_assessment)


def test_fto_disclaimer_on_fto_dimension():
    """FTO dimension carries its own disclaimer."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    fto = result["dimensions"]["freedom_to_operate"]
    assert "fto_disclaimer" in fto
    assert (
        "qualified" in fto["fto_disclaimer"].lower()
        or "counsel" in fto["fto_disclaimer"].lower()
    ), "FTO disclaimer must mention qualified review"


_check("FTO disclaimer on FTO dimension", test_fto_disclaimer_on_fto_dimension)


def test_fto_disclaimer_on_assessment_records():
    """Assessment records for FTO carry the disclaimer in rationale."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    records = build_assessment_records(result, "IC-001")
    fto_records = [r for r in records if "freedom to operate" in r["claim"].lower()]
    assert len(fto_records) >= 1, "expected at least one FTO assessment record"
    for r in fto_records:
        assert (
            "not formal legal clearance" in r["rationale"].lower()
            or "NOT formal legal clearance" in r["rationale"]
        ), "FTO assessment record must carry the legal clearance disclaimer"


_check(
    "FTO disclaimer on assessment records", test_fto_disclaimer_on_assessment_records
)


def test_fto_disclaimer_even_when_no_risk():
    """FTO disclaimer is present even when there are no patents."""
    result = assess_competitive_differentiation(
        _no_patents(),
        "NOVEL_TARGET_XYZ",
    )
    fto = result["dimensions"]["freedom_to_operate"]
    assert "fto_disclaimer" in fto
    assert (
        "not formal legal clearance" in fto["fto_disclaimer"].lower()
        or "NOT formal legal clearance" in fto["fto_disclaimer"]
    )


_check(
    "FTO disclaimer present even with no patents", test_fto_disclaimer_even_when_no_risk
)


# ---------------------------------------------------------------------------
# 6. Assessment record validation
# ---------------------------------------------------------------------------
print("\n--- Assessment record validation ---")


def test_assessment_records_validate():
    """All generated assessment records pass evidence.validate_assessment()."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
        modality="small_molecule",
        indication="solid_tumors",
        charter_constraints=["No biologic modality per DEC-001"],
    )
    records = build_assessment_records(result, "IC-001-r2")

    # At least 4 records: competitor, patentability, FTO, charter constraint.
    assert len(records) >= 4, f"expected >= 4 records, got {len(records)}"

    for i, record in enumerate(records):
        # Assign a real ID for validation.
        record["id"] = f"AR-{900 + i:03d}"
        errors = validate_assessment(record)
        assert errors == [], (
            f"record {record['id']} ({record['claim'][:40]}...): "
            f"validation errors: {errors}"
        )


_check(
    "all assessment records pass validate_assessment()",
    test_assessment_records_validate,
)


def test_assessment_records_evidence_types():
    """Assessment records use the correct evidence types."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    records = build_assessment_records(result, "IC-001")

    evidence_types = {r["evidence"]["evidence_type"] for r in records}
    assert EVIDENCE_TYPE_COMPETITOR in evidence_types, (
        f"expected {EVIDENCE_TYPE_COMPETITOR} in evidence types"
    )
    assert EVIDENCE_TYPE_PATENT in evidence_types, (
        f"expected {EVIDENCE_TYPE_PATENT} in evidence types"
    )


_check(
    "assessment records use correct evidence types",
    test_assessment_records_evidence_types,
)


def test_assessment_records_concept_ref_preserved():
    """Assessment records preserve the concept reference."""
    result = assess_competitive_differentiation(
        _sparse_patents(),
        "FBXL19",
    )
    records = build_assessment_records(result, "IC-042-r3")
    for r in records:
        assert r["concept_ref"] == "IC-042-r3"


_check(
    "concept_ref preserved in assessment records",
    test_assessment_records_concept_ref_preserved,
)


# ---------------------------------------------------------------------------
# 7. Modality, indication, and entity scope preservation
# ---------------------------------------------------------------------------
print("\n--- Scope preservation ---")


def test_modality_indication_preserved():
    """The assessment preserves modality, indication, and entity scope."""
    result = assess_competitive_differentiation(
        _sparse_patents(),
        "IFIH1",
        modality="small_molecule",
        indication="psoriatic_arthritis",
        entity="MDA5-inh-001",
    )
    ctx = result["concept_context"]
    assert ctx["modality"] == "small_molecule"
    assert ctx["indication"] == "psoriatic_arthritis"
    assert ctx["entity"] == "MDA5-inh-001"


_check("modality, indication, entity preserved", test_modality_indication_preserved)


# ---------------------------------------------------------------------------
# 8. Search metadata completeness
# ---------------------------------------------------------------------------
print("\n--- Search metadata ---")


def test_search_metadata_present():
    """Top-level search metadata is always present."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    meta = result["search_metadata"]
    assert "search_date" in meta
    assert "search_scope" in meta
    assert "coverage_limits" in meta
    assert "source" in meta


_check("search metadata present", test_search_metadata_present)


def test_custom_search_metadata():
    """Custom search scope and coverage limits are propagated."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
        search_scope="Custom scope: EPO OPS API",
        coverage_limits="Only EP patents searched",
    )
    meta = result["search_metadata"]
    assert meta["search_scope"] == "Custom scope: EPO OPS API"
    assert meta["coverage_limits"] == "Only EP patents searched"

    # Propagated to each dimension.
    for dim_name, dim in result["dimensions"].items():
        assert dim["search_scope"] == "Custom scope: EPO OPS API", (
            f"{dim_name}: search_scope not propagated"
        )
        assert dim["coverage_limits"] == "Only EP patents searched", (
            f"{dim_name}: coverage_limits not propagated"
        )


_check("custom search metadata propagated", test_custom_search_metadata)


# ---------------------------------------------------------------------------
# 9. Edge cases
# ---------------------------------------------------------------------------
print("\n--- Edge cases ---")


def test_empty_patents_produces_valid_result():
    """Empty patent list produces a valid assessment, not an error."""
    result = assess_competitive_differentiation(
        [],
        "NOVEL_GENE",
    )
    dims = result["dimensions"]
    assert dims["competitor_activity"]["density"] == "uncrowded"
    assert dims["patentability"]["novelty_assessment"] == "high_novelty"
    assert dims["freedom_to_operate"]["risk_level"] == "no_recent_filings"


_check(
    "empty patent list produces valid assessment",
    test_empty_patents_produces_valid_result,
)


def test_no_modality_indication_still_works():
    """Assessment works without modality or indication context."""
    result = assess_competitive_differentiation(
        _crowded_patents(),
        "CDK4 inhibitor",
    )
    assert "dimensions" in result
    assert len(result["dimensions"]) == 3


_check(
    "assessment works without modality/indication",
    test_no_modality_indication_still_works,
)


def test_multiple_charter_constraints():
    """Multiple charter constraints produce multiple records."""
    result = assess_competitive_differentiation(
        _sparse_patents(),
        "FBXL19",
        charter_constraints=[
            "No oral formulations per DEC-003",
            "Oncology indications only per DEC-004",
        ],
    )
    records = build_assessment_records(result, "IC-042")
    constraint_records = [
        r for r in records if "charter constraint" in r["claim"].lower()
    ]
    assert len(constraint_records) == 2, (
        f"expected 2 charter constraint records, got {len(constraint_records)}"
    )


_check(
    "multiple charter constraints produce multiple records",
    test_multiple_charter_constraints,
)


# ---------------------------------------------------------------------------
# 10. Relay code registration
# ---------------------------------------------------------------------------
print("\n--- Relay codes ---")


def test_relay_codes_registered():
    """New relay codes are registered in provenance.RELAY_CODES."""
    from dde.core.provenance import RELAY_CODES

    assert "differentiation.crowded_landscape" in RELAY_CODES, (
        "missing relay code: differentiation.crowded_landscape"
    )
    assert "differentiation.not_legal_clearance" in RELAY_CODES, (
        "missing relay code: differentiation.not_legal_clearance"
    )
    # Existing code should still be there.
    assert "patent.fto_risk_identified" in RELAY_CODES


_check("relay codes registered in provenance.RELAY_CODES", test_relay_codes_registered)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
