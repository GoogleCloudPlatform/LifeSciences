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

"""Fixture definitions for the DDE evaluation harness.

Each fixture documents:
  - scenario: what workflow situation it represents
  - provenance: "synthetic" (clearly labeled) or "derived from <source>"
  - category: which issue #82 fixture category it covers
  - setup: what control-plane records and artifacts it creates
  - expected_behavior: what the current workflow should do (not a gold label,
    but a description of observable process behavior)

Fixture categories from issue #82:
  1. no_genetic_support
  2. negative_pocket_conformation
  3. positive_model_geometry
  4. modality_mismatch
  5. absent_entity_inputs
  6. tool_failure
  7. disputed_citation
  8. bounded_review_exhaustion
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Intentionally evaluated once at import time and shared across all
# fixtures.  All synthetic records carry the same timestamp because they
# represent a single evaluation snapshot, not a time-ordered sequence.
_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FixtureDefinition:
    """A single evaluation fixture with provenance documentation."""

    fixture_id: str
    label: str
    category: str
    provenance: str  # "synthetic" or "derived from <source>"
    scenario_description: str
    expected_behavior: str  # Observable process behavior, NOT a correctness label

    # Work-order data for the fixture
    work_order: dict[str, Any] = field(default_factory=dict)

    # Optional: hypothesis data for hypothesis-based fixtures
    hypothesis_data: list[dict[str, Any]] | None = None

    # Optional: synthetic artifacts to place in the project
    synthetic_artifacts: list[dict[str, Any]] = field(default_factory=list)

    # Optional: run data for run-lifecycle fixtures
    run_data: dict[str, Any] | None = None

    # Optional: validation data for validation fixtures
    validation_data: dict[str, Any] | None = None


def _wo(
    wo_id: str,
    decision_question: str,
    requested_role: str,
    stage: str,
    state: str = "proposed",
    revision: int = 1,
    cycle: int = 1,
    layer_0_classes: list[str] | None = None,
    layer_1: list[str] | None = None,
    context: dict[str, Any] | None = None,
    dependencies: list[str] | None = None,
    capabilities: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a valid work-order record dict."""
    data: dict[str, Any] = {
        "id": wo_id,
        "revision": revision,
        "state": state,
        "decision_question": decision_question,
        "requested_role": requested_role,
        "stage": stage,
        "cycle": cycle,
        "context": context or {"summary": "Evaluation fixture context"},
        "dependencies": dependencies or [],
        "capabilities": capabilities or ["evaluation"],
        "deliverables": {},
        "acceptance_criteria": "Evaluation fixture — process measurement only",
        "alert_policy": {"on_failure": "log"},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "eval-harness",
        "created_at": _NOW,
    }
    deliverables: dict[str, Any] = {}
    if layer_0_classes:
        deliverables["layer_0_classes"] = layer_0_classes
    if layer_1:
        deliverables["layer_1"] = layer_1
    if not deliverables:
        deliverables["layer_0_classes"] = ["structures"]
    data["deliverables"] = deliverables
    data.update(extra)
    return data


# -----------------------------------------------------------------------
# Fixture 1: No genetic support
# -----------------------------------------------------------------------

FIXTURE_NO_GENETIC_SUPPORT = FixtureDefinition(
    fixture_id="EVAL-001",
    label="No genetic support — target lacks GWAS/genetic evidence",
    category="no_genetic_support",
    provenance="synthetic",
    scenario_description=(
        "A hypothesis set is adopted for a target (synthetic protein X) "
        "with no genetic association evidence.  A work order requests "
        "genetic evidence analysis.  Submitted for validation with no "
        "genetics artifacts in Layer 0 — validation should fail on "
        "deliverables_exist because no artifacts are present."
    ),
    expected_behavior=(
        "Work order reaches submitted state.  Validation fails on "
        "deliverables_exist check because no genetics Layer 0 artifacts "
        "exist.  The workflow correctly flags the gap rather than "
        "accepting absent evidence."
    ),
    work_order=_wo(
        wo_id="WO-001",
        decision_question=(
            "Does synthetic protein X have genetic evidence supporting "
            "its role in Alzheimer's disease?"
        ),
        requested_role="computational-biologist",
        stage="target-nomination",
        layer_0_classes=["genomics"],
        layer_1=["findings/computational-biology/genetic-evidence.md"],
        context={
            "summary": "Evaluate genetic support for synthetic protein X in AD",
            "target": "synthetic-protein-X",
            "indication": "Alzheimer's disease",
            "note": "SYNTHETIC FIXTURE — no real target",
        },
    ),
    hypothesis_data=[
        {
            "statement": (
                "Synthetic protein X variants are enriched in Alzheimer's "
                "disease GWAS loci"
            ),
            "mechanism": "Unknown — synthetic fixture",
            "evidence_basis": "None — this is a synthetic no-evidence fixture",
        },
    ],
)


# -----------------------------------------------------------------------
# Fixture 2: One negative pocket conformation
# -----------------------------------------------------------------------

FIXTURE_NEGATIVE_POCKET = FixtureDefinition(
    fixture_id="EVAL-002",
    label="Negative pocket conformation — unfavorable druggability",
    category="negative_pocket_conformation",
    provenance="synthetic",
    scenario_description=(
        "A work order requests pocket druggability assessment.  A single "
        "synthetic fpocket result shows an unfavorable druggability score "
        "(low volume, poor enclosure).  The analysis should produce "
        "relays indicating single-conformation assessment."
    ),
    expected_behavior=(
        "Work order transitions through the lifecycle.  Pocket analysis "
        "artifact has unfavorable metrics.  The fpocket.single_conformation "
        "relay fires because only one conformation was assessed."
    ),
    work_order=_wo(
        wo_id="WO-002",
        decision_question=(
            "Is the binding pocket of synthetic kinase Y druggable by small molecules?"
        ),
        requested_role="structural-biologist",
        stage="target-nomination",
        layer_0_classes=["pocket"],
        layer_1=["findings/structural-biology/pocket-druggability.md"],
        context={
            "summary": "Pocket druggability for synthetic kinase Y",
            "target": "synthetic-kinase-Y",
            "structure_source": "AlphaFold2 prediction",
            "note": "SYNTHETIC FIXTURE — fabricated unfavorable pocket",
        },
    ),
    synthetic_artifacts=[
        {
            "path": "raw/pocket/synthetic-kinase-Y.fpocket.json",
            "content": {
                "schema": "dde.pocket-druggability.v1",
                "target": "synthetic-kinase-Y",
                "pdb_source": "AF-SYNTHY-F1-model_v4.pdb",
                "method": "fpocket",
                "pockets": [
                    {
                        "pocket_id": 1,
                        "volume_A3": 85.2,
                        "druggability_score": 0.12,
                        "enclosure_ratio": 0.31,
                        "hydrophobicity_score": 0.28,
                        "assessment": "unfavorable",
                    },
                ],
                "conformations_assessed": 1,
                "note": "SYNTHETIC — fabricated unfavorable pocket for evaluation",
            },
            "sidecar": {
                "tool": "pocket",
                "subcommand": "fetch",
                "endpoint": "fpocket-local",
                "parameters": {"pdb": "AF-SYNTHY-F1-model_v4.pdb"},
                "started_at": _NOW,
                "finished_at": _NOW,
                "outputs": [],
                "warnings": [],
                "mandatory_relays": [
                    {
                        "code": "fpocket.single_conformation",
                        "obligation": (
                            "Only one conformation was assessed. "
                            "Pocket geometry may differ across conformational "
                            "ensemble."
                        ),
                    },
                ],
            },
        },
    ],
)


# -----------------------------------------------------------------------
# Fixture 3: Positive model geometry
# -----------------------------------------------------------------------

FIXTURE_POSITIVE_GEOMETRY = FixtureDefinition(
    fixture_id="EVAL-003",
    label="Positive model geometry — favorable structural confidence",
    category="positive_model_geometry",
    provenance="synthetic",
    scenario_description=(
        "A work order for structure confidence assessment.  A synthetic "
        "AlphaFold model has high pLDDT in the binding region.  This "
        "represents a favorable structural assessment scenario."
    ),
    expected_behavior=(
        "Work order transitions normally.  Structure confidence artifact "
        "shows favorable metrics.  Validation passes if deliverables "
        "are present."
    ),
    work_order=_wo(
        wo_id="WO-003",
        decision_question=(
            "Is the AlphaFold model of synthetic enzyme Z reliable "
            "enough for structure-based drug design?"
        ),
        requested_role="structural-biologist",
        stage="target-nomination",
        layer_0_classes=["structures"],
        layer_1=["findings/structural-biology/structure-confidence.md"],
        context={
            "summary": "Structure confidence for synthetic enzyme Z",
            "target": "synthetic-enzyme-Z",
            "uniprot": "SYNZZ_HUMAN",
            "note": "SYNTHETIC FIXTURE — fabricated favorable geometry",
        },
    ),
    synthetic_artifacts=[
        {
            "path": "raw/structures/synthetic-enzyme-Z.alphafold.json",
            "content": {
                "schema": "dde.structure-confidence.v1",
                "target": "synthetic-enzyme-Z",
                "uniprot_id": "SYNZZ_HUMAN",
                "source": "AlphaFold DB v4",
                "global_plddt": 88.3,
                "binding_region_plddt": 91.7,
                "binding_region_residues": "145-210",
                "pae_max_binding_region": 3.2,
                "assessment": "high confidence in binding region",
                "note": "SYNTHETIC — fabricated favorable geometry for evaluation",
            },
            "sidecar": {
                "tool": "alphafold",
                "subcommand": "fetch",
                "endpoint": "alphafold-db",
                "parameters": {"uniprot": "SYNZZ_HUMAN"},
                "started_at": _NOW,
                "finished_at": _NOW,
                "outputs": [],
                "warnings": [],
                "mandatory_relays": [],
            },
        },
    ],
)


# -----------------------------------------------------------------------
# Fixture 4: Modality mismatch
# -----------------------------------------------------------------------

FIXTURE_MODALITY_MISMATCH = FixtureDefinition(
    fixture_id="EVAL-004",
    label="Modality mismatch — antibody target with small-molecule deliverables",
    category="modality_mismatch",
    provenance="synthetic",
    scenario_description=(
        "A work order specifies an antibody-class target in its context "
        "but requests small-molecule-oriented deliverables (docking, "
        "compound descriptors).  The current workflow does not have "
        "an explicit modality-check gate — this fixture measures whether "
        "the mismatch is caught or silently accepted."
    ),
    expected_behavior=(
        "Work order creates and transitions normally — the current "
        "workflow has no modality-mismatch check.  Validation may pass "
        "on structural checks but the mismatch is not flagged.  This "
        "establishes a baseline for future modality-awareness improvements."
    ),
    work_order=_wo(
        wo_id="WO-004",
        decision_question=(
            "Can synthetic membrane receptor W be targeted with a "
            "small-molecule compound?"
        ),
        requested_role="medicinal-chemist",
        stage="target-nomination",
        layer_0_classes=["docking", "compounds", "descriptors"],
        layer_1=["findings/medicinal-chemistry/compound-screen.md"],
        context={
            "summary": "Small-molecule screen for synthetic membrane receptor W",
            "target": "synthetic-receptor-W",
            "modality": "antibody",
            "indication": "Psoriatic arthritis",
            "note": (
                "SYNTHETIC FIXTURE — deliberate mismatch: context says "
                "antibody, deliverables say small-molecule"
            ),
        },
    ),
)


# -----------------------------------------------------------------------
# Fixture 5: Absent entity inputs
# -----------------------------------------------------------------------

FIXTURE_ABSENT_ENTITY = FixtureDefinition(
    fixture_id="EVAL-005",
    label="Absent entity inputs — missing required compound and target identifiers",
    category="absent_entity_inputs",
    provenance="synthetic",
    scenario_description=(
        "A work order requests compound property analysis but the "
        "context contains no compound identifiers (no SMILES, no "
        "InChIKey, no PubChem CID).  The target field is also empty.  "
        "This tests how the workflow handles missing required inputs."
    ),
    expected_behavior=(
        "Work order creates successfully (the control plane does not "
        "validate context content).  When a tool attempts to use the "
        "missing identifiers, it would fail with a usage or artifact "
        "error.  The baseline measures whether missing inputs produce "
        "clear error messages at the appropriate stage."
    ),
    work_order=_wo(
        wo_id="WO-005",
        decision_question=("What are the ADMET properties of the lead compound?"),
        requested_role="admet-dmpk-scientist",
        stage="target-nomination",
        layer_0_classes=["compounds", "descriptors", "admet"],
        layer_1=["findings/admet-dmpk/compound-profile.md"],
        context={
            "summary": "ADMET profiling for lead compound",
            "target": "",
            "compound_smiles": "",
            "compound_inchikey": "",
            "pubchem_cid": None,
            "note": (
                "SYNTHETIC FIXTURE — deliberately empty entity "
                "identifiers to test absent-input handling"
            ),
        },
    ),
)


# -----------------------------------------------------------------------
# Fixture 6: Tool failure
# -----------------------------------------------------------------------

FIXTURE_TOOL_FAILURE = FixtureDefinition(
    fixture_id="EVAL-006",
    label="Tool failure — run fails with infrastructure error",
    category="tool_failure",
    provenance="synthetic",
    scenario_description=(
        "A work order proceeds normally through committed and queued "
        "states.  The run transitions to running, then fails with "
        "failure_class 'transient_infrastructure'.  This tests the "
        "failure-handling path in the state machine and whether the "
        "failure is properly recorded."
    ),
    expected_behavior=(
        "Work order transitions to in_progress.  Run transitions from "
        "queued -> starting -> running -> failed.  The failure_class "
        "is recorded.  The work order can potentially be retried "
        "(new run created)."
    ),
    work_order=_wo(
        wo_id="WO-006",
        decision_question=(
            "What is the expression profile of synthetic gene V across GTEx tissues?"
        ),
        requested_role="computational-biologist",
        stage="target-nomination",
        layer_0_classes=["expression", "gtex"],
        layer_1=["findings/computational-biology/expression-profile.md"],
        context={
            "summary": "GTEx expression profile for synthetic gene V",
            "target": "synthetic-gene-V",
            "ensembl_id": "ENSG_SYNTHETIC_V",
            "note": "SYNTHETIC FIXTURE — run will be transitioned to failed",
        },
    ),
    run_data={
        "run_id": "RUN-006",
        "work_order_id": "WO-006",
        "work_order_revision": 1,
        "state": "queued",
        "attempt": 1,
        "created_at": _NOW,
    },
)


# -----------------------------------------------------------------------
# Fixture 7: Disputed citation
# -----------------------------------------------------------------------

FIXTURE_DISPUTED_CITATION = FixtureDefinition(
    fixture_id="EVAL-007",
    label="Disputed citation — finding references retracted paper",
    category="disputed_citation",
    provenance="synthetic",
    scenario_description=(
        "A hypothesis is adopted.  The hypothesis references a "
        "publication that has been retracted.  A synthetic finding "
        "artifact carries a relay code indicating the citation cannot "
        "be verified.  This tests whether disputed citations propagate "
        "through the validation pipeline."
    ),
    expected_behavior=(
        "Hypothesis adoption succeeds.  The relay code for the "
        "disputed citation is recorded in the sidecar.  Validation "
        "should detect the relay in the provenance chain."
    ),
    work_order=_wo(
        wo_id="WO-007",
        decision_question=(
            "Is the reported mechanism of synthetic compound Q "
            "supported by peer-reviewed evidence?"
        ),
        requested_role="scientific-reviewer",
        stage="target-nomination",
        layer_0_classes=["literature"],
        layer_1=["findings/regulatory/citation-review.md"],
        context={
            "summary": "Citation verification for synthetic compound Q mechanism",
            "target": "synthetic-compound-Q",
            "doi_under_review": "10.xxxx/RETRACTED.2024.SYNTHETIC",
            "note": (
                "SYNTHETIC FIXTURE — references a fabricated retracted DOI "
                "to test disputed-citation handling"
            ),
        },
    ),
    hypothesis_data=[
        {
            "statement": (
                "Synthetic compound Q inhibits target R via allosteric "
                "mechanism described in DOI:10.xxxx/RETRACTED.2024.SYNTHETIC"
            ),
            "mechanism": "Allosteric inhibition at site 2",
            "evidence_basis": "DOI:10.xxxx/RETRACTED.2024.SYNTHETIC (RETRACTED)",
        },
    ],
)


# -----------------------------------------------------------------------
# Fixture 8: Bounded-review exhaustion
# -----------------------------------------------------------------------

FIXTURE_BOUNDED_REVIEW = FixtureDefinition(
    fixture_id="EVAL-008",
    label="Bounded-review exhaustion — work order cycles through revision requests",
    category="bounded_review_exhaustion",
    provenance="synthetic",
    scenario_description=(
        "A work order is submitted and fails validation.  It transitions "
        "back to in_progress (recovery path), is resubmitted, fails "
        "again, and this cycle repeats.  After multiple cycles, the "
        "work order should still follow the state machine — there is "
        "no automatic cycle-limit in the current workflow.  This "
        "fixture measures how many cycles the state machine permits."
    ),
    expected_behavior=(
        "The state machine allows unlimited recovery cycles: "
        "validation_failed -> in_progress -> submitted -> validation_failed. "
        "There is no built-in circuit breaker.  The fixture runs "
        "3 cycles to establish the baseline behavior."
    ),
    work_order=_wo(
        wo_id="WO-008",
        decision_question=(
            "Is the selectivity profile of synthetic inhibitor M "
            "acceptable for clinical development?"
        ),
        requested_role="medicinal-chemist",
        stage="target-nomination",
        layer_0_classes=["screening", "selectivity"],
        layer_1=["findings/medicinal-chemistry/selectivity-assessment.md"],
        context={
            "summary": "Selectivity assessment for synthetic inhibitor M",
            "target": "synthetic-kinase-family",
            "compound": "synthetic-inhibitor-M",
            "note": (
                "SYNTHETIC FIXTURE — will cycle through validation "
                "failure and recovery multiple times"
            ),
        },
    ),
)


# -----------------------------------------------------------------------
# All fixtures
# -----------------------------------------------------------------------

ALL_FIXTURES: list[FixtureDefinition] = [
    FIXTURE_NO_GENETIC_SUPPORT,
    FIXTURE_NEGATIVE_POCKET,
    FIXTURE_POSITIVE_GEOMETRY,
    FIXTURE_MODALITY_MISMATCH,
    FIXTURE_ABSENT_ENTITY,
    FIXTURE_TOOL_FAILURE,
    FIXTURE_DISPUTED_CITATION,
    FIXTURE_BOUNDED_REVIEW,
]


# Declined-candidate sample for selection-bias review (issue #82):
# Two of the eight fixtures represent concepts that would be declined
# in a real workflow (no genetic support, negative pocket).  These are
# included in the follow-up review sample to expose selection bias.
DECLINED_CANDIDATE_SAMPLE: list[str] = [
    "EVAL-001",  # No genetic support → likely declined
    "EVAL-002",  # Negative pocket → likely declined
]
