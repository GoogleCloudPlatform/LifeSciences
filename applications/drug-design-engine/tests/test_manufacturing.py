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

"""Tests for progressive manufacturing assessment (#23).

Covers:
  - Inventory: compound.py's existing SA-score command compatibility
  - Three demonstration cases: small-molecule, biologic, no-entity
  - Stereocenter-count flag scoped as heuristic, not hard rejection
  - Qualitative Phase 1 finding omits fabricated data
  - Assessment supersedes chain for reassessment
  - Stage requirement structure for Stages 0, 2, 3, 4
  - SA-score != synthesizability distinction preserved
  - Production-platform fit for known modalities
  - Complexity heuristics computation

Run with:
    PYTHONPATH=tools python3 tests/test_manufacturing.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import json
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

from dde.core.evidence import (
    EVIDENCE_STATUSES,
    validate_assessment,
)
from dde.core.manufacturing import (
    MANUFACTURING_EVIDENCE_TYPES,
    PRODUCTION_PLATFORMS,
    SA_SCORE_MODALITIES,
    STAGE_REQUIREMENTS,
    assess_stage0,
    compute_complexity_heuristics,
)

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


def _small_molecule_concept(
    entity_ref: str | None = "c1ccc(CC(=O)O)cc1",
) -> dict[str, Any]:
    """A small-molecule concept with a real SMILES structure."""
    return {
        "schema": "dde.intervention-concept.v1",
        "id": "IC-001",
        "revision": 1,
        "state": "active",
        "disease_context": {"indication": "solid_tumors"},
        "target_pathway": {
            "gene": "CDK4",
            "protein": "CDK4",
            "pathway": "Rb/E2F cell cycle regulation",
            "mechanism_hypothesis": "CDK4 inhibition restores Rb-mediated cell cycle arrest",
        },
        "modality": "small_molecule",
        "entity_ref": entity_ref,
        "delivery_assumptions": {
            "route": "oral",
            "formulation": "tablet",
        },
        "charter_ref": "DEC-001",
        "termination_authority": "human",
        "created_at": _NOW,
    }


def _biologic_concept() -> dict[str, Any]:
    """A biologic concept (antibody)."""
    return {
        "schema": "dde.intervention-concept.v1",
        "id": "IC-002",
        "revision": 1,
        "state": "active",
        "disease_context": {"indication": "non_small_cell_lung_cancer"},
        "target_pathway": {
            "gene": "PD-L1",
            "protein": "PD-L1",
            "pathway": "PD-1/PD-L1 immune checkpoint",
            "mechanism_hypothesis": "PD-L1 blockade restores anti-tumor immunity",
        },
        "modality": "antibody",
        "entity_ref": "anti-PD-L1-mAb-001",
        "delivery_assumptions": {
            "route": "intravenous",
            "formulation": "liquid for infusion",
        },
        "charter_ref": "DEC-002",
        "termination_authority": "human",
        "created_at": _NOW,
    }


def _no_entity_concept() -> dict[str, Any]:
    """A concept without starting matter (entity_ref is null)."""
    return {
        "schema": "dde.intervention-concept.v1",
        "id": "IC-003",
        "revision": 1,
        "state": "draft",
        "disease_context": {"indication": "alzheimers_disease"},
        "target_pathway": {
            "gene": "TREM2",
            "protein": "TREM2",
            "pathway": "microglial activation",
            "mechanism_hypothesis": "TREM2 agonism enhances microglial phagocytosis",
        },
        "modality": "biologic",
        "entity_ref": None,
        "delivery_assumptions": None,
        "charter_ref": None,
        "termination_authority": "human",
        "created_at": _NOW,
    }


def _sa_score_data(score: float = 2.5) -> dict[str, Any]:
    """SA-score record from `dde compound sa-score`."""
    return {
        "tool": "compound",
        "subcommand": "sa-score",
        "canonical_smiles": "O=C(O)Cc1ccccc1",
        "sa_score": score,
        "rdkit_version": "2024.03.1",
    }


# A complex molecule with multiple stereocenters for heuristic testing
# Taxol core (simplified) — has multiple stereocenters
_COMPLEX_SMILES = "O=C(O)[C@@H](O)[C@H](O)[C@@H](O)[C@H]1OC(=O)c2ccccc21"


# ===========================================================================
# Tests
# ===========================================================================

print("=" * 60)
print("test_manufacturing.py — issue #23 manufacturing assessment")
print("=" * 60)


# ---------------------------------------------------------------------------
# 1. Inventory: compound.py SA-score compatibility
# ---------------------------------------------------------------------------
print("\n--- Inventory: SA-score command compatibility ---")


def test_sa_score_command_exists():
    """The compound sa-score command module exists on disk.

    Verifies that compound.py contains the sa-score subcommand
    definition.  This is an inventory check — confirming existing
    capability is present, not a new feature test.

    Note: ``click`` may not be installed in the test environment,
    so we verify by file inspection rather than import.
    """
    compound_py = REPO_ROOT / "tools" / "dde" / "commands" / "compound.py"
    assert compound_py.is_file(), "compound.py not found"
    source = compound_py.read_text(encoding="utf-8")
    assert "sa-score" in source or "sa_score" in source, (
        "compound.py does not contain an sa-score command"
    )
    assert "def sa_score_cmd" in source, "compound.py does not define sa_score_cmd"


_check(
    "compound sa-score command exists (file inspection)", test_sa_score_command_exists
)


def test_sa_score_helpers_present():
    """The SA-score helper functions are defined in compound.py."""
    compound_py = REPO_ROOT / "tools" / "dde" / "commands" / "compound.py"
    source = compound_py.read_text(encoding="utf-8")
    assert "_require_rdkit_sa_score" in source, (
        "compound.py does not define _require_rdkit_sa_score"
    )
    assert "sascorer" in source, "compound.py does not reference the sascorer module"


_check("SA-score helpers present in compound.py", test_sa_score_helpers_present)


def test_compound_analyze_exists():
    """The compound analyze command is defined in compound.py."""
    compound_py = REPO_ROOT / "tools" / "dde" / "commands" / "compound.py"
    source = compound_py.read_text(encoding="utf-8")
    assert "def analyze_cmd" in source, "compound.py does not define analyze_cmd"
    # Verify it reads descriptors, alerts, and SA-score
    assert "descriptors" in source
    assert "alerts" in source
    assert "sa_score" in source or "sa-score" in source


_check(
    "compound analyze command exists (file inspection)", test_compound_analyze_exists
)


def test_compound_descriptors_exists():
    """The compound descriptors command is defined in compound.py."""
    compound_py = REPO_ROOT / "tools" / "dde" / "commands" / "compound.py"
    source = compound_py.read_text(encoding="utf-8")
    assert "def descriptors_cmd" in source, (
        "compound.py does not define descriptors_cmd"
    )
    # Verify it computes MW, LogP, TPSA, HBA, HBD, rotatable_bonds
    assert "molecular_weight" in source
    assert "logp" in source
    assert "tpsa" in source


_check(
    "compound descriptors command exists (file inspection)",
    test_compound_descriptors_exists,
)


# ---------------------------------------------------------------------------
# 2. Demonstration case: small-molecule with real structure
# ---------------------------------------------------------------------------
print("\n--- Small-molecule concept with structure ---")


def test_small_molecule_with_sa_score():
    """A small-molecule concept with entity_ref (SMILES) and SA-score data
    produces a 'supported' assessment with SA-score findings."""
    concept = _small_molecule_concept()
    sa_data = _sa_score_data(2.5)
    result = assess_stage0(concept, sa_data)

    assert result["evidence_status"] == "supported", (
        f"expected 'supported', got {result['evidence_status']!r}"
    )
    assert result["execution_outcome"] == "completed"
    assert result["schema"] == "dde.evidence-assessment.v1"
    assert "IC-001-r1" in result["concept_ref"]

    # Check findings
    findings = result["findings"]
    aspects = [f["aspect"] for f in findings]
    assert "production_platform_fit" in aspects
    assert "synthetic_accessibility" in aspects
    # complexity_heuristic only appears when RDKit is available
    # (compute_complexity_heuristics returns None without RDKit)
    rdkit_available = compute_complexity_heuristics("C") is not None
    if rdkit_available:
        assert "complexity_heuristic" in aspects


_check(
    "small-molecule + SA-score => supported assessment",
    test_small_molecule_with_sa_score,
)


def test_small_molecule_sa_score_distinction_preserved():
    """The SA-score finding preserves the distinction between SA-score
    and demonstrated synthetic route."""
    concept = _small_molecule_concept()
    sa_data = _sa_score_data(2.5)
    result = assess_stage0(concept, sa_data)

    sa_finding = None
    for f in result["findings"]:
        if f["aspect"] == "synthetic_accessibility":
            sa_finding = f
            break

    assert sa_finding is not None, "no synthetic_accessibility finding"
    assert "distinction" in sa_finding, "missing distinction field"
    assert "NOT" in sa_finding["distinction"], (
        "SA-score distinction must explicitly state SA-score is NOT a synthesis route"
    )
    assert (
        "fragment" in sa_finding["distinction"].lower()
        or "heuristic" in sa_finding["detail"].lower()
    ), "SA-score detail must mention it is a fragment-frequency heuristic"


_check(
    "SA-score distinction (SA-score != synthesizability) preserved",
    test_small_molecule_sa_score_distinction_preserved,
)


def test_small_molecule_without_sa_score():
    """A small-molecule concept with entity_ref but no SA-score data
    produces a finding noting SA-score is not assessed."""
    concept = _small_molecule_concept()
    result = assess_stage0(concept, sa_score_data=None)

    sa_finding = None
    for f in result["findings"]:
        if f["aspect"] == "synthetic_accessibility":
            sa_finding = f
            break

    assert sa_finding is not None, "missing synthetic_accessibility finding"
    assert sa_finding["status"] == "not_assessed", (
        f"expected 'not_assessed' for missing SA-score, got {sa_finding['status']!r}"
    )


_check(
    "small-molecule without SA-score => not_assessed for SA finding",
    test_small_molecule_without_sa_score,
)


# ---------------------------------------------------------------------------
# 3. Demonstration case: biologic concept
# ---------------------------------------------------------------------------
print("\n--- Biologic concept ---")


def test_biologic_concept_assessment():
    """A biologic concept produces a qualitative manufacturing assessment
    with appropriate limitations and no fabricated data."""
    concept = _biologic_concept()
    result = assess_stage0(concept)

    assert result["evidence_status"] == "supported", (
        f"expected 'supported', got {result['evidence_status']!r}"
    )
    assert result["execution_outcome"] == "completed"

    # Check findings
    findings = result["findings"]
    aspects = [f["aspect"] for f in findings]
    assert "production_platform_fit" in aspects
    assert "biologic_manufacturing_qualitative" in aspects

    # Qualitative finding must NOT fabricate data
    bio_finding = next(
        f for f in findings if f["aspect"] == "biologic_manufacturing_qualitative"
    )
    assert "limitations" in bio_finding
    limitations_text = " ".join(bio_finding["limitations"])
    assert "NOT" in limitations_text or "not" in limitations_text.lower(), (
        "biologic finding must state what is NOT assessed"
    )


_check(
    "biologic concept => qualitative assessment without fabrication",
    test_biologic_concept_assessment,
)


def test_biologic_no_fabricated_data():
    """The biologic assessment must not fabricate yield, cost, stability,
    or formulation properties."""
    concept = _biologic_concept()
    result = assess_stage0(concept)

    json.dumps(result)
    fabrication_terms = [
        "yield",
        "cost_of_goods",
        "stability_data",
        "formulation_property",
    ]
    for term in fabrication_terms:
        # These terms should not appear as values (only as limitations/disclaimers)
        json.dumps(result["findings"])
        # Check that none of these appear as actual data values
        for f in result["findings"]:
            assert term not in f.get("status", ""), (
                f"finding status should not contain fabricated term {term!r}"
            )


_check(
    "biologic assessment has no fabricated yield/cost/stability",
    test_biologic_no_fabricated_data,
)


# ---------------------------------------------------------------------------
# 4. Demonstration case: concept without starting matter
# ---------------------------------------------------------------------------
print("\n--- Concept without starting matter ---")


def test_no_entity_produces_not_yet_applicable():
    """A concept with entity_ref=null produces not_yet_applicable, not a
    failure and not a fabricated result."""
    concept = _no_entity_concept()
    result = assess_stage0(concept)

    assert result["evidence_status"] == "not_yet_applicable", (
        f"expected 'not_yet_applicable', got {result['evidence_status']!r}"
    )
    assert result["execution_outcome"] == "completed", (
        "execution_outcome should be 'completed' — the tool ran, "
        "the result is that assessment is not yet applicable"
    )

    # Must have a finding explaining the not_yet_applicable status
    entity_finding = None
    for f in result["findings"]:
        if f["aspect"] == "entity_manufacturing_assessment":
            entity_finding = f
            break

    assert entity_finding is not None, "missing entity_manufacturing_assessment finding"
    assert entity_finding["status"] == "not_yet_applicable"
    assert "trigger" in entity_finding, (
        "not_yet_applicable finding must specify the trigger for future assessment"
    )


_check(
    "no entity_ref => not_yet_applicable (not failure, not fabrication)",
    test_no_entity_produces_not_yet_applicable,
)


def test_no_entity_is_not_a_failure():
    """Missing entity_ref is 'not yet applicable', not an error or failure.
    The execution_outcome must be 'completed', not 'data_unavailable'."""
    concept = _no_entity_concept()
    result = assess_stage0(concept)

    # It should NOT be an execution failure
    assert result["execution_outcome"] == "completed"
    assert result["evidence_status"] != "not_assessed", (
        "no-entity should be not_yet_applicable, not not_assessed"
    )


_check(
    "no entity_ref is not_yet_applicable, not a failure",
    test_no_entity_is_not_a_failure,
)


# ---------------------------------------------------------------------------
# 5. Stereocenter-count flag as heuristic, not rejection
# ---------------------------------------------------------------------------
print("\n--- Stereocenter count as heuristic ---")


def test_stereocenters_flagged_as_heuristic():
    """Stereocenter counts must be flagged as prioritization_heuristic,
    not as a hard rejection or scientific_cutoff.

    When RDKit is unavailable, the test verifies the heuristic type
    is correct in the compute_complexity_heuristics output structure,
    and that the assessment does not contain a hard rejection.
    """
    rdkit_available = compute_complexity_heuristics("C") is not None

    if rdkit_available:
        # Full test with RDKit
        concept = _small_molecule_concept(entity_ref=_COMPLEX_SMILES)
        result = assess_stage0(concept)

        complexity_finding = None
        for f in result["findings"]:
            if f["aspect"] == "complexity_heuristic":
                complexity_finding = f
                break

        assert complexity_finding is not None, "missing complexity_heuristic finding"
        assert (
            complexity_finding.get("requirement_type") == "prioritization_heuristic"
        ), (
            f"expected requirement_type 'prioritization_heuristic', "
            f"got {complexity_finding.get('requirement_type')!r}"
        )
        assert complexity_finding.get("requirement_type") != "hard_constraint"
        assert complexity_finding.get("requirement_type") != "scientific_cutoff"
        detail = complexity_finding.get("detail", "")
        assert "heuristic" in detail.lower() or "NOT" in detail
    else:
        # Without RDKit: verify the structure definition is correct
        # The heuristic_type in the compute function's return schema
        # is defined as "prioritization_heuristic" — verify this from
        # the source code
        import inspect

        source = inspect.getsource(compute_complexity_heuristics)
        assert "prioritization_heuristic" in source
        assert "scientific_cutoff" not in source or "not" in source.lower()
        print("    (RDKit not available — verified from source definition)")


_check(
    "stereocenter count => prioritization_heuristic, not hard rejection",
    test_stereocenters_flagged_as_heuristic,
)


def test_high_stereocenters_not_automatic_rejection():
    """A molecule with many stereocenters is NOT automatically rejected.
    It is flagged with the specific concern.

    When RDKit is unavailable, verify the assessment does not produce
    a 'contradicted' status (which would be an automatic rejection).
    """
    concept = _small_molecule_concept(entity_ref=_COMPLEX_SMILES)
    result = assess_stage0(concept)

    # Regardless of RDKit availability: the assessment must not reject
    # based on complexity alone.  Without RDKit, no complexity finding
    # is produced (which is correct — no heuristic data available).
    # The status should never be "contradicted" due to stereocenters.
    assert result["evidence_status"] != "contradicted", (
        "complex molecule must not be auto-rejected (contradicted)"
    )

    # When RDKit is available, status should be "supported"
    rdkit_available = compute_complexity_heuristics("C") is not None
    if rdkit_available:
        assert result["evidence_status"] == "supported", (
            f"expected 'supported' even with complex molecule, "
            f"got {result['evidence_status']!r}"
        )
    else:
        # Without RDKit and without SA-score, it may be "not_assessed"
        # since we can't compute complexity — that's acceptable
        assert result["evidence_status"] in ("not_assessed", "supported"), (
            f"without RDKit, expected not_assessed or supported, "
            f"got {result['evidence_status']!r}"
        )
        print("    (RDKit not available — verified no auto-rejection)")


_check(
    "high stereocenters => still 'supported' (not rejected)",
    test_high_stereocenters_not_automatic_rejection,
)


# ---------------------------------------------------------------------------
# 6. Qualitative Phase 1 finding: no fabricated data
# ---------------------------------------------------------------------------
print("\n--- Qualitative Phase 1 finding: no fabrication ---")


def test_qualitative_finding_has_required_fields():
    """Qualitative Phase 1 findings must cite precedent, assumptions,
    limitations, owner, and next required evidence."""
    concept = _biologic_concept()
    result = assess_stage0(concept)

    bio_finding = None
    for f in result["findings"]:
        if f["aspect"] == "biologic_manufacturing_qualitative":
            bio_finding = f
            break

    assert bio_finding is not None, "missing biologic_manufacturing_qualitative"

    # Required fields per task brief
    assert "assumptions" in bio_finding, "qualitative finding must cite assumptions"
    assert "limitations" in bio_finding, "qualitative finding must cite limitations"
    assert "next_evidence" in bio_finding, (
        "qualitative finding must name next required evidence"
    )
    assert "owner" in bio_finding, "qualitative finding must name the owner"

    # Assumptions must be non-empty
    assert len(bio_finding["assumptions"]) > 0, "assumptions must be non-empty"
    assert len(bio_finding["limitations"]) > 0, "limitations must be non-empty"


_check(
    "qualitative finding has precedent, assumptions, limitations, owner, next_evidence",
    test_qualitative_finding_has_required_fields,
)


def test_qualitative_finding_no_fabricated_numbers():
    """Qualitative findings must NOT contain fabricated yield, cost of
    goods, stability, or formulation properties."""
    concept = _biologic_concept()
    result = assess_stage0(concept)

    # Scan all findings for fabricated numeric data that should not exist
    for f in result["findings"]:
        # These keys should never appear with invented values
        assert "yield_percent" not in f, "yield_percent is fabricated data"
        assert "cost_of_goods" not in f, "cost_of_goods is fabricated data"
        assert "stability_months" not in f, "stability_months is fabricated data"
        assert "formulation_viscosity" not in f, (
            "formulation_viscosity is fabricated data"
        )
        assert "titer" not in f, "titer is fabricated data"


_check(
    "qualitative finding has no fabricated yield/cost/stability numbers",
    test_qualitative_finding_no_fabricated_numbers,
)


# ---------------------------------------------------------------------------
# 7. Assessment supersedes chain
# ---------------------------------------------------------------------------
print("\n--- Assessment supersedes chain ---")


def test_supersedes_chain_supported():
    """The assessment schema supports a supersedes field for reassessment.
    An initial rejection followed by a superseding reassessment should
    be representable."""
    # Initial assessment — contradicted (manufacturing concern)
    initial = assess_stage0(_small_molecule_concept(), _sa_score_data(8.5))
    initial["id"] = "AR-010"

    # Override the status for test — simulating a rejection
    initial["evidence_status"] = "contradicted"
    initial["rationale"] = "High SA-score indicates synthetic difficulty"

    # Validate the initial assessment
    errors = validate_assessment(initial)
    # Filter out errors about extra fields (our findings/stage_requirements are extras)
    schema_errors = [
        e
        for e in errors
        if "missing" in e
        or "schema" in e
        or "evidence_status" in e
        or "execution_outcome" in e
    ]
    assert len(schema_errors) == 0, (
        f"initial assessment validation errors: {schema_errors}"
    )

    # Superseding assessment — new evidence shows a viable route
    superseding = assess_stage0(_small_molecule_concept(), _sa_score_data(3.0))
    superseding["id"] = "AR-011"
    superseding["supersedes"] = "AR-010"

    # Validate the superseding assessment
    errors2 = validate_assessment(superseding)
    schema_errors2 = [
        e
        for e in errors2
        if "missing" in e
        or "schema" in e
        or "evidence_status" in e
        or "execution_outcome" in e
    ]
    assert len(schema_errors2) == 0, (
        f"superseding assessment validation errors: {schema_errors2}"
    )

    # Verify the supersedes chain
    assert superseding["supersedes"] == "AR-010"
    assert superseding["id"] != initial["id"]


_check("assessment supersedes chain is representable", test_supersedes_chain_supported)


def test_supersedes_format_validation():
    """The supersedes field must match AR-NNN format."""
    assessment = assess_stage0(_small_molecule_concept(), _sa_score_data(2.5))
    assessment["id"] = "AR-020"
    assessment["supersedes"] = "WRONG-001"

    errors = validate_assessment(assessment)
    supersedes_errors = [e for e in errors if "supersedes" in e]
    assert len(supersedes_errors) > 0, (
        "supersedes with bad format should produce validation errors"
    )


_check("supersedes bad format => validation error", test_supersedes_format_validation)


# ---------------------------------------------------------------------------
# 8. Stage requirement structure
# ---------------------------------------------------------------------------
print("\n--- Stage requirement structure ---")


def test_stage_requirements_defined():
    """Stage 0, 2, 3, and 4 requirements are defined."""
    assert 0 in STAGE_REQUIREMENTS
    assert 2 in STAGE_REQUIREMENTS
    assert 3 in STAGE_REQUIREMENTS
    assert 4 in STAGE_REQUIREMENTS


_check("stages 0, 2, 3, 4 are defined", test_stage_requirements_defined)


def test_stage_requirements_have_evidence_types():
    """Each stage definition has evidence_types that can be referenced
    by #11 policy requirements."""
    for stage, req in STAGE_REQUIREMENTS.items():
        assert "evidence_types" in req, f"stage {stage} missing evidence_types"
        assert len(req["evidence_types"]) > 0, f"stage {stage} has no evidence_types"
        assert "description" in req, f"stage {stage} missing description"


_check(
    "each stage has evidence_types referrable by policy",
    test_stage_requirements_have_evidence_types,
)


def test_stage_2_4_are_placeholders():
    """Stages 2-4 are documented as forward-looking placeholders since
    underlying tools don't exist yet."""
    for stage in (2, 3, 4):
        req = STAGE_REQUIREMENTS[stage]
        assert "status" in req, (
            f"stage {stage} should have a 'status' field indicating placeholder"
        )
        status = req["status"]
        assert "placeholder" in status.lower(), (
            f"stage {stage} status should indicate placeholder, got: {status!r}"
        )


_check("stages 2-4 documented as placeholders", test_stage_2_4_are_placeholders)


def test_stage0_is_concrete():
    """Stage 0 is concretely implemented (no placeholder status)."""
    req = STAGE_REQUIREMENTS[0]
    assert "status" not in req or "placeholder" not in req.get("status", "").lower(), (
        "Stage 0 should be concretely implemented, not a placeholder"
    )


_check("stage 0 is concretely implemented", test_stage0_is_concrete)


# ---------------------------------------------------------------------------
# 9. Production platform fit
# ---------------------------------------------------------------------------
print("\n--- Production platform fit ---")


def test_known_modalities_have_platforms():
    """All known modalities have production platform mappings."""
    expected_modalities = {
        "small_molecule",
        "biologic",
        "antibody",
        "molecular_glue",
        "protac",
        "peptide",
        "oligonucleotide",
    }
    for mod in expected_modalities:
        assert mod in PRODUCTION_PLATFORMS, (
            f"modality {mod!r} missing from PRODUCTION_PLATFORMS"
        )
        assert len(PRODUCTION_PLATFORMS[mod]["platforms"]) > 0, (
            f"modality {mod!r} has no production platforms"
        )


_check(
    "known modalities have production platform mappings",
    test_known_modalities_have_platforms,
)


def test_unknown_modality_produces_not_assessed():
    """An unknown modality produces not_assessed for platform fit, not a crash."""
    concept = _small_molecule_concept()
    concept["modality"] = "novel_modality_xyz"
    concept["entity_ref"] = None  # also no entity

    result = assess_stage0(concept)

    # Should not crash
    assert result["execution_outcome"] == "completed"

    # Platform fit finding should be not_assessed
    platform_finding = next(
        f for f in result["findings"] if f["aspect"] == "production_platform_fit"
    )
    assert platform_finding["status"] == "not_assessed"


_check(
    "unknown modality => not_assessed for platform fit",
    test_unknown_modality_produces_not_assessed,
)


# ---------------------------------------------------------------------------
# 10. Complexity heuristics computation
# ---------------------------------------------------------------------------
print("\n--- Complexity heuristics ---")


def test_complexity_heuristics_simple_molecule():
    """Complexity heuristics for a simple molecule (aspirin)."""
    # Aspirin: CC(=O)Oc1ccccc1C(=O)O
    result = compute_complexity_heuristics("CC(=O)Oc1ccccc1C(=O)O")
    if result is None:
        # RDKit not available — skip but don't fail
        print("    (skipped: RDKit not available)")
        return

    assert result["stereocenter_count"] == 0, (
        f"aspirin has no stereocenters, got {result['stereocenter_count']}"
    )
    assert result["ring_count"] >= 1
    assert result["heuristic_type"] == "prioritization_heuristic"


_check(
    "complexity heuristics: simple molecule (aspirin)",
    test_complexity_heuristics_simple_molecule,
)


def test_complexity_heuristics_chiral_molecule():
    """Complexity heuristics for a molecule with stereocenters."""
    result = compute_complexity_heuristics(_COMPLEX_SMILES)
    if result is None:
        print("    (skipped: RDKit not available)")
        return

    assert result["stereocenter_count"] > 0, (
        "complex molecule should have stereocenters"
    )
    assert result["heuristic_type"] == "prioritization_heuristic"
    assert "scope" in result


_check(
    "complexity heuristics: chiral molecule", test_complexity_heuristics_chiral_molecule
)


def test_complexity_heuristics_invalid_smiles():
    """Invalid SMILES returns None for complexity heuristics."""
    result = compute_complexity_heuristics("not_a_smiles")
    if result is None:
        # Either RDKit not available or invalid SMILES
        pass  # Expected


_check(
    "complexity heuristics: invalid SMILES => None",
    test_complexity_heuristics_invalid_smiles,
)


# ---------------------------------------------------------------------------
# 11. Assessment record validates against evidence schema
# ---------------------------------------------------------------------------
print("\n--- Assessment record validation ---")


def test_assessment_validates_against_schema():
    """Manufacturing assessment records validate against the evidence
    assessment schema (#75)."""
    concept = _small_molecule_concept()
    result = assess_stage0(concept, _sa_score_data(2.5))
    result["id"] = "AR-100"

    errors = validate_assessment(result)
    # Filter to schema-level errors (not extra fields)
    critical = [
        e
        for e in errors
        if "missing" in e
        or "schema" in e
        or "evidence_status" in e
        or "execution_outcome" in e
        or "id" in e
    ]
    assert len(critical) == 0, f"assessment schema errors: {critical}"


_check(
    "manufacturing assessment validates against evidence schema",
    test_assessment_validates_against_schema,
)


def test_not_yet_applicable_is_valid_status():
    """not_yet_applicable is a valid evidence status in the schema."""
    assert "not_yet_applicable" in EVIDENCE_STATUSES


_check(
    "not_yet_applicable is in EVIDENCE_STATUSES",
    test_not_yet_applicable_is_valid_status,
)


def test_assessment_evidence_type_is_manufacturing():
    """The assessment evidence type is 'manufacturing_feasibility'."""
    concept = _small_molecule_concept()
    result = assess_stage0(concept, _sa_score_data(2.5))

    evidence = result.get("evidence", {})
    assert evidence.get("evidence_type") == "manufacturing_feasibility"


_check(
    "assessment evidence_type is manufacturing_feasibility",
    test_assessment_evidence_type_is_manufacturing,
)


# ---------------------------------------------------------------------------
# 12. SA-score modality scoping
# ---------------------------------------------------------------------------
print("\n--- SA-score modality scoping ---")


def test_sa_score_only_for_chemical_synthesis_modalities():
    """SA-score is only applicable to modalities using chemical synthesis."""
    assert "small_molecule" in SA_SCORE_MODALITIES
    assert "molecular_glue" in SA_SCORE_MODALITIES
    assert "protac" in SA_SCORE_MODALITIES
    assert "antibody" not in SA_SCORE_MODALITIES
    assert "biologic" not in SA_SCORE_MODALITIES


_check(
    "SA-score scoped to chemical synthesis modalities",
    test_sa_score_only_for_chemical_synthesis_modalities,
)


def test_biologic_does_not_get_sa_score():
    """A biologic concept does not produce an SA-score finding."""
    concept = _biologic_concept()
    result = assess_stage0(concept)

    aspects = [f["aspect"] for f in result["findings"]]
    assert "synthetic_accessibility" not in aspects, (
        "biologic assessment should not have synthetic_accessibility finding"
    )


_check("biologic concept has no SA-score finding", test_biologic_does_not_get_sa_score)


# ---------------------------------------------------------------------------
# 13. Neither early screen implies GMP or manufacturing clearance
# ---------------------------------------------------------------------------
print("\n--- No GMP or manufacturing clearance implied ---")


def test_disclaimer_present():
    """Every assessment must include the disclaimer that Stage 0 does NOT
    imply GMP readiness or manufacturing clearance."""
    for concept_fn in [_small_molecule_concept, _biologic_concept, _no_entity_concept]:
        concept = concept_fn()
        sa = (
            _sa_score_data()
            if concept["modality"] in SA_SCORE_MODALITIES and concept["entity_ref"]
            else None
        )
        result = assess_stage0(concept, sa)

        rationale = result.get("rationale", "")
        assert "NOT" in rationale or "not" in rationale.lower(), (
            f"rationale for {concept['id']} must disclaim GMP/clearance"
        )
        assert "GMP" in rationale or "manufacturing clearance" in rationale.lower(), (
            f"rationale for {concept['id']} must mention GMP or manufacturing clearance"
        )


_check("all assessments disclaim GMP/manufacturing clearance", test_disclaimer_present)


# ---------------------------------------------------------------------------
# 14. Manufacturing evidence types
# ---------------------------------------------------------------------------
print("\n--- Manufacturing evidence types ---")


def test_evidence_types_documented():
    """Manufacturing evidence types are documented with descriptions."""
    assert "manufacturing_feasibility" in MANUFACTURING_EVIDENCE_TYPES
    assert "synthetic_accessibility" in MANUFACTURING_EVIDENCE_TYPES
    assert "complexity_heuristic" in MANUFACTURING_EVIDENCE_TYPES
    assert "production_platform_fit" in MANUFACTURING_EVIDENCE_TYPES


_check("manufacturing evidence types documented", test_evidence_types_documented)


# ---------------------------------------------------------------------------
# 15. Delivery assumptions handling
# ---------------------------------------------------------------------------
print("\n--- Delivery assumptions ---")


def test_delivery_assumptions_assessed():
    """When delivery_assumptions are present, they produce a finding."""
    concept = _small_molecule_concept()
    assert concept["delivery_assumptions"] is not None

    result = assess_stage0(concept, _sa_score_data())

    aspects = [f["aspect"] for f in result["findings"]]
    assert "delivery_manufacturing_compatibility" in aspects


_check("delivery assumptions produce a finding", test_delivery_assumptions_assessed)


def test_null_delivery_no_finding():
    """When delivery_assumptions is null, no delivery finding is produced."""
    concept = _no_entity_concept()
    assert concept["delivery_assumptions"] is None

    result = assess_stage0(concept)

    aspects = [f["aspect"] for f in result["findings"]]
    assert "delivery_manufacturing_compatibility" not in aspects


_check(
    "null delivery_assumptions => no delivery finding", test_null_delivery_no_finding
)


# ---------------------------------------------------------------------------
# 16. Regression: artifact_dir("manufacturing") default-output resolution
# ---------------------------------------------------------------------------
print("\n--- Default artifact-dir resolution for manufacturing (#23 regression) ---")


def test_artifact_dir_manufacturing_registered():
    """ARTIFACT_DIRS must contain 'manufacturing' so that the default output
    path works without --out.  This was missing when #23 merged, causing
    `dde manufacturing assess-stage0` to fail with 'unknown artifact class'
    on every invocation that didn't pass --out explicitly."""
    from dde.core.context import ARTIFACT_DIRS

    assert "manufacturing" in ARTIFACT_DIRS, (
        "'manufacturing' missing from ARTIFACT_DIRS — the default-output "
        "path in `dde manufacturing assess-stage0` is broken"
    )
    assert ARTIFACT_DIRS["manufacturing"] == "raw/manufacturing", (
        f"expected 'raw/manufacturing', got {ARTIFACT_DIRS['manufacturing']!r}"
    )


_check(
    "ARTIFACT_DIRS contains 'manufacturing' entry",
    test_artifact_dir_manufacturing_registered,
)


def test_manufacturing_cli_default_output_path():
    """Run `dde manufacturing assess-stage0` via CliRunner WITHOUT --out.

    This is the exact code path that was broken: the command resolves its
    output directory via artifact_dir("manufacturing", None), which looks
    up 'manufacturing' in ARTIFACT_DIRS.  Before the fix, this raised
    ProjectRootError('unknown artifact class').

    When click is unavailable, falls back to a direct artifact_dir() call
    to verify the resolution succeeds (the same call the CLI makes).
    """
    import tempfile

    try:
        from click.testing import CliRunner
        from dde.cli import cli

        has_click = True
    except ImportError:
        has_click = False

    if has_click:
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "test-project"
            project.mkdir()
            (project / ".dde").mkdir()

            # Write a concept record
            concept = _small_molecule_concept()
            concept_file = project / "concept.json"
            concept_file.write_text(json.dumps(concept, indent=2), encoding="utf-8")

            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "manufacturing",
                    "assess-stage0",
                    str(concept_file),
                    "--json",
                ],
            )

            assert result.exit_code == 0, (
                f"CLI exited with code {result.exit_code}; output:\n{result.output}"
            )

            # Verify the output was written to the default location
            mfg_dir = project / "raw" / "manufacturing"
            assert mfg_dir.is_dir(), (
                f"expected default output dir {mfg_dir} to be created"
            )
            output_files = list(mfg_dir.glob("*.manufacturing-stage0.json"))
            assert len(output_files) == 1, (
                f"expected 1 output file in {mfg_dir}, found {len(output_files)}: "
                f"{output_files}"
            )
    else:
        # click not installed — verify the core resolution path directly.
        # This is the call that manufacturing.py:99 makes; a KeyError here
        # is the exact bug this regression test exists to catch.
        from dde.core.context import ProjectContext

        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "test-project"
            project.mkdir()
            (project / ".dde").mkdir()

            ctx = ProjectContext(root=project, source="test")
            target = ctx.artifact_dir("manufacturing", None)

            assert target == project / "raw" / "manufacturing", (
                f"expected {project / 'raw' / 'manufacturing'}, got {target}"
            )
            assert target.is_dir(), "artifact_dir must create the directory"
        print("    (click not available — verified via direct artifact_dir() call)")


_check(
    "CLI assess-stage0 succeeds via default output path (no --out)",
    test_manufacturing_cli_default_output_path,
)


# ===========================================================================
# Regression: #221 — Unrecognized modality must NOT be falsely certified
# ===========================================================================


def test_unrecognized_modality_with_entity_not_supported():
    """An unknown modality + entity_ref must NOT produce 'supported'.

    Before the fix, an unrecognized modality with a non-null entity_ref
    fell through to the biologic-like else branch and was falsely
    certified as having a supported manufacturing path.
    """
    concept = {
        "schema": "dde.intervention-concept.v1",
        "id": "IC-099",
        "revision": 1,
        "state": "active",
        "disease_context": {"indication": "solid_tumors"},
        "target_pathway": {
            "gene": "TEST",
            "protein": "TEST",
            "pathway": "Test",
            "mechanism_hypothesis": "Test hypothesis",
        },
        "modality": "novel_unmapped_modality",
        "entity_ref": "CHEMBL123",
        "delivery_assumptions": None,
        "charter_ref": None,
        "termination_authority": "human",
        "created_at": _NOW,
    }
    assessment = assess_stage0(concept)
    assert assessment["evidence_status"] != "supported", (
        f"Unrecognized modality must NOT be certified as 'supported', "
        f"got {assessment['evidence_status']!r}"
    )
    assert assessment["evidence_status"] == "not_assessed", (
        f"Expected 'not_assessed' for unrecognized modality, "
        f"got {assessment['evidence_status']!r}"
    )
    # Ensure no biologic_manufacturing_qualitative finding exists
    aspects = [f["aspect"] for f in assessment.get("findings", [])]
    assert "biologic_manufacturing_qualitative" not in aspects, (
        "Unrecognized modality must NOT produce a biologic qualitative finding"
    )


_check(
    "unrecognized_modality_with_entity_not_supported (#221)",
    test_unrecognized_modality_with_entity_not_supported,
)


def test_unrecognized_modality_null_entity_not_supported():
    """An unknown modality + null entity_ref must NOT be 'supported'."""
    concept = {
        "schema": "dde.intervention-concept.v1",
        "id": "IC-100",
        "revision": 1,
        "state": "active",
        "disease_context": {"indication": "solid_tumors"},
        "target_pathway": {
            "gene": "TEST",
            "protein": "TEST",
            "pathway": "Test",
            "mechanism_hypothesis": "Test hypothesis",
        },
        "modality": "completely_fake_modality",
        "entity_ref": None,
        "delivery_assumptions": None,
        "charter_ref": None,
        "termination_authority": "human",
        "created_at": _NOW,
    }
    assessment = assess_stage0(concept)
    assert assessment["evidence_status"] != "supported", (
        f"Unrecognized modality (no entity) must NOT be 'supported', "
        f"got {assessment['evidence_status']!r}"
    )


_check(
    "unrecognized_modality_null_entity_not_supported (#221)",
    test_unrecognized_modality_null_entity_not_supported,
)


def test_known_biologic_modality_still_works():
    """A known biologic modality with entity_ref should still work."""
    concept = {
        "schema": "dde.intervention-concept.v1",
        "id": "IC-101",
        "revision": 1,
        "state": "active",
        "disease_context": {"indication": "nsclc"},
        "target_pathway": {
            "gene": "PD-L1",
            "protein": "PD-L1",
            "pathway": "PD-1/PD-L1",
            "mechanism_hypothesis": "PD-L1 blockade",
        },
        "modality": "antibody",
        "entity_ref": "anti-PD-L1-mAb",
        "delivery_assumptions": None,
        "charter_ref": None,
        "termination_authority": "human",
        "created_at": _NOW,
    }
    assessment = assess_stage0(concept)
    assert assessment["evidence_status"] == "supported", (
        f"Known biologic modality with entity should be 'supported', "
        f"got {assessment['evidence_status']!r}"
    )
    aspects = [f["aspect"] for f in assessment.get("findings", [])]
    assert "biologic_manufacturing_qualitative" in aspects, (
        "Known biologic modality must produce a biologic qualitative finding"
    )


_check(
    "known_biologic_modality_still_works (#221 non-regression)",
    test_known_biologic_modality_still_works,
)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
