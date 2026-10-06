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

"""Manufacturing assessment logic — progressive stage-gated evaluation.

Stage 0 (qualitative, no external APIs):
  Assess production-platform fit for a concept record using modality,
  delivery_assumptions, and entity_ref.

The assessment logic lives here in ``core/`` — separate from the CLI
commands in ``commands/manufacturing.py`` — following the project's
existing pattern: core modules hold domain logic; command modules are
thin CLI wrappers.

Hard constraints:
  - SA-score != synthesizability.  A good SA-score is a cheap heuristic
    about fragment frequency, not evidence of a scalable process.
  - Stereocenter/step counts are scoped heuristics, not vetoes.
  - No fabrication of yield, cost of goods, stability, or formulation.
  - Qualitative findings cite precedent, assumptions, limitations, owner,
    and the next required evidence.

Stage definitions (progressive requirements):
  Stage 0: product/modality/delivery and production-platform fit
  Stage 2: entity/route or therapeutic-sequence feasibility
  Stage 3: formulation/process/developability evidence
  Stage 4: supply and applicable readiness evidence
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Manufacturing evidence types (extend the non-authoritative registry)
# ---------------------------------------------------------------------------

MANUFACTURING_EVIDENCE_TYPES = {
    "manufacturing_feasibility": "Stage 0 production-platform fit assessment",
    "synthetic_accessibility": (
        "SA-score (Ertl & Schuffenhauer) — fragment-frequency heuristic, "
        "NOT a synthesis route"
    ),
    "complexity_heuristic": (
        "Stereocenter/step-count complexity flags — scoped heuristics, not vetoes"
    ),
    "production_platform_fit": ("Modality-delivery to production-platform mapping"),
    "biologic_developability": (
        "Sequence-based biologic developability (qualitative at Stage 0)"
    ),
}

# ---------------------------------------------------------------------------
# Stage definitions (progressive requirements)
# ---------------------------------------------------------------------------

STAGE_REQUIREMENTS: dict[int, dict[str, Any]] = {
    0: {
        "name": "product_modality_delivery_fit",
        "description": "Product/modality/delivery and production-platform fit",
        "required_inputs": ["modality"],
        "optional_inputs": ["delivery_assumptions", "entity_ref"],
        "evidence_types": [
            "manufacturing_feasibility",
            "synthetic_accessibility",
            "complexity_heuristic",
            "production_platform_fit",
        ],
    },
    2: {
        "name": "entity_route_feasibility",
        "description": "Entity/route or therapeutic-sequence feasibility",
        "required_inputs": ["entity_ref"],
        "optional_inputs": ["synthesis_route", "expression_system"],
        "evidence_types": [
            "synthetic_route_precedent",
            "starting_material_availability",
            "expression_system_feasibility",
            "sequence_liability",
        ],
        "status": "placeholder — underlying tools not yet available",
    },
    3: {
        "name": "formulation_process_developability",
        "description": "Formulation/process/developability evidence",
        "required_inputs": ["entity_ref", "process_data"],
        "optional_inputs": ["formulation_data", "stability_data"],
        "evidence_types": [
            "process_chemistry_feasibility",
            "formulation_feasibility",
            "developability_assessment",
        ],
        "status": "placeholder — requires real process data",
    },
    4: {
        "name": "supply_readiness",
        "description": "Supply and applicable readiness evidence",
        "required_inputs": ["entity_ref", "supply_chain_data"],
        "optional_inputs": ["gmp_assessment", "regulatory_data"],
        "evidence_types": [
            "supply_chain_readiness",
            "manufacturing_scale_assessment",
            "regulatory_manufacturing_readiness",
        ],
        "status": "placeholder — requires real supply data",
    },
}

# ---------------------------------------------------------------------------
# Production platform mapping
# ---------------------------------------------------------------------------

#: Known modality-to-production-platform mappings for Stage 0 feasibility.
PRODUCTION_PLATFORMS: dict[str, dict[str, Any]] = {
    "small_molecule": {
        "platforms": ["chemical_synthesis"],
        "stage0_assessment": "synthetic_accessibility",
        "requires_entity": True,
        "description": (
            "Chemical synthesis.  Stage 0 feasibility assessable via "
            "SA-score and complexity heuristics when a structure exists."
        ),
    },
    "biologic": {
        "platforms": ["mammalian_cell_culture", "microbial_fermentation"],
        "stage0_assessment": "expression_system_qualitative",
        "requires_entity": False,
        "description": (
            "Recombinant protein expression.  Stage 0 assessable from "
            "modality class and delivery assumptions (route, formulation "
            "constraints).  Sequence-level assessment requires entity_ref."
        ),
    },
    "antibody": {
        "platforms": ["mammalian_cell_culture"],
        "stage0_assessment": "expression_system_qualitative",
        "requires_entity": False,
        "description": (
            "Monoclonal antibody production in CHO or similar mammalian "
            "host.  Well-established platform with known developability "
            "assessment framework."
        ),
    },
    "molecular_glue": {
        "platforms": ["chemical_synthesis"],
        "stage0_assessment": "synthetic_accessibility",
        "requires_entity": True,
        "description": (
            "Chemical synthesis of molecular glue degraders.  Typically "
            "small molecules; SA-score and complexity heuristics apply."
        ),
    },
    "protac": {
        "platforms": ["chemical_synthesis"],
        "stage0_assessment": "synthetic_accessibility",
        "requires_entity": True,
        "description": (
            "Chemical synthesis of PROTACs.  Bifunctional molecules with "
            "higher complexity than typical small molecules.  SA-score "
            "is a weaker signal due to linker chemistry."
        ),
    },
    "peptide": {
        "platforms": ["solid_phase_synthesis", "recombinant_expression"],
        "stage0_assessment": "peptide_feasibility_qualitative",
        "requires_entity": False,
        "description": (
            "Solid-phase peptide synthesis or recombinant expression.  "
            "Length, cyclization, and non-natural amino acids affect "
            "manufacturing complexity."
        ),
    },
    "oligonucleotide": {
        "platforms": ["solid_phase_synthesis"],
        "stage0_assessment": "oligonucleotide_feasibility_qualitative",
        "requires_entity": False,
        "description": (
            "Solid-phase oligonucleotide synthesis.  Established platform "
            "for ASOs and siRNAs; length and modification pattern affect "
            "scalability."
        ),
    },
    "gene_therapy": {
        "platforms": ["viral_vector_production"],
        "stage0_assessment": "gene_therapy_feasibility_qualitative",
        "requires_entity": False,
        "description": (
            "Viral vector (AAV, lentiviral) production.  Manufacturing "
            "scale and purity remain significant challenges.  Stage 0 "
            "feasibility depends on vector choice and payload size."
        ),
    },
    "cell_therapy": {
        "platforms": ["cell_manufacturing"],
        "stage0_assessment": "cell_therapy_feasibility_qualitative",
        "requires_entity": False,
        "description": (
            "Autologous or allogeneic cell manufacturing.  Patient-specific "
            "production (autologous) has fundamentally different scale "
            "constraints than off-the-shelf (allogeneic)."
        ),
    },
}

#: Modalities where SA-score is applicable (chemical synthesis platforms).
SA_SCORE_MODALITIES = {"small_molecule", "molecular_glue", "protac"}


# ---------------------------------------------------------------------------
# Complexity heuristics (stereocenter/step count)
# ---------------------------------------------------------------------------


def compute_complexity_heuristics(smiles: str) -> dict[str, Any] | None:
    """Compute complexity heuristics for a SMILES string.

    Returns a dict with stereocenter_count, chiral_centers, ring_count,
    and estimated_step_range, or None if RDKit is not available or the
    SMILES is invalid.

    These are **scoped heuristics**, not vetoes.  A molecule with many
    stereocenters is flagged with the specific concern, not automatically
    rejected.  The requirement_type is ``prioritization_heuristic`` or
    sponsor-specific ``hard_constraint`` — never an unconditional
    ``scientific_cutoff``.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, rdMolDescriptors
    except ImportError:
        return None

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    # Stereocenters: count unspecified and specified chiral centers
    chiral_centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True)
    n_stereocenters = len(chiral_centers)

    # Ring complexity
    ring_info = mol.GetRingInfo()
    n_rings = ring_info.NumRings()
    n_aromatic_rings = Descriptors.NumAromaticRings(mol)
    n_spiro = rdMolDescriptors.CalcNumSpiroAtoms(mol)
    n_bridgehead = rdMolDescriptors.CalcNumBridgeheadAtoms(mol)

    # Heavy atom count (rough proxy for step count range)
    n_heavy = mol.GetNumHeavyAtoms()

    # Rough step estimate based on heavy atom count.
    # This is NOT a synthesis route — it is a coarse heuristic.
    if n_heavy <= 15:
        step_range = "1-5 (estimated from molecular size)"
    elif n_heavy <= 25:
        step_range = "3-8 (estimated from molecular size)"
    elif n_heavy <= 35:
        step_range = "5-12 (estimated from molecular size)"
    else:
        step_range = (
            ">10 (estimated from molecular size; retrosynthetic analysis needed)"
        )

    return {
        "stereocenter_count": n_stereocenters,
        "chiral_centers": [
            {"atom_idx": idx, "assignment": assign} for idx, assign in chiral_centers
        ],
        "ring_count": n_rings,
        "aromatic_ring_count": n_aromatic_rings,
        "spiro_atoms": n_spiro,
        "bridgehead_atoms": n_bridgehead,
        "heavy_atom_count": n_heavy,
        "estimated_step_range": step_range,
        "heuristic_type": "prioritization_heuristic",
        "scope": (
            "These are scoped heuristics for prioritization, not synthesis "
            "vetoes.  A molecule with many stereocenters is flagged with "
            "the specific concern — it is not automatically rejected.  "
            "Step-count estimates are derived from molecular size, not "
            "from retrosynthetic analysis."
        ),
    }


# ---------------------------------------------------------------------------
# Stage 0 assessment logic
# ---------------------------------------------------------------------------


def assess_stage0(
    concept: dict[str, Any],
    sa_score_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce a Stage 0 manufacturing feasibility assessment.

    Parameters
    ----------
    concept:
        A concept record dict with at least ``modality``.  Optionally
        ``entity_ref``, ``delivery_assumptions``.
    sa_score_data:
        Optional SA-score data dict (from ``compound sa-score``).

    Returns
    -------
    dict
        A ``dde.evidence-assessment.v1`` record for manufacturing
        feasibility at Stage 0.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    modality = concept.get("modality")
    entity_ref = concept.get("entity_ref")
    delivery = concept.get("delivery_assumptions")
    concept_id = concept.get("id", "IC-???")
    concept_rev = concept.get("revision", 1)
    concept_ref = f"{concept_id}-r{concept_rev}"

    findings: list[dict[str, Any]] = []
    overall_status = "not_assessed"
    execution_outcome = "completed"

    # --- 1. Production-platform fit ---
    platform_info = PRODUCTION_PLATFORMS.get(modality)
    if platform_info is None:
        findings.append(
            {
                "aspect": "production_platform_fit",
                "status": "not_assessed",
                "detail": (
                    f"Modality {modality!r} is not in the known "
                    "production-platform mapping.  Manufacturing feasibility "
                    "cannot be assessed at Stage 0 without knowing the "
                    "production platform."
                ),
                "next_evidence": "Define the intended production platform",
            }
        )
    else:
        findings.append(
            {
                "aspect": "production_platform_fit",
                "status": "supported",
                "detail": platform_info["description"],
                "platforms": platform_info["platforms"],
                "precedent": (
                    f"Modality {modality!r} has established production "
                    f"platforms: {', '.join(platform_info['platforms'])}."
                ),
                "assumptions": [
                    f"Standard {modality} production infrastructure is available",
                    "No unusual formulation or delivery constraints that "
                    "would preclude standard manufacturing",
                ],
                "limitations": [
                    "Platform fit does not imply GMP readiness",
                    "Platform fit does not imply cost-effective manufacturing",
                    "Entity-specific manufacturing challenges are not "
                    "assessed until a physical entity exists",
                ],
            }
        )

    # --- 2. Delivery compatibility ---
    if delivery is not None:
        route = delivery.get("route")
        formulation = delivery.get("formulation")
        vehicle = delivery.get("vehicle")

        delivery_finding: dict[str, Any] = {
            "aspect": "delivery_manufacturing_compatibility",
            "status": "supported",
            "detail": (
                f"Delivery route {route!r} is noted.  Manufacturing "
                "compatibility with the specified delivery assumptions "
                "is qualitatively assessed."
            ),
            "assumptions": [],
            "limitations": [
                "Formulation properties (stability, viscosity, "
                "aggregation) are NOT assessed — they require real data",
                "No yield, cost of goods, or formulation property is "
                "fabricated in this assessment",
            ],
        }
        if route:
            delivery_finding["assumptions"].append(f"Route of administration: {route}")
        if formulation:
            delivery_finding["assumptions"].append(f"Formulation type: {formulation}")
        if vehicle:
            delivery_finding["assumptions"].append(f"Delivery vehicle: {vehicle}")
        delivery_finding["next_evidence"] = (
            "Stage 3 formulation/process/developability evidence "
            "required to validate delivery assumptions"
        )
        findings.append(delivery_finding)

    # --- 3. Entity-specific assessment ---
    if entity_ref is None:
        # No physical entity — not_yet_applicable
        findings.append(
            {
                "aspect": "entity_manufacturing_assessment",
                "status": "not_yet_applicable",
                "detail": (
                    "No physical entity (compound, sequence, construct) "
                    "exists for this concept.  Entity-level manufacturing "
                    "assessment is not yet applicable."
                ),
                "trigger": (
                    "This assessment becomes applicable when entity_ref "
                    "is populated — i.e., when a physical entity is "
                    "identified for this concept."
                ),
            }
        )
        overall_status = "not_yet_applicable"
    elif platform_info is None:
        # Entity exists but modality is unrecognized — cannot assess
        findings.append(
            {
                "aspect": "entity_manufacturing_assessment",
                "status": "not_assessed",
                "detail": (
                    f"Modality {modality!r} is not recognized.  "
                    "Entity-level manufacturing assessment cannot be "
                    "performed without a known production platform."
                ),
            }
        )
        # overall_status stays "not_assessed" — do NOT override
    else:
        if modality in SA_SCORE_MODALITIES:
            # Small-molecule-like: SA-score + complexity heuristics
            if sa_score_data is not None:
                sa_score = sa_score_data.get("sa_score")
                findings.append(
                    {
                        "aspect": "synthetic_accessibility",
                        "status": (
                            "supported" if sa_score is not None else "not_assessed"
                        ),
                        "sa_score": sa_score,
                        "detail": (
                            f"SA-score: {sa_score} (1=easy, 10=hard).  "
                            "This is a fragment-frequency heuristic (Ertl "
                            "& Schuffenhauer 2009), NOT evidence of a "
                            "demonstrated synthetic route or scalable "
                            "manufacturing process."
                        ),
                        "distinction": (
                            "SA-score reflects how common the molecule's "
                            "fragments are in known compounds.  A low "
                            "SA-score does NOT mean the molecule is "
                            "synthesizable.  A high SA-score does NOT mean "
                            "the molecule cannot be synthesized.  Real "
                            "synthesis feasibility requires route analysis."
                        ),
                        "evidence_type": "synthetic_accessibility",
                        "method": "sa_score_ertl_schuffenhauer_2009",
                    }
                )
                overall_status = "supported" if sa_score is not None else "not_assessed"
            else:
                findings.append(
                    {
                        "aspect": "synthetic_accessibility",
                        "status": "not_assessed",
                        "detail": (
                            "SA-score has not been computed for this entity.  "
                            "Run `dde compound sa-score` to compute it."
                        ),
                        "next_evidence": (
                            "Run `dde compound sa-score` with the entity's SMILES"
                        ),
                    }
                )

            # Complexity heuristics
            complexity = compute_complexity_heuristics(entity_ref)
            if complexity is not None:
                stereo_count = complexity["stereocenter_count"]
                flags: list[str] = []
                if stereo_count > 2:
                    flags.append(
                        f"High stereocenter count ({stereo_count}).  "
                        "This is a prioritization heuristic — a "
                        "molecule with many stereocenters requires "
                        "more careful synthetic planning, but is NOT "
                        "automatically rejected."
                    )
                if complexity["spiro_atoms"] > 0:
                    flags.append(
                        f"Contains {complexity['spiro_atoms']} spiro "
                        "atom(s).  Spiro centers add synthetic "
                        "complexity."
                    )
                if complexity["bridgehead_atoms"] > 0:
                    flags.append(
                        f"Contains {complexity['bridgehead_atoms']} "
                        "bridgehead atom(s).  Bridged ring systems "
                        "require specialized synthetic approaches."
                    )

                findings.append(
                    {
                        "aspect": "complexity_heuristic",
                        "status": "supported",
                        "complexity": complexity,
                        "flags": flags,
                        "requirement_type": "prioritization_heuristic",
                        "detail": (
                            f"Stereocenter count: {stereo_count}, "
                            f"rings: {complexity['ring_count']}, "
                            f"estimated steps: "
                            f"{complexity['estimated_step_range']}.  "
                            "These are scoped heuristics for "
                            "prioritization.  Step/stereocenter counts "
                            "are NOT universal scientific vetoes — they "
                            "are flagged with the specific concern."
                        ),
                    }
                )
                if overall_status == "not_assessed":
                    overall_status = "supported"
        else:
            # Biologic-like modalities: qualitative assessment
            findings.append(
                {
                    "aspect": "biologic_manufacturing_qualitative",
                    "status": "supported",
                    "detail": (
                        f"Modality {modality!r} uses established biologic "
                        "production platforms.  Entity-specific assessment "
                        "(sequence liabilities, expression system "
                        "feasibility, developability) requires the "
                        "therapeutic construct sequence, not the target "
                        "protein sequence."
                    ),
                    "assumptions": [
                        "Standard expression systems (CHO, E. coli, etc.) "
                        "are available",
                        "No known sequence liabilities that would preclude "
                        "expression (requires sequence-level analysis)",
                    ],
                    "limitations": [
                        "Sequence-level liability assessment NOT performed "
                        "— this is a concept-level qualitative assessment "
                        "only",
                        "No developability data (aggregation, viscosity, "
                        "charge patches) is fabricated",
                        "Post-translational modification complexity not assessed",
                        "Formulation properties not assessed",
                    ],
                    "next_evidence": (
                        "Stage 2: sequence-level developability assessment "
                        "(deamidation hotspots, oxidation-prone residues, "
                        "aggregation propensity, charge patches)"
                    ),
                    "owner": "CMC scientist or developability specialist",
                }
            )
            if overall_status == "not_assessed":
                overall_status = "supported"

    # --- Build the assessment record ---
    claim = (
        f"Concept {concept_ref} has a plausible Stage 0 manufacturing "
        f"path for modality {modality!r}"
    )

    assessment: dict[str, Any] = {
        "schema": "dde.evidence-assessment.v1",
        "id": "AR-PENDING",  # Caller assigns the real ID
        "concept_ref": concept_ref,
        "claim": claim,
        "evidence_status": overall_status,
        "execution_outcome": execution_outcome,
        "evidence": {
            "artifact_path": "generated-by-manufacturing-assess-stage0",
            "evidence_type": "manufacturing_feasibility",
            "metric_name": None,
            "metric_value": None,
            "metric_units": None,
            "method": "dde_manufacturing_assess_stage0",
            "context": (
                "Stage 0 manufacturing feasibility assessment.  "
                "Neither early screen implies GMP or manufacturing "
                "clearance."
            ),
        },
        "rationale": _build_rationale(findings, modality, entity_ref),
        "confidence": "moderate" if entity_ref else "low",
        "assessed_at": now,
        "assessed_by": "dde-manufacturing-stage0",
        "manufacturing_stage": 0,
        "findings": findings,
        "stage_requirements": STAGE_REQUIREMENTS,
    }

    return assessment


def _build_rationale(
    findings: list[dict[str, Any]],
    modality: str | None,
    entity_ref: str | None,
) -> str:
    """Build a human-readable rationale from the findings."""
    parts = []
    for f in findings:
        aspect = f.get("aspect", "unknown")
        status = f.get("status", "unknown")
        detail = f.get("detail", "")
        parts.append(f"[{aspect}] {status}: {detail}")

    if entity_ref is None:
        parts.append(
            "Entity-level manufacturing assessment is not yet applicable "
            "— no physical entity exists for this concept."
        )

    parts.append(
        "IMPORTANT: This is a Stage 0 qualitative assessment.  It does "
        "NOT imply GMP readiness or manufacturing clearance.  No yield, "
        "cost of goods, stability, or formulation properties have been "
        "fabricated."
    )

    return "\n".join(parts)
