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

"""Pydantic schemas shared by the Sentinel sub-agents.

These schemas are designed to mirror the language and decision points of a
real Medical / Legal / Regulatory (MLR) review. They intentionally avoid a
binary pass/fail framing and instead capture observations, the underlying
principle being applied, and discussion-style explanations a reviewer can
use as a starting point.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ReviewLens(str, Enum):
    """Which MLR perspective produced a finding."""

    MEDICAL = "medical"
    LEGAL = "legal"
    REGULATORY = "regulatory"
    EDITORIAL = "editorial"
    # `custom` is for findings produced by the rules_reviewer — i.e. the
    # user-uploaded rules file (brand voice, internal SOPs, market-specific
    # restrictions, etc.). Distinct from the four canonical MLR lenses
    # because the rule semantics are user-defined per submission.
    CUSTOM = "custom"


class Severity(str, Enum):
    """Severity bands.

    INFORMATIONAL is intentionally distinct from LOW: it captures observations
    that are worth surfacing for discussion but are not, on their own, an
    issue to remediate.
    """

    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceDepth(str, Enum):
    """How thoroughly a finding has been substantiated.

    SURFACE: Observed in the content itself, no cross-referencing.
    MODERATE: Cross-referenced against other parts of the same submission
        (e.g., headline claim vs. ISI; body copy vs. footnote).
    DEEP: Verified against an external authoritative source. Reserved for
        future expansion when external lookup tools are wired in.
    """

    SURFACE = "surface"
    MODERATE = "moderate"
    DEEP = "deep"


class ConfidenceBand(str, Enum):
    """Qualitative confidence band for human-readable summaries."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IssueCategory(str, Enum):
    """Categories grouped by review lens.

    The grouping is informational only; categories may legitimately be
    surfaced by more than one lens (e.g., a fair-balance issue is both a
    medical and a regulatory concern).
    """

    # Medical
    CLINICAL_ACCURACY = "clinical_accuracy"
    MECHANISM_OF_ACTION = "mechanism_of_action"
    DOSING = "dosing"
    EFFICACY_CLAIM = "efficacy_claim"
    SAFETY_PROFILE = "safety_profile"
    CONTRAINDICATION = "contraindication"
    FAIR_BALANCE = "fair_balance"
    PATIENT_POPULATION = "patient_population"

    # Legal
    CLAIM_SUBSTANTIATION = "claim_substantiation"
    COMPARATIVE_CLAIM = "comparative_claim"
    SUPERLATIVE_CLAIM = "superlative_claim"
    CITATION = "citation"
    DISCLOSURE = "disclosure"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    ENDORSEMENT = "endorsement"

    # Regulatory
    INDICATION_SCOPE = "indication_scope"
    OFF_LABEL = "off_label"
    IMPORTANT_SAFETY_INFO = "important_safety_info"
    BLACK_BOX_WARNING = "black_box_warning"
    REGULATORY_GUIDANCE = "regulatory_guidance"
    PI_CONSISTENCY = "pi_consistency"

    # Editorial
    CLARITY = "clarity"
    ACCESSIBILITY = "accessibility"
    TONE = "tone"
    VISUAL_DESIGN = "visual_design"
    TYPOGRAPHY = "typography"
    READABILITY = "readability"

    OTHER = "other"


class ContentLocation(BaseModel):
    """Pointer to where in the content an item or finding lives."""

    page: Optional[int] = Field(
        None, description="Page number for paginated content (PDF)."
    )
    section: Optional[str] = Field(
        None,
        description=(
            "Section identifier — for HTML this might be a heading or DOM "
            "selector; for images, a region label such as 'header banner'."
        ),
    )
    bbox: Optional[list[float]] = Field(
        None,
        description=(
            "Normalized bounding box [x_min, y_min, x_max, y_max] in 0..1 "
            "coordinates for image content."
        ),
    )
    quote: Optional[str] = Field(
        None,
        description="Short verbatim excerpt for textual content.",
    )


class ContentItemKind(str, Enum):
    """Kinds of notable elements the intake agent extracts."""

    PRODUCT_CLAIM = "product_claim"
    EFFICACY_STATISTIC = "efficacy_statistic"
    SAFETY_STATEMENT = "safety_statement"
    INDICATION_STATEMENT = "indication_statement"
    DOSING_STATEMENT = "dosing_statement"
    MECHANISM_STATEMENT = "mechanism_statement"
    CITATION = "citation"
    FOOTNOTE = "footnote"
    DISCLAIMER = "disclaimer"
    IMPORTANT_SAFETY_INFO = "important_safety_info"
    HEADLINE = "headline"
    CALL_TO_ACTION = "call_to_action"
    ENDORSEMENT = "endorsement"
    COMPARATIVE_STATEMENT = "comparative_statement"
    VISUAL_ELEMENT = "visual_element"
    TABLE_OR_CHART = "table_or_chart"
    OTHER = "other"


class ContentItem(BaseModel):
    """A single notable element extracted from the submission."""

    item_id: str = Field(..., description="Stable ID, e.g. 'C1', 'C2', ...")
    kind: ContentItemKind
    text: str = Field(
        ...,
        description="The verbatim text or a short factual description for visuals.",
    )
    location: Optional[ContentLocation] = None
    notes: Optional[str] = Field(
        None,
        description="Intake-agent observations worth carrying to the reviewers.",
    )


class AudienceType(str, Enum):
    """Who the piece appears to be written for."""

    HEALTHCARE_PROFESSIONAL = "healthcare_professional"
    PATIENT_OR_CONSUMER = "patient_or_consumer"
    PAYER = "payer"
    MIXED_OR_UNCLEAR = "mixed_or_unclear"


class ContentInventory(BaseModel):
    """Output of the intake agent.

    The intake pass is deliberately exhaustive: every potentially reviewable
    element is catalogued so downstream reviewers have a stable index to
    refer to, rather than re-quoting the content.
    """

    content_summary: str = Field(
        ...,
        description="2-4 sentence neutral summary of what the submission communicates.",
    )
    promotional_intent: str = Field(
        ...,
        description="Best-effort statement of the persuasive goal of the piece.",
    )
    intended_audience: AudienceType
    audience_rationale: str = Field(
        ...,
        description="Why the intake agent inferred this audience.",
    )
    product_or_topic: Optional[str] = Field(
        None,
        description="Product, indication, or topic the piece centers on, if discernible.",
    )
    items: list[ContentItem] = Field(
        ...,
        description="Catalogue of reviewable elements.",
    )
    open_observations: list[str] = Field(
        default_factory=list,
        description="Things the intake agent noticed that don't map to a single item.",
    )


class Finding(BaseModel):
    """A single MLR-style observation worth discussing."""

    finding_id: str = Field(..., description="Stable ID, e.g. 'F-MED-1'.")
    review_lens: ReviewLens
    category: IssueCategory
    severity: Severity
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Numeric confidence in the observation (not the severity).",
    )
    confidence_band: ConfidenceBand
    evidence_depth: EvidenceDepth
    related_item_ids: list[str] = Field(
        default_factory=list,
        description="ContentInventory item_ids this finding relates to.",
    )
    quoted_content: Optional[str] = Field(
        None,
        description="Verbatim excerpt the finding hinges on, if applicable.",
    )
    observation: str = Field(
        ...,
        description="Plain-language statement of what was noticed.",
    )
    mlr_principle: str = Field(
        ...,
        description=(
            "The underlying MLR principle, regulatory framework, or industry "
            "norm being applied (e.g., 'fair balance', 'OPDP guidance on "
            "comparative claims', 'PhRMA Code §3')."
        ),
    )
    discussion: str = Field(
        ...,
        description=(
            "Educational explanation a reviewer can use as a starting point. "
            "Frame this as 'here is why this is worth a closer look', not as "
            "a verdict."
        ),
    )
    suggested_questions: list[str] = Field(
        default_factory=list,
        description="Questions the reviewer might raise with the submitter.",
    )
    suggested_actions: list[str] = Field(
        default_factory=list,
        description="Possible remediation paths, presented as options not mandates.",
    )
    location: Optional[ContentLocation] = None


class ReviewerOutput(BaseModel):
    """Output schema used by every reviewer sub-agent."""

    lens: ReviewLens
    reviewer_summary: str = Field(
        ...,
        description=(
            "1-3 sentence narrative summary of what the reviewer focused on "
            "and the overall character of the submission from this lens."
        ),
    )
    findings: list[Finding]


class SeverityAdjustment(BaseModel):
    """Critic recommendation to revise a finding's severity."""

    finding_id: str
    current_severity: Severity
    recommended_severity: Severity
    rationale: str


class DuplicateGroup(BaseModel):
    """Set of finding IDs the critic considers redundant."""

    finding_ids: list[str]
    rationale: str
    keep: str = Field(
        ...,
        description="The finding_id the critic recommends retaining as canonical.",
    )


class CrossLensTheme(BaseModel):
    """A theme that recurs across findings from multiple lenses."""

    theme: str = Field(
        ...,
        description="Short label for the recurring theme (e.g., 'no safety apparatus').",
    )
    related_finding_ids: list[str] = Field(
        ...,
        description="Findings from any lens that touch this theme.",
    )
    discussion: str = Field(
        ...,
        description=(
            "Why this cluster matters as a whole — what a reviewer should "
            "see when they look at the findings together rather than "
            "individually."
        ),
    )


class Defense(BaseModel):
    """A single argument the submitter's advocate would make."""

    addresses_topic: str = Field(
        ...,
        description=(
            "What this defense is about, in the advocate's own words "
            "(e.g., 'the curative claim', 'the cymbal-to-ear visual')."
        ),
    )
    related_item_ids: list[str] = Field(
        default_factory=list,
        description="ContentInventory item_ids this defense touches.",
    )
    argument: str = Field(
        ...,
        description="The advocate's argument (2–4 sentences).",
    )
    charitable_interpretation: str = Field(
        ...,
        description=(
            "How a reasonable submitter would read or intend this element. "
            "The point is to give critics a fair counter-frame to weigh."
        ),
    )
    proposed_compromise: Optional[str] = Field(
        None,
        description=(
            "If the advocate would concede a middle-ground revision, "
            "describe it here."
        ),
    )
    likely_to_dispute: list[ReviewLens] = Field(
        default_factory=list,
        description=(
            "Which review lenses are most likely to push back on this "
            "defense, in the advocate's estimation."
        ),
    )


class SubmitterDefenseBrief(BaseModel):
    """Output of the submitter's advocate agent.

    The advocate runs in parallel with the four critical reviewers and
    proactively argues for the submission. The critic panel weighs these
    defenses when calibrating severity and framing the final report.
    """

    overall_framing: str = Field(
        ...,
        description=(
            "1–3 sentence narrative of how the advocate would frame the "
            "submission charitably."
        ),
    )
    defenses: list[Defense]


class DedupeCriticOutput(BaseModel):
    """Output of the dedupe-focused critic."""

    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list)
    cross_lens_themes: list[CrossLensTheme] = Field(
        default_factory=list,
        description=(
            "Thematic clusters across lenses worth surfacing even when "
            "the underlying findings should remain distinct."
        ),
    )
    rationale: str = Field(
        ...,
        description="Brief narrative explaining the dedupe decisions.",
    )


class SeverityCriticOutput(BaseModel):
    """Output of the severity-calibration-focused critic."""

    severity_adjustments: list[SeverityAdjustment] = Field(default_factory=list)
    confidence_concerns: list[str] = Field(
        default_factory=list,
        description=(
            "Findings whose confidence score seems miscalibrated relative "
            "to the underlying observation. Plain-language descriptions."
        ),
    )
    advocate_weighed: bool = Field(
        ...,
        description="True if the submitter's defense brief was considered.",
    )
    rationale: str = Field(
        ...,
        description="Brief narrative explaining the calibration decisions.",
    )


class GapCriticOutput(BaseModel):
    """Output of the gap-finding-focused critic."""

    gaps_identified: list[str] = Field(
        default_factory=list,
        description="Issues the reviewer panel may have missed.",
    )
    additional_findings: list[Finding] = Field(
        default_factory=list,
        description="Net-new findings the gap critic surfaces.",
    )
    completeness_assessment: str = Field(
        ...,
        description=(
            "Brief narrative on coverage — which lenses look thorough, "
            "which look thin."
        ),
    )


class CriticAssessment(BaseModel):
    """Consolidated output of the critic stage.

    Produced by the critic_merger from the three specialist critic outputs.
    Schema kept compatible with what the synthesizer expects.
    """

    overall_assessment: str = Field(
        ...,
        description="Narrative assessment of the reviewer pass as a whole.",
    )
    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list)
    cross_lens_themes: list[CrossLensTheme] = Field(default_factory=list)
    severity_adjustments: list[SeverityAdjustment] = Field(default_factory=list)
    gaps_identified: list[str] = Field(default_factory=list)
    additional_findings: list[Finding] = Field(default_factory=list)
    iteration_recommendation: str = Field(
        ...,
        description=(
            "One of: 'another_pass_would_help' or 'reviewers_have_converged'. "
            "Used by the loop decider to decide whether to iterate again."
        ),
    )


class FinalReport(BaseModel):
    """End-user-facing report produced by the synthesizer."""

    content_summary: str
    intended_audience: AudienceType
    promotional_intent: str
    executive_summary: str = Field(
        ...,
        description=(
            "Narrative summary of the review. This is not a verdict; it is "
            "the orientation a senior reviewer would give a junior reviewer "
            "before they sit down with the package."
        ),
    )
    themes: list[str] = Field(
        default_factory=list,
        description="Cross-cutting themes that recur across multiple findings.",
    )
    findings: list[Finding] = Field(
        ...,
        description="Final consolidated set of findings, after critic review.",
    )
    open_questions_for_reviewers: list[str] = Field(default_factory=list)
    recommended_discussion_topics: list[str] = Field(default_factory=list)
    counts_by_lens: dict[str, int] = Field(default_factory=dict)
    counts_by_severity: dict[str, int] = Field(default_factory=dict)
